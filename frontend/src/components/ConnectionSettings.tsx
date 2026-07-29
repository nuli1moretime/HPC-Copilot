import { useState, useEffect } from 'react'

interface Props {
  onConnect: (config: Record<string, string>) => void
}

const STORAGE_KEY = 'hpc-copilot-config'

export default function ConnectionSettings({ onConnect }: Props) {
  const [mode, setMode] = useState<'webshell' | 'rest' | 'ssh'>('webshell')

  // REST API 配置
  const [restUrl, setRestUrl] = useState('')
  const [restUser, setRestUser] = useState('')
  const [restToken, setRestToken] = useState('')

  // SSH 配置
  const [sshHost, setSshHost] = useState('')
  const [sshUser, setSshUser] = useState('')
  const [sshPassword, setSshPassword] = useState('')
  const [sshPort, setSshPort] = useState('22')

  // Web Shell (SCOW) 配置
  const [webshellUrl, setWebshellUrl] = useState('https://107.ustc.edu.cn')
  const [webshellCluster, setWebshellCluster] = useState('training')
  const [webshellLoginNode, setWebshellLoginNode] = useState('11.11.10.202')
  const [webshellCookie, setWebshellCookie] = useState('')

  // LLM 配置
  const [llmApiBase, setLlmApiBase] = useState('https://api.llm.ustc.edu.cn/v1')
  const [llmApiKey, setLlmApiKey] = useState('')
  const [llmModel, setLlmModel] = useState('deepseek-v4-pro')

  // 从 localStorage 加载保存的配置
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const cfg = JSON.parse(saved)
        if (cfg.mode) setMode(cfg.mode)
        if (cfg.rest_url) setRestUrl(cfg.rest_url)
        if (cfg.rest_user) setRestUser(cfg.rest_user)
        if (cfg.rest_token) setRestToken(cfg.rest_token)
        if (cfg.ssh_host) setSshHost(cfg.ssh_host)
        if (cfg.ssh_user) setSshUser(cfg.ssh_user)
        if (cfg.ssh_password) setSshPassword(cfg.ssh_password)
        if (cfg.ssh_port) setSshPort(cfg.ssh_port)
        if (cfg.webshell_url) setWebshellUrl(cfg.webshell_url)
        if (cfg.webshell_cluster) setWebshellCluster(cfg.webshell_cluster)
        if (cfg.webshell_login_node) setWebshellLoginNode(cfg.webshell_login_node)
        if (cfg.webshell_cookie) setWebshellCookie(cfg.webshell_cookie)
        if (cfg.llm_api_base) setLlmApiBase(cfg.llm_api_base)
        if (cfg.llm_api_key) setLlmApiKey(cfg.llm_api_key)
        if (cfg.llm_model) setLlmModel(cfg.llm_model)
      }
    } catch {
      // 忽略解析错误
    }
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const config = {
      mode,
      rest_url: restUrl,
      rest_user: restUser,
      rest_token: restToken,
      ssh_host: sshHost,
      ssh_user: sshUser,
      ssh_password: sshPassword,
      ssh_port: sshPort,
      webshell_url: webshellUrl,
      webshell_cluster: webshellCluster,
      webshell_login_node: webshellLoginNode,
      webshell_cookie: webshellCookie,
      llm_api_base: llmApiBase,
      llm_api_key: llmApiKey,
      llm_model: llmModel,
    }
    // 保存配置到 localStorage
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
    } catch {
      // 忽略存储错误
    }
    onConnect(config)
  }

  const inputClass =
    'w-full bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] outline-none transition-shadow focus:border-[var(--accent-blue-end)] focus:shadow-[0_0_0_2px_rgba(0,81,168,.12)]'
  const labelClass = 'block text-xs text-[var(--text-secondary)] mb-1.5'

  return (
    <div
      className="min-h-screen flex items-center justify-center bg-[var(--bg-deep)] p-4"
      style={{
        backgroundImage:
          'radial-gradient(ellipse 70% 50% at 50% 0%, rgba(0,81,168,.08), transparent 60%), radial-gradient(ellipse 50% 40% at 90% 100%, rgba(0,51,102,.05), transparent 60%)',
      }}
    >
      <div className="w-full max-w-lg bg-[var(--bg-panel)] rounded-[var(--radius-xl)] p-7 border border-[var(--border)] shadow-2xl">
        {/* 标题区 */}
        <div className="flex flex-col items-center mb-6">
          <div
            className="w-12 h-12 rounded-[var(--radius-lg)] flex items-center justify-center font-mono-term text-xl font-semibold text-white mb-3"
            style={{ background: 'var(--grad-blue)', boxShadow: '0 4px 20px rgba(0,81,168,.3)' }}
          >
            &gt;_
          </div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">HPC Copilot</h1>
          <p className="text-[var(--text-muted)] text-sm mt-1">连接到你的算力中心，开始智能诊断</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* 连接模式选择 */}
          <div>
            <label className={labelClass}>连接模式</label>
            <div className="flex gap-2">
              {(['webshell', 'rest', 'ssh'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`flex-1 py-2 rounded-[var(--radius-md)] text-sm font-medium transition-colors border ${
                    mode === m
                      ? 'text-white border-[var(--accent-blue-end)]'
                      : 'bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border)] hover:border-[var(--text-dim)]'
                  }`}
                  style={mode === m ? { background: 'var(--grad-blue)' } : undefined}
                >
                  {m === 'webshell' ? 'Web Shell' : m === 'rest' ? 'REST API' : 'SSH'}
                </button>
              ))}
            </div>
          </div>

          {/* Web Shell (SCOW) 配置 */}
          {mode === 'webshell' && (
            <div className="space-y-3 p-4 bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border border-[var(--border)]">
              <div>
                <label className={labelClass}>平台地址</label>
                <input
                  className={inputClass}
                  placeholder="https://107.ustc.edu.cn"
                  value={webshellUrl}
                  onChange={(e) => setWebshellUrl(e.target.value)}
                />
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className={labelClass}>集群 ID</label>
                  <input
                    className={inputClass}
                    placeholder="training"
                    value={webshellCluster}
                    onChange={(e) => setWebshellCluster(e.target.value)}
                  />
                </div>
                <div className="flex-1">
                  <label className={labelClass}>登录节点</label>
                  <input
                    className={inputClass}
                    placeholder="11.11.10.202"
                    value={webshellLoginNode}
                    onChange={(e) => setWebshellLoginNode(e.target.value)}
                  />
                </div>
              </div>
              <div>
                <label className={labelClass}>
                  Cookie（从浏览器 F12 → Network → 任意请求 → Headers 中复制）
                </label>
                <textarea
                  className={inputClass + ' h-20 resize-none font-mono-term text-xs'}
                  placeholder="session=xxx; other=yyy"
                  value={webshellCookie}
                  onChange={(e) => setWebshellCookie(e.target.value)}
                />
              </div>
              <p className="text-xs text-[var(--text-dim)] leading-relaxed">
                提示：在浏览器中登录算力平台后，按 F12 打开开发者工具，
                在 Network 标签页中找到任意请求，复制 Request Headers 中的 Cookie 值。
              </p>
            </div>
          )}

          {/* REST API 配置 */}
          {mode === 'rest' && (
            <div className="space-y-3 p-4 bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border border-[var(--border)]">
              <div>
                <label className={labelClass}>API 地址</label>
                <input
                  className={inputClass}
                  placeholder="http://hpc.school.edu:6820"
                  value={restUrl}
                  onChange={(e) => setRestUrl(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>用户名</label>
                <input
                  className={inputClass}
                  placeholder="你的集群用户名"
                  value={restUser}
                  onChange={(e) => setRestUser(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>Token（可选）</label>
                <input
                  className={inputClass}
                  type="password"
                  placeholder="JWT token（如果有的话）"
                  value={restToken}
                  onChange={(e) => setRestToken(e.target.value)}
                />
              </div>
            </div>
          )}

          {/* SSH 配置 */}
          {mode === 'ssh' && (
            <div className="space-y-3 p-4 bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border border-[var(--border)]">
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className={labelClass}>主机地址</label>
                  <input
                    className={inputClass}
                    placeholder="hpc.school.edu"
                    value={sshHost}
                    onChange={(e) => setSshHost(e.target.value)}
                  />
                </div>
                <div className="w-20">
                  <label className={labelClass}>端口</label>
                  <input
                    className={inputClass}
                    placeholder="22"
                    value={sshPort}
                    onChange={(e) => setSshPort(e.target.value)}
                  />
                </div>
              </div>
              <div>
                <label className={labelClass}>用户名</label>
                <input
                  className={inputClass}
                  placeholder="你的集群用户名"
                  value={sshUser}
                  onChange={(e) => setSshUser(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>密码</label>
                <input
                  className={inputClass}
                  type="password"
                  placeholder="SSH 密码"
                  value={sshPassword}
                  onChange={(e) => setSshPassword(e.target.value)}
                />
              </div>
            </div>
          )}

          {/* LLM 配置 */}
          <details className="group">
            <summary className="cursor-pointer text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
              ⚙ 大模型配置
            </summary>
            <div className="space-y-3 mt-3 p-4 bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border border-[var(--border)]">
              <div>
                <label className={labelClass}>API 地址</label>
                <input
                  className={inputClass}
                  placeholder="https://api.llm.ustc.edu.cn/v1"
                  value={llmApiBase}
                  onChange={(e) => setLlmApiBase(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>API Key</label>
                <input
                  className={inputClass}
                  type="password"
                  placeholder="sk-..."
                  value={llmApiKey}
                  onChange={(e) => setLlmApiKey(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>模型名称</label>
                <input
                  className={inputClass}
                  placeholder="deepseek-v4-pro"
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                />
              </div>
            </div>
          </details>

          {/* 提交按钮 */}
          <button
            type="submit"
            className="w-full py-3 text-white rounded-[var(--radius-lg)] font-semibold transition-all hover:-translate-y-px"
            style={{ background: 'var(--grad-blue)', border: '1px solid var(--accent-blue-end)', boxShadow: '0 4px 20px rgba(0,81,168,.3)' }}
          >
            连接并开始
          </button>
        </form>
      </div>
    </div>
  )
}
