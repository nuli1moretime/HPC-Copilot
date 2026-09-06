"""大模型客户端。

使用 OpenAI 兼容接口（/v1/chat/completions），
将规则引擎的诊断结果转化为初学者友好的解释和操作建议。

设计原则：
- 平台和规则提供事实，大模型负责理解和表达
- 不让大模型决定平台规则，只让它组织语言
- API 不可用时优雅降级，不阻塞核心诊断功能
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

from .models import AgentResponse, DiagnosisResult

logger = logging.getLogger("hpc-copilot.llm")

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
        # 按需创建并复用连接池，避免一次对话中的每次请求都重新握手。
        self._async_client: Optional[httpx.AsyncClient] = None

        # 额外 body 参数（JSON 字符串），用于关闭推理模型 thinking 等模型特定开关。
        # 例：LLM_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'  (Qwen3)
        #     LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}'                   (Claude)
        #     LLM_EXTRA_BODY='{"enable_thinking":false}'                          (部分网关)
        _extra = os.getenv("LLM_EXTRA_BODY", "").strip()
        self.extra_body: dict = {}
        if _extra:
            try:
                import json as _json
                self.extra_body = _json.loads(_extra)
            except Exception:
                import logging as _logging
                _logging.getLogger("hpc-copilot.llm").warning(
                    "LLM_EXTRA_BODY 不是合法 JSON，已忽略: %r", _extra,
                )

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient()
        return self._async_client

    async def aclose(self) -> None:
        """关闭异步连接池；WebSocket 会话结束时调用。"""
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base and self.api_key)

    def chat(self, system_prompt: str, user_message: str) -> str:
        """通用对话接口：发送 system + user 消息，返回纯文本回复。"""
        if not self.is_configured:
            return "⚠️ 大模型未配置。"

        response = httpx.post(
            f"{self.api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.5,
                "max_tokens": 1024,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[list[dict]] = None,
        temperature: float = 0.5,
        max_tokens: int = 1024,
    ):
        """流式对话接口：逐块 yield 文本片段（SSE streaming）。

        Args:
            system_prompt: 系统提示词
            user_message: 当前用户消息
            history: 历史对话（[{"role": "user"/"assistant", "content": ...}]），
                用于多轮对话保留上下文
            temperature: 生成温度
            max_tokens: 最大生成 token 数
        """
        if not self.is_configured:
            yield "⚠️ 大模型未配置。"
            return

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # 分离超时：connect 快失败，read 给足以容忍慢流
        _timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)

        # 推理模型检测：DeepSeek-R1 / QwQ / Qwen3-thinking 等会在 delta.reasoning_content
        # 里输出思维链 token，这些 token 用户看不到但实实在在烧时间。
        # 第一次检测到时打 WARNING，方便定位"为什么这么慢"。
        _reasoning_chars = 0
        _reasoning_warned = False
        _usage: dict | None = None

        client = self._get_async_client()
        async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                    # 让网关在最后一个 chunk 带上 token 用量统计
                    "stream_options": {"include_usage": True},
                    # 模型特定开关（如关闭推理模型 thinking），来自 LLM_EXTRA_BODY
                    **self.extra_body,
                },
                timeout=_timeout,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]  # 去掉 "data: " 前缀
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # 用量统计（通常在最后一个 chunk，choices 为空）
                    if chunk_data.get("usage"):
                        _usage = chunk_data["usage"]

                    choices = chunk_data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}

                    # 推理模型的思维链 token（用户不可见，但烧时间）
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        _reasoning_chars += len(reasoning)
                        if not _reasoning_warned:
                            import logging as _logging
                            _logging.getLogger("hpc-copilot.llm").warning(
                                "检测到推理模型思维链（reasoning_content）！模型=%s。"
                                "思维链 token 用户看不到但会显著拖慢响应。"
                                "建议在 .env 里换一个非推理模型（如 qwen-turbo / glm-4-flash / gpt-4o-mini），"
                                "或传 extra body 关闭 thinking。",
                                self.model,
                            )
                            _reasoning_warned = True

                    content = delta.get("content", "")
                    if content:
                        yield content

        # 流结束：打用量统计，帮助定位慢的原因
        if _usage or _reasoning_chars:
            import logging as _logging
            _logging.getLogger("hpc-copilot.llm").info(
                "[LLM 用量] model=%s, reasoning_chars=%d, usage=%s",
                self.model, _reasoning_chars, _usage,
            )

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> dict:
        """非流式对话 + 工具调用（OpenAI function-calling 协议）。

        Args:
            messages: 完整的消息列表（含 system/user/assistant/tool）
            tools: OpenAI 格式的工具定义列表
            temperature: 生成温度
            max_tokens: 最大生成 token 数

        Returns:
            API 原始 JSON 响应 dict（含 choices[0].message）

        Raises:
            httpx.HTTPStatusError: API 返回非 2xx
            Exception: 网络/超时等错误
        """
        if not self.is_configured:
            raise RuntimeError("大模型未配置（缺少 api_base 或 api_key）")

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            # 模型特定开关（如关闭推理模型 thinking），来自 LLM_EXTRA_BODY
            **self.extra_body,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        client = self._get_async_client()
        response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60.0,
            )
        response.raise_for_status()
        return response.json()

    async def chat_with_tools_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ):
        """流式对话 + 工具调用。

        逐块 yield 事件 dict：
        - {"type": "content", "text": "..."}: 普通文本片段（可实时推给用户）
        - {"type": "tool_calls", "tool_calls": [...]}: 一次完整的 tool_calls 列表
          （在所有 arguments 拼接完成后一次性 yield，格式与非流式 API 一致）
        - {"type": "done"}: 流结束

        如果 LLM 决定调用工具，通常先 yield tool_calls（content 为空）；
        如果 LLM 决定直接回答，则 yield 一串 content 事件（不会 yield tool_calls）。
        """
        if not self.is_configured:
            raise RuntimeError("大模型未配置（缺少 api_base 或 api_key）")

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            # 模型特定开关（如关闭推理模型 thinking），来自 LLM_EXTRA_BODY
            **self.extra_body,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # 按 index 累积 tool_calls（arguments 会分多块到达）
        tool_calls_acc: dict[int, dict] = {}
        # 推理模型思维链检测（同 chat_stream）
        _reasoning_chars = 0
        _reasoning_warned = False
        _usage: dict | None = None

        # 分离超时：connect 快失败（15s），read 给足（120s，per-chunk 间隔），
        # 避免网关不可达时也要等 120s 才报错。
        _timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
        client = self._get_async_client()
        async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=_timeout,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("usage"):
                        _usage = chunk["usage"]

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    # 推理模型思维链（用户不可见但烧时间）
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        _reasoning_chars += len(reasoning)
                        if not _reasoning_warned:
                            logger.warning(
                                "[agent 路径] 检测到推理模型思维链！model=%s。"
                                "建议换非推理模型或设 LLM_EXTRA_BODY 关闭 thinking。",
                                self.model,
                            )
                            _reasoning_warned = True

                    # 1) 普通文本增量
                    content = delta.get("content")
                    if content:
                        yield {"type": "content", "text": content}

                    # 2) 工具调用增量（按 index 累积）
                    tcs = delta.get("tool_calls")
                    if tcs:
                        for tc in tcs:
                            idx = tc.get("index", 0)
                            slot = tool_calls_acc.setdefault(idx, {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]

        if _usage or _reasoning_chars:
            logger.info(
                "[LLM 用量/agent] model=%s, reasoning_chars=%d, usage=%s",
                self.model, _reasoning_chars, _usage,
            )

        # 流结束：如果累积了 tool_calls，一次性 yield 完整列表
        if tool_calls_acc:
            ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
            yield {"type": "tool_calls", "tool_calls": ordered}

        yield {"type": "done"}

    async def explain_diagnosis_async(
        self,
        diagnosis: DiagnosisResult,
        raw_log: str,
        explanation_hint: str = "",
    ) -> Optional[AgentResponse]:
        """异步版本：将诊断结果交给大模型，生成初学者友好的解释。"""
        if not self.is_configured:
            return None

        user_message = self._build_user_message(diagnosis, raw_log, explanation_hint)

        try:
            client = self._get_async_client()
            response = await client.post(
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

    def explain_diagnosis(
        self,
        diagnosis: DiagnosisResult,
        raw_log: str,
        explanation_hint: str = "",
    ) -> Optional[AgentResponse]:
        """同步版本（兼容旧代码）。"""
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
            lines = [line for line in lines if not line.strip().startswith("```")]
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
