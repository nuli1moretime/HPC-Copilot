"""按生产配置评估RAG，同时报告严格章节命中和宽松文件命中。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "benchmark_v2"))

from run_comparison import build_retriever, load_connection_config  # noqa: E402

import yaml  # noqa: E402


def first_hit_rank(results, expected: list[dict], strict_heading: bool) -> int:
    for rank, result in enumerate(results, start=1):
        source_file = result.chunk.source_file.lower()
        heading = result.chunk.heading.lower()
        for target in expected:
            if str(target["file"]).lower() not in source_file:
                continue
            if strict_heading and str(target["heading_contains"]).lower() not in heading:
                continue
            return rank
    return 0


def metric_summary(ranks: list[int], k: int) -> dict:
    total = len(ranks)
    hits = sum(0 < rank <= k for rank in ranks)
    reciprocal = sum(1 / rank for rank in ranks if 0 < rank <= k)
    return {
        "hits": hits,
        "total": total,
        "hit_rate": round(hits / total, 4) if total else None,
        "mrr": round(reciprocal / total, 4) if total else None,
    }


def build_markdown(data: dict) -> str:
    metrics = data["metrics"]
    hybrid_enabled = bool(data.get("hybrid_enabled", False))
    lines = [
        "# RAG检索开发集评测",
        "",
        "> 这是公开开发集结果，用于改进切块和检索，不能作为最终竞赛成绩。",
        "",
        "## 运行配置",
        "",
        f"- Embedding模型：`{data.get('embedding_model') or '未记录'}`",
        f"- 生产Top-K：{data['production_top_k']}",
        f"- FAISS候选数：{data['search_top_k']}",
        f"- 本地混合重排：{'开启' if hybrid_enabled else '关闭'}",
        f"- 关键词权重：{data.get('lexical_weight', '未记录') if hybrid_enabled else '不适用'}",
        f"- Reranker：{'开启' if data['reranker_enabled'] else '关闭'}",
        f"- Embedding失败：{data['embedding_failures']}",
        f"- 用时：{data['elapsed_seconds']:.2f}秒",
        "",
        "## 核心指标",
        "",
        "| 指标 | 命中 | Hit Rate | MRR |",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "strict_section_at_1": "严格章节@1",
        "strict_section_at_3": "严格章节@3",
        "file_at_1": "正确文件@1",
        "file_at_3": "正确文件@3",
    }
    for key, label in labels.items():
        metric = metrics[key]
        lines.append(
            f"| {label} | {metric['hits']}/{metric['total']} | "
            f"{metric['hit_rate']:.1%} | {metric['mrr']:.3f} |"
        )

    file_misses = [item for item in data["details"] if item.get("file_rank", 0) == 0]
    section_only_misses = [
        item
        for item in data["details"]
        if item.get("strict_section_rank", 0) == 0 and item.get("file_rank", 0) > 0
    ]
    lines.extend([
        "",
        "## 未命中正确文件的题目",
        "",
    ])
    if file_misses:
        for item in file_misses:
            top = item.get("production_results", [])
            top_text = f"{top[0]['file']} > {top[0]['heading']}" if top else "无结果"
            lines.append(f"- `{item['id']}`：{item['question']}；Top-1为 `{top_text}`")
    else:
        lines.append("全部题目都在Top-3中找到了正确文件。")

    lines.extend([
        "",
        "## 找对文件但未命中指定章节",
        "",
    ])
    if section_only_misses:
        for item in section_only_misses:
            lines.append(f"- `{item['id']}`：{item['question']}（正确文件排名{item['file_rank']}）")
    else:
        lines.append("没有此类题目。")

    lines.extend([
        "",
        "## 指标解释",
        "",
        "- 严格章节命中要求文件名和章节标题都符合预设答案，适合检查切块质量。",
        "- 正确文件命中只要求来源文件正确，允许同一文档中的相邻章节提供等价信息。",
        "- 检索命中不等于最终回答正确，最终还需结合三组回答对照和真实平台执行结果。",
        "",
    ])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    config = load_connection_config()
    if config.get("api_base") and config.get("model") and not config.get("api_key") and sys.stdin.isatty():
        config["api_key"] = getpass.getpass("请输入大模型 API Key（不会显示或保存）：").strip()
    if not config.get("api_base") or not config.get("api_key"):
        print("缺少Embedding API地址或Key，未发出请求。")
        return 2

    case_data = yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}
    cases = case_data.get("test_cases", [])
    retriever = await build_retriever(config)
    strict_ranks: list[int] = []
    file_ranks: list[int] = []
    details = []
    failures = 0
    started = time.perf_counter()

    vectors = []
    questions = [case["question"] for case in cases]
    for start in range(0, len(questions), args.batch_size):
        batch = questions[start : start + args.batch_size]
        batch_vectors = None
        for attempt in range(args.max_retries + 1):
            batch_vectors = await retriever.embedding.embed(batch)
            if batch_vectors is not None and len(batch_vectors) == len(batch):
                break
            if attempt < args.max_retries:
                wait_seconds = min(args.retry_base_seconds * (attempt + 1), 45.0)
                print(f"Embedding批次失败，{wait_seconds:.0f}秒后重试（{attempt + 1}/{args.max_retries}）")
                await asyncio.sleep(wait_seconds)
        if batch_vectors is None or len(batch_vectors) != len(batch):
            batch_vectors = [None] * len(batch)
        vectors.extend(batch_vectors)
        print(f"Embedding进度：{min(start + len(batch), len(questions))}/{len(questions)}")
        if start + len(batch) < len(questions) and args.request_delay > 0:
            await asyncio.sleep(args.request_delay)

    for index, (case, vector) in enumerate(zip(cases, vectors), start=1):
        if vector is None:
            strict_ranks.append(0)
            file_ranks.append(0)
            failures += 1
            details.append({"id": case["id"], "error": "embedding_failed"})
            continue
        candidates = retriever.store.search(vector, top_k=max(5, retriever.search_top_k))
        # 与生产 Retriever.retrieve() 保持同一排序链路。评测脚本直接调用
        # VectorStore 是为了复用批量 embedding，但不能因此绕过本地混合重排。
        if retriever.hybrid_enabled:
            candidates = retriever._hybrid_rerank(case["question"], candidates)
        if retriever.reranker and retriever.reranker.is_configured:
            production = await retriever.reranker.rerank(
                case["question"], candidates, top_n=retriever.top_k
            )
        else:
            production = candidates[: retriever.top_k]
        strict_rank = first_hit_rank(production, case["expected_hits"], strict_heading=True)
        file_rank = first_hit_rank(production, case["expected_hits"], strict_heading=False)
        strict_ranks.append(strict_rank)
        file_ranks.append(file_rank)
        details.append({
            "id": case["id"],
            "question": case["question"],
            "strict_section_rank": strict_rank,
            "file_rank": file_rank,
            "production_results": [
                {
                    "file": item.chunk.source_file,
                    "heading": item.chunk.heading,
                    "score": item.score,
                }
                for item in production
            ],
        })
        print(f"{index:02d}/{len(cases)} {case['id']} strict={strict_rank or '-'} file={file_rank or '-'}")

    output_data = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.cases.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "embedding_model": retriever.embedding.model,
        "production_top_k": retriever.top_k,
        "search_top_k": retriever.search_top_k,
        "hybrid_enabled": retriever.hybrid_enabled,
        "lexical_weight": retriever.lexical_weight,
        "reranker_enabled": bool(retriever.reranker and retriever.reranker.is_configured),
        "embedding_failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "metrics": {
            "strict_section_at_1": metric_summary(strict_ranks, 1),
            "strict_section_at_3": metric_summary(strict_ranks, 3),
            "file_at_1": metric_summary(file_ranks, 1),
            "file_at_3": metric_summary(file_ranks, 3),
        },
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output = args.output.with_suffix(".md")
    markdown_output.write_text(build_markdown(output_data), encoding="utf-8")
    print("\n生产配置指标：")
    for name, metric in output_data["metrics"].items():
        print(f"- {name}: Hit={metric['hit_rate']:.1%}, MRR={metric['mrr']:.3f}")
    print(f"结果已保存：{args.output}")
    print(f"可读报告已保存：{markdown_output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按生产配置运行RAG开发集评测")
    parser.add_argument(
        "--cases", type=Path, default=PROJECT_ROOT / "benchmark" / "rag_eval_cases.yaml"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "benchmark_v2" / "results" / "retrieval_development_eval.json",
    )
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--request-delay", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-base-seconds", type=float, default=15.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
