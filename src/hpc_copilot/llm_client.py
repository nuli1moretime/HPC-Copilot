"""大模型客户端。

使用 OpenAI 兼容接口（/v1/chat/completions），
将规则引擎的诊断结果转化为初学者友好的解释和操作建议。

设计原则（来自交接文档）：
- 平台和规则提供事实，大模型负责理解和表达
- 不让大模型决定平台规则，只让它组织语言
- API 不可用时优雅降级，不阻塞核心诊断功能
"""

from __future__ import annotations

import json
import os
from typing import Optional

import httpx

from .models import AgentResponse, DiagnosisResult

_SYSTEM_PROMPT = """\
你是 HPC Copilot，一个帮助算力平台初学者的智能体。
你的用户是刚开始使用学校 HPC 集群、对 Linux 和 Slurm 不熟悉的学生。

你的任务：根据已经确定诊断出的错误类型和证据，用通俗、耐心的语言向用户解释：
1. 发生了什么（用类比或简单语言，不要堆术语）
2. 为什么会这样（根因）
3. 下一步具体怎么做（给出可以直接复制粘贴的命令，并解释每条命令的作用）

规则：
- 不要编造平台信息、分区名称或 API 地址
- 不要建议用户执行危险操作（rm -rf、修改系统文件等）
- 如果信息不足以确定原因，诚实说明并建议用户提供更多日志
- 语气像一个耐心的学长/学姐在旁边指导
- 回答用中文

你必须以 JSON 格式回复，结构如下：
{
  "explanation": "通俗解释发生了什么和为什么",
  "next_steps": ["第一步：...", "第二步：...", ...],
  "warning": "需要特别注意的事项（没有则为 null）"
}
"""


class LLMClient:
    """OpenAI 兼容接口客户端。"""

    def __init__(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_base = (api_base or os.getenv("LLM_API_BASE", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base and self.api_key)

    def explain_diagnosis(
        self,
        diagnosis: DiagnosisResult,
        raw_log: str,
        explanation_hint: str = "",
    ) -> Optional[AgentResponse]:
        """将诊断结果交给大模型，生成初学者友好的解释。

        返回 None 表示 API 不可用或调用失败（优雅降级）。
        """
        if not self.is_configured:
            return None

        user_message = self._build_user_message(diagnosis, raw_log, explanation_hint)

        try:
            response = httpx.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1024,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return self._parse_response(content)
        except (httpx.HTTPError, KeyError, json.JSONDecodeError):
            return None

    def _build_user_message(
        self, diagnosis: DiagnosisResult, raw_log: str, explanation_hint: str
    ) -> str:
        parts = [
            f"【已诊断的错误类型】{diagnosis.error_type.value}",
            f"【置信度】{diagnosis.confidence}",
            f"【根因（规则引擎判断）】{diagnosis.root_cause_brief}",
            f"【证据（日志中命中的行）】",
        ]
        for e in diagnosis.evidence:
            parts.append(f"  - {e}")
        if diagnosis.suggested_commands:
            parts.append("【建议命令（确定性）】")
            for cmd in diagnosis.suggested_commands:
                parts.append(f"  - {cmd}")
        if explanation_hint:
            parts.append(f"【背景提示】{explanation_hint}")
        parts.append(f"\n【用户粘贴的原始日志】\n```\n{raw_log[:2000]}\n```")
        parts.append("\n请根据以上信息，用初学者能懂的语言解释并给出操作步骤。")
        return "\n".join(parts)

    def _parse_response(self, content: str) -> Optional[AgentResponse]:
        """解析大模型返回的 JSON。兼容 markdown 代码块包裹的情况。"""
        text = content.strip()
        # 去掉可能的 ```json ... ``` 包裹
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
            return AgentResponse(
                explanation=data.get("explanation", ""),
                next_steps=data.get("next_steps", []),
                warning=data.get("warning"),
            )
        except (json.JSONDecodeError, KeyError):
            # 如果 JSON 解析失败，把整段文字作为 explanation
            return AgentResponse(
                explanation=content.strip(),
                next_steps=[],
                warning=None,
            )
