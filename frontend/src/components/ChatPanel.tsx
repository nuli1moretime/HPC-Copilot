import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import type { AgentAlert } from '../App'
import type { JobInfo, AgentStep, TemplateState, TemplateStageState } from '../hooks/useWebSocket'
import { JOB_TEMPLATES } from '../data/jobTemplates'

/**
 * 单条 AI 消息内部的时间线元素：文字流和 agent 工具步骤按到达顺序交叉排列。
 * 这样即使 agent 跑了很多轮，最新的文字永远在气泡底部可见，不会被步骤卡挤走。
 */
export type TimelineItem =
  | { kind: 'text'; text: string }
  | { kind: 'step'; step: AgentStep }

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'agent' | 'alert'
  content: string
  timestamp?: number
  alert?: AgentAlert
  id?: string
  /** 告警卡片专用：LLM 通俗解释，流式填充进卡片内部（与卡片合并为一段） */
  explanation?: string
  /**
   * 交叉时间线：agent 步骤和流式文字按时间顺序混排。
   * 新的渲染路径优先使用它；若为空则回退到 content 单独渲染。
   */
  timeline?: TimelineItem[]
  /** @deprecated 用 timeline 替代；保留只为兼容旧数据 */
  agentSteps?: AgentStep[]
}

interface Props {
  messages: ChatMessage[]
  onSend: (msg: string) => void
  onRunCommand?: (cmd: string) => void
  onRunTemplate?: (templateId: string, request: string) => void
  isTyping?: boolean
  jobs?: JobInfo[]
  templateState?: TemplateState | null
}

type PendingExecution = {
  kind: 'command' | 'script'
  content: string
}

// 从 React 子节点中提取纯文本（代码块内容可能是字符串或嵌套节点）
function extractText(node: React.ReactNode): string {
  if (node == null) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (typeof node === 'object' && 'props' in (node as React.ReactElement)) {
    return extractText((node as React.ReactElement).props.children)
  }
  return ''
}

const SHELL_LANGUAGES = new Set(['bash', 'sh', 'shell', 'zsh'])
const COMMON_HPC_COMMANDS = new Set([
  'apptainer', 'cat', 'cd', 'chmod', 'command', 'conda', 'cp', 'df', 'du', 'echo',
  'find', 'gcc', 'g++', 'grep', 'head', 'ls', 'mkdir', 'module', 'mpicxx', 'nvcc',
  'nvidia-smi', 'pwd', 'python', 'python3', 'quota', 'sacct', 'sacctmgr', 'sbatch',
  'scancel', 'scontrol', 'sinfo', 'singularity', 'squeue', 'srun', 'source', 'tail',
  'which',
])

function normalizeRunnableCommand(text: string): string {
  return text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.replace(/^\s*\$\s?/, ''))
    .join('\n')
    .trim()
}

function hasUnresolvedPlaceholder(text: string): boolean {
  return /<[^>\n]+>|\{(?:job_id|user|username|partition|qos)[^}\n]*\}|(?:请替换|你的?用户名|作业号)/i.test(text)
}

function isRunnableShellBlock(text: string, className?: string): boolean {
  const command = normalizeRunnableCommand(text)
  if (!command || /(^|[;&|]\s*)(?:rm|sudo|shutdown|reboot|mkfs|dd|kill|pkill|chown)\b/im.test(command)) {
    return false
  }
  if (/<<\s*['"]?[A-Z0-9_]+|(^|\s)>{1,2}\s*\/(?:etc|root|boot|usr)\b/im.test(command)) {
    return false
  }

  const language = className?.match(/language-([\w+-]+)/i)?.[1]?.toLowerCase()
  const lines = command
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
  if (lines.length === 0 || lines.length > 12) return false

  // 即使模型把普通输出误标为 bash，也只允许常见平台命令出现运行入口。
  const allLinesAreCommands = lines.every((line) => {
    const firstToken = line.match(/^(?:env\s+)?([\w.+-]+)/)?.[1]?.toLowerCase()
    return Boolean(firstToken && COMMON_HPC_COMMANDS.has(firstToken))
  })
  if (!allLinesAreCommands) return false
  return !language || SHELL_LANGUAGES.has(language)
}

// ─── 作业状态 → 颜色/中文映射（作业队列小组件用） ───
function jobStateMeta(state: string): { color: string; label: string } {
  const s = state.toUpperCase()
  if (s === 'RUNNING') return { color: 'var(--info)', label: '运行中' }
  if (s === 'PENDING') return { color: 'var(--warning)', label: '排队中' }
  if (s === 'COMPLETED') return { color: 'var(--accent-green)', label: '已完成' }
  if (['FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL', 'PREEMPTED', 'CANCELLED'].includes(s)) {
    return { color: 'var(--error)', label: s }
  }
  return { color: 'var(--text-muted)', label: s }
}

// ─── 诊断卡片严重级别 → 顶部重音条渐变 ───
function severityBar(errorType: string): string {
  const t = errorType.toLowerCase()
  if (t.includes('timeout')) return 'linear-gradient(90deg, #fbbf24, #f59e0b)'
  if (t.includes('memory') || t.includes('oom')) return 'linear-gradient(90deg, #f87171, #fb923c)'
  if (t.includes('qos') || t.includes('permission')) return 'linear-gradient(90deg, #60a5fa, #3b82f6)'
  return 'linear-gradient(90deg, #f87171, #fb923c)' // 默认：错误
}

function severityIcon(errorType: string): string {
  const t = errorType.toLowerCase()
  if (t.includes('timeout')) return '⏱'
  if (t.includes('memory') || t.includes('oom')) return '💾'
  if (t.includes('qos') || t.includes('permission')) return '🔑'
  return '⚠️'
}

// Markdown 渲染组件（深色主题样式）
function MarkdownContent({
  content,
  onRunCommand,
  onRunScript,
}: {
  content: string
  onRunCommand?: (command: string) => void
  onRunScript?: (script: string) => void
}) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        components={{
          h1: ({ children }) => <h1 className="text-base font-bold mt-3 mb-1">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-bold mt-3 mb-1">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-bold mt-2 mb-1">{children}</h3>,
          h4: ({ children }) => <h4 className="text-sm font-semibold mt-2 mb-1">{children}</h4>,
          p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          code: ({ className, children }) => {
            const rawText = extractText(children)
            const isBlock = className?.includes('language-') || rawText.endsWith('\n')
            if (isBlock) {
              const text = rawText.replace(/\n$/, '')
              const isSbatch = /(^|\n)#SBATCH/.test(text)
              const runnableCommand = !isSbatch && isRunnableShellBlock(text, className)
              const normalizedCommand = normalizeRunnableCommand(text)
              const hasPlaceholder = hasUnresolvedPlaceholder(normalizedCommand)
              return (
                <span className="block my-2">
                  <code className="block bg-[var(--bg-deep)] border border-[var(--border)] rounded-[var(--radius-md)] px-3 py-2 text-xs text-[#7ab8f5] overflow-x-auto whitespace-pre font-mono-term">
                    {text}
                  </code>
                  {(isSbatch || runnableCommand) && (
                    <span className="flex gap-2 mt-1.5">
                      <button
                        onClick={() => navigator.clipboard.writeText(text).catch(() => {})}
                        className="text-xs px-2.5 py-1 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-ai)] hover:border-[var(--accent-blue-end)] text-[var(--text-secondary)] transition-colors"
                      >
                        📋 {isSbatch ? '复制脚本' : '复制命令'}
                      </button>
                      {isSbatch && onRunScript && (
                        <button
                          onClick={() => onRunScript(text)}
                          className="text-xs px-2.5 py-1 rounded-[var(--radius-sm)] text-blue-100 transition-all hover:-translate-y-px"
                          style={{ background: 'var(--grad-blue)', border: '1px solid var(--accent-blue-end)' }}
                          title="写入集群并用 sbatch 提交"
                        >
                          🚀 提交作业
                        </button>
                      )}
                      {runnableCommand && onRunCommand && (
                        <button
                          onClick={() => onRunCommand(normalizedCommand)}
                          disabled={hasPlaceholder}
                          className="text-xs px-2.5 py-1 rounded-[var(--radius-sm)] text-blue-100 transition-all hover:-translate-y-px disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:translate-y-0"
                          style={{
                            background: hasPlaceholder ? 'var(--bg-elevated)' : 'var(--grad-blue)',
                            border: `1px solid ${hasPlaceholder ? 'var(--border)' : 'var(--accent-blue-end)'}`,
                          }}
                          title={hasPlaceholder ? '命令含未替换的占位符，请替换后再运行' : '确认后在当前连接的平台上运行'}
                        >
                          ▶ 运行命令
                        </button>
                      )}
                    </span>
                  )}
                </span>
              )
            }
            return (
              <code className="bg-[var(--bg-deep)] border border-[var(--border)] rounded px-1.5 py-0.5 text-xs text-[#7ab8f5] font-mono-term">
                {children}
              </code>
            )
          },
          pre: ({ children }) => <pre className="mb-2">{children}</pre>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--info)] underline hover:opacity-80">
              {children}
            </a>
          ),
          strong: ({ children }) => <strong className="font-bold text-white">{children}</strong>,
          hr: () => <hr className="border-[var(--border)] my-3" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-[var(--border-ai)] pl-3 my-2 text-[var(--text-secondary)]">{children}</blockquote>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2 max-w-full rounded-[var(--radius-sm)]">
              <table className="border-collapse text-xs w-max min-w-full">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-[var(--border)] px-2 py-1 bg-[var(--bg-elevated)] text-left">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border border-[var(--border)] px-2 py-1">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

// ─── 作业队列小组件 ───
function JobsWidget({ jobs }: { jobs: JobInfo[] }) {
  if (!jobs || jobs.length === 0) {
    return (
      <div className="flex items-center gap-1.5 px-2.5 h-7 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-ai)]">
        <span className="text-[11px] text-[var(--text-muted)]">队列空闲 · 暂无作业</span>
      </div>
    )
  }
  // 只显示最新 3 个作业
  const shown = jobs.slice(-3).reverse()
  return (
    <div className="flex items-center gap-1.5">
      {shown.map((j) => {
        const meta = jobStateMeta(j.state)
        return (
          <div
            key={j.job_id}
            className="flex items-center gap-1.5 px-2 h-7 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-ai)]"
            title={`作业 ${j.job_id}: ${meta.label}`}
          >
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: meta.color }} />
            <span className="text-[11px] font-mono-term text-[var(--text-secondary)]">#{j.job_id}</span>
            <span className="text-[11px]" style={{ color: meta.color }}>{meta.label}</span>
          </div>
        )
      })}
    </div>
  )
}

function TemplateProgressCard({ state }: { state: TemplateState }) {
  const [now, setNow] = useState(Date.now())
  const [detailsOpen, setDetailsOpen] = useState(state.running)

  useEffect(() => {
    if (!state.running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [state.running])

  useEffect(() => {
    if (!state.running) setDetailsOpen(false)
  }, [state.running])

  const completed = state.stages.filter((stage) => stage.status === 'done').length
  const active = state.stages.find((stage) => stage.status === 'running')
  const progress = state.stages.length > 0 ? (completed / state.stages.length) * 100 : 0
  const elapsed = state.durationSeconds ?? Math.max(0, Math.floor((now - state.startedAt) / 1000))

  return (
    <div className="flex justify-start workflow-enter">
      <div
        className="w-full max-w-[94%] rounded-[var(--radius-lg)] overflow-hidden"
        style={{
          background: 'linear-gradient(180deg, #111a27, #0c1625)',
          border: '1px solid var(--border-ai)',
          boxShadow: state.running ? '0 0 24px rgba(0,81,168,.10)' : 'none',
          borderRadius: '10px 10px 10px 2px',
        }}
      >
        <div className="p-3.5 pb-3">
          <div className="flex items-center gap-2.5">
            <span className={`text-lg ${state.running ? 'workflow-icon-live' : ''}`}>{state.icon}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-[var(--text-primary)]">{state.title}</span>
                {state.jobId && (
                  <span className="text-[10px] font-mono-term text-[var(--info)]">#{state.jobId}</span>
                )}
              </div>
              <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
                {state.running
                  ? active?.detail || '正在准备下一步'
                  : state.success
                    ? `全部完成 · 用时 ${elapsed.toFixed(1)} 秒`
                    : `执行中断 · 用时 ${elapsed.toFixed(1)} 秒`}
              </div>
            </div>
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full border ${
                state.running
                  ? 'text-blue-300 border-blue-500/30 bg-blue-500/10'
                  : state.success
                    ? 'text-green-300 border-green-500/30 bg-green-500/10'
                    : 'text-red-300 border-red-500/30 bg-red-500/10'
              }`}
            >
              {state.running ? `运行中 · ${elapsed}s` : state.success ? '✓ 已完成' : '✕ 未完成'}
            </span>
          </div>

          {state.overview && (
            <div className="mt-3 rounded-[var(--radius-md)] border border-[rgba(96,165,250,.18)] bg-[rgba(0,81,168,.055)] px-3 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--info)]">
                本次计算说明
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-[var(--text-secondary)]">
                {state.overview}
              </p>

              <div className="mt-2 grid grid-cols-1 gap-2 text-[10px] sm:grid-cols-3">
                {state.conditions && state.conditions.length > 0 && (
                  <div>
                    <div className="font-medium text-[var(--text-primary)]">工况参数</div>
                    <ul className="mt-1 space-y-0.5 text-[var(--text-muted)]">
                      {state.conditions.map((item) => <li key={item}>• {item}</li>)}
                    </ul>
                  </div>
                )}
                {state.models && state.models.length > 0 && (
                  <div>
                    <div className="font-medium text-[var(--text-primary)]">计算方法</div>
                    <ul className="mt-1 space-y-0.5 text-[var(--text-muted)]">
                      {state.models.map((item) => <li key={item}>• {item}</li>)}
                    </ul>
                  </div>
                )}
                {state.outputs && state.outputs.length > 0 && (
                  <div>
                    <div className="font-medium text-[var(--text-primary)]">将得到什么</div>
                    <ul className="mt-1 space-y-0.5 text-[var(--text-muted)]">
                      {state.outputs.map((item) => <li key={item}>• {item}</li>)}
                    </ul>
                  </div>
                )}
              </div>

              {state.workDir && (
                <div className="mt-2 border-t border-[var(--border-ai)] pt-2 text-[10px] text-[var(--text-muted)]">
                  工作目录：<code className="font-mono-term text-[#8fc4f4]">{state.workDir}</code>
                  {state.artifacts && state.artifacts.length > 0 && (
                    <span className="ml-3">
                      主要结果：
                      <code className="font-mono-term text-[#8fc4f4]">
                        {state.artifacts[0].replace('{job_id}', state.jobId || '<作业号>')}
                      </code>
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="h-1.5 rounded-full bg-[var(--bg-deep)] overflow-hidden mt-3">
            <div
              className={`h-full rounded-full transition-all duration-500 ${state.running ? 'workflow-progress-live' : ''}`}
              style={{
                width: `${state.running ? Math.max(progress, 4) : state.success ? 100 : progress}%`,
                background: state.success === false ? 'var(--error)' : 'linear-gradient(90deg, #0051a8, #38bdf8)',
              }}
            />
          </div>

          <div className="flex items-center justify-between mt-1.5 text-[10px] text-[var(--text-dim)]">
            <span>{active ? `第 ${active.index + 1} 步，共 ${state.stages.length} 步` : `${completed}/${state.stages.length} 步完成`}</span>
            {state.running && (
              <span className="flex items-center gap-1 text-[var(--text-muted)]">
                集群正在处理
                <span className="typing-dot !w-1 !h-1" />
                <span className="typing-dot !w-1 !h-1" />
                <span className="typing-dot !w-1 !h-1" />
              </span>
            )}
          </div>
        </div>

        <details
          className="group border-t border-[var(--border-ai)]"
          open={detailsOpen}
          onToggle={(event) => setDetailsOpen(event.currentTarget.open)}
        >
          <summary className="cursor-pointer list-none px-3.5 py-2 text-[10px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] flex items-center justify-between">
            <span>{state.running ? '实时执行记录' : '查看执行记录'}</span>
            <span className="group-open:rotate-90 transition-transform">›</span>
          </summary>
          <div className="px-3.5 pb-3 space-y-2">
            {state.stages.map((stage: TemplateStageState) => (
              <div key={stage.index} className="flex items-start gap-2 text-[11px]">
                <span className="flex-shrink-0 w-4 text-center mt-px">
                  {stage.status === 'done' && <span className="text-green-400">✓</span>}
                  {stage.status === 'running' && <span className="workflow-ring" />}
                  {stage.status === 'error' && <span className="text-red-400">✕</span>}
                  {stage.status === 'pending' && <span className="text-[var(--text-dim)]">○</span>}
                </span>
                <div className="min-w-0 flex-1">
                  <div className={
                    stage.status === 'running' ? 'text-[var(--text-primary)] font-medium' :
                    stage.status === 'error' ? 'text-red-400' :
                    stage.status === 'done' ? 'text-[var(--text-secondary)]' : 'text-[var(--text-dim)]'
                  }>
                    {stage.label}
                  </div>
                  {stage.detail && stage.status !== 'pending' && (
                    <div className="text-[10px] text-[var(--text-muted)] mt-0.5 break-words">{stage.detail}</div>
                  )}
                  {stage.status === 'error' && stage.output && (
                    <pre className="mt-1 text-[10px] text-red-300/80 whitespace-pre-wrap break-words font-mono-term">{stage.output}</pre>
                  )}
                </div>
              </div>
            ))}
          </div>
        </details>

        {(state.summary || state.summaryStreaming) && (
          <div className="border-t border-[var(--border-ai)] p-3.5 bg-[rgba(0,81,168,.045)]">
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-[var(--info)] mb-2">
              <span>✦</span>
              <span>运行结论</span>
              {state.summaryStreaming && <span className="text-[var(--text-muted)] normal-case tracking-normal">生成中…</span>}
            </div>
            <div className="text-xs text-[var(--text-secondary)]">
              <MarkdownContent content={state.summary || '正在整理输出…'} />
            </div>
          </div>
        )}

        {!state.running && state.jobOutput && (
          <details className="group border-t border-[var(--border-ai)]">
            <summary className="cursor-pointer list-none px-3.5 py-2 text-[10px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] flex items-center justify-between">
              <span>查看作业原始输出</span>
              <span className="group-open:rotate-90 transition-transform">›</span>
            </summary>
            <pre className="mx-3.5 mb-3 max-h-52 overflow-auto rounded bg-[var(--bg-deep)] border border-[var(--border)] p-2.5 text-[10px] leading-relaxed text-[#9cc7ef] whitespace-pre-wrap font-mono-term">
              {state.jobOutput}
            </pre>
          </details>
        )}
      </div>
    </div>
  )
}

// ─── Agent 步骤卡片（展示工具调用过程） ───
function AgentStepsCard({ steps, live = false }: { steps: AgentStep[]; live?: boolean }) {
  const [expanded, setExpanded] = useState(live)
  // 只展示 tool_call 和 tool_result（thinking 步骤用动画暗示即可）
  const toolSteps = steps.filter((s) => s.action === 'tool_call' || s.action === 'tool_result' || s.action === 'error')
  const isThinking = live && steps.length > 0 && steps[steps.length - 1].action === 'thinking'
  const lastTool = toolSteps.length > 0 ? toolSteps[toolSteps.length - 1] : null

  if (steps.length === 0) return null

  return (
    <div
      className="rounded-[var(--radius-lg)] border border-[var(--border-ai)] overflow-hidden mb-2"
      style={{ background: 'linear-gradient(180deg, #0d1520, #0a1018)' }}
    >
      {/* 头部：摘要行，点击展开/折叠 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[rgba(255,255,255,.02)] transition-colors"
      >
        <span className="text-xs text-[var(--info)]">
          {live ? (isThinking ? '🤔' : '⚡') : '🔧'}
        </span>
        <span className="text-[11px] text-[var(--text-secondary)] flex-1 truncate">
          {live
            ? isThinking
              ? `Agent 思考中（第 ${steps[steps.length - 1].round || 1} 轮）...`
              : lastTool?.action === 'tool_call'
                ? `正在执行: ${lastTool.tool}...`
                : lastTool?.action === 'tool_result'
                  ? `${lastTool.tool} 完成，继续推理...`
                  : 'Agent 执行中...'
            : `Agent 执行了 ${toolSteps.filter((s) => s.action === 'tool_call').length} 步操作`}
        </span>
        {live && (
          <span className="flex gap-0.5">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </span>
        )}
        <span className="text-[10px] text-[var(--text-muted)]">{expanded ? '▼' : '▶'}</span>
      </button>

      {/* 展开内容：每一步的详情 */}
      {expanded && (
        <div className="px-3 pb-2.5 space-y-1.5 border-t border-[var(--border-ai)]">
          {toolSteps.map((step, i) => (
            <div key={i} className="text-[11px]">
              {step.action === 'tool_call' && (
                <div className="flex items-start gap-1.5 mt-1.5">
                  <span className="text-[var(--info)] flex-shrink-0 mt-px">▸</span>
                  <div className="min-w-0">
                    <span className="text-[var(--accent-green)] font-mono-term">{step.tool}</span>
                    {step.args && (
                      <span className="text-[var(--text-muted)] ml-1.5 break-all">
                        {Object.entries(step.args).map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 80) : JSON.stringify(v)}`).join(', ')}
                      </span>
                    )}
                  </div>
                </div>
              )}
              {step.action === 'tool_result' && (
                <div className="flex items-start gap-1.5 mt-0.5">
                  <span className="text-[var(--text-muted)] flex-shrink-0 mt-px">←</span>
                  <code className="text-[var(--text-muted)] font-mono-term break-all line-clamp-3 bg-[var(--bg-deep)] rounded px-1.5 py-0.5 border border-[var(--border)] max-w-full overflow-hidden">
                    {(step.result || '').slice(0, 300)}
                    {(step.result || '').length > 300 ? '...' : ''}
                  </code>
                </div>
              )}
              {step.action === 'error' && (
                <div className="flex items-start gap-1.5 mt-1.5">
                  <span className="text-[var(--error)] flex-shrink-0">✗</span>
                  <span className="text-[var(--error)]">{step.message}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 时间线单步行（内嵌在 AI 气泡里，与流式文字交叉显示） ───
function TimelineStepRow({ step, isLast }: { step: AgentStep; isLast?: boolean }) {
  if (step.action === 'thinking') {
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)] italic py-0.5">
        <span>🤔</span>
        <span>继续推理（第 {step.round || 1} 轮）</span>
        {isLast && (
          <span className="flex gap-0.5 ml-1">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </span>
        )}
      </div>
    )
  }
  if (step.action === 'tool_call') {
    const argsPreview = step.args
      ? Object.entries(step.args)
          .map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 100) : JSON.stringify(v).slice(0, 100)}`)
          .join(' ')
      : ''
    return (
      <div
        className="flex items-start gap-1.5 text-[11px] rounded-[var(--radius-sm)] px-2 py-1 my-0.5"
        style={{
          background: 'rgba(122,184,245,.06)',
          borderLeft: '2px solid var(--info)',
        }}
      >
        <span className="text-[var(--info)] flex-shrink-0 mt-px">▸</span>
        <div className="min-w-0 flex-1">
          <span className="text-[var(--accent-green)] font-mono-term font-medium">{step.tool}</span>
          {argsPreview && (
            <span className="text-[var(--text-muted)] ml-1.5 break-all font-mono-term">{argsPreview}</span>
          )}
        </div>
      </div>
    )
  }
  if (step.action === 'tool_result') {
    const full = step.result || ''
    const preview = full.length > 300 ? full.slice(0, 300) + '...' : full
    return (
      <details
        className="text-[11px] rounded-[var(--radius-sm)] px-2 py-1 my-0.5 group"
        style={{
          background: 'var(--bg-deep)',
          borderLeft: '2px solid var(--border)',
        }}
      >
        <summary className="cursor-pointer list-none flex items-start gap-1.5 select-none">
          <span className="text-[var(--text-muted)] flex-shrink-0 mt-px">←</span>
          <code className="text-[var(--text-muted)] font-mono-term break-all line-clamp-2 flex-1">
            {preview}
          </code>
          <span className="text-[9px] text-[var(--text-muted)] opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
            {full.length > 300 ? '展开' : ''}
          </span>
        </summary>
        {full.length > 300 && (
          <code className="block mt-1 text-[var(--text-muted)] font-mono-term break-all whitespace-pre-wrap max-h-48 overflow-y-auto">
            {full}
          </code>
        )}
      </details>
    )
  }
  if (step.action === 'error') {
    return (
      <div className="flex items-start gap-1.5 text-[11px] text-[var(--error)] py-0.5">
        <span className="flex-shrink-0">✗</span>
        <span>{step.message}</span>
      </div>
    )
  }
  return null
}

// ─── 快捷指令 chips ───
// 空状态（引导页）使用的默认指令
const DEFAULT_PROMPTS = [
  '帮我写一个 GPU 计算作业脚本',
  '解释一下 squeue 命令',
  '我的作业为什么失败了？',
]

// 根据最近一条 AI 侧消息的内容，动态生成"下一步"引导指令
function suggestNext(messages: ChatMessage[]): string[] {
  // 从后往前找最近一条 AI 消息（assistant / agent / alert），跳过 user / system
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role === 'user' || m.role === 'system') continue

    // 告警卡片 → 错误处理引导
    if (m.role === 'alert') {
      return ['这个错误严重吗？', '如何避免再次出现？', '帮我分析作业日志']
    }

    const text = (m.content || '') + (m.explanation || '')
    // 生成了作业脚本 → 参数解释 / 调整 / 提交引导
    if (text.includes('#SBATCH')) {
      return ['解释脚本里的关键参数', '换成 A100 分区重新生成', '如何提交这个作业？']
    }
    // 解释了某个 Slurm 命令 → 深入 / 举例引导
    if (/\b(squeue|sbatch|sinfo|scancel|scontrol)\b/.test(text)) {
      return ['这个命令还有哪些常用参数？', '举一个实际使用的例子', '其他常用 Slurm 命令']
    }
    // 诊断了作业失败 → 重试 / 日志 / 预防引导
    if (/失败|FAILED|OOM|TIMEOUT/i.test(text)) {
      return ['如何重新提交作业？', '怎样查看作业日志？', '如何避免再次失败？']
    }
    // 最近一条 AI 消息没有明确主题 → 用默认指令
    return DEFAULT_PROMPTS
  }
  return DEFAULT_PROMPTS
}

export default function ChatPanel({ messages, onSend, onRunCommand, onRunTemplate, isTyping, jobs = [], templateState }: Props) {
  const [input, setInput] = useState('')
  const [pendingExecution, setPendingExecution] = useState<PendingExecution | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部（timeline 变化会通过 messages 引用变更触发）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping, templateState])

  const handleSend = (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg) return
    onSend(msg)
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const requestRunCommand = (command: string) => {
    if (!onRunCommand) return
    const normalized = normalizeRunnableCommand(command)
    if (!normalized || hasUnresolvedPlaceholder(normalized)) return
    setPendingExecution({ kind: 'command', content: normalized })
  }

  // 一键提交 LLM 生成的脚本：先确认，再写入临时文件并用 sbatch 提交
  const handleSubmitScript = (script: string) => {
    if (!onRunCommand) return
    setPendingExecution({ kind: 'script', content: script.trim() })
  }

  const confirmExecution = () => {
    if (!pendingExecution || !onRunCommand) return

    if (pendingExecution.kind === 'script') {
      const remote = '/tmp/hpc_copilot_job.sh'
      const command = [
        `cat > ${remote} << 'HPC_COPILOT_SCRIPT_EOF'`,
        pendingExecution.content,
        'HPC_COPILOT_SCRIPT_EOF',
        `sbatch ${remote}`,
      ].join('\n')
      onRunCommand(command)
    } else {
      onRunCommand(pendingExecution.content)
    }

    setPendingExecution(null)
  }

  // 空状态：只有初始系统消息时显示引导卡片
  const isEmpty = messages.length <= 1

  // 把工作流卡片放到触发它的用户消息之后。后续对话仍按时间顺序排在卡片下方。
  const conversationItems: Array<
    | { kind: 'message'; message: ChatMessage; index: number }
    | { kind: 'template'; state: TemplateState }
  > = []
  let templateInserted = false
  messages.forEach((message, index) => {
    conversationItems.push({ kind: 'message', message, index })
    if (templateState?.anchorMessageId && message.id === templateState.anchorMessageId) {
      conversationItems.push({ kind: 'template', state: templateState })
      templateInserted = true
    }
  })
  // 兼容刷新前发起、没有锚点的旧工作流事件。
  if (templateState && !templateInserted) {
    conversationItems.push({ kind: 'template', state: templateState })
  }

  return (
    <div
      className="flex flex-col h-full bg-[var(--bg-panel)] relative"
      style={{
        backgroundImage:
          'radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,81,168,.06), transparent 60%), radial-gradient(ellipse 60% 50% at 80% 90%, rgba(0,51,102,.04), transparent 60%)',
      }}
    >
      {/* ─── AI 头部：头像 + 状态 + 作业队列小组件 ─── */}
      <div className="flex items-center gap-3 px-4 h-14 flex-shrink-0 border-b border-[var(--border-ai)] relative">
        {/* 顶部渐变发光线 */}
        <span
          className="absolute top-0 left-0 right-0 h-px"
          style={{ background: 'linear-gradient(90deg, transparent, rgba(0,81,168,.3), transparent)' }}
        />
        {/* AI 头像 + 脉冲光晕 */}
        <div className="relative flex items-center justify-center w-[42px] h-[42px]">
          <span
            className="ai-halo absolute inset-0 rounded-[12px]"
            style={{ background: 'radial-gradient(circle, rgba(0,81,168,.35), transparent 70%)' }}
          />
          <div
            className="relative w-[30px] h-[30px] rounded-[9px] flex items-center justify-center text-white text-sm font-semibold"
            style={{ background: 'var(--grad-blue)' }}
          >
            ✦
          </div>
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-[var(--text-primary)]">HPC Copilot</div>
          <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-1">
            <span className="w-1 h-1 rounded-full bg-[var(--accent-green)] inline-block" />
            在线 · 规则定事实，LLM 做表达
          </div>
        </div>
        {/* 作业队列小组件 */}
        <div className="ml-auto">
          <JobsWidget jobs={jobs} />
        </div>
      </div>

      {/* ─── 消息列表 ─── */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {isEmpty && (
          <div className="mt-2 space-y-3">
            {/* 作业模板卡片：一键触发 Agent 自主工作流 */}
            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide px-1 mb-1.5">
              一键体验 · Agent 自主执行
            </div>
            <div className="grid grid-cols-1 gap-2">
              {JOB_TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  onClick={() => onRunTemplate?.(t.id, t.request)}
                  disabled={!onRunTemplate}
                  className="text-left px-3.5 py-3 rounded-[var(--radius-lg)] bg-[var(--bg-elevated)] border border-[var(--border-ai)] hover:border-[var(--accent-blue-end)] hover:shadow-[0_0_12px_rgba(0,81,168,.08)] transition-all group disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="flex items-start gap-2.5">
                    <span className="text-lg flex-shrink-0 mt-0.5">{t.icon}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-[var(--text-primary)] group-hover:text-white transition-colors">
                          {t.title}
                        </span>
                        <span className="text-[10px] px-1.5 py-px rounded-full bg-[rgba(0,81,168,.15)] text-[var(--info)] border border-[rgba(0,81,168,.25)]">
                          {t.tag}
                        </span>
                      </div>
                      <div className="text-[11px] text-[var(--text-muted)] mt-0.5 leading-relaxed">
                        {t.desc}
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
            {/* 快捷提问 */}
            <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide px-1 pt-1">
              或者直接问我
            </div>
            <div className="grid grid-cols-1 gap-1.5">
              {DEFAULT_PROMPTS.map((p) => (
                <button
                  key={p}
                  onClick={() => handleSend(p)}
                  className="text-left text-xs px-3 py-2 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border-ai)] text-[var(--text-secondary)] hover:border-[var(--accent-blue-end)] hover:text-[var(--text-primary)] transition-colors"
                >
                  <span className="mr-2 text-[var(--info)]">›</span>
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        {conversationItems.map((item) => {
          if (item.kind === 'template') {
            return <TemplateProgressCard key={`template-${item.state.startedAt}`} state={item.state} />
          }
          const { message: msg, index: i } = item
          // 告警卡片（诊断卡片）：规则结论 + LLM 通俗解释 + 建议命令
          if (msg.role === 'alert' && msg.alert) {
            const alert = msg.alert
            return (
              <div
                key={i}
                className="rounded-[var(--radius-lg)] overflow-hidden border border-[var(--border-ai)]"
                style={{ background: 'linear-gradient(180deg, #111a27, #0d1522)' }}
              >
                {/* 顶部重音条 */}
                <div className="h-[3px]" style={{ background: severityBar(alert.error_type) }} />
                <div className="p-3.5 text-sm">
                  {/* 卡片头：图标 + 标题 + 标签 */}
                  <div className="flex items-center gap-2.5 mb-2">
                    <div
                      className="w-7 h-7 rounded-[7px] flex items-center justify-center text-sm"
                      style={{ background: 'rgba(248,113,113,.15)' }}
                    >
                      {severityIcon(alert.error_type)}
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-semibold text-[var(--text-primary)]">
                        检测到 {alert.error_type}
                      </div>
                      <div className="text-[11px] text-[var(--text-muted)]">Agent 自动诊断 · 规则引擎</div>
                    </div>
                    <span className="text-[11px] px-2 py-0.5 rounded-[var(--radius-xs)] bg-[rgba(248,113,113,.12)] text-[var(--error)] border border-[rgba(248,113,113,.25)]">
                      需处理
                    </span>
                  </div>

                  {/* 规则引擎结论 */}
                  <div className="text-xs text-[var(--text-secondary)] leading-relaxed">{alert.root_cause}</div>

                  {/* LLM 通俗解释：流式填充进卡片 */}
                  {msg.explanation && (
                    <div className="mt-2.5 pt-2.5 border-t border-[var(--border-ai)] text-sm text-[var(--text-primary)]">
                      <MarkdownContent
                        content={msg.explanation}
                        onRunCommand={requestRunCommand}
                        onRunScript={handleSubmitScript}
                      />
                    </div>
                  )}

                  {alert.evidence.length > 0 && (
                    <div className="mt-2.5">
                      <span className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide">证据</span>
                      {alert.evidence.map((e, j) => (
                        <code key={j} className="block text-xs bg-[var(--bg-deep)] border border-[var(--border)] rounded-[var(--radius-sm)] px-2.5 py-1.5 mt-1 text-[#7ab8f5] font-mono-term">
                          {e}
                        </code>
                      ))}
                    </div>
                  )}

                  {alert.suggested_commands.length > 0 && (
                    <div className="mt-2.5">
                      <span className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide">建议操作</span>
                      {alert.suggested_commands.map((cmd, j) => {
                        const hasPlaceholder = hasUnresolvedPlaceholder(cmd)
                        return (
                          <div key={j} className="flex items-center gap-2 mt-1">
                            <code className="flex-1 text-xs bg-[var(--bg-deep)] border border-[var(--border)] rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[#7ab8f5] font-mono-term">
                              {cmd}
                            </code>
                            {onRunCommand && (
                              <button
                                onClick={() => requestRunCommand(cmd)}
                                disabled={hasPlaceholder}
                                className="flex-shrink-0 text-xs px-2.5 py-1.5 rounded-[var(--radius-sm)] transition-all disabled:opacity-40 disabled:cursor-not-allowed text-white hover:-translate-y-px"
                                style={{ background: hasPlaceholder ? 'var(--bg-elevated)' : 'var(--grad-blue)', border: `1px solid ${hasPlaceholder ? 'var(--border)' : 'var(--accent-blue-end)'}` }}
                                title={hasPlaceholder ? '命令含未替换的占位符，无法直接执行' : '在终端中执行此命令'}
                              >
                                ▶ 运行
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )
          }

          // 系统欢迎消息：居中弱化展示
          if (msg.role === 'system') {
            return (
              <div key={i} className="flex justify-center">
                <div className="max-w-[90%] text-center text-xs text-[var(--text-muted)] leading-relaxed px-4 py-2">
                  {msg.content}
                </div>
              </div>
            )
          }

          // 用户 / AI 气泡
          const isUser = msg.role === 'user'
          return (
            <div key={i} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] min-w-0 overflow-hidden rounded-[var(--radius-lg)] px-3.5 py-2.5 text-sm break-words ${isUser ? 'whitespace-pre-wrap' : ''}`}
                style={
                  isUser
                    ? {
                        background: 'linear-gradient(135deg, #003366, #004a94)',
                        color: '#f0f6ff',
                        borderRadius: '10px 10px 2px 10px',
                        boxShadow: '0 2px 8px rgba(0,51,102,.2)',
                      }
                    : {
                        background: 'linear-gradient(180deg, #111a27, #0f1a2e)',
                        border: '1px solid var(--border-ai)',
                        color: 'var(--text-primary)',
                        borderRadius: '10px 10px 10px 2px',
                        boxShadow: '0 1px 4px rgba(0,0,0,.2), inset 0 1px 0 rgba(255,255,255,.02)',
                      }
                }
              >
                {isUser ? (
                  msg.content
                ) : msg.timeline && msg.timeline.length > 0 ? (
                  // 新路径：按时间顺序交叉渲染 agent 步骤和流式文字
                  <div className="space-y-1">
                    {msg.timeline.map((item, idx) => {
                      const isLast = idx === msg.timeline!.length - 1
                      if (item.kind === 'step') {
                        return <TimelineStepRow key={idx} step={item.step} isLast={isLast} />
                      }
                      return (
                        <MarkdownContent
                          key={idx}
                          content={item.text}
                          onRunCommand={requestRunCommand}
                          onRunScript={handleSubmitScript}
                        />
                      )
                    })}
                  </div>
                ) : (
                  // 旧路径兼容：无 timeline 的历史消息，退化到 agentSteps + content
                  <>
                    {msg.agentSteps && msg.agentSteps.length > 0 && (
                      <AgentStepsCard steps={msg.agentSteps} live={false} />
                    )}
                    <MarkdownContent
                      content={msg.content}
                      onRunCommand={requestRunCommand}
                      onRunScript={handleSubmitScript}
                    />
                  </>
                )}
              </div>
            </div>
          )
        })}

        {/* Agent 实时步骤已合并进消息气泡的 timeline，不再单独渲染卡片 */}

        {/* 正在思考指示器：只在 typing 事件到达但还没有任何 timeline 内容时短暂显示 */}
        {isTyping && (
          <div className="flex justify-start">
            <div
              className="rounded-[var(--radius-lg)] px-4 py-3 flex items-center gap-1.5"
              style={{
                background: 'linear-gradient(180deg, #111a27, #0f1a2e)',
                border: '1px solid var(--border-ai)',
                borderRadius: '10px 10px 10px 2px',
              }}
            >
              <span className="text-[11px] text-[var(--text-muted)] mr-1.5">正在思考</span>
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* ─── 输入区 ─── */}
      <div
        className="flex-shrink-0 px-4 pt-2 pb-3.5 border-t border-[var(--border)]"
        style={{ background: 'linear-gradient(0deg, #0a0e17, rgba(13,17,28,0.6))' }}
      >
        {/* 快捷指令 chips：根据最近对话内容动态引导下一步（空状态时已由上方大按钮引导） */}
        {!isEmpty && (
          <div className="flex gap-2 mb-2.5 flex-wrap">
            {suggestNext(messages).map((p) => (
              <button
                key={p}
                onClick={() => handleSend(p)}
                className="text-xs px-2.5 py-1 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-ai)] text-[var(--text-secondary)] hover:border-[var(--accent-blue-end)] hover:text-[var(--text-primary)] transition-colors"
              >
                {p}
              </button>
            ))}
          </div>
        )}
        <div className="flex gap-2 items-end">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="问我任何关于 HPC 作业的问题..."
            rows={2}
            className="flex-1 bg-[var(--bg-elevated)] border border-[var(--border-ai)] rounded-[var(--radius-lg)] px-3.5 py-2.5 text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] resize-none outline-none transition-shadow focus:border-[var(--accent-blue-end)] focus:shadow-[0_0_0_2px_rgba(0,81,168,.1)]"
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim()}
            className="w-[34px] h-[34px] flex-shrink-0 rounded-[var(--radius-md)] text-white text-base flex items-center justify-center transition-all hover:scale-105 disabled:opacity-30 disabled:hover:scale-100"
            style={{ background: 'var(--grad-blue)', border: '1px solid var(--accent-blue-end)', boxShadow: '0 2px 8px rgba(0,81,168,.25)' }}
            title="发送 (Enter)"
          >
            ↑
          </button>
        </div>
      </div>

      {pendingExecution && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/65 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="execution-confirm-title"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPendingExecution(null)
          }}
        >
          <div className="w-full max-w-lg overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-ai)] bg-[var(--bg-panel)] shadow-2xl">
            <div className="border-b border-[var(--border)] px-4 py-3">
              <div id="execution-confirm-title" className="text-sm font-semibold text-[var(--text-primary)]">
                {pendingExecution.kind === 'script' ? '确认提交作业' : '确认运行命令'}
              </div>
              <div className="mt-1 text-[11px] leading-relaxed text-[var(--text-muted)]">
                将在当前连接的算力平台上执行，请确认下面的内容。
              </div>
            </div>
            <pre className="mx-4 mt-4 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-deep)] p-3 text-xs text-[#7ab8f5] font-mono-term">
              {pendingExecution.content}
            </pre>
            <div className="flex justify-end gap-2 px-4 py-4">
              <button
                type="button"
                onClick={() => setPendingExecution(null)}
                className="rounded-[var(--radius-sm)] border border-[var(--border-ai)] bg-[var(--bg-elevated)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent-blue-end)] hover:text-[var(--text-primary)]"
              >
                取消
              </button>
              <button
                type="button"
                onClick={confirmExecution}
                className="rounded-[var(--radius-sm)] px-3 py-1.5 text-xs text-white transition-all hover:-translate-y-px"
                style={{ background: 'var(--grad-blue)', border: '1px solid var(--accent-blue-end)' }}
              >
                {pendingExecution.kind === 'script' ? '确认提交' : '确认运行'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
