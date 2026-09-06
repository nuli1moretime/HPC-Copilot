"""SCOW Web Shell 执行器。

通过 WebSocket 连接 SCOW 平台的 Web Shell 服务，实现完整的终端交互。
适用于集群不开放 SSH 直连、但提供 SCOW Web Shell 的场景。

协议（来自 OpenSCOW 源码）：
- WebSocket URL: wss://{host}/api/shell?cluster={cluster}&loginNode={node}&cols=80&rows=30
- 认证：通过 Cookie 头传递 SCOW 登录 session
- 发送消息：{"$case": "data", "data": {"data": "命令文本"}}
- 接收消息：{"$case": "data", "data": {"data": "输出文本"}} 或
            {"$case": "exit", "exit": {"code": 0}}
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from typing import AsyncGenerator
from urllib.parse import urlencode

import websockets

from .base import BaseExecutor


_CSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_ESCAPE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _plain_command_output(value: str) -> str:
    """把终端协议输出转换成适合普通文本界面展示的内容。"""
    cleaned = _OSC_ESCAPE_RE.sub("", value or "")
    cleaned = _CSI_ESCAPE_RE.sub("", cleaned).replace("\r", "")
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        # SCOW 会把 shell 提示符一并返回；它不属于命令结果。
        if "@" in stripped and (stripped.endswith("$") or stripped.endswith(">")):
            continue
        if stripped:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


class WebShellExecutor(BaseExecutor):
    """基于 SCOW Web Shell WebSocket 的命令执行器。"""

    def __init__(
        self,
        base_url: str,
        cluster: str,
        login_node: str,
        cookie: str,
        cols: int = 120,
        rows: int = 30,
    ):
        """初始化 Web Shell 执行器。

        Args:
            base_url: SCOW 平台地址，如 https://107.ustc.edu.cn
            cluster: 集群 ID，如 training
            login_node: 登录节点地址，如 11.11.10.202
            cookie: 浏览器登录 SCOW 后的 Cookie 字符串
            cols: 终端列数
            rows: 终端行数
        """
        self.base_url = base_url.rstrip("/")
        self.cluster = cluster
        self.login_node = login_node
        self.cookie = cookie
        self.cols = cols
        self.rows = rows
        self._ws = None
        self._connected = False
        # 用于命令输出同步
        self._output_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        # 一个 SCOW shell 同一时刻只能可靠执行一条命令。模板工作流和后台
        # 作业监控会共享连接，用锁防止两边争抢同一个输出队列。
        self._command_lock = asyncio.Lock()

    def _build_ws_url(self) -> str:
        """构建 WebSocket 连接 URL。"""
        # https -> wss, http -> ws
        if self.base_url.startswith("https"):
            ws_base = self.base_url.replace("https://", "wss://", 1)
        else:
            ws_base = self.base_url.replace("http://", "ws://", 1)

        params = urlencode({
            "cluster": self.cluster,
            "loginNode": self.login_node,
            "cols": str(self.cols),
            "rows": str(self.rows),
        })
        return f"{ws_base}/api/shell?{params}"

    async def connect(self) -> None:
        """建立 WebSocket 连接。"""
        url = self._build_ws_url()

        # SCOW 使用自签名证书或标准 HTTPS，这里允许两种情况
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        extra_headers = {
            "Cookie": self.cookie,
            "Origin": self.base_url,
        }

        self._ws = await websockets.connect(
            url,
            additional_headers=extra_headers,
            ssl=ssl_context,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        self._connected = True

        # 启动后台接收任务
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _cleanup(self) -> None:
        """安全关闭现有 WebSocket 连接和接收任务（用于重连前清理）。"""
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        self._recv_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._connected = False

        # 清空输出队列，避免残留数据干扰新连接
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _ensure_connected(self) -> None:
        """确保 WebSocket 连接可用；若已断开则自动重连。"""
        if self._connected and self._ws is not None:
            return
        # 连接不可用，清理后重建
        await self._cleanup()
        await self.connect()

    async def read_initial_output(self) -> str:
        """连接后读取远程 shell 的初始输出（通常是提示符）。"""
        await self._ensure_connected()

        output = ""
        # 等待最多 3 秒收集初始输出
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(
                    self._output_queue.get(), timeout=0.5
                )
                if chunk is None:
                    break
                output += chunk
                # 如果检测到提示符，立即返回
                if self._looks_like_prompt(output):
                    break
            except asyncio.TimeoutError:
                # 如果已经有内容且没有新数据，认为初始输出结束
                if output:
                    break
        return output

    async def _receive_loop(self) -> None:
        """后台循环接收 WebSocket 消息，放入队列。"""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                case = msg.get("$case")
                if case == "data":
                    data = msg.get("data", {}).get("data", "")
                    if data:
                        await self._output_queue.put(data)
                elif case == "exit":
                    # shell 退出
                    await self._output_queue.put(None)
                    break
        except websockets.exceptions.ConnectionClosed:
            self._connected = False
            await self._output_queue.put(None)
        except Exception:
            self._connected = False
            await self._output_queue.put(None)

    async def _send_command(self, command: str) -> bool:
        """发送命令到远程 shell；连接失效时自动重连并重试一次。

        Returns:
            True 表示发送成功，False 表示重连后仍然失败。
        """
        send_msg = json.dumps({
            "$case": "data",
            "data": {"data": command + "\n"},
        })
        for _ in range(2):
            await self._ensure_connected()
            try:
                await self._ws.send(send_msg)
                return True
            except Exception:
                # 连接已失效，清理后下一轮循环重连重试
                await self._cleanup()
        return False

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        """串行执行命令，避免多个协程交叉消费远程输出。"""
        async with self._command_lock:
            async for chunk in self._execute_stream_unlocked(command):
                yield chunk

    async def _execute_stream_unlocked(self, command: str) -> AsyncGenerator[str, None]:
        """通过 Web Shell 执行命令，流式返回输出。

        原理：发送命令 + 换行符，然后持续读取输出，
        直到检测到命令提示符（表示命令执行完毕）。
        会过滤掉远程 shell 的命令回显（前端已本地回显）。
        """
        # 清空队列中残留的输出
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 发送命令（连接失效时自动重连并重试一次）
        if not await self._send_command(command):
            yield "\r\n\x1b[31m[无法连接到集群，请检查网络或重新登录]\x1b[0m\r\n"
            return

        # 读取输出，直到检测到命令完成
        buffer = ""
        idle_count = 0
        max_idle = 15  # 连续 15 次超时（每次 0.5s = 7.5s 无输出）认为命令完成
        echo_skipped = False  # 是否已跳过命令回显
        retried = False  # 是否已因断线重连并重发过命令

        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._output_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                idle_count += 1
                if idle_count >= max_idle:
                    break
                continue

            if chunk is None:
                # 连接断开：尝试重连并重发命令（仅一次）
                if not retried:
                    retried = True
                    await self._cleanup()
                    if await self._send_command(command):
                        # 重连成功，重置读取状态继续等待输出
                        buffer = ""
                        idle_count = 0
                        echo_skipped = False
                        continue
                yield "\r\n\x1b[31m[连接已断开]\x1b[0m\r\n"
                break

            idle_count = 0
            buffer += chunk

            # 跳过远程 shell 的命令回显
            # 远程 shell 会先回显 "command\r\n"，然后才是实际输出
            if not echo_skipped:
                # 检查 buffer 是否包含命令回显（命令文本 + 换行）
                cmd_echo = command.strip()
                # 远程回显可能是 "command\r\n" 或带提示符前缀
                if cmd_echo in buffer:
                    # 找到回显位置，跳过回显部分
                    idx = buffer.find(cmd_echo)
                    after_echo = buffer[idx + len(cmd_echo):]
                    # 跳过回显后的换行符
                    after_echo = after_echo.lstrip("\r\n")
                    buffer = after_echo
                    echo_skipped = True
                    if after_echo:
                        yield after_echo
                    # 回显跳过后，若缓冲末尾已是提示符，命令即完成
                    # （空命令/无输出命令：提示符和回显在同一 chunk 到达）
                    # 避免空等 7.5s 空闲超时阻塞主循环
                    if self._looks_like_prompt(buffer):
                        break
                else:
                    # 还没收到完整回显，继续等
                    continue
                continue

            yield chunk

            # 检测命令提示符：以 $ 或 > 结尾，且前面有换行
            if self._looks_like_prompt(buffer):
                break

    def _looks_like_prompt(self, text: str) -> bool:
        """检测输出末尾是否是 shell 提示符。"""
        # 彩色 shell 会在提示符周围插入 ANSI 转义序列；不清理会导致实际
        # 已回到 `$` 提示符却继续等到空闲超时。
        text = _OSC_ESCAPE_RE.sub("", text)
        text = _CSI_ESCAPE_RE.sub("", text)
        # 取最后几行
        lines = text.rstrip().split("\n")
        if not lines:
            return False
        last_line = lines[-1].strip()
        # 常见提示符模式：user@host:path$ 或 user@host:path>
        if last_line.endswith("$") or last_line.endswith(">"):
            if "@" in last_line or last_line == "$" or last_line == ">":
                return True
        return False

    async def submit_job(self, script_content: str) -> dict:
        """通过 Web Shell 提交作业（写入临时文件后 sbatch）。"""
        remote_script = "/tmp/_hpc_copilot_job.sh"
        # 用 heredoc 写入脚本内容，避免转义问题
        write_cmd = f"cat > {remote_script} << 'HPC_EOF'\n{script_content}\nHPC_EOF"

        output = ""
        async for chunk in self.execute_stream(write_cmd):
            output += chunk

        # 提交作业
        output = ""
        async for chunk in self.execute_stream(f"sbatch {remote_script}"):
            output += chunk

        # 清理临时文件
        async for _ in self.execute_stream(f"rm -f {remote_script}"):
            pass

        # 解析 job id
        job_id = "?"
        if "Submitted batch job" in output:
            parts = output.strip().split()
            for i, p in enumerate(parts):
                if p == "job" and i + 1 < len(parts):
                    job_id = parts[i + 1].strip()
                    break

        return {"job_id": job_id, "output": output}

    async def get_jobs(self) -> list[dict]:
        """通过 squeue 获取作业列表。"""
        output = ""
        async for chunk in self.execute_stream(
            "squeue -u $USER -h -o '%i|%P|%j|%T|%M|%D'"
        ):
            output += chunk

        jobs = []
        for line in output.strip().splitlines():
            # 跳过提示符行
            if "@" in line and ("$" in line or ">" in line):
                continue
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id": parts[0].strip(),
                    "partition": parts[1].strip(),
                    "name": parts[2].strip(),
                    "job_state": parts[3].strip(),
                    "run_time": parts[4].strip(),
                    "node_count": parts[5].strip(),
                })
        return jobs

    async def get_job_states(self) -> dict[str, str]:
        """查询用户所有作业的状态（含失败终态），返回 {job_id: state}。

        使用 --states=all 确保能捕获 FAILED/TIMEOUT/CANCELLED 等
        即将被清理的终态作业（供后台监控使用）。
        """
        output = ""
        async for chunk in self.execute_stream(
            "squeue -u $USER --states=all -h -o '%i|%T'"
        ):
            output += chunk

        states: dict[str, str] = {}
        for line in output.strip().splitlines():
            # 跳过提示符行
            if "@" in line and ("$" in line or ">" in line):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                job_id = parts[0].strip()
                state = parts[1].strip()
                if job_id and job_id.isdigit():
                    states[job_id] = state
        return states

    async def cancel_job(self, job_id: str) -> None:
        """通过 scancel 取消作业。"""
        async for _ in self.execute_stream(f"scancel {job_id}"):
            pass

    async def execute_agent_command(self, command: str) -> str:
        """Agent 专用：执行命令并收集全部输出为单个字符串。

        复用已有的 WebShell 连接。因为 agent 执行时用户通常在观看
        对话面板而非同时键入终端，实际冲突概率极低。
        """
        output = ""
        async for chunk in self.execute_stream(command):
            output += chunk
        output = _plain_command_output(output)
        return output if output else "(命令执行成功，无输出)"

    async def close(self) -> None:
        """关闭 WebSocket 连接。"""
        if self._ws:
            # 发送断开消息（优雅关闭 SCOW shell 会话）
            try:
                await self._ws.send(json.dumps({
                    "$case": "disconnect",
                    "disconnect": {},
                }))
            except Exception:
                pass
        await self._cleanup()
