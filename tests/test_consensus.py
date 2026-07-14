"""Regression tests for parallel blinded consensus execution."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import tools.consensus as consensus_module
from tools.consensus import ConsensusRequest, ConsensusTool
from tools.consensus_status import ConsensusStatusTool
from tools.models import ToolModelCategory
from utils.consensus_tasks import get_consensus_task_manager
from utils.model_context import ModelContext


@pytest.fixture(autouse=True)
def reset_consensus_tasks():
    manager = get_consensus_task_manager()
    manager.reset_for_testing()
    yield manager
    manager.reset_for_testing()


def build_arguments(
    models: list[dict],
    *,
    prompt: str = "Evaluate proposal A.",
    relevant_files: list[str] | None = None,
) -> dict:
    arguments = {
        "step": prompt,
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "findings": f"Independent analysis for {prompt}",
        "models": models,
    }
    if relevant_files:
        arguments["relevant_files"] = relevant_files
    return arguments


class TestConsensusTool:
    def test_tool_metadata(self) -> None:
        tool = ConsensusTool()

        assert tool.get_name() == "consensus"
        assert "并行" in tool.get_description()
        assert tool.get_default_temperature() == 1.0
        assert tool.get_model_category() == ToolModelCategory.EXTENDED_REASONING
        assert tool.requires_model() is False

    def test_request_validation_requires_models_in_step_one(self) -> None:
        with pytest.raises(ValueError, match="第 1 步必须通过 'models' 字段"):
            ConsensusRequest(
                step="Test step",
                step_number=1,
                total_steps=1,
                next_step_required=False,
                findings="Test findings",
            )

    def test_request_validation_rejects_duplicate_model_stance(self) -> None:
        with pytest.raises(ValueError, match="重复的 model \\+ stance 组合"):
            ConsensusRequest(
                **build_arguments(
                    [
                        {"model": "o3", "stance": "for"},
                        {"model": "o3", "stance": "for"},
                    ]
                )
            )

    def test_request_validation_requires_at_least_two_models(self) -> None:
        with pytest.raises(ValueError, match="至少需要指定 2 个模型"):
            ConsensusRequest(**build_arguments([{"model": "only-one"}]))

    def test_input_schema_keeps_parallel_consensus_fields(self) -> None:
        schema = ConsensusTool().get_input_schema()

        for field in ["step", "step_number", "total_steps", "next_step_required", "findings", "models"]:
            assert field in schema["properties"]
        assert "relevant_files" in schema["properties"]
        assert "images" in schema["properties"]
        assert "model" not in schema["properties"]
        assert "temperature" not in schema["properties"]
        assert "thinking_mode" not in schema["properties"]

        models_items = schema["properties"]["models"]["items"]
        assert {"model", "stance", "stance_prompt"}.issubset(models_items["properties"])

    def test_required_actions_describe_parallel_synthesis(self) -> None:
        actions = ConsensusTool().get_required_actions(1, "exploring", "findings", 1)

        assert any("并行" in action for action in actions)
        assert any("综合" in action for action in actions)

    def test_stance_enhanced_prompt_generation(self) -> None:
        tool = ConsensusTool()

        assert "SUPPORTIVE PERSPECTIVE" in tool._get_stance_enhanced_prompt("for")
        assert "CRITICAL PERSPECTIVE" in tool._get_stance_enhanced_prompt("against")
        assert "BALANCED PERSPECTIVE" in tool._get_stance_enhanced_prompt("neutral")

        custom_prompt = tool._get_stance_enhanced_prompt("for", "Focus on delivery risk")
        assert "Focus on delivery risk" in custom_prompt
        assert "SUPPORTIVE PERSPECTIVE" not in custom_prompt

    @pytest.mark.asyncio
    async def test_consensus_runs_at_most_three_models_concurrently(self, monkeypatch, reset_consensus_tasks) -> None:
        tool = ConsensusTool()
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_consult(model_config, request, original_proposal):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return {
                "model": model_config["model"],
                "stance": model_config.get("stance", "neutral"),
                "status": "success",
                "verdict": original_proposal,
            }

        monkeypatch.setattr(consensus_module, "CONSENSUS_MAX_CONCURRENCY", 3)
        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 2.0)
        monkeypatch.setattr(tool, "_consult_model_sync", fake_consult)
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: "thread-a")
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        models = [{"model": f"model-{index}", "stance": "neutral"} for index in range(5)]
        result = await tool.execute(build_arguments(models))
        payload = json.loads(result[0].text)

        assert payload["status"] == "consensus_workflow_complete"
        assert max_active == 3
        assert [response["model"] for response in payload["model_responses"]] == [model["model"] for model in models]

    @pytest.mark.asyncio
    async def test_consensus_model_failure_does_not_cancel_others(self, monkeypatch) -> None:
        tool = ConsensusTool()

        def fake_consult(model_config, request, original_proposal):
            if model_config["model"] == "bad":
                return {
                    "model": "bad",
                    "stance": "against",
                    "status": "error",
                    "error": "provider failed",
                }
            return {
                "model": model_config["model"],
                "stance": "neutral",
                "status": "success",
                "verdict": original_proposal,
            }

        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 1.0)
        monkeypatch.setattr(tool, "_consult_model_sync", fake_consult)
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: "thread-b")
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        models = [
            {"model": "good-a", "stance": "neutral"},
            {"model": "bad", "stance": "against"},
            {"model": "good-b", "stance": "neutral"},
        ]
        result = await tool.execute(build_arguments(models))
        payload = json.loads(result[0].text)

        assert payload["successful_responses"] == 2
        assert payload["failed_responses"] == 1
        assert [response["status"] for response in payload["model_responses"]] == [
            "success",
            "error",
            "success",
        ]

    @pytest.mark.asyncio
    async def test_provider_client_is_initialized_once_before_parallel_calls(self, monkeypatch) -> None:
        tool = ConsensusTool()

        class LazyProvider:
            def __init__(self):
                self._client = None
                self.initializations = 0

            @property
            def client(self):
                if self._client is None:
                    self.initializations += 1
                    self._client = object()
                return self._client

            def generate_content(self, *, model_name, **kwargs):
                assert self._client is not None
                return SimpleNamespace(content=f"response:{model_name}")

            @staticmethod
            def get_provider_type():
                return SimpleNamespace(value="test")

        provider = LazyProvider()
        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 1.0)
        monkeypatch.setattr(tool, "get_model_provider", lambda model_name: provider)
        monkeypatch.setattr(tool, "validate_and_correct_temperature", lambda temperature, model_context: (1.0, []))
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: "thread-provider")
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        result = await tool.execute(
            build_arguments(
                [
                    {"model": "shared", "stance": "for"},
                    {"model": "shared", "stance": "against"},
                    {"model": "shared", "stance": "neutral"},
                ]
            )
        )
        payload = json.loads(result[0].text)

        assert payload["successful_responses"] == 3
        assert provider.initializations == 1

    @pytest.mark.asyncio
    async def test_consensus_returns_task_id_after_sync_deadline(self, monkeypatch, reset_consensus_tasks) -> None:
        tool = ConsensusTool()
        release = threading.Event()

        def slow_consult(model_config, request, original_proposal):
            release.wait(timeout=1)
            return {
                "model": model_config["model"],
                "stance": "neutral",
                "status": "success",
                "verdict": original_proposal,
            }

        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 0.005)
        monkeypatch.setattr(consensus_module, "CONSENSUS_BACKGROUND_WAIT_SECONDS", 0.05)
        monkeypatch.setattr(tool, "_consult_model_sync", slow_consult)
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: "thread-c")
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        result = await tool.execute(build_arguments([{"model": "slow-a"}, {"model": "slow-b"}]))
        payload = json.loads(result[0].text)

        assert payload["status"] == "consensus_in_progress"
        assert payload["background_wait_seconds"] == 0.05
        assert payload["total_timeout_seconds"] == 0.055
        task_id = payload["task_id"]

        pending_result = await ConsensusStatusTool().execute({"task_id": task_id})
        pending_payload = json.loads(pending_result[0].text)
        assert pending_payload["status"] == "pending"
        assert pending_payload["completed_models"] == 0

        release.set()
        await reset_consensus_tasks.wait(task_id, 1)

        completed_result = await ConsensusStatusTool().execute({"task_id": task_id})
        completed_payload = json.loads(completed_result[0].text)
        assert completed_payload["status"] == "completed"
        assert [response["verdict"] for response in completed_payload["result"]["model_responses"]] == [
            "Evaluate proposal A.",
            "Evaluate proposal A.",
        ]

    @pytest.mark.asyncio
    async def test_consensus_marks_task_failed_after_total_deadline(self, monkeypatch, reset_consensus_tasks) -> None:
        tool = ConsensusTool()
        release = threading.Event()

        def blocked_consult(model_config, request, original_proposal):
            release.wait(timeout=1)
            return {
                "model": model_config["model"],
                "stance": "neutral",
                "status": "success",
                "verdict": original_proposal,
            }

        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 0.005)
        monkeypatch.setattr(consensus_module, "CONSENSUS_BACKGROUND_WAIT_SECONDS", 0.02)
        monkeypatch.setattr(tool, "_consult_model_sync", blocked_consult)
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: "thread-timeout")
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        result = await tool.execute(build_arguments([{"model": "blocked-a"}, {"model": "blocked-b"}]))
        payload = json.loads(result[0].text)
        task_id = payload["task_id"]

        assert payload["status"] == "consensus_in_progress"
        assert payload["total_timeout_seconds"] == 0.025

        await reset_consensus_tasks.wait(task_id, 1)
        status_result = await ConsensusStatusTool().execute({"task_id": task_id})
        status_payload = json.loads(status_result[0].text)

        assert status_payload["status"] == "failed"
        assert status_payload["error"] == "Consensus 总执行时间超过 0.025 秒"

        release.set()
        await asyncio.sleep(0.02)

    @pytest.mark.asyncio
    async def test_concurrent_consensus_requests_do_not_share_prompts(self, monkeypatch) -> None:
        tool = ConsensusTool()
        received: list[tuple[str, str]] = []
        lock = threading.Lock()

        def fake_consult(model_config, request, original_proposal):
            with lock:
                received.append((model_config["model"], original_proposal))
            time.sleep(0.02)
            return {
                "model": model_config["model"],
                "stance": "neutral",
                "status": "success",
                "verdict": original_proposal,
            }

        thread_ids = iter(["thread-a", "thread-b"])
        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 1.0)
        monkeypatch.setattr(tool, "_consult_model_sync", fake_consult)
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: next(thread_ids))
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        result_a, result_b = await asyncio.gather(
            tool.execute(build_arguments([{"model": "a1"}, {"model": "a2"}], prompt="Proposal A")),
            tool.execute(build_arguments([{"model": "b1"}, {"model": "b2"}], prompt="Proposal B")),
        )

        payload_a = json.loads(result_a[0].text)
        payload_b = json.loads(result_b[0].text)
        assert {response["verdict"] for response in payload_a["model_responses"]} == {"Proposal A"}
        assert {response["verdict"] for response in payload_b["model_responses"]} == {"Proposal B"}
        assert set(received) == {
            ("a1", "Proposal A"),
            ("a2", "Proposal A"),
            ("b1", "Proposal B"),
            ("b2", "Proposal B"),
        }

    @pytest.mark.asyncio
    async def test_concurrent_requests_keep_file_context_and_model_prompts_isolated(self, monkeypatch) -> None:
        tool = ConsensusTool()
        captured_prompts: dict[str, str] = {}
        lock = threading.Lock()

        class FakeProvider:
            _client = object()

            def generate_content(self, *, prompt, model_name, **kwargs):
                with lock:
                    captured_prompts[model_name] = prompt
                return SimpleNamespace(content=f"verdict:{model_name}")

            @staticmethod
            def get_provider_type():
                return SimpleNamespace(value="test")

        def prepare_files(request_files, *args, **kwargs):
            return f"file-marker:{request_files[0]}", list(request_files)

        thread_ids = iter(["thread-files-a", "thread-files-b"])
        monkeypatch.setattr(consensus_module, "CONSENSUS_SYNC_WAIT_SECONDS", 1.0)
        monkeypatch.setattr(tool, "get_model_provider", lambda model_name: FakeProvider())
        monkeypatch.setattr(tool, "_prepare_file_content_for_prompt", prepare_files)
        monkeypatch.setattr(tool, "validate_and_correct_temperature", lambda temperature, model_context: (1.0, []))
        monkeypatch.setattr(consensus_module, "create_thread", lambda *args, **kwargs: next(thread_ids))
        monkeypatch.setattr(consensus_module, "add_turn", lambda *args, **kwargs: True)
        monkeypatch.setattr(tool, "_build_continuation_offer", lambda continuation_id: None)

        result_a, result_b = await asyncio.gather(
            tool.execute(
                build_arguments(
                    [{"model": "a1"}, {"model": "a2"}],
                    prompt="Proposal A",
                    relevant_files=[r"C:\context\proposal-a.md"],
                )
            ),
            tool.execute(
                build_arguments(
                    [{"model": "b1"}, {"model": "b2"}],
                    prompt="Proposal B",
                    relevant_files=[r"C:\context\proposal-b.md"],
                )
            ),
        )

        payload_a = json.loads(result_a[0].text)
        payload_b = json.loads(result_b[0].text)
        assert payload_a["successful_responses"] == 2
        assert payload_b["successful_responses"] == 2

        for model_name in ["a1", "a2"]:
            assert "Proposal A" in captured_prompts[model_name]
            assert "proposal-a.md" in captured_prompts[model_name]
            assert "Proposal B" not in captured_prompts[model_name]
            assert "proposal-b.md" not in captured_prompts[model_name]
        for model_name in ["b1", "b2"]:
            assert "Proposal B" in captured_prompts[model_name]
            assert "proposal-b.md" in captured_prompts[model_name]
            assert "Proposal A" not in captured_prompts[model_name]
            assert "proposal-a.md" not in captured_prompts[model_name]
        assert all("verdict:" not in prompt for prompt in captured_prompts.values())

    @pytest.mark.asyncio
    async def test_later_step_reports_parallel_completion(self) -> None:
        tool = ConsensusTool()
        result = await tool.execute(
            {
                "step": "Legacy second step",
                "step_number": 2,
                "total_steps": 2,
                "next_step_required": False,
                "findings": "Already complete",
            }
        )
        payload = json.loads(result[0].text)

        assert payload["status"] == "consensus_already_completed"
        assert payload["next_step_required"] is False

    def test_relevant_files_are_prepared_with_model_context(self) -> None:
        tool = ConsensusTool()
        request = ConsensusRequest(
            **build_arguments(
                [
                    {"model": "flash", "stance": "neutral"},
                    {"model": "flash", "stance": "for"},
                ],
                prompt="Test proposal",
            ),
            relevant_files=["/test/file.py"],
        )
        provider = Mock()
        provider.generate_content.return_value = SimpleNamespace(content="test response")
        provider.get_provider_type.return_value = SimpleNamespace(value="test")

        with (
            patch.object(tool, "get_model_provider", return_value=provider),
            patch.object(tool, "_prepare_file_content_for_prompt", return_value=("file content", [])) as prepare_files,
            patch.object(tool, "validate_and_correct_temperature", return_value=(1.0, [])),
        ):
            result = tool._consult_model_sync(
                {"model": "flash", "stance": "neutral"},
                request,
                "Test proposal",
            )

        assert result["status"] == "success"
        model_context = prepare_files.call_args.kwargs["model_context"]
        assert isinstance(model_context, ModelContext)
        assert model_context.model_name == "flash"
        assert prepare_files.call_args.kwargs["arguments"] == {"_remaining_tokens": None}
