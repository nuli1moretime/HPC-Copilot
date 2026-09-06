"""Reranker 重排序（可选）。

调用 qwen3-reranker API 对候选文档块精排。
API 不可用时跳过，直接返回 FAISS 的排序结果。
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from .loader import DocChunk
from .vector_store import SearchResult

logger = logging.getLogger("hpc-copilot.rag.reranker")


class Reranker:
    """文档重排序服务。"""

    def __init__(
        self,
        api_base: str = "",
        api_key: str = "",
        model: str = "qwen3-reranker",
        timeout: float = 15.0,
    ):
        self.api_base = api_base.rstrip("/") if api_base else ""
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base and self.api_key)

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_n: int = 3,
    ) -> list[SearchResult]:
        """对候选结果重排序，返回 top-n。

        如果 API 不可用，直接返回原始结果的前 top_n 个。
        """
        if not self.is_configured or not results:
            return results[:top_n]

        try:
            # 构造 reranker 输入
            pairs = [
                {"query": query, "document": r.chunk.text}
                for r in results
            ]

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_base}/rerank",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": [r.chunk.text for r in results],
                        "top_n": top_n,
                    },
                )
                response.raise_for_status()
                data = response.json()

                # 解析结果
                reranked = []
                for item in data.get("results", []):
                    idx = item.get("index", 0)
                    score = item.get("relevance_score", 0.0)
                    if 0 <= idx < len(results):
                        reranked.append(SearchResult(
                            chunk=results[idx].chunk,
                            score=score,
                        ))

                return reranked[:top_n]

        except Exception as e:
            logger.warning("Reranker API 调用失败，使用原始排序: %s", e)
            return results[:top_n]
