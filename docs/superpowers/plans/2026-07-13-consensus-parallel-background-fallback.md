# Consensus 并行执行与后台回退实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Consensus 改为单次调用内最多 3 个模型并行执行，并在同步等待 240 秒后自动返回可通过 `consensus_status` 查询的后台任务 ID。

**Architecture:** `ConsensusTool` 使用局部不可变输入创建进程内任务，模型调用通过 `asyncio.to_thread()` 脱离 MCP 事件循环，并由 Semaphore 限制并发。`ConsensusTaskManager` 保存任务、进度和最终结果；首次调用使用 `asyncio.wait_for(asyncio.shield(...))` 同步等待，超时后由独立的只读 `consensus_status` 工具查询。

**Tech Stack:** Python 3.10+、asyncio、dataclasses、Pydantic、MCP `TextContent`、pytest/pytest-asyncio。

## Global Constraints

- 不创建或使用 Git worktree。
- 不修改 Provider 现有 HTTP 超时、重试次数或网关配置。
- 默认最大并发数必须是 3。
- 首次调用默认同步等待 240 秒。
- 后台任务状态默认保留 10800 秒。
- 默认仍是同步返回；只有超过内部等待阈值才返回 `task_id`。
- 模型之间保持 blinded consensus，不传递其他模型的结果。
- 不采用红绿式 TDD；新增测试必须具备长期回归价值。
- 开发期间不做分阶段提交；全部实现和验证完成后统一提交一次。
- 所有面向用户的文档和提交说明使用简体中文。

---

## 文件结构

- Create: `utils/consensus_tasks.py`
  进程内 Consensus 任务注册、进度更新、同步等待、TTL 清理和查询快照。
- Create: `tools/consensus_status.py`
  无模型调用的只读状态查询工具。
- Modify: `config.py`
  定义并解析并发数、同步等待时间和任务 TTL。
- Modify: `tools/consensus.py`
  将多步串行咨询替换为单次并行任务，构造兼容响应并接入任务管理器。
- Modify: `tools/__init__.py`
  导出 `ConsensusStatusTool`。
- Modify: `server.py`
  注册 `consensus_status`，并确保 Consensus 可用时查询工具也可用。
- Modify: `tests/test_consensus.py`
  验证并行上限、顺序、上下文隔离、同步返回和后台回退。
- Modify: `tests/test_model_resolution_bug.py`
  移除对旧实例级 proposal 状态的测试依赖。
- Create: `tests/test_consensus_tasks.py`
  验证任务管理器生命周期、状态快照、失败和 TTL。
- Create: `tests/test_consensus_status.py`
  验证状态工具 schema 和各类返回。
- Modify: `simulator_tests/test_consensus_workflow_accurate.py`
  将旧多步串行场景更新为单次并行完成。
- Modify: `simulator_tests/test_consensus_three_models.py`
  验证三个模型的完整结果、计数和稳定顺序。
- Modify: `simulator_tests/test_consensus_conversation.py`
  验证完整 Consensus 结果写入已有会话并可跨工具继续。
- Modify: `docs/tools/consensus.md`
  更新并行行为、配置和查询示例。

---

### Task 1: Consensus 配置和任务管理器

**Files:**
- Modify: `config.py:71`
- Create: `utils/consensus_tasks.py`
- Create: `tests/test_consensus_tasks.py`

**Interfaces:**
- Produces: `CONSENSUS_MAX_CONCURRENCY: int`
- Produces: `CONSENSUS_SYNC_WAIT_SECONDS: float`
- Produces: `CONSENSUS_TASK_TTL_SECONDS: int`
- Produces: `ConsensusTaskManager.create_record(models: list[dict[str, str]]) -> str`
- Produces: `ConsensusTaskManager.start(task_id: str, worker: Coroutine[Any, Any, dict[str, Any]]) -> None`
- Produces: `ConsensusTaskManager.wait(task_id: str, timeout_seconds: float) -> dict[str, Any] | None`
- Produces: `ConsensusTaskManager.get_snapshot(task_id: str) -> dict[str, Any]`
- Produces: `ConsensusTaskManager.mark_model_running(task_id: str, index: int) -> None`
- Produces: `ConsensusTaskManager.mark_model_finished(task_id: str, index: int, status: str) -> None`
- Produces: `ConsensusTaskManager.discard(task_id: str) -> None`
- Produces: `get_consensus_task_manager() -> ConsensusTaskManager`

- [x] **Step 1: 更新 Consensus 配置**

在 `config.py` 中将旧的未使用超时注释替换为正数环境变量解析：

```python
def _get_positive_int(name: str, default: int) -> int:
    raw = get_env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_positive_float(name: str, default: float) -> float:
    raw = get_env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


CONSENSUS_MAX_CONCURRENCY = _get_positive_int("CONSENSUS_MAX_CONCURRENCY", 3)
CONSENSUS_SYNC_WAIT_SECONDS = _get_positive_float("CONSENSUS_SYNC_WAIT_SECONDS", 240.0)
CONSENSUS_TASK_TTL_SECONDS = _get_positive_int("CONSENSUS_TASK_TTL_SECONDS", 10_800)
DEFAULT_CONSENSUS_MAX_INSTANCES_PER_COMBINATION = 2
```

- [x] **Step 2: 实现任务数据结构**

在 `utils/consensus_tasks.py` 中定义：

```python
@dataclass
class ConsensusTaskRecord:
    task_id: str
    created_at: float
    expires_at: float
    models: list[dict[str, str]]
    model_statuses: list[str]
    state: str = "pending"
    completed_models: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[dict[str, Any]] | None = None
```

任务状态只允许 `pending`、`running`、`completed`、`failed`；模型状态允许 `pending`、`running`、`completed`、`error`。

- [x] **Step 3: 实现任务生命周期**

`start()` 使用 `asyncio.create_task()` 启动包装协程；包装协程负责将任务设为 `running`，保存结果或错误，并更新 TTL。`wait()` 必须使用：

```python
try:
    await asyncio.wait_for(asyncio.shield(record.task), timeout=timeout_seconds)
except TimeoutError:
    return None
```

`get_snapshot()` 返回可 JSON 序列化的数据，不暴露 `asyncio.Task`。TTL 从任务完成或失败时重新计算；运行中任务不因结果保留 TTL 到期而取消，避免同步等待阈值大于 TTL 时返回立即失效的 `task_id`。

- [x] **Step 4: 增加任务管理器回归测试**

覆盖以下长期行为：

```python
@pytest.mark.asyncio
async def test_wait_timeout_keeps_task_running():
    ...

@pytest.mark.asyncio
async def test_completed_task_snapshot_contains_result():
    ...

@pytest.mark.asyncio
async def test_failed_task_snapshot_contains_error():
    ...

async def test_running_task_does_not_expire_before_completion():
    ...
```

测试使用毫秒级事件和可注入时钟，不等待真实 240 秒。

- [x] **Step 5: 运行任务管理器测试**

Run:

```powershell
python -m pytest tests/test_consensus_tasks.py -q
```

Expected: 全部通过。

---

### Task 2: Consensus 状态查询工具

**Files:**
- Create: `tools/consensus_status.py`
- Modify: `tools/__init__.py`
- Modify: `server.py:258`
- Create: `tests/test_consensus_status.py`

**Interfaces:**
- Consumes: `get_consensus_task_manager()`
- Produces: `ConsensusStatusRequest(task_id: str)`
- Produces: `ConsensusStatusTool.execute(arguments: dict[str, Any]) -> list[TextContent]`

- [x] **Step 1: 实现只读状态工具**

`ConsensusStatusTool` 继承 `BaseTool`，设置：

```python
def get_name(self) -> str:
    return "consensus_status"

def requires_model(self) -> bool:
    return False

def get_annotations(self) -> dict[str, Any]:
    return {"readOnlyHint": True}
```

`execute()` 校验 `task_id` 后调用任务管理器，返回 `pending`、`completed`、`failed` 或 `not_found`。`pending` 返回模型状态和计数；`completed` 将完整 Consensus 结果放在 `result` 字段。

- [x] **Step 2: 注册工具**

在 `tools/__init__.py` 导出 `ConsensusStatusTool`，在 `server.py` 的 `TOOLS` 中注册：

```python
"consensus_status": ConsensusStatusTool(),
```

修改工具过滤逻辑：当 `consensus` 未禁用时，即使用户只在 `DISABLED_TOOLS` 中误写了 `consensus_status`，也保留查询工具；当 `consensus` 被禁用时同时禁用 `consensus_status`。

- [x] **Step 3: 增加状态工具测试**

覆盖：

```python
def test_status_tool_is_read_only_and_model_free():
    ...

@pytest.mark.asyncio
async def test_status_tool_returns_pending_snapshot():
    ...

@pytest.mark.asyncio
async def test_status_tool_returns_completed_result():
    ...

@pytest.mark.asyncio
async def test_status_tool_returns_not_found():
    ...
```

- [x] **Step 4: 运行状态工具和注册测试**

Run:

```powershell
python -m pytest tests/test_consensus_status.py tests/test_server.py -q
```

Expected: 全部通过。

---

### Task 3: Consensus 单次并行执行

**Files:**
- Modify: `tools/consensus.py:1-645`
- Modify: `tests/test_consensus.py`
- Modify: `tests/test_consensus_integration.py`
- Modify: `tests/test_model_resolution_bug.py`
- Modify: `simulator_tests/test_consensus_workflow_accurate.py`
- Modify: `simulator_tests/test_consensus_three_models.py`
- Modify: `simulator_tests/test_consensus_conversation.py`

**Interfaces:**
- Consumes: `CONSENSUS_MAX_CONCURRENCY`
- Consumes: `CONSENSUS_SYNC_WAIT_SECONDS`
- Consumes: `ConsensusTaskManager`
- Produces: `ConsensusTool._run_parallel_consensus(...) -> dict[str, Any]`
- Produces: `ConsensusTool._consult_model_sync(...) -> dict[str, Any]`
- Produces: Step 1 完整结果或 `consensus_in_progress`

- [x] **Step 1: 更新 schema 描述和兼容行为**

更新模块、字段和类注释，明确所有模型在 Step 1 内并行咨询。保留 workflow 基础字段，但 `step_number > 1` 直接返回：

```json
{
  "status": "consensus_already_completed",
  "consensus_complete": true,
  "next_step_required": false,
  "next_steps": "并行 Consensus 已在 Step 1 完成；如首次返回 task_id，请使用 consensus_status"
}
```

- [x] **Step 2: 将单模型咨询拆成同步核心**

实现：

```python
def _consult_model_sync(
    self,
    model_config: dict[str, Any],
    request: ConsensusRequest,
    original_proposal: str,
) -> dict[str, Any]:
    ...
```

该方法只使用显式传入的 proposal 和 request，不读取 `self.original_proposal`、`self.models_to_consult` 或 `self.accumulated_responses`。保持现有 ModelContext、文件嵌入、stance、temperature 和 Provider 调用行为。

- [x] **Step 3: 实现受限并行执行**

实现 `_run_parallel_consensus()`：

```python
semaphore = asyncio.Semaphore(CONSENSUS_MAX_CONCURRENCY)

async def consult_one(index: int, model_config: dict[str, Any]) -> dict[str, Any]:
    async with semaphore:
        manager.mark_model_running(task_id, index)
        result = await asyncio.to_thread(
            self._consult_model_sync,
            model_config,
            request,
            original_proposal,
        )
        manager.mark_model_finished(task_id, index, "completed" if result["status"] == "success" else "error")
        return result

responses = await asyncio.gather(
    *(consult_one(index, model) for index, model in enumerate(models))
)
```

使用 `gather()` 返回列表的输入顺序保证最终模型顺序稳定。

- [x] **Step 4: 构造完整兼容响应**

完整响应至少包含：

```python
{
    "status": "consensus_workflow_complete",
    "consensus_complete": True,
    "next_step_required": False,
    "model_responses": responses,
    "accumulated_responses": responses,
    "successful_responses": success_count,
    "failed_responses": failure_count,
    "complete_consensus": {
        "initial_prompt": original_proposal,
        "models_consulted": [...],
        "total_responses": len(responses),
        "consensus_confidence": "high" if success_count >= 2 else "partial",
    },
    "agent_analysis": {
        "initial_analysis": request.step,
        "findings": request.findings,
    },
    "next_steps": "...",
}
```

全部模型失败时使用 `status: consensus_failed`，但仍将任务标记为完成并返回所有错误。

- [x] **Step 5: 接入同步等待和后台回退**

Step 1 执行流程：

```python
task_id = manager.create_record(model_descriptors)
manager.start(task_id, self._run_parallel_consensus(...))
result = await manager.wait(task_id, CONSENSUS_SYNC_WAIT_SECONDS)

if result is not None:
    manager.discard(task_id)
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

snapshot = manager.get_snapshot(task_id)
return [TextContent(type="text", text=json.dumps({
    "status": "consensus_in_progress",
    "consensus_complete": False,
    "next_step_required": False,
    "task_id": task_id,
    "completed_models": snapshot["completed_models"],
    "total_models": snapshot["total_models"],
    "next_steps": "使用 consensus_status 并传入 task_id 查询进度或最终结果",
}, indent=2, ensure_ascii=False))]
```

- [x] **Step 6: 保存会话结果**

首次调用继续创建 `continuation_id`。完整结果完成时使用 `add_turn()` 保存一次 assistant turn，不读取共享 workflow 状态；后台完成的结果也执行相同存储逻辑。

- [x] **Step 7: 更新 Consensus 长期回归测试**

删除只验证旧多步串行过程的测试，保留 stance、schema、文件上下文等仍有效测试，并新增：

```python
@pytest.mark.asyncio
async def test_consensus_runs_at_most_three_models_concurrently():
    ...

@pytest.mark.asyncio
async def test_consensus_preserves_model_order():
    ...

@pytest.mark.asyncio
async def test_consensus_model_failure_does_not_cancel_others():
    ...

@pytest.mark.asyncio
async def test_consensus_returns_complete_result_before_sync_deadline():
    ...

@pytest.mark.asyncio
async def test_consensus_returns_task_id_after_sync_deadline():
    ...

@pytest.mark.asyncio
async def test_concurrent_consensus_requests_do_not_share_prompts():
    ...
```

测试通过 monkeypatch 将同步等待阈值设为毫秒级，不等待真实 240 秒。

- [x] **Step 8: 运行 Consensus 测试**

Run:

```powershell
python -m pytest tests/test_consensus.py tests/test_consensus_integration.py tests/test_consensus_tasks.py tests/test_consensus_status.py -q
```

Expected: 全部通过；需要真实 API 的测试按现有 marker 跳过。

实际结果：Consensus 专项与相关回归共 52 项通过。2026-07-14 已将集成测试指定模型从旧的 `gpt-5`/`gpt-5.2` 更新为当前目录中的 `gpt-5.5`/`gpt-5.4`；缺少新录制文件且未配置真实 API Key 时会明确跳过，等待下次有凭据时重新录制。

---

### Task 4: 文档、质量检查和端到端回归

**Files:**
- Modify: `docs/tools/consensus.md`
- Modify: `.env.example`（如果项目已在该文件集中记录可选运行参数）

**Interfaces:**
- Documents: `CONSENSUS_MAX_CONCURRENCY`
- Documents: `CONSENSUS_SYNC_WAIT_SECONDS`
- Documents: `CONSENSUS_TASK_TTL_SECONDS`
- Documents: `consensus_status`

- [x] **Step 1: 更新用户文档**

将 `Sequential processing` 改为：

- 单次调用并行咨询全部模型。
- 默认最大并发数为 3。
- 正常任务直接同步返回。
- 超过 240 秒返回 `task_id`。
- 使用 `consensus_status` 查询 pending/completed/failed/not_found。
- PAL 重启后后台任务 ID 失效。

- [x] **Step 2: 更新环境变量示例**

如果 `.env.example` 已记录 Consensus 配置，增加：

```dotenv
CONSENSUS_MAX_CONCURRENCY=3
CONSENSUS_SYNC_WAIT_SECONDS=240
CONSENSUS_BACKGROUND_WAIT_SECONDS=360
CONSENSUS_TASK_TTL_SECONDS=10800
```

- [x] **Step 3: 运行格式和静态检查**

Run:

```powershell
ruff check config.py utils/consensus_tasks.py tools/consensus.py tools/consensus_status.py tests/test_consensus.py tests/test_consensus_tasks.py tests/test_consensus_status.py
black --check config.py utils/consensus_tasks.py tools/consensus.py tools/consensus_status.py tests/test_consensus.py tests/test_consensus_tasks.py tests/test_consensus_status.py
isort --check-only config.py utils/consensus_tasks.py tools/consensus.py tools/consensus_status.py tests/test_consensus.py tests/test_consensus_tasks.py tests/test_consensus_status.py
```

Expected: 全部退出码为 0。

- [x] **Step 4: 运行非集成测试全集**

Run:

```powershell
python -m pytest tests/ -q -m "not integration"
```

Expected: 全部通过，只有项目既有 skip。

实际结果：`810 passed, 4 skipped, 16 deselected, 63 failed`。63 项失败与改造前基线一致，集中在 Windows GBK/Unix 路径、Bash 部署脚本、旧 cassette 和旧 Provider 模型配置；Consensus 相关测试全部通过。

- [x] **Step 5: 检查工作区和差异**

Run:

```powershell
git diff --check
git status --short
```

Expected: 无空白错误；仅出现本任务预期文件。

- [x] **Step 6: 统一提交全部实现**

```powershell
git add config.py utils/consensus_tasks.py tools/consensus.py tools/consensus_status.py tools/__init__.py server.py tests/test_consensus.py tests/test_consensus_integration.py tests/test_consensus_tasks.py tests/test_consensus_status.py docs/tools/consensus.md .env.example docs/superpowers/plans/2026-07-13-consensus-parallel-background-fallback.md
git commit -m "feat(consensus): 支持并行执行和后台结果查询" -m "- 默认最多并发咨询 3 个模型并保持结果顺序`n- 超过 240 秒后返回任务 ID，支持通过 consensus_status 查询"
```

---

### Task 5: 增加后台 6 分钟与总计 10 分钟执行上限

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `utils/consensus_tasks.py`
- Modify: `tools/consensus.py`
- Modify: `tests/test_consensus_tasks.py`
- Modify: `tests/test_consensus.py`
- Modify: `docs/tools/consensus.md`

**Interfaces:**
- Produces: `CONSENSUS_BACKGROUND_WAIT_SECONDS: float`
- Consumes: `CONSENSUS_SYNC_WAIT_SECONDS + CONSENSUS_BACKGROUND_WAIT_SECONDS`
- Produces: `ConsensusTaskManager.start(..., timeout_seconds: float | None = None)`

- [x] **Step 1: 增加后台等待配置**

新增 `CONSENSUS_BACKGROUND_WAIT_SECONDS=360`，非法或非正数时回退 360 秒。

- [x] **Step 2: 增加任务级总执行超时**

`ConsensusTaskManager` 使用 `asyncio.wait_for()` 限制 worker 总运行时间。超时后任务状态设为 `failed`，默认错误信息为“Consensus 总执行时间超过 10 分钟（600 秒）”。

- [x] **Step 3: 接入 Consensus**

启动任务时传入：

```python
timeout_seconds = CONSENSUS_SYNC_WAIT_SECONDS + CONSENSUS_BACKGROUND_WAIT_SECONDS
```

超时返回 ID 时增加后台剩余时间和总执行上限字段。

- [x] **Step 4: 增加回归测试**

使用毫秒级超时验证：

- 同步等待到期后任务仍运行。
- 总执行上限到期后状态变为 `failed`。
- 超时失败状态按现有 TTL 保留。
- 正常完成任务不受影响。

- [x] **Step 5: 运行验证并统一提交**

运行 Consensus 专项测试、Ruff、Black、isort、compileall 和 `git diff --check`，完成后只提交一次。
