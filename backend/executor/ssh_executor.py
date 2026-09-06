"""SSH 执行器（备选方案）。

当算力中心没有开放 slurmrestd 时，通过 SSH 连接执行任意命令。
使用 paramiko 库，支持完整的终端交互体验。
"""

import asyncio
import io
import logging
import re
import socket
import threading
import time
from typing import AsyncGenerator

from .base import BaseExecutor

logger = logging.getLogger("hpc-copilot.ssh")

# 用于从提示符行剥离 ANSI 颜色/光标转义，避免误判
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore


class SSHExecutor(BaseExecutor):
    """基于 SSH 的命令执行器，支持任意 shell 命令。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str = "",
        key_filename: str = "",
        port: int = 22,
        key_content: str = "",
        totp: str = "",
        cols: int = 120,
        rows: int = 30,
    ):
        """初始化 SSH 执行器。

        Args:
            host: 集群地址
            username: 用户名
            password: 密码（与密钥二选一）
            key_filename: SSH 私钥文件路径
            port: SSH 端口（默认 22）
            key_content: SSH 私钥内容（直接粘贴的 PEM 文本，优先于 key_filename）
            totp: Google Authenticator 动态验证码（6 位数字，用于两步验证）
            cols: 终端列数（分配 PTY 时用，决定远程按多宽换行）
            rows: 终端行数
        """
        if paramiko is None:
            raise ImportError(
                "SSH 模式需要安装 paramiko: pip install paramiko"
            )

        self.host = host
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.key_content = key_content
        self.totp = totp
        self.port = port
        self.cols = cols
        self.rows = rows
        self._transport: "paramiko.Transport | None" = None
        # 持久交互式 shell 相关状态
        self._shell: "paramiko.Channel | None" = None
        self._output_queue: "asyncio.Queue[str | None] | None" = None
        self._loop: "asyncio.AbstractEventLoop | None" = None
        self._reader_thread: "threading.Thread | None" = None
        self._connected = False

    async def connect(self) -> None:
        """建立 SSH 连接并打开一个持久交互式 shell（带 PTY）。

        与 exec_command 的「每条命令一个无 PTY 的非登录 shell」不同，
        invoke_shell 会拿到真实的登录环境（.bashrc / module / conda 生效）、
        真实提示符、按终端宽度换行——体验与 SCOW Web Shell 一致。
        """
        self._loop = asyncio.get_event_loop()
        self._output_queue = asyncio.Queue()

        # 1) 认证，拿到 Transport
        transport = await self._loop.run_in_executor(None, self._connect_sync)
        self._transport = transport

        # 2) 打开交互式 shell 通道（分配 PTY）
        shell = await self._loop.run_in_executor(None, self._open_shell_sync)
        self._shell = shell
        self._connected = True

        # 3) 启动后台读取线程，把远程输出喂进 asyncio.Queue
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="ssh-shell-reader"
        )
        self._reader_thread.start()

    def _open_shell_sync(self) -> "paramiko.Channel":
        """同步打开交互式 shell 通道（在线程池中执行）。"""
        logger.info("[SSH 4/4] 正在打开交互式 PTY shell ...")
        try:
            chan = self._transport.open_session(timeout=30)
            # 分配 PTY：xterm-256color + 指定宽高，让 squeue/sinfo 等按终端宽度换行
            chan.get_pty(term="xterm-256color", width=self.cols, height=self.rows)
            chan.invoke_shell()
        except socket.timeout as e:
            raise RuntimeError(
                "打开 SSH shell 通道超时（30s）：认证已通过但服务器没响应 PTY 请求。"
                "常见于计算资源紧张或 gateway 卡顿，稍等 30 秒再试。"
            ) from e
        except Exception as e:
            raise RuntimeError(f"打开 SSH shell 通道失败：{type(e).__name__}: {e}") from e
        logger.info("[SSH 4/4] ✅ SSH 交互式 shell 已打开 (pty=%dx%d)", self.cols, self.rows)
        return chan

    def _reader_loop(self) -> None:
        """后台线程：持续从 shell 通道读取输出，推入 asyncio.Queue。

        用 call_soon_threadsafe 跨线程投递；连接关闭/EOF 时投递 None 作为结束标记。
        """
        chan = self._shell
        loop = self._loop
        q = self._output_queue
        try:
            while True:
                if chan is None or chan.closed or chan.eof_received:
                    break
                if chan.recv_ready():
                    data = chan.recv(65536)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="replace")
                    loop.call_soon_threadsafe(q.put_nowait, text)
                elif chan.recv_stderr_ready():
                    data = chan.recv_stderr(65536)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        loop.call_soon_threadsafe(q.put_nowait, text)
                else:
                    time.sleep(0.02)
        except Exception as e:
            logger.warning("SSH shell 读取线程异常退出: %s: %s", type(e).__name__, e)
        finally:
            self._connected = False
            try:
                loop.call_soon_threadsafe(q.put_nowait, None)
            except Exception:
                pass

    async def resize(self, cols: int, rows: int) -> None:
        """调整远程 PTY 尺寸（前端窗口变化时调用）。"""
        self.cols = cols
        self.rows = rows
        if self._shell is not None:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None, lambda: self._shell.resize_pty(width=cols, height=rows)
                )
            except Exception as e:
                logger.warning("调整 PTY 尺寸失败: %s", e)

    async def read_initial_output(self) -> str:
        """连接后读取远程 shell 的初始输出（通常是登录横幅 + 提示符）。"""
        # 该方法在任何命令之前被调用，需在此确保连接/ shell 已建立
        if (
            self._shell is None
            or self._transport is None
            or not self._transport.is_active()
            or not self._connected
            or self._output_queue is None
        ):
            await self.connect()

        output = ""
        # 107 登录横幅 + 集群状态提示偶尔会拖到 5-8 秒，deadline 从 4s 提到 10s
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(self._output_queue.get(), timeout=0.5)
                if chunk is None:
                    break
                output += chunk
                if self._looks_like_prompt(output):
                    break
            except asyncio.TimeoutError:
                if output:
                    break
        return output

    def _load_private_key(self) -> "paramiko.PKey | None":
        """加载私钥，自动识别「PEM 内容」还是「文件路径」。

        - 若文本以 -----BEGIN 开头 → 当作私钥内容（粘贴的 PEM）
        - 否则 → 当作私钥文件路径（本地部署时更可靠，避免粘贴损坏）

        依次尝试 Ed25519 / RSA / ECDSA / DSA，passphrase 用密码字段。
        """
        raw = self.key_content.strip()
        if not raw:
            return None

        passphrase = self.password or None
        key_class_names = ("Ed25519Key", "RSAKey", "ECDSAKey", "DSAKey")

        is_pem_content = raw.startswith("-----BEGIN") or "PRIVATE KEY-----" in raw

        if is_pem_content:
            raw = self._normalize_pem(raw)

        for name in key_class_names:
            cls = getattr(paramiko, name, None)
            if cls is None:
                continue
            try:
                if is_pem_content:
                    return cls.from_private_key(io.StringIO(raw), password=passphrase)
                else:
                    return cls.from_private_key_file(raw, password=passphrase)
            except paramiko.SSHException:
                # 类型不匹配或需要 passphrase，换下一个类
                continue
            except Exception:
                continue
        return None

    @staticmethod
    def _normalize_pem(text: str) -> str:
        """修复粘贴到网页 textarea 时常见的 PEM 损坏。

        处理：Windows 换行 \\r\\n、行尾空格、被压成一行的 base64 体。
        保证 -----BEGIN/END----- 头尾各占一行，中间按 64 字符折行。
        """
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 找到 BEGIN / END 标记
        import re
        m = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----", text)
        if not m:
            return text.strip() + "\n"
        header = m.group(0)
        end_tag = "-----END " + m.group(1) + "-----"
        end_idx = text.find(end_tag)
        if end_idx == -1:
            return text.strip() + "\n"
        body = text[m.end():end_idx]
        # 去掉所有空白，重新按 64 字符折行
        body_clean = re.sub(r"\s+", "", body)
        lines = [header]
        for i in range(0, len(body_clean), 64):
            lines.append(body_clean[i:i + 64])
        lines.append(end_tag)
        return "\n".join(lines) + "\n"

    def _connect_sync(self) -> "paramiko.Transport":
        """同步连接（在线程池中执行）。

        手动驱动 Transport，按 publickey → keyboard-interactive(TOTP) → password
        的顺序尝试，以支持 107 平台「公钥 + Google 动态码」两步验证。
        返回已认证的 Transport，供 execute_stream 直接开 session 通道。
        """
        # 阶段 1：TCP 三次握手（107 网关偶尔会慢，给足 30s）
        logger.info("[SSH 1/4] 正在建立 TCP 连接 %s:%s ...", self.host, self.port)
        try:
            sock = socket.create_connection((self.host, self.port), timeout=30)
        except socket.timeout as e:
            raise RuntimeError(
                f"TCP 连接超时（30s）：{self.host}:{self.port} 不可达。"
                "请确认在校园网/VPN 内，且主机端口没写错。"
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"TCP 连接失败：{type(e).__name__}: {e}。"
                "常见原因：主机名解析不到、端口被封、防火墙拦截。"
            ) from e
        logger.info("[SSH 1/4] TCP 已连通")

        transport = paramiko.Transport(sock)
        transport.banner_timeout = 60
        transport.auth_timeout = 60

        # 阶段 2：SSH banner + 密钥交换（KEX）
        logger.info("[SSH 2/4] 等待 SSH banner 与密钥交换 ...")
        try:
            transport.start_client(timeout=30)
        except socket.timeout as e:
            transport.close()
            raise RuntimeError(
                "SSH 密钥交换超时（30s）：服务器接受了 TCP 但没完成 KEX。"
                "常见于网关限流、连接数打满、或者中间设备把 SSH 流量识别为异常。"
                "建议等 1-2 分钟让服务器清理旧会话再试。"
            ) from e
        except paramiko.SSHException as e:
            transport.close()
            raise RuntimeError(f"SSH 协议握手失败：{e}") from e

        # paramiko 5.0 下 start_client 可能未等 KEX 完全结束就返回，
        # 认证前显式等待 transport 就绪，否则 ensure_session 抛 "No existing session"。
        import time as _time
        deadline = _time.time() + 30
        while not (transport.active and transport.initial_kex_done):
            if _time.time() > deadline:
                transport.close()
                raise RuntimeError(
                    f"SSH 密钥交换未完成 (active={transport.active}, kex_done={transport.initial_kex_done})"
                )
            if not transport.is_active():
                transport.close()
                raise RuntimeError(
                    "SSH 连接在密钥交换阶段被服务器关闭。"
                    "常见于服务器端会话数达上限，或客户端 IP 被临时限流。"
                )
            _time.sleep(0.1)

        logger.info(
            "[SSH 2/4] transport 已就绪, active=%s, kex_done=%s",
            transport.active, transport.initial_kex_done,
        )

        authenticated = False

        # 阶段 3：认证（publickey → keyboard-interactive(TOTP) → password）
        attempted: list[str] = []
        logger.info("[SSH 3/4] 开始认证，用户名=%s", self.username)

        # 3.1) 公钥认证
        pkey = None
        if self.key_content.strip():
            pkey = self._load_private_key()
            if pkey is None:
                logger.warning(
                    "私钥内容已提供但加载失败——很可能是 passphrase 不对或未填。"
                    "请在「密码」框填写私钥的 passphrase。"
                )
            else:
                logger.info("私钥加载成功: %s", pkey.get_name())
        if pkey is not None and not authenticated:
            attempted.append("publickey")
            try:
                transport.auth_publickey(self.username, pkey)
                logger.info("公钥认证后: authenticated=%s, active=%s", transport.is_authenticated(), transport.active)
                if transport.is_authenticated():
                    authenticated = True
                    logger.info("[SSH 3/4] ✅ 公钥认证成功")
            except Exception as e:
                logger.warning("公钥认证异常: %s: %s", type(e).__name__, e)

        # 3.2) keyboard-interactive（动态验证码）
        #    107 两步验证：公钥通过后服务器仍要求输入 TOTP，
        #    此时 is_authenticated() 为 False，需用验证码完成第二因子。
        logger.info("是否收到动态码: %s (长度=%d)", bool(self.totp.strip()), len(self.totp.strip()))
        if self.totp.strip() and not authenticated:
            attempted.append("keyboard-interactive(TOTP)")
            totp = self.totp.strip()

            def handler(title, instructions, prompt_list):
                # 打印服务器发来的交互提示，便于诊断要的是什么
                logger.info(
                    "keyboard-interactive 挑战: title=%r instructions=%r prompts=%r",
                    title, instructions, prompt_list,
                )
                # 关键：响应数量必须与提示数量严格一致。
                # 服务器可能发多次挑战，其中某次 prompts=[] 为空，
                # 此时必须返回空列表 []，否则协议报文数量不匹配会被服务器断开。
                return [totp for _ in prompt_list]

            try:
                transport.auth_interactive(self.username, handler)
                logger.info("动态码认证后: authenticated=%s, active=%s", transport.is_authenticated(), transport.active)
                if transport.is_authenticated():
                    authenticated = True
                    logger.info("[SSH 3/4] ✅ 动态码认证成功")
            except Exception as e:
                logger.warning("动态码认证异常: %s: %s", type(e).__name__, e)

        # 3.3) 密码认证（兜底）
        if not authenticated and self.password:
            attempted.append("password")
            try:
                transport.auth_password(self.username, self.password)
                if transport.is_authenticated():
                    authenticated = True
                    logger.info("[SSH 3/4] ✅ 密码认证成功")
            except Exception as e:
                logger.warning("密码认证异常: %s: %s", type(e).__name__, e)

        if not authenticated:
            transport.close()
            tried = "/".join(attempted) if attempted else "无（未提供任何凭证）"
            raise paramiko.AuthenticationException(
                f"SSH 认证失败：已尝试 [{tried}]，均未通过。"
                "两步验证请确认动态码新鲜（30 秒内）、私钥 passphrase 填在「密码」框。"
            )

        logger.info(
            "[SSH 3/4] 认证完成, active=%s, kex_done=%s",
            transport.active, transport.initial_kex_done,
        )
        return transport

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        """通过持久交互式 shell 执行命令，流式返回输出。

        原理与 WebShell 一致：把「命令 + 换行」写进远程 PTY，然后持续读取
        输出，跳过远程对命令本身的回显（前端已本地回显），直到再次检测到
        shell 提示符（表示命令执行完毕）或空闲超时。
        """
        # 确保连接可用（断开则重连）
        try:
            if (
                self._shell is None
                or self._transport is None
                or not self._transport.is_active()
                or not self._connected
            ):
                await self.connect()
        except paramiko.AuthenticationException:
            yield "❌ SSH 认证失败，请检查用户名/密钥/动态码。\n"
            return
        except Exception as e:
            yield f"❌ SSH 连接失败: {type(e).__name__}: {e}\n"
            return

        q = self._output_queue
        # 清空队列中残留的输出
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 发送命令到远程 PTY
        try:
            self._shell.sendall((command + "\n").encode("utf-8"))
        except Exception as e:
            yield f"\r\n\x1b[31m[无法发送到集群: {e}]\x1b[0m\r\n"
            return

        cmd_echo = command.strip()
        # 仅对「普通可打印命令」跳过远程回显；空命令 / 控制字符（如 Ctrl+C \x03）
        # 不跳，直接透传输出，否则会一直等不到回显而空转到超时。
        skip_echo = bool(cmd_echo) and all(ord(c) >= 32 for c in cmd_echo)

        buffer = ""
        idle_count = 0
        max_idle = 30  # 每次 0.5s，连续 30 次（约 15s）无新输出认为命令结束
        echo_skipped = not skip_echo

        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                idle_count += 1
                if idle_count >= max_idle:
                    break
                continue

            if chunk is None:
                # shell 已关闭
                self._connected = False
                yield "\r\n\x1b[31m[SSH 连接已断开]\x1b[0m\r\n"
                break

            idle_count = 0
            buffer += chunk

            # 跳过远程对命令的回显
            if not echo_skipped:
                if cmd_echo in buffer:
                    idx = buffer.find(cmd_echo)
                    after_echo = buffer[idx + len(cmd_echo):].lstrip("\r\n")
                    buffer = after_echo
                    echo_skipped = True
                    if after_echo:
                        yield after_echo
                    if self._looks_like_prompt(buffer):
                        break
                else:
                    # 还没收到完整回显，继续等
                    continue
                continue

            yield chunk

            # 检测到提示符 → 命令执行完毕
            if self._looks_like_prompt(buffer):
                break

    def _looks_like_prompt(self, text: str) -> bool:
        """检测输出末尾是否是 shell 提示符（先剥离 ANSI 转义再判断）。"""
        lines = text.rstrip().split("\n")
        if not lines:
            return False
        last_line = _ANSI_RE.sub("", lines[-1]).strip()
        if not last_line:
            return False
        if last_line.endswith("$") or last_line.endswith(">") or last_line.endswith("#"):
            if "@" in last_line or last_line in ("$", ">", "#"):
                return True
        return False

    async def submit_job(self, script_content: str) -> dict:
        """通过交互式 shell 提交作业（heredoc 写临时文件后 sbatch）。"""
        remote_script = "/tmp/_hpc_copilot_job.sh"
        write_cmd = f"cat > {remote_script} << 'HPC_EOF'\n{script_content}\nHPC_EOF"

        async for _ in self.execute_stream(write_cmd):
            pass

        output = ""
        async for chunk in self.execute_stream(f"sbatch {remote_script}"):
            output += chunk

        async for _ in self.execute_stream(f"rm -f {remote_script}"):
            pass

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
        """查询用户所有作业状态（含失败终态），返回 {job_id: state}。"""
        output = ""
        async for chunk in self.execute_stream(
            "squeue -u $USER --states=all -h -o '%i|%T'"
        ):
            output += chunk

        states: dict[str, str] = {}
        for line in output.strip().splitlines():
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
        """Agent 专用：在已认证的 Transport 上开独立 channel 执行命令。

        与 execute_stream（走交互式 PTY shell）不同，这里用 exec_command
        开一个隔离的 session channel——不会干扰用户正在使用的终端。

        关键：命令必须包在 `bash -lc '...'` 里执行，否则 paramiko 的
        exec_command 默认走非登录非交互 shell，不会加载 /etc/profile
        和 ~/.bashrc，导致 `module load`、conda 环境等全部失效
        （典型报错：bash: module: command not found）。
        """
        if self._transport is None or not self._transport.is_active():
            return "❌ SSH 未连接，无法执行命令。"

        loop = asyncio.get_event_loop()

        # 用单引号包裹命令；命令内部的单引号用 '\'' 转义（POSIX 标准做法）
        escaped = command.replace("'", "'\\''")
        wrapped = f"bash -lc '{escaped}'"

        def _run() -> tuple[str, int]:
            chan = self._transport.open_session(timeout=15)
            chan.settimeout(120)  # 单条命令最长 2 分钟（提交、查询日志等可能较慢）
            chan.set_combine_stderr(True)
            chan.exec_command(wrapped)
            with chan.makefile("rb") as f:
                data = f.read()
            exit_code = chan.recv_exit_status()
            chan.close()
            return data.decode("utf-8", errors="replace"), exit_code

        try:
            out, code = await loop.run_in_executor(None, _run)
            if out.strip():
                return out
            elif code != 0:
                return f"(命令退出码 {code}，无输出)"
            else:
                return "(命令执行成功，无输出)"
        except Exception as e:
            return f"❌ 命令执行失败: {type(e).__name__}: {e}"

    async def close(self) -> None:
        """关闭交互式 shell 通道与 SSH 连接。"""
        self._connected = False
        shell = self._shell
        transport = self._transport
        self._shell = None
        self._transport = None

        def _close_sync():
            try:
                if shell is not None:
                    shell.close()
            except Exception:
                pass
            try:
                if transport is not None:
                    transport.close()
            except Exception:
                pass

        try:
            await asyncio.get_event_loop().run_in_executor(None, _close_sync)
        except Exception:
            pass
