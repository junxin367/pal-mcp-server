# Chat 同步超时与后台回退设计

## 目标

为 `chat` 增加与并行 Consensus 一致的同步优先、后台回退机制，避免模型请求超过 MCP 客户端等待上限后直接丢失结果。

## 执行语义

- Chat 默认保持同步调用体验。
- 同步等待上限默认 240 秒。
- 240 秒内完成时，返回格式与现有 Chat 完全一致。
- 超过 240 秒仍未完成时，返回 `task_id`，并引导调用方使用 `chat_status` 查询。
- 后台最多继续运行 360 秒，从任务开始计算的总执行上限为 600 秒。
- 完成或失败的任务默认保留 10800 秒。
- PAL 进程重启后，进程内任务记录失效。

## 组件

### `ChatTool`

`ChatTool.execute()` 负责创建后台任务、同步等待和超时响应。实际单次 Chat 流程由独立的 `ChatTool` 实例执行，避免服务器注册的工具单例在多个并发请求之间共享 `_current_arguments`、`_model_context` 等可变状态。

### `ChatTaskManager`

进程内保存任务状态、结果、失败原因和过期时间。任务状态包括 `pending`、`running`、`completed`、`failed`。

同一个 `continuation_id` 使用可转移的执行租约串行化。租约在服务器重建会话前获取，并在同步或后台 Chat 工作流真正结束后释放，避免用户消息和模型响应乱序。并发请求不会在 240 秒同步窗口之外排队，而是立即返回 `conversation_in_progress`；活动 Chat 已生成后台任务时同时返回其 `task_id`。

### `ChatStatusTool`

- 任务仍在运行时返回进度信息。
- 任务完成时直接返回原始 Chat 响应。
- 任务失败、过期或服务重启时返回结构化错误。

## 非阻塞执行

Chat 的 Provider 接口当前是同步调用。Chat 通过 `asyncio.to_thread()` 执行 Provider 请求，使 MCP 事件循环可以在同步等待到期后及时返回 `task_id`。空响应重试也走同一非阻塞调用入口。

OpenAI-compatible Provider 同时接收绝对截止时间，并把剩余时间作为 SDK 请求级 `timeout`；SDK 内部重试关闭，由 PAL 自己的截止时间感知重试循环负责。对于不支持请求级取消的 Provider，600 秒表示 PAL 不再接受迟到结果，底层网络调用仍可能由 Provider SDK 自行结束。

## 配置

- `CHAT_SYNC_WAIT_SECONDS=240`
- `CHAT_BACKGROUND_WAIT_SECONDS=360`
- `CHAT_TASK_TTL_SECONDS=10800`

配置值必须为正数；无效值回退到默认值。

## 验证

- 240 秒内完成时保持原始返回格式。
- 同步期限到期后返回 `task_id`，任务继续执行。
- `chat_status` 能查询运行状态并返回最终原始结果。
- 总执行上限到期后任务标记为失败。
- 未知或过期任务返回 `not_found`。
- 现有 Chat 与 Consensus 回归测试保持通过。
