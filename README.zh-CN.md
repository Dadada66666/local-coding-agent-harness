# Local Coding Agent Harness

[English](README.md) | [中文](README.zh-CN.md)

Local Coding Agent Harness 是一个本地 Coding Agent Runtime。它让模型通过受控工具操作真实仓库，而不是直接访问文件系统。

这个 runtime 保持轻量，但覆盖了 coding agent 的核心闭环：

- 理解用户任务
- 检查当前工作目录
- 通过受控工具读取和编辑文件
- 运行验证命令
- 在验证失败后进行修复尝试
- 生成 trace、report、diff、cost 等运行产物

## 当前范围

本项目聚焦单个本地 agent loop。不包含 sub-agent、MCP、后台任务、插件发现、worktree 隔离或 LangGraph adapter。

核心目录：

- `agent/`：loop、上下文状态、提示词、模型客户端、消息转换
- `tools/`：工具实现和注册表
- `runtime/`：权限、hooks、sandbox 集成、trace、artifact、上下文压缩、失败恢复、报告、成本统计
- `cli/`：Typer CLI
- `tests/`：单元测试和 runtime 行为测试
- `examples/`：示例仓库 fixture

## 安装

```bash
pip install -e ".[dev]"
```

从 `.env.example` 创建 `.env`：

```bash
ANTHROPIC_API_KEY=
MODEL_ID=
MODEL_CONTEXT_WINDOW_TOKENS=
ANTHROPIC_BASE_URL=
```

默认只加载 Harness 根目录下的 `.env`，不会在当前 `WORKDIR` 中自动搜索。以安装包
方式运行且配置文件位于其他位置时，应通过 `LCAH_ENV_FILE` 指定明确路径。

当 provider 不提供模型窗口大小时，可设置 `MODEL_CONTEXT_WINDOW_TOKENS`，启用基于 token
预算的压缩；未设置时继续使用兼容的字符阈值回退策略。

模型适配层使用 Anthropic Messages API 形状，包括顶层 `system`、`messages`、`tools`、assistant `tool_use` blocks 和 user `tool_result` blocks。`ANTHROPIC_BASE_URL` 可以指向 Anthropic-compatible provider。

## CLI

安装后的命令：

```bash
agent
lcah
```

未安装 console script 时可以使用：

```bash
python -m cli.main
```

交互模式使用当前终端目录作为 `WORKDIR`：

```bash
agent --permission accept_edits
agent --sandbox
```

一次性任务：

```bash
agent run "Fix the failing tests" --permission accept_edits
agent run "Inspect this project and summarize the structure" --permission read_only
```

读取产物：

```bash
agent report <run_id>
agent replay <run_id>
```

权限模式：

- `read_only`：只允许读取和搜索；写入会被 gate。
- `accept_edits`：允许普通文件编辑和安全命令；风险命令仍会被 gate。
- `manual_approval`：编辑和命令执行前都询问用户。

## 工具

工具通过 `ToolRegistry` 注册，并由统一的 `ToolExecutor` 执行。每个工具自己负责参数校验、操作分类和工具语义；权限检查、trace 记录、大输出落盘、验证结果追踪等 runtime 逻辑由 hooks 处理。

当前工具：

- `list_dir`：列出可见文件和目录，跳过 `.agent`、`.git`、`.venv`、`node_modules`、`__pycache__` 等 runtime/cache 目录。
- `grep`：搜索 UTF-8 仓库文本，带匹配数量限制和截断 metadata。
- `read_file`：按行号读取 UTF-8 文本，并记录文件 snapshot。非 UTF-8 文件会返回普通工具失败，而不是未处理的 decode exception。
- `read_artifact`：通过当前 run 内有效的不透明 ID，分页读取大工具结果；不接受文件系统路径。
- `write_file`：写入新的 UTF-8 文件。文件已存在属于工具语义失败，不是权限拒绝；已有文件应使用 `edit_file`。成功写入会更新文件 snapshot。
- `edit_file`：基于已知 snapshot 做 exact text replacement。支持单处 `old_text` / `new_text`，也支持 `edits` 批量替换。重复匹配默认保持 ambiguous；`occurrence` 可指定某一次匹配，`replace_all` 可显式替换全部匹配。批量编辑是原子操作。
- `delete_file`：删除一个已有 snapshot 的普通文件。`accept_edits` 下可自动清理当前任务创建的文件；删除预先存在的文件需要审批。不支持目录和符号链接。
- `bash`：在 `WORKDIR` 下运行验证或检查命令。命令可以带 `purpose="verify"`，使验证结果进入 report success 判断。验证命令采用 fail-fast 语义，不能夹带显式文件修改；shell patch 会被路由到结构化文件工具。
- `view_diff`：在 git 仓库中查看 diff；非 git 目录会返回干净的 "diff unavailable" 结果。

文件工具由 `AgentContext.safe_path()` 约束，读写不能逃逸 `WORKDIR`。

## Runtime 行为

关键 runtime 属性：

- 文件编辑需要来自 `read_file`、`write_file` 或成功 `edit_file` 的已知 snapshot。
- 成功编辑会刷新 snapshot，所以同一文件多次编辑不需要无意义地重新读取。
- no-op edit 会成功返回，但不会标记文件已变更。
- 交互会话会区分 whole-run 状态和 current-task 状态。上一轮 prompt 的失败验证或 changed files 不会污染下一轮 prompt 的 success inference。
- `max_turns` 限制每个任务的模型调用次数；交互运行中的 trace turn ID 仍保持全局唯一。
- runtime 会记录并校验模型 `stop_reason`。截断、拒绝或协议不一致的响应不能被报告为成功；未提供 `stop_reason` 的兼容 provider 仍使用内容块判断。
- 并行工具调用会在一个 user message 中返回全部匹配的 `tool_result`。terminal deny 后未执行的调用会得到显式 cancelled result，保持消息历史合法。
- 成功验证会绑定当前 mutation version。验证后的文件修改会让证据变为 stale，直到重新运行验证。
- Bash 文件删除会返回非终止的工具路由失败，让模型改用 `delete_file`；递归或大范围破坏性命令仍会终止任务。
- 目录列举和递归搜索会在 canonical path 解析后过滤受保护路径，包括最终解析到受保护文件的路径别名。
- 上下文压力计算覆盖 system prompt、tool schemas、messages、预留输出和安全余量；provider usage 可作为本地估算的锚点。容量软上限与默认 32K 经济上下文目标取较小值，字符阈值继续作为兼容兜底。
- 上下文缩减采用分层策略：先按单轮总预算把大 observation 落盘，再把已消费的旧 observation 替换为可恢复引用，最后生成有界 runtime checkpoint，并完整保留最近 API rounds。append-only 审计历史不会被改写。
- provider context overflow 只允许一次有界强制压缩重试；重复溢出或连续压缩失败会明确停止，不会进入死循环。
- 上下文测量和节省量只写入 trace/report，不会追加到模型 messages。
- unknown tool 和参数校验失败会作为正常 tool result 进入 trace，方便排障。
- recovery prompt 不会重复塞入已经存在于前一个 tool result 中的大段失败输出。

## 运行产物

每次运行写入：

```bash
<WORKDIR>/.agent/runs/<run_id>/
```

产物：

- `trace.jsonl`：结构化 runtime events
- `readable_trace.md`：开发者友好的对话/工具链路视图
- `report.md`：状态、变更文件、验证结果、sandbox、成本、artifact
- `diff.patch`：git diff，非 git 目录会写入清晰占位内容
- `cost.json`：模型 usage 和每轮 token breakdown 估算
- `artifacts/`：完整大工具输出，可在当前 run 内通过不透明 ID 恢复

`cost.json` 会把模型输入/输出拆成 system prompt、tool schemas、user messages、assistant tool calls、tool results、compacted history、assistant text、tool calls 等类别。这个 breakdown 是本地优化估算；provider 返回的 usage 才是计费真实来源。
provider 返回时，cache creation/read usage 与上下文管理的估算节省量也会分别记录。

## 验证机制

模型运行行为验证命令时，应设置：

```json
{"purpose": "verify"}
```

runtime 会记录以下验证结果：

- `pytest`、`unittest`、`npm test` 等已知测试命令
- 任意带 `purpose="verify"` 的 `bash` 命令

`find`、`git status`、`git diff`、`ls`、`rg`、`grep` 等只读 discovery 命令即使带了 `verify`，也不会被当作验证结果。
显式修改文件的命令同样不会被记录为验证；应先使用结构化文件工具完成修改，再通过独立的 `bash` 调用验证。

## Sandbox Runtime

Bash 命令可以通过 Anthropic Sandbox Runtime (`srt`) 包裹，作为额外的本地执行边界：

```bash
npm install -g @anthropic-ai/sandbox-runtime
srt --version
agent --sandbox
```

常用选项：

- `--sandbox-fail-if-unavailable`：如果 `srt` 不可运行，启动直接失败。
- `--sandbox-settings <path>`：使用自定义 SRT settings 文件。
- `--sandbox-auto-allow/--no-sandbox-auto-allow`：控制 strong sandbox 可用时 unknown bash 是否自动允许。
- `--bash-env <name>`：显式向 Bash 传入一个非敏感环境变量；可重复指定。Provider
  Key 和名称疑似 secret/token/password 的变量永远不会继承。

Linux/macOS：

- 使用 `srt --settings <settings_path> ...`
- `.env`、`.agent`、`.mcp.json` 和 SSH 数据在 OS 读取边界被拒绝；`git status`
  所需的 Git 元数据仍可由 Git 内部读取，但直接 Bash 引用和所有受保护写入仍会被 gate
- 启动时运行 OS 级 protected-read canary，只有确认读取被拒绝后才视为 strong boundary

Windows：

- 命令以 `srt <real-shell-argv...>` 包裹，不使用 `--settings`
- runtime 将其视为 weak boundary
- 不能因为安装了 `srt` 就自动批准 unknown bash

sandbox 是执行边界，不是 `PermissionGate` 的替代品。破坏性命令、网络命令、受保护路径、通过 shell 写文件等仍由 runtime permission checks 控制。

Bash 使用经过清理的环境，而不是继承 Harness 进程的完整环境。Tool output 会在
回填模型、写 trace 或持久化 artifact 之前统一脱敏。

## 开发

运行 lint：

```bash
python -m ruff check . --no-cache
```

运行测试：

```bash
python -m pytest -q -p no:cacheprovider
```

Windows 下如果 pytest 无法创建或清理默认 temp/cache 目录，可以使用仓库内临时目录：

```powershell
New-Item -ItemType Directory -Force -Path .tmp | Out-Null
$env:GIT_CEILING_DIRECTORIES=(Resolve-Path .tmp).Path
python -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-full
```

测试覆盖工具语义、权限行为、验证追踪、trace/report 生成、recovery、上下文压缩和 interactive 状态隔离。
