"""Read-only status lookup for background consensus tasks."""

from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent
from pydantic import Field

from tools.shared.base_models import ToolRequest
from tools.shared.base_tool import BaseTool
from utils.consensus_tasks import get_consensus_task_manager


class ConsensusStatusRequest(ToolRequest):
    """Request payload for looking up a background consensus task."""

    task_id: str = Field(..., min_length=1, description="耗时较长的 consensus 调用返回的任务 ID。")


class ConsensusStatusTool(BaseTool):
    """Return task progress or the final result without calling an AI model."""

    def get_name(self) -> str:
        return "consensus_status"

    def get_description(self) -> str:
        return "查询超过同步等待时间后仍在后台运行的 consensus 任务进度和最终结果。"

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "耗时较长的 consensus 调用返回的任务 ID。",
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
        return ConsensusStatusRequest

    def requires_model(self) -> bool:
        return False

    async def prepare_prompt(self, request: ConsensusStatusRequest) -> str:  # noqa: ARG002
        return ""

    def format_response(
        self,
        response: str,
        request: ConsensusStatusRequest,  # noqa: ARG002
        model_info: dict | None = None,  # noqa: ARG002
    ) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        request = ConsensusStatusRequest(**arguments)
        snapshot = get_consensus_task_manager().get_snapshot(request.task_id)
        return [
            TextContent(
                type="text",
                text=json.dumps(snapshot, indent=2, ensure_ascii=False),
            )
        ]
