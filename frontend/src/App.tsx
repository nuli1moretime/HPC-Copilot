import { useState, useRef, useEffect, useCallback } from 'react'
import Terminal from './components/Terminal'
import ChatPanel from './components/ChatPanel'
import ConnectionSettings from './components/ConnectionSettings'
import { useWebSocket } from './hooks/useWebSocket'

export interface AgentAlert {
  error_type: string
  confidence: number
  evidence: string[]
  root_cause: string
  suggested_commands: string[]
}

export default function App() {
  const [connected, setConnected] = useState(false)
  const [showSettings, setShowSettings] = useState(true)

  const {
    sendCommand,
    sendChatMessage,
    terminalMessages,
    chatMessages,
    isTyping,
    connectAll,
  } = useWebSocket()

  // 左右面板可拖动调整宽度
  const [leftWidth, setLeftWidth] = useState(50)
  const isDraggingRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const startDrag = useCallback(() => {
    isDraggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const pct = ((e.clientX - rect.left) / rect.width) * 100
      setLeftWidth(Math.min(80, Math.max(20, pct)))
    }
    const handleUp = () => {
      if (!isDraggingRef.current) return
      isDraggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
  }, [])

  const handleConnect = async (config: Record<string, string>) => {
    try {
      const resp = await fetch('/api/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      if (resp.ok) {
        setConnected(true)
        setShowSettings(false)
        // 立即建立 WebSocket 连接
        connectAll()
      }
    } catch {
      alert('连接失败，请确认后端已启动 (uvicorn backend.main:app)')
    }
  }

  if (showSettings) {
    return <ConnectionSettings onConnect={handleConnect} />
  }

  return (
    <div ref={containerRef} className="flex h-screen">
      {/* 左：终端 */}
      <div
        style={{ width: `${leftWidth}%` }}
        className="flex flex-col min-w-0"
      >
        <div className="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
          <span className="text-sm font-medium text-gray-300">
            🖥️ 终端 {connected ? '🟢' : '🔴'}
          </span>
          <button
            onClick={() => setShowSettings(true)}
            className="text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300"
          >
            ⚙️ 设置
          </button>
        </div>
        <div className="flex-1">
          <Terminal
            messages={terminalMessages}
            onCommand={sendCommand}
          />
        </div>
      </div>

      {/* 可拖动分隔条 */}
      <div
        onMouseDown={startDrag}
        className="w-1.5 flex-shrink-0 cursor-col-resize bg-gray-700 hover:bg-blue-500 active:bg-blue-500 transition-colors"
        title="拖动调整宽度"
      />

      {/* 右：对话 */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-2 bg-gray-800 border-b border-gray-700">
          <span className="text-sm font-medium text-gray-300">
            🤖 HPC Copilot 智能体
          </span>
        </div>
        <ChatPanel
          messages={chatMessages}
          onSend={sendChatMessage}
          onRunCommand={(cmd) => sendCommand(cmd, true)}
          isTyping={isTyping}
        />
      </div>
    </div>
  )
}
