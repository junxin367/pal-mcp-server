"""Regression coverage for process-local consensus task management."""

from __future__ import annotations

import asyncio

import pytest

from utils.consensus_tasks import ConsensusTaskManager


@pytest.mark.asyncio
async def test_wait_timeout_keeps_task_running() -> None:
    manager = ConsensusTaskManager(ttl_seconds=60)
    release = asyncio.Event()

    async def worker() -> dict:
        await release.wait()
        return {"status": "done"}

    task_id = manager.create_record([{"model": "a", "stance": "neutral"}])
    manager.start(task_id, worker())

    assert await manager.wait(task_id, 0.01) is None
    assert manager.get_snapshot(task_id)["status"] == "pending"

    release.set()
    assert await manager.wait(task_id, 1) == {"status": "done"}
    snapshot = manager.get_snapshot(task_id)
    assert snapshot["status"] == "completed"
    assert snapshot["result"] == {"status": "done"}


@pytest.mark.asyncio
async def test_cancelling_waiter_keeps_task_running() -> None:
    manager = ConsensusTaskManager(ttl_seconds=60)
    release = asyncio.Event()

    async def worker() -> dict:
        await release.wait()
        return {"status": "done"}

    task_id = manager.create_record([{"model": "a"}])
    manager.start(task_id, worker())
    waiter = asyncio.create_task(manager.wait(task_id, 10))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert manager.get_snapshot(task_id)["status"] == "pending"
    release.set()
    assert await manager.wait(task_id, 1) == {"status": "done"}


@pytest.mark.asyncio
async def test_model_progress_is_counted_once() -> None:
    manager = ConsensusTaskManager(ttl_seconds=60)

    async def worker() -> dict:
        manager.mark_model_running(task_id, 0)
        manager.mark_model_finished(task_id, 0, "completed")
        manager.mark_model_finished(task_id, 0, "completed")
        return {"status": "done"}

    task_id = manager.create_record(
        [
            {"model": "a", "stance": "for"},
            {"model": "b", "stance": "against"},
        ]
    )
    manager.start(task_id, worker())
    await manager.wait(task_id, 1)

    snapshot = manager.get_snapshot(task_id)
    assert snapshot["completed_models"] == 1
    assert snapshot["models"][0]["status"] == "completed"
    assert snapshot["models"][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_failed_task_snapshot_contains_error() -> None:
    manager = ConsensusTaskManager(ttl_seconds=60)

    async def worker() -> dict:
        raise RuntimeError("boom")

    task_id = manager.create_record([{"model": "a"}])
    manager.start(task_id, worker())
    result = await manager.wait(task_id, 1)

    assert result is not None
    assert result["status"] == "consensus_failed"
    snapshot = manager.get_snapshot(task_id)
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "boom"


@pytest.mark.asyncio
async def test_task_timeout_marks_failed_and_retains_timeout_status() -> None:
    now = [100.0]
    manager = ConsensusTaskManager(ttl_seconds=10, clock=lambda: now[0])
    release = asyncio.Event()

    async def worker() -> dict:
        await release.wait()
        return {"status": "done"}

    task_id = manager.create_record([{"model": "a"}])
    manager.start(task_id, worker(), timeout_seconds=0.01)
    result = await manager.wait(task_id, 1)

    assert result is not None
    assert result["status"] == "consensus_failed"
    assert result["error"] == "Consensus 总执行时间超过 0.01 秒"

    snapshot = manager.get_snapshot(task_id)
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "Consensus 总执行时间超过 0.01 秒"

    now[0] = 111.0
    assert manager.get_snapshot(task_id)["status"] == "not_found"


def test_default_total_timeout_error_is_reported_as_ten_minutes() -> None:
    assert ConsensusTaskManager._build_timeout_error(600) == "Consensus 总执行时间超过 10 分钟（600 秒）"


@pytest.mark.asyncio
async def test_running_task_does_not_expire_before_completion() -> None:
    now = [100.0]
    manager = ConsensusTaskManager(ttl_seconds=10, clock=lambda: now[0])
    release = asyncio.Event()

    async def worker() -> dict:
        await release.wait()
        return {"status": "done"}

    task_id = manager.create_record([{"model": "a"}])
    manager.start(task_id, worker())
    now[0] = 111.0

    snapshot = manager.get_snapshot(task_id)
    assert snapshot["status"] == "pending"

    release.set()
    await manager.wait(task_id, 1)
    now[0] = 122.0

    snapshot = manager.get_snapshot(task_id)
    assert snapshot["status"] == "not_found"
