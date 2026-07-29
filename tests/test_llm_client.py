"""LLM 客户端测试（不依赖真实 API，测试解析逻辑）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hpc_copilot.llm_client import LLMClient
from hpc_copilot.models import DiagnosisResult, ErrorType


class TestLLMClientParsing:
    """测试大模型返回内容的解析逻辑。"""

    def setup_method(self):
        self.client = LLMClient(api_base="http://fake", api_key="fake-key")

    def test_parse_clean_json(self):
        content = '{"explanation": "你的QoS不对", "next_steps": ["运行sacctmgr"], "warning": null}'
        resp = self.client._parse_response(content)
        assert resp is not None
        assert resp.explanation == "你的QoS不对"
        assert resp.next_steps == ["运行sacctmgr"]
        assert resp.warning is None

    def test_parse_json_in_code_block(self):
        content = '```json\n{"explanation": "test", "next_steps": ["step1"], "warning": "注意"}\n```'
        resp = self.client._parse_response(content)
        assert resp is not None
        assert resp.explanation == "test"
        assert resp.warning == "注意"

    def test_parse_invalid_json_fallback(self):
        content = "这是一段纯文本解释，不是JSON格式。"
        resp = self.client._parse_response(content)
        assert resp is not None
        assert "纯文本解释" in resp.explanation
        assert resp.next_steps == []

    def test_not_configured(self):
        client = LLMClient(api_base="", api_key="")
        assert not client.is_configured

    def test_configured(self):
        assert self.client.is_configured


class TestPromptBuilding:
    """测试发给大模型的 prompt 构建。"""

    def test_build_user_message(self):
        client = LLMClient(api_base="http://fake", api_key="fake-key")
        diagnosis = DiagnosisResult(
            error_type=ErrorType.INVALID_QOS,
            confidence=1.0,
            evidence=["Invalid qos specification"],
            matched_rule="invalid_qos",
            root_cause_brief="QoS 无效",
            suggested_commands=["sacctmgr show assoc"],
        )
        msg = client._build_user_message(diagnosis, "raw log here", "QoS 决定资源权限")
        assert "INVALID_QOS" in msg
        assert "Invalid qos specification" in msg
        assert "sacctmgr show assoc" in msg
        assert "raw log here" in msg
        assert "QoS 决定资源权限" in msg
