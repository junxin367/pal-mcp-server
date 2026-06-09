# PAL MCP：多种工作流，一个上下文

<div align="center">

  <em>你的 AI PAL：Provider Abstraction Layer</em><br />
  <sub><a href="docs/name-change.md">原名 Zen MCP</a></sub>
  <br />
  <sub>简体中文 · <a href="README.md">English</a></sub>

  [PAL 实战演示](https://github.com/user-attachments/assets/0d26061e-5f21-4ab1-b7d0-f883ddc2c3da)

👉 **[查看更多示例](#watch-tools-in-action)**

### 你熟悉的 CLI + 多模型 = 你的 AI 开发团队

**继续使用你喜欢的 🤖 CLI：**<br />
[Claude Code](https://www.anthropic.com/claude-code) · [Gemini CLI](https://github.com/google-gemini/gemini-cli) · [Codex CLI](https://github.com/openai/codex) · [Qwen Code CLI](https://qwenlm.github.io/qwen-code-docs/) · [Cursor](https://cursor.com) · _以及更多_

**在一次提示词中调度多个模型：**<br />
Gemini · OpenAI · Anthropic · Grok · Azure · Ollama · OpenRouter · DIAL · 端侧模型

</div>

---

## 🆕 现在支持 CLI-to-CLI Bridge

新的 **[`clink`](docs/tools/clink.md)**（CLI + Link）工具可以把外部 AI CLI 直接接入你的当前工作流：

- **连接外部 CLI**：例如 [Gemini CLI](https://github.com/google-gemini/gemini-cli)、[Codex CLI](https://github.com/openai/codex) 和 [Claude Code](https://www.anthropic.com/claude-code)。
- **CLI Subagents**：可以从当前 CLI 内启动隔离的 CLI 实例。Claude Code 可以启动 Codex subagent，Codex 可以启动 Gemini CLI subagent。适合把代码审查、缺陷排查等重任务交给全新的上下文处理，主会话上下文不会被污染，subagent 只返回最终结果。
- **上下文隔离**：单独运行调查任务，不污染主工作区。
- **角色专精**：使用 `planner`、`codereviewer` 或自定义角色系统提示词启动专用 agent。
- **完整 CLI 能力**：支持 Web 搜索、文件检查、MCP 工具访问、最新文档查询等。
- **无缝连续性**：子 CLI 作为一等参与者接入，工具之间保留完整会话上下文。

```bash
# Codex 启动 Codex subagent，在全新上下文中做隔离代码审查
clink with codex codereviewer to audit auth module for security issues
# subagent 独立审查并返回最终报告，不会让主上下文充满文件遍历和中间过程

# 多模型共识 → 带完整上下文的实现交接
Use consensus with gpt-5 and gemini-pro to decide: dark mode or offline support next
Continue with clink gemini - implement the recommended feature
# Gemini 会收到完整讨论上下文并立即开始实现
```

👉 **[了解 clink](docs/tools/clink.md)**

---

## 为什么选择 PAL MCP？

**既然可以调度所有模型，为什么只依赖一个 AI 模型？**

PAL MCP 是一个 Model Context Protocol 服务器，可以增强 [Claude Code](https://www.anthropic.com/claude-code)、[Codex CLI](https://developers.openai.com/codex/cli) 以及 [Cursor](https://cursor.com)、[Claude Dev VS Code extension](https://marketplace.visualstudio.com/items?itemName=Anthropic.claude-vscode) 等 IDE 客户端。**PAL MCP 把你喜欢的 AI 工具连接到多个 AI 模型**，用于更强的代码分析、问题求解和协作式开发。

### 具备会话连续性的真正 AI 协作

PAL 支持 **conversation threading**，你的 CLI 可以和多个 AI 模型**讨论想法、交换推理、获得第二意见，甚至让多个模型协作辩论**，帮助你获得更深入的洞察和更可靠的方案。

你的 CLI 始终保持控制权，但可以为不同子任务引入最合适的 AI 视角。上下文会在工具和模型之间顺畅传递，支持代码审查、多模型规划、实现、pre-commit 验证等复杂流程。

> **控制权在你手里。** 你选择的 CLI 负责调度 AI 团队，而工作流由你决定。你可以写出强力提示词，在需要时精确引入 Gemini Pro、GPT 5、Flash 或本地离线模型。

<details>
<summary><b>为什么使用 PAL MCP</b></summary>

以 Claude Code 为例，典型工作流可以获得：

1. **多模型编排**：Claude 可协调 Gemini Pro、O3、GPT-5 和 50+ 其他模型，为每个任务选择更合适的分析能力。

2. **上下文恢复能力**：即使 Claude 的上下文重置，也可以让其他模型“提醒”Claude 之前的讨论内容，继续推进。

3. **引导式工作流**：通过系统化调查阶段，避免仓促分析，确保代码检查足够深入。

4. **扩展上下文窗口**：把大代码库任务委托给 Gemini（1M tokens）或 O3（200K tokens），突破 Claude 的上下文限制。

5. **真正的会话连续性**：上下文在工具和模型之间完整流动，Gemini 可以记住 O3 在 10 步之前说过什么。

6. **发挥模型长处**：Gemini Pro 做深度思考，Flash 提供速度，O3 强化推理，本地 Ollama 提供隐私。

7. **专业代码审查**：多轮分析、严重级别、可执行反馈，并可汇总多个 AI 专家的共识。

8. **智能调试助手**：通过假设跟踪和置信度等级做系统化根因分析。

9. **自动模型选择**：Claude 会智能选择适合当前子任务的模型，也可以由你手动指定。

10. **视觉能力**：支持使用具备视觉能力的模型分析截图、图表和可视内容。

11. **本地模型支持**：本地运行 Llama、Mistral 或其他模型，获得更好的隐私和零 API 成本。

12. **绕过 MCP token 限制**：自动处理 MCP 对大型提示词和响应的 25K 限制。

**核心亮点：** 当 Claude 上下文重置时，只需要让会话 “continue with O3”，另一个模型的响应就能恢复 Claude 对之前讨论的理解，无需重新塞入所有文档。

#### 示例：多模型代码审查工作流

1. `Perform a codereview using gemini pro and o3 and use planner to generate a detailed plan, implement the fixes and do a final precommit check by continuing from the previous codereview`
2. 触发 [`codereview`](docs/tools/codereview.md) 工作流，Claude 系统化浏览代码并寻找问题。
3. 多轮检查后，收集相关代码和中间发现。
4. 使用 `exploring`、`low`、`medium`、`high`、`certain` 等 `confidence` 级别跟踪问题识别的把握程度。
5. 生成从 critical 到 low 的问题清单。
6. 把相关文件和发现交给 **Gemini Pro**，进行第二轮深入 [`codereview`](docs/tools/codereview.md)。
7. 返回后再交给 O3，并在发现新线索时补充提示词。
8. 完成后，Claude 综合所有反馈，输出统一的问题清单、代码中的好模式，以及其他模型指出的修正或新增发现。
9. 如果需要较大重构，再使用 [`planner`](docs/tools/planner.md) 工作流拆解实施步骤。
10. Claude 执行实际修复。
11. 完成后，再回到 Gemini Pro 做 [`precommit`](docs/tools/precommit.md) 检查。

整个过程都在同一条会话线程内完成。第 11 步的 Gemini Pro 仍然知道第 7 步 O3 的建议，并能结合此前审查结果做最终验证。

**可以把它理解为 Claude Code 的协作增强层。** PAL MCP 不是魔法，它只是把这些能力粘合起来。

> **记住：** Claude 仍然掌握执行控制权，但由 **你** 决定何时调用谁。
> PAL 的设计目标是让 Claude 只在需要时引入其他模型，并完成有意义的来回协作。
> **你** 编写提示词，让 Claude 按你的意图引入 Gemini、Flash、O3，或独立完成。
> 你是引导者、提示词编排者和流程控制者。
> #### You are the AI - **Actually Intelligent**.
</details>

#### 推荐 AI 组合

<details>
<summary>Claude Code 用户</summary>

使用 [Claude Code](https://claude.ai/code) 时，推荐：

- **Sonnet 4.5**：负责主要 agentic 工作和编排。
- **Gemini 3.0 Pro** 或 **GPT-5.2 / Pro**：负责深度思考、额外代码审查、调试、验证和 pre-commit 分析。
</details>

<details>
<summary>Codex 用户</summary>

使用 [Codex CLI](https://developers.openai.com/codex/cli) 时，推荐：

- **GPT-5.2 Codex Medium**：负责主要 agentic 工作和编排。
- **Gemini 3.0 Pro** 或 **GPT-5.2-Pro**：负责深度思考、额外代码审查、调试、验证和 pre-commit 分析。
</details>

## 快速开始（5 分钟）

**前置条件：** Python 3.10+、Git、已安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

**1. 获取 API Key**（选择一个或多个）：

- **[OpenRouter](https://openrouter.ai/)**：通过一个 API 访问多个模型。
- **[Gemini](https://makersuite.google.com/app/apikey)**：Google 最新模型。
- **[OpenAI](https://platform.openai.com/api-keys)**：O3、GPT-5 系列。
- **[Azure OpenAI](https://learn.microsoft.com/azure/ai-services/openai/)**：GPT-4o、GPT-4.1、GPT-5 家族的企业部署。
- **[X.AI](https://console.x.ai/)**：Grok 模型。
- **[DIAL](https://dialx.ai/)**：厂商无关的模型访问方式。
- **[Ollama](https://ollama.ai/)**：本地模型，免费运行。

**2. 安装**（二选一）：

**选项 A：克隆并自动设置**（推荐）

```bash
git clone https://github.com/BeehiveInnovations/pal-mcp-server.git
cd pal-mcp-server

# 处理 setup、配置、系统环境中的 API keys。
# 自动配置 Claude Desktop、Claude Code、Gemini CLI、Codex CLI、Qwen CLI。
# 其他开关可在 .env 中启用或禁用。
./run-server.sh
```

**选项 B：使用 [uvx](https://docs.astral.sh/uv/getting-started/installation/) 快速接入**

```json
// 添加到 ~/.claude/settings.json 或 .mcp.json
// 记得在 env 中配置你的 API keys
{
  "mcpServers": {
    "pal": {
      "command": "bash",
      "args": ["-c", "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \"$p\" ] && exec \"$p\" --from git+https://github.com/BeehiveInnovations/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin",
        "GEMINI_API_KEY": "your-key-here",
        "DISABLED_TOOLS": "analyze,refactor,testgen,secaudit,docgen,tracer",
        "DEFAULT_MODEL": "auto"
      }
    }
  }
}
```

**3. 开始使用**

```text
"Use pal to analyze this code for security issues with gemini pro"
"Debug this error with o3 and then get flash to suggest optimizations"
"Plan the migration strategy with pal, get consensus from multiple models"
"clink with cli_name=\"gemini\" role=\"planner\" to draft a phased rollout plan"
```

👉 **[完整安装指南](docs/getting-started.md)**：包含安装、Gemini / Codex / Qwen 配置和故障排查。
👉 **[Cursor 与 VS Code 设置](docs/getting-started.md#ide-clients)**：IDE 集成说明。
📺 **[查看工具演示](#watch-tools-in-action)**：真实使用示例。

## Provider 配置

PAL 会启用所有在 `.env` 中具备凭据的 provider。更多自定义配置请参考 `.env.example`。

## 核心工具

> **说明：** 每个工具都有自己的多步骤工作流、参数和说明，这些内容即使不使用也会占用上下文窗口。为了优化性能，部分工具默认禁用。需要启用时请查看下方 [工具配置](#tool-configuration)。

**协作与规划**（默认启用）

- **[`clink`](docs/tools/clink.md)**：把请求桥接到外部 AI CLI，例如 Gemini planner、codereviewer 等。
- **[`chat`](docs/tools/chat.md)**：头脑风暴、获取第二意见、验证方案。搭配 GPT-5.2 Pro、Gemini 3.0 Pro 等强模型时，也可生成完整代码或实现。
- **[`thinkdeep`](docs/tools/thinkdeep.md)**：扩展推理、边界情况分析和替代视角。
- **[`planner`](docs/tools/planner.md)**：把复杂项目拆成结构化、可执行的计划。
- **[`consensus`](docs/tools/consensus.md)**：通过 stance steering 获取多个 AI 模型的专家意见。

**代码分析与质量**

- **[`debug`](docs/tools/debug.md)**：系统化调查和根因分析。
- **[`precommit`](docs/tools/precommit.md)**：提交前验证变更，降低回归风险。
- **[`codereview`](docs/tools/codereview.md)**：带严重级别和可执行反馈的专业审查。
- **[`analyze`](docs/tools/analyze.md)**（默认禁用，可在 [工具配置](#tool-configuration) 中启用）：理解整个代码库的架构、模式和依赖。

**开发工具**（默认禁用，可在 [工具配置](#tool-configuration) 中启用）

- **[`refactor`](docs/tools/refactor.md)**：以拆解复杂度为重点的智能重构。
- **[`testgen`](docs/tools/testgen.md)**：包含边界情况的完整测试生成。
- **[`secaudit`](docs/tools/secaudit.md)**：基于 OWASP Top 10 等视角的安全审计。
- **[`docgen`](docs/tools/docgen.md)**：结合复杂度分析生成文档。

**实用工具**

- **[`apilookup`](docs/tools/apilookup.md)**：在子进程中强制查询当前年份的 API/SDK 文档，节省当前上下文窗口 token，并避免过时训练数据带来的错误。
- **[`challenge`](docs/tools/challenge.md)**：通过批判性分析避免 “You're absolutely right!” 式的机械附和。
- **[`tracer`](docs/tools/tracer.md)**（默认禁用，可在 [工具配置](#tool-configuration) 中启用）：用于调用流映射的静态分析提示。

<details>
<summary><b id="tool-configuration">👉 工具配置</b></summary>

### 默认配置

为了优化上下文窗口使用，默认只启用核心工具：

**默认启用：**

- `chat`、`thinkdeep`、`planner`、`consensus`：核心协作工具。
- `codereview`、`precommit`、`debug`：基础代码质量工具。
- `apilookup`：快速 API/SDK 信息查询。
- `challenge`：批判性思考工具。

**默认禁用：**

- `analyze`、`refactor`、`testgen`、`secaudit`、`docgen`、`tracer`。

### 启用更多工具

从 `DISABLED_TOOLS` 中移除对应工具即可启用：

**选项 1：编辑 `.env` 文件**

```bash
# 默认配置（来自 .env.example）
DISABLED_TOOLS=analyze,refactor,testgen,secaudit,docgen,tracer

# 启用某个工具时，从列表中移除它
# 示例：启用 analyze
DISABLED_TOOLS=refactor,testgen,secaudit,docgen,tracer

# 启用全部工具
DISABLED_TOOLS=
```

**选项 2：在 MCP settings 中配置**

```json
// ~/.claude/settings.json 或 .mcp.json
{
  "mcpServers": {
    "pal": {
      "env": {
        // 工具配置
        "DISABLED_TOOLS": "refactor,testgen,secaudit,docgen,tracer",
        "DEFAULT_MODEL": "pro",
        "DEFAULT_THINKING_MODE_THINKDEEP": "high",

        // API 配置
        "GEMINI_API_KEY": "your-gemini-key",
        "OPENAI_API_KEY": "your-openai-key",
        "OPENROUTER_API_KEY": "your-openrouter-key",

        // 日志与性能
        "LOG_LEVEL": "INFO",
        "CONVERSATION_TIMEOUT_HOURS": "6",
        "MAX_CONVERSATION_TURNS": "50"
      }
    }
  }
}
```

**选项 3：启用全部工具**

```json
// 移除 DISABLED_TOOLS，或将其置空
{
  "mcpServers": {
    "pal": {
      "env": {
        "DISABLED_TOOLS": ""
      }
    }
  }
}
```

**注意：**

- 基础工具 `version`、`listmodels` 不能禁用。
- 修改工具配置后，需要重启 Claude 会话才能生效。
- 每个工具都会增加上下文窗口占用，建议只启用实际需要的工具。

</details>

<a id="watch-tools-in-action"></a>

## 📺 工具演示

<details>
<summary><b>Chat Tool</b>：协作式决策与多轮对话</summary>

**选择 Redis 还是 Memcached：**

[Chat Redis or Memcached_web.webm](https://github.com/user-attachments/assets/41076cfe-dd49-4dfc-82f5-d7461b34705d)

**带 continuation 的多轮对话：**

[Chat With Gemini_web.webm](https://github.com/user-attachments/assets/37bd57ca-e8a6-42f7-b5fb-11de271e95db)

</details>

<details>
<summary><b>Consensus Tool</b>：多模型辩论与决策</summary>

**多模型共识辩论：**

[PAL Consensus Debate](https://github.com/user-attachments/assets/76a23dd5-887a-4382-9cf0-642f5cf6219e)

</details>

<details>
<summary><b>PreCommit Tool</b>：全面的变更验证</summary>

**提交前验证工作流：**

<div align="center">
  <img src="https://github.com/user-attachments/assets/584adfa6-d252-49b4-b5b0-0cd6e97fb2c6" width="950">
</div>

</details>

<details>
<summary><b>API Lookup Tool</b>：当前文档与过时 API 对比</summary>

**不使用 PAL：容易得到过时 API**

[API without PAL](https://github.com/user-attachments/assets/01a79dc9-ad16-4264-9ce1-76a56c3580ee)

**使用 PAL：查询当前 API**

[API with PAL](https://github.com/user-attachments/assets/5c847326-4b66-41f7-8f30-f380453dce22)

</details>

<details>
<summary><b>Challenge Tool</b>：批判性思考，而不是机械附和</summary>

**不使用 PAL：**

![without_pal@2x](https://github.com/user-attachments/assets/64f3c9fb-7ca9-4876-b687-25e847edfd87)

**使用 PAL：**

![with_pal@2x](https://github.com/user-attachments/assets/9d72f444-ba53-4ab1-83e5-250062c6ee70)

</details>

## 关键特性

**AI 编排**

- **自动模型选择**：Claude 为每个任务选择合适的 AI。
- **多模型工作流**：在单个会话中串联不同模型。
- **会话连续性**：上下文在工具和模型之间保留。
- **[上下文恢复](docs/context-revival.md)**：即使上下文重置，也可以继续此前讨论。

**模型支持**

- **多个 provider**：Gemini、OpenAI、Azure、X.AI、OpenRouter、DIAL、Ollama。
- **最新模型**：GPT-5、Gemini 3.0 Pro、O3、Grok-4、本地 Llama。
- **[Thinking modes](docs/advanced-usage.md#thinking-modes)**：控制推理深度和成本。
- **视觉支持**：分析图片、图表和截图。

**开发体验**

- **引导式工作流**：系统化调查，避免仓促分析。
- **智能文件处理**：自动展开目录并管理 token 限制。
- **Web 搜索集成**：获取当前文档和最佳实践。
- **[大提示词支持](docs/advanced-usage.md#working-with-large-prompts)**：绕过 MCP 的 25K token 限制。

## 示例工作流

**多模型代码审查：**

```text
"Perform a codereview using gemini pro and o3, then use planner to create a fix strategy"
```

→ Claude 系统化审查代码 → 咨询 Gemini Pro → 获取 O3 视角 → 生成统一行动计划。

**协作式调试：**

```text
"Debug this race condition with max thinking mode, then validate the fix with precommit"
```

→ 深入调查 → 专家分析 → 实现解决方案 → pre-commit 验证。

**架构规划：**

```text
"Plan our microservices migration, get consensus from pro and o3 on the approach"
```

→ 结构化规划 → 多个专家意见 → 共识构建 → 实施路线图。

👉 **[高级使用指南](docs/advanced-usage.md)**：复杂工作流、模型配置和高级能力。

## 快速链接

**📖 文档**

- [文档总览](docs/index.md)：主要指南地图。
- [Getting Started](docs/getting-started.md)：完整设置指南。
- [Tools Reference](docs/tools/)：全部工具与示例。
- [Advanced Usage](docs/advanced-usage.md)：高级用户能力。
- [Configuration](docs/configuration.md)：环境变量与限制。
- [Adding Providers](docs/adding_providers.md)：provider 设置，例如 OpenAI、Azure、自定义网关。
- [Model Ranking Guide](docs/model_ranking.md)：auto-mode 推荐如何使用模型智能分。

**🔧 设置与支持**

- [WSL Setup](docs/wsl-setup.md)：Windows 用户设置。
- [Troubleshooting](docs/troubleshooting.md)：常见问题。
- [Contributing](docs/contributions.md)：代码规范和 PR 流程。

## License

Apache 2.0 License，详见 [LICENSE](LICENSE)。

## 致谢

由 **Multi-Model AI** 协作能力构建 🤝

- 来自真实人类的 **A**ctual **I**ntelligence
- [MCP (Model Context Protocol)](https://modelcontextprotocol.com)
- [Codex CLI](https://developers.openai.com/codex/cli)
- [Claude Code](https://claude.ai/code)
- [Gemini](https://ai.google.dev/)
- [OpenAI](https://openai.com/)
- [Azure OpenAI](https://learn.microsoft.com/azure/ai-services/openai/)

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=BeehiveInnovations/pal-mcp-server&type=Date)](https://www.star-history.com/#BeehiveInnovations/pal-mcp-server&Date)
