import { useRef, useState, useCallback, useEffect } from 'react'
import type { TerminalMessage } from '../components/Terminal'
import type { ChatMessage } from '../components/ChatPanel'
import type { AgentAlert } from '../App'

/** 作业队列小组件用：单个作业的状态快照 */
export interface JobInfo {
  job_id: string
  state: string
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
  const streamingRef = useRef(false)
  const streamingIdRef = useRef<string | null>(null)
  // 当前待填充 LLM 解释的告警卡片 id（让解释流式进卡片，而不是另起一条消息）
  const alertExplainIdRef = useRef<string | null>(null)
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
        alertExplainIdRef.current = alertId
        setChatMessages((prev) => [
          ...prev,
          { role: 'alert', content: '', alert, id: alertId },
        ])
        // 转发到对话 WebSocket，触发大模型生成通俗解释
        if (chatWsRef.current && chatWsRef.current.readyState === WebSocket.OPEN) {
          chatWsRef.current.send(
            JSON.stringify({ type: 'agent_diagnosis', data: msg.data })
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
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)

      switch (msg.type) {
        case 'typing':
          // 大模型正在思考
          setIsTyping(true)
          streamingRef.current = false
          break

        case 'stream_chunk':
          // 流式片段：
          // - 若正在为某张告警卡片生成通俗解释，追加进该卡片的 explanation（合并展示）
          // - 否则追加到"正在流式输出"的普通消息（按 id 定位，避免丢字）
          if (alertExplainIdRef.current) {
            const alertId = alertExplainIdRef.current
            setIsTyping(false)
            setChatMessages((prev) =>
              prev.map((m) =>
                m.id === alertId
                  ? { ...m, explanation: (m.explanation || '') + msg.data }
                  : m
              )
            )
          } else if (!streamingRef.current) {
            // 第一个 chunk：创建带唯一 id 的新消息
            streamingRef.current = true
            setIsTyping(false)
            const id = `stream-${Date.now()}-${Math.random().toString(36).slice(2)}`
            streamingIdRef.current = id
            setChatMessages((prev) => [
              ...prev,
              { role: 'assistant', content: msg.data, id },
            ])
          } else {
            // 后续 chunk：按 id 精确追加
            const sid = streamingIdRef.current
            setChatMessages((prev) =>
              prev.map((m) =>
                m.id === sid ? { ...m, content: m.content + msg.data } : m
              )
            )
          }
          break

        case 'stream_end':
          // 流式结束
          streamingRef.current = false
          streamingIdRef.current = null
          alertExplainIdRef.current = null
          setIsTyping(false)
          break

        case 'system':
        case 'reply':
        case 'agent_explanation':
          setIsTyping(false)
          streamingRef.current = false
          // 告警解释的兜底（大模型流式失败时的一次性消息）：同样合并进卡片
          if (msg.type === 'agent_explanation' && alertExplainIdRef.current) {
            const alertId = alertExplainIdRef.current
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
      }
    }

    ws.onclose = () => {
      // 停止心跳
      if (chatPingTimerRef.current !== null) {
        window.clearInterval(chatPingTimerRef.current)
        chatPingTimerRef.current = null
      }
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

  // 发送对话消息
  const sendChatMessage = useCallback(
    (msg: string) => {
      // 先在本地显示用户消息
      setChatMessages((prev) => [...prev, { role: 'user', content: msg }])

      if (!chatWsRef.current || chatWsRef.current.readyState !== WebSocket.OPEN) {
        connectChat()
        setTimeout(() => {
          chatWsRef.current?.send(
            JSON.stringify({ type: 'user_message', data: msg })
          )
        }, 500)
      } else {
        chatWsRef.current.send(JSON.stringify({ type: 'user_message', data: msg }))
      }
    },
    [connectChat]
  )

  // 立即连接所有 WebSocket（在 /api/connect 成功后调用）
  const connectAll = useCallback(() => {
    connectTerminal()
    connectChat()
  }, [connectTerminal, connectChat])

  return {
    terminalWs: terminalWsRef,
    chatWs: chatWsRef,
    sendCommand,
    sendChatMessage,
    terminalMessages,
    chatMessages,
    isTyping,
    jobs,
    connectTerminal,
    connectChat,
    connectAll,
  }
}
