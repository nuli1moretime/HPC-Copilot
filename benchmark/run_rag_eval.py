"""RAG 检索质量评测脚本。

评测指标：
- Hit Rate@K: top-K 结果中是否命中了期望的文档块
- MRR (Mean Reciprocal Rank): 第一个命中结果的排名倒数的均值
- 逐条详细输出：命中的 chunk、分数、是否匹配期望

运行方式（在项目根目录执行）：
    python benchmark/run_rag_eval.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import yaml

# 确保能导入 backend 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from rag.embedding import EmbeddingService
from rag.loader import load_chunks
from rag.reranker import Reranker
from rag.retriever import Retriever
from rag.vector_store import VectorStore


# ─── 配置 ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_CASES_FILE = PROJECT_ROOT / "benchmark" / "rag_eval_cases.yaml"
KNOWLEDGE_DIR = PROJECT_ROOT / "docs" / "knowledge"

# 使用与生产相同的 embedding 配置
# 如果连接配置存在，从中读取 API 信息
CONFIG_FILE = PROJECT_ROOT / "backend" / "data" / "connection_config.json"


def load_connection_config() -> dict:
    """从连接配置文件读取 API 信息。"""
    if CONFIG_FILE.exists():
        import json
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {
            "api_base": data.get("llm_api_base", ""),
            "api_key": data.get("llm_api_key", ""),
        }
    return {"api_base": "", "api_key": ""}


def load_eval_cases() -> list[dict]:
    """加载评测用例。"""
    data = yaml.safe_load(EVAL_CASES_FILE.read_text(encoding="utf-8"))
    return data.get("test_cases", [])


def check_hit(result_chunks: list, expected_hits: list[dict]) -> tuple[bool, int]:
    """检查检索结果是否命中期望文档。

    Returns:
        (hit: bool, rank: int) — hit 表示是否命中，rank 表示第一个命中的排名（1-based，0=未命中）
    """
    for rank, result in enumerate(result_chunks, start=1):
        chunk = result.chunk
        for expected in expected_hits:
            file_match = expected["file"] in chunk.source_file
            heading_match = expected["heading_contains"].lower() in chunk.heading.lower()
            if file_match and heading_match:
                return True, rank
    return False, 0


async def run_eval():
    """运行 RAG 检索质量评测。"""
    # 加载配置
    conn_cfg = load_connection_config()
    api_base = conn_cfg["api_base"]
    api_key = conn_cfg["api_key"]

    if not api_base or not api_key:
        print("[ERROR] 未找到连接配置，请先启动后端并配置 API")
        return

    # 加载评测用例
    cases = load_eval_cases()
    print(f"=" * 60)
    print(f"RAG 检索质量评测")
    print(f"=" * 60)
    print(f"评测用例数: {len(cases)}")
    print(f"知识库目录: {KNOWLEDGE_DIR}")
    print(f"Embedding API: {api_base}")
    print()

    # 初始化 Retriever
    print("正在初始化 RAG 检索器...")
    embedding_service = EmbeddingService(
        api_base=api_base,
        api_key=api_key,
        model="qwen3-embedding",
    )

    retriever = Retriever(
        knowledge_dir=KNOWLEDGE_DIR,
        index_dir=PROJECT_ROOT / "data" / "rag_index",
        embedding_service=embedding_service,
        reranker=None,  # 评测时不用 reranker，测原始 FAISS 效果
        top_k=5,
        search_top_k=10,
    )

    success = await retriever.initialize()
    if not success:
        print("[ERROR] RAG 初始化失败")
        return

    print(f"RAG 初始化成功: {len(retriever.store._chunks)} 个文档块, 维度 {retriever.store._dim}")
    print()

    # 逐条评测
    hits = 0
    total = len(cases)
    reciprocal_ranks = []
    detailed_results = []

    print("-" * 60)
    print(f"{'ID':<20} {'Hit':>4} {'Rank':>5} {'Top-1 Source':<40}")
    print("-" * 60)

    start_time = time.time()

    for case in cases:
        qid = case["id"]
        question = case["question"]
        expected = case["expected_hits"]

        # 请求间隔，避免 API 限流
        await asyncio.sleep(0.5)

        # 检索
        query_vec = await embedding_service.embed_query(question)
        if query_vec is None:
            print(f"{qid:<20} [SKIP] embedding 失败")
            continue

        results = retriever.store.search(query_vec, top_k=5)
        hit, rank = check_hit(results, expected)

        if hit:
            hits += 1
            reciprocal_ranks.append(1.0 / rank)

        # 输出
        top1 = results[0] if results else None
        top1_info = f"{top1.chunk.source_file} > {top1.chunk.heading[:25]}" if top1 else "N/A"
        hit_str = "PASS" if hit else "FAIL"
        rank_str = str(rank) if hit else "-"

        print(f"{qid:<20} {hit_str:>4} {rank_str:>5} {top1_info:<40}")

        detailed_results.append({
            "id": qid,
            "question": question,
            "hit": hit,
            "rank": rank,
            "top_results": [
                {
                    "file": r.chunk.source_file,
                    "heading": r.chunk.heading,
                    "score": r.score,
                }
                for r in results[:3]
            ],
        })

    elapsed = time.time() - start_time

    # 汇总
    hit_rate = hits / total if total > 0 else 0
    mrr = sum(reciprocal_ranks) / total if total > 0 else 0

    print()
    print("=" * 60)
    print(f"评测结果汇总")
    print(f"=" * 60)
    print(f"总用例数:    {total}")
    print(f"命中数:      {hits}/{total}")
    print(f"Hit Rate@5:  {hit_rate:.1%}")
    print(f"MRR:         {mrr:.4f}")
    print(f"耗时:        {elapsed:.1f}s (含 API 调用)")
    print()

    # 按类别统计
    categories = {
        "分区/QoS": ["q_partition", "q_qos"],
        "作业提交": ["q_submit"],
        "环境配置": ["q_conda", "q_module"],
        "GPU相关": ["q_gpu", "q_pytorch"],
        "超时/内存": ["q_timeout", "q_oom"],
        "平台基础": ["q_basic"],
        "权限/错误": ["q_permission", "q_disk"],
        "脚本规范": ["q_script"],
        "领域场景": ["q_domain"],
        "排查流程": ["q_debug"],
    }

    print("按类别统计:")
    for cat_name, prefixes in categories.items():
        cat_cases = [r for r in detailed_results if any(r["id"].startswith(p) for p in prefixes)]
        if cat_cases:
            cat_hits = sum(1 for r in cat_cases if r["hit"])
            print(f"  {cat_name:<12} {cat_hits}/{len(cat_cases)} ({cat_hits/len(cat_cases):.0%})")

    # 保存详细结果
    output_file = PROJECT_ROOT / "benchmark" / "rag_eval_results.yaml"
    output_data = {
        "summary": {
            "total": total,
            "hits": hits,
            "hit_rate": f"{hit_rate:.1%}",
            "mrr": f"{mrr:.4f}",
            "elapsed_seconds": round(elapsed, 1),
        },
        "details": detailed_results,
    }
    output_file.write_text(
        yaml.dump(output_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"\n详细结果已保存到: {output_file}")


if __name__ == "__main__":
    asyncio.run(run_eval())
