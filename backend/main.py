"""HPC Copilot - FastAPI 后端入口。

提供 WebSocket 接口：
- /ws/terminal: 终端交互（命令执行 + 输出流）
- /ws/chat: 对话交互（LLM 解释 + Agent 主动推送）

启动方式：
    uvicorn backend.main:app --reload --port 8000
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 确保 backend 目录在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.monitor import AgentMonitor
from core.diagnostics import DiagnosticsEngine
from core.llm_client import LLMClient
from core.models import ErrorType
from executor.slurm_rest import SlurmRestExecutor
from executor.ssh_executor import SSHExecutor
from executor.webshell_executor import WebShellExecutor

# 日志：输出到 uvicorn 控制台，便于排查后台监控等问题
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hpc-copilot")

# ─── FastAPI 应用 ────────────────────────────────────────────

app = FastAPI(
    title="HPC Copilot Backend",
    description="面向算力平台初学者的 Slurm 作业故障诊断智能体",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── 连接配置模型 ────────────────────────────────────────────

class ConnectionConfig(BaseModel):
    """连接配置（前端通过 REST 接口设置）。"""

    mode: str = "rest"  # "rest" / "ssh" / "webshell"
    # REST API 配置
    rest_url: str = ""
    rest_user: str = ""
    rest_token: str = ""
    # SSH 配置
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_password: str = ""
    ssh_key: str = ""
    ssh_port: int = 22
    # Web Shell (SCOW) 配置
    webshell_url: str = ""  # SCOW 平台地址，如 https://107.ustc.edu.cn
    webshell_cluster: str = "training"  # 集群 ID
    webshell_login_node: str = ""  # 登录节点，如 11.11.10.202
    webshell_cookie: str = ""  # 浏览器 Cookie
    # LLM 配置
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"


# 配置文件路径：让连接配置在后端重启（如 uvicorn --reload）后自动恢复
_CONFIG_FILE = Path(__file__).resolve().parent / "data" / "connection_config.json"


def _save_config(config: ConnectionConfig) -> None:
    """把连接配置写入磁盘。"""
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(
            config.model_dump_json(indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("保存连接配置到磁盘失败: %s", e)


def _load_config() -> Optional[ConnectionConfig]:
    """启动时从磁盘读取上次的连接配置（没有则返回 None）。"""
    try:
        if _CONFIG_FILE.exists():
            return ConnectionConfig.model_validate_json(
                _CONFIG_FILE.read_text(encoding="utf-8")
            )
    except Exception as e:
        logger.warning("从磁盘加载连接配置失败: %s", e)
    return None


# 全局连接配置（启动时尝试从磁盘恢复，免去每次重启后重新填写）
_connection_config: Optional[ConnectionConfig] = _load_config()


# ─── REST 接口 ───────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """健康检查。"""
    return {"status": "ok", "version": "0.2.0"}


@app.post("/api/connect")
async def set_connection(config: ConnectionConfig):
    """设置连接配置（并持久化到磁盘，重启后自动恢复）。"""
    global _connection_config
    _connection_config = config
    _save_config(config)
    return {"status": "configured", "mode": config.mode}


# ─── 后台作业监控 ────────────────────────────────────────────

# 需要告警的作业失败终态
_FAILURE_STATES = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}


async def _job_monitor_loop(
    ws: WebSocket, config: ConnectionConfig, send_lock: asyncio.Lock
) -> None:
    """后台任务：轮询作业状态，检测作业失败并推送诊断告警。

    使用独立的 Web Shell 连接（不干扰用户终端）。sbatch 提交的作业
    在计算节点上运行，报错不会出现在登录节点终端，因此必须靠轮询
    作业状态来捕获失败。

    send_lock 与主终端循环共享，保证对同一 WebSocket 的发送串行化。
    """
    # 创建独立的监控连接
    poller = WebShellExecutor(
        base_url=config.webshell_url,
        cluster=config.webshell_cluster,
        login_node=config.webshell_login_node,
        cookie=config.webshell_cookie,
    )
    engine = DiagnosticsEngine()
    known_states: dict[str, str] = {}   # job_id -> 上次状态
    alerted: set[str] = set()           # 已告警过的作业，避免重复
    last_pushed: dict[str, str] | None = None  # 上次推给前端的作业快照

    try:
        await poller.connect()
        initial = await poller.read_initial_output()  # 读取初始提示符
        if initial.strip():
            logger.info("作业监控已启动（独立 Web Shell 连接）")
        else:
            # 连接建立但没拿到 shell 提示符：监控可能静默空转，必须明确告警
            logger.warning(
                "作业监控未获取到 shell 提示符，可能无法正常工作（疑似凭证过期或会话受限）"
            )
    except Exception as e:
        logger.warning("作业监控连接失败，已退出: %s", e)
        return  # 监控连接失败，退出（不影响用户终端）

    # 启动基线扫描：记录当前已存在的作业。对其中已处于失败终态的作业，
    # 直接标记为"已告警"，避免把队列里残留的旧失败作业当成新失败反复打扰用户。
    # 有了基线，后续告警就不再依赖"必须先见过正常态"，快速失败的作业也不会漏报。
    try:
        baseline = await poller.get_job_states()
        for jid, st in baseline.items():
            known_states[jid] = st
            if st in _FAILURE_STATES:
                alerted.add(jid)
        logger.info("作业监控基线扫描完成，当前作业: %s", baseline or "无")
    except Exception as e:
        logger.warning("作业监控基线扫描失败: %s", e)

    try:
        poll_count = 0
        while True:
            await asyncio.sleep(2)  # FAILED 作业只保留 1-2 分钟，轮询要快
            try:
                states = await poller.get_job_states()
            except Exception as e:
                logger.warning("作业状态轮询失败: %s", e)
                continue

            poll_count += 1
            if states:
                logger.info("当前作业状态: %s", states)
            elif poll_count % 15 == 0:
                # 约每 30 秒打一次心跳，证明监控还活着、没在静默空转
                logger.info("作业监控运行中（当前队列无作业）")

            # 作业队列快照有变化时推给前端，驱动"作业队列"小组件实时刷新。
            # 只在快照变化时推送，避免每 2 秒无意义地刷消息。
            if states != last_pushed:
                last_pushed = dict(states)
                try:
                    async with send_lock:
                        await ws.send_json({
                            "type": "jobs_update",
                            "data": {
                                "jobs": [
                                    {"job_id": jid, "state": st}
                                    for jid, st in states.items()
                                ]
                            },
                        })
                except Exception:
                    return  # WebSocket 已断开，退出监控

            for job_id, state in states.items():
                prev = known_states.get(job_id)
                known_states[job_id] = state

                # 作业进入失败终态且尚未告警 → 告警。
                # 启动基线已把旧的失败作业标为已告警，故这里无需再要求
                # "必须先见过正常态"——即使第一次看到作业它就已是失败态
                # （快速失败或监控重启后才出现），也不会漏报。
                if state in _FAILURE_STATES and job_id not in alerted:
                    alerted.add(job_id)
                    logger.info(
                        "检测到作业 %s 失败: %s -> %s", job_id, prev, state
                    )

                    # 结构化诊断：FAILED 通常是程序非零退出
                    state_reason = "NonZeroExitCode" if state == "FAILED" else ""
                    diagnosis = engine.diagnose_job(
                        state=state,
                        state_reason=state_reason,
                    )

                    # 把建议命令/证据里的 <JOB_ID> 占位符替换成真实作业号，
                    # 这样告警卡片上的"一键运行"按钮拿到的就是可直接执行的命令
                    concrete_commands = [
                        cmd.replace("<JOB_ID>", str(job_id))
                        for cmd in diagnosis.suggested_commands
                    ]
                    concrete_evidence = [
                        ev.replace("<JOB_ID>", str(job_id))
                        for ev in diagnosis.evidence
                    ]

                    # 推送到终端（加锁，避免与主循环的输出发送交错）
                    try:
                        async with send_lock:
                            await ws.send_json({
                                "type": "agent_alert",
                                "data": {
                                    "job_id": job_id,
                                    "error_type": diagnosis.error_type.value,
                                    "confidence": diagnosis.confidence,
                                    "evidence": concrete_evidence,
                                    "root_cause": diagnosis.root_cause_brief,
                                    "suggested_commands": concrete_commands,
                                },
                            })
                    except Exception:
                        return  # WebSocket 已断开，退出监控
    except asyncio.CancelledError:
        pass
    finally:
        await poller.close()


# ─── WebSocket: 终端 ─────────────────────────────────────────

@app.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket):
    """终端 WebSocket：前端输入命令 → 后端执行 → 流式返回输出。

    同时 Agent 实时监听输出，检测到错误时通过同一连接推送告警。
    """
    await ws.accept()
    logger.info("终端客户端已连接")

    # 初始化 Agent 监听器
    monitor = AgentMonitor()

    # 根据配置创建执行器
    executor = _create_executor()
    if executor is None:
        await ws.send_json({
            "type": "output",
            "data": "❌ 未配置连接。请先通过 /api/connect 设置集群连接信息。\n",
        })
        await ws.close()
        return

    # 告诉前端终端模式：webshell 是真实终端（远程回显），其他是命令模式（本地回显）
    await ws.send_json({
        "type": "terminal_mode",
        "data": _connection_config.mode,
    })

    is_webshell = _connection_config.mode == "webshell"

    if is_webshell:
        # 先验证连通、再上报状态，避免给用户"假连接成功"的错觉：
        # WebSocket 握手成功并不代表 shell 可用——Cookie 过期时 SCOW 会
        # 接受连接但不启动 shell，此时 read_initial_output 读不到任何提示符。
        # 只有真正读到初始输出才认为连接成功，否则明确报错并提示换 Cookie。
        initial = ""
        conn_error = ""
        try:
            initial = await executor.read_initial_output()
        except Exception as e:
            conn_error = str(e)
            logger.warning("SCOW Web Shell 初始连接失败: %s", e)

        if initial.strip():
            await ws.send_json({
                "type": "output",
                "data": f"✅ 已连接到算力中心（{_connection_config.mode} 模式）\n",
            })
            await ws.send_json({"type": "output", "data": initial})
        else:
            detail = f"：{conn_error}" if conn_error else ""
            logger.warning("SCOW Web Shell 连接后未获取到提示符（疑似凭证过期）%s", detail)
            await ws.send_json({
                "type": "output",
                "data": f"❌ 连接到算力中心失败（{_connection_config.mode} 模式）{detail}\n",
            })
            await ws.send_json({
                "type": "output",
                "data": "\x1b[31m[未能获取到 shell 提示符]\x1b[0m\r\n"
                        "\x1b[33m[最常见原因是登录凭证（Cookie）过期，请点右上角「设置」重新填写最新 Cookie 后重连]\x1b[0m\r\n",
            })
    else:
        await ws.send_json({
            "type": "output",
            "data": f"✅ 已连接到算力中心（{_connection_config.mode} 模式）\n",
        })

    # 启动后台作业监控（检测 sbatch 提交的作业在计算节点上失败）
    # send_lock 与监控任务共享，保证对同一 WebSocket 的发送串行化
    send_lock = asyncio.Lock()
    monitor_task: Optional[asyncio.Task] = None
    if is_webshell:
        monitor_task = asyncio.create_task(
            _job_monitor_loop(ws, _connection_config, send_lock)
        )

    try:
        while True:
            # 接收前端命令
            raw = await ws.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = {"command": raw}

            # 前端心跳保活消息，直接忽略
            if message.get("type") == "ping":
                continue

            # 前端请求重打提示符：告警横幅是后台异步推送的，会打断 webshell
            # 当前的提示符行（横幅画完后下方没有提示符）。前端画完横幅发此消息，
            # 后端让远程 shell 重新打印提示符——发一个空行即可，bash 收到空行会重打提示符。
            if message.get("type") == "redraw_prompt":
                if is_webshell:
                    try:
                        async for chunk in executor.execute_stream(""):
                            async with send_lock:
                                await ws.send_json({"type": "output", "data": chunk})
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        logger.warning("重打提示符失败: %s", e)
                continue

            command = message.get("command", "")

            if not command.strip():
                continue

            # 回显命令文本：
            # - webshell 按钮触发（echo=true）：真实提示符 "...~$ " 已在屏幕上，
            #   只补命令文本即可，否则会多出一个 $（变成 "~$ $ cat ..."）
            # - 命令模式（非 webshell）：无真实 shell 回显，由后端补完整 "$ 命令"
            # - webshell 用户键入：前端 xterm 已本地回显，后端不补
            if message.get("echo") and is_webshell:
                await ws.send_json({
                    "type": "output",
                    "data": f"{command}\r\n",
                })
            elif not is_webshell:
                await ws.send_json({
                    "type": "output",
                    "data": f"$ {command}\r\n",
                })

            # 执行命令，流式返回（单条命令出错不终止整个会话）
            try:
                async for chunk in executor.execute_stream(command):
                    async with send_lock:
                        await ws.send_json({"type": "output", "data": chunk})

                    # Agent 实时监听
                    diagnosis = monitor.feed(chunk)
                    if diagnosis:
                        # 推送诊断告警到前端（加锁，避免与监控任务交错）
                        async with send_lock:
                            await ws.send_json({
                                "type": "agent_alert",
                                "data": {
                                    "error_type": diagnosis.error_type.value,
                                    "confidence": diagnosis.confidence,
                                    "evidence": diagnosis.evidence,
                                    "root_cause": diagnosis.root_cause_brief,
                                    "suggested_commands": diagnosis.suggested_commands,
                                },
                            })
            except WebSocketDisconnect:
                raise
            except Exception as e:
                async with send_lock:
                    await ws.send_json({
                        "type": "output",
                        "data": f"\r\n\x1b[31m[命令执行出错: {e}]\x1b[0m\r\n",
                    })

            # webshell 模式下远程 shell 自带提示符，不需要额外发
            if not is_webshell:
                await ws.send_json({"type": "prompt", "data": ""})

    except WebSocketDisconnect:
        pass
    finally:
        # 停止后台作业监控
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        await executor.close()


# ─── WebSocket: 对话 ─────────────────────────────────────────

# 对话系统提示词：把真实平台事实硬编码进去（规则定事实），
# 让大模型只负责组织语言和生成脚本，杜绝它编造分区/QoS/命令。
CHAT_SYSTEM_PROMPT = """\
你是 HPC Copilot，帮助算力平台初学者的智能体。
用户是刚开始使用学校 HPC 集群（Slurm 调度器）的学生，对 Linux 和 Slurm 不熟悉。
请用通俗、耐心的中文回答，语气像一位耐心的学长/学姐。

【平台事实 —— 必须严格采用，禁止编造或更改】
- 集群名：training
- 可用分区（partition）：P107-A100（A100 GPU）、P107-RTX5090（RTX5090 GPU）、Students
- QoS：qos_p107-a100
- 可用 module：cuda/13.0、python3.12、miniconda/py312
- 本集群未接入 slurmdbd，sacct 命令不可用；查看作业历史输出请用 cat slurm-<作业号>.out
- 提交作业：sbatch 脚本.sh；查询作业：squeue -u $USER；取消作业：scancel <作业号>

【回答规则】
- 给出可以直接复制粘贴的命令，命令必须逐字准确（例如 sbatch 绝不能写成 sbach）
- 不确定的信息要诚实说明，不要编造分区名、路径或 API
- 不要建议危险操作（rm -rf、改系统文件等）

【生成作业脚本时】
当用户请求生成/写一个作业脚本，请输出一个完整可用的 bash 脚本，放在 ```bash 代码块中。
脚本必须以 #SBATCH 注释行开头，至少包含：
  #SBATCH --partition=<分区>      （默认 P107-A100）
  #SBATCH --qos=qos_p107-a100
  #SBATCH --job-name=<任务名>
  #SBATCH --output=slurm-%j.out
  #SBATCH --time=HH:MM:SS         （时间格式必须是 时:分:秒）
  #SBATCH --gres=gpu:<数量>        （需要 GPU 时）
然后是 module load 行（如 module load cuda/13.0 python3.12）和实际运行命令。
脚本前后用一两句中文说明用法（如何保存为 .sh 文件并 sbatch 提交）。
"""


@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    """对话 WebSocket：用户提问 → LLM 回答。

    也接收 Agent 主动推送的诊断结果，调用 LLM 生成通俗解释。
    """
    await ws.accept()

    # 初始化 LLM 客户端
    llm = _create_llm_client()
    monitor = AgentMonitor()

    # 本连接的多轮对话历史（user/assistant 交替），让追问能记住上文
    history: list[dict] = []

    # 欢迎语由前端本地初始消息展示，这里不再重复发送，避免打招呼两次

    try:
        while True:
            raw = await ws.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = {"type": "user_message", "data": raw}

            msg_type = message.get("type", "user_message")
            content = message.get("data", "")

            if msg_type == "user_message":
                # 用户主动提问
                if not llm.is_configured:
                    await ws.send_json({
                        "type": "reply",
                        "data": "⚠️ 大模型未配置。请在连接设置中填写 API 地址和 Key。\n不过左边的终端诊断功能仍然可用！",
                    })
                    continue

                # 发送"正在思考"状态
                await ws.send_json({"type": "typing", "data": ""})

                # 流式调用大模型（携带历史，支持多轮追问）
                try:
                    full_response = ""
                    async for chunk in llm.chat_stream(
                        system_prompt=CHAT_SYSTEM_PROMPT,
                        user_message=content,
                        history=history,
                    ):
                        full_response += chunk
                        await ws.send_json({"type": "stream_chunk", "data": chunk})

                    # 流式结束
                    await ws.send_json({"type": "stream_end", "data": ""})

                    # 记录本轮对话到历史（保留最近 20 条，控制 token 消耗）
                    history.append({"role": "user", "content": content})
                    history.append({"role": "assistant", "content": full_response})
                    if len(history) > 20:
                        del history[: len(history) - 20]
                except Exception as e:
                    await ws.send_json({
                        "type": "reply",
                        "data": f"⚠️ 大模型调用失败: {e}\n请稍后重试或检查 API 配置。",
                    })

            elif msg_type == "agent_diagnosis":
                # Agent 检测到错误，调用 LLM 生成通俗解释
                diagnosis_data = content
                if llm.is_configured:
                    await ws.send_json({"type": "typing", "data": ""})
                    try:
                        error_type = diagnosis_data.get("error_type", "未知")
                        root_cause = diagnosis_data.get("root_cause", "")
                        evidence = diagnosis_data.get("evidence", [])
                        commands = diagnosis_data.get("suggested_commands", [])
                        job_id = diagnosis_data.get("job_id", "")

                        # 把建议命令里的 <JOB_ID> 占位符替换成真实作业号，
                        # 让用户拿到可以直接复制粘贴的命令
                        concrete_commands = [
                            cmd.replace("<JOB_ID>", str(job_id)) if job_id else cmd
                            for cmd in commands
                        ]

                        job_line = f"作业号：{job_id}\n" if job_id else ""
                        prompt = (
                            f"用户在算力平台上遇到了错误：{error_type}\n"
                            f"{job_line}"
                            f"根因：{root_cause}\n"
                            f"证据：{'; '.join(evidence)}\n"
                            f"建议命令：{'; '.join(concrete_commands)}\n\n"
                            "请用初学者能懂的中文解释发生了什么、为什么、下一步怎么做。"
                            "语气像耐心的学长。"
                            "【注意】上面的建议命令已经以可一键运行的按钮形式展示在界面上了，"
                            "你不需要再逐条抄写命令，专注于把道理讲清楚即可。"
                        )
                        system_prompt = (
                            "你是 HPC Copilot，面向算力平台初学者的智能体。用通俗中文解释错误并给出操作思路。"
                            "【重要平台约束】这个集群没有接入 slurmdbd，sacct 命令不可用，"
                            "绝对不要建议用户使用 sacct；查看作业历史输出请用 cat slurm-<作业号>.out。"
                            "【重要】解释会直接拼接显示在告警卡片内部，建议命令已以按钮形式给出，"
                            "不要重复罗列命令，重点讲清楚发生了什么、为什么、下一步思路。"
                            "如需提及命令，命令名必须逐字准确（sbatch 不能写成 sbach）。"
                        )

                        # 流式输出解释
                        async for chunk in llm.chat_stream(
                            system_prompt=system_prompt,
                            user_message=prompt,
                        ):
                            await ws.send_json({"type": "stream_chunk", "data": chunk})
                        await ws.send_json({"type": "stream_end", "data": ""})
                    except Exception:
                        # LLM 失败时回退到规则引擎的简要信息
                        fallback = (
                            f"🔍 检测到错误：{diagnosis_data.get('error_type', '未知')}\n"
                            f"原因：{diagnosis_data.get('root_cause', '分析中...')}\n"
                            f"建议命令：{', '.join(diagnosis_data.get('suggested_commands', []))}"
                        )
                        await ws.send_json({"type": "agent_explanation", "data": fallback})

    except WebSocketDisconnect:
        pass


# ─── 辅助函数 ────────────────────────────────────────────────

def _create_executor():
    """根据全局配置创建执行器实例。"""
    global _connection_config
    if _connection_config is None:
        return None

    if _connection_config.mode == "ssh":
        return SSHExecutor(
            host=_connection_config.ssh_host,
            username=_connection_config.ssh_user,
            password=_connection_config.ssh_password,
            key_filename=_connection_config.ssh_key,
            port=_connection_config.ssh_port,
        )
    elif _connection_config.mode == "webshell":
        return WebShellExecutor(
            base_url=_connection_config.webshell_url,
            cluster=_connection_config.webshell_cluster,
            login_node=_connection_config.webshell_login_node,
            cookie=_connection_config.webshell_cookie,
        )
    else:
        return SlurmRestExecutor(
            base_url=_connection_config.rest_url,
            user=_connection_config.rest_user,
            token=_connection_config.rest_token,
        )


def _create_llm_client() -> LLMClient:
    """根据全局配置创建 LLM 客户端。"""
    global _connection_config
    if _connection_config is None:
        return LLMClient()

    return LLMClient(
        api_base=_connection_config.llm_api_base,
        api_key=_connection_config.llm_api_key,
        model=_connection_config.llm_model,
    )


# ─── 开发模式直接运行 ────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
