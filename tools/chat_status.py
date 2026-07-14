"""Read-only status lookup for background Chat tasks."""

from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent
from pydantic import Field

from tools.shared.base_models import ToolRequest
from tools.shared.base_tool import BaseTool
from utils.chat_tasks import TASK_COMPLETED, get_chat_task_manager


class ChatStatusRequest(ToolRequest):
    """Request payload for looking up a background Chat task."""

    task_id: str = Field(..., min_length=1, description="耗时较长的 chat 调用返回的任务 ID。")


class ChatStatusTool(BaseTool):
    """Return Chat task progress or its original final response."""

    def get_name(self) -> str:
        return "chat_status"

    def get_description(self) -> str:
        return "查询超过同步等待时间后仍在后台运行的 chat 任务；完成时直接返回原始 Chat 结果。"

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "耗时较长的 chat 调用返回的任务 ID。",
                }
            },
            "required": ["task_id"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict[str, Any] | None:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ChatStatusRequest

    def requires_model(self) -> bool:
        return False

    async def prepare_prompt(self, request: ChatStatusRequest) -> str:  # noqa: ARG002
        return ""

    def format_response(
        self,
        response: str,
        request: ChatStatusRequest,  # noqa: ARG002
        model_info: dict | None = None,  # noqa: ARG002
    ) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        request = ChatStatusRequest(**arguments)
        manager = get_chat_task_manager()
        snapshot = manager.get_snapshot(request.task_id)
        if snapshot["status"] == TASK_COMPLETED:
            result = manager.get_result(request.task_id)
            if result:
                return result

        return [
            TextContent(
                type="text",
                text=json.dumps(snapshot, indent=2, ensure_ascii=False),
            )
        ]
