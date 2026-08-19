你现在需要对一个 Coding Agent Harness 的 **Context Management / Context Compression / Tool Result Management / Prompt Cache Friendliness** 进行一次架构级优化。

这不是一次“看到问题就直接改代码”的任务。

你的工作必须严格分为：

```
理解现状
→ 建立问题模型
→ 明确设计原则
→ 输出修改 Spec
→ 审核 Spec 是否闭合
→ 冻结 Spec
→ 按 Spec 实现
→ 验证
```

**在 Spec 没有明确、闭合、可执行之前，禁止开始修改代码。**

------

## 一、任务背景

当前 Harness 在上下文管理上可能存在以下倾向：

1. 在每次模型请求之前主动精简历史 Context；
2. 对已经消费过的 Tool Result 进行 eager projection / compact；
3. 将较大的 Tool Result 持久化到 artifact，然后将历史中的原始结果替换成较短 representation；
4. 使用固定或偏小的 context target，较早触发 context pressure；
5. 希望通过持续降低每轮 Context 大小降低 token 成本。

现在需要重新审视这套设计。

核心怀疑是：

> 当前系统可能优化了“单次请求的 Context Size”，却没有优化“完成整个任务的总成本”。

长任务中可能因此出现：

```
读取文件
→ Tool Result 很快被投影/压缩
→ 模型几轮后需要原内容
→ 再次 read_file / grep
→ 再次产生 Tool Result
→ 再次被压缩
→ 重复读取
```

形成类似：

```
working-set thrashing
```

的问题。

除此之外，还必须把 **Prompt Prefix Cache** 纳入 Context Manager 的设计。

大部分现代模型 API 的 Prompt Cache 都高度依赖：

```
稳定的 Prompt Prefix
```

因此：

> 修改一个已经存在于历史较早位置的 Tool Result，不只是“删除了 N 个 token”，还可能导致这个位置之后的大段 Prefix Cache 失效。

所以不能只按照：

```
Tool Result 是否容易重新获取
```

决定是否清理。

还必须考虑：

```
修改历史是否值得破坏现有稳定 Prefix。
```

------

# 二、本次优化的核心设计假设

你必须首先验证以下设计假设是否和当前代码实际情况一致。

不要直接相信这些假设。

请通过代码、测试、调用路径和数据结构逐项确认。

------

## 假设 A：Context 不应该每轮持续重写

正常情况下：

```
Request N:
Prefix + A + B + C


Request N+1:
Prefix + A + B + C + D


Request N+2:
Prefix + A + B + C + D + E
```

应优先保持：

```
append-only
```

而不是：

```
Request N:
A B C D


Request N+1:
A B' C D E


Request N+2:
A B'' C' D E F
```

因为后者可能不断破坏 Prompt Prefix Cache。

------

## 假设 B：Admission 和 Eviction 必须分开

这是本次修改最重要的概念之一。

### Admission

一个新的 Tool Result **第一次进入模型 Context 时**，决定：

```
它以什么形式进入 Context？
```

例如：

```
120K bash output
```

不应该先完整进入 Context，再在下一轮修改历史。

而应该第一次就：

```
full raw output
    → artifact


model-visible result
    →
    command
    exit code
    salient error
    bounded head/tail
    artifact reference
```

这种处理属于：

```
Admission Shaping
```

它不会修改已经形成的历史 Prefix。

------

### Eviction / Historical Rewrite

指：

```
一个已经存在于历史 Context 中的结果
```

后来被：

```
替换
删除
压缩
投影
```

这是高成本操作，因为可能：

```
1. 破坏 Prompt Prefix Cache
2. 丢失 working-set locality
3. 引发 Tool re-read / re-grep
```

因此：

> Historical Rewrite 应该非常保守。

------

# 三、目标架构：Context Epoch

优先评估是否应该将 Context Manager 简化为：

```
Context Epoch
```

模型。

一个 Epoch 内：

```
Stable System / Tool Prefix
+
Epoch Checkpoint
+
Trajectory
+
Trajectory
+
Trajectory
+
...
```

原则：

> Epoch 内尽可能 append-only。

只有当 Context 真正达到较高压力时，才允许：

```
REBASE / COMPACTION
```

例如：

```
Epoch 1


[checkpoint]
[old trajectory]
[recent trajectory]


        ↓ pressure threshold


COMPACTION


        ↓


Epoch 2


[new checkpoint]
[recent raw trajectory]


        ↓


继续 append-only
```

也就是说成本模式应该尽量接近：

```
cache hit
cache hit
cache hit
cache hit
cache hit


       ↓


one intentional rebase


       ↓


cache hit
cache hit
cache hit
cache hit
```

而不是：

```
rewrite
cache miss


rewrite
cache miss


rewrite
cache miss
```

------

# 四、不要过早实现复杂的 Memory Replacement Algorithm

本次修改**明确禁止**一开始引入类似：

```
reuse_probability
information_value
rehydration_cost
LRU score
importance classifier
semantic eviction score
complex per-message state machine
```

除非你通过现有代码和 benchmark 证明：

> 不引入这些机制就无法完成本次 Spec。

否则不要实现。

本次优先采用：

```
simple
deterministic
measurable
cache-friendly
```

的 Context Policy。

不要为了“未来可能需要”增加抽象。

------

# 五、Tool 类型应该如何参与决策

不要使用这种过度简化规则：

```
grep -> 删除
list_dir -> 删除
read_file -> 保留
```

Tool 类型主要应该影响：

# Initial Admission Representation

而不是：

# Historical Eviction Timing

例如可以评估：

### `list_dir`

如果结果本身不大：

```
直接 append
```

以后不要因为它容易重新执行，就专门回头删除历史中的结果。

------

### `grep`

应该优先在 Tool 执行层限制：

```
match count
output size
search scope
```

如果原始结果巨大：

```
raw result → artifact/cache
visible result → bounded matches
```

但进入历史后不要频繁修改。

------

### `read_file`

优先通过：

```
offset
limit
range
```

控制 admission 大小。

正常大小的 read result 可以原样进入 Context。

不要默认：

```
读完几轮后自动投影成 stub
```

除非发生真正的全局 Context Pressure。

------

### `bash` / test output

非常适合 admission shaping：

```
完整 stdout/stderr → artifact


Context:
command
exit code
important failures
salient stack/error
bounded head/tail
artifact reference
```

尤其禁止：

```
先将 100K output 放入历史
→ 下一轮再重写为 stub
```

------

# 六、重要修正：不要为了 Tool 类型牺牲 Prefix Cache

假设：

```
Step 2:
list_dir = 1K tokens


之后已经产生：
40K tokens trajectory
```

此时不要因为：

```
list_dir 很便宜重新执行
```

就删除这 1K。

因为收益可能是：

```
释放 1K Context
```

但潜在损失可能是：

```
破坏后续 40K 的 Prefix Cache
```

因此 Historical Rewrite 的判断优先级应该类似：

```
1. 当前是否真的存在 Context Pressure？
2. 这次修改会破坏多长的稳定 Prefix？
3. 一次实际能够释放多少 Context？
4. 释放空间是否足以值得一次 Rebase？
5. 内容是否可以恢复？
6. Tool 类型 / 重算成本
```

而不是 Tool 类型优先。

------

# 七、Compaction 策略要求

请重点评估以下方向。

不要机械照抄数字，应结合当前模型配置与代码能力确定最终值。

建议基线：

```
HIGH_WATERMARK
≈ usable context 的 75%~85%
```

例如可以从：

```
80%
```

开始 benchmark。

当：

```
context_usage < HIGH_WATERMARK
```

时：

> 禁止对已经进入历史的普通 Tool Result 做 eager historical rewrite。

除非存在明确 correctness / provider hard-limit 问题。

------

达到 HIGH_WATERMARK 后：

不要进行小幅连续压缩。

应该评估：

```
一次较大的 compaction / rebase
```

例如将 Context 从：

```
~80%
```

压回：

```
~55%~65%
```

原因：

> 既然这次 Rebase 已经可能破坏 Prefix Cache，就应该一次释放有意义的空间，以降低下一次 Rebase 的频率。

------

# 八、必须存在 Minimum Reclaim Gain

禁止为了：

```
释放几百 / 一两千 token
```

执行一次历史 Compaction。

请设计：

```
MIN_RECLAIM_TOKENS
```

或者等价机制。

例如可以评估：

```
max(
    fixed_floor,
    usable_context * ratio
)
```

作为 minimum worthwhile gain。

具体数值必须通过：

```
当前模型 Context Window
+
当前 Harness 行为
+
benchmark
```

确定。

不要硬编码一个未经论证的数字。

------

# 九、Semantic Checkpoint 的职责

Semantic Checkpoint 不应该每轮重新生成。

如果每轮修改 Prompt 前部的：

```
Current State
Plan
Memory
Checkpoint
```

同样会严重降低 Prefix Stability。

因此优先采用：

```
Checkpoint is immutable within one Context Epoch.
```

即：

```
Epoch 1 checkpoint
```

生成后不变。

直到下一次：

```
global compaction / rebase
```

才生成：

```
Epoch 2 checkpoint
```

------

新的 checkpoint 应该总结：

```
Previous Checkpoint
+
本次被 compact 掉的 old trajectory
```

并保留：

```
用户目标
明确约束
重要设计决定
决定的 rationale
用户纠正
重要失败
错误信息
当前实现状态
changed files
pending work
current plan
```

同时保留一段足够大的：

```
recent raw trajectory
```

不要把整个任务全部压成一个极小的 summary。

------

# 十、Artifact 的正确职责

Artifact 应该定义为：

```
authoritative cold storage
```

而不是：

```
“内容已经从模型中删除”的同义词
```

一个 Tool Result 可以：

```
立即持久化到 artifact
```

同时：

```
仍然以合理 representation 留在 Context
```

二者不是互斥的。

Artifact 的价值是：

```
recoverability
auditability
large-output storage
rehydration
```

不是强制 eviction。

------

# 十一、Rehydration 必须是 Append，而不是修改过去

假如 Context 中存在：

```
artifact://abc
```

模型后来需要完整内容：

```
read_artifact(abc)
```

新的结果应该：

```
append 到当前 Context 尾部
```

而不是：

```
找到历史中的 artifact stub
然后原地替换成完整内容
```

原则：

> Rehydration should append new evidence, not mutate old history.

这样可以最大程度维持 Prefix Stability。

------

# 十二、本次第一阶段修改的优先级

请重点验证以下三个修改是否应该成为本次正式 Spec：

### 1. 移除或关闭非 Pressure 状态下的 Historical Eager Projection

重点检查当前：

```
prepare_context()
```

及相关 projection 流程。

明确区分：

```
Admission shaping
```

和：

```
Historical projection
```

如果当前逻辑在 Context 仍然充足时持续修改过去 Tool Result，需要重点重新设计。

------

### 2. 将 Tool Result Budget 前移到 Admission 阶段

例如当前类似：

```
max_tool_round_tokens
```

的机制，需要分析它究竟发生在：

```
Tool Result 第一次写入 messages 之前
```

还是：

```
已经进入历史后，在下一轮 prepare_context 中再改写
```

如果属于后者，需要评估改成：

```
Admission Budget
```

即：

```
tool executes
↓
result normalized / persisted / bounded
↓
第一次写入 messages
```

之后不再随意修改。

------

### 3. Context Manager 改为 Epoch / High-Watermark Rebase

正常：

```
append-only
```

达到：

```
HIGH_WATERMARK
```

才允许：

```
full semantic compaction
```

并且一次压回明显较低的水位。

------

# 十三、这次任务禁止的工程行为

这是强约束。

## 禁止防御性编程泛滥

不要因为：

```
理论上可能出现某个异常
```

就在各层增加：

```
fallback
secondary fallback
legacy fallback
safe fallback
compatibility wrapper
try/except swallowing
duplicate state
redundant validation
```

如果现有系统没有这个需求，本次 Spec 也没有要求：

> 不要增加。

------

## 禁止无依据的新抽象

不要为了“代码看起来架构化”创建大量：

```
ContextPolicyManager
ContextEvictionPolicy
ContextAdmissionPolicy
ContextMemoryTier
ContextMemoryClassifier
ContextRetentionStrategy
...
```

如果两个简单函数就能表达逻辑：

> 用两个简单函数。

------

## 禁止同时保留旧行为和新行为

如果 Spec 决定：

```
eager historical projection
```

不再需要：

应删除或关闭旧路径。

不要变成：

```
new_mode
legacy_mode
fallback_mode
safe_mode
compat_mode
```

除非仓库存在明确向后兼容要求。

------

## 禁止 speculative generalization

不要因为未来可能支持：

```
更多 Provider
更多 Memory backend
更多 Tool
更多压缩策略
```

提前设计插件系统。

只解决：

```
当前仓库
当前 Provider abstraction
当前 Tool architecture
当前 ContextManager
```

真实存在的问题。

------

## 禁止增加无价值状态

任何新字段都必须回答：

```
谁写它？
谁读它？
它参与哪个具体决策？
没有它为什么无法实现 Spec？
```

回答不出来：

> 不增加。

------

# 十四、第一阶段：只分析，禁止修改代码

首先完整检查仓库中所有与以下内容有关的实现：

```
ContextManager
ContextBudget
Tool Result handling
Tool execution
Artifact persistence
Source state
Checkpoint
Semantic summary
Provider usage
Prompt cache usage
Model call loop
Overflow recovery
Compaction
Tests
Metrics
```

需要追踪真实调用链。

尤其明确：

```
Tool Result
```

从：

```
Tool execution
```

到：

```
写入 messages
```

再到：

```
下一次 model request
```

期间到底经过了哪些 transformation。

------

# 十五、分析阶段必须输出的内容

在写任何代码之前，必须输出一份：

# Context Management Analysis

至少包含以下部分。

------

## A. 当前真实流程

用具体函数和文件说明：

```
Tool executes
↓
Tool Result produced
↓
哪里持久化
↓
哪里裁剪
↓
哪里进入 messages
↓
prepare_context 做什么
↓
哪里可能修改历史 message
↓
哪里做 compaction
↓
最终如何发送给 Provider
```

不要只描述抽象架构。

必须对应实际代码。

------

## B. 当前所有 Historical Mutation 点

列出所有：

```
已经进入历史 Context
```

之后还会被：

```
修改
替换
删除
压缩
```

的位置。

每一个都写：

```
文件
函数
触发条件
修改对象
释放 token 的目的
是否可能破坏 prefix cache
```

------

## C. 当前 Admission 点

确认新的 Tool Result 第一次进入 Context 前：

```
有哪些大小限制？
有哪些 formatter？
是否已经持久化？
是否已经 bounded？
```

------

## D. 当前 Compaction 触发条件

明确：

```
context soft limit
hard limit
target
eager threshold
fallback char threshold
overflow handling
```

它们之间的优先级。

------

## E. 当前 Prompt Cache 行为

如果 Provider 返回：

```
cached_tokens
cache_read
cache_write
```

确认当前是否：

```
解析
记录
暴露 metrics
```

如果没有：

说明当前缺失在哪里。

不要因为想优化 cache 就立即构造复杂 Provider abstraction。

------

# 十六、分析完成后必须先给出问题结论

请把发现分成：

```
P0 — 必须改
P1 — 高价值
P2 — 可后续实验
Not a problem — 当前设计正确
```

尤其必须判断：

### 是否真的存在：

```
eager historical rewrite
```

### 是否真的存在：

```
固定小 target 导致过早 pressure
```

### Tool Result budget 到底属于：

```
admission
```

还是：

```
historical rewrite
```

### Artifact 当前是否已经能作为：

```
cold authoritative storage
```

### Rehydration 当前是否：

```
append-only
```

------

# 十七、第二阶段：输出正式修改 Spec

完成分析后，**不要立即修改代码**。

先输出：

# Context Manager V2 Spec

要求它足够具体，使另一个工程师只看 Spec 就能实现。

必须至少包含：

------

## 1. Goals

例如：

```
减少非必要历史重写
提高 prefix stability
减少 full compaction 次数
减少 repeated read/grep
降低 total task cost
保持 task success
```

------

## 2. Non-Goals

必须明确本次不做什么。

例如：

```
不做 semantic retrieval engine
不做 per-message ML importance scoring
不做完整 memory hierarchy
不做复杂 LRU
不重新设计 Agent Runtime
```

------

## 3. Invariants

例如：

```
正常 Epoch 内历史 append-only


Tool Result 第一次进入 Context 后，
除 global rebase 外不得独立改写


Oversized Result 必须在 admission 阶段处理


Artifact persistence 和 context eviction 相互独立


Rehydration 只 append 新结果


Checkpoint 在 Epoch 内 immutable
```

------

## 4. Exact Trigger Semantics

必须明确：

```
何时进入 pressure
何时 compaction
何时不 compaction
minimum reclaim 如何判断
overflow 如何处理
```

不要使用：

```
“适当”
“较大”
“必要时”
```

这种模糊词。

------

## 5. Exact Tool Admission Semantics

分别说明：

```
read_file
grep
list_dir
bash
test output
generic oversized tool result
```

第一次写入 Context 时如何处理。

------

## 6. Exact Compaction Semantics

明确：

```
compact 哪一段
recent raw tail 保留多少
checkpoint 如何生成
artifact reference 是否保留
tool call/result pair 如何保持合法
```

------

## 7. Cache Preservation Semantics

明确写出：

```
哪些操作允许破坏 prefix
哪些操作禁止破坏 prefix
```

------

## 8. Config Changes

每一个：

```
新增
删除
重命名
默认值变化
```

都必须列出来。

禁止留下无意义 legacy config。

------

## 9. Data Structure Changes

每个新字段：

```
名称
用途
writer
reader
生命周期
```

如果无需新增字段，优先不新增。

------

## 10. Metrics

第一阶段只保留真正有价值的指标：

```
input_tokens
cached_input_tokens
uncached_input_tokens
cache_hit_ratio


context_peak
context_before_compaction
context_after_compaction


compaction_count


tool_call_count
read_file_count
grep_count


repeated_read_file
repeated_grep


artifact_rehydration_count


task success
latency
actual provider cost
```

如果 Provider 无法提供某项，明确说明。

------

# 十八、Spec 必须包含删除计划

不要只有：

```
Add X
Add Y
Add Z
```

还必须明确：

```
Remove X
Delete Y
Stop calling Z
Rename A
Simplify B
```

本次优化的目标之一就是：

> 降低代码复杂度。

所以最终代码量可能：

```
持平
甚至减少
```

而不是必然增加。

------

# 十九、Spec Review

输出 Spec 后，自行做一次严格 Review。

逐条回答：

```
1. 是否存在两个机制解决同一个问题？
2. 是否留下旧 eager projection 和新 epoch policy 同时运行？
3. 是否增加了没有消费者的新 metadata？
4. 是否为了异常场景增加过多 fallback？
5. 是否有配置实际上已经失去用途？
6. 是否任何一次 normal request 仍可能重写历史？
7. Admission 和 Compaction 是否仍然混在一起？
8. 是否存在每轮动态重写 Prompt 前部的状态？
9. 是否有机制会为了少量 reclaim 破坏大量 prefix？
10. 是否能用更少代码实现同样 Spec？
```

如果存在：

> 先修 Spec。

不要进入实现。

------

# 二十、Spec Freeze

Review 完成后，输出：

```
SPEC STATUS: FROZEN
```

并列出最终：

```
Files expected to change
Functions expected to change
Functions expected to delete
Configs expected to change
Tests expected to add/change
Explicitly untouched areas
```

从这一刻开始：

> 实现必须严格遵守 Frozen Spec。

如果实现过程中发现 Spec 有错误：

不要偷偷改变架构。

必须明确输出：

```
SPEC DEVIATION REQUIRED
```

说明：

```
原 Spec 哪一点错误
代码事实是什么
需要怎样修改
为什么
影响范围
```

更新 Spec 后再继续。

------

# 二十一、进入实现后的原则

实现阶段：

## 优先修改最短路径

如果可以：

```
删除一个 eager projection call
+
前移一个 admission function
+
修改 compaction threshold
```

完成：

不要重写整个 Context subsystem。

------

## 每个 Commit / 修改单元必须对应 Spec

任何代码变化必须能够映射到：

```
SPEC-X.Y
```

无法映射的修改：

> 不做。

------

## 禁止顺手重构

如果发现：

```
命名不好
格式不好
旁边代码也可以优化
```

除非阻碍本次 Spec：

> 不改。

避免 scope creep。

------

# 二十二、测试要求

不能只测试：

```
context tokens 变少
```

必须验证任务级效果。

------

## 单元测试

至少覆盖：

### Admission

```
small result
→ 原样进入


oversized result
→ 首次即 bounded
→ raw artifact 可恢复
```

------

### Append-only Epoch

低于 pressure 时：

```
before_model_call
```

不得修改历史 message 内容。

应直接测试：

```
message identity/content
```

或等价行为。

------

### Compaction

达到 threshold：

```
只执行一次 global rebase
```

压缩后：

```
context 显著下降
recent raw tail 存在
checkpoint 存在
```

------

### Minimum Reclaim

预计释放空间不足：

```
不 compaction
```

------

### Rehydration

```
artifact read
```

必须：

```
append result
```

不得修改原始历史位置。

------

# 二十三、Long-Horizon Integration Test

必须增加至少一个真正的长任务测试。

模拟：

```
20~40 model calls
多次 read_file
多次 grep
多次 test
大型 tool output
至少一次 compaction
```

测试应该观测：

```
是否重复读相同 source
是否重复 grep
full compaction 次数
artifact rehydration
peak context
cache-hit metrics
```

如果测试环境不能真实获得 Provider Cache：

至少记录：

```
历史 prefix mutation count
```

作为本地 proxy。

理想结果：

```
normal epoch:
historical_mutation_count = 0
```

只有：

```
global compaction
```

允许增加。

------

# 二十四、对比实验

如果已有 Context Efficiency benchmark：

至少比较：

```
Current
vs
Context Manager V2
```

输出：

| Metric          | Before | After | Change |
| --------------- | ------ | ----- | ------ |
| Task success    |        |       |        |
| Model calls     |        |       |        |
| Input tokens    |        |       |        |
| Cached tokens   |        |       |        |
| Uncached tokens |        |       |        |
| Cache hit ratio |        |       |        |
| read_file       |        |       |        |
| repeated read   |        |       |        |
| grep            |        |       |        |
| repeated grep   |        |       |        |
| compactions     |        |       |        |
| artifact reads  |        |       |        |
| peak context    |        |       |        |
| latency         |        |       |        |
| cost            |        |       |        |

不能只说：

```
Context 更小了
```

就认定优化成功。

------

# 二十五、成功标准

本次优化不是要求所有指标都下降。

允许：

```
Average Context Size 上升
```

甚至：

```
Total raw input tokens 上升
```

如果同时：

```
cache hit ratio ↑
uncached input ↓
repeated reads ↓
model calls ↓
task success ↑
total cost ↓
```

仍然属于成功。

必须用：

# Task-Level Economics

而不是：

# Per-Call Context Size

评价。

------

# 二十六、最终输出格式

在开始编码前，你必须先输出以下内容：

```
1. Repository Reality Check
2. Current Context Flow
3. Historical Mutation Inventory
4. Admission Flow
5. Prompt Cache Risk Analysis
6. Root Causes
7. P0 / P1 / P2 Findings
8. Context Manager V2 Spec
9. Config Spec
10. Data Structure Spec
11. Migration / Deletion Plan
12. Test Spec
13. Benchmark Spec
14. Spec Self-Review
15. Frozen Spec
```

只有以上内容完整并且：

```
SPEC STATUS: FROZEN
```

之后，才进入代码修改。

------

# 二十七、最重要的工程原则

整个任务始终遵守以下原则：

```
Persist aggressively.
Evict conservatively.


Shape at admission.
Do not rewrite history casually.


Prefix stability first.
Compaction second.


Append normally.
Rebase occasionally.


One meaningful compaction
is better than many tiny rewrites.


Optimize total task cost,
not per-call context size.


Measure before adding intelligence.


Do not predict future reuse
unless benchmarks prove prediction is necessary.


Prefer deletion and simplification
over adding abstraction.


No speculative defensive programming.
No compatibility code without an actual compatibility requirement.
No new state without a concrete decision that consumes it.
```

最后一条尤其重要：

> **不要试图一次设计“完美 Context Manager”。先实现最简单、可测量、Prefix-Cache-Friendly 的版本，并用真实长任务 benchmark 判断它是否解决了实际问题。**