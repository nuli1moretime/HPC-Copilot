"""检索编排器 — RAG 对外接口。

整合 loader、embedding、vector_store、reranker，
提供完整的"问题 → 相关文档块"检索流程。
"""

from __future__ import annotations

import logging
import hashlib
import json
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from .embedding import EmbeddingService
from .loader import DocChunk, load_chunks, truncate_chunk
from .reranker import Reranker
from .vector_store import SearchResult, VectorStore

logger = logging.getLogger("hpc-copilot.rag.retriever")

_CHUNKING_SCHEMA_VERSION = 2


def _corpus_fingerprint(chunks: list[DocChunk], embedding_model: str) -> str:
    payload = {
        "chunking_schema_version": _CHUNKING_SCHEMA_VERSION,
        "embedding_model": embedding_model,
        "chunks": [
            {
                "id": chunk.id,
                "heading": chunk.heading,
                "source_file": chunk.source_file,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class _LRUCache:
    """极简 LRU 缓存，用于查询向量复用（同一问题重复问时省一次 embedding API 调用）。"""

    def __init__(self, maxsize: int = 128):
        self._data: "OrderedDict[str, list[float]]" = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: str, value) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)


class Retriever:
    """RAG 检索器。"""

    def __init__(
        self,
        knowledge_dir: Path,
        index_dir: Path,
        embedding_service: EmbeddingService,
        reranker: Optional[Reranker] = None,
        top_k: int = 3,
        search_top_k: int = 10,
        hybrid_enabled: bool = True,
        lexical_weight: float = 0.35,
    ):
        self.knowledge_dir = knowledge_dir
        self.index_dir = index_dir
        self.embedding = embedding_service
        self.reranker = reranker
        self.top_k = top_k
        self.search_top_k = search_top_k
        self.hybrid_enabled = hybrid_enabled
        self.lexical_weight = lexical_weight
        self.store = VectorStore()
        self._ready = False
        # 查询向量 LRU 缓存：同一问题重复问时跳过 embedding API 调用（省 2-10s）
        self._query_cache = _LRUCache(maxsize=256)

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def initialize(self) -> bool:
        """初始化 RAG 检索器。

        1. 加载文档并切块
        2. 尝试从磁盘加载缓存索引
        3. 如果没有缓存，调用 embedding API 构建索引并保存

        返回 True 表示初始化成功。
        """
        # 1. 加载文档
        chunks = load_chunks(self.knowledge_dir)
        if not chunks:
            logger.warning("知识库目录为空或无 .md 文件: %s", self.knowledge_dir)
            return False

        logger.info("加载了 %d 个文档块（来自 %s）", len(chunks), self.knowledge_dir)

        corpus_fingerprint = _corpus_fingerprint(chunks, self.embedding.model)

        # 2. 尝试从磁盘加载；文档、切块方式或embedding模型变化时自动重建
        if self.store.load(self.index_dir, expected_fingerprint=corpus_fingerprint):
            self._ready = True
            logger.info("RAG 索引从缓存加载成功")
            return True

        # 3. 构建新索引
        if not self.embedding.is_configured:
            logger.warning("Embedding API 未配置，RAG 不可用")
            return False

        logger.info("正在调用 Embedding API 构建索引...")
        texts = [truncate_chunk(c.text) for c in chunks]
        embeddings = await self.embedding.embed(texts)

        if embeddings is None:
            logger.warning("Embedding API 调用失败，RAG 不可用")
            return False

        # 过滤掉 None（理论上不会发生，但保险起见）
        valid_pairs = [
            (c, e) for c, e in zip(chunks, embeddings) if e is not None
        ]
        if not valid_pairs:
            logger.warning("所有文档块 embedding 均为 None")
            return False

        valid_chunks, valid_embeddings = zip(*valid_pairs)
        self.store.build(list(valid_chunks), list(valid_embeddings))

        # 持久化（失败不影响运行，下次启动重新构建即可）
        try:
            self.store.save(self.index_dir, corpus_fingerprint=corpus_fingerprint)
        except Exception as e:
            logger.warning("索引缓存保存失败（不影响运行）: %s", e)

        self._ready = True
        logger.info("RAG 索引构建完成（%d 个文档块）", len(valid_chunks))
        return True

    async def retrieve(self, query: str) -> str:
        """完整检索流程：问题 → 格式化的上下文文本。

        Args:
            query: 用户问题

        Returns:
            格式化的上下文字符串（可直接注入 prompt），
            如果检索不到或 RAG 未就绪，返回空字符串。
        """
        if not self._ready:
            return ""

        _t_total = time.perf_counter()
        _cache_hit = False

        # 1. 查询向量化（带 LRU 缓存：同一问题重复问直接命中，省一次网关调用）
        _t0 = time.perf_counter()
        query_vec = self._query_cache.get(query)
        if query_vec is not None:
            _cache_hit = True
        else:
            query_vec = await self.embedding.embed_query(query)
            if query_vec is not None:
                self._query_cache.put(query, query_vec)
        _t_embed = time.perf_counter() - _t0
        if query_vec is None:
            logger.warning("[RAG] embedding 失败或超时，跳过检索")
            return ""

        # 2. FAISS 检索（本地内存，应该 <10ms）
        _t0 = time.perf_counter()
        results = self.store.search(query_vec, top_k=self.search_top_k)
        _t_faiss = time.perf_counter() - _t0
        if not results:
            return ""

        # 中文短问题仅靠向量相似度容易被“关键链接/概述”等泛化块干扰。
        # 用本地关键词覆盖度对候选做轻量重排，不增加任何API调用。
        if self.hybrid_enabled:
            results = self._hybrid_rerank(query, results)

        # 3. Reranker 重排序（可选，走网关 API 通常 2-10s，是主要瓶颈）
        _t_rerank = 0.0
        if self.reranker and self.reranker.is_configured:
            _t0 = time.perf_counter()
            results = await self.reranker.rerank(query, results, top_n=self.top_k)
            _t_rerank = time.perf_counter() - _t0
        else:
            results = results[: self.top_k]

        # 4. 格式化为上下文
        context = self._format_context(results)
        _t_total_elapsed = time.perf_counter() - _t_total

        logger.info(
            "[RAG 耗时] total=%.2fs embed=%.2fs%s faiss=%.3fs rerank=%.2fs ctx_chars=%d",
            _t_total_elapsed,
            _t_embed,
            "(cache)" if _cache_hit else "",
            _t_faiss,
            _t_rerank,
            len(context),
        )
        return context

    @staticmethod
    def _lexical_terms(text: str) -> set[str]:
        lowered = text.lower()
        terms = set(re.findall(r"[a-z0-9_+.-]{2,}", lowered))
        stop_terms = {"怎么", "什么", "可以", "是否", "一下", "我的", "平台", "作业"}
        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
            for size in (2, 3):
                terms.update(
                    segment[index : index + size]
                    for index in range(len(segment) - size + 1)
                )
        return terms - stop_terms

    def _hybrid_rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        query_terms = self._lexical_terms(query)
        if not query_terms:
            return results

        rescored: list[SearchResult] = []
        for result in results:
            heading_terms = self._lexical_terms(result.chunk.heading)
            body_terms = self._lexical_terms(result.chunk.text)
            heading_coverage = len(query_terms & heading_terms) / len(query_terms)
            body_coverage = len(query_terms & body_terms) / len(query_terms)
            lexical_score = 0.7 * heading_coverage + 0.3 * body_coverage
            rescored.append(
                SearchResult(
                    chunk=result.chunk,
                    score=result.score + self.lexical_weight * lexical_score,
                )
            )
        return sorted(rescored, key=lambda item: item.score, reverse=True)

    def _format_context(self, results: list[SearchResult]) -> str:
        """将检索结果格式化为 prompt 上下文。"""
        if not results:
            return ""

        parts = ["【平台文档参考 — 以下信息来自平台官方文档，请优先基于此回答】"]

        for r in results:
            source = r.chunk.source_file.replace(".md", "")
            heading = r.chunk.heading or "概述"
            parts.append(f"\n--- 来源: {source} > {heading} (相关度: {r.score:.2f}) ---")
            # 限制每个块的长度，避免 prompt 过长
            text = truncate_chunk(r.chunk.text, max_chars=800)
            parts.append(text)

        parts.append("\n【回答规则】")
        parts.append("- 优先引用上述文档中的信息")
        parts.append("- 如果文档中没有相关信息，诚实说明")
        parts.append("- 给出可以直接复制粘贴的命令")

        return "\n".join(parts)
