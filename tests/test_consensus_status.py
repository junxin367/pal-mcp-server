"""Tests for the read-only consensus background task status tool."""

from __future__ import annotations

import json

import pytest

from tools.consensus_status import ConsensusStatusTool
from utils.consensus_tasks import get_consensus_task_manager


@pytest.fixture(autouse=True)
def reset_task_manager():
    manager = get_consensus_task_manager()
    manager.reset_for_testing()
    yield manager
    manager.reset_for_testing()


def test_status_tool_is_read_only_and_model_free() -> None:
    tool = ConsensusStatusTool()

    assert tool.get_name() == "consensus_status"
    assert tool.requires_model() is False
    assert tool.get_annotations() == {"readOnlyHint": True}
    assert tool.get_input_schema()["required"] == ["task_id"]


@pytest.mark.asyncio
async def test_status_tool_returns_pending_snapshot(reset_task_manager) -> None:
    task_id = reset_task_manager.create_record([{"model": "a", "stance": "neutral"}])

    result = await ConsensusStatusTool().execute({"task_id": task_id})
    payload = json.loads(result[0].text)

    assert payload["status"] == "pending"
    assert payload["completed_models"] == 0
    assert payload["total_models"] == 1


@pytest.mark.asyncio
async def test_status_tool_returns_completed_result(reset_task_manager) -> None:
    async def worker() -> dict:
        return {"status": "consensus_workflow_complete"}

    task_id = reset_task_manager.create_record([{"model": "a"}])
    reset_task_manager.start(task_id, worker())
    await reset_task_manager.wait(task_id, 1)

    result = await ConsensusStatusTool().execute({"task_id": task_id})
    payload = json.loads(result[0].text)

    assert payload["status"] == "completed"
    assert payload["result"]["status"] == "consensus_workflow_complete"

    repeated_result = await ConsensusStatusTool().execute({"task_id": task_id})
    assert json.loads(repeated_result[0].text) == payload


@pytest.mark.asyncio
async def test_status_tool_returns_failed_task(reset_task_manager) -> None:
    async def worker() -> dict:
        raise RuntimeError("task-level failure")

    task_id = reset_task_manager.create_record([{"model": "a"}])
    reset_task_manager.start(task_id, worker())
    await reset_task_manager.wait(task_id, 1)

    result = await ConsensusStatusTool().execute({"task_id": task_id})
    payload = json.loads(result[0].text)

    assert payload["status"] == "failed"
    assert payload["error"] == "task-level failure"


@pytest.mark.asyncio
async def test_status_tool_returns_not_found() -> None:
    result = await ConsensusStatusTool().execute({"task_id": "missing"})
    payload = json.loads(result[0].text)

    assert payload["status"] == "not_found"


def test_status_tool_follows_consensus_filtering() -> None:
    from server import apply_tool_filter

    tools = {
        "consensus": object(),
        "consensus_status": object(),
        "chat": object(),
    }

    status_only_disabled = apply_tool_filter(tools, {"consensus_status"})
    assert "consensus" in status_only_disabled
    assert "consensus_status" in status_only_disabled

    consensus_disabled = apply_tool_filter(tools, {"consensus"})
    assert "consensus" not in consensus_disabled
    assert "consensus_status" not in consensus_disabled
