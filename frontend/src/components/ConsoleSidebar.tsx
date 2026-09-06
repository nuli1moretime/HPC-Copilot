import { useState } from 'react'
import { JOB_TEMPLATES } from '../data/jobTemplates'

interface Props {
  onRunCommand?: (cmd: string) => void
  /** 点击作业模板卡片时触发：ID 用于协议，request 用于对话区自然语言展示。 */
  onRunTemplate?: (templateId: string, request: string) => void
}

type PanelTab = 'commands' | 'info' | 'templates'

// ─── Slurm 常用命令速查 ───
const SLURM_COMMANDS: { cmd: string; desc: string }[] = [
  { cmd: 'squeue -u $USER', desc: '查看我的作业队列' },
  { cmd: 'sinfo', desc: '查看分区与节点状态' },
  { cmd: 'ls -t slurm-*.out | head -5', desc: '列出最近的作业输出文件' },
  { cmd: 'df -h ~', desc: '检查主目录剩余空间' },
  { cmd: 'scontrol show partitions', desc: '查看分区配置详情' },
  { cmd: 'scancel <JOB_ID>', desc: '取消指定作业' },
  { cmd: 'cat slurm-<JOB_ID>.out', desc: '查看指定作业的输出' },
]

// ─── 平台信息（与后端 CHAT_SYSTEM_PROMPT 中的平台事实一致） ───
const PARTITIONS = [
  { name: 'P107-A100', desc: 'A100 GPU 分区' },
  { name: 'P107-RTX5090', desc: 'RTX 5090 GPU 分区' },
  { name: 'Students', desc: '学生专属分区' },
]
const MODULES = ['cuda/13.0', 'python3.12', 'miniconda/py312']

// ─── 内联 SVG 图标 ───
function IconTerminal() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  )
}
function IconBook() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  )
}
function IconInfo() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  )
}
function IconSparkles() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l1.9 5.7L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.3z" />
      <path d="M19 15l.9 2.6L22.5 18l-2.6.9L19 21.5l-.9-2.6L15.5 18l2.6-.4z" />
    </svg>
  )
}

export default function ConsoleSidebar({ onRunCommand, onRunTemplate }: Props) {
  // 默认展开"命令速查"，让左侧一开始就有内容
  const [active, setActive] = useState<PanelTab | null>('commands')

  const toggle = (tab: PanelTab) => setActive((prev) => (prev === tab ? null : tab))

  const iconBtn = (tab: PanelTab, title: string, icon: React.ReactNode) => {
    const isActive = active === tab
    return (
      <button
        onClick={() => toggle(tab)}
        title={title}
        className={`relative w-9 h-9 rounded-[var(--radius-md)] flex items-center justify-center transition-colors ${
          isActive
            ? 'text-[var(--accent-green)] bg-[rgba(52,211,153,.1)]'
            : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)]'
        }`}
      >
        {icon}
        {/* 活动指示条 */}
        {isActive && (
          <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-[var(--accent-green)] -ml-[7px]" />
        )}
      </button>
    )
  }

  const panelTitle =
    active === 'commands' ? '命令速查' :
    active === 'info' ? '平台信息' :
    active === 'templates' ? '作业模板' : ''

  return (
    <div className="flex h-full flex-shrink-0">
      {/* ─── 活动栏 ─── */}
      <div className="w-11 flex flex-col items-center pt-2 pb-3 gap-1 bg-[var(--bg-deep)] border-r border-[var(--border)]">
        {/* 终端（常驻，仅作上下文标识） */}
        <div
          className="w-9 h-9 rounded-[var(--radius-md)] flex items-center justify-center text-[var(--text-dim)]"
          title="终端"
        >
          <IconTerminal />
        </div>
        <div className="w-5 h-px bg-[var(--border)] my-1.5" />
        {iconBtn('templates', '作业模板（一键触发 Agent）', <IconSparkles />)}
        {iconBtn('commands', '命令速查', <IconBook />)}
        {iconBtn('info', '平台信息', <IconInfo />)}
      </div>

      {/* ─── 可展开面板 ─── */}
      {active && (
        <div className="w-60 flex flex-col bg-[var(--bg-panel)] border-r border-[var(--border)]">
          {/* 面板标题 */}
          <div className="flex items-center justify-between px-3.5 h-9 flex-shrink-0 border-b border-[var(--border)]">
            <span className="text-xs font-semibold text-[var(--text-primary)]">{panelTitle}</span>
            <button
              onClick={() => setActive(null)}
              className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-sm leading-none transition-colors"
              title="收起面板"
            >
              ×
            </button>
          </div>

          {/* 面板内容 */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {active === 'templates' && (
              <>
                <p className="text-[11px] text-[var(--text-dim)] leading-relaxed px-0.5">
                  一键触发预设工作流，自动完成写脚本→提交→轮询→AI总结全流程。
                </p>
                {JOB_TEMPLATES.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => onRunTemplate?.(t.id, t.request)}
                    disabled={!onRunTemplate}
                    className="w-full text-left rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] p-2.5 hover:border-[var(--accent-blue-end)] hover:shadow-[0_0_10px_rgba(0,81,168,.08)] transition-all disabled:opacity-50 disabled:cursor-not-allowed group"
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-base flex-shrink-0 leading-none mt-0.5">{t.icon}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-semibold text-[var(--text-primary)] group-hover:text-white transition-colors truncate">
                            {t.title}
                          </span>
                          <span className="text-[9px] px-1.5 py-px rounded-full bg-[rgba(0,81,168,.15)] text-[var(--info)] border border-[rgba(0,81,168,.25)] flex-shrink-0">
                            {t.tag}
                          </span>
                        </div>
                        <div className="text-[11px] text-[var(--text-muted)] mt-1 leading-relaxed">
                          {t.desc}
                        </div>
                        <div className="text-[10px] text-[var(--info)] mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          ▶ 一键执行工作流
                        </div>
                      </div>
                    </div>
                  </button>
                ))}
                <div className="rounded-[var(--radius-md)] bg-[rgba(96,165,250,.06)] border border-[rgba(96,165,250,.2)] px-2.5 py-2 mt-1">
                  <div className="text-[11px] text-[var(--info)] leading-relaxed">
                    💡 模板会把预设指令发给右侧对话面板，Agent 会实时展示每一步工具调用。
                  </div>
                </div>
              </>
            )}

            {active === 'commands' && (
              <>
                <p className="text-[11px] text-[var(--text-dim)] leading-relaxed px-0.5">
                  常用 Slurm 命令，点击 ▶ 直接在终端执行。
                </p>
                {SLURM_COMMANDS.map((c) => {
                  const hasPlaceholder = /<[^>]+>/.test(c.cmd)
                  return (
                    <div
                      key={c.cmd}
                      className="rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] p-2.5 hover:border-[var(--text-dim)] transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <code className="flex-1 text-xs text-[#7ab8f5] font-mono-term break-all">
                          {c.cmd}
                        </code>
                        {onRunCommand && (
                          <button
                            onClick={() => onRunCommand(c.cmd)}
                            disabled={hasPlaceholder}
                            className="flex-shrink-0 w-6 h-6 rounded-[var(--radius-sm)] flex items-center justify-center text-[10px] transition-all disabled:opacity-30 disabled:cursor-not-allowed text-white"
                            style={{
                              background: hasPlaceholder ? 'var(--bg-deep)' : 'rgba(52,211,153,.15)',
                              border: `1px solid ${hasPlaceholder ? 'var(--border)' : 'rgba(52,211,153,.4)'}`,
                              color: hasPlaceholder ? 'var(--text-muted)' : 'var(--accent-green)',
                            }}
                            title={hasPlaceholder ? '需先把 <JOB_ID> 换成真实作业号' : '在终端中执行'}
                          >
                            ▶
                          </button>
                        )}
                      </div>
                      <div className="text-[11px] text-[var(--text-muted)] mt-1.5">{c.desc}</div>
                    </div>
                  )
                })}
              </>
            )}

            {active === 'info' && (
              <>
                {/* 分区 */}
                <div className="text-[11px] text-[var(--text-dim)] uppercase tracking-wide px-0.5 pt-1">分区</div>
                {PARTITIONS.map((p) => (
                  <div
                    key={p.name}
                    className="flex items-center gap-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] px-2.5 py-2"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-green)] flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="text-xs font-mono-term text-[var(--text-primary)]">{p.name}</div>
                      <div className="text-[11px] text-[var(--text-muted)]">{p.desc}</div>
                    </div>
                  </div>
                ))}

                {/* QoS */}
                <div className="text-[11px] text-[var(--text-dim)] uppercase tracking-wide px-0.5 pt-2">QoS</div>
                <div className="rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] px-2.5 py-2">
                  <code className="text-xs font-mono-term text-[#7ab8f5]">qos_p107-a100</code>
                </div>

                {/* 常用模块 */}
                <div className="text-[11px] text-[var(--text-dim)] uppercase tracking-wide px-0.5 pt-2">常用模块</div>
                <div className="flex flex-wrap gap-1.5">
                  {MODULES.map((m) => (
                    <code
                      key={m}
                      className="text-[11px] font-mono-term px-2 py-1 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border)] text-[var(--text-secondary)]"
                    >
                      {m}
                    </code>
                  ))}
                </div>

                {/* 登录节点 */}
                <div className="text-[11px] text-[var(--text-dim)] uppercase tracking-wide px-0.5 pt-2">登录节点</div>
                <div className="rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)] px-2.5 py-2">
                  <code className="text-xs font-mono-term text-[var(--text-primary)]">tradmin-02</code>
                  <span className="text-[11px] text-[var(--text-muted)] ml-2">11.11.10.202</span>
                </div>

                {/* 提示 */}
                <div className="rounded-[var(--radius-md)] bg-[rgba(96,165,250,.06)] border border-[rgba(96,165,250,.2)] px-2.5 py-2 mt-1">
                  <div className="text-[11px] text-[var(--info)] leading-relaxed">
                    💡 本平台未启用 sacct，查询作业输出请用 <code className="font-mono-term">cat slurm-&lt;作业号&gt;.out</code>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
