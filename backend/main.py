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
import os
import posixpath
import re
import shlex
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent.monitor import AgentMonitor
from .core.diagnostics import DiagnosticsEngine
from .core.llm_client import LLMClient
from .executor.slurm_rest import SlurmRestExecutor
from .executor.ssh_executor import SSHExecutor
from .executor.webshell_executor import WebShellExecutor

# RAG 模块
from .rag.embedding import EmbeddingService
from .rag.reranker import Reranker
from .rag.retriever import Retriever
from .rag.prompts import build_rag_prompt

# 日志：输出到 uvicorn 控制台，便于排查后台监控等问题
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hpc-copilot")

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_ESCAPE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _clean_workflow_output(value: str) -> str:
    """移除终端控制码和提示符噪声，供网页工作流卡片展示。"""
    cleaned = _OSC_ESCAPE_RE.sub("", value or "")
    cleaned = _ANSI_ESCAPE_RE.sub("", cleaned).replace("\r", "")
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if "@" in stripped and (stripped.endswith("$") or stripped.endswith(">")):
            continue
        if stripped:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()

# ─── FastAPI 应用 ────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时准备知识库；使用 FastAPI 当前推荐的生命周期接口。"""
    await _init_rag()
    yield

app = FastAPI(
    title="HPC Copilot Backend",
    description="面向算力平台初学者的 Slurm 作业故障诊断智能体",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:8000", "http://114.214.241.118:8000", "http://114.214.241.118"],
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
    ssh_totp: str = ""  # Google Authenticator 动态验证码（两步验证）
    # Web Shell (SCOW) 配置
    webshell_url: str = ""  # SCOW 平台地址，如 https://107.ustc.edu.cn
    webshell_cluster: str = "training"  # 集群 ID
    webshell_login_node: str = ""  # 登录节点，如 11.11.10.202
    webshell_cookie: str = ""  # 浏览器 Cookie
    # LLM 配置
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"


class ClusterConnectRequest(BaseModel):
    """算力平台连接请求（不含大模型配置，二者相互独立）。"""

    mode: str = "webshell"  # "rest" / "ssh" / "webshell"
    rest_url: str = ""
    rest_user: str = ""
    rest_token: str = ""
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_password: str = ""
    ssh_key: str = ""
    ssh_port: int = 22
    ssh_totp: str = ""
    webshell_url: str = ""
    webshell_cluster: str = "training"
    webshell_login_node: str = ""
    webshell_cookie: str = ""


class LLMConfigRequest(BaseModel):
    """大模型 API 配置请求（不含集群连接信息）。"""

    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = ""


# 配置文件路径：让连接配置在后端重启（如 uvicorn --reload）后自动恢复
_CONFIG_FILE = Path(__file__).resolve().parent / "data" / "connection_config.json"

# ─── RAG 配置 ────────────────────────────────────────────────
import yaml

_RAG_CONFIG_FILE = Path(__file__).resolve().parent / "config" / "rag_config.yaml"

def _load_rag_config() -> dict:
    """加载 RAG 配置文件。"""
    try:
        if _RAG_CONFIG_FILE.exists():
            return yaml.safe_load(_RAG_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("加载 RAG 配置失败: %s", e)
    return {"enabled": False}

_rag_config = _load_rag_config()

# 全局 RAG 检索器（启动时初始化）
_retriever: Optional[Retriever] = None
# 防止重复初始化（启动 + 保存大模型配置后重建）并发
_rag_init_lock = asyncio.Lock()


async def _init_rag() -> bool:
    """初始化（或重建）RAG 检索器。返回是否就绪。

    embedding API 与大模型共用同一套配置，因此在「保存大模型配置」后
    需要重新调用本函数，否则先连集群、后配大模型会导致 RAG 拿不到 key。
    """
    global _retriever

    async with _rag_init_lock:
        # 环境变量 RAG_ENABLED=0 可全局关闭（run.py --no-rag 会设置），
        # 关掉后连 FAISS 索引都不加载，启动更快，用于对比测试。
        if os.getenv("RAG_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
            logger.info("RAG 已通过环境变量 RAG_ENABLED=%s 全局关闭，跳过索引加载", os.getenv("RAG_ENABLED"))
            _retriever = None
            return False

        if not _rag_config.get("enabled", False):
            logger.info("RAG 模块已禁用")
            return False

        # 知识库目录和索引缓存目录（相对于项目根目录）
        project_root = Path(__file__).resolve().parent.parent
        knowledge_dir = project_root / _rag_config.get("knowledge_dir", "docs/knowledge")
        index_dir = project_root / _rag_config.get("index_dir", "data/rag_index")

        if not knowledge_dir.exists():
            logger.warning("知识库目录不存在: %s", knowledge_dir)
            return False

        # 从连接配置获取 embedding API 信息（与 LLM 共用）
        api_base = ""
        api_key = ""
        if _connection_config:
            api_base = _connection_config.llm_api_base
            api_key = _connection_config.llm_api_key

        # 创建 embedding 和 reranker 服务
        emb_cfg = _rag_config.get("embedding", {})
        embedding_service = EmbeddingService(
            api_base=api_base,
            api_key=api_key,
            model=emb_cfg.get("model", "qwen3-embedding"),
        )

        rerank_cfg = _rag_config.get("reranker", {})
        reranker = None
        if rerank_cfg.get("enabled", False):
            reranker = Reranker(
                api_base=api_base,
                api_key=api_key,
                model=rerank_cfg.get("model", "qwen3-reranker"),
            )

        # 创建检索器
        retrieval_cfg = _rag_config.get("retrieval", {})
        retriever = Retriever(
            knowledge_dir=knowledge_dir,
            index_dir=index_dir,
            embedding_service=embedding_service,
            reranker=reranker,
            top_k=retrieval_cfg.get("top_k", 3),
            search_top_k=retrieval_cfg.get("search_top_k", 10),
            hybrid_enabled=retrieval_cfg.get("hybrid_enabled", True),
            lexical_weight=float(retrieval_cfg.get("lexical_weight", 0.35)),
        )

        # 初始化（加载或构建索引）
        success = await retriever.initialize()
        if success:
            _retriever = retriever
            logger.info("RAG 模块初始化成功")
            return True
        else:
            logger.warning("RAG 模块初始化失败，对话将不使用知识库")
            _retriever = None
            return False


def _save_config(config: ConnectionConfig) -> None:
    """只把非敏感连接配置写入磁盘。

    密码、私钥、Cookie、Slurm Token 和大模型 API Key 只保留在当前
    进程内存中，避免项目目录中的 JSON 变成凭证副本。
    """
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        persisted_fields = {
            "mode",
            "rest_url",
            "rest_user",
            "ssh_host",
            "ssh_user",
            "ssh_port",
            "webshell_url",
            "webshell_cluster",
            "webshell_login_node",
            "llm_api_base",
            "llm_model",
        }
        data = {
            key: value
            for key, value in config.model_dump().items()
            if key in persisted_fields
        }
        _CONFIG_FILE.write_text(
            ConnectionConfig(**data).model_dump_json(indent=2), encoding="utf-8"
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

# 全局活跃执行器引用：terminal_ws 连接成功后设置，chat_ws 的 agent loop 通过它
# 调用 execute_agent_command 在集群上跑命令（与用户交互终端隔离，互不干扰）。
# terminal_ws 断开时清空，agent 发现为 None 则降级为纯对话（无工具能力）。
_active_executor = None


# ─── REST 接口 ───────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """健康检查。"""
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/rag/status")
async def rag_status():
    """RAG 模块状态。"""
    return {
        "enabled": _rag_config.get("enabled", False),
        "ready": _retriever.is_ready if _retriever else False,
        "knowledge_dir": str(_retriever.knowledge_dir) if _retriever else None,
        "num_chunks": len(_retriever.store._chunks) if _retriever and _retriever.store.is_built else 0,
    }


@app.post("/api/connect")
async def set_connection(req: ClusterConnectRequest):
    """设置算力平台连接（与大模型配置相互独立，合并保存不清空 LLM 字段）。"""
    global _connection_config
    base = _connection_config or ConnectionConfig()
    _connection_config = base.model_copy(update=req.model_dump())
    _save_config(_connection_config)
    return {"status": "configured", "mode": _connection_config.mode}


@app.post("/api/llm/config")
async def set_llm_config(req: LLMConfigRequest):
    """单独设置大模型 API（与集群连接相互独立）。

    保存后立即返回；RAG 重建（调 embedding API + 构建 FAISS 索引，可能较慢）
    放到后台任务异步执行，避免阻塞本次配置请求。embedding 与大模型共用同一套
    API 凭证，所以配置变化后需要重建，否则「先连集群、后配大模型」会让 RAG
    一直拿不到 key。
    """
    global _connection_config
    base = _connection_config or ConnectionConfig()
    # 只覆盖非空字段，避免用空值抹掉已有配置
    update = {k: v for k, v in req.model_dump().items() if v != ""}
    _connection_config = base.model_copy(update=update)
    _save_config(_connection_config)

    # 后台异步重建 RAG，不阻塞响应
    rag_enabled = bool(_rag_config.get("enabled", False))
    if rag_enabled:
        asyncio.create_task(_init_rag())

    return {
        "status": "configured",
        "llm_model": _connection_config.llm_model,
        "rag_ready": _retriever.is_ready if _retriever else False,
        "rag_rebuilding": rag_enabled,
    }


# ─── 后台作业监控 ────────────────────────────────────────────

# 需要告警的作业失败终态
_FAILURE_STATES = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}


def _parse_job_log_paths(job_info: str, job_id: str) -> list[str]:
    """从 ``scontrol show job -o`` 中提取并规范化 stdout/stderr 路径。

    Slurm 通常会返回绝对路径；若返回相对路径，则以 WorkDir 为基准。
    同一个文件同时作为 stdout 和 stderr 时只读取一次。
    """
    workdir_match = re.search(r"(?:^|\s)WorkDir=(\S+)", job_info or "")
    workdir = workdir_match.group(1) if workdir_match else ""
    paths: list[str] = []

    for field in ("StdErr", "StdOut"):
        match = re.search(rf"(?:^|\s){field}=(\S+)", job_info or "")
        if not match:
            continue
        path = match.group(1).strip()
        if not path or path in {"(null)", "None", "N/A"}:
            continue

        # scontrol 一般已经展开这些占位符；保留替换可兼容精简版控制器。
        path = path.replace("%j", job_id).replace("%A", job_id)
        if not path.startswith("/") and workdir and workdir.startswith("/"):
            path = posixpath.join(workdir, path)
        if path not in paths:
            paths.append(path)

    return paths


async def _discover_job_log_paths(
    poller: WebShellExecutor, job_id: str
) -> list[str]:
    """查询一次作业详情，在作业被控制器清理前缓存日志位置。"""
    job_info = await poller.execute_agent_command(
        f"scontrol show job {job_id} -o 2>/dev/null"
    )
    return _parse_job_log_paths(job_info, job_id)


async def _read_job_logs(
    poller: WebShellExecutor, paths: list[str]
) -> str:
    """读取作业末尾日志，供规则引擎做失败根因诊断。"""
    sections: list[str] = []
    for path in paths:
        quoted = shlex.quote(path)
        output = await poller.execute_agent_command(
            f"if [ -f {quoted} ]; then tail -n 240 -- {quoted}; fi"
        )
        cleaned = _clean_workflow_output(output)
        if cleaned and cleaned != "(命令执行成功，无输出)":
            sections.append(f"[{path}]\n{cleaned}")
    return "\n".join(sections)


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
    job_log_paths: dict[str, list[str]] = {}  # 作业运行期间缓存 stdout/stderr
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
            # 队列空闲时降低轮询频率，减少额外 SCOW 会话给登录节点造成的压力；
            # 有活动作业时再恢复到 2 秒，以便及时捕获短暂的失败终态。
            has_active_jobs = any(
                state in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING"}
                for state in known_states.values()
            )
            await asyncio.sleep(2 if has_active_jobs else 10)
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

                # 作业结束后可能很快从 scontrol 中消失，因此第一次看到它时就
                # 缓存 StdOut/StdErr，而不是等失败后才开始找日志文件。
                if job_id not in job_log_paths:
                    try:
                        paths = await _discover_job_log_paths(poller, job_id)
                        if paths:
                            job_log_paths[job_id] = paths
                            logger.info("已缓存作业 %s 日志路径: %s", job_id, paths)
                    except Exception as e:
                        logger.debug("读取作业 %s 日志路径失败: %s", job_id, e)

                # 作业进入失败终态且尚未告警 → 告警。
                # 启动基线已把旧的失败作业标为已告警，故这里无需再要求
                # "必须先见过正常态"——即使第一次看到作业它就已是失败态
                # （快速失败或监控重启后才出现），也不会漏报。
                if state in _FAILURE_STATES and job_id not in alerted:
                    alerted.add(job_id)
                    logger.info(
                        "检测到作业 %s 失败: %s -> %s", job_id, prev, state
                    )

                    # 失败后先读取 stdout/stderr。具体日志错误优先于笼统的
                    # FAILED/NonZeroExitCode；日志不可用时才回退到结构化状态。
                    paths = job_log_paths.get(job_id, [])
                    if not paths:
                        try:
                            paths = await _discover_job_log_paths(poller, job_id)
                            if paths:
                                job_log_paths[job_id] = paths
                        except Exception:
                            paths = []

                    job_log = ""
                    if paths:
                        # 给计算节点和共享文件系统短暂的日志落盘时间。
                        for attempt in range(3):
                            job_log = await _read_job_logs(poller, paths)
                            if job_log:
                                break
                            if attempt < 2:
                                await asyncio.sleep(0.5)

                    if job_log:
                        logger.info(
                            "已读取作业 %s 失败日志（%d 字符），开始具体诊断",
                            job_id,
                            len(job_log),
                        )
                    else:
                        logger.warning(
                            "作业 %s 失败，但未能读取 stdout/stderr，将使用状态兜底诊断",
                            job_id,
                        )

                    state_reason = "NonZeroExitCode" if state == "FAILED" else ""
                    diagnosis = engine.diagnose_job(
                        state=state,
                        state_reason=state_reason,
                        log_text=job_log,
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
    global _active_executor
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

    # 告诉前端终端模式：webshell/ssh 都是真实终端（远程回显 + 真实提示符），
    # 其他（rest）是命令模式（本地回显）。前端只认 "webshell"/"command" 两种，
    # 所以交互式 SSH 也上报 "webshell"，以复用远程回显的渲染逻辑。
    is_interactive = _connection_config.mode in ("webshell", "ssh")
    await ws.send_json({
        "type": "terminal_mode",
        "data": "webshell" if is_interactive else "command",
    })

    is_webshell = _connection_config.mode == "webshell"

    if is_interactive:
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
            logger.warning(
                "[%s 模式] 初始连接失败: %s: %s",
                _connection_config.mode, type(e).__name__, e,
            )

        if initial.strip():
            _active_executor = executor
            await ws.send_json({
                "type": "output",
                "data": f"✅ 已连接到算力中心（{_connection_config.mode} 模式）\n",
            })
            await ws.send_json({"type": "output", "data": initial})
        else:
            detail = f"：{conn_error}" if conn_error else ""
            logger.warning(
                "[%s 模式] 交互式连接后未获取到提示符 %s",
                _connection_config.mode, detail,
            )
            await ws.send_json({
                "type": "output",
                "data": f"❌ 连接到算力中心失败（{_connection_config.mode} 模式）{detail}\n",
            })
            if _connection_config.mode == "ssh":
                hint = (
                    "\x1b[31m[未能获取到 shell 提示符]\x1b[0m\r\n"
                    "\x1b[33m[SSH 请检查：私钥 passphrase（填在「密码」框）是否正确、"
                    "动态验证码是否新鲜未过期（30 秒内），然后点「设置」重填后重连]\x1b[0m\r\n"
                )
            else:
                hint = (
                    "\x1b[31m[未能获取到 shell 提示符]\x1b[0m\r\n"
                    "\x1b[33m[最常见原因是登录凭证（Cookie）过期，请点右上角「设置」重新填写最新 Cookie 后重连]\x1b[0m\r\n"
                )
            await ws.send_json({"type": "output", "data": hint})
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
                if is_interactive:
                    try:
                        async for chunk in executor.execute_stream(""):
                            async with send_lock:
                                await ws.send_json({"type": "output", "data": chunk})
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        logger.warning("重打提示符失败: %s", e)
                continue

            # 多行粘贴：将整块文本作为一条命令发送给远程 shell
            # 远程 PTY 会一次性收到所有字符，bash 能正确处理 heredoc 等多行语法
            if message.get("type") == "paste":
                paste_text = message.get("data", "")
                if paste_text.strip():
                    try:
                        paste_output = ""
                        async for chunk in executor.execute_stream(paste_text):
                            paste_output += chunk
                            async with send_lock:
                                await ws.send_json({"type": "output", "data": chunk})
                        # 粘贴执行完后通知 Agent 监听
                        if paste_output:
                            diagnosis = monitor.feed(paste_output)
                            if diagnosis:
                                async with send_lock:
                                    await ws.send_json({
                                        "type": "agent_alert",
                                        "data": {
                                            "error_type": diagnosis.error_type.value,
                                            "root_cause": diagnosis.root_cause_brief,
                                            "confidence": diagnosis.confidence,
                                            "evidence": diagnosis.evidence,
                                            "suggested_commands": diagnosis.suggested_commands,
                                        },
                                    })
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        logger.warning("粘贴执行失败: %s", e)
                continue

            command = message.get("command", "")

            if not command.strip():
                continue

            # 回显命令文本：
            # - 交互式（webshell/ssh）按钮触发（echo=true）：真实提示符已在屏幕上，
            #   只补命令文本即可，否则会多出一个 $（变成 "~$ $ cat ..."）
            # - 命令模式（非交互式）：无真实 shell 回显，由后端补完整 "$ 命令"
            # - 交互式用户键入：前端 xterm 已本地回显，后端不补
            if message.get("echo") and is_interactive:
                await ws.send_json({
                    "type": "output",
                    "data": f"{command}\r\n",
                })
            elif not is_interactive:
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

            # 交互式（webshell/ssh）远程 shell 自带提示符，不需要额外发
            if not is_interactive:
                await ws.send_json({"type": "prompt", "data": ""})

    except WebSocketDisconnect:
        pass
    finally:
        # 终端断开，清空全局 executor 引用（agent loop 降级为纯对话）
        _active_executor = None
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
- 文档记录的分区与QoS配对：P107-A100 → qos_p107-a100；P107-RTX5090 → qos_p107-rtx5090；Students → qos_stu_default
- 文档记录的比赛QoS单作业上限：qos_p107-a100 为CPU 16、GPU 2；qos_p107-rtx5090 为CPU 16、GPU 4。用户实际权限仍应通过 sacctmgr show assoc 核实
- 可用 module：cuda/13.0、python3.12、miniconda/py312
- 本集群未接入 slurmdbd，sacct 命令不可用；查看作业历史输出请用 cat slurm-<作业号>.out
- 提交作业：sbatch 脚本.sh；查询作业：squeue -u $USER；取消作业：scancel <作业号>

【回答规则】
- 给出可以直接复制粘贴的命令，命令必须逐字准确（例如 sbatch 绝不能写成 sbach）
- 不确定的信息要诚实说明，不要编造分区名、路径或 API
- 不要假设或举例编造某个节点有几张GPU；未从文档或平台查询得到的硬件数量必须明确说“不确定”，并给出查询方法
- 不要建议危险操作（rm -rf、改系统文件等）
- 遇到“刚才的作业”“这个输出文件”“上面的结果”等指代时，必须结合对话历史中的最近模板、作业号和文件路径回答
- 如果最近模板已经结束，不要再说“等它跑完”；用户想看文件时，应明确文件路径并给出对应的 cat/head/tail 命令

【生成作业脚本时】
当用户请求生成/写一个作业脚本，请输出一个完整可用的 bash 脚本，放在 ```bash 代码块中。
脚本必须以 #!/bin/bash 开头，紧跟 #SBATCH 注释行，并至少包含：
  #SBATCH --partition=<分区>      （默认 P107-A100）
  #SBATCH --qos=qos_p107-a100
  #SBATCH --job-name=<任务名>
  #SBATCH --output=slurm-%j.out
  #SBATCH --time=HH:MM:SS         （时间格式必须是 时:分:秒）
  #SBATCH --gres=gpu:<数量>        （需要 GPU 时）
然后是 module load 行（如 module load cuda/13.0 python3.12）和实际运行命令。
脚本前后用一两句中文说明用法（如何保存为 .sh 文件并 sbatch 提交）。
如果用户明确要求“只输出脚本”，则不要添加代码块以外的说明文字。
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
    last_template_context = ""

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
            request_id = str(message.get("request_id", ""))

            if msg_type == "user_message":
                # 用户主动提问
                if not llm.is_configured:
                    await ws.send_json({
                        "type": "reply",
                        "data": "⚠️ 大模型未配置。请在连接设置中填写 API 地址和 Key。\n不过左边的终端诊断功能仍然可用！",
                        "request_id": request_id,
                    })
                    continue

                # WebSocket 重连后服务端会话内存会重置。前端随消息带回最近
                # 对话和模板上下文，只在本连接尚无历史时恢复，避免重复追加。
                client_history = message.get("history", [])
                if not history and isinstance(client_history, list):
                    for item in client_history[-8:]:
                        if not isinstance(item, dict):
                            continue
                        role = item.get("role")
                        item_content = str(item.get("content", ""))[:3000]
                        if role in ("user", "assistant") and item_content:
                            history.append({"role": role, "content": item_content})
                client_template = message.get("template_context")
                if isinstance(client_template, dict) and client_template.get("title"):
                    last_template_context = (
                        f"最近运行的模板：{client_template.get('title')}\n"
                        f"作业号：{client_template.get('job_id') or '未取得'}\n"
                        f"状态：{'成功' if client_template.get('success') else '失败或未确认'}\n"
                        f"工作目录：{client_template.get('work_dir') or '未知'}\n"
                        f"输出文件：{', '.join(client_template.get('artifacts') or [])}\n"
                        f"运行结论：{str(client_template.get('summary') or '')[:2000]}"
                    )

                # 发送"正在思考"状态
                await ws.send_json({"type": "typing", "data": "", "request_id": request_id})

                # ── 短消息 / 闲聊快速路径：跳过 RAG（省 1-3s） ──
                # RAG 每条消息都要 embedding + FAISS 检索，对"你好/谢谢/sbatch 是什么"
                # 这类短问题是纯浪费。判定条件：去空格后 <12 字，或匹配常见寒暄模式。
                import re as _re
                _trimmed = content.strip()
                _chatty_pattern = _re.compile(
                    r"^(你好|您好|hi|hello|hey|哈喽|嗨|在吗|谢谢|多谢|thanks|thank you|"
                    r"好的|ok|okay|嗯+|哦+|再见|拜拜|bye|测试|test)[\s\.,!！。~～]*$",
                    _re.IGNORECASE,
                )
                _skip_rag = len(_trimmed) < 12 or bool(_chatty_pattern.match(_trimmed))

                # RAG 检索：从知识库获取相关上下文（短消息跳过 / 全局关闭跳过）
                # 通过环境变量 RAG_ENABLED=0 或 false 可全局关闭 RAG，用于对比测试速度/质量。
                rag_context = ""
                system_prompt = CHAT_SYSTEM_PROMPT
                _rag_enabled = os.getenv("RAG_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
                if not _rag_enabled:
                    logger.info("RAG 已全局关闭（RAG_ENABLED=%s），跳过检索", os.getenv("RAG_ENABLED"))
                elif _skip_rag:
                    logger.info("短消息/闲聊（%d 字），跳过 RAG 检索以加速", len(_trimmed))
                elif _retriever and _retriever.is_ready:
                    try:
                        rag_context = await _retriever.retrieve(content)
                        if rag_context:
                            system_prompt = build_rag_prompt(CHAT_SYSTEM_PROMPT, rag_context)
                            logger.info("RAG 检索成功，注入上下文到 system prompt")
                    except Exception as e:
                        logger.warning("RAG 检索失败，使用原始 prompt: %s", e)

                if last_template_context and _re.search(
                    r"(刚才|刚刚|上面|之前|这个|那个|它|输出文件|结果文件|运行结果|作业结果)",
                    content,
                ):
                    system_prompt += (
                        "\n\n【最近一次模板运行上下文——用于理解当前指代】\n"
                        + last_template_context
                    )

                # ── 两级路由：闲聊 / HPC 对话 ──────────────────────────
                # 闲聊走极简提示词；知识问答可注入 RAG。实际集群操作由可审计的
                # 作业模板完成，不让普通聊天在用户不知情时执行命令。
                import time as _time
                import re as _re2

                _action_verbs = _re2.compile(
                    r"(帮我|帮忙|请帮|给我|替我|查一下|查看|查询|查|看看|看下|看一下|"
                    r"跑一下|跑个|跑|运行|执行|提交|部署|安装|加载|创建|新建|写入|写个|写|"
                    r"读取|下载|上传|取消|杀掉|停止|重启|分析|诊断|排查|测试|验证|生成)"
                )
                _hpc_keywords = _re2.compile(
                    r"(sbatch|squeue|scancel|sinfo|scontrol|srun|salloc|作业|任务|集群|"
                    r"分区|节点|队列|脚本|SBATCH|slurm|Slurm|SLURM|"
                    r"GPU|CUDA|cuda|A100|RTX|显卡|训练|推理|模型|"
                    r"module|conda|环境|依赖|报错|失败|错误|异常|日志|"
                    r"文件|目录|路径|/home|/data|/mnt)"
                , _re2.IGNORECASE)

                # 判定：短寒暄（跳过 RAG 那些）且不含动作词/HPC 关键词 → 纯闲聊
                # 其余消息走 HPC 对话；是否为知识问答决定是否使用知识库提示词。
                # 其他（"什么是 sbatch"、"解释一下 squeue" 等知识问答）→ 走轻量 chat_stream
                _has_action = bool(_action_verbs.search(content))
                _has_hpc_kw = bool(_hpc_keywords.search(content))
                _is_pure_chitchat = _skip_rag and not _has_action and not _has_hpc_kw
                _is_knowledge_qa = (not _has_action) and (not _is_pure_chitchat)

                # 极简闲聊 prompt：让模型活泼但不铺陈
                _CHITCHAT_PROMPT = (
                    "你是 HPC Copilot，面向算力平台初学者的智能体助手，"
                    "性格活泼亲切，像一位热心的学长/学姐。"
                    "当前是闲聊/寒暄场景，请用 1-3 句自然口语化回复，"
                    "可以适当用 emoji（如 😊👍🎉）点缀情绪，"
                    "但不要介绍自己能做什么、不要罗列功能清单。"
                )
                # 知识问答 prompt：保留平台事实，去掉 agent 工具规则，允许适度展开
                _KNOWLEDGE_PROMPT = system_prompt + (
                    "\n\n【当前是知识问答场景】用户在问概念/命令用法，"
                    "不需要操作集群，直接用通俗中文解释即可。"
                    "语气活泼亲切，可以用 emoji 点缀（如 💡⚠️✅），"
                    "回答控制在 400 字以内，鼓励用一个代码块举例说明。"
                )

                try:
                    _t0 = _time.perf_counter()
                    _ttft_logged = False

                    if _is_pure_chitchat:
                        # ── 路径 1：纯闲聊（"你好/谢谢/在吗"） ──
                        # 无 tools、极简 prompt、temperature 高一点更活泼
                        logger.info("[路由] 闲聊快速路径（%d 字）", len(content.strip()))
                        full_response = ""
                        async for chunk in llm.chat_stream(
                            system_prompt=_CHITCHAT_PROMPT,
                            user_message=content,
                            history=history[-4:],  # 闲聊只带最近 2 轮上下文
                            temperature=0.9,
                            max_tokens=400,
                        ):
                            if not _ttft_logged:
                                logger.info(
                                    "[TTFT] 闲聊首字延迟: %.2fs",
                                    _time.perf_counter() - _t0,
                                )
                                _ttft_logged = True
                            full_response += chunk
                            await ws.send_json({"type": "stream_chunk", "data": chunk, "request_id": request_id})
                        await ws.send_json({"type": "stream_end", "data": "", "request_id": request_id})

                    else:
                        # ── 路径 2：HPC 知识问答或操作指导 ──
                        logger.info(
                            "[路由] HPC 对话路径（knowledge_qa=%s, has_action=%s）",
                            _is_knowledge_qa, _has_action,
                        )
                        full_response = ""
                        async for chunk in llm.chat_stream(
                            system_prompt=_KNOWLEDGE_PROMPT if _is_knowledge_qa else system_prompt,
                            user_message=content,
                            history=history,
                            temperature=0.7,
                            max_tokens=1500,
                        ):
                            if not _ttft_logged:
                                logger.info(
                                    "[TTFT] 知识问答首字延迟: %.2fs",
                                    _time.perf_counter() - _t0,
                                )
                                _ttft_logged = True
                            full_response += chunk
                            await ws.send_json({"type": "stream_chunk", "data": chunk, "request_id": request_id})
                        await ws.send_json({"type": "stream_end", "data": "", "request_id": request_id})

                    logger.info(
                        "[完成] 总耗时 %.2fs, 回复长度 %d 字",
                        _time.perf_counter() - _t0, len(full_response),
                    )

                    # 记录本轮对话到历史（保留最近 10 条，控制 token 消耗）
                    history.append({"role": "user", "content": content})
                    history.append({"role": "assistant", "content": full_response})
                    if len(history) > 10:
                        del history[: len(history) - 10]
                except WebSocketDisconnect:
                    # 客户端中途断开：让外层 while/except 处理，避免往关闭的 socket 上再发东西
                    raise
                except Exception as e:
                    logger.error("Chat 处理失败: %s: %s", type(e).__name__, e)
                    # 防御性发送：如果 socket 已经关闭（比如网络异常导致 disconnect
                    # 但没被识别为 WebSocketDisconnect），忽略二次错误
                    try:
                        await ws.send_json({
                            "type": "reply",
                            "data": f"⚠️ 大模型调用失败: {e}\n请稍后重试或检查 API 配置。",
                            "request_id": request_id,
                        })
                    except Exception:
                        pass

            elif msg_type == "agent_diagnosis":
                # Agent 检测到错误，调用 LLM 生成通俗解释
                diagnosis_data = content
                if llm.is_configured:
                    await ws.send_json({"type": "typing", "data": "", "request_id": request_id})
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
                            await ws.send_json({"type": "stream_chunk", "data": chunk, "request_id": request_id})
                        await ws.send_json({"type": "stream_end", "data": "", "request_id": request_id})
                    except WebSocketDisconnect:
                        # 客户端断开：冒泡到外层，别再往关闭的 socket 上发东西
                        raise
                    except Exception:
                        # LLM 失败时回退到规则引擎的简要信息
                        fallback = (
                            f"🔍 检测到错误：{diagnosis_data.get('error_type', '未知')}\n"
                            f"原因：{diagnosis_data.get('root_cause', '分析中...')}\n"
                            f"建议命令：{', '.join(diagnosis_data.get('suggested_commands', []))}"
                        )
                        try:
                            await ws.send_json({
                                "type": "agent_explanation",
                                "data": fallback,
                                "request_id": request_id,
                            })
                        except Exception:
                            pass

            elif msg_type == "run_template":
                # ── 预设模板工作流：按阶段顺序执行，实时推送进度 ──
                from .agent.templates import get_template
                template_id = content if isinstance(content, str) else content.get("id", "")
                template_request = (
                    ""
                    if isinstance(content, str)
                    else str(content.get("request", "")).strip()
                )
                template_id = str(template_id).strip()
                template = get_template(template_id)
                if not template:
                    logger.warning("[模板] 前端请求了未注册的模板: %r", template_id)
                    await ws.send_json({
                        "type": "reply",
                        "data": "⚠️ 当前服务还不支持这个工作流，请刷新页面；如果仍然出现，请重启后端服务。",
                    })
                    continue

                if _active_executor is None:
                    await ws.send_json({
                        "type": "reply",
                        "data": "⚠️ 终端未连接，无法执行模板。请先在左侧连接集群。",
                    })
                    continue

                logger.info("[模板] 开始执行: %s (%d 阶段)", template.title, len(template.stages))
                history.append({
                    "role": "user",
                    "content": template_request or f"请运行作业模板：{template.title}",
                })
                # 推送模板开始事件（前端创建进度卡片）
                await ws.send_json({
                    "type": "template_start",
                    "data": {
                        "template_id": template.id,
                        "title": template.title,
                        "icon": template.icon,
                        "stages": [s.label for s in template.stages],
                        "overview": template.overview,
                        "conditions": template.conditions,
                        "models": template.models,
                        "outputs": template.outputs,
                        "work_dir": template.work_dir,
                        "artifacts": template.artifacts,
                    },
                })

                # 阶段输出变量池（供后续阶段引用，如 {job_output}）
                stage_vars: dict[str, str] = {}
                template_failed = False
                job_needs_log_verification = False
                poll_stage_index: Optional[int] = None
                template_summary_text = ""
                template_started = asyncio.get_running_loop().time()

                for idx, stage in enumerate(template.stages):
                    running_detail = {
                        "write_file": f"正在集群中准备 {stage.params.get('path', '作业文件')}",
                        "run_command": "正在执行命令并等待集群返回",
                        "poll_jobs": "正在查询 Slurm 队列",
                        "llm_summarize": "正在整理本次运行结果",
                    }.get(stage.action, "正在处理")
                    # 推送阶段开始
                    await ws.send_json({
                        "type": "template_stage",
                        "data": {
                            "index": idx,
                            "label": stage.label,
                            "status": "running",
                            "detail": running_detail,
                        },
                    })

                    stage_failed = False
                    try:
                        result = ""
                        if stage.action == "write_file":
                            path = stage.params["path"]
                            file_content = stage.params["content"]
                            # 通过 executor 写文件（mkdir -p + cat heredoc）
                            dir_path = path.rsplit("/", 1)[0] if "/" in path else "."
                            escaped = file_content.replace("'", "'\\''")
                            cmd = f"mkdir -p {dir_path} && printf '%s' '{escaped}' > {path}"
                            result = await _active_executor.execute_agent_command(cmd)
                            if "error" in result.lower() and "mkdir" not in result.lower():
                                # 尝试用 tee 方式（某些 shell 对长 heredoc 有问题）
                                cmd2 = f"mkdir -p {dir_path} && cat > {path} << 'HPCEOF'\n{file_content}\nHPCEOF"
                                result = await _active_executor.execute_agent_command(cmd2)
                            if result == "(命令执行成功，无输出)":
                                result = f"已写入 {path}"

                        elif stage.action == "run_command":
                            cmd = stage.params["command"]
                            # 替换变量引用
                            for var_name, var_val in stage_vars.items():
                                cmd = cmd.replace(f"{{{var_name}}}", var_val)
                            result = await _active_executor.execute_agent_command(cmd)

                        elif stage.action == "poll_jobs":
                            import re as _re_poll

                            interval = stage.params.get("interval", 5)
                            timeout = stage.params.get("timeout", 180)
                            # 从之前的输出提取 job id
                            job_id = ""
                            extract_from = stage.params.get("extract_job_id_from", "")
                            if extract_from and extract_from in stage_vars:
                                m = _re_poll.search(r"(\d{4,})", stage_vars[extract_from])
                                if m:
                                    job_id = m.group(1)
                                    stage_vars["job_id"] = job_id
                            if not job_id:
                                raise RuntimeError("未从 sbatch 返回信息中识别到作业号，请检查提交输出")
                            await ws.send_json({
                                "type": "template_stage",
                                "data": {
                                    "index": idx,
                                    "label": stage.label,
                                    "status": "running",
                                    "detail": f"已提交作业 #{job_id}，正在等待调度",
                                },
                            })
                            # 轮询直到终态或超时
                            import time as _time_poll
                            deadline = _time_poll.time() + timeout
                            last_state = "UNKNOWN"
                            poll_count = 0
                            while _time_poll.time() < deadline:
                                poll_count += 1
                                if job_id:
                                    poll_cmd = f"squeue -j {job_id} -h -o '%T' 2>/dev/null"
                                else:
                                    poll_cmd = "squeue -u $USER -h -o '%i %T' 2>/dev/null | head -5"
                                poll_result = await _active_executor.execute_agent_command(poll_cmd)
                                # squeue 的结果来自真实终端，可能夹带 bracketed-paste、
                                # OSC 标题和彩色提示符；清理后才能解析并发给普通文本卡片。
                                poll_stripped = _clean_workflow_output(poll_result)
                                no_output = (
                                    not poll_stripped
                                    or poll_stripped == "(命令执行成功，无输出)"
                                )
                                if no_output:
                                    # 离开 squeue 只代表作业已结束，不能直接推断成功。
                                    # 优先从 accounting 读取最终状态，再回退到控制器。
                                    if job_id:
                                        accounting = await _active_executor.execute_agent_command(
                                            f"sacct -X -j {job_id} -n -P -o State,ExitCode 2>/dev/null | head -1"
                                        )
                                        accounting_clean = _clean_workflow_output(accounting).upper()
                                        state_match = _re_poll.search(
                                            r"\b(COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|"
                                            r"NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE)\b",
                                            accounting_clean,
                                        )
                                        if state_match:
                                            last_state = state_match.group(1)
                                        else:
                                            final_info = await _active_executor.execute_agent_command(
                                                f"scontrol show job {job_id} -o 2>/dev/null"
                                            )
                                            state_match = _re_poll.search(
                                                r"\bJobState=([A-Z_]+)", final_info.upper()
                                            )
                                            last_state = (
                                                state_match.group(1)
                                                if state_match
                                                else "FINISHED_UNVERIFIED"
                                            )
                                        # 某些教学集群关闭了 accounting，且作业结束后会立即
                                        # 清理 scontrol 记录。此时读取模板脚本自行写入的退出码。
                                        if last_state == "FINISHED_UNVERIFIED":
                                            status_file = stage.params.get("status_file", "")
                                            if status_file:
                                                status_file = status_file.replace("{job_id}", job_id)
                                                exit_text = await _active_executor.execute_agent_command(
                                                    f"cat {status_file} 2>/dev/null"
                                                )
                                                exit_clean = _clean_workflow_output(exit_text).strip()
                                                exit_match = _re_poll.search(r"(?:^|\n)(\d+)(?:\n|$)", exit_clean)
                                                if exit_match:
                                                    last_state = (
                                                        "COMPLETED"
                                                        if exit_match.group(1) == "0"
                                                        else "FAILED"
                                                    )
                                    else:
                                        last_state = "QUEUE_EMPTY_UNVERIFIED"
                                    break
                                last_state = poll_stripped
                                if any(s in poll_stripped.upper() for s in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT")):
                                    break
                                # 推送轮询中间状态
                                await ws.send_json({
                                    "type": "template_stage",
                                    "data": {"index": idx, "label": stage.label,
                                             "status": "running",
                                             "detail": f"作业 #{job_id}：{poll_stripped} · 已检查 {poll_count} 次"},
                                })
                                await asyncio.sleep(interval)
                            result = f"最终状态: {last_state}"
                            if last_state.upper() == "FINISHED_UNVERIFIED":
                                result += "\n调度记录暂不可用，将继续读取本次日志核验运行结果"
                                job_needs_log_verification = True
                                poll_stage_index = idx
                            elif last_state.upper() not in ("COMPLETED", ""):
                                result += f"\n⚠️ 作业可能异常终止（{last_state}）"
                                # 流程仍继续读取日志并生成解释，但最终卡片必须显示失败，
                                # 不能把“工作流走完了”误报成“计算成功了”。
                                stage_failed = True
                                template_failed = True

                        elif stage.action == "llm_summarize":
                            # 把前面所有输出交给 LLM 做智能总结
                            prompt_tpl = stage.params.get("prompt", "请总结以上输出。")
                            for var_name, var_val in stage_vars.items():
                                prompt_tpl = prompt_tpl.replace(f"{{{var_name}}}", var_val[:3000])
                            # 流式输出总结
                            await ws.send_json({
                                "type": "template_stage",
                                "data": {"index": idx, "label": stage.label, "status": "running",
                                         "detail": "AI 正在阅读作业输出并生成结论"},
                            })
                            summary_parts = []
                            async for chunk in llm.chat_stream(
                                system_prompt=CHAT_SYSTEM_PROMPT,
                                user_message=prompt_tpl,
                                temperature=0.7,
                                max_tokens=1500,
                            ):
                                summary_parts.append(chunk)
                                await ws.send_json({"type": "template_summary_chunk", "data": chunk})
                            result = "".join(summary_parts)
                            template_summary_text = result
                            await ws.send_json({"type": "template_summary_end", "data": ""})

                        else:
                            result = f"未知阶段类型: {stage.action}"

                        # 对没有 accounting/scontrol 历史记录的平台，用模板日志中的
                        # 确定性成功标记完成二次核验，避免把成功误报成失败，也避免仅凭
                        # LLM 文本判断状态。
                        if stage.output_var == "job_output" and job_needs_log_verification:
                            clean_result = _clean_workflow_output(result)
                            success_markers = stage.params.get("success_markers", [])
                            log_verified = bool(success_markers) and all(
                                marker in clean_result for marker in success_markers
                            )
                            if log_verified:
                                job_needs_log_verification = False
                                stage_vars["job_status"] = "最终状态: COMPLETED（由退出日志核验）"
                                if poll_stage_index is not None:
                                    await ws.send_json({
                                        "type": "template_stage",
                                        "data": {
                                            "index": poll_stage_index,
                                            "status": "done",
                                            "detail": f"作业 #{stage_vars.get('job_id', '')}：日志已核验成功",
                                            "output": "最终状态: COMPLETED（由日志成功标记核验）",
                                        },
                                    })
                            else:
                                job_needs_log_verification = False
                                stage_failed = True
                                template_failed = True

                        # 保存输出变量
                        if stage.output_var:
                            stage_vars[stage.output_var] = _clean_workflow_output(result)

                        display_output = _clean_workflow_output(result)
                        if stage.action == "write_file":
                            done_detail = f"文件已准备：{stage.params.get('path', '')}"
                        elif stage.action == "poll_jobs":
                            done_detail = f"作业 #{stage_vars.get('job_id', '')}：{display_output.splitlines()[0] if display_output else '状态已确认'}"
                        elif stage.action == "llm_summarize":
                            done_detail = "运行结论已生成"
                        elif stage.output_var == "submit_output":
                            submitted_id = stage_vars.get("job_id") or next(
                                iter(re.findall(r"\d{4,}", display_output)), ""
                            )
                            done_detail = f"Slurm 已接收作业{f' #{submitted_id}' if submitted_id else ''}"
                        elif stage.output_var == "job_output":
                            done_detail = f"已读取 {len(display_output.splitlines())} 行本次作业输出"
                        else:
                            done_detail = "已完成"

                        # 推送阶段完成
                        await ws.send_json({
                            "type": "template_stage",
                            "data": {"index": idx, "label": stage.label,
                                     "status": "error" if stage_failed else "done",
                                     "detail": done_detail,
                                     "output": display_output[:4000]},
                        })

                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        logger.error("[模板] 阶段 %d (%s) 失败: %s: %s",
                                     idx, stage.label, type(e).__name__, e)
                        await ws.send_json({
                            "type": "template_stage",
                            "data": {"index": idx, "label": stage.label, "status": "error",
                                     "output": f"{type(e).__name__}: {e}"},
                        })
                        template_failed = True
                        break  # 某阶段失败则中止后续

                # 把真实运行信息写回对话历史，后续“这个输出文件”等追问才能
                # 对应到明确的作业和路径，而不是退化成无上下文闲聊。
                completed_job_id = stage_vars.get("job_id", "")
                resolved_artifacts = [
                    path.replace("{job_id}", completed_job_id)
                    for path in template.artifacts
                ]
                last_template_context = (
                    f"最近运行的模板：{template.title}\n"
                    f"计算内容：{template.overview}\n"
                    f"作业号：{completed_job_id or '未取得'}\n"
                    f"状态：{'成功' if not template_failed else '失败'}\n"
                    f"工作目录：{template.work_dir}\n"
                    f"输出文件：{', '.join(resolved_artifacts)}\n"
                    f"运行结论：{template_summary_text[:2000]}\n"
                    f"日志摘录：{stage_vars.get('job_output', '')[:2000]}"
                )
                history.append({"role": "assistant", "content": last_template_context})
                if len(history) > 10:
                    del history[: len(history) - 10]

                # 推送模板结束
                await ws.send_json({
                    "type": "template_end",
                    "data": {
                        "template_id": template.id,
                        "success": not template_failed,
                        "job_id": stage_vars.get("job_id", ""),
                        "job_output": stage_vars.get("job_output", "")[:4000],
                        "work_dir": template.work_dir,
                        "artifacts": resolved_artifacts,
                        "duration_seconds": round(
                            asyncio.get_running_loop().time() - template_started, 1
                        ),
                    },
                })
                logger.info("[模板] %s 执行%s", template.title, "失败" if template_failed else "完成")

    except WebSocketDisconnect as e:
        # code 参考 RFC 6455：
        # 1000=正常关闭, 1001=going away(浏览器切页/关标签),
        # 1006=异常断开(无 close frame，通常是代理/网络层丢包),
        # 1011=服务端异常, 1012/1013=服务重启/临时过载
        logger.info(
            "chat_ws 客户端断开: code=%s, reason=%r",
            getattr(e, "code", "?"), getattr(e, "reason", ""),
        )
    except Exception as e:
        logger.error(
            "chat_ws 未预期异常: %s: %s", type(e).__name__, e, exc_info=True,
        )
        try:
            await ws.close(code=1011)
        except Exception:
            pass
    finally:
        # 一个聊天连接复用一个 HTTP 连接池，并在连接结束时统一释放。
        await llm.aclose()


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
            key_content=_connection_config.ssh_key,
            totp=_connection_config.ssh_totp,
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


# ─── 前端静态文件托管（生产部署用）──────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 前端构建后的目录（相对于项目根目录）
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    # 挂载静态资源目录（assets/）
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """托管前端 SPA：所有未匹配的路由都返回 index.html"""
        # 尝试返回具体文件（如 favicon.ico）
        file_path = _FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # 否则返回 index.html（SPA 路由）
        return FileResponse(_FRONTEND_DIST / "index.html")
