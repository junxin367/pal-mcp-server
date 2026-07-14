# Chat Background Fallback Implementation Plan

> **For agentic workers:** 在当前会话内按顺序执行；禁止使用 worktree，禁止执行 `git add`、`git commit` 或 `git push`。

**Goal:** 为 Chat 增加 240 秒同步等待、360 秒后台续跑、600 秒总上限和状态查询能力。

**Architecture:** 使用独立的 Chat 任务管理器保存进程内任务状态。`ChatTool` 为每次请求创建独立工作实例，并将同步 Provider 调用移入线程，确保 MCP 事件循环能按时返回任务 ID。

**Tech Stack:** Python 3.9+、asyncio、Pydantic、MCP TextContent、pytest。

## Global Constraints

- 不修改 Chat 在同步完成时的响应格式。
- 不把 Chat 默认改成纯异步调用。
- 不使用 Git worktree。
- 不执行任何 Git 写操作。
- 不使用 TDD 红绿流程；完成实现后补充长期回归测试。

---

### Task 1: Chat 任务状态管理

**Files:**
- Create: `utils/chat_tasks.py`
- Modify: `config.py`
- Modify: `providers/base.py`
- Modify: `providers/openai_compatible.py`

**Interfaces:**
- Produces: `ChatTaskManager`、`get_chat_task_manager()`。
- Produces: `CHAT_SYNC_WAIT_SECONDS`、`CHAT_BACKGROUND_WAIT_SECONDS`、`CHAT_TASK_TTL_SECONDS`。

- [x] 增加 Chat 超时和任务保留配置。
- [x] 实现任务创建、启动、同步等待、结果读取、失败处理、总超时和过期清理。
- [x] 保证同步等待超时不会取消后台任务。
- [x] 将同一截止时间下沉到 OpenAI-compatible 请求和重试层。

### Task 2: Chat 状态查询工具

**Files:**
- Create: `tools/chat_status.py`
- Modify: `tools/__init__.py`
- Modify: `server.py`

**Interfaces:**
- Consumes: `get_chat_task_manager()`。
- Produces: MCP 工具 `chat_status(task_id: str)`。

- [x] 运行中任务返回结构化状态。
- [x] 完成任务直接返回原始 Chat `TextContent`。
- [x] 失败和不存在任务返回结构化错误。
- [x] 将工具注册到 PAL 工具目录。

### Task 3: Chat 同步优先与后台回退

**Files:**
- Modify: `tools/chat.py`
- Modify: `tools/simple/base.py`

**Interfaces:**
- Produces: `SimpleTool._generate_model_response(...)` 异步扩展点。
- Consumes: Chat 任务管理器和三个 Chat 超时配置。

- [x] 将 SimpleTool 的首轮生成和空响应重试统一经过异步扩展点。
- [x] Chat 覆盖扩展点，使用 `asyncio.to_thread()` 调用同步 Provider。
- [x] Chat 使用隔离的工作实例执行原有单次流程，并保留实例扩展行为。
- [x] 同一 `continuation_id` 在会话重建前获取执行租约，后台结束后释放。
- [x] 240 秒内完成时直接返回原始结果。
- [x] 超时后返回 `task_id`、总时限和 `chat_status` 调用提示。

### Task 4: 回归验证

**Files:**
- Modify: `tests/test_chat_simple.py`
- Modify: `tests/test_tools.py`

- [x] 覆盖同步完成、后台回退、状态查询、总超时和未知任务。
- [x] 运行 Chat、Consensus 和工具注册相关测试。
- [x] 检查工作区，确认没有生成临时测试文件或意外文件。
