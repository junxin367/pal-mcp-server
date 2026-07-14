"""Process-local task management for long-running Chat requests."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Callable

from config import CHAT_TASK_TTL_SECONDS

logger = logging.getLogger(__name__)

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"


@dataclass
class ChatTaskRecord:
    """Mutable process-local state for one Chat request."""

    task_id: str
    created_at: float
    expires_at: float
    model: str
    state: str = TASK_PENDING
    result: list[Any] | None = None
    error: str | None = None
    exception: BaseException | None = None
    retain_exception: bool = True
    task: asyncio.Task[list[Any]] | None = None


@dataclass
class _ExecutionLockRecord:
    """Reference-counted keyed lock used to preserve continuation ordering."""

    lock: asyncio.Lock
    users: int = 0
    owner_tool: str | None = None
    task_id: str | None = None


class ChatExecutionLease:
    """Transferable ownership of a keyed Chat execution lock."""

    def __init__(
        self,
        release_callback: Callable[[], None],
        bind_task_callback: Callable[[str], None],
    ) -> None:
        self._release_callback = release_callback
        self._bind_task_callback = bind_task_callback
        self._transferred = False
        self._released = False

    def transfer(self) -> None:
        """Transfer release responsibility from the request dispatcher to the worker."""
        self._transferred = True

    def bind_task(self, task_id: str) -> None:
        """Expose the active background task to competing continuation calls."""
        self._bind_task_callback(task_id)

    def release_if_untransferred(self) -> None:
        """Release when execution failed before the background worker took ownership."""
        if not self._transferred:
            self.release()

    def release(self) -> None:
        """Release the keyed lock exactly once."""
        if self._released:
            return
        self._released = True
        self._release_callback()


class ChatTaskManager:
    """Track Chat tasks, results, failures, and expiry within one PAL process."""

    def __init__(
        self,
        ttl_seconds: int = CHAT_TASK_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._records: dict[str, ChatTaskRecord] = {}
        self._execution_locks: dict[str, _ExecutionLockRecord] = {}
        self._lock = threading.RLock()

    async def acquire_execution_lease(self, key: str) -> ChatExecutionLease:
        """Acquire a keyed lock and return transferable release ownership."""
        with self._lock:
            lock_record = self._execution_locks.get(key)
            if lock_record is None:
                lock_record = _ExecutionLockRecord(lock=asyncio.Lock())
                self._execution_locks[key] = lock_record
            lock_record.users += 1

        try:
            await lock_record.lock.acquire()
        except BaseException:
            self._release_execution_lock(key, lock_record, acquired=False)
            raise

        return ChatExecutionLease(
            lambda: self._release_execution_lock(key, lock_record, acquired=True),
            lambda task_id: self._bind_execution_task(key, lock_record, task_id),
        )

    async def try_acquire_execution_lease(
        self,
        key: str,
        *,
        owner_tool: str,
    ) -> tuple[ChatExecutionLease | None, dict[str, str | None]]:
        """Acquire immediately or report the operation already owning this key."""
        with self._lock:
            existing = self._execution_locks.get(key)
            if existing is not None and existing.users > 0:
                return None, {
                    "owner_tool": existing.owner_tool,
                    "task_id": existing.task_id,
                }

            lock_record = _ExecutionLockRecord(
                lock=asyncio.Lock(),
                users=1,
                owner_tool=owner_tool,
            )
            self._execution_locks[key] = lock_record

        try:
            await lock_record.lock.acquire()
        except BaseException:
            self._release_execution_lock(key, lock_record, acquired=False)
            raise
        return (
            ChatExecutionLease(
                lambda: self._release_execution_lock(key, lock_record, acquired=True),
                lambda task_id: self._bind_execution_task(key, lock_record, task_id),
            ),
            {
                "owner_tool": owner_tool,
                "task_id": None,
            },
        )

    def _bind_execution_task(
        self,
        key: str,
        lock_record: _ExecutionLockRecord,
        task_id: str,
    ) -> None:
        with self._lock:
            if self._execution_locks.get(key) is lock_record:
                lock_record.task_id = task_id

    def _release_execution_lock(
        self,
        key: str,
        lock_record: _ExecutionLockRecord,
        *,
        acquired: bool,
    ) -> None:
        if acquired and lock_record.lock.locked():
            lock_record.lock.release()
        with self._lock:
            lock_record.users -= 1
            if lock_record.users == 0 and self._execution_locks.get(key) is lock_record:
                self._execution_locks.pop(key, None)

    def create_record(self, model: str) -> str:
        """Create a task record before scheduling its worker coroutine."""
        self._cleanup_expired()
        task_id = str(uuid.uuid4())
        now = self._clock()
        record = ChatTaskRecord(
            task_id=task_id,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            model=model,
        )
        with self._lock:
            self._records[task_id] = record
        return task_id

    def start(
        self,
        task_id: str,
        worker: Coroutine[Any, Any, list[Any]],
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Start a previously registered Chat worker."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                worker.close()
                raise KeyError(f"未找到 Chat 任务 '{task_id}'")
            if record.task is not None:
                worker.close()
                raise RuntimeError(f"Chat 任务 '{task_id}' 已经启动")

            task = asyncio.create_task(
                self._run_worker(task_id, worker, timeout_seconds),
                name=f"chat-{task_id}",
            )
            record.task = task
            task.add_done_callback(self._consume_task_exception)

    async def _run_worker(
        self,
        task_id: str,
        worker: Coroutine[Any, Any, list[Any]],
        timeout_seconds: float | None,
    ) -> list[Any]:
        self._set_task_state(task_id, TASK_RUNNING)
        try:
            if timeout_seconds is None:
                result = await worker
            else:
                result = await asyncio.wait_for(worker, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            error = self._build_timeout_error(timeout_seconds)
            self._finish_task(task_id, TASK_FAILED, error=error, exception=RuntimeError(error))
            return []
        except asyncio.CancelledError:
            error = "Chat 任务已取消"
            self._finish_task(task_id, TASK_FAILED, error=error, exception=RuntimeError(error))
            return []
        except Exception as exc:
            logger.exception("Chat task %s failed", task_id)
            self._finish_task(task_id, TASK_FAILED, error=str(exc), exception=exc)
            return []

        self._finish_task(task_id, TASK_COMPLETED, result=result)
        return result

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[list[Any]]) -> None:
        """Consume unexpected task exceptions so the event loop stays quiet."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected unhandled Chat task exception")

    @staticmethod
    def _build_timeout_error(timeout_seconds: float) -> str:
        if timeout_seconds >= 60 and timeout_seconds % 60 == 0:
            timeout_minutes = timeout_seconds / 60
            return f"Chat 总执行时间超过 {timeout_minutes:g} 分钟（{timeout_seconds:g} 秒）"
        return f"Chat 总执行时间超过 {timeout_seconds:g} 秒"

    async def wait(self, task_id: str, timeout_seconds: float) -> list[Any] | None:
        """Wait without cancelling the task when the synchronous deadline expires."""
        with self._lock:
            record = self._records.get(task_id)
            task = record.task if record else None
        if task is None:
            return None

        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None

    def get_snapshot(self, task_id: str) -> dict[str, Any]:
        """Return a JSON-serializable task snapshot without embedding the Chat result."""
        self._cleanup_expired()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return {
                    "status": "not_found",
                    "task_id": task_id,
                    "error": "任务不存在、已过期，或 PAL 服务已经重启",
                }

            status = TASK_PENDING if record.state in {TASK_PENDING, TASK_RUNNING} else record.state
            snapshot: dict[str, Any] = {
                "status": status,
                "task_id": task_id,
                "model": record.model,
            }
            if record.state == TASK_COMPLETED:
                snapshot["result_available"] = True
            elif record.state == TASK_FAILED:
                snapshot["error"] = record.error or "Chat 任务执行失败"
            return snapshot

    def get_result(self, task_id: str) -> list[Any] | None:
        """Return the original Chat result after successful completion."""
        self._cleanup_expired()
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.state != TASK_COMPLETED:
                return None
            return record.result

    def get_exception(self, task_id: str) -> BaseException | None:
        """Return the original synchronous-stage exception for a failed task."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.state != TASK_FAILED:
                return None
            return record.exception

    def mark_exposed(self, task_id: str) -> None:
        """Stop retaining traceback-bearing exceptions after a task ID is returned."""
        with self._lock:
            record = self._records.get(task_id)
            if record is not None:
                record.retain_exception = False
                record.exception = None

    def discard(self, task_id: str) -> None:
        """Discard a task whose identifier was never exposed to the caller."""
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
        result: list[Any] | None = None,
        error: str | None = None,
        exception: BaseException | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.state = state
            record.result = result
            record.error = error
            record.exception = exception if record.retain_exception else None
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


_task_manager: ChatTaskManager | None = None
_task_manager_lock = threading.Lock()


def get_chat_task_manager() -> ChatTaskManager:
    """Return the process-local singleton Chat task manager."""
    global _task_manager
    if _task_manager is None:
        with _task_manager_lock:
            if _task_manager is None:
                _task_manager = ChatTaskManager()
    return _task_manager
