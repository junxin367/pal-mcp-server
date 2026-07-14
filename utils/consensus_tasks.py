"""Process-local task management for long-running parallel consensus requests."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Callable

from config import CONSENSUS_TASK_TTL_SECONDS

logger = logging.getLogger(__name__)

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"

MODEL_PENDING = "pending"
MODEL_RUNNING = "running"
MODEL_COMPLETED = "completed"
MODEL_ERROR = "error"


@dataclass
class ConsensusTaskRecord:
    """Mutable process-local state for one consensus request."""

    task_id: str
    created_at: float
    expires_at: float
    models: list[dict[str, str]]
    model_statuses: list[str]
    state: str = TASK_PENDING
    completed_models: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[dict[str, Any]] | None = None


class ConsensusTaskManager:
    """Track consensus tasks, progress, results, and expiry within one PAL process."""

    def __init__(
        self,
        ttl_seconds: int = CONSENSUS_TASK_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._records: dict[str, ConsensusTaskRecord] = {}
        self._lock = threading.RLock()

    def create_record(self, models: list[dict[str, Any]]) -> str:
        """Create a task record before its worker coroutine is scheduled."""
        self._cleanup_expired()
        task_id = str(uuid.uuid4())
        now = self._clock()
        model_descriptors = [
            {
                "model": str(model.get("model", "unknown")),
                "stance": str(model.get("stance", "neutral")),
            }
            for model in models
        ]
        record = ConsensusTaskRecord(
            task_id=task_id,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            models=model_descriptors,
            model_statuses=[MODEL_PENDING] * len(model_descriptors),
        )
        with self._lock:
            self._records[task_id] = record
        return task_id

    def start(
        self,
        task_id: str,
        worker: Coroutine[Any, Any, dict[str, Any]],
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Start a previously registered consensus worker."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                worker.close()
                raise KeyError(f"未找到 Consensus 任务 '{task_id}'")
            if record.task is not None:
                worker.close()
                raise RuntimeError(f"Consensus 任务 '{task_id}' 已经启动")

            task = asyncio.create_task(
                self._run_worker(task_id, worker, timeout_seconds),
                name=f"consensus-{task_id}",
            )
            record.task = task
            task.add_done_callback(self._consume_task_exception)

    async def _run_worker(
        self,
        task_id: str,
        worker: Coroutine[Any, Any, dict[str, Any]],
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        self._set_task_state(task_id, TASK_RUNNING)
        try:
            if timeout_seconds is None:
                result = await worker
            else:
                result = await asyncio.wait_for(worker, timeout=timeout_seconds)
        except TimeoutError:
            error = self._build_timeout_error(timeout_seconds)
            failure_result = self._build_failure_result(error)
            self._finish_task(task_id, TASK_FAILED, result=failure_result, error=error)
            return failure_result
        except asyncio.CancelledError:
            error = "Consensus 任务已取消"
            failure_result = self._build_failure_result(error)
            self._finish_task(task_id, TASK_FAILED, result=failure_result, error=error)
            return failure_result
        except Exception as exc:
            logger.exception("Consensus task %s failed", task_id)
            error = str(exc)
            failure_result = self._build_failure_result(error)
            self._finish_task(task_id, TASK_FAILED, result=failure_result, error=error)
            return failure_result

        self._finish_task(task_id, TASK_COMPLETED, result=result)
        return result

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[dict[str, Any]]) -> None:
        """Consume unexpected task exceptions so the event loop does not emit warnings."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected unhandled consensus task exception")

    @staticmethod
    def _build_failure_result(error: str) -> dict[str, Any]:
        return {
            "status": "consensus_failed",
            "consensus_complete": True,
            "next_step_required": False,
            "error": error,
        }

    @staticmethod
    def _build_timeout_error(timeout_seconds: float) -> str:
        if timeout_seconds >= 60 and timeout_seconds % 60 == 0:
            timeout_minutes = timeout_seconds / 60
            return f"Consensus 总执行时间超过 {timeout_minutes:g} 分钟（{timeout_seconds:g} 秒）"
        return f"Consensus 总执行时间超过 {timeout_seconds:g} 秒"

    async def wait(self, task_id: str, timeout_seconds: float) -> dict[str, Any] | None:
        """Wait for a task without cancelling it when the synchronous deadline expires."""
        with self._lock:
            record = self._records.get(task_id)
            task = record.task if record else None
        if task is None:
            return None

        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except TimeoutError:
            return None

    def mark_model_running(self, task_id: str, index: int) -> None:
        """Mark a model as actively using its concurrency slot."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or not 0 <= index < len(record.model_statuses):
                return
            if record.model_statuses[index] == MODEL_PENDING:
                record.model_statuses[index] = MODEL_RUNNING

    def mark_model_finished(self, task_id: str, index: int, status: str) -> None:
        """Mark a model as completed or errored and advance progress exactly once."""
        if status not in {MODEL_COMPLETED, MODEL_ERROR}:
            raise ValueError(f"Unsupported consensus model status: {status}")

        with self._lock:
            record = self._records.get(task_id)
            if record is None or not 0 <= index < len(record.model_statuses):
                return
            previous = record.model_statuses[index]
            if previous in {MODEL_COMPLETED, MODEL_ERROR}:
                return
            record.model_statuses[index] = status
            record.completed_models += 1

    def get_snapshot(self, task_id: str) -> dict[str, Any]:
        """Return a JSON-serializable task snapshot."""
        self._cleanup_expired()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return {
                    "status": "not_found",
                    "task_id": task_id,
                    "error": "任务不存在、已过期，或 PAL 服务已经重启",
                }

            models = [
                {
                    "model": model["model"],
                    "stance": model["stance"],
                    "status": record.model_statuses[index],
                }
                for index, model in enumerate(record.models)
            ]
            snapshot: dict[str, Any] = {
                "status": TASK_PENDING if record.state in {TASK_PENDING, TASK_RUNNING} else record.state,
                "task_id": task_id,
                "completed_models": record.completed_models,
                "total_models": len(record.models),
                "models": models,
            }
            if record.state == TASK_COMPLETED:
                snapshot["result"] = record.result
            elif record.state == TASK_FAILED:
                snapshot["error"] = record.error or "Consensus 任务执行失败"
            return snapshot

    def discard(self, task_id: str) -> None:
        """Discard a task record whose identifier was never exposed to the caller."""
        with self._lock:
            self._records.pop(task_id, None)

    def reset_for_testing(self) -> None:
        """Cancel and remove all task records."""
        with self._lock:
            records = list(self._records.values())
            self._records.clear()
        for record in records:
            if record.task is not None and not record.task.done():
                record.task.cancel()

    def _set_task_state(self, task_id: str, state: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is not None:
                record.state = state

    def _finish_task(
        self,
        task_id: str,
        state: str,
        *,
        result: dict[str, Any],
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.state = state
            record.result = result
            record.error = error
            record.expires_at = self._clock() + self._ttl_seconds

    def _cleanup_expired(self) -> None:
        now = self._clock()
        with self._lock:
            expired_ids = [
                task_id
                for task_id, record in self._records.items()
                if record.state in {TASK_COMPLETED, TASK_FAILED} and record.expires_at <= now
            ]
            for task_id in expired_ids:
                self._records.pop(task_id, None)


_task_manager: ConsensusTaskManager | None = None
_task_manager_lock = threading.Lock()


def get_consensus_task_manager() -> ConsensusTaskManager:
    """Return the process-local singleton task manager."""
    global _task_manager
    if _task_manager is None:
        with _task_manager_lock:
            if _task_manager is None:
                _task_manager = ConsensusTaskManager()
    return _task_manager
