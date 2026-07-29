import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import type { AgentAlert } from '../App'
import type { JobInfo } from '../hooks/useWebSocket'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'agent' | 'alert'
  content: string
  timestamp?: number
  alert?: AgentAlert
  id?: string
  /** 告警卡片专用：LLM 通俗解释，流式填充进卡片内部（与卡片合并为一段） */
  explanation?: string
}

interface Props {
  messages: ChatMessage[]
  onSend: (msg: string) => void
  onRunCommand?: (cmd: string) => void
  isTyping?: boolean
  jobs?: JobInfo[]
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
  onRunScript,
}: {
  content: string
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
            const isBlock = className?.includes('language-')
            if (isBlock) {
              const text = extractText(children).replace(/\n$/, '')
              const isSbatch = /(^|\n)#SBATCH/.test(text)
              return (
                <span className="block my-2">
                  <code className="block bg-[var(--bg-deep)] border border-[var(--border)] rounded-[var(--radius-md)] px-3 py-2 text-xs text-[#7ab8f5] overflow-x-auto whitespace-pre font-mono-term">
                    {text}
                  </code>
                  {isSbatch && (
                    <span className="flex gap-2 mt-1.5">
                      <button
                        onClick={() => navigator.clipboard.writeText(text).catch(() => {})}
                        className="text-xs px-2.5 py-1 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-ai)] hover:border-[var(--accent-blue-end)] text-[var(--text-secondary)] transition-colors"
                      >
                        📋 复制脚本
                      </button>
                      {onRunScript && (
                        <button
                          onClick={() => onRunScript(text)}
                          className="text-xs px-2.5 py-1 rounded-[var(--radius-sm)] text-blue-100 transition-all hover:-translate-y-px"
                          style={{ background: 'var(--grad-blue)', border: '1px solid var(--accent-blue-end)' }}
                          title="写入集群并用 sbatch 提交"
                        >
                          🚀 提交作业
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

// ─── 快捷指令 chips ───
// 空状态（引导页）使用的默认指令
const DEFAULT_PROMPTS = [
  '帮我写一个 GPU 训练作业脚本',
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

export default function ChatPanel({ messages, onSend, onRunCommand, isTyping, jobs = [] }: Props) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

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

  // 一键提交 LLM 生成的脚本：写入临时文件后用 sbatch 提交
  const handleSubmitScript = (script: string) => {
    if (!onRunCommand) return
    const remote = '/tmp/hpc_copilot_job.sh'
    const cmd = [
      `cat > ${remote} << 'HPC_COPILOT_SCRIPT_EOF'`,
      script,
      'HPC_COPILOT_SCRIPT_EOF',
      `sbatch ${remote}`,
    ].join('\n')
    onRunCommand(cmd)
  }

  // 空状态：只有初始系统消息时显示引导卡片
  const isEmpty = messages.length <= 1

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
          <div className="grid grid-cols-1 gap-2 mt-2">
            {DEFAULT_PROMPTS.map((p) => (
              <button
                key={p}
                onClick={() => handleSend(p)}
                className="text-left text-xs px-3.5 py-3 rounded-[var(--radius-lg)] bg-[var(--bg-elevated)] border border-[var(--border-ai)] text-[var(--text-secondary)] hover:border-[var(--accent-blue-end)] hover:text-[var(--text-primary)] transition-colors"
              >
                <span className="mr-2 text-[var(--info)]">›</span>
                {p}
              </button>
            ))}
          </div>
        )}

        {messages.map((msg, i) => {
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
                      <MarkdownContent content={msg.explanation} onRunScript={handleSubmitScript} />
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
                        const hasPlaceholder = /<[^>]+>/.test(cmd)
                        return (
                          <div key={j} className="flex items-center gap-2 mt-1">
                            <code className="flex-1 text-xs bg-[var(--bg-deep)] border border-[var(--border)] rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[#7ab8f5] font-mono-term">
                              {cmd}
                            </code>
                            {onRunCommand && (
                              <button
                                onClick={() => onRunCommand(cmd)}
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
                ) : (
                  <MarkdownContent content={msg.content} onRunScript={handleSubmitScript} />
                )}
              </div>
            </div>
          )
        })}

        {/* 正在思考指示器（复用 AI 气泡样式） */}
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
    </div>
  )
}
