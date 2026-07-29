import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'

export interface TerminalMessage {
  type: 'output' | 'prompt' | 'agent_alert' | 'terminal_mode'
  data: string | Record<string, unknown>
}

interface Props {
  messages: TerminalMessage[]
  onCommand: (cmd: string) => void
}

export default function Terminal({ messages, onCommand }: Props) {
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
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      theme: {
        background: '#1a1b26',
        foreground: '#a9b1d6',
        cursor: '#c0caf5',
        selectionBackground: '#33467c',
      },
    })

    const fitAddon = new FitAddon()
    const webLinksAddon = new WebLinksAddon()
    term.loadAddon(fitAddon)
    term.loadAddon(webLinksAddon)
    term.open(containerRef.current)
    fitAddon.fit()

    // 欢迎信息
    term.writeln('\x1b[36m╔══════════════════════════════════════╗\x1b[0m')
    term.writeln('\x1b[36m║     🖥️  HPC Copilot Terminal       ║\x1b[0m')
    term.writeln('\x1b[36m╚══════════════════════════════════════╝\x1b[0m')
    term.writeln('')
    term.writeln('\x1b[33m等待连接到算力中心...\x1b[0m')
    term.write('\r\n$ ')

    xtermRef.current = term

    // 处理用户输入
    term.onData((data) => {
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
    // 粘贴函数：从系统剪贴板读取内容，直接写入输入缓冲和终端显示
    const pasteFromClipboard = async () => {
      try {
        const text = await navigator.clipboard.readText()
        if (text) {
          // 只取第一行（终端命令逐行执行）
          const lines = text.split('\n')
          for (let i = 0; i < lines.length; i++) {
            const line = lines[i].replace(/\r$/, '')
            if (i > 0 && line.trim()) {
              // 多行内容：前面的行直接提交执行
              inputBufferRef.current += line
              term.write(line)
              // 触发回车执行
              const cmd = inputBufferRef.current
              term.write('\r\n')
              if (cmd.trim()) onCommand(cmd)
              inputBufferRef.current = ''
            } else {
              inputBufferRef.current += line
              term.write(line)
            }
          }
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
        return false // 不让 xterm 处理
      }

      // Ctrl+C：有选中文本时复制，否则放行（发送中断信号）
      if (event.ctrlKey && event.key.toLowerCase() === 'c') {
        const selection = term.getSelection()
        if (selection) {
          event.preventDefault()
          navigator.clipboard.writeText(selection).catch(() => {})
          return false
        }
        return true // 无选中：让 xterm 发送 \x03
      }

      return true // 其他按键正常处理
    })

    // 右键粘贴
    const container = containerRef.current
    if (container) {
      container.addEventListener('contextmenu', (e: MouseEvent) => {
        e.preventDefault()
        pasteFromClipboard()
      })
    }

    // 窗口大小变化时自适应
    const handleResize = () => fitAddon.fit()
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      term.dispose()
    }
  }, [onCommand])

  // 处理后端推送的消息
  useEffect(() => {
    const term = xtermRef.current
    if (!term) return

    // 只处理新消息
    const newMessages = messages.slice(processedRef.current)
    processedRef.current = messages.length

    for (const msg of newMessages) {
      if (msg.type === 'terminal_mode') {
        // 后端告知终端模式
        modeRef.current = msg.data as 'command' | 'webshell'
      } else if (msg.type === 'output') {
        const text = (msg.data as string).replace(/\n/g, '\r\n')
        term.write(text)
      } else if (msg.type === 'prompt') {
        term.write('\r\n$ ')
      } else if (msg.type === 'agent_alert') {
        const alert = msg.data as Record<string, unknown>
        term.write('\r\n')
        term.write('\x1b[31m┌─── ⚠️  Agent 检测到错误 ───┐\x1b[0m\r\n')
        term.write(`\x1b[31m│ 类型: ${alert.error_type}\x1b[0m\r\n`)
        term.write(`\x1b[33m│ 原因: ${alert.root_cause}\x1b[0m\r\n`)
        term.write('\x1b[31m└─────────────────────────────┘\x1b[0m\r\n')
        term.write('\x1b[36m→ 详细解释已推送到右侧对话面板\x1b[0m\r\n')
        if (modeRef.current === 'command') {
          term.write('$ ')
        }
      }
    }
  }, [messages])

  return <div ref={containerRef} className="h-full w-full" />
}
