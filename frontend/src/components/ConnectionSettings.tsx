import { useState } from 'react'

type Form = Record<string, string>

const STORAGE_KEY = 'hpc-copilot-config'

// 凭证只保留在当前 React 状态中，不写入 localStorage。
const SENSITIVE_KEYS = new Set([
  'rest_token',
  'ssh_password',
  'ssh_key',
  'ssh_totp',
  'webshell_cookie',
  'llm_api_key',
])

// 表单默认值（与后端 ConnectionConfig 默认保持一致）
const DEFAULTS: Form = {
  mode: 'webshell',
  rest_url: '',
  rest_user: '',
  rest_token: '',
  ssh_host: '',
  ssh_user: '',
  ssh_password: '',
  ssh_key: '',
  ssh_totp: '',
  ssh_port: '22',
  webshell_url: 'https://107.ustc.edu.cn',
  webshell_cluster: 'training',
  webshell_login_node: '11.11.10.202',
  webshell_cookie: '',
  llm_api_base: 'https://api.llm.ustc.edu.cn/v1',
  llm_api_key: '',
  llm_model: 'deepseek-v4-pro',
}

// 集群连接字段 / 大模型字段——两套配置相互独立
const CLUSTER_KEYS = [
  'mode',
  'rest_url',
  'rest_user',
  'rest_token',
  'ssh_host',
  'ssh_user',
  'ssh_password',
  'ssh_key',
  'ssh_totp',
  'ssh_port',
  'webshell_url',
  'webshell_cluster',
  'webshell_login_node',
  'webshell_cookie',
]
const LLM_KEYS = ['llm_api_base', 'llm_api_key', 'llm_model']

function loadConfig(): Form {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved) as Form
      for (const key of SENSITIVE_KEYS) delete parsed[key]
      // 顺手迁移旧版本留下的明文凭证。
      localStorage.setItem(STORAGE_KEY, JSON.stringify(parsed))
      return { ...DEFAULTS, ...parsed }
    }
  } catch {
    // 忽略解析错误
  }
  return { ...DEFAULTS }
}

function saveConfig(patch: Form) {
  try {
    const cur = loadConfig()
    const merged = { ...cur, ...patch }
    for (const key of SENSITIVE_KEYS) delete merged[key]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
  } catch {
    // 忽略存储错误
  }
}

function pick(form: Form, keys: string[]): Form {
  const out: Form = {}
  for (const k of keys) out[k] = form[k] ?? ''
  return out
}

const inputClass =
  'w-full bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] outline-none transition-shadow focus:border-[var(--accent-blue-end)] focus:shadow-[0_0_0_2px_rgba(0,81,168,.12)]'
const labelClass = 'block text-xs text-[var(--text-secondary)] mb-1.5'
const sectionClass =
  'space-y-3 p-4 bg-[var(--bg-elevated)] rounded-[var(--radius-lg)] border border-[var(--border)]'

// ─── 集群连接字段 ────────────────────────────────────────────
function ClusterFields({
  form,
  setField,
}: {
  form: Form
  setField: (k: string, v: string) => void
}) {
  const mode = form.mode || 'webshell'
  return (
    <div className="space-y-4">
      {/* 连接模式选择 */}
      <div>
        <label className={labelClass}>连接模式</label>
        <div className="flex gap-2">
          {(['webshell', 'rest', 'ssh'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setField('mode', m)}
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

      {/* Web Shell (SCOW) */}
      {mode === 'webshell' && (
        <div className={sectionClass}>
          <div>
            <label className={labelClass}>平台地址</label>
            <input
              className={inputClass}
              placeholder="https://107.ustc.edu.cn"
              value={form.webshell_url}
              onChange={(e) => setField('webshell_url', e.target.value)}
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelClass}>集群 ID</label>
              <input
                className={inputClass}
                placeholder="training"
                value={form.webshell_cluster}
                onChange={(e) => setField('webshell_cluster', e.target.value)}
              />
            </div>
            <div className="flex-1">
              <label className={labelClass}>登录节点</label>
              <input
                className={inputClass}
                placeholder="11.11.10.202"
                value={form.webshell_login_node}
                onChange={(e) => setField('webshell_login_node', e.target.value)}
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
              value={form.webshell_cookie}
              onChange={(e) => setField('webshell_cookie', e.target.value)}
            />
          </div>
        </div>
      )}

      {/* REST API */}
      {mode === 'rest' && (
        <div className={sectionClass}>
          <div>
            <label className={labelClass}>API 地址</label>
            <input
              className={inputClass}
              placeholder="http://hpc.school.edu:6820"
              value={form.rest_url}
              onChange={(e) => setField('rest_url', e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass}>用户名</label>
            <input
              className={inputClass}
              placeholder="你的集群用户名"
              value={form.rest_user}
              onChange={(e) => setField('rest_user', e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass}>Token（可选）</label>
            <input
              className={inputClass}
              type="password"
              placeholder="JWT token（如果有的话）"
              value={form.rest_token}
              onChange={(e) => setField('rest_token', e.target.value)}
            />
          </div>
        </div>
      )}

      {/* SSH */}
      {mode === 'ssh' && (
        <div className={sectionClass}>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelClass}>主机地址</label>
              <input
                className={inputClass}
                placeholder="hpc.school.edu"
                value={form.ssh_host}
                onChange={(e) => setField('ssh_host', e.target.value)}
              />
            </div>
            <div className="w-20">
              <label className={labelClass}>端口</label>
              <input
                className={inputClass}
                placeholder="22"
                value={form.ssh_port}
                onChange={(e) => setField('ssh_port', e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className={labelClass}>用户名</label>
            <input
              className={inputClass}
              placeholder="你的集群用户名"
              value={form.ssh_user}
              onChange={(e) => setField('ssh_user', e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass}>密码 / 私钥 passphrase</label>
            <input
              className={inputClass}
              type="password"
              placeholder="私钥带 passphrase 时填在这里"
              value={form.ssh_password}
              onChange={(e) => setField('ssh_password', e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass}>
              私钥（粘贴 PEM 内容，或填私钥文件路径如 C:\Users\pc\.ssh\id_ed25519）
            </label>
            <textarea
              className={inputClass + ' h-24 resize-none font-mono-term text-xs'}
              placeholder={
                "C:\\Users\\pc\\.ssh\\id_ed25519\n或粘贴 -----BEGIN OPENSSH PRIVATE KEY-----"
              }
              value={form.ssh_key}
              onChange={(e) => setField('ssh_key', e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass}>
              动态验证码（Google Authenticator 6 位数字，两步验证时填写）
            </label>
            <input
              className={inputClass + ' font-mono-term tracking-widest'}
              placeholder="例如 123456"
              maxLength={6}
              inputMode="numeric"
              value={form.ssh_totp}
              onChange={(e) => setField('ssh_totp', e.target.value.replace(/\D/g, ''))}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ─── 大模型字段 ──────────────────────────────────────────────
function LlmFields({
  form,
  setField,
}: {
  form: Form
  setField: (k: string, v: string) => void
}) {
  return (
    <div className={sectionClass}>
      <div>
        <label className={labelClass}>API 地址</label>
        <input
          className={inputClass}
          placeholder="https://api.llm.ustc.edu.cn/v1"
          value={form.llm_api_base}
          onChange={(e) => setField('llm_api_base', e.target.value)}
        />
      </div>
      <div>
        <label className={labelClass}>API Key</label>
        <input
          className={inputClass}
          type="password"
          placeholder="sk-..."
          value={form.llm_api_key}
          onChange={(e) => setField('llm_api_key', e.target.value)}
        />
      </div>
      <div>
        <label className={labelClass}>模型名称</label>
        <input
          className={inputClass}
          placeholder="deepseek-v4-pro"
          value={form.llm_model}
          onChange={(e) => setField('llm_model', e.target.value)}
        />
      </div>
      <p className="text-xs text-[var(--text-dim)] leading-relaxed">
        大模型用于把规则引擎的诊断结果组织成自然语言解释，同时驱动知识库检索（RAG）。
        与算力平台登录相互独立，可单独配置、随时修改。
      </p>
    </div>
  )
}

// ─── 首次进入：两步向导 ──────────────────────────────────────
interface WizardProps {
  onClusterConnect: (cfg: Form) => Promise<boolean>
  onLlmSave: (cfg: Form) => Promise<boolean>
  onDone: () => void
}

export default function ConnectionSettings({
  onClusterConnect,
  onLlmSave,
  onDone,
}: WizardProps) {
  const [step, setStep] = useState<1 | 2>(1)
  const [form, setForm] = useState<Form>(() => loadConfig())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const setField = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const submitCluster = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    const cfg = pick(form, CLUSTER_KEYS)
    saveConfig(cfg)
    const ok = await onClusterConnect(cfg)
    setBusy(false)
    if (ok) setStep(2)
    else setErr('连接失败：请确认后端已启动（uvicorn backend.main:app），并检查填写是否正确。')
  }

  const submitLlm = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    const cfg = pick(form, LLM_KEYS)
    saveConfig(cfg)
    const ok = await onLlmSave(cfg)
    setBusy(false)
    if (ok) onDone()
    else setErr('大模型配置保存失败，请检查后端是否启动。')
  }

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
        <div className="flex flex-col items-center mb-5">
          <div
            className="w-12 h-12 rounded-[var(--radius-lg)] flex items-center justify-center font-mono-term text-xl font-semibold text-white mb-3"
            style={{ background: 'var(--grad-blue)', boxShadow: '0 4px 20px rgba(0,81,168,.3)' }}
          >
            &gt;_
          </div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">HPC Copilot</h1>
          <p className="text-[var(--text-muted)] text-sm mt-1">
            {step === 1 ? '第 1 步 · 登录算力平台' : '第 2 步 · 配置大模型 API'}
          </p>
        </div>

        {/* 步骤指示 */}
        <div className="flex items-center gap-2 mb-5">
          {[1, 2].map((s) => (
            <div key={s} className="flex-1 flex items-center gap-2">
              <span
                className={`w-6 h-6 flex-shrink-0 rounded-full text-xs flex items-center justify-center font-semibold border ${
                  step >= s
                    ? 'text-white border-[var(--accent-blue-end)]'
                    : 'text-[var(--text-dim)] border-[var(--border)] bg-[var(--bg-elevated)]'
                }`}
                style={step >= s ? { background: 'var(--grad-blue)' } : undefined}
              >
                {s}
              </span>
              <span
                className={`text-xs ${step >= s ? 'text-[var(--text-secondary)]' : 'text-[var(--text-dim)]'}`}
              >
                {s === 1 ? '算力平台' : '大模型'}
              </span>
              {s === 1 && <div className="flex-1 h-px bg-[var(--border)]" />}
            </div>
          ))}
        </div>

        {step === 1 ? (
          <form onSubmit={submitCluster} className="space-y-4">
            <ClusterFields form={form} setField={setField} />
            {err && <p className="text-xs text-[var(--error)] leading-relaxed">{err}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full py-3 text-white rounded-[var(--radius-lg)] font-semibold transition-all hover:-translate-y-px disabled:opacity-60 disabled:hover:translate-y-0"
              style={{
                background: 'var(--grad-blue)',
                border: '1px solid var(--accent-blue-end)',
                boxShadow: '0 4px 20px rgba(0,81,168,.3)',
              }}
            >
              {busy ? '连接中…' : '连接算力平台 →'}
            </button>
          </form>
        ) : (
          <form onSubmit={submitLlm} className="space-y-4">
            <LlmFields form={form} setField={setField} />
            {err && <p className="text-xs text-[var(--error)] leading-relaxed">{err}</p>}
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onDone}
                disabled={busy}
                className="flex-1 py-3 rounded-[var(--radius-lg)] font-medium text-[var(--text-secondary)] bg-[var(--bg-elevated)] border border-[var(--border)] hover:border-[var(--text-dim)] transition-colors disabled:opacity-60"
              >
                稍后再说
              </button>
              <button
                type="submit"
                disabled={busy}
                className="flex-1 py-3 text-white rounded-[var(--radius-lg)] font-semibold transition-all hover:-translate-y-px disabled:opacity-60 disabled:hover:translate-y-0"
                style={{
                  background: 'var(--grad-blue)',
                  border: '1px solid var(--accent-blue-end)',
                  boxShadow: '0 4px 20px rgba(0,81,168,.3)',
                }}
              >
                {busy ? '保存中…' : '保存并进入'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

// ─── 顶栏入口：独立弹窗（集群 / 大模型 各一个）─────────────────
interface ModalProps {
  section: 'cluster' | 'llm'
  onClose: () => void
  onClusterConnect: (cfg: Form) => Promise<boolean>
  onLlmSave: (cfg: Form) => Promise<boolean>
}

export function SettingsModal({
  section,
  onClose,
  onClusterConnect,
  onLlmSave,
}: ModalProps) {
  const [form, setForm] = useState<Form>(() => loadConfig())
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [ok, setOk] = useState(false)

  const setField = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setMsg('')
    setBusy(true)
    if (section === 'cluster') {
      const cfg = pick(form, CLUSTER_KEYS)
      saveConfig(cfg)
      const success = await onClusterConnect(cfg)
      setBusy(false)
      setOk(success)
      setMsg(success ? '已保存，重连终端后生效' : '保存失败，请检查后端与填写内容')
      if (success) setTimeout(onClose, 900)
    } else {
      const cfg = pick(form, LLM_KEYS)
      saveConfig(cfg)
      const success = await onLlmSave(cfg)
      setBusy(false)
      setOk(success)
      setMsg(success ? '已保存，对话与知识库检索即刻生效' : '保存失败，请检查后端与填写内容')
      if (success) setTimeout(onClose, 900)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-[var(--bg-panel)] rounded-[var(--radius-xl)] p-6 border border-[var(--border)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">
            {section === 'cluster' ? '算力平台连接' : '大模型 API'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-[var(--radius-sm)] text-[var(--text-dim)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)] transition-colors"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          {section === 'cluster' ? (
            <ClusterFields form={form} setField={setField} />
          ) : (
            <LlmFields form={form} setField={setField} />
          )}

          {msg && (
            <p className={`text-xs leading-relaxed ${ok ? 'text-[var(--accent-green)]' : 'text-[var(--error)]'}`}>
              {msg}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full py-2.5 text-white rounded-[var(--radius-lg)] font-semibold transition-all hover:-translate-y-px disabled:opacity-60 disabled:hover:translate-y-0"
            style={{
              background: 'var(--grad-blue)',
              border: '1px solid var(--accent-blue-end)',
              boxShadow: '0 4px 20px rgba(0,81,168,.3)',
            }}
          >
            {busy ? '保存中…' : '保存'}
          </button>
        </form>
      </div>
    </div>
  )
}
