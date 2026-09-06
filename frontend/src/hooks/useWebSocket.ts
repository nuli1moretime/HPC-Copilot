import { useRef, useState, useCallback, useEffect } from 'react'
import type { TerminalMessage } from '../components/Terminal'
import type { ChatMessage, TimelineItem } from '../components/ChatPanel'
import type { AgentAlert } from '../App'

/** 作业队列小组件用：单个作业的状态快照 */
export interface JobInfo {
  job_id: string
  state: string
}

/** Agent 工具调用中间步骤（实时展示给用户看 agent 在做什么） */
export interface AgentStep {
  action: 'thinking' | 'tool_call' | 'tool_result' | 'error'
  round?: number
  tool?: string
  args?: Record<string, unknown>
  result?: string
  message?: string
}

/** 模板工作流阶段状态 */
export interface TemplateStageState {
  index: number
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  detail?: string
  output?: string
}

/** 模板工作流整体状态 */
export interface TemplateState {
  templateId: string
  title: string
  icon: string
  stages: TemplateStageState[]
  running: boolean
  success?: boolean
  summary: string
  summaryStreaming: boolean
  startedAt: number
  durationSeconds?: number
  jobId?: string
  jobOutput?: string
  overview?: string
  conditions?: string[]
  models?: string[]
  outputs?: string[]
  workDir?: string
  artifacts?: string[]
  /** 运行卡片插在触发该模板的用户消息之后，而不是永远固定在对话末尾。 */
  anchorMessageId?: string
}

interface Options {
  onAgentAlert?: (alert: AgentAlert) => void
}

export function useWebSocket({ onAgentAlert }: Options = {}) {
  const terminalWsRef = useRef<WebSocket | null>(null)
  const chatWsRef = useRef<WebSocket | null>(null)

  const [terminalMessages, setTerminalMessages] = useState<TerminalMessage[]>([])
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      role: 'system',
      content: '👋 你好！我是 HPC Copilot 智能体。\n在左边终端操作时遇到问题，我会自动帮你分析。\n也可以直接在这里问我任何 HPC 相关问题。',
    },
  ])
  const [isTyping, setIsTyping] = useState(false)
  // 作业队列实时快照（后端监控循环推送 jobs_update 更新），驱动作业小组件
  const [jobs, setJobs] = useState<JobInfo[]>([])
  // 模板工作流进度状态（null = 当前没有模板在跑）
  const [templateState, setTemplateState] = useState<TemplateState | null>(null)
  const streamingRef = useRef(false)
  const streamingIdRef = useRef<string | null>(null)
  // 当前待填充 LLM 解释的告警卡片 id（让解释流式进卡片，而不是另起一条消息）
  const alertExplainIdRef = useRef<string | null>(null)
  const pendingTemplateAnchorRef = useRef<string | null>(null)
  // 待处理的告警队列：AI 正在流式输出时不能立即触发告警解释，
  // 否则前端 stream_chunk 会被 alertExplainIdRef 劫持，agent 正文全被塞进告警卡片。
  // 等 stream_end 后再从这里取出、发送 agent_diagnosis。
  const pendingAlertsRef = useRef<Array<{ alertId: string; data: AgentAlert }>>([])
  // 当前终端模式（webshell = 远程真实终端，command = 命令模式）
  const terminalModeRef = useRef<'command' | 'webshell'>('command')

  // 终端自动重连相关
  const terminalRetriesRef = useRef(0)
  const terminalReconnectTimerRef = useRef<number | null>(null)
  const terminalPingTimerRef = useRef<number | null>(null)
  const connectTerminalRef = useRef<() => void>(() => {})

  // 对话自动重连相关
  const chatRetriesRef = useRef(0)
  const chatReconnectTimerRef = useRef<number | null>(null)
  const chatPingTimerRef = useRef<number | null>(null)
  const connectChatRef = useRef<() => void>(() => {})
  // 首次连接期间的消息先排队，onopen 后按顺序发送；不再猜测“500ms 应该连好”。
  const chatOutboundQueueRef = useRef<string[]>([])

  // 连接终端 WebSocket
  const connectTerminal = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/terminal`)
    terminalWsRef.current = ws

    ws.onopen = () => {
      // 连接成功，重置重连计数
      terminalRetriesRef.current = 0
      // 定期发送心跳，防止代理（Vite/nginx）因空闲超时断开连接
      if (terminalPingTimerRef.current !== null) {
        window.clearInterval(terminalPingTimerRef.current)
      }
      terminalPingTimerRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 25000)
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data) as TerminalMessage

      // 记录终端模式（webshell / command），供告警处理判断是否需要重打提示符
      if (msg.type === 'terminal_mode') {
        terminalModeRef.current = msg.data as 'command' | 'webshell'
      }

      // 作业队列快照更新（后端监控循环推送），驱动作业小组件
      if (msg.type === 'jobs_update') {
        const payload = msg.data as unknown as { jobs: JobInfo[] }
        setJobs(payload.jobs || [])
      }

      setTerminalMessages((prev) => [...prev, msg])

      // 如果是 agent_alert：插入告警卡片，并转发获取 LLM 解释（解释会流式填充回这张卡片）
      if (msg.type === 'agent_alert') {
        const alert = msg.data as unknown as AgentAlert
        if (onAgentAlert) {
          onAgentAlert(alert)
        }
        // 给卡片一个唯一 id，并记下它，让随后的 LLM 解释流式进这张卡片（合并为一段）
        const alertId = `alert-${Date.now()}-${Math.random().toString(36).slice(2)}`
        setChatMessages((prev) => [
          ...prev,
          { role: 'alert', content: '', alert, id: alertId },
        ])

        // 关键：如果 AI 正在流式输出（streamingRef=true），
        // 立即设 alertExplainIdRef 会让后续 stream_chunk 全被劫持到告警卡片。
        // 先入队，等 stream_end 时再触发解释请求。
        if (
          streamingRef.current ||
          !chatWsRef.current ||
          chatWsRef.current.readyState !== WebSocket.OPEN
        ) {
          pendingAlertsRef.current.push({ alertId, data: alert })
        } else {
          alertExplainIdRef.current = alertId
          // 转发到对话 WebSocket，触发大模型生成通俗解释
          chatWsRef.current.send(
            JSON.stringify({ type: 'agent_diagnosis', data: msg.data, request_id: alertId })
          )
        }
        // webshell 模式：告警横幅是异步插入的，会"吃掉"远程 shell 的提示符行。
        // 请求后端重打提示符，让横幅下方出现 sa25232066@tradmin-02:~$
        if (terminalModeRef.current === 'webshell') {
          ws.send(JSON.stringify({ type: 'redraw_prompt' }))
        }
      }
    }

    ws.onclose = () => {
      // 停止心跳
      if (terminalPingTimerRef.current !== null) {
        window.clearInterval(terminalPingTimerRef.current)
        terminalPingTimerRef.current = null
      }

      setTerminalMessages((prev) => [
        ...prev,
        { type: 'output', data: '\r\n\x1b[31m[连接已断开]\x1b[0m\r\n' },
      ])

      // 自动重连（最多 5 次，每次间隔 1.5 秒）
      if (terminalRetriesRef.current < 5) {
        terminalRetriesRef.current += 1
        const attempt = terminalRetriesRef.current
        setTerminalMessages((prev) => [
          ...prev,
          { type: 'output', data: `\x1b[33m[正在尝试重连 (${attempt}/5)...]\x1b[0m\r\n` },
        ])
        terminalReconnectTimerRef.current = window.setTimeout(() => {
          connectTerminalRef.current()
        }, 1500)
      } else {
        setTerminalMessages((prev) => [
          ...prev,
          { type: 'output', data: '\x1b[31m[重连失败，请刷新页面重试]\x1b[0m\r\n' },
        ])
      }
    }

    return ws
  }, [onAgentAlert])

  // 始终让 ref 指向最新的 connectTerminal，供 onclose 安全地递归调用
  connectTerminalRef.current = connectTerminal

  // 卸载时清理未触发的重连定时器和心跳
  useEffect(() => {
    return () => {
      if (terminalReconnectTimerRef.current !== null) {
        window.clearTimeout(terminalReconnectTimerRef.current)
      }
      if (terminalPingTimerRef.current !== null) {
        window.clearInterval(terminalPingTimerRef.current)
      }
      if (chatReconnectTimerRef.current !== null) {
        window.clearTimeout(chatReconnectTimerRef.current)
      }
      if (chatPingTimerRef.current !== null) {
        window.clearInterval(chatPingTimerRef.current)
      }
    }
  }, [])

  // 连接对话 WebSocket
  const connectChat = useCallback(() => {
    const current = chatWsRef.current
    if (
      current &&
      (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)
    ) {
      return current
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`)
    chatWsRef.current = ws

    ws.onopen = () => {
      // 连接成功，重置重连计数
      chatRetriesRef.current = 0
      // 定期发送心跳，防止代理因空闲超时断开连接
      if (chatPingTimerRef.current !== null) {
        window.clearInterval(chatPingTimerRef.current)
      }
      chatPingTimerRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 25000)

      // 严格在 OPEN 之后发送首次连接期间积压的用户消息。
      const queued = chatOutboundQueueRef.current.splice(0)
      for (const payload of queued) ws.send(payload)

      // 终端可能比聊天先连好并产生告警。只有解释请求真的发出时，
      // 才设置 alertExplainIdRef，避免第一条普通回复被误认为告警解释。
      if (queued.length === 0 && !streamingRef.current && pendingAlertsRef.current.length > 0) {
        const next = pendingAlertsRef.current.shift()!
        alertExplainIdRef.current = next.alertId
        ws.send(JSON.stringify({ type: 'agent_diagnosis', data: next.data, request_id: next.alertId }))
      }
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)

      switch (msg.type) {
        case 'typing':
          if (String(msg.request_id || '').startsWith('alert-')) break
          // 大模型开始思考：重置流式引用，让下一个 agent_step / stream_chunk
          // 创建一条新的 assistant 消息（timeline 从空开始）
          setIsTyping(true)
          streamingRef.current = false
          streamingIdRef.current = null
          break

        case 'agent_step':
          // Agent 工具调用中间步骤：追加到当前流式消息的 timeline
          {
            const step = msg.data as AgentStep
            // 有了具体步骤就不再显示通用"正在思考"占位
            setIsTyping(false)

            const sid = streamingIdRef.current
            if (!sid) {
              // 首个事件是 agent_step（LLM 直接调工具没先输出文字）→ 新建消息
              const id = `stream-${Date.now()}-${Math.random().toString(36).slice(2)}`
              streamingIdRef.current = id
              streamingRef.current = true
              setChatMessages((prev) => [
                ...prev,
                {
                  role: 'assistant',
                  content: '',
                  id,
                  timeline: [{ kind: 'step', step }],
                },
              ])
            } else {
              setChatMessages((prev) =>
                prev.map((m) =>
                  m.id === sid
                    ? { ...m, timeline: [...(m.timeline || []), { kind: 'step', step }] }
                    : m,
                ),
              )
            }
          }
          break

        case 'stream_chunk':
          // 流式文字片段：
          // - 若正在为告警卡片生成解释，走 alertExplain 分支（合并进卡片）
          // - 否则追加到当前流式消息的 timeline（与上一个 text 项合并，避免碎片化）
          if (String(msg.request_id || '').startsWith('alert-')) {
            const alertId = String(msg.request_id)
            setIsTyping(false)
            setChatMessages((prev) =>
              prev.map((m) =>
                m.id === alertId
                  ? { ...m, explanation: (m.explanation || '') + msg.data }
                  : m
              )
            )
          } else {
            setIsTyping(false)
            const sid = streamingIdRef.current
            if (!sid) {
              // 首个事件是 stream_chunk（LLM 直接输出文字，没调工具）→ 新建消息
              const id = `stream-${Date.now()}-${Math.random().toString(36).slice(2)}`
              streamingIdRef.current = id
              streamingRef.current = true
              setChatMessages((prev) => [
                ...prev,
                {
                  role: 'assistant',
                  content: msg.data,
                  id,
                  timeline: [{ kind: 'text', text: msg.data }],
                },
              ])
            } else {
              setChatMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== sid) return m
                  const tl: TimelineItem[] = [...(m.timeline || [])]
                  const last = tl[tl.length - 1]
                  if (last && last.kind === 'text') {
                    // 与上一段文字合并，避免几百个碎片节点拖慢渲染
                    tl[tl.length - 1] = { kind: 'text', text: last.text + msg.data }
                  } else {
                    tl.push({ kind: 'text', text: msg.data })
                  }
                  return { ...m, content: m.content + msg.data, timeline: tl }
                }),
              )
            }
          }
          break

        case 'stream_end':
          if (String(msg.request_id || '').startsWith('alert-')) {
            if (alertExplainIdRef.current === msg.request_id) {
              alertExplainIdRef.current = null
            }
            if (pendingAlertsRef.current.length > 0) {
              const next = pendingAlertsRef.current.shift()!
              alertExplainIdRef.current = next.alertId
              chatWsRef.current?.send(JSON.stringify({
                type: 'agent_diagnosis',
                data: next.data,
                request_id: next.alertId,
              }))
            }
            break
          }
          // 流式结束：timeline 已经完整挂在消息上，只需清引用
          streamingRef.current = false
          streamingIdRef.current = null
          alertExplainIdRef.current = null
          setIsTyping(false)
          // 当前流式回答已结束，处理挂起的告警解释请求（一次一个，避免相互打架）
          if (pendingAlertsRef.current.length > 0) {
            const next = pendingAlertsRef.current.shift()!
            alertExplainIdRef.current = next.alertId
            if (chatWsRef.current && chatWsRef.current.readyState === WebSocket.OPEN) {
              chatWsRef.current.send(
                JSON.stringify({
                  type: 'agent_diagnosis',
                  data: next.data,
                  request_id: next.alertId,
                })
              )
            }
          }
          break

        case 'system':
        case 'reply':
        case 'agent_explanation':
          setIsTyping(false)
          streamingRef.current = false
          // 告警解释的兜底（大模型流式失败时的一次性消息）：同样合并进卡片
          if (msg.type === 'agent_explanation' && (msg.request_id || alertExplainIdRef.current)) {
            const alertId = String(msg.request_id || alertExplainIdRef.current)
            alertExplainIdRef.current = null
            setChatMessages((prev) =>
              prev.map((m) =>
                m.id === alertId ? { ...m, explanation: msg.data } : m
              )
            )
          } else {
            setChatMessages((prev) => [
              ...prev,
              {
                role: msg.type === 'agent_explanation' ? 'agent' : 'assistant',
                content: msg.data,
              },
            ])
          }
          break

        // ── 模板工作流事件 ──
        case 'template_start':
          {
            const d = msg.data as {
              template_id: string
              title: string
              icon: string
              stages: string[]
              overview?: string
              conditions?: string[]
              models?: string[]
              outputs?: string[]
              work_dir?: string
              artifacts?: string[]
            }
            setTemplateState({
              templateId: d.template_id,
              title: d.title,
              icon: d.icon,
              stages: d.stages.map((label, i) => ({ index: i, label, status: 'pending' as const })),
              running: true,
              summary: '',
              summaryStreaming: false,
              startedAt: Date.now(),
              anchorMessageId: pendingTemplateAnchorRef.current ?? undefined,
              overview: d.overview,
              conditions: d.conditions,
              models: d.models,
              outputs: d.outputs,
              workDir: d.work_dir,
              artifacts: d.artifacts,
            })
            pendingTemplateAnchorRef.current = null
            setIsTyping(false)
          }
          break

        case 'template_stage':
          {
            const d = msg.data as { index: number; label: string; status: string; detail?: string; output?: string }
            setTemplateState((prev) => {
              if (!prev) return prev
              const stages = prev.stages.map((stage) =>
                stage.index === d.index
                  ? {
                      ...stage,
                      status: d.status as TemplateStageState['status'],
                      detail: d.detail ?? stage.detail,
                      output: d.output ?? stage.output,
                    }
                  : stage,
              )
              return { ...prev, stages }
            })
          }
          break

        case 'template_summary_chunk':
          setTemplateState((prev) =>
            prev
              ? {
                  ...prev,
                  summary: prev.summary + String(msg.data ?? ''),
                  summaryStreaming: true,
                }
              : prev,
          )
          break

        case 'template_summary_end':
          setTemplateState((prev) =>
            prev ? { ...prev, summaryStreaming: false } : prev,
          )
          break

        case 'template_end':
          {
            const d = msg.data as {
              template_id: string
              success: boolean
              duration_seconds?: number
              job_id?: string
              job_output?: string
              work_dir?: string
              artifacts?: string[]
            }
            setTemplateState((prev) => {
              if (!prev) return prev
              return {
                ...prev,
                running: false,
                success: d.success,
                summaryStreaming: false,
                durationSeconds: d.duration_seconds,
                jobId: d.job_id,
                jobOutput: d.job_output,
                workDir: d.work_dir ?? prev.workDir,
                artifacts: d.artifacts ?? prev.artifacts,
              }
            })
          }
          break
      }
    }

    ws.onclose = (ev: CloseEvent) => {
      // 已被新连接替代的旧 socket 关闭时，不得清理新连接状态或触发重连。
      if (chatWsRef.current !== ws) return
      // 打印关闭码到浏览器控制台，方便定位是谁把连接关掉的：
      // 1000=正常, 1001=going away(切页/刷新), 1006=异常(代理/网络丢包，无 close frame),
      // 1011=服务端异常, 1012/1013=服务重启/过载
      console.warn(
        `[chat_ws] closed code=${ev.code} reason=${JSON.stringify(ev.reason)} wasClean=${ev.wasClean}`,
      )
      // 停止心跳
      if (chatPingTimerRef.current !== null) {
        window.clearInterval(chatPingTimerRef.current)
        chatPingTimerRef.current = null
      }
      // 清空进行中的状态：isTyping / streaming refs
      // 否则重连后前端会一直显示断开前那一刻的"卡住"状态
      setIsTyping(false)
      streamingRef.current = false
      streamingIdRef.current = null
      alertExplainIdRef.current = null
      pendingAlertsRef.current = []
      // 自动重连（最多 5 次，每次间隔 1.5 秒），静默进行不打扰对话
      if (chatRetriesRef.current < 5) {
        chatRetriesRef.current += 1
        chatReconnectTimerRef.current = window.setTimeout(() => {
          connectChatRef.current()
        }, 1500)
      }
    }

    return ws
  }, [])

  // 始终让 ref 指向最新的 connectChat，供 onclose 安全地递归调用
  connectChatRef.current = connectChat

  // 发送终端命令（echo=true 时后端会在终端显示命令文本，用于按钮触发场景）
  const sendCommand = useCallback(
    (cmd: string, echo = false) => {
      const payload = JSON.stringify({ command: cmd, echo })
      if (!terminalWsRef.current || terminalWsRef.current.readyState !== WebSocket.OPEN) {
        // 如果还没连接，先连接
        connectTerminal()
        // 等待连接建立后发送（简化处理）
        setTimeout(() => {
          terminalWsRef.current?.send(payload)
        }, 500)
      } else {
        terminalWsRef.current.send(payload)
      }
    },
    [connectTerminal]
  )

  // 发送多行粘贴文本（作为一整块发给后端，避免逐行执行）
  const sendPaste = useCallback(
    (text: string) => {
      const payload = JSON.stringify({ type: 'paste', data: text })
      if (!terminalWsRef.current || terminalWsRef.current.readyState !== WebSocket.OPEN) {
        connectTerminal()
        setTimeout(() => {
          terminalWsRef.current?.send(payload)
        }, 500)
      } else {
        terminalWsRef.current.send(payload)
      }
    },
    [connectTerminal]
  )

  // 发送对话消息
  const sendChatMessage = useCallback(
    (msg: string) => {
      // 先在本地显示用户消息
      setChatMessages((prev) => [...prev, { role: 'user', content: msg }])

      const requestId = `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`
      const recentHistory = chatMessages
        .filter((message) =>
          (message.role === 'user' || message.role === 'assistant') && Boolean(message.content),
        )
        .slice(-8)
        .map((message) => ({ role: message.role, content: message.content }))
      const templateContext = templateState && !templateState.running
        ? {
            title: templateState.title,
            job_id: templateState.jobId,
            success: templateState.success,
            summary: templateState.summary,
            work_dir: templateState.workDir,
            artifacts: templateState.artifacts,
          }
        : undefined
      const payload = JSON.stringify({
        type: 'user_message',
        data: msg,
        request_id: requestId,
        history: recentHistory,
        template_context: templateContext,
      })
      if (!chatWsRef.current || chatWsRef.current.readyState !== WebSocket.OPEN) {
        chatOutboundQueueRef.current.push(payload)
        connectChat()
      } else {
        chatWsRef.current.send(payload)
      }
    },
    [chatMessages, connectChat, templateState]
  )

  // 立即连接所有 WebSocket（在 /api/connect 成功后调用）
  const connectAll = useCallback(() => {
    connectTerminal()
    connectChat()
  }, [connectTerminal, connectChat])

  // 触发预设模板工作流（发送到 chat_ws，后端按阶段执行并推送进度）
  const sendRunTemplate = useCallback(
    (templateId: string, request: string) => {
      // 对话区展示用户真正想做的事；内部模板 ID 只用于前后端协议。
      const messageId = `template-request-${Date.now()}-${Math.random().toString(36).slice(2)}`
      pendingTemplateAnchorRef.current = messageId
      setChatMessages((prev) => [
        ...prev,
        { role: 'user', content: request, id: messageId },
      ])
      const payload = JSON.stringify({
        type: 'run_template',
        data: { id: templateId, request },
      })
      if (!chatWsRef.current || chatWsRef.current.readyState !== WebSocket.OPEN) {
        chatOutboundQueueRef.current.push(payload)
        connectChat()
      } else {
        chatWsRef.current.send(payload)
      }
    },
    [connectChat]
  )

  return {
    terminalWs: terminalWsRef,
    chatWs: chatWsRef,
    sendCommand,
    sendPaste,
    sendChatMessage,
    sendRunTemplate,
    terminalMessages,
    chatMessages,
    isTyping,
    jobs,
    templateState,
    connectTerminal,
    connectChat,
    connectAll,
  }
}
