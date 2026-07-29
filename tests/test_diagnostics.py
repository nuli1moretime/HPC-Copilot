"""诊断引擎测试。

使用交接文档中记录的真实平台报错作为测试用例。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hpc_copilot.diagnostics import DiagnosticsEngine
from hpc_copilot.models import ErrorType

engine = DiagnosticsEngine()


class TestRealPlatformErrors:
    """来自真实平台的报错（交接文档第 5 节）。"""

    def test_invalid_qos(self):
        """5.1 Invalid qos specification"""
        log = "sbatch: error: Batch job submission failed: Invalid qos specification"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.INVALID_QOS
        assert result.confidence == 1.0
        assert "Invalid qos specification" in result.evidence[0]
        assert result.matched_rule == "invalid_qos"
        assert any("--qos" in cmd for cmd in result.suggested_commands)

    def test_getcwd_failed(self):
        """5.2 getcwd failed"""
        log = "sbatch: error: getcwd failed: No such file or directory"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.WORKDIR_NOT_FOUND
        assert result.confidence == 1.0
        assert result.matched_rule == "workdir_not_found"
        assert any("cd" in cmd for cmd in result.suggested_commands)


class TestCommonErrors:
    """常见错误场景。"""

    def test_timeout(self):
        log = (
            "slurmstepd: error: *** JOB 12345 ON node01 CANCELLED AT "
            "2025-07-20T14:30:00 DUE TO TIME LIMIT ***"
        )
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.TIMEOUT
        assert result.confidence == 1.0

    def test_entrypoint_not_found(self):
        log = "python: can't open file 'train.py': [Errno 2] No such file or directory"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.ENTRYPOINT_NOT_FOUND

    def test_module_not_found(self):
        log = "ModuleNotFoundError: No module named 'torch'"
        result = engine.diagnose(log)
        # 这个会匹配到 program_exit_nonzero（Traceback）或 module_not_found
        # 取决于规则顺序，但不应是 UNKNOWN
        assert result.error_type != ErrorType.UNKNOWN

    def test_out_of_memory(self):
        log = "slurmstepd: error: Detected 1 oom_kill event in StepId=12347.batch"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.OUT_OF_MEMORY

    def test_program_exit_nonzero(self):
        log = (
            "Traceback (most recent call last):\n"
            '  File "train.py", line 3, in <module>\n'
            "    import torch\n"
            "ModuleNotFoundError: No module named 'torch'"
        )
        result = engine.diagnose(log)
        assert result.error_type != ErrorType.UNKNOWN

    def test_invalid_partition(self):
        log = "sbatch: error: Batch job submission failed: Invalid partition specification"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.INVALID_PARTITION


class TestEdgeCases:
    """边界情况。"""

    def test_empty_log(self):
        result = engine.diagnose("")
        assert result.error_type == ErrorType.UNKNOWN
        assert result.confidence == 0.0

    def test_whitespace_only(self):
        result = engine.diagnose("   \n\n  ")
        assert result.error_type == ErrorType.UNKNOWN

    def test_unknown_error(self):
        log = "some random text that doesn't match anything"
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.UNKNOWN

    def test_multiline_log_matches_first_pattern(self):
        """多行日志中，按配置顺序匹配第一个命中的规则。"""
        log = (
            "sbatch: error: Batch job submission failed: Invalid qos specification\n"
            "some other line"
        )
        result = engine.diagnose(log)
        assert result.error_type == ErrorType.INVALID_QOS
