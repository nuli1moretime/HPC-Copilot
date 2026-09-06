"""Embedding 服务 — 调用 USTC API 将文本转向量。

支持批量请求和缓存，API 不可用时降级为简单关键词匹配。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

import httpx

logger = logging.getLogger("hpc-copilot.rag.embedding")


class EmbeddingService:
    """文本向量化服务。"""

    def __init__(
        self,
        api_base: str = "",
        api_key: str = "",
        model: str = "qwen3-embedding",
        timeout: float = 30.0,
    ):
        self.api_base = api_base.rstrip("/") if api_base else ""
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._cache: dict[str, list[float]] = {}  # text_hash -> vector

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base and self.api_key)

    async def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """批量文本转向量。返回 None 表示 API 不可用。"""
        if not self.is_configured:
            return None

        # 检查缓存
        results: list[Optional[list[float]]] = [None] * len(texts)
        to_embed: list[tuple[int, str]] = []  # (index, text)

        for i, text in enumerate(texts):
            h = hashlib.md5(text.encode()).hexdigest()
            if h in self._cache:
                results[i] = self._cache[h]
            else:
                to_embed.append((i, text))

        if not to_embed:
            return results  # type: ignore

        # 批量调用 API
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_base}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "input": [t for _, t in to_embed],
                    },
                )
                response.raise_for_status()
                data = response.json()

                embeddings = data.get("data", [])
                for idx, (orig_idx, text) in enumerate(to_embed):
                    if idx < len(embeddings):
                        vec = embeddings[idx].get("embedding", [])
                        results[orig_idx] = vec
                        # 写入缓存
                        h = hashlib.md5(text.encode()).hexdigest()
                        self._cache[h] = vec

            return results  # type: ignore

        except Exception as e:
            logger.warning("Embedding API 调用失败: %s", e)
            return None

    async def embed_query(self, text: str) -> Optional[list[float]]:
        """单条查询转向量。"""
        results = await self.embed([text])
        if results and results[0] is not None:
            return results[0]
        return None
