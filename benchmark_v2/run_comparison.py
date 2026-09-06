"""在同一模型上运行三组离线回答对照并保存原始输出。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.diagnostics import DiagnosticsEngine  # noqa: E402
from backend.core.llm_client import LLMClient  # noqa: E402
from backend.main import CHAT_SYSTEM_PROMPT  # noqa: E402
from backend.rag.embedding import EmbeddingService  # noqa: E402
from backend.rag.prompts import build_rag_prompt  # noqa: E402
from backend.rag.reranker import Reranker  # noqa: E402
from backend.rag.retriever import Retriever  # noqa: E402
from core import create_freeze_manifest, load_yaml, validate_case_set  # noqa: E402
from render_report import build_report  # noqa: E402
from score_results import score_all  # noqa: E402


SYSTEMS = ("llm_only", "llm_rag", "hpc_copilot")


def load_connection_config() -> dict[str, str]:
    path = PROJECT_ROOT / "backend" / "data" / "connection_config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {
        "api_base": os.getenv("LLM_API_BASE", "") or str(data.get("llm_api_base", "")),
        "api_key": os.getenv("LLM_API_KEY", "") or str(data.get("llm_api_key", "")),
        "model": os.getenv("LLM_MODEL_NAME", "") or str(data.get("llm_model", "")),
    }


async def build_retriever(config: dict[str, str]) -> Retriever:
    rag_cfg = yaml.safe_load(
        (PROJECT_ROOT / "backend" / "config" / "rag_config.yaml").read_text(encoding="utf-8")
    )
    embedding = EmbeddingService(
        api_base=config["api_base"],
        api_key=config["api_key"],
        model=rag_cfg.get("embedding", {}).get("model", "qwen3-embedding"),
    )
    reranker = None
    reranker_cfg = rag_cfg.get("reranker", {})
    if reranker_cfg.get("enabled", False):
        reranker = Reranker(
            api_base=config["api_base"],
            api_key=config["api_key"],
            model=reranker_cfg.get("model", "qwen3-reranker"),
        )
    retrieval_cfg = rag_cfg.get("retrieval", {})
    retriever = Retriever(
        knowledge_dir=PROJECT_ROOT / rag_cfg.get("knowledge_dir", "docs/knowledge"),
        index_dir=PROJECT_ROOT / rag_cfg.get("index_dir", "data/rag_index"),
        embedding_service=embedding,
        reranker=reranker,
        top_k=int(retrieval_cfg.get("top_k", 3)),
        search_top_k=int(retrieval_cfg.get("search_top_k", 10)),
        hybrid_enabled=bool(retrieval_cfg.get("hybrid_enabled", True)),
        lexical_weight=float(retrieval_cfg.get("lexical_weight", 0.35)),
    )
    if not await retriever.initialize():
        raise RuntimeError("RAG 初始化失败，请先检查API配置和索引")
    return retriever


def diagnosis_context(case: dict[str, Any], engine: DiagnosticsEngine) -> str:
    if case.get("category") != "diagnosis":
        return ""
    diagnosis = engine.diagnose(case["prompt"])
    evidence = "；".join(diagnosis.evidence) or "无确定证据"
    commands = "；".join(diagnosis.suggested_commands) or "无"
    return (
        "\n\n【确定性诊断工具返回】\n"
        f"错误类型：{diagnosis.error_type.value}\n"
        f"根因：{diagnosis.root_cause_brief}\n"
        f"证据：{evidence}\n"
        f"建议命令：{commands}\n"
        "请把工具结果作为可信事实；信息不足时明确说明。"
    )


def benchmark_prompt(case: dict[str, Any]) -> str:
    prompt = case["prompt"]
    if case.get("category") == "diagnosis":
        prompt += (
            "\n\n为了统一评测，请在回答第一行写成 "
            "[DIAGNOSIS_TYPE: 错误类型]，然后再向初学者解释。"
        )
    return prompt


async def collect_stream(client: LLMClient, system_prompt: str, user_prompt: str) -> str:
    chunks: list[str] = []
    async for chunk in client.chat_stream(
        system_prompt=system_prompt,
        user_message=user_prompt,
        temperature=0.0,
        max_tokens=1400,
    ):
        chunks.append(chunk)
    return "".join(chunks).strip()


async def collect_with_retry(
    client: LLMClient,
    system_prompt: str,
    user_prompt: str,
    max_retries: int,
    retry_base_seconds: float,
) -> str:
    """429/5xx 时退避重试，避免把网关限流误记成模型失败。"""
    for attempt in range(max_retries + 1):
        try:
            return await collect_stream(client, system_prompt, user_prompt)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status == 429 or 500 <= status < 600
            if not retryable or attempt >= max_retries:
                raise
            retry_after = exc.response.headers.get("Retry-After", "").strip()
            try:
                wait_seconds = float(retry_after)
            except ValueError:
                wait_seconds = retry_base_seconds * (attempt + 1)
            wait_seconds = min(max(wait_seconds, 1.0), 45.0)
            print(f"    API返回{status}，{wait_seconds:.0f}秒后重试（{attempt + 1}/{max_retries}）")
            await asyncio.sleep(wait_seconds)
    raise RuntimeError("重试循环异常结束")


async def run(args: argparse.Namespace) -> int:
    case_data = load_yaml(args.cases)
    errors = validate_case_set(case_data)
    if errors:
        print("题目校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    freeze_manifest = None
    if args.freeze_manifest:
        freeze_manifest = json.loads(args.freeze_manifest.read_text(encoding="utf-8"))
        current = create_freeze_manifest(PROJECT_ROOT, args.cases.resolve())
        if freeze_manifest.get("case_file_sha256") != current["case_file_sha256"]:
            print("拒绝运行：正式题目文件与冻结清单不一致")
            return 4
        if freeze_manifest.get("aggregate_sha256") != current["aggregate_sha256"]:
            print("拒绝运行：代码、知识库或评测程序在冻结后发生变化")
            return 4

    config = load_connection_config()
    if config.get("api_base") and config.get("model") and not config.get("api_key") and sys.stdin.isatty():
        config["api_key"] = getpass.getpass("请输入大模型 API Key（不会显示或保存）：").strip()
    if not all(config.get(key) for key in ("api_base", "api_key", "model")):
        print(
            "缺少大模型连接配置。API地址和模型名可沿用设置页配置；"
            "API Key请在交互终端输入，或临时设置LLM_API_KEY环境变量。"
        )
        return 2

    requested_systems = [item.strip() for item in args.systems.split(",") if item.strip()]
    unknown_systems = [item for item in requested_systems if item not in SYSTEMS]
    if unknown_systems:
        print(f"未知系统：{', '.join(unknown_systems)}")
        return 3

    needs_rag = any(item in {"llm_rag", "hpc_copilot"} for item in requested_systems)
    retriever = await build_retriever(config) if needs_rag else None
    engine = DiagnosticsEngine()
    client = LLMClient(
        api_base=config["api_base"],
        api_key=config["api_key"],
        model=config["model"],
        timeout=120.0,
    )

    output = args.output or (
        PROJECT_ROOT / "benchmark_v2" / "results" / f"{case_data['suite_id']}_responses.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    prompt_sha256 = hashlib.sha256(CHAT_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    if args.resume and output.exists():
        payload = json.loads(output.read_text(encoding="utf-8"))
        if payload.get("suite_id") != case_data["suite_id"] or payload.get("model") != config["model"]:
            print("拒绝断点续跑：已有结果的题目集或模型与本次不一致")
            return 6
        if payload.get("base_system_prompt_sha256") != prompt_sha256:
            print("拒绝断点续跑：基础提示词已经变化，请改用新的输出文件或添加 --no-resume")
            return 6
        print(f"读取已有结果并断点续跑：{output}")
    else:
        payload = {
            "schema_version": 1,
            "suite_id": case_data["suite_id"],
            "suite_status": case_data["status"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "model": config["model"],
            "temperature": 0.0,
            "base_system_prompt_sha256": prompt_sha256,
            "system_definitions": {
                "llm_only": "相同基础提示词，不注入检索内容和诊断工具结果",
                "llm_rag": "基础提示词加生产RAG检索内容",
                "hpc_copilot": "RAG基础上，诊断题再注入确定性规则结果；非诊断题的离线输入与llm_rag相同",
            },
            "freeze_aggregate_sha256": freeze_manifest.get("aggregate_sha256") if freeze_manifest else None,
            "systems": {},
        }

    try:
        for system_name in requested_systems:
            print(f"\n[{system_name}] 开始，共 {len(case_data['cases'])} 题")
            existing_system = payload.get("systems", {}).get(system_name, {})
            system_results: dict[str, Any] = dict(existing_system.get("cases", {}))
            for index, case in enumerate(case_data["cases"], start=1):
                previous = system_results.get(case["id"], {})
                if args.resume and previous.get("response") and not previous.get("error"):
                    print(f"  {index:02d}/{len(case_data['cases'])} {case['id']} 已完成，跳过")
                    continue
                system_prompt = CHAT_SYSTEM_PROMPT
                rag_context = ""
                if system_name in {"llm_rag", "hpc_copilot"} and retriever:
                    rag_context = await retriever.retrieve(case["prompt"])
                    system_prompt = build_rag_prompt(system_prompt, rag_context)
                if system_name == "hpc_copilot":
                    system_prompt += diagnosis_context(case, engine)

                started = time.perf_counter()
                try:
                    if args.request_delay > 0:
                        await asyncio.sleep(args.request_delay)
                    response = await collect_with_retry(
                        client,
                        system_prompt,
                        benchmark_prompt(case),
                        max_retries=args.max_retries,
                        retry_base_seconds=args.retry_base_seconds,
                    )
                    error = None
                except Exception as exc:  # 单题失败仍保留其余原始结果
                    response = ""
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = time.perf_counter() - started
                system_results[case["id"]] = {
                    "response": response,
                    "error": error,
                    "elapsed_seconds": round(elapsed, 3),
                    "rag_context_sha256": hashlib.sha256(rag_context.encode("utf-8")).hexdigest()
                    if rag_context
                    else None,
                    "rag_context_chars": len(rag_context),
                }
                print(f"  {index:02d}/{len(case_data['cases'])} {case['id']} ({elapsed:.1f}s){' 失败' if error else ''}")

                payload.setdefault("systems", {})[system_name] = {"cases": system_results}
                output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            payload["systems"][system_name] = {"cases": system_results}
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await client.aclose()

    if freeze_manifest:
        after = create_freeze_manifest(PROJECT_ROOT, args.cases.resolve())
        if after["aggregate_sha256"] != freeze_manifest.get("aggregate_sha256"):
            print("警告：评测运行期间文件发生变化，本次正式结果无效")
            return 5

    scored = score_all(case_data, payload)
    scored_output = output.with_name(output.stem.replace("_responses", "_scored") + ".json")
    report_output = scored_output.with_suffix(".md")
    scored_output.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    report_output.write_text(build_report(scored), encoding="utf-8")
    print(f"\n原始回答已保存：{output}")
    print(f"客观评分已保存：{scored_output}")
    print(f"可读报告已生成：{report_output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行HPC Copilot三组对照实验")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--systems", default=",".join(SYSTEMS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--request-delay", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-base-seconds", type=float, default=15.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
