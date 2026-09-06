import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'

export interface TerminalMessage {
  type: 'output' | 'prompt' | 'agent_alert' | 'terminal_mode' | 'jobs_update'
  data: string | Record<string, unknown>
}

interface Props {
  messages: TerminalMessage[]
  onCommand: (cmd: string) => void
  onPaste?: (text: string) => void
  connected?: boolean
}

// 设计 token 对应的 ANSI 24-bit 颜色（终端诊断块配色与全局体系统一）
const ANSI = {
  green: '\x1b[38;2;52;211;153m',   // #34d399 终端绿
  red: '\x1b[38;2;248;113;113m',    // #f87171 错误
  yellow: '\x1b[38;2;251;191;36m',  // #fbbf24 警告
  blue: '\x1b[38;2;96;165;250m',    // #60a5fa 信息
  dim: '\x1b[38;2;71;85;105m',      // #475569 弱化
  reset: '\x1b[0m',
}

export default function Terminal({ messages, onCommand, onPaste, connected = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerm | null>(null)
  const inputBufferRef = useRef('')
  const processedRef = useRef(0)
  // "webshell" = 远程真实终端（远程回显），"command" = 命令模式（本地回显）
  const modeRef = useRef<'command' | 'webshell'>('command')

  // 初始化 xterm
  useEffect(() => {
    if (!containerRef.current) return

    const term = new XTerm({
      cursorBlink: true,
      fontSize: 14,
      lineHeight: 1.5,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      theme: {
        background: '#080b13',
        foreground: '#cbd5e1',
        cursor: '#34d399',
        cursorAccent: '#080b13',
        selectionBackground: '#1e3a5a',
      },
    })

    const fitAddon = new FitAddon()
    const webLinksAddon = new WebLinksAddon()
    term.loadAddon(fitAddon)
    term.loadAddon(webLinksAddon)
    term.open(containerRef.current)
    fitAddon.fit()

    // ─── 欢迎屏（Claude Code 风格：细边框 + 大号 HPC 字母 + Welcome back + 校训） ───
    const cols = term.cols
    const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, '')
    // 显示宽度：先剥离 ANSI 转义，中日韩 / 全角字符按 2 列计
    const dispW = (s: string) => {
      let w = 0
      for (const ch of stripAnsi(s)) {
        const cp = ch.codePointAt(0) || 0
        if ((cp >= 0x4e00 && cp <= 0x9fff) || (cp >= 0xff00 && cp <= 0xffef) || (cp >= 0x3000 && cp <= 0x303f)) w += 2
        else w += 1
      }
      return w
    }
    const sp = (n: number) => ' '.repeat(Math.max(0, n))

    // 大号 HPC 字母（ANSI Shadow 风格，每行 24 列）
    const HPC = [
      '██╗  ██╗██████╗  ██████╗',
      '██║  ██║██╔══██╗██╔════╝',
      '███████║██████╔╝██║     ',
      '██╔══██║██╔═══╝ ██║     ',
      '██║  ██║██║     ╚██████╗',
      '╚═╝  ╚═╝╚═╝      ╚═════╝',
    ]
    // 横向渐变：绿 #34d399 → 蓝 #60a5fa（逐字符，空格跳过）
    const hgrad = (s: string) =>
      Array.from(s).map((ch, i, arr) => {
        if (ch === ' ') return ch
        const t = arr.length > 1 ? i / (arr.length - 1) : 0
        const r = Math.round(52 + (96 - 52) * t)
        const g = Math.round(211 + (165 - 211) * t)
        const b = Math.round(153 + (250 - 153) * t)
        return `\x1b[38;2;${r};${g};${b}m${ch}`
      }).join('') + ANSI.reset

    // 边框盒子：宽度自适应（最宽 58 列），整体居中
    const boxW = Math.min(58, cols - 2)
    const innerW = boxW - 2
    const boxOffset = sp(Math.floor((cols - boxW) / 2))
    // 盒子内部居中
    const centerIn = (s: string) => sp(Math.floor((innerW - dispW(s)) / 2)) + s
    // 盒子内容行：│ + 内容 + 补齐空格 + │
    const boxLine = (content: string) =>
      `${ANSI.dim}│${ANSI.reset}${content}${sp(innerW - dispW(content))}${ANSI.dim}│${ANSI.reset}`

    term.writeln(`${boxOffset}${ANSI.dim}╭${'─'.repeat(innerW)}╮${ANSI.reset}`)
    term.writeln(boxOffset + boxLine(''))
    term.writeln(boxOffset + boxLine(`\x1b[1m${centerIn('Welcome back!')}${ANSI.reset}`))
    term.writeln(boxOffset + boxLine(''))
    HPC.forEach((row) => term.writeln(boxOffset + boxLine(centerIn(hgrad(row)))))
    term.writeln(boxOffset + boxLine(''))
    term.writeln(boxOffset + boxLine(`${ANSI.dim}${centerIn('红专并进 理实交融')}${ANSI.reset}`))
    term.writeln(boxOffset + boxLine(''))
    term.writeln(`${boxOffset}${ANSI.dim}╰${'─'.repeat(innerW)}╯${ANSI.reset}`)
    term.write('\r\n$ ')

    xtermRef.current = term

    // 处理用户输入
    term.onData((data) => {
      // 多行粘贴检测：数据长度 >1 且包含换行符（区别于单次回车 data='\r'）
      if (data.length > 1 && (data.includes('\r') || data.includes('\n'))) {
        const text = data.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
        const lines = text.split('\n').filter((l) => l.trim())
        if (lines.length > 1) {
          for (const line of lines) {
            term.write(line + '\r\n')
          }
          if (onPaste) {
            onPaste(lines.join('\n'))
          } else {
            for (const line of lines) onCommand(line)
          }
          return
        }
      }

      const code = data.charCodeAt(0)
      const isWebshell = modeRef.current === 'webshell'

      if (data === '\r') {
        // 回车：发送命令
        const cmd = inputBufferRef.current
        term.write('\r\n')
        if (cmd.trim()) {
          onCommand(cmd)
        } else if (!isWebshell) {
          // 命令模式下空行显示提示符；webshell 模式远程 shell 会处理
          term.write('$ ')
        }
        inputBufferRef.current = ''
      } else if (code === 127) {
        // 退格
        if (inputBufferRef.current.length > 0) {
          inputBufferRef.current = inputBufferRef.current.slice(0, -1)
          term.write('\b \b')
        }
      } else if (code === 3) {
        // Ctrl+C
        if (isWebshell) {
          onCommand('\x03')
        } else {
          term.write('^C\r\n$ ')
        }
        inputBufferRef.current = ''
      } else if (code >= 32) {
        // 可打印字符 - 两种模式都本地回显
        inputBufferRef.current += data
        term.write(data)
      }
    })

    // ─── 剪贴板支持 ───────────────────────────────────────
    const pasteFromClipboard = async () => {
      try {
        const text = await navigator.clipboard.readText()
        if (!text) return

        const lines = text.split('\n').map((l) => l.replace(/\r$/, ''))
        const isMultiLine = lines.filter((l) => l.trim()).length > 1

        if (isMultiLine) {
          // 多行粘贴：显示所有行，然后作为一整块发送到后端
          for (const line of lines) {
            term.write(line + '\r\n')
          }
          if (onPaste) {
            onPaste(text)
          } else {
            // 降级：逐行发送（兼容无 onPaste 的旧调用方）
            for (const line of lines) {
              if (line.trim()) onCommand(line)
            }
          }
        } else {
          // 单行粘贴：填入输入缓冲区，等用户按回车
          const line = lines[0] || ''
          inputBufferRef.current += line
          term.write(line)
        }
      } catch {
        // 剪贴板权限被拒绝时忽略
      }
    }

    // 使用 xterm 官方 API 拦截键盘事件
    term.attachCustomKeyEventHandler((event: KeyboardEvent) => {
      if (event.type !== 'keydown') return true

      // Ctrl+V / Ctrl+Shift+V 粘贴
      if (event.ctrlKey && event.key.toLowerCase() === 'v') {
        event.preventDefault()
        pasteFromClipboard()
        return false
      }

      // Ctrl+C：有选中文本时复制，否则放行（发送中断信号）
      if (event.ctrlKey && event.key.toLowerCase() === 'c') {
        const selection = term.getSelection()
        if (selection) {
          event.preventDefault()
          navigator.clipboard.writeText(selection).catch(() => {})
          return false
        }
        return true
      }

      return true
    })

    // 右键粘贴
    const container = containerRef.current
    if (container) {
      container.addEventListener('contextmenu', (e: MouseEvent) => {
        e.preventDefault()
        pasteFromClipboard()
      })
    }

    // 容器尺寸变化时自适应：窗口缩放、拖动分隔条、展开/收起侧边栏
    // 都会改变终端可用宽度，ResizeObserver 比 window.resize 覆盖更全
    const resizeObserver = new ResizeObserver(() => fitAddon.fit())
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      term.dispose()
    }
  }, [onCommand, onPaste])

  // 处理后端推送的消息
  useEffect(() => {
    const term = xtermRef.current
    if (!term) return

    // 只处理新消息
    const newMessages = messages.slice(processedRef.current)
    processedRef.current = messages.length

    for (const msg of newMessages) {
      if (msg.type === 'terminal_mode') {
        modeRef.current = msg.data as 'command' | 'webshell'
      } else if (msg.type === 'output') {
        const text = (msg.data as string).replace(/\n/g, '\r\n')
        term.write(text)
      } else if (msg.type === 'prompt') {
        term.write('\r\n$ ')
      } else if (msg.type === 'agent_alert') {
        // 内嵌诊断块：ANSI 文本混排在终端流中，配色对齐全局 token
        const alert = msg.data as Record<string, unknown>
        term.write('\r\n')
        term.write(`${ANSI.red}┌─── ⚠ Agent 检测到错误 ───────────────┐${ANSI.reset}\r\n`)
        term.write(`${ANSI.red}│ 类型: ${alert.error_type}${ANSI.reset}\r\n`)
        term.write(`${ANSI.yellow}│ 原因: ${alert.root_cause}${ANSI.reset}\r\n`)
        term.write(`${ANSI.red}└──────────────────────────────────────┘${ANSI.reset}\r\n`)
        term.write(`${ANSI.blue}→ 详细解释已推送到右侧对话面板${ANSI.reset}\r\n`)
        if (modeRef.current === 'command') {
          term.write('$ ')
        }
      }
      // jobs_update 由 useWebSocket 处理（驱动右侧作业小组件），终端不渲染
    }
  }, [messages])

  return (
    <div className="flex flex-col h-full bg-[var(--bg-panel)]">
      {/* ─── 终端内容区（内边距避免文字贴边；xterm 挂载元素本身不加 padding，
             否则 FitAddon 会按含 padding 的尺寸计算列数导致换行错位） ─── */}
      <div className="flex-1 min-h-0 bg-[var(--bg-deep)] px-3.5 py-2.5">
        <div ref={containerRef} className="h-full w-full" />
      </div>

      {/* ─── 底部状态条（VS Code 风格） ─── */}
      <div className="flex items-center gap-4 h-7 flex-shrink-0 px-3 bg-[var(--bg-deep)] border-t border-[var(--border)] font-mono-term text-[11px] text-[var(--text-muted)]">
        <span>
          分区 <span className="text-[var(--text-secondary)]">P107-A100</span>
        </span>
        <span>
          QoS <span className="text-[var(--text-secondary)]">qos_p107-a100</span>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-[var(--accent-green)] status-dot-live text-[var(--accent-green)]' : 'bg-[var(--text-dim)]'}`}
          />
          <span className={connected ? 'text-[var(--accent-green)]' : ''}>
            {connected ? 'Agent 监控中' : '未连接'}
          </span>
        </span>
      </div>
    </div>
  )
}
