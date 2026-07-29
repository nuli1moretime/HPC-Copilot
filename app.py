"""HPC Copilot - Streamlit 主界面。

三种诊断模式：
1. Job ID 自动诊断：输入作业号 → REST API 拉取状态 → 规则诊断 → 大模型解释
2. 后台监控：盯着一个作业，失败时自动触发诊断
3. 手动粘贴日志：直接粘贴报错文本 → 诊断 → 解释
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st

from hpc_copilot.diagnostics import DiagnosticsEngine
from hpc_copilot.llm_client import LLMClient
from hpc_copilot.models import DiagnosisResult, ErrorType
from hpc_copilot.platform import RestPlatformAdapter, JobInfo

# ─── 页面配置 ───────────────────────────────────────────────
st.set_page_config(page_title="HPC Copilot", page_icon="🖥️", layout="wide")

# ─── 初始化 ─────────────────────────────────────────────────
engine = DiagnosticsEngine()

# ─── 侧边栏配置 ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 配置")

    st.subheader("平台连接")
    slurm_api_base = st.text_input(
        "Slurm REST API",
        value="http://107.ustc.edu.cn:6820",
        help="学校算力平台 slurmrestd 地址",
    )
    slurm_token = st.text_input(
        "JWT Token",
        type="password",
        placeholder="scontrol token 生成的令牌",
        help="在集群上运行 scontrol token 获取",
    )

    st.divider()
    st.subheader("大模型")
    llm_api_base = st.text_input(
        "API 地址",
        placeholder="https://your-endpoint/v1",
    )
    llm_api_key = st.text_input("API Key", type="password", placeholder="sk-...")
    llm_model = st.text_input("模型名称", value="gpt-4o-mini")

    st.divider()
    st.caption("平台连接用于自动拉取作业状态；大模型用于生成通俗解释。两者独立，可分别配置。")


# ─── 工具函数 ───────────────────────────────────────────────
def get_platform() -> RestPlatformAdapter | None:
    if not slurm_token:
        return None
    return RestPlatformAdapter(api_base=slurm_api_base, token=slurm_token)


def get_llm() -> LLMClient:
    return LLMClient(api_base=llm_api_base, api_key=llm_api_key, model=llm_model)


def render_diagnosis(diagnosis: DiagnosisResult, log_text: str):
    """渲染诊断结果 + 大模型解释（复用组件）。"""
    if diagnosis.error_type == ErrorType.UNKNOWN:
        st.warning("没有匹配到已知的错误模式。建议提供完整日志或联系管理员。")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric("错误类型", diagnosis.error_type.value)
    with col2:
        st.metric("置信度", f"{diagnosis.confidence:.0%}")

    if diagnosis.evidence:
        st.markdown("**证据：**")
        for e in diagnosis.evidence:
            st.code(e, language="text")

    # 大模型解释
    llm = get_llm()
    if llm.is_configured:
        with st.spinner("智能体正在分析..."):
            hint = engine.get_pattern_hint(diagnosis.error_type)
            agent_resp = llm.explain_diagnosis(diagnosis, log_text, hint)

        if agent_resp:
            st.markdown("### 🤖 智能体解释")
            st.info(agent_resp.explanation)
            if agent_resp.next_steps:
                st.markdown("### 👣 下一步操作")
                for i, step in enumerate(agent_resp.next_steps, 1):
                    st.markdown(f"**{i}.** {step}")
            if agent_resp.warning:
                st.warning(f"⚠️ {agent_resp.warning}")
        else:
            st.error("大模型调用失败，显示规则引擎建议：")
            for cmd in diagnosis.suggested_commands:
                st.code(cmd, language="bash")
    else:
        st.markdown("### 🔧 建议操作")
        st.caption("配置大模型 API 后可获得通俗解释。")
        for cmd in diagnosis.suggested_commands:
            st.code(cmd, language="bash")

    with st.expander("🔬 原始诊断数据"):
        st.json(diagnosis.model_dump())


def render_job_status(job: JobInfo):
    """渲染作业状态卡片。"""
    state_color = {
        "RUNNING": "🟢", "COMPLETED": "✅", "PENDING": "🟡",
        "FAILED": "🔴", "CANCELLED": "⚪", "TIMEOUT": "🟠",
    }
    icon = state_color.get(job.state, "❓")
    st.markdown(f"#### {icon} Job {job.job_id} — {job.name}")
    cols = st.columns(4)
    cols[0].metric("状态", job.state)
    cols[1].metric("分区", job.partition)
    cols[2].metric("退出码", job.exit_code if job.exit_code is not None else "-")
    cols[3].metric("原因", job.state_reason or "-")


# ─── 主界面 ─────────────────────────────────────────────────
st.title("🖥️ HPC Copilot")
st.caption("面向算力平台初学者的作业故障诊断智能体")

tab_job, tab_watch, tab_paste = st.tabs([
    "🔍 Job ID 诊断",
    "👁️ 后台监控",
    "📋 粘贴日志",
])

# ═══ Tab 1: Job ID 自动诊断 ═════════════════════════════════
with tab_job:
    st.write("输入作业号，自动从平台拉取状态并诊断。")

    if not slurm_token:
        st.info("请在左侧边栏填入 JWT Token（集群上运行 `scontrol token` 获取）。")
    else:
        job_id_input = st.text_input("作业 ID", placeholder="例如 26291", key="job_id_input")

        if st.button("诊断此作业", type="primary", disabled=not job_id_input.strip()):
            platform = get_platform()
            try:
                job_id = int(job_id_input.strip())
            except ValueError:
                st.error("请输入有效的数字 Job ID")
                st.stop()

            with st.spinner(f"正在查询 Job {job_id} ..."):
                job = platform.get_job(job_id)

            if job is None:
                st.warning(
                    f"Job {job_id} 不在当前队列中（可能已完成并被清理）。"
                    "如果刚提交，请等几秒后重试。"
                )
            else:
                render_job_status(job)
                st.divider()

                if job.is_failed or job.state == "FAILED":
                    st.markdown("### 📋 诊断结果")
                    log_text = job.to_log_text()
                    diagnosis = engine.diagnose_job(
                        state=job.state,
                        exit_code=job.exit_code,
                        state_reason=job.state_reason,
                        log_text=log_text,
                    )
                    render_diagnosis(diagnosis, log_text)
                elif job.state == "COMPLETED":
                    st.success("作业已成功完成，无需诊断。")
                elif job.state == "RUNNING":
                    st.info("作业正在运行中。如果想等它结束后自动诊断，请切换到「后台监控」。")
                elif job.state == "PENDING":
                    st.info(f"作业正在排队等待资源（原因：{job.state_reason or '等待中'}）。")

# ═══ Tab 2: 后台监控 ════════════════════════════════════════
with tab_watch:
    st.write("智能体持续监控你的作业，一旦失败立即自动诊断。")

    if not slurm_token:
        st.info("请在左侧边栏填入 JWT Token。")
    else:
        watch_mode = st.radio(
            "监控模式",
            ["自动扫描（推荐）", "指定 Job ID"],
            horizontal=True,
            help="自动扫描：不需要 Job ID，智能体盯着你所有作业；指定 ID：监控某个特定作业",
        )
        poll_interval = st.slider("轮询间隔（秒）", 2, 30, 3)
        username_input = st.text_input(
            "你的集群用户名",
            value="sa25232066",
            help="用于过滤只属于你的作业",
        )

        if watch_mode == "自动扫描（推荐）":
            st.caption("点击开始后，去终端提交作业即可。智能体会自动捕获你账号下的失败作业。")

            if st.button("👁️ 开始扫描", type="primary"):
                platform = get_platform()
                status_area = st.empty()
                result_area = st.empty()

                # 记录已知的作业 ID，只关注新出现的失败
                known_jobs: set[int] = set()
                # 先扫一次，记录当前已有的作业
                for j in platform.get_jobs(user=username_input.strip()):
                    known_jobs.add(j.job_id)

                start_time = time.time()
                max_wait = 300
                found_failure = False

                status_area.info(
                    f"👁️ 正在扫描 {username_input} 的作业（已记录 {len(known_jobs)} 个现有作业）..."
                )

                while time.time() - start_time < max_wait:
                    elapsed = int(time.time() - start_time)
                    jobs = platform.get_jobs(user=username_input.strip())

                    for job in jobs:
                        # 发现新的失败作业
                        if job.job_id not in known_jobs and job.is_failed:
                            found_failure = True
                            status_area.empty()
                            result_area.markdown(
                                f"### 🚨 捕获到失败作业 Job {job.job_id}（{job.name}）"
                            )
                            render_job_status(job)
                            st.divider()
                            log_text = job.to_log_text()
                            diagnosis = engine.diagnose_job(
                                state=job.state,
                                exit_code=job.exit_code,
                                state_reason=job.state_reason,
                                log_text=log_text,
                            )
                            render_diagnosis(diagnosis, log_text)
                            break

                        # 已知作业变为失败
                        if job.job_id in known_jobs and job.is_failed:
                            found_failure = True
                            status_area.empty()
                            result_area.markdown(
                                f"### 🚨 Job {job.job_id}（{job.name}）刚刚失败"
                            )
                            render_job_status(job)
                            st.divider()
                            log_text = job.to_log_text()
                            diagnosis = engine.diagnose_job(
                                state=job.state,
                                exit_code=job.exit_code,
                                state_reason=job.state_reason,
                                log_text=log_text,
                            )
                            render_diagnosis(diagnosis, log_text)
                            break

                        known_jobs.add(job.job_id)

                    if found_failure:
                        break

                    active_count = len([j for j in jobs if not j.is_terminal])
                    status_area.info(
                        f"👁️ 扫描中... 已监控 {elapsed}s | "
                        f"你的活跃作业: {active_count} 个 | 等待新作业提交..."
                    )
                    time.sleep(poll_interval)

                if not found_failure:
                    status_area.warning("扫描超时（5 分钟），未检测到新的失败作业。")

        else:
            # 指定 Job ID 模式
            watch_id_input = st.text_input("要监控的作业 ID", placeholder="例如 26304", key="watch_id")

            if st.button("开始监控", type="primary", disabled=not watch_id_input.strip()):
                try:
                    watch_id = int(watch_id_input.strip())
                except ValueError:
                    st.error("请输入有效的数字 Job ID")
                    st.stop()

                platform = get_platform()
                status_area = st.empty()
                result_area = st.empty()

                status_area.info(f"正在监控 Job {watch_id}，每 {poll_interval} 秒检查一次...")

                start_time = time.time()
                max_wait = 300
                final_job = None

                while time.time() - start_time < max_wait:
                    job = platform.get_job(watch_id)

                    if job is None:
                        status_area.warning(f"Job {watch_id} 已从队列消失（可能已完成）。")
                        break

                    elapsed = int(time.time() - start_time)
                    status_area.info(
                        f"⏳ Job {watch_id} | 状态: {job.state} | 已监控 {elapsed}s"
                    )

                    if job.is_terminal:
                        final_job = job
                        break

                    time.sleep(poll_interval)

                status_area.empty()

                if final_job:
                    render_job_status(final_job)
                    st.divider()

                    if final_job.is_failed:
                        result_area.markdown("### 🚨 检测到作业失败，自动诊断：")
                        log_text = final_job.to_log_text()
                        diagnosis = engine.diagnose_job(
                            state=final_job.state,
                            exit_code=final_job.exit_code,
                            state_reason=final_job.state_reason,
                            log_text=log_text,
                        )
                        with result_area.container():
                            render_diagnosis(diagnosis, log_text)
                    elif final_job.state == "COMPLETED":
                        result_area.success(f"Job {watch_id} 已成功完成！无需诊断。")
                    else:
                        result_area.info(f"Job {watch_id} 结束，状态: {final_job.state}")
                else:
                    result_area.warning("监控超时或作业已从队列消失。")

# ═══ Tab 3: 手动粘贴日志 ════════════════════════════════════
with tab_paste:
    st.write("把终端里的报错信息粘贴到下面，直接诊断。")

    EXAMPLE_LOGS = {
        "（选择示例或自己粘贴）": "",
        "Invalid qos specification": "sbatch: error: Batch job submission failed: Invalid qos specification",
        "getcwd failed": "sbatch: error: getcwd failed: No such file or directory",
        "作业超时": (
            "slurmstepd: error: *** JOB 12345 ON node01 CANCELLED AT 2025-07-20T14:30:00 "
            "DUE TO TIME LIMIT ***"
        ),
        "Python 文件找不到": (
            "python: can't open file 'train.py': [Errno 2] No such file or directory"
        ),
        "程序报错退出": (
            "Traceback (most recent call last):\n"
            '  File "train.py", line 3, in <module>\n'
            "    import torch\n"
            "ModuleNotFoundError: No module named 'torch'"
        ),
    }

    selected = st.selectbox("快速试用", list(EXAMPLE_LOGS.keys()))
    log_input = st.text_area(
        "粘贴日志",
        value=EXAMPLE_LOGS[selected] if selected != "（选择示例或自己粘贴）" else "",
        height=180,
        placeholder="把终端里的报错信息复制粘贴到这里...",
    )

    if st.button("🔍 开始诊断", type="primary", disabled=not log_input.strip()):
        with st.spinner("正在分析..."):
            diagnosis = engine.diagnose(log_input)
        st.divider()
        st.markdown("### 📋 诊断结果")
        render_diagnosis(diagnosis, log_input)
