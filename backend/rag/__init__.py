"""RAG 模块 — 知识库检索增强生成。

提供文档加载、向量化、检索和 prompt 注入功能，
让 LLM 在回答用户问题时能参考真实的平台文档。
"""

from .retriever import Retriever

__all__ = ["Retriever"]
