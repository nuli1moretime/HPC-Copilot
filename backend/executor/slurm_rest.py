"""Slurm REST API 执行器。

通过 slurmrestd 提供的 REST API 与算力中心交互。
注意：REST API 不是真正的 shell，只支持 Slurm 相关命令的映射。
"""

from typing import AsyncGenerator

import httpx

from .base import BaseExecutor

# slurmrestd API 版本前缀
_API_PREFIX = "/slurm/v0.0.41"


class SlurmRestExecutor(BaseExecutor):
    """基于 Slurm REST API 的命令执行器。"""

    def __init__(self, base_url: str, user: str, token: str = ""):
        """初始化 REST API 执行器。

        Args:
            base_url: slurmrestd 地址，如 http://hpc.school.edu:6820
            user: 集群用户名
            token: JWT token（可选，取决于认证方式）
        """
        self.base_url = base_url.rstrip("/")
        self.user = user
        headers = {"X-SLURM-USER-NAME": user}
        if token:
            headers["X-SLURM-USER-TOKEN"] = token
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
        )

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        """将终端命令映射到 REST API 调用。

        slurmrestd 不提供任意 shell，只支持 Slurm 命令映射：
        - sbatch → POST /job/submit
        - squeue → GET /jobs
        - scancel → DELETE /job/{id}
        - sinfo → GET /partitions + /nodes
        - sacct → GET /jobs（历史）
        """
        cmd = command.strip()
        cmd_name = cmd.split()[0] if cmd else ""

        try:
            if cmd_name == "sbatch":
                # 简化处理：提取脚本路径（实际场景需要读取脚本内容）
                yield "⚠️  REST API 模式下 sbatch 需要提供脚本内容。\n"
                yield "   请使用右侧面板上传脚本，或改用 SSH 模式。\n"

            elif cmd_name == "squeue":
                jobs = await self.get_jobs()
                yield self._format_squeue(jobs)

            elif cmd_name == "scancel":
                parts = cmd.split()
                if len(parts) < 2:
                    yield "用法: scancel <job_id>\n"
                else:
                    job_id = parts[1]
                    await self.cancel_job(job_id)
                    yield f"已取消作业 {job_id}\n"

            elif cmd_name == "sinfo":
                partitions = await self._get_partitions()
                yield self._format_sinfo(partitions)

            elif cmd_name == "sacct":
                jobs = await self.get_jobs()
                yield self._format_sacct(jobs)

            elif cmd_name in ("help", "?"):
                yield self._help_text()

            else:
                yield f"⚠️  REST API 模式不支持命令: {cmd_name}\n"
                yield "   支持的命令: squeue / scancel / sinfo / sacct / help\n"
                yield "   如需执行任意命令，请切换到 SSH 模式。\n"

        except httpx.ConnectError:
            yield "❌ 无法连接到 Slurm REST API，请检查网络和配置。\n"
        except httpx.HTTPStatusError as e:
            yield f"❌ API 返回错误: HTTP {e.response.status_code}\n"
            yield f"   {e.response.text[:200]}\n"
        except Exception as e:
            yield f"❌ 执行出错: {type(e).__name__}: {e}\n"

    async def submit_job(self, script_content: str) -> dict:
        """通过 REST API 提交作业。"""
        resp = await self.client.post(
            f"{_API_PREFIX}/job/submit",
            json={"script": script_content},
        )
        resp.raise_for_status()
        data = resp.json()
        job_id = data.get("job_id", data.get("jobid", "?"))
        return {"job_id": job_id, "raw": data}

    async def get_jobs(self) -> list[dict]:
        """获取作业列表。"""
        resp = await self.client.get(f"{_API_PREFIX}/jobs")
        resp.raise_for_status()
        return resp.json().get("jobs", [])

    async def cancel_job(self, job_id: str) -> None:
        """取消作业。"""
        resp = await self.client.delete(f"{_API_PREFIX}/job/{job_id}")
        resp.raise_for_status()

    async def _get_partitions(self) -> list[dict]:
        """获取分区信息。"""
        resp = await self.client.get(f"{_API_PREFIX}/partitions")
        resp.raise_for_status()
        return resp.json().get("partitions", [])

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self.client.aclose()

    # ─── 格式化输出 ────────────────────────────────────────────

    def _format_squeue(self, jobs: list[dict]) -> str:
        """模拟 squeue 输出格式。"""
        if not jobs:
            return "             JOBID PARTITION     NAME     USER ST       TIME  NODES\n"

        lines = ["             JOBID PARTITION     NAME     USER ST       TIME  NODES"]
        for j in jobs[:30]:
            state = j.get("job_state", ["?"])
            state_str = state[0] if isinstance(state, list) else str(state)
            lines.append(
                f"{j.get('job_id', '?'):>12} "
                f"{j.get('partition', '?'):>9} "
                f"{j.get('name', '?'):>8} "
                f"{j.get('user_name', self.user):>8} "
                f"{state_str:>2} "
                f"{j.get('run_time', 0):>9} "
                f"{j.get('node_count', 1):>6}"
            )
        return "\n".join(lines) + "\n"

    def _format_sinfo(self, partitions: list[dict]) -> str:
        """模拟 sinfo 输出格式。"""
        lines = ["PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST"]
        for p in partitions[:20]:
            lines.append(
                f"{p.get('name', '?'):>9} "
                f"{'up':>5} "
                f"{p.get('max_time', 'infinite'):>9} "
                f"{p.get('total_nodes', '?'):>6} "
                f"{'idle':>6} "
                f"{p.get('nodes', '?')}"
            )
        return "\n".join(lines) + "\n"

    def _format_sacct(self, jobs: list[dict]) -> str:
        """模拟 sacct 输出格式。"""
        lines = ["       JobID    JobName  Partition      State  Elapsed    MaxRSS"]
        lines.append("-" * 68)
        for j in jobs[:20]:
            state = j.get("job_state", ["?"])
            state_str = state[0] if isinstance(state, list) else str(state)
            lines.append(
                f"{j.get('job_id', '?'):>12} "
                f"{j.get('name', '?'):>9} "
                f"{j.get('partition', '?'):>9} "
                f"{state_str:>10} "
                f"{j.get('run_time', 0):>8} "
                f"{j.get('max_rss', '?'):>9}"
            )
        return "\n".join(lines) + "\n"

    def _help_text(self) -> str:
        """帮助信息。"""
        return (
            "\n🖥️  HPC Copilot - Slurm REST API 模式\n"
            "=" * 45 + "\n"
            "支持的命令：\n"
            "  squeue          查看作业队列\n"
            "  scancel <id>    取消作业\n"
            "  sinfo           查看分区和节点\n"
            "  sacct           查看作业历史\n"
            "  help            显示本帮助\n"
            "\n"
            "提示：REST API 模式不支持任意 shell 命令。\n"
            "如需完整终端体验，请在连接设置中切换到 SSH 模式。\n"
        )
