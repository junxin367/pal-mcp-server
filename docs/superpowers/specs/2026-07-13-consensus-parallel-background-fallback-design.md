# Consensus 并行执行与后台回退设计

## 状态

- 日期：2026-07-13
- 状态：已确认
- 目标版本：当前 PAL MCP Server 分支

## 背景

当前 `consensus` 采用多步串行工作流：每次 MCP 调用只咨询一个外部模型，客户端收到该模型结果后再发起下一步。现有 blinded consensus 设计已经保证各模型只看到原始问题、相关文件、图片和自身 stance，不会看到其他模型的回答或客户端对前序结果的总结，因此模型咨询之间不存在数据依赖，可以安全并行。

同时，Codex 对单次 MCP `tools/call` 存在约 300 秒的等待上限。并行执行通常可以直接在该期限内返回；当任务仍未完成时，PAL 需要在内部截止时间前返回可查询的任务 ID，让后台任务继续执行。该后台查询方式只作为慢任务回退，不改变默认同步使用体验。

## 目标

1. 第一次 `consensus` 调用并行咨询全部指定模型。
2. 默认最大并发数为 3，超过并发数的模型分批执行。
3. 正常任务在同一次 MCP 调用中直接返回完整结果。
4. 同步等待超过 240 秒时返回 `task_id`，后台继续执行。
5. 提供独立的 `consensus_status` 工具查询任务进度和最终结果。
6. 保证不同 Consensus 调用之间的上下文和结果完全隔离。
7. 单个模型失败不影响其他模型完成。

## 非目标

- 不修改 Provider 现有 HTTP 超时、重试次数或网关配置。
- 不让模型读取其他模型的回答，不引入辩论链式依赖。
- 不默认采用立即返回任务 ID 的纯异步模式。
- 不为后台任务提供跨进程、跨重启持久化。
- 不改变最终由调用方综合多模型结果的职责。

## 方案选择

采用“同步优先、超时后转后台”方案：

1. 调用开始时在 PAL 内部创建 Consensus 任务。
2. 任务立即按最大并发数 3 启动模型咨询。
3. MCP 调用最多同步等待 240 秒。
4. 任务在 240 秒内完成时，直接返回完整结果，不暴露内部任务 ID。
5. 任务在 240 秒内未完成时，返回 `consensus_in_progress` 和 `task_id`。
6. 调用方随后使用 `consensus_status` 获取进度或完整结果。

不采用以下方案：

- 始终立即返回任务 ID：会降低正常快速任务的使用体验。
- 240 秒后终止任务并只返回部分结果：会丢失仍在执行的模型结果。
- 保持逐模型多步返回：无法获得单次并行执行的主要性能收益。

## 配置

新增以下配置项：

| 配置 | 默认值 | 说明 |
| --- | ---: | --- |
| `CONSENSUS_MAX_CONCURRENCY` | `3` | 同时执行的最大模型数，最小值为 1 |
| `CONSENSUS_SYNC_WAIT_SECONDS` | `240` | 首次调用同步等待时间，超时后返回任务 ID |
| `CONSENSUS_BACKGROUND_WAIT_SECONDS` | `360` | 返回任务 ID 后允许继续运行的最长时间 |
| `CONSENSUS_TASK_TTL_SECONDS` | `10800` | 任务状态和结果保留时间，默认 3 小时 |

环境变量缺失、为空或格式非法时使用默认值。并发数小于 1 时回退为 3；同步等待时间、后台等待时间和 TTL 小于等于 0 时回退到各自默认值。

默认总执行上限为 `240 + 360 = 600` 秒，即从任务开始计算最多 10 分钟。任务在前 240 秒内未完成时返回 `task_id`；后台最多再执行 360 秒。到达总执行上限后任务标记为 `failed`，错误信息说明 Consensus 总执行时间超过 10 分钟。该失败状态从标记时刻起继续按 `CONSENSUS_TASK_TTL_SECONDS` 保留。

## 架构

### ConsensusTool

`ConsensusTool` 负责：

- 校验模型配置。
- 从 Step 1 请求中提取不可变的原始问题、文件、图片和模型列表。
- 创建内部 Consensus 任务。
- 最多等待 240 秒。
- 根据任务状态返回完整结果或后台查询信息。

主执行路径不再依赖 `self.original_proposal`、`self.models_to_consult` 或 `self.accumulated_responses` 等共享实例字段。模型请求所需数据通过不可变任务输入显式传递。

### ConsensusTaskManager

新增进程内任务管理器，负责：

- 生成 UUID 格式的 `task_id`。
- 保存 `asyncio.Task` 引用和任务元数据。
- 记录任务状态：`pending`、`running`、`completed`、`failed`。
- 记录模型总数、已完成数量和最终结果。
- 为首次调用提供带 `asyncio.shield()` 的限时等待。
- 按 TTL 清理已完成或失败的任务记录；运行中任务不使用结果保留 TTL，避免返回立即失效的任务 ID。

任务管理器只在当前 PAL 进程内有效。PAL 重启后旧 `task_id` 返回 `not_found`。

### consensus_status

新增不调用模型的轻量工具，仅接收：

```json
{
  "task_id": "UUID"
}
```

工具立即读取任务状态，不阻塞等待模型完成。

### 模型执行器

每个模型咨询执行以下流程：

1. 使用原始 proposal 构造 prompt。
2. 根据该模型的 `ModelContext` 处理相关文件。
3. 注入该模型自己的 stance system prompt。
4. 获取并校验 temperature。
5. 获取对应 Provider。
6. 在工作线程中调用同步 `provider.generate_content()`。
7. 将成功或失败结果写入任务对应的固定索引。

使用 `asyncio.Semaphore(CONSENSUS_MAX_CONCURRENCY)` 控制并发数。使用 `asyncio.gather()` 汇总任务，最终结果按输入模型顺序返回，而不是按完成顺序返回。

## 数据流

```text
consensus Step 1
      |
      v
创建 task_id 和不可变任务输入
      |
      v
最多 3 个模型并行执行
      |
      +---------------------------+
      |                           |
240 秒内完成                 240 秒仍未完成
      |                           |
      v                           v
直接返回完整结果            返回 task_id 和当前进度
                                  |
                                  v
                         consensus_status(task_id)
                                  |
                         +--------+--------+
                         |                 |
                      pending          completed/failed
                         |                 |
                      返回进度          返回最终结果
```

## Consensus 返回格式

### 240 秒内完成

```json
{
  "status": "consensus_workflow_complete",
  "consensus_complete": true,
  "next_step_required": false,
  "total_models": 3,
  "successful_responses": 3,
  "failed_responses": 0,
  "model_responses": [
    {
      "model": "model-a",
      "stance": "for",
      "status": "success",
      "verdict": "..."
    }
  ],
  "next_steps": "综合全部模型观点并给出最终建议"
}
```

为兼容现有调用方，响应继续提供 `accumulated_responses` 和 `complete_consensus`。不再要求客户端继续调用 Step 2 或更高步骤。

### 240 秒后转后台

```json
{
  "status": "consensus_in_progress",
  "consensus_complete": false,
  "next_step_required": false,
  "task_id": "UUID",
  "completed_models": 1,
  "total_models": 3,
  "background_wait_seconds": 360,
  "total_timeout_seconds": 600,
  "next_steps": "使用 consensus_status 并传入 task_id 查询进度或最终结果"
}
```

此响应不返回尚未稳定的中间模型正文，避免调用方提前综合部分结果。进度只提供计数和模型状态摘要。

## consensus_status 返回格式

### 仍在执行

```json
{
  "status": "pending",
  "task_id": "UUID",
  "completed_models": 1,
  "total_models": 3,
  "models": [
    {"model": "model-a", "status": "completed"},
    {"model": "model-b", "status": "running"},
    {"model": "model-c", "status": "pending"}
  ]
}
```

### 已完成

```json
{
  "status": "completed",
  "task_id": "UUID",
  "result": {
    "status": "consensus_workflow_complete",
    "consensus_complete": true,
    "model_responses": []
  }
}
```

### 任务失败

```json
{
  "status": "failed",
  "task_id": "UUID",
  "error": "任务级错误信息"
}
```

### 不存在或过期

```json
{
  "status": "not_found",
  "task_id": "UUID",
  "error": "任务不存在、已过期，或 PAL 服务已经重启"
}
```

## 错误处理

- 模型级异常转换为该模型的 `status: error`，其他模型继续执行。
- 任务创建、参数校验或结果组装等任务级异常将任务标记为 `failed`。
- 任务从开始计算超过 600 秒时标记为 `failed`，不会再接受迟到的模型结果覆盖超时状态。
- `asyncio.wait_for()` 只限制首次 MCP 调用的等待时间；通过 `asyncio.shield()` 保证超时后内部任务继续执行。
- 首次调用被取消时，已注册的内部任务继续运行，但如果客户端在 PAL 返回 `task_id` 前主动断开，则客户端可能无法获知该 ID。
- 运行中任务不会因结果保留 TTL 到期而被取消。
- 后台任务完成后结果保留 3 小时，重复查询返回相同完整结果。
- PAL 进程退出时不承诺等待所有后台任务完成。

任务级超时会取消 asyncio 编排协程，但 Python 无法强制终止已经进入 `asyncio.to_thread()` 的同步 Provider 线程。迟到线程可以自然结束，但其结果不会写回已标记超时的 Consensus 任务。

## 上下文隔离

每个任务保存独立的不可变输入：

- `original_proposal`
- `models`
- `relevant_files`
- `images`
- `agent_analysis`
- `findings`

模型咨询函数不读取 ConsensusTool 的共享工作流字段。不同任务即使同时执行，也不会覆盖彼此的 proposal、模型列表或累计响应。

每个模型只接收：

- 同一个原始 proposal。
- 由自身 `ModelContext` 处理后的相关文件。
- 相同图片集合。
- 自己的 stance system prompt。

模型不会接收其他模型的输出。

## 兼容策略

- 保留现有 `ConsensusRequest` 的基础 workflow 字段，减少 MCP schema 变化。
- Step 1 仍要求提供 `models`。
- 工具在 Step 1 内完成所有模型咨询，并强制返回 `next_step_required: false`。
- 旧客户端如果仍调用 Step 2，将收到明确提示：并行 Consensus 已在 Step 1 完成，应使用首次结果或 `consensus_status`。
- 保留现有主要结果字段，同时增加复数形式的 `model_responses` 和任务状态字段。
- `consensus_status` 注册为独立工具，并在 `consensus` 可用时保持可用。

## 测试设计

### 并发行为

- 5 个模拟模型、并发数 3 时，任意时刻最多有 3 个模型执行。
- 验证第二批模型只在第一批出现空闲槽位后启动。
- 验证返回结果顺序与输入模型顺序一致。

### 上下文独立

- 同一个任务中的所有模型收到相同原始 proposal。
- 模型 B 的 prompt 不包含模型 A 的 verdict。
- 两个并发 Consensus 使用不同 proposal 时不发生交叉污染。

### 同步优先

- 所有模型在内部等待期限前完成时直接返回完整结果。
- 完整结果不要求调用 `consensus_status`。

### 后台回退

- 测试中注入极短等待阈值，验证返回 `task_id`，不实际等待 240 秒。
- 初次查询返回 `pending` 和正确进度。
- 后台完成后查询返回完整结果。
- 重复查询返回一致结果。
- 不存在和过期 ID 返回 `not_found`。

### 异常隔离

- 一个模型失败、其他模型成功时，任务整体完成并保留全部模型状态。
- 任务级异常返回 `failed`。
- `consensus_status` 不触发任何 Provider 调用。

### 回归验证

- 运行 Consensus 单元测试。
- 运行工具注册和 schema 测试。
- 运行 conversation memory 相关测试。
- 运行非集成测试全集。

## 文档更新

- 更新 Consensus 工具文档，说明单次并行执行行为。
- 说明默认并发数、同步等待时间及环境变量。
- 增加 `consensus_status` 示例。
- 明确后台任务只在当前 PAL 进程内有效，服务重启后任务 ID 失效。
