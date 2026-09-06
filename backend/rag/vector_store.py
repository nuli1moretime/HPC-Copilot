"""FAISS 向量索引。

使用 faiss-cpu 构建本地向量索引，支持持久化和加载。
归一化后使用 Inner Product = cosine similarity。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

from .loader import DocChunk

logger = logging.getLogger("hpc-copilot.rag.vector_store")


class SearchResult:
    """检索结果。"""

    def __init__(self, chunk: DocChunk, score: float):
        self.chunk = chunk
        self.score = score

    def __repr__(self):
        return f"SearchResult(id={self.chunk.id!r}, score={self.score:.4f})"


class VectorStore:
    """FAISS 向量索引。"""

    def __init__(self):
        self._index = None
        self._chunks: list[DocChunk] = []
        self._dim: int = 0

    @property
    def is_built(self) -> bool:
        return self._index is not None and len(self._chunks) > 0

    def build(self, chunks: list[DocChunk], embeddings: list[list[float]]) -> None:
        """构建 FAISS 索引。

        Args:
            chunks: 文档块列表
            embeddings: 对应的向量列表（需与 chunks 等长）
        """
        import faiss

        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks ({len(chunks)}) 和 embeddings ({len(embeddings)}) 数量不匹配")

        self._chunks = chunks
        self._dim = len(embeddings[0])

        # 转为 numpy 数组并归一化（使 IP = cosine similarity）
        vectors = np.array(embeddings, dtype=np.float32)
        faiss.normalize_L2(vectors)

        # 使用 Inner Product（归一化后等价于 cosine similarity）
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(vectors)

        logger.info("FAISS 索引构建完成: %d 个文档块, 维度 %d", len(chunks), self._dim)

    def search(self, query_vec: list[float], top_k: int = 5) -> list[SearchResult]:
        """向量检索，返回 top-k 个最相似的文档块。"""
        if not self.is_built:
            return []

        import faiss

        # 归一化查询向量
        query = np.array([query_vec], dtype=np.float32)
        faiss.normalize_L2(query)

        # 检索
        k = min(top_k, len(self._chunks))
        scores, indices = self._index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self._chunks):
                results.append(SearchResult(
                    chunk=self._chunks[idx],
                    score=float(score),
                ))

        return results

    def save(self, path: Path, corpus_fingerprint: str | None = None) -> None:
        """持久化索引到磁盘。

        使用 Python 文件 I/O 而非 faiss.write_index，
        因为 faiss 的 C++ 底层在 Windows 下不支持中文路径。
        """
        import faiss

        if not self.is_built:
            logger.warning("索引未构建，跳过保存")
            return

        # 确保目录存在
        path.mkdir(parents=True, exist_ok=True)

        index_file = path / "index.faiss"
        meta_file = path / "metadata.json"

        try:
            # 用 faiss.serialize_index 序列化到内存，再用 Python 写文件
            # 这样绕过 faiss.write_index 的 C++ fopen（不支持中文路径）
            index_bytes = faiss.serialize_index(self._index)
            with open(index_file, "wb") as f:
                f.write(index_bytes)

            # 保存元数据
            metadata = {
                "dim": self._dim,
                "num_chunks": len(self._chunks),
                "corpus_fingerprint": corpus_fingerprint,
                "chunks": [
                    {
                        "id": c.id,
                        "text": c.text,
                        "source_file": c.source_file,
                        "heading": c.heading,
                    }
                    for c in self._chunks
                ],
            }
            meta_file.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("索引已保存到 %s", path)
        except Exception as e:
            logger.warning("保存索引失败: %s", e)

    def load(self, path: Path, expected_fingerprint: str | None = None) -> bool:
        """从磁盘加载索引。返回 True 表示加载成功。"""
        import faiss

        index_file = path / "index.faiss"
        meta_file = path / "metadata.json"

        if not index_file.exists() or not meta_file.exists():
            return False

        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
            if expected_fingerprint is not None and metadata.get("corpus_fingerprint") != expected_fingerprint:
                logger.info("知识库内容或切块方式已变化，忽略旧RAG索引")
                return False

            # 用 Python 读文件 + faiss.deserialize_index，绕过中文路径问题
            with open(index_file, "rb") as f:
                index_bytes = f.read()
            # Windows/Python 下 read() 返回 bytes，而 FAISS 的 Python 包装层
            # 需要 uint8 ndarray。显式转换也兼容不同 faiss-cpu 版本。
            serialized = np.frombuffer(index_bytes, dtype=np.uint8)
            self._index = faiss.deserialize_index(serialized)
            self._dim = self._index.d

            self._chunks = [
                DocChunk(
                    id=c["id"],
                    text=c["text"],
                    source_file=c["source_file"],
                    heading=c["heading"],
                )
                for c in metadata["chunks"]
            ]

            logger.info(
                "从磁盘加载索引: %d 个文档块, 维度 %d",
                len(self._chunks), self._dim,
            )
            return True

        except Exception as e:
            logger.warning("加载索引失败: %s", e)
            return False
