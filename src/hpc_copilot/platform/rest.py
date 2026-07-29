"""REST API 平台适配器。

对接学校算力平台的 slurmrestd（v0.0.41），通过 JWT 认证。
已验证可用：GET /jobs, GET /partitions
已验证不可用：slurmdbd 相关接口（历史查询、QoS 列表）
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

from .base import JobInfo, PlatformAdapter

API_VERSION = "v0.0.41"


class RestPlatformAdapter(PlatformAdapter):
    """通过 Slurm REST API 与真实平台交互。"""

    def __init__(
        self,
        api_base: str = "http://107.ustc.edu.cn:6820",
        token: str = "",
        timeout: float = 10.0,
    ):
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None

    @property
    def _headers(self) -> dict:
        return {"X-SLURM-USER-TOKEN": self.token}

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _url(self, path: str) -> str:
        return f"{self.api_base}/slurm/{API_VERSION}/{path}"

    def get_job(self, job_id: int) -> Optional[JobInfo]:
        """获取单个作业状态。先查队列，队列中没有则返回 None（可能已完成）。"""
        try:
            resp = self.client.get(self._url("jobs"), headers=self._headers)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
            for j in jobs:
                if j.get("job_id") == job_id:
                    return self._parse_job(j)
            return None
        except httpx.HTTPError:
            return None

    def get_jobs(self, user: Optional[str] = None) -> list[JobInfo]:
        """获取作业列表。"""
        try:
            resp = self.client.get(self._url("jobs"), headers=self._headers)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
            results = [self._parse_job(j) for j in jobs]
            if user:
                results = [j for j in results if j.user_name == user]
            return results
        except httpx.HTTPError:
            return []

    def get_partitions(self) -> list[str]:
        """获取可用分区列表。"""
        try:
            resp = self.client.get(self._url("partitions"), headers=self._headers)
            resp.raise_for_status()
            partitions = resp.json().get("partitions", [])
            return [p.get("name", "") for p in partitions if p.get("name")]
        except httpx.HTTPError:
            return []

    def poll_until_terminal(
        self,
        job_id: int,
        interval: float = 3.0,
        max_wait: float = 120.0,
        on_update=None,
    ) -> Optional[JobInfo]:
        """轮询作业直到结束。

        Args:
            job_id: 作业 ID
            interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
            on_update: 每次轮询时的回调函数，接收 JobInfo

        Returns:
            作业结束后的 JobInfo，超时返回 None
        """
        elapsed = 0.0
        while elapsed < max_wait:
            job = self.get_job(job_id)
            if job and on_update:
                on_update(job)
            if job and job.is_terminal:
                return job
            if job is None:
                # 作业不在队列中，可能已经完成并被清理
                return None
            time.sleep(interval)
            elapsed += interval
        return None

    def _parse_job(self, raw: dict) -> JobInfo:
        """将 API 返回的原始 JSON 解析为 JobInfo。"""
        # 状态
        state_raw = raw.get("job_state", [])
        state = state_raw[0] if isinstance(state_raw, list) and state_raw else str(state_raw)

        # 退出码
        exit_code_raw = raw.get("exit_code", {})
        rc = exit_code_raw.get("return_code", {})
        exit_code = rc.get("number") if rc.get("set") else None

        # 时间
        time_limit_raw = raw.get("time_limit", {})
        time_limit = time_limit_raw.get("number") if time_limit_raw.get("set") else None

        run_time_raw = raw.get("run_time")
        run_time = run_time_raw if isinstance(run_time_raw, (int, float)) else None

        # CPU
        cpus_raw = raw.get("cpus", {})
        cpus = cpus_raw.get("number") if isinstance(cpus_raw, dict) and cpus_raw.get("set") else None

        return JobInfo(
            job_id=raw.get("job_id", 0),
            name=raw.get("name", ""),
            user_name=raw.get("user_name", ""),
            partition=raw.get("partition", ""),
            state=state,
            exit_code=exit_code,
            state_reason=raw.get("state_reason", ""),
            run_time=int(run_time) if run_time else None,
            time_limit=time_limit,
            cpus=cpus,
            qos=raw.get("qos", ""),
            working_directory=raw.get("current_working_directory", ""),
            std_out=raw.get("standard_output", ""),
            std_err=raw.get("standard_error", ""),
            raw=raw,
        )

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()
