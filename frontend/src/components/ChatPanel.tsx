import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import type { AgentAlert } from '../App'

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
                  <code className="block bg-black/40 rounded-md px-3 py-2 text-xs text-green-300 overflow-x-auto whitespace-pre">
                    {text}
                  </code>
                  {isSbatch && (
                    <span className="flex gap-2 mt-1">
                      <button
                        onClick={() => navigator.clipboard.writeText(text).catch(() => {})}
                        className="text-xs px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
                      >
                        📋 复制脚本
                      </button>
                      {onRunScript && (
                        <button
                          onClick={() => onRunScript(text)}
                          className="text-xs px-2 py-0.5 rounded bg-blue-700 hover:bg-blue-600 text-blue-100 transition-colors"
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
              <code className="bg-black/30 rounded px-1.5 py-0.5 text-xs text-cyan-300">
                {children}
              </code>
            )
          },
          pre: ({ children }) => <pre className="mb-2">{children}</pre>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 underline hover:text-blue-300">
              {children}
            </a>
          ),
          strong: ({ children }) => <strong className="font-bold text-white">{children}</strong>,
          hr: () => <hr className="border-gray-600 my-3" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-gray-500 pl-3 my-2 text-gray-300">{children}</blockquote>
          ),
          table: ({ children }) => (
            <table className="border-collapse text-xs my-2 w-full">{children}</table>
          ),
          th: ({ children }) => (
            <th className="border border-gray-600 px-2 py-1 bg-gray-800 text-left">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border border-gray-600 px-2 py-1">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default function ChatPanel({ messages, onSend, onRunCommand, isTyping }: Props) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  const handleSend = () => {
    if (!input.trim()) return
    onSend(input.trim())
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

  return (
    <div className="flex flex-col h-full bg-gray-900">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 系统消息、对话消息和告警卡片（按时间顺序统一渲染） */}
        {messages.map((msg, i) => {
          // 告警卡片：规则结论 + LLM 通俗解释 + 建议命令，合并为一张卡片
          if (msg.role === 'alert' && msg.alert) {
            const alert = msg.alert
            return (
              <div
                key={i}
                className="bg-yellow-900/30 border border-yellow-700 rounded-lg p-3 text-sm"
              >
                <div className="font-medium text-yellow-300 mb-1">
                  🔍 检测到: {alert.error_type}
                </div>
                {/* 规则引擎结论（即时显示，不依赖大模型） */}
                <div className="text-yellow-100/80">{alert.root_cause}</div>

                {/* LLM 通俗解释：流式填充进卡片，和告警合并为一段，避免读两块内容 */}
                {msg.explanation && (
                  <div className="mt-2 pt-2 border-t border-yellow-700/50 text-gray-100">
                    <MarkdownContent content={msg.explanation} onRunScript={handleSubmitScript} />
                  </div>
                )}

                {alert.evidence.length > 0 && (
                  <div className="mt-2">
                    <span className="text-xs text-gray-400">证据：</span>
                    {alert.evidence.map((e, j) => (
                      <code key={j} className="block text-xs bg-black/30 rounded px-2 py-1 mt-1 text-green-300">
                        {e}
                      </code>
                    ))}
                  </div>
                )}
                {alert.suggested_commands.length > 0 && (
                  <div className="mt-2">
                    <span className="text-xs text-gray-400">建议命令：</span>
                    {alert.suggested_commands.map((cmd, j) => {
                      // 含未替换占位符（如 <JOB_ID>、<你的脚本>）的命令无法直接执行，禁用按钮
                      const hasPlaceholder = /<[^>]+>/.test(cmd)
                      return (
                        <div key={j} className="flex items-center gap-2 mt-1">
                          <code className="flex-1 text-xs bg-black/30 rounded px-2 py-1 text-cyan-300">
                            {cmd}
                          </code>
                          {onRunCommand && (
                            <button
                              onClick={() => onRunCommand(cmd)}
                              disabled={hasPlaceholder}
                              className="flex-shrink-0 text-xs px-2 py-1 rounded bg-green-800 hover:bg-green-700 disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed text-green-200 transition-colors"
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
            )
          }

          return (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-4 py-2 text-sm ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white whitespace-pre-wrap'
                    : msg.role === 'agent'
                    ? 'bg-red-900/50 border border-red-700 text-red-100'
                    : 'bg-gray-700 text-gray-100'
                }`}
              >
                {msg.role === 'agent' && (
                  <span className="block text-xs text-red-400 mb-1">⚠️ Agent 自动诊断</span>
                )}
                {msg.role === 'user' ? (
                  msg.content
                ) : (
                  <MarkdownContent content={msg.content} onRunScript={handleSubmitScript} />
                )}
              </div>
            </div>
          )
        })}

        {/* 正在思考指示器 */}
        {isTyping && (
          <div className="flex justify-start">
            <div className="bg-gray-700 rounded-lg px-4 py-3 flex items-center gap-1">
              <span className="text-xs text-gray-400 mr-2">正在思考</span>
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* 输入框 */}
      <div className="border-t border-gray-700 p-3">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="问我任何关于 HPC 作业的问题..."
            rows={2}
            className="flex-1 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg text-sm font-medium transition-colors"
          >
            发送
          </button>
        </div>
      </div>

      <style>{`
        .typing-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background-color: #9ca3af;
          animation: typing-bounce 1.4s infinite ease-in-out both;
        }
        .typing-dot:nth-child(2) { animation-delay: 0.16s; }
        .typing-dot:nth-child(3) { animation-delay: 0.32s; }
        .typing-dot:nth-child(4) { animation-delay: 0.48s; }
        @keyframes typing-bounce {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
