import { useState, useRef, useEffect, useCallback } from 'react'
import Terminal from './components/Terminal'
import ChatPanel from './components/ChatPanel'
import ConnectionSettings, { SettingsModal } from './components/ConnectionSettings'
import ConsoleSidebar from './components/ConsoleSidebar'
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
  // 顶栏独立入口：当前打开的设置弹窗（集群 / 大模型），null 表示未打开
  const [settingsModal, setSettingsModal] = useState<'cluster' | 'llm' | null>(null)
  // 保存连接配置，供顶部状态栏展示集群/节点信息
  const [config, setConfig] = useState<Record<string, string>>({})

  const {
    sendCommand,
    sendPaste,
    sendChatMessage,
    sendRunTemplate,
    terminalMessages,
    chatMessages,
    isTyping,
    jobs,
    templateState,
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

  const handleClusterConnect = async (cfg: Record<string, string>): Promise<boolean> => {
    try {
      const resp = await fetch('/api/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      })
      if (resp.ok) {
        setConfig((c) => ({ ...c, ...cfg }))
        setConnected(true)
        return true
      }
      return false
    } catch {
      return false
    }
  }

  const handleLlmSave = async (cfg: Record<string, string>): Promise<boolean> => {
    try {
      const resp = await fetch('/api/llm/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      })
      if (resp.ok) {
        setConfig((c) => ({ ...c, ...cfg }))
        return true
      }
      return false
    } catch {
      return false
    }
  }

  const handleWizardDone = () => {
    setShowSettings(false)
    connectAll()
  }

  if (showSettings) {
    return (
      <ConnectionSettings
        onClusterConnect={handleClusterConnect}
        onLlmSave={handleLlmSave}
        onDone={handleWizardDone}
      />
    )
  }

  const cluster = config.webshell_cluster || config.cluster || 'training'
  const node = config.webshell_login_node || 'tradmin-02'

  return (
    <div className="flex flex-col h-screen bg-[var(--bg-deep)]">
      {/* ─── 顶部全局状态栏 ─── */}
      <header className="flex items-center gap-4 px-4 h-12 flex-shrink-0 bg-[var(--bg-panel)] border-b border-[var(--border)]">
        {/* 产品标识 */}
        <div className="flex items-center gap-2.5">
          <div
            className="w-7 h-7 rounded-[var(--radius-md)] flex items-center justify-center font-mono-term text-sm font-semibold text-white"
            style={{ background: 'var(--grad-blue)' }}
          >
            &gt;_
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-[var(--text-primary)]">HPC Copilot</div>
            <div className="text-[11px] text-[var(--text-dim)] font-mono-term">智能体 · v1.0</div>
          </div>
        </div>

        {/* 连接状态胶囊 */}
        <div className="flex items-center gap-2 ml-2 px-3 h-7 rounded-full bg-[var(--bg-elevated)] border border-[var(--border)]">
          <span
            className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-[var(--accent-green)] status-dot-live text-[var(--accent-green)]' : 'bg-[var(--error)]'}`}
          />
          <span className="text-xs font-mono-term text-[var(--text-secondary)]">
            {cluster} · {node}
          </span>
          <span className="text-xs text-[var(--text-dim)]">
            {connected ? '已连接' : '未连接'}
          </span>
        </div>

        {/* 右侧：Agent 监控状态 + 设置 */}
        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-green)] status-dot-live text-[var(--accent-green)]" />
            <span className="text-xs text-[var(--text-secondary)]">作业监控中</span>
          </div>
          <div className="w-px h-4 bg-[var(--border)]" />
          <button
            onClick={() => setSettingsModal('cluster')}
            className="text-xs px-2.5 h-7 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border)] hover:border-[var(--text-dim)] text-[var(--text-secondary)] transition-colors"
          >
            ⚙ 集群
          </button>
          <button
            onClick={() => setSettingsModal('llm')}
            className="text-xs px-2.5 h-7 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border)] hover:border-[var(--text-dim)] text-[var(--text-secondary)] transition-colors"
          >
            ✦ 大模型
          </button>
        </div>
      </header>

      {/* ─── 主工作区 ─── */}
      <div ref={containerRef} className="flex flex-1 min-h-0">
        {/* 左：控制台（侧边栏 + 终端） */}
        <div style={{ width: `${leftWidth}%` }} className="flex min-w-0">
          <ConsoleSidebar
            onRunCommand={(cmd) => sendCommand(cmd, true)}
            onRunTemplate={(templateId, request) => sendRunTemplate(templateId, request)}
          />
          <div className="flex-1 flex flex-col min-w-0">
            <Terminal
              messages={terminalMessages}
              onCommand={sendCommand}
              onPaste={sendPaste}
              connected={connected}
            />
          </div>
        </div>

        {/* 可拖动分隔条 */}
        <div
          onMouseDown={startDrag}
          className="w-px flex-shrink-0 cursor-col-resize bg-[var(--border)] hover:bg-[var(--accent-blue-end)] active:bg-[var(--accent-blue-end)] transition-colors relative group"
          title="拖动调整宽度"
        >
          <div className="absolute inset-y-0 -left-1 -right-1" />
        </div>

        {/* 右：AI 智能对话 */}
        <div className="flex-1 flex flex-col min-w-0">
          <ChatPanel
            messages={chatMessages}
            onSend={sendChatMessage}
            onRunCommand={(cmd) => sendCommand(cmd, true)}
            onRunTemplate={(templateId, request) => sendRunTemplate(templateId, request)}
            isTyping={isTyping}
            jobs={jobs}
            templateState={templateState}
          />
        </div>
      </div>

      {/* 顶栏独立设置弹窗：集群连接 / 大模型 API 各自独立 */}
      {settingsModal && (
        <SettingsModal
          section={settingsModal}
          onClose={() => setSettingsModal(null)}
          onClusterConnect={handleClusterConnect}
          onLlmSave={handleLlmSave}
        />
      )}
    </div>
  )
}
