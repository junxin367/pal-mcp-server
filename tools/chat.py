"""
Chat tool - General development chat and collaborative thinking

This tool provides a conversational interface for general development assistance,
brainstorming, problem-solving, and collaborative thinking. It supports file context,
images, and conversation continuation for seamless multi-turn interactions.
"""

import asyncio
import copy
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

if TYPE_CHECKING:
    from providers.shared import ModelCapabilities
    from tools.models import ToolModelCategory

from mcp.types import TextContent

from config import (
    CHAT_BACKGROUND_WAIT_SECONDS,
    CHAT_SYNC_WAIT_SECONDS,
    DEFAULT_MODEL,
    TEMPERATURE_BALANCED,
)
from systemprompts import CHAT_PROMPT, GENERATE_CODE_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, THINKING_MODES, ToolRequest
from utils.chat_tasks import TASK_FAILED, get_chat_task_manager

from .simple.base import SimpleTool

# Field descriptions matching the original Chat tool exactly
CHAT_FIELD_DESCRIPTIONS = {
    "prompt": (
        "Your question or idea for collaborative thinking to be sent to the external model. Provide detailed context, "
        "including your goal, what you've tried, and any specific challenges. "
        "WARNING: Large inline code must NOT be shared in prompt. Provide full-path to files on disk as separate parameter."
    ),
    "absolute_file_paths": ("Full, absolute file paths to relevant code in order to share with external model"),
    "images": "Image paths (absolute) or base64 strings for optional visual context.",
    "working_directory_absolute_path": (
        "Absolute path to an existing directory where generated code artifacts can be saved."
    ),
}


class ChatRequest(ToolRequest):
    """Request model for Chat tool"""

    prompt: str = Field(..., description=CHAT_FIELD_DESCRIPTIONS["prompt"])
    absolute_file_paths: Optional[list[str]] = Field(
        default_factory=list,
        description=CHAT_FIELD_DESCRIPTIONS["absolute_file_paths"],
    )
    images: Optional[list[str]] = Field(default_factory=list, description=CHAT_FIELD_DESCRIPTIONS["images"])
    working_directory_absolute_path: str = Field(
        ...,
        description=CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"],
    )


class ChatTool(SimpleTool):
    """
    General development chat and collaborative thinking tool using SimpleTool architecture.

    This tool provides identical functionality to the original Chat tool but uses the new
    SimpleTool architecture for cleaner code organization and better maintainability.

    Migration note: This tool is designed to be a drop-in replacement for the original
    Chat tool with 100% behavioral compatibility.
    """

    _artifact_write_lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._last_recordable_response: Optional[str] = None

    def get_name(self) -> str:
        return "chat"

    def get_description(self) -> str:
        return (
            "General chat and collaborative thinking partner for brainstorming, development discussion, "
            "getting second opinions, and exploring ideas. Use for ideas, validations, questions, and thoughtful explanations."
        )

    def get_annotations(self) -> Optional[dict[str, Any]]:
        """Chat writes generated artifacts when code-generation is enabled."""

        return {"readOnlyHint": False}

    def get_system_prompt(self) -> str:
        return CHAT_PROMPT

    def get_capability_system_prompts(self, capabilities: Optional["ModelCapabilities"]) -> list[str]:
        prompts = list(super().get_capability_system_prompts(capabilities))
        if capabilities and capabilities.allow_code_generation:
            prompts.append(GENERATE_CODE_PROMPT)
        return prompts

    def get_default_temperature(self) -> float:
        return TEMPERATURE_BALANCED

    def get_model_category(self) -> "ToolModelCategory":
        """Chat prioritizes fast responses and cost efficiency"""
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    def get_request_model(self):
        """Return the Chat-specific request model"""
        return ChatRequest

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Run Chat synchronously first, then leave slow requests running in the background."""
        model_name = str(arguments.get("_resolved_model_name") or arguments.get("model") or DEFAULT_MODEL)
        manager = get_chat_task_manager()
        task_id = manager.create_record(model_name)
        total_timeout_seconds = CHAT_SYNC_WAIT_SECONDS + CHAT_BACKGROUND_WAIT_SECONDS

        worker = self._create_worker()
        worker._request_deadline_monotonic = time.monotonic() + total_timeout_seconds
        execution_lease = arguments.get("_chat_execution_lease")

        async def run_worker() -> list[TextContent]:
            try:
                return await worker._execute_once(dict(arguments))
            finally:
                if execution_lease is not None:
                    execution_lease.release()

        manager.start(
            task_id,
            run_worker(),
            timeout_seconds=total_timeout_seconds,
        )
        if execution_lease is not None:
            execution_lease.transfer()

        try:
            result = await manager.wait(task_id, CHAT_SYNC_WAIT_SECONDS)
        except asyncio.CancelledError:
            manager.discard(task_id)
            raise
        if result is not None:
            snapshot = manager.get_snapshot(task_id)
            exception = manager.get_exception(task_id) if snapshot["status"] == TASK_FAILED else None
            manager.discard(task_id)
            if exception is not None:
                raise exception
            self._adopt_worker_state(worker)
            return result

        manager.mark_exposed(task_id)
        if execution_lease is not None:
            execution_lease.bind_task(task_id)
        snapshot = manager.get_snapshot(task_id)
        response_data = {
            "status": "chat_in_progress",
            "task_id": task_id,
            "model": model_name,
            "background_wait_seconds": CHAT_BACKGROUND_WAIT_SECONDS,
            "total_timeout_seconds": total_timeout_seconds,
            "next_steps": (
                "请调用 chat_status，并传入此 task_id 查询进度或获取最终结果。"
                f"任务从开始计算最多运行 {total_timeout_seconds:g} 秒，"
                f"返回 task_id 后还可后台运行 {CHAT_BACKGROUND_WAIT_SECONDS:g} 秒。"
                "不要重新发起相同的 chat 请求。"
            ),
        }
        if snapshot.get("status") == TASK_FAILED:
            response_data["status"] = "chat_failed"
            response_data["error"] = snapshot.get("error", "Chat 任务执行失败")

        return [
            TextContent(
                type="text",
                text=json.dumps(response_data, indent=2, ensure_ascii=False),
            )
        ]

    async def _execute_once(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Execute the original SimpleTool Chat flow without creating another background task."""
        return await super().execute(arguments)

    def _create_worker(self) -> "ChatTool":
        """Clone this instance while clearing request-scoped mutable state."""
        worker = copy.copy(self)
        for attribute in [
            "_current_arguments",
            "_current_model_name",
            "_model_context",
            "_actually_processed_files",
            "_request_deadline_monotonic",
        ]:
            worker.__dict__.pop(attribute, None)
        worker._last_recordable_response = None
        return worker

    def _adopt_worker_state(self, worker: "ChatTool") -> None:
        """Preserve synchronous-call observability expected by existing integrations."""
        if hasattr(worker, "_actually_processed_files"):
            self._actually_processed_files = list(worker._actually_processed_files)

    async def _generate_model_response(self, provider, **kwargs):
        """Run the synchronous provider call outside the MCP event loop."""
        request_deadline = getattr(self, "_request_deadline_monotonic", None)
        if request_deadline is not None:
            kwargs["request_deadline_monotonic"] = request_deadline
        return await asyncio.to_thread(provider.generate_content, **kwargs)

    # === Schema Generation Utilities ===

    def get_input_schema(self) -> dict[str, Any]:
        """Generate input schema matching the original Chat tool expectations."""

        required_fields = ["prompt", "working_directory_absolute_path"]
        if self.is_effective_auto_mode():
            required_fields.append("model")

        schema = {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": CHAT_FIELD_DESCRIPTIONS["prompt"],
                },
                "absolute_file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": CHAT_FIELD_DESCRIPTIONS["absolute_file_paths"],
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": CHAT_FIELD_DESCRIPTIONS["images"],
                },
                "working_directory_absolute_path": {
                    "type": "string",
                    "description": CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"],
                },
                "model": self.get_model_field_schema(),
                "temperature": {
                    "type": "number",
                    "description": COMMON_FIELD_DESCRIPTIONS["temperature"],
                    "minimum": 0,
                    "maximum": 1,
                },
                "thinking_mode": {
                    "type": "string",
                    "enum": list(THINKING_MODES),
                    "description": COMMON_FIELD_DESCRIPTIONS["thinking_mode"],
                },
                "continuation_id": {
                    "type": "string",
                    "description": COMMON_FIELD_DESCRIPTIONS["continuation_id"],
                },
            },
            "required": required_fields,
            "additionalProperties": False,
        }

        return schema

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        """Tool-specific field definitions used by SimpleTool scaffolding."""

        return {
            "prompt": {
                "type": "string",
                "description": CHAT_FIELD_DESCRIPTIONS["prompt"],
            },
            "absolute_file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": CHAT_FIELD_DESCRIPTIONS["absolute_file_paths"],
            },
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": CHAT_FIELD_DESCRIPTIONS["images"],
            },
            "working_directory_absolute_path": {
                "type": "string",
                "description": CHAT_FIELD_DESCRIPTIONS["working_directory_absolute_path"],
            },
        }

    def get_required_fields(self) -> list[str]:
        """Required fields for ChatSimple tool"""
        return ["prompt", "working_directory_absolute_path"]

    # === Hook Method Implementations ===

    async def prepare_prompt(self, request: ChatRequest) -> str:
        """
        Prepare the chat prompt with optional context files.

        This implementation matches the original Chat tool exactly while using
        SimpleTool convenience methods for cleaner code.
        """
        # Use SimpleTool's Chat-style prompt preparation
        return self.prepare_chat_style_prompt(request)

    def _validate_file_paths(self, request) -> Optional[str]:
        """Extend validation to cover the working directory path."""

        files = self.get_request_files(request)
        if files:
            expanded_files: list[str] = []
            for file_path in files:
                expanded = os.path.expanduser(file_path)
                if not os.path.isabs(expanded):
                    return (
                        "Error: All file paths must be FULL absolute paths to real files / folders - DO NOT SHORTEN. "
                        f"Received: {file_path}"
                    )
                expanded_files.append(expanded)
            self.set_request_files(request, expanded_files)

        error = super()._validate_file_paths(request)
        if error:
            return error

        working_directory = request.working_directory_absolute_path
        if working_directory:
            expanded = os.path.expanduser(working_directory)
            if not os.path.isabs(expanded):
                return (
                    "Error: 'working_directory_absolute_path' must be an absolute path (you may use '~' which will be expanded). "
                    f"Received: {working_directory}"
                )
            if not os.path.isdir(expanded):
                return (
                    "Error: 'working_directory_absolute_path' must reference an existing directory. "
                    f"Received: {working_directory}"
                )
        return None

    def format_response(self, response: str, request: ChatRequest, model_info: Optional[dict] = None) -> str:
        """
        Format the chat response to match the original Chat tool exactly.
        """
        self._last_recordable_response = None
        body = response
        recordable_override: Optional[str] = None

        if self._model_supports_code_generation():
            block, remainder, _ = self._extract_generated_code_block(response)
            if block:
                sanitized_text = remainder.strip()
                target_directory = request.working_directory_absolute_path
                try:
                    artifact_path = self._persist_generated_code_block(block, target_directory)
                except Exception as exc:  # pragma: no cover - rare filesystem failures
                    logger.error("Failed to persist generated code block: %s", exc, exc_info=True)
                    warning = (
                        f"WARNING: Unable to write pal_generated.code inside '{target_directory}'. "
                        "Check the path permissions and re-run. The generated code block is included below for manual handling."
                    )

                    history_copy_base = sanitized_text
                    history_copy = self._join_sections(history_copy_base, warning) if history_copy_base else warning
                    recordable_override = history_copy

                    sanitized_warning = history_copy.strip()
                    body = f"{sanitized_warning}\n\n{block.strip()}".strip()
                else:
                    if not sanitized_text:
                        base_message = (
                            "Generated code saved to pal_generated.code.\n"
                            "\n"
                            "CRITICAL: Contains mixed instructions + partial snippets - NOT complete code to copy as-is!\n"
                            "\n"
                            "You MUST:\n"
                            "  1. Read as a proposal from partial context - you may need to read the file in sections\n"
                            "  2. Implement ideas using YOUR complete codebase context and understanding\n"
                            "  3. Never paste wholesale - snippets may be partial with missing lines, pasting will corrupt your code!\n"
                            "  4. Adapt to fit your actual structure and style\n"
                            "  5. Build/lint/test after implementation to verify correctness\n"
                            "\n"
                            "Treat as guidance to implement thoughtfully, not ready-to-paste code."
                        )
                        sanitized_text = base_message

                    instruction = self._build_agent_instruction(artifact_path)
                    body = self._join_sections(sanitized_text, instruction)

        final_output = (
            f"{body}\n\n---\n\nAGENT'S TURN: Evaluate this perspective alongside your analysis to "
            "form a comprehensive solution and continue with the user's request and task at hand."
        )

        if recordable_override is not None:
            self._last_recordable_response = (
                f"{recordable_override}\n\n---\n\nAGENT'S TURN: Evaluate this perspective alongside your analysis to "
                "form a comprehensive solution and continue with the user's request and task at hand."
            )
        else:
            self._last_recordable_response = final_output

        return final_output

    def _record_assistant_turn(
        self, continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        recordable = self._last_recordable_response if self._last_recordable_response is not None else response_text
        try:
            super()._record_assistant_turn(continuation_id, recordable, request, model_info)
        finally:
            self._last_recordable_response = None

    def _model_supports_code_generation(self) -> bool:
        context = getattr(self, "_model_context", None)
        if not context:
            return False

        try:
            capabilities = context.capabilities
        except Exception:  # pragma: no cover - defensive fallback
            return False

        return bool(capabilities.allow_code_generation)

    def _extract_generated_code_block(self, text: str) -> tuple[Optional[str], str, int]:
        matches = list(re.finditer(r"<GENERATED-CODE>.*?</GENERATED-CODE>", text, flags=re.DOTALL | re.IGNORECASE))
        if not matches:
            return None, text, 0

        last_match = matches[-1]
        block = last_match.group(0).strip()

        # Merge the text before and after the final block while trimming excess whitespace
        before = text[: last_match.start()]
        after = text[last_match.end() :]
        remainder = self._join_sections(before, after)

        return block, remainder, len(matches)

    def _persist_generated_code_block(self, block: str, working_directory: str) -> Path:
        expanded = os.path.expanduser(working_directory)
        target_dir = Path(expanded).resolve()
        if not target_dir.is_dir():
            raise FileNotFoundError(f"Absolute working directory path '{working_directory}' does not exist")

        target_file = target_dir / "pal_generated.code"
        content = block if block.endswith("\n") else f"{block}\n"
        with self._artifact_write_lock:
            if target_file.exists():
                try:
                    target_file.unlink()
                except OSError as exc:
                    logger.warning("Unable to remove existing pal_generated.code: %s", exc)
            target_file.write_text(content, encoding="utf-8")
        logger.info("Generated code artifact written to %s", target_file)
        return target_file

    @staticmethod
    def _build_agent_instruction(artifact_path: Path) -> str:
        return (
            f"CONTINUING FROM PREVIOUS DISCUSSION: Implementation plan saved to `{artifact_path}`.\n"
            "\n"
            f"CRITICAL WARNING: `{artifact_path}` may contain partial code snippets from another AI with limited context. "
            "Wholesale copy-pasting MAY CORRUPT your codebase with incomplete logic and missing lines.\n"
            "\n"
            "Required workflow:\n"
            "1. For <UPDATED_EXISTING_FILE:...> blocks: Partial excerpts only. Understand the intent and implement using YOUR full context. "
            "DO NOT copy wholesale - adapt ideas to fit actual structure.\n"
            "2. For <NEWFILE:...> blocks: Understand proposal and create properly. Verify completeness (imports, syntax, logic).\n"
            "3. Validation: After ALL changes, verify correctness using available tools (build/compile, linters, tests, type checks, etc.).\n"
            f"4. Cleanup: After you're done reading and applying changes, delete `{artifact_path}` once verified to prevent stale instructions.\n"
            "\n"
            "Treat this as a patch-set requiring manual integration, not ready-to-paste code. You have full codebase context - use it."
        )

    @staticmethod
    def _join_sections(*sections: str) -> str:
        chunks: list[str] = []
        for section in sections:
            if section:
                trimmed = section.strip()
                if trimmed:
                    chunks.append(trimmed)
        return "\n\n".join(chunks)

    def get_websearch_guidance(self) -> str:
        """
        Return Chat tool-style web search guidance.
        """
        return self.get_chat_style_websearch_guidance()


logger = logging.getLogger(__name__)
