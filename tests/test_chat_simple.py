"""
Tests for Chat tool - validating SimpleTool architecture

This module contains unit tests to ensure that the Chat tool
(now using SimpleTool architecture) maintains proper functionality.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.types import TextContent

from providers.custom import CustomProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.shared import ModelCapabilities, ModelResponse, ProviderType, RangeTemperatureConstraint
from tools.chat import ChatRequest, ChatTool
from tools.chat_status import ChatStatusTool
from tools.shared.exceptions import ToolExecutionError
from utils.chat_tasks import ChatTaskManager


class TestChatTool:
    """Test suite for ChatSimple tool"""

    def setup_method(self):
        """Set up test fixtures"""
        self.tool = ChatTool()

    def test_tool_metadata(self):
        """Test that tool metadata matches requirements"""
        assert self.tool.get_name() == "chat"
        assert "collaborative thinking" in self.tool.get_description()
        assert self.tool.get_system_prompt() is not None
        assert self.tool.get_default_temperature() > 0
        assert self.tool.get_model_category() is not None

    def test_schema_structure(self):
        """Test that schema has correct structure"""
        schema = self.tool.get_input_schema()

        # Basic schema structure
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

        # Required fields
        assert "prompt" in schema["required"]
        assert "working_directory_absolute_path" in schema["required"]

        # Properties
        properties = schema["properties"]
        assert "prompt" in properties
        assert "absolute_file_paths" in properties
        assert "images" in properties
        assert "working_directory_absolute_path" in properties
        assert properties["thinking_mode"]["enum"] == ["medium", "high", "xhigh", "max"]

    def test_request_model_validation(self):
        """Test that the request model validates correctly"""
        # Test valid request
        request_data = {
            "prompt": "Test prompt",
            "absolute_file_paths": ["test.txt"],
            "images": ["test.png"],
            "model": "anthropic/claude-opus-4.1",
            "temperature": 0.7,
            "working_directory_absolute_path": "/tmp",  # Dummy absolute path
        }

        request = ChatRequest(**request_data)
        assert request.prompt == "Test prompt"
        assert request.absolute_file_paths == ["test.txt"]
        assert request.images == ["test.png"]
        assert request.model == "anthropic/claude-opus-4.1"
        assert request.temperature == 0.7
        assert request.working_directory_absolute_path == "/tmp"

    def test_required_fields(self):
        """Test that required fields are enforced"""
        # Missing prompt should raise validation error
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChatRequest(model="anthropic/claude-opus-4.1", working_directory_absolute_path="/tmp")

    def test_model_availability(self):
        """Test that model availability works"""
        models = self.tool._get_available_models()
        assert len(models) > 0  # Should have some models
        assert isinstance(models, list)

    def test_model_field_schema(self):
        """Test that model field schema generation works correctly"""
        schema = self.tool.get_model_field_schema()

        assert schema["type"] == "string"
        assert "description" in schema

        # Description should route callers to listmodels, regardless of mode
        assert "listmodels" in schema["description"]
        if self.tool.is_effective_auto_mode():
            assert "auto mode" in schema["description"].lower()
        else:
            import config

            assert f"'{config.DEFAULT_MODEL}'" in schema["description"]

    @pytest.mark.asyncio
    async def test_prompt_preparation(self):
        """Test that prompt preparation works correctly"""
        request = ChatRequest(
            prompt="Test prompt",
            absolute_file_paths=[],
            working_directory_absolute_path="/tmp",
        )

        # Mock the system prompt and file handling
        with patch.object(self.tool, "get_system_prompt", return_value="System prompt"):
            with patch.object(self.tool, "handle_prompt_file_with_fallback", return_value="Test prompt"):
                with patch.object(self.tool, "_prepare_file_content_for_prompt", return_value=("", [])):
                    with patch.object(self.tool, "_validate_token_limit"):
                        with patch.object(self.tool, "get_websearch_instruction", return_value=""):
                            prompt = await self.tool.prepare_prompt(request)

                            assert "Test prompt" in prompt
                            assert prompt.startswith("=== USER REQUEST ===")
                            assert "System prompt" not in prompt

    def test_response_formatting(self):
        """Test that response formatting works correctly"""
        response = "Test response content"
        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")

        formatted = self.tool.format_response(response, request)

        assert "Test response content" in formatted
        assert "AGENT'S TURN:" in formatted
        assert "Evaluate this perspective" in formatted

    def test_format_response_multiple_generated_code_blocks(self, tmp_path):
        """All generated-code blocks should be combined and saved to pal_generated.code."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Intro text\n"
            "<GENERATED-CODE>print('hello')</GENERATED-CODE>\n"
            "Other text\n"
            "<GENERATED-CODE>print('world')</GENERATED-CODE>"
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        saved_content = saved_path.read_text(encoding="utf-8")

        assert "print('world')" in saved_content
        assert "print('hello')" not in saved_content
        assert saved_content.count("<GENERATED-CODE>") == 1
        assert "<GENERATED-CODE>print('hello')" in formatted
        assert str(saved_path) in formatted

    def test_format_response_single_generated_code_block(self, tmp_path):
        """Single <GENERATED-CODE> block should be saved and removed from narrative."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Intro text before code.\n"
            "<GENERATED-CODE>print('only-once')</GENERATED-CODE>\n"
            "Closing thoughts after code."
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        saved_content = saved_path.read_text(encoding="utf-8")

        assert "print('only-once')" in saved_content
        assert "<GENERATED-CODE>" in saved_content
        assert "print('only-once')" not in formatted
        assert "Closing thoughts after code." in formatted

    def test_format_response_ignores_unclosed_generated_code(self, tmp_path):
        """Unclosed generated-code tags should be ignored to avoid accidental clipping."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = "Intro text\n<GENERATED-CODE>print('oops')\nStill ongoing"

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        assert not saved_path.exists()
        assert "print('oops')" in formatted

    def test_format_response_ignores_orphaned_closing_tag(self, tmp_path):
        """Stray closing tags should not trigger extraction."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = "Intro text\n</GENERATED-CODE> just text"

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        saved_path = tmp_path / "pal_generated.code"
        assert not saved_path.exists()
        assert "</GENERATED-CODE> just text" in formatted

    def test_format_response_preserves_narrative_after_generated_code(self, tmp_path):
        """Narrative content after generated code must remain intact in the formatted output."""
        tool = ChatTool()
        tool._model_context = SimpleNamespace(capabilities=SimpleNamespace(allow_code_generation=True))

        response = (
            "Summary before code.\n"
            "<GENERATED-CODE>print('demo')</GENERATED-CODE>\n"
            "### Follow-up\n"
            "Further analysis and guidance after the generated snippet.\n"
        )

        request = ChatRequest(prompt="Test", working_directory_absolute_path=str(tmp_path))

        formatted = tool.format_response(response, request)

        assert "Summary before code." in formatted
        assert "### Follow-up" in formatted
        assert "Further analysis and guidance after the generated snippet." in formatted
        assert "print('demo')" not in formatted

    def test_tool_name(self):
        """Test tool name is correct"""
        assert self.tool.get_name() == "chat"

    def test_websearch_guidance(self):
        """Test web search guidance matches Chat tool style"""
        guidance = self.tool.get_websearch_guidance()
        chat_style_guidance = self.tool.get_chat_style_websearch_guidance()

        assert guidance == chat_style_guidance
        assert "Documentation for any technologies" in guidance
        assert "Current best practices" in guidance

    def test_convenience_methods(self):
        """Test SimpleTool convenience methods work correctly"""
        assert self.tool.supports_custom_request_model()

        # Test that the tool fields are defined correctly
        tool_fields = self.tool.get_tool_fields()
        assert "prompt" in tool_fields
        assert "absolute_file_paths" in tool_fields
        assert "images" in tool_fields

        required_fields = self.tool.get_required_fields()
        assert "prompt" in required_fields
        assert "working_directory_absolute_path" in required_fields

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("request_thinking_mode", "expected_effective_mode"),
        [
            (None, "medium"),
            ("xhigh", "xhigh"),
        ],
    )
    async def test_execute_preserves_requested_thinking_mode(
        self,
        tmp_path,
        request_thinking_mode,
        expected_effective_mode,
    ):
        """Simple tools should distinguish explicit thinking mode from their fallback."""
        capabilities = ModelCapabilities(
            provider=ProviderType.CUSTOM,
            model_name="reasoning-model",
            friendly_name="Reasoning Model",
            context_window=100_000,
            max_output_tokens=10_000,
            supports_extended_thinking=True,
            supports_temperature=True,
            temperature_constraint=RangeTemperatureConstraint(0.0, 1.0, 0.3),
        )
        provider = Mock()
        provider.get_provider_type.return_value = ProviderType.CUSTOM
        provider.generate_content.return_value = ModelResponse(
            content="Test response",
            model_name="reasoning-model",
            friendly_name="Reasoning Model",
            provider=ProviderType.CUSTOM,
            metadata={"finish_reason": "stop"},
        )
        model_context = SimpleNamespace(
            provider=provider,
            capabilities=capabilities,
            model_name="reasoning-model",
        )
        arguments = {
            "prompt": "Test",
            "model": "reasoning-model",
            "working_directory_absolute_path": str(tmp_path),
            "_model_context": model_context,
        }
        if request_thinking_mode is not None:
            arguments["thinking_mode"] = request_thinking_mode

        with (
            patch.object(self.tool, "prepare_prompt", new=AsyncMock(return_value="Test prompt")),
            patch("server.get_follow_up_instructions", return_value=""),
        ):
            await self.tool.execute(arguments)

        call_kwargs = provider.generate_content.call_args.kwargs
        assert call_kwargs["thinking_mode"] == expected_effective_mode
        assert call_kwargs["requested_thinking_mode"] == request_thinking_mode


class TestChatRequestModel:
    """Test suite for ChatRequest model"""

    def test_field_descriptions(self):
        """Test that field descriptions are proper"""
        from tools.chat import CHAT_FIELD_DESCRIPTIONS

        # Field descriptions should exist and be descriptive
        assert len(CHAT_FIELD_DESCRIPTIONS["prompt"]) > 50
        assert "context" in CHAT_FIELD_DESCRIPTIONS["prompt"]
        files_desc = CHAT_FIELD_DESCRIPTIONS["absolute_file_paths"].lower()
        assert "absolute" in files_desc
        assert "visual context" in CHAT_FIELD_DESCRIPTIONS["images"]
        assert "directory" in CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"].lower()

    def test_working_directory_absolute_path_description_matches_behavior(self):
        """Absolute working directory description should reflect existing-directory requirement."""
        from tools.chat import CHAT_FIELD_DESCRIPTIONS

        description = CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"].lower()
        assert "existing directory" in description

    @pytest.mark.asyncio
    async def test_working_directory_absolute_path_must_exist(self, tmp_path):
        """Chat tool should reject non-existent working directories."""
        tool = ChatTool()
        missing_dir = tmp_path / "nonexistent_subdir"

        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(
                {
                    "prompt": "test",
                    "absolute_file_paths": [],
                    "images": [],
                    "working_directory_absolute_path": str(missing_dir),
                }
            )

        payload = json.loads(exc_info.value.payload)
        assert payload["status"] == "error"
        assert "existing directory" in payload["content"].lower()

    def test_default_values(self):
        """Test that default values work correctly"""
        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")

        assert request.prompt == "Test"
        assert request.absolute_file_paths == []  # Should default to empty list
        assert request.images == []  # Should default to empty list

    def test_thinking_mode_validation(self):
        """Chat requests should accept only the MCP-exposed reasoning levels."""
        request = ChatRequest(
            prompt="Test",
            working_directory_absolute_path="/tmp",
            thinking_mode="xhigh",
        )
        assert request.thinking_mode == "xhigh"

        for removed_mode in ("minimal", "low"):
            with pytest.raises(ValueError, match="thinking_mode must be one of"):
                ChatRequest(
                    prompt="Test",
                    working_directory_absolute_path="/tmp",
                    thinking_mode=removed_mode,
                )

    def test_inheritance(self):
        """Test that ChatRequest properly inherits from ToolRequest"""
        from tools.shared.base_models import ToolRequest

        request = ChatRequest(prompt="Test", working_directory_absolute_path="/tmp")
        assert isinstance(request, ToolRequest)

        # Should have inherited fields
        assert hasattr(request, "model")
        assert hasattr(request, "temperature")
        assert hasattr(request, "thinking_mode")
        assert hasattr(request, "continuation_id")
        assert hasattr(request, "images")  # From base model too


class TestChatBackgroundFallback:
    """Long-running Chat requests should fall back to process-local background tasks."""

    @staticmethod
    def _patch_task_manager(monkeypatch, manager: ChatTaskManager) -> None:
        monkeypatch.setattr("tools.chat.get_chat_task_manager", lambda: manager)
        monkeypatch.setattr("tools.chat_status.get_chat_task_manager", lambda: manager)

    @pytest.mark.asyncio
    async def test_fast_chat_preserves_original_response(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)
        expected = [TextContent(type="text", text='{"status":"success","content":"done"}')]

        async def execute_once(_self, _arguments):
            return expected

        monkeypatch.setattr(ChatTool, "_execute_once", execute_once)
        monkeypatch.setattr("tools.chat.CHAT_SYNC_WAIT_SECONDS", 0.1)
        monkeypatch.setattr("tools.chat.CHAT_BACKGROUND_WAIT_SECONDS", 0.2)

        result = await ChatTool().execute({"model": "test-model"})

        assert result == expected

    @pytest.mark.asyncio
    async def test_slow_chat_returns_task_id_and_status_returns_original_result(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)
        release = asyncio.Event()
        expected = [TextContent(type="text", text='{"status":"success","content":"background done"}')]

        async def execute_once(_self, _arguments):
            await release.wait()
            return expected

        monkeypatch.setattr(ChatTool, "_execute_once", execute_once)
        monkeypatch.setattr("tools.chat.CHAT_SYNC_WAIT_SECONDS", 0.01)
        monkeypatch.setattr("tools.chat.CHAT_BACKGROUND_WAIT_SECONDS", 1.0)
        execution_lease = await manager.acquire_execution_lease("continuation:test")

        initial = await ChatTool().execute(
            {
                "model": "slow-model",
                "_chat_execution_lease": execution_lease,
            }
        )
        payload = json.loads(initial[0].text)

        assert payload["status"] == "chat_in_progress"
        assert payload["model"] == "slow-model"
        assert payload["total_timeout_seconds"] == 1.01
        assert "chat_status" in payload["next_steps"]

        competing_lease, active_operation = await manager.try_acquire_execution_lease(
            "continuation:test",
            owner_tool="chat",
        )
        assert competing_lease is None
        assert active_operation["task_id"] == payload["task_id"]

        pending = await ChatStatusTool().execute({"task_id": payload["task_id"]})
        assert json.loads(pending[0].text)["status"] == "pending"

        next_lease_waiter = asyncio.create_task(manager.acquire_execution_lease("continuation:test"))
        await asyncio.sleep(0)
        assert not next_lease_waiter.done()

        release.set()
        await manager.wait(payload["task_id"], 1)
        next_lease = await asyncio.wait_for(next_lease_waiter, timeout=1)
        next_lease.release()

        completed = await ChatStatusTool().execute({"task_id": payload["task_id"]})
        assert completed == expected

    @pytest.mark.asyncio
    async def test_chat_total_timeout_is_reported_as_failed(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)

        async def execute_once(_self, _arguments):
            await asyncio.sleep(1)
            return [TextContent(type="text", text="late")]

        monkeypatch.setattr(ChatTool, "_execute_once", execute_once)
        monkeypatch.setattr("tools.chat.CHAT_SYNC_WAIT_SECONDS", 0.01)
        monkeypatch.setattr("tools.chat.CHAT_BACKGROUND_WAIT_SECONDS", 0.01)

        initial = await ChatTool().execute({"model": "slow-model"})
        task_id = json.loads(initial[0].text)["task_id"]
        await manager.wait(task_id, 1)

        failed = await ChatStatusTool().execute({"task_id": task_id})
        payload = json.loads(failed[0].text)
        assert payload["status"] == "failed"
        assert "总执行时间超过" in payload["error"]
        assert manager.get_exception(task_id) is None

    @pytest.mark.asyncio
    async def test_chat_status_reports_unknown_task(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)

        result = await ChatStatusTool().execute({"task_id": "missing"})
        payload = json.loads(result[0].text)

        assert payload["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_fast_chat_preserves_original_exception(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)
        expected = ToolExecutionError('{"status":"error","content":"boom"}')

        async def execute_once(_self, _arguments):
            raise expected

        monkeypatch.setattr(ChatTool, "_execute_once", execute_once)
        monkeypatch.setattr("tools.chat.CHAT_SYNC_WAIT_SECONDS", 0.1)
        monkeypatch.setattr("tools.chat.CHAT_BACKGROUND_WAIT_SECONDS", 0.2)

        with pytest.raises(ToolExecutionError) as exc_info:
            await ChatTool().execute({"model": "test-model"})

        assert exc_info.value is expected

    @pytest.mark.asyncio
    async def test_completed_chat_task_expires_after_ttl(self):
        now = [100.0]
        manager = ChatTaskManager(ttl_seconds=10, clock=lambda: now[0])

        async def worker():
            return [TextContent(type="text", text="done")]

        task_id = manager.create_record("test-model")
        manager.start(task_id, worker())
        await manager.wait(task_id, 1)
        assert manager.get_snapshot(task_id)["status"] == "completed"

        now[0] = 111.0
        assert manager.get_snapshot(task_id)["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_provider_call_runs_outside_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        provider = Mock()
        captured_kwargs = {}

        def generate_content(**kwargs):
            captured_kwargs.update(kwargs)
            return threading.get_ident()

        provider.generate_content.side_effect = generate_content
        tool = ChatTool()
        tool._request_deadline_monotonic = 123.0

        provider_thread = await tool._generate_model_response(provider, prompt="test")

        assert provider_thread != event_loop_thread
        assert captured_kwargs["request_deadline_monotonic"] == 123.0

    @pytest.mark.asyncio
    async def test_execution_lease_serializes_same_continuation(self):
        manager = ChatTaskManager()
        first = await manager.acquire_execution_lease("continuation:test")
        second_waiter = asyncio.create_task(manager.acquire_execution_lease("continuation:test"))
        await asyncio.sleep(0)

        assert not second_waiter.done()

        first.release()
        second = await asyncio.wait_for(second_waiter, timeout=1)
        second.release()

    @pytest.mark.asyncio
    async def test_server_serializes_chat_before_continuation_reconstruction(self, monkeypatch):
        import server

        manager = ChatTaskManager()
        release_background = asyncio.Event()
        calls = []

        async def fake_impl(name, arguments):
            calls.append(arguments["prompt"])
            lease = arguments["_chat_execution_lease"]
            if arguments["prompt"] == "first":
                lease.transfer()

                async def release_later():
                    await release_background.wait()
                    lease.release()

                asyncio.create_task(release_later())
            return [TextContent(type="text", text=arguments["prompt"])]

        monkeypatch.setattr("utils.chat_tasks.get_chat_task_manager", lambda: manager)
        monkeypatch.setattr(server, "_handle_call_tool_impl", fake_impl)

        first = await server.handle_call_tool(
            "chat",
            {"prompt": "first", "continuation_id": "thread-1"},
        )
        assert first[0].text == "first"

        second = await server.handle_call_tool(
            "chat",
            {"prompt": "second", "continuation_id": "thread-1"},
        )
        assert calls == ["first"]
        busy_payload = json.loads(second[0].text)
        assert busy_payload["status"] == "conversation_in_progress"
        assert busy_payload["active_tool"] == "chat"

        release_background.set()
        await asyncio.sleep(0)

        third = await server.handle_call_tool(
            "chat",
            {"prompt": "third", "continuation_id": "thread-1"},
        )
        assert third[0].text == "third"
        assert calls == ["first", "third"]

    @pytest.mark.asyncio
    async def test_cancelled_sync_wait_discards_unexposed_task_record(self, monkeypatch):
        manager = ChatTaskManager()
        self._patch_task_manager(monkeypatch, manager)
        release = asyncio.Event()

        async def execute_once(_self, _arguments):
            await release.wait()
            return [TextContent(type="text", text="done")]

        monkeypatch.setattr(ChatTool, "_execute_once", execute_once)
        monkeypatch.setattr("tools.chat.CHAT_SYNC_WAIT_SECONDS", 10.0)
        monkeypatch.setattr("tools.chat.CHAT_BACKGROUND_WAIT_SECONDS", 1.0)

        call = asyncio.create_task(ChatTool().execute({"model": "slow-model"}))
        await asyncio.sleep(0)
        call.cancel()

        with pytest.raises(asyncio.CancelledError):
            await call

        assert manager._records == {}
        release.set()

    def test_worker_clone_preserves_instance_overrides(self):
        tool = ChatTool()
        override = AsyncMock(return_value="custom prompt")
        tool.prepare_prompt = override
        tool._actually_processed_files = ["stale.py"]

        worker = tool._create_worker()

        assert worker.prepare_prompt is override
        assert not hasattr(worker, "_actually_processed_files")

    def test_openai_compatible_request_uses_remaining_deadline(self):
        deadline = time.monotonic() + 10

        request_params = OpenAICompatibleProvider._with_request_deadline(
            {"model": "test-model"},
            deadline,
        )

        assert request_params["model"] == "test-model"
        assert 0 < request_params["timeout"] <= 10

    def test_openai_compatible_request_rejects_expired_deadline(self):
        with pytest.raises(TimeoutError, match="deadline exceeded"):
            OpenAICompatibleProvider._with_request_deadline(
                {"model": "test-model"},
                time.monotonic() - 1,
            )

    def test_openai_compatible_deadline_disables_sdk_internal_retries(self):
        provider = object.__new__(CustomProvider)
        provider._client = Mock()
        bounded_client = Mock()
        provider._client.with_options.return_value = bounded_client

        result = provider._client_without_internal_retries(time.monotonic() + 10)

        assert result is bounded_client
        provider._client.with_options.assert_called_once_with(max_retries=0)

    def test_chat_status_is_registered_without_model_requirement(self):
        from server import TOOLS

        assert "chat_status" in TOOLS
        assert TOOLS["chat_status"].requires_model() is False
        assert TOOLS["chat_status"].get_annotations() == {"readOnlyHint": True}

    def test_chat_status_follows_chat_filtering(self):
        from server import apply_tool_filter

        tools = {
            "chat": object(),
            "chat_status": object(),
            "consensus": object(),
        }

        status_only_disabled = apply_tool_filter(tools, {"chat_status"})
        assert "chat" in status_only_disabled
        assert "chat_status" in status_only_disabled

        chat_disabled = apply_tool_filter(tools, {"chat"})
        assert "chat" not in chat_disabled
        assert "chat_status" not in chat_disabled


if __name__ == "__main__":
    pytest.main([__file__])
