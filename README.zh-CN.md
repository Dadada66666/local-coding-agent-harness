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

- `src/agent/`：agent loop、提示词、模型客户端和消息转换
- `src/runtime/`：会话状态、工具执行、失败恢复和运行时装配
- `src/runtime/plan/`：计划策略、生命周期控制器、门禁和审计快照
- `src/runtime/context/`：上下文预算、检查点、压缩和工具结果投影
- `src/runtime/security/`：访问策略、权限 Gate、风险分析和 Sandbox
- `src/runtime/hooks/`：生命周期、策略和状态追踪 Hooks
- `src/runtime/observability/`：Trace、Report、Artifact、Diff 和成本统计
- `src/tools/`：显式工具实现和注册表
- `src/cli/`：Typer 命令、交互模式和 Trace Replay
- `tests/unit/` 与 `tests/integration/`：按源码领域组织的测试

依赖方向和主要执行链路见 [`docs/architecture.md`](docs/architecture.md)。

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
python -m cli.app
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

## Plan Mode

Plan Mode 是可选的 Runtime 能力，包含三种策略：

- `off`：保持原有 agent loop。不会注入计划提示词，不暴露计划工具，不执行计划门禁，也不写入
  `plan.json`。为保证向后兼容，这是默认值。
- `auto`：模型可以先使用只读工具检查仓库，但在执行 Bash 或修改仓库前，必须通过
  `select_execution_mode` 结构化选择 `direct` 或 `plan`。选择 direct 后沿用原流程；选择 plan
  后保持只读，直到提交结构化计划。
- `required`：任务直接进入只读规划。

执行路径策略与审批策略彼此独立。审批默认是 `manual`，因此 `auto` 和 `required` 下提交的计划都会
暂停在 `awaiting_approval`。只有显式设置 `--plan-approval auto`，Runtime 才会自动授权已提交版本。

启动参数：

```bash
agent --plan-mode auto
agent --plan-mode required
agent --plan-mode off
agent --plan       # 等价于 --plan-mode required
agent --no-plan    # 等价于 --plan-mode off
agent --plan-mode auto --plan-approval manual
agent --plan-mode auto --plan-approval auto
```

冲突参数会明确报错，不会静默覆盖。交互模式还支持：

```text
/plan-mode auto|required|off
/plan-approval manual|auto
/plan
/approve
/revise <feedback>
/cancel-plan
/plan-status
```

`/approve` 和 `/revise` 会恢复同一个任务，不会调用 `begin_task()`；模型调用次数、mutation、
verification、recovery 和上下文预算都保持连续。审批提示中的 `1`、`2`、`3` 分别表示批准执行、修改
计划和拒绝执行；选择 `2` 后会继续询问修改意见。精确回复 `同意`、`同意执行`、`批准`、`批准执行`、
`approve` 或 `approved` 时，Runtime 通过确定性 fast path 直接批准，不消耗一次模型调用来解释授权。
匹配只裁剪首尾空白并将 ASCII 转为小写，不做 substring 匹配，因此“我不同意”和附带条件的回复绝不会
被直接批准。其他普通文本仍属于原任务续接，由动态可见的 `resolve_plan_response` 结构化解释。

Auto 模式不使用关键词或 prompt 长度规则判断任务复杂度，而是由模型结合任务与实际仓库，通过可追踪的
工具调用选择 direct 或 plan。

Plan Gate 与 Permission Gate 职责不同。未选择模式、规划中、以及 required 等待批准时，Plan Gate
会在权限判断前阻止 Bash 和仓库副作用；direct 或已授权执行阶段则把调用交回现有 Permission Gate。
计划状态本身不会自动批准任何文件或命令权限。

工具可见性按计划状态动态收窄。规划阶段只暴露检查工具和当前阶段合法的计划 action；存在新用户输入的
待批准阶段只暴露 `resolve_plan_response`。`ToolRegistry.resolve()` 同时决定 schema 可见性和 Executor
可调用性，Plan Gate 继续作为纵深防御，不把可见性当安全边界。

进入该审批解析回合前，已消费源码会投影为有界 source stub。审批响应只要包含任何非 resolver 工具，
Runtime 就会拒绝整个 batch，并提供一次仅限 resolver 的自动纠错；再次失败则暂停，不进入循环。

计划工具使用同一份 capability 投影：

- `auto + undecided`：显示 `select_execution_mode`
- `plan + planning/executing`：显示 `update_plan`
- `awaiting_approval + 新用户回复`：显示 `resolve_plan_response`
- `off`、direct、completed 和 cancelled：不显示计划工具

活跃计划会原子写入 `<WORKDIR>/.agent/runs/<run_id>/plan.json`，记录决策与审批策略、task ID/status、
模型选择理由、计划版本、授权来源、阶段和步骤进度。快照不保存环境数据，并在落盘前脱敏。`plan.json` 是计划决策与
执行状态的审计快照，不等同于完整会话恢复。

计划子系统明确不实现 SQLite、多 Worker、分布式调度、租约、心跳、后台执行、任意崩溃点恢复或 Web UI。

## 工具

工具通过 `ToolRegistry` 注册，并由统一的 `ToolExecutor` 执行。每个工具自己负责参数校验、操作分类和工具语义；权限检查、trace 记录、大输出落盘、验证结果追踪等 runtime 逻辑由 hooks 处理。

当前工具：

- `list_dir`：列出可见文件和目录，跳过 `.agent`、`.git`、`.venv`、`node_modules`、`__pycache__` 等 runtime/cache 目录。
- `grep`：搜索 UTF-8 仓库文本，带匹配数量限制和截断 metadata。
- `read_file`：按行号读取 UTF-8 文本，并记录文件 snapshot。每页明确返回总行数、实际行范围、
  `next_offset` 和 `has_more`；task-local、绑定 SHA 的区间覆盖会识别重叠和完整扫描。未变化且已完整扫描
  的源码再次被宽范围读取时只返回小型提示，`force=true` 可显式刷新。非 UTF-8 文件会返回普通工具失败，
  而不是未处理的 decode exception。
- `read_artifact`：通过当前 run 内有效的不透明 ID，分页读取大工具结果；不接受文件系统路径。
- `write_file`：写入完整的 UTF-8 文件。新文件使用排他创建；已有文件必须具备完整且最新的 snapshot，并通过原子替换写入。成功写入会更新文件 snapshot。
- `edit_file`：基于已知 snapshot 做 exact text replacement。支持单处 `old_text` / `new_text`，也支持 `edits` 批量替换。重复匹配默认保持 ambiguous；`occurrence` 可指定某一次匹配，`replace_all` 可显式替换全部匹配。批量编辑是原子操作。
- `delete_file`：删除一个已有 snapshot 的普通文件。`accept_edits` 下可自动清理当前任务创建的文件；删除预先存在的文件需要审批。不支持目录和符号链接。
- `bash`：在 `WORKDIR` 下运行验证或检查命令。命令可以带 `purpose="verify"`，使验证结果进入 report success 判断。验证命令采用 fail-fast 语义，不能夹带显式文件修改；预期整体返回非零状态时可设置 `exit_expectation="nonzero"`。shell patch 会被路由到结构化文件工具。
- `view_diff`：在 git 仓库中查看 diff；非 git 目录会返回干净的 "diff unavailable" 结果。
- `select_execution_mode`：仅在 auto 未决策阶段动态可见，记录模型选择 direct 或 plan 的具体理由。
- `update_plan`：仅在计划生命周期相关阶段动态可见；使用按 action 区分的 schema，可在一次调用中替换并提交计划，
  但不能批准 manual 计划。
- `resolve_plan_response`：仅在等待审批且存在新用户续接时可见，结构化记录批准、修改或取消。

文件工具由 `AgentContext.safe_path()` 约束，读写不能逃逸 `WORKDIR`。

## Runtime 行为

关键 runtime 属性：

- 文件编辑需要来自 `read_file`、`write_file` 或成功 `edit_file` 的已知 snapshot。
- 成功编辑会刷新 snapshot，所以同一文件多次编辑不需要无意义地重新读取。
- no-op edit 会成功返回，但不会标记文件已变更。
- 交互会话会区分 whole-run 状态和 current-task 状态。上一轮 prompt 的失败验证或 changed files 不会污染下一轮 prompt 的 success inference。
- Task 生命周期显式区分 `idle`、`running`、`waiting_user`、`completed`、`failed` 和 `cancelled`。
  `finished` 只表示当前 loop invocation 已停止；等待用户的任务不会被归档、重置，也不会触发 task-boundary 压缩。
- `max_turns` 限制每个任务的模型调用次数（默认 40 次）；交互运行中的 trace turn ID 仍保持全局唯一。
- runtime 会记录并校验模型 `stop_reason`。截断、拒绝或协议不一致的响应不能被报告为成功；未提供 `stop_reason` 的兼容 provider 仍使用内容块判断。
- 并行工具调用会在一个 user message 中返回全部匹配的 `tool_result`。terminal deny 后未执行的调用会得到显式 cancelled result，保持消息历史合法。
- 成功验证会绑定当前 mutation version。验证后的文件修改会让证据变为 stale，直到重新运行验证。
- Bash 文件删除会返回非终止的工具路由失败，让模型改用 `delete_file`；递归或大范围破坏性命令仍会终止任务。
- Shell 风险分析具备引号感知能力，并会记录复合副作用。网络命令如果同时创建目录或写文件，审批会同时展示目标主机和文件路径，不会用单一 `network` 标签隐藏写入行为。
- 目录列举和递归搜索会在 canonical path 解析后过滤受保护路径，包括最终解析到受保护文件的路径别名。
- 上下文压力计算覆盖 system prompt、tool schemas、messages、预留输出和安全余量；provider usage 可作为本地估算的锚点。容量软上限与默认 32K 经济上下文目标取较小值，字符阈值继续作为兼容兜底。
- 上下文缩减采用分层策略。提前投影水位默认按经济上下文目标派生并带滞回；预算允许时，分页源码在
  完成连贯扫描且被模型消费前暂时保留，硬上下文安全始终优先。已消费源码页转换为保持行坐标的 source
  stub，不生成通用 artifact；Bash、grep 和日志仍保留可恢复 artifact。完整压缩会写入有界 source
  manifest，append-only 审计历史不会被改写。
- 交互任务切换时，如果已完成历史超过 token 阈值，runtime 会在下一任务开始前生成确定性 checkpoint；当前 prompt 和 append-only 审计链保持完整。
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
- `report.md`：task/session 成本、变更文件、验证等级、已恢复失败、sandbox 和 artifact
- `diff.patch`：git diff，非 git 目录会写入清晰占位内容
- `cost.json`：模型 usage 和每轮 token breakdown 估算
- `plan.json`：按需生成的计划决策与执行状态审计快照
- `artifacts/`：完整大工具输出，可在当前 run 内通过不透明 ID 恢复

`cost.json` 会把模型输入/输出拆成 system prompt、tool schemas、user messages、assistant tool calls、tool results、compacted history、assistant text、tool calls 等类别。这个 breakdown 是本地优化估算；provider 返回的 usage 才是计费真实来源。
provider 返回时，cache creation/read usage 与上下文管理的估算节省量也会分别记录。
顶层 totals 保持 session 累计口径；`current_task` 和已完成任务记录提供任务级 usage，不会重置整次运行的审计数据。

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
