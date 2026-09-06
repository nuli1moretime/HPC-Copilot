"""针对当前 FastAPI 实现的架构回归测试。"""

import json

from fastapi.testclient import TestClient

from backend import main
from backend.agent.templates import TEMPLATES
from backend.rag.loader import DocChunk
from backend.rag.loader import _split_by_headings
from backend.rag.retriever import Retriever
from backend.rag.vector_store import SearchResult, VectorStore
from backend.executor.webshell_executor import WebShellExecutor, _plain_command_output


def test_saved_config_excludes_credentials(tmp_path, monkeypatch):
    """配置文件不能再成为密码、Cookie 或 API Key 的明文副本。"""
    target = tmp_path / "connection_config.json"
    monkeypatch.setattr(main, "_CONFIG_FILE", target)
    config = main.ConnectionConfig(
        mode="ssh",
        ssh_host="cluster.example",
        ssh_user="student",
        ssh_password="secret-password",
        ssh_key="private-key-content",
        ssh_totp="123456",
        rest_token="slurm-token",
        webshell_cookie="session=cookie-value",
        llm_api_base="https://llm.example/v1",
        llm_api_key="llm-secret",
        llm_model="demo-model",
    )

    main._save_config(config)
    saved = json.loads(target.read_text(encoding="utf-8"))

    assert saved["ssh_host"] == "cluster.example"
    assert saved["llm_model"] == "demo-model"
    assert saved["ssh_password"] == ""
    assert saved["ssh_key"] == ""
    assert saved["ssh_totp"] == ""
    assert saved["rest_token"] == ""
    assert saved["webshell_cookie"] == ""
    assert saved["llm_api_key"] == ""


def test_job_templates_extract_and_reuse_the_submitted_job_id():
    """轮询和读日志必须绑定本次提交的 Job ID，不能误读旧作业。"""
    for template_id in ("gpu-training", "slurm-first-job", "fire-growth"):
        stages = TEMPLATES[template_id].stages
        poll_stage = next(stage for stage in stages if stage.action == "poll_jobs")
        log_stage = next(
            stage
            for stage in stages
            if stage.action == "run_command" and "slurm-" in stage.params.get("command", "")
        )

        assert poll_stage.params["extract_job_id_from"] == "submit_output"
        assert "{job_id}" in log_stage.params["command"]
        assert "slurm-*.out" not in log_stage.params["command"]
        assert poll_stage.params["status_file"].endswith("job-{job_id}.status")
        assert log_stage.params["success_markers"]
        assert TEMPLATES[template_id].overview
        assert TEMPLATES[template_id].conditions
        assert TEMPLATES[template_id].models
        assert TEMPLATES[template_id].outputs
        assert TEMPLATES[template_id].work_dir
        assert TEMPLATES[template_id].artifacts


def test_first_slurm_job_has_visible_beginner_friendly_output():
    template = TEMPLATES["slurm-first-job"]
    write_stage = next(stage for stage in template.stages if stage.action == "write_file")
    script = write_stage.params["content"]

    assert "[${step}/5]" in script
    assert "Final result" in script
    assert "Status       : SUCCESS" in script
    assert "sleep 1" in script


def test_gpu_template_is_short_self_contained_cuda_example():
    template = TEMPLATES["gpu-training"]
    source_stage = next(
        stage for stage in template.stages
        if stage.action == "write_file" and stage.params["path"].endswith(".cu")
    )
    submit_stage = next(
        stage for stage in template.stages
        if stage.action == "write_file" and stage.params["path"].endswith("submit.sh")
    )
    summary_stage = next(stage for stage in template.stages if stage.action == "llm_summarize")
    source = source_stage.params["content"]
    submit = submit_stage.params["content"]
    summary_prompt = summary_stage.params["prompt"]

    assert "vector_add<<<" in source
    assert "cudaEventElapsedTime" in source
    assert '"PASS" : "FAIL"' in source
    assert "nvcc -O2 -arch=sm_80 vector_add.cu" in submit
    assert "module load cuda/13.0" in submit
    assert submit.index("unset PYTHONHOME PYTHONPATH") < submit.index("module load cuda/13.0")
    assert 'echo "Status       : SUCCESS"' in submit
    assert "不是模型训练" in summary_prompt
    assert "不要推测日志中没有出现" in summary_prompt
    assert "MNIST" not in source + submit
    assert "download" not in source + submit


def test_fire_template_runs_and_writes_meaningful_results(tmp_path, monkeypatch):
    template = TEMPLATES["fire-growth"]
    model_stage = next(
        stage for stage in template.stages
        if stage.action == "write_file" and stage.params["path"].endswith("fire_growth.py")
    )
    source = model_stage.params["content"]
    submit_stage = next(
        stage for stage in template.stages
        if stage.action == "write_file" and stage.params["path"].endswith("submit.sh")
    )
    submit = submit_stage.params["content"]

    monkeypatch.chdir(tmp_path)
    exec(compile(source, "fire_growth.py", "exec"), {})
    csv_text = (tmp_path / "fire_results.csv").read_text(encoding="utf-8")

    assert "time_s,hrr_kw,flame_height_m" in csv_text
    assert "Model status       : SUCCESS" in source
    assert "alpert_ceiling_jet" in source
    assert "oxygen_consumed" in source
    assert "numpy" not in source
    assert "pandas" not in source
    assert "unset PYTHONHOME PYTHONPATH" in submit
    assert "PYTHON_BIN=/usr/bin/python3" in submit
    assert "import encodings, csv, math" in submit
    assert '"$PYTHON_BIN" -I fire_growth.py' in submit
    assert "module load python3.12" not in submit
    assert "job-check" not in TEMPLATES


def test_colored_shell_prompt_is_detected_without_idle_timeout():
    executor = WebShellExecutor(
        base_url="https://cluster.example",
        cluster="training",
        login_node="login-01",
        cookie="test-cookie",
    )
    assert executor._looks_like_prompt("output\n\x1b[32mstudent@login-01:~$\x1b[0m")


def test_terminal_control_sequences_never_leak_into_workflow_text():
    raw = (
        "RUNNING\r\n"
        "\x1b[?2004l\x1b[?2004h"
        "\x1b]0;student@login-01: ~/work\x07"
        "\x1b[01;32mstudent@login-01\x1b[00m:"
        "\x1b[01;34m~/work\x1b[00m$ "
    )

    assert _plain_command_output(raw) == "RUNNING"
    assert main._clean_workflow_output(raw) == "RUNNING"


def test_scontrol_log_paths_are_cached_and_job_placeholders_are_expanded():
    """后台监控应能从作业详情定位本次作业的 stdout/stderr。"""
    job_info = (
        "JobId=61234 JobState=RUNNING WorkDir=/home/student/demo "
        "StdErr=error-demo-%j.err StdOut=/home/student/demo/output-%j.out"
    )

    assert main._parse_job_log_paths(job_info, "61234") == [
        "/home/student/demo/error-demo-61234.err",
        "/home/student/demo/output-61234.out",
    ]


def test_chat_stream_echoes_request_id(monkeypatch):
    """首次消息和告警解释必须靠 request_id 区分，不能再互相劫持。"""
    class FakeLLM:
        is_configured = True

        async def chat_stream(self, **_kwargs):
            yield "你好，我收到了。"

        async def aclose(self):
            return None

    async def skip_rag():
        return False

    monkeypatch.setattr(main, "_create_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(main, "_init_rag", skip_rag)

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({
                "type": "user_message",
                "data": "你好",
                "request_id": "chat-first-message",
            })
            typing = websocket.receive_json()
            chunk = websocket.receive_json()
            end = websocket.receive_json()

    assert typing["request_id"] == "chat-first-message"
    assert chunk == {
        "type": "stream_chunk",
        "data": "你好，我收到了。",
        "request_id": "chat-first-message",
    }
    assert end["type"] == "stream_end"
    assert end["request_id"] == "chat-first-message"


def test_failed_slurm_job_cannot_be_reported_as_success(monkeypatch):
    """工作流步骤走完不等于计算成功，最终状态必须服从 Slurm。"""
    class FakeLLM:
        is_configured = True

        async def chat_stream(self, **_kwargs):
            yield "日志表明作业失败。"

        async def aclose(self):
            return None

    class FakeExecutor:
        async def execute_agent_command(self, command):
            if "sbatch submit.sh" in command:
                return "Submitted batch job 12345"
            if "squeue -j" in command:
                return "(命令执行成功，无输出)"
            if "sacct -X" in command:
                return "FAILED|1:0"
            if "slurm-12345.out" in command:
                return "Fatal Python error"
            return "(命令执行成功，无输出)"

    monkeypatch.setattr(main, "_create_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(main, "_active_executor", FakeExecutor())

    events = []
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({"type": "run_template", "data": "fire-growth"})
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "template_end":
                    break

    poll_events = [
        event for event in events
        if event["type"] == "template_stage"
        and event["data"].get("label") == "等待仿真完成"
    ]
    assert any(event["data"]["status"] == "error" for event in poll_events)
    assert events[-1]["data"]["success"] is False


def test_unavailable_accounting_uses_deterministic_log_markers(monkeypatch):
    """控制器查不到历史记录时，成功日志应当消除 FINISHED_UNVERIFIED。"""
    class FakeLLM:
        is_configured = True

        async def chat_stream(self, **_kwargs):
            yield "仿真成功。"

        async def aclose(self):
            return None

    class FakeExecutor:
        async def execute_agent_command(self, command):
            if "sbatch submit.sh" in command:
                return "Submitted batch job 12345"
            if "squeue -j" in command:
                return "(命令执行成功，无输出)"
            if "sacct -X" in command or "scontrol show job" in command:
                return "(命令执行成功，无输出)"
            if "job-12345.status" in command:
                return "(命令执行成功，无输出)"
            if "slurm-12345.out" in command:
                return (
                    "Model status       : SUCCESS\n"
                    "CSV output         : fire_results.csv\n"
                )
            return "(命令执行成功，无输出)"

    monkeypatch.setattr(main, "_create_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(main, "_active_executor", FakeExecutor())

    events = []
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({"type": "run_template", "data": "fire-growth"})
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "template_end":
                    break

    assert events[-1]["data"]["success"] is True
    assert any(
        event["type"] == "template_stage"
        and "日志已核验成功" in event["data"].get("detail", "")
        for event in events
    )


def test_template_result_is_available_to_follow_up_chat(monkeypatch):
    """模板结束后的“这个输出文件”必须能关联到刚才的 CSV 和作业号。"""
    class RecordingLLM:
        is_configured = True

        def __init__(self):
            self.calls = []

        async def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            yield "已找到刚才的输出文件。"

        async def aclose(self):
            return None

    class FakeExecutor:
        async def execute_agent_command(self, command):
            if "sbatch submit.sh" in command:
                return "Submitted batch job 55166"
            if "squeue -j" in command:
                return "(命令执行成功，无输出)"
            if "sacct -X" in command:
                return "COMPLETED|0:0"
            if "slurm-55166.out" in command:
                return (
                    "Model status       : SUCCESS\n"
                    "CSV output         : fire_results.csv\n"
                )
            return "(命令执行成功，无输出)"

    llm = RecordingLLM()
    monkeypatch.setattr(main, "_create_llm_client", lambda: llm)
    monkeypatch.setattr(main, "_active_executor", FakeExecutor())

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({
                "type": "run_template",
                "data": {"id": "fire-growth", "request": "请运行火灾算例"},
            })
            start_event = websocket.receive_json()
            assert start_event["type"] == "template_start"
            assert "6 m × 4 m × 3 m" in start_event["data"]["overview"]
            assert start_event["data"]["conditions"]
            while True:
                event = websocket.receive_json()
                if event["type"] == "template_end":
                    break

            websocket.send_json({
                "type": "user_message",
                "data": "我想看一下这个输出文件",
                "request_id": "follow-up-file",
            })
            while True:
                event = websocket.receive_json()
                if event["type"] == "stream_end":
                    break

    follow_up = llm.calls[-1]
    assert "fire_results.csv" in follow_up["system_prompt"]
    assert "55166" in follow_up["system_prompt"]
    assert any("fire_results.csv" in item["content"] for item in follow_up["history"])


def test_faiss_index_round_trip_works_with_python_file_io(tmp_path):
    """中文路径兼容方案写出的缓存必须能在后端重启后重新加载。"""
    chunks = [
        DocChunk(id="a", text="sbatch 提交作业", source_file="guide.md", heading="提交"),
        DocChunk(id="b", text="squeue 查询队列", source_file="guide.md", heading="查询"),
    ]
    store = VectorStore()
    store.build(chunks, [[1.0, 0.0], [0.0, 1.0]])
    store.save(tmp_path)

    loaded = VectorStore()
    assert loaded.load(tmp_path)
    results = loaded.search([1.0, 0.0], top_k=1)
    assert results[0].chunk.id == "a"


def test_rag_loader_splits_level_three_headings_with_parent_context():
    chunks = _split_by_headings(
        "# Guide\n\nGeneral introduction long enough for one chunk.\n\n"
        "## FAQ\n\nOverview long enough for one chunk.\n\n"
        "### More workers?\n\nToo many workers can increase memory pressure.",
        "guide.md",
    )
    assert [chunk.heading for chunk in chunks] == ["FAQ", "FAQ > More workers?"]


def test_faiss_cache_rejects_changed_corpus_fingerprint(tmp_path):
    chunks = [DocChunk(id="a", text="sbatch submit", source_file="guide.md", heading="submit")]
    store = VectorStore()
    store.build(chunks, [[1.0, 0.0]])
    store.save(tmp_path, corpus_fingerprint="old")

    loaded = VectorStore()
    assert not loaded.load(tmp_path, expected_fingerprint="new")
    assert loaded.load(tmp_path, expected_fingerprint="old")


def test_local_hybrid_rerank_can_promote_exact_command_heading(tmp_path):
    retriever = Retriever(
        knowledge_dir=tmp_path,
        index_dir=tmp_path / "index",
        embedding_service=type("FakeEmbeddingService", (), {"model": "fake"})(),
        lexical_weight=0.5,
    )
    wrong = SearchResult(
        DocChunk(id="a", text="general cluster information", source_file="a.md", heading="Overview"),
        0.70,
    )
    right = SearchResult(
        DocChunk(
            id="b",
            text="srun starts an interactive task while sbatch submits a batch script",
            source_file="b.md",
            heading="srun and sbatch",
        ),
        0.66,
    )
    ranked = retriever._hybrid_rerank("srun和sbatch有什么区别", [wrong, right])
    assert ranked[0].chunk.id == "b"
