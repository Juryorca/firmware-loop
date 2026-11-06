# Agent Runtime 架构对照(参考 Pi,不依赖 Pi)

只读研究对象:`@earendil-works/pi-coding-agent` 安装版本的 `dist/core/`
(`agent-session.js`、`messages.js`、`compaction/*.js`)。
Pi 只是**架构参考**,fwcore 不调用、不依赖、不提交 Pi 的任何配置/会话。

## 1. 逐项对照

| 领域 | Pi 实际做法(源码依据) | fwcore 当前做法 | 差异 | 调整 |
|---|---|---|---|---|
| 会话存储 | 会话是**append-only entries**(树/分支),provider 上下文由 `buildSessionProjection().messages` 投影得到 | 内存 `history` 列表 + 每步一个 `checkpoints` 快照 | 快照是"每步全量",写入放大约 N 倍 | 暂保留(有恢复价值);长期改 append-only 日志 |
| 消息角色生命周期 | user/assistant/tool 严格按协议;`convertToLlm` 统一转换 | 同一套(assistant.tool_calls ↔ tool.tool_call_id),`to_api_messages` 做配对校验 | 无差异 | — |
| System Prompt | 每会话一份 `session.systemPrompt`;运行中通过 `_preparePromptAndToolLoadout` 产生的**更新消息**进入对话,而不是改写 system | `rules_prompt()` 静态规则(逐字节稳定);动态状态作为**尾部 user 消息** | 结构一致(动态不进 system) | 已对齐 |
| 动态上下文 | 有 "context-only custom messages, flushed once the current turn's tool results are in" | `state_prompt()` 每轮生成、**不写入 history**、只作为本次请求的最后一条 user 消息 | 一致 | 已对齐(加"程序生成"标注) |
| 工具往返 | provider tool_calls → tool result 一一对应;compaction 保证不切断配对 | 同;`to_api_messages` 校验缺配会抛错 | 一致 | — |
| Context 用量 | `calculateContextTokens` / 估算 + `shouldCompact(contextTokens, contextWindow, settings)` | `fwcore/compact.estimate_tokens`(字符/3.5,只用于触发) + `Limits.context_tokens`(显式配置) | 估算精度低 | 显式配置窗口;真实计费仍看 usage |
| Compaction | 阈值/溢出触发;保留最近 `keepRecentTokens: 20000`;用**模型**生成结构化摘要,后续用 `UPDATE_SUMMARIZATION_*` 增量保留旧信息 | 本轮新增:`compact_history()`(安全切点 + 保留最近 token + 注入摘要器);摘要默认调用模型并**计入预算** | 已具备同构机制 | 已对齐;增量摘要暂未做 |
| Resume | 从 entries 投影恢复;compaction 记录也在其中 | `_load_history()` 读最新 checkpoint(含旧坏 JSON 修复);`resume`/`advance` 共用 | 一致 | 已对齐 |
| 模型切换 | `resolveModel` 每轮可换 | 单模型(adapter 固定) | 未支持(非当前需求) | 记录为差异 |
| 重试/中断 | 溢出做 compaction 后重试;错误结束本轮 | 失败异常上抛并落库;预算触发暂停 | 无自动重试 | 记录为差异(不做静默重试) |

## 2. 消息生命周期(fwcore,当前)

```
目标/DB 事实 ──► state_prompt() ─┐                     (动态,不持久化)
                                ├─► 请求 = [system 静态] + history + [user 状态]
history(append-only in turn) ───┘
   │
   ├─ assistant(content | tool_calls, reasoning)
   ├─ tool(tool_call_id, content[≤8k, 合法 JSON])
   └─ (阈值:context_tokens) compact_history() → [摘要头] + 最近 keep_recent_tokens
                                   │
                                   └─► checkpoint(每步,磁盘保护 ≤ 1MB 时截断旧工具输出并留路径)
resume/advance ──► _load_history()(最新 checkpoint + 坏 JSON 修复)──► 继续追加
```

## 3. 本轮缓存调整的设计复核(7 问)

1. **动态状态进持久化 history 吗?** 不进:`msgs` 每轮现拼,`history` 只追加 assistant/tool;
   测试 `test_prompt_cache_layout.py::test_history_prefix_is_append_only`。
2. **每轮产生新状态快照?** 只在请求里;不留存。
3. **旧快照积累?** 不积累(上一条保证);压缩测试还断言摘要只出现一次。
4. **多份过期状态冲突?** 不可能(同一时刻只有一份,位于尾部)。
5. **缓存复用范围?** `system(静态) + history(已发送部分)`;尾部状态与新增工具结果不命中
   ——这正是"正常且必要"的未命中部分。
6. **会不会改变用户指令语义?** 有风险:尾部 user 消息可能被当成新指令。
   已加显式标注「现状(程序生成,不是用户的新指令)」;原始目标仍以同一块里的「本轮目标」表述,不含指令性变更。
7. **resume/advance 顺序一致?** 两者都走 `_load_history()`(同一修复/同一顺序);
   `test_advance_resumes_history_from_checkpoint` 覆盖续跑历史存在性。

## 4. 主动发现的架构级问题

| 问题 | 根因 | 影响 | 处理 |
|---|---|---|---|
| 无上下文生命周期(历史无限增长) | 只有预算/时间边界,没有窗口感知 | 长跑必然撞 provider 上限(实测 prompt 已达 127k) | **本轮修**:阈值压缩 + 保留最近 + 摘要(模型调用计入预算) |
| 动态内容在 system 首位 → 前缀缓存整体失效 | 把状态拼进第一条消息 | 实测命中 98% → 1%,未命中输入 11.95M | 上轮已修(system 静态化) |
| checkpoint 每步全量快照 | 恢复优先 | 300+ 快照、写入放大 | 记录为已知限制(快照有恢复价值,未改) |
| checkpoint 磁盘保护截断旧工具输出 | 控制在 200 字符 | 恢复后细节丢失(保留 tool_call_id + 产物路径) | 记录为**设计选择**,不冒充压缩 |
| `model_calls` 与 `model.call` 事件差 1 | 失败调用计入预算但无 usage | 统计口径需说明 | 文档标注,不猜 token |

## 5. 明确不做

- 不引入第二套会话存储、不复制 Pi 代码、不依赖 Pi 运行时;
- 不做模型自动切换/静默重试;
- 不在没有真实 usage 数据时宣称"省了多少 token"。

## 6. 上下文感知(Context Awareness)与压缩联动(2026-11-06,P8)

**注入位置**:合并进已有的 `state_prompt()` **尾部**(不新增消息、不进 system、不写 history/checkpoint)。
内容(每次请求实时生成):

```
[Context status] model=… window=… estimated_input=… output_reserve=… available=…
                 auto_compact=on|off threshold=…
last reply usage: prompt … (cached …). estimated_input is an estimate (chars/2.0), not provider usage.
+ 行为提示:空间充足 ⇒ 继续工作,不得声称上下文快满;>=90% 阈值 ⇒ 保存关键进度,框架会在安全边界压缩;
  窗口未配置 ⇒ 明确 unknown,不得假设快满。
```

**Token 估算校准(真实数据,224 次调用)**:实测 **2.08 字符/token**;旧的 3.5 会**低估约 40%**
(est/actual ≈ 0.59)。现用 **2.0**(略偏保守,宁可早触发);估算只用于触发阈值,计费仍以 usage 为准。

**阈值**:`context_tokens`(显式窗口,0=不启用)+ `output_reserve_tokens`(默认 8k);
阈值 = 窗口 − max(预留, 10% 窗口)。生产配置在 `state/model.json`:`context_tokens: 1000000`
(依据:Pi 模型目录 `deepseek-flash` 的 `contextWindow=1,000,000`,不是按名字猜)。

**压缩失败语义**:摘要异常 ⇒ **历史原样保留** + `run.compact_failed` 事件 + 冷却 `compact_retry_cooldown_calls`
(默认 5 次调用内不重试,避免重复付费);摘要为空视为失败;不产生"空摘要头"。

**真实 10 次调用 A/B 基线**(独立临时工作区,非研究数据):

| 指标 | 值 |
|---|---|
| prompt_tokens | 2,552 → 5,767(单调增长) |
| cached_tokens | 0 → 3,968(第 2 次起命中) |
| 命中率 | 70.9% → 81.3%(小历史;大历史下更高) |
| 状态块自身 | 332 字符 ≈ 160 tokens(按 2.08 字符/token 估算;≈ 初始请求的 6%) |

**未验证(如实)**:长历史(>500k tokens)下的压缩后命中率、多轮压缩的真实 API 行为——本轮未跑付费长程,
只做了离线/fake 端到端。

## 7. P9:Pi 实际运行基准 vs fwcore(实测,非推断)

### 7.1 Pi 实际运行事实(本机只读测量)

- 版本 **pi 1.5.0**;会话记录中的 provider/model:`deepseek/deepseek-flash` 5,352 次(另有少量 `sigvoid/deepseek-flash`、`deepseek/deepseek-v4-pro`)。
- 模型目录(pi models-store):`deepseek-flash` **contextWindow=1,000,000 / maxTokens=384,000**;
  单价(每 1M):input **$0.3**、output **$1.2**、cacheRead **$0.006**(≈ 输入价的 1/50)、cacheWrite 0。
- Pi 每条 assistant 记录的 usage 字段:`input`(未命中输入)、`cacheRead`(命中)、`cacheWrite`、`output`、`reasoning`、`totalTokens`、`cost`。
- **实测命中率(不是 UI 百分比)** = `cacheRead / (input + cacheRead)`:
  2,505,984,896 / (41,757,463 + 2,505,984,896) = **98.36%**(n=5,386);按 1000 条分段 97.6%–99.9%;最近 50 条 97.8%。
  分母只含输入(cacheRead 已包含在 prompt 侧),不含 output/reasoning ⇒ 与 DeepSeek 的
  `cached_tokens ⊆ prompt_tokens` 口径一致。

### 7.2 DeepSeek 原生缓存与"客户端该做什么"

官方文档确认:缓存**默认开启**,按**请求公共前缀**复用,命中 best-effort、可能过期清除。
⇒ 客户端不需要实现任何缓存;需要做的是**保持请求前缀稳定**并**读真实 usage**。
Pi 的 98.36% 不是"Pi 有客户端缓存算法",而是它的请求结构天然前缀稳定。

### 7.3 fwcore 实测(同一官方 endpoint,独立临时工作区,31 次调用)

| 区间 | prompt_tokens | cached_tokens | 命中率 |
|---|---|---|---|
| 1–10 | 54,882 | 43,904 | 80.0% |
| 11–20 | 117,424 | 104,576 | 89.1% |
| 21–30 | 177,700 | 164,864 | 92.8% |
| 全部 | 371,061 | 333,056 | **89.8%** |

逐次命中 85% → 93%(单调上升)。请求结构:`[静态 system] + history(append-only) + [尾部 state ~160 tokens]`,
工具 schema 固定 5,430 字符,`reasoning_content` 按协议回传。
修复前(P6 之前)的对照:system 稳定的窗口 **98.3%**,system 内容一变就掉到 **1.8%**。

### 7.4 结论

1. **Pi 与 fwcore 的差异不在缓存算法**,而在请求前缀是否稳定;fwcore 的 P6 修复正是修这个根因;
2. 剩余差距(89.8% vs 98.36%)主要来自**小历史下新增内容占比大**(每次新增工具结果 + 状态块占本次 prompt 的比例),
   以及 DeepSeek best-effort/批次边界;随历史增长命中率单调上升(85→93),不是结构性缺陷;
3. 不引入任何客户端缓存实现;不删历史、不减工具定义。

### 7.5 仍未验证(如实)

- >500k tokens 长历史下的稳态命中率(本轮未跑到该规模);
- 多轮压缩后的真实 API 缓存重建行为(只做了离线模拟);
- 第三方网关下的缓存行为(本机实测走官方 `api.deepseek.com`)。

## 8. P9 验收纠偏(2026-11-06 补充)

### 8.1 缓存对比必须按请求规模分层(否则不可比)

同一 Provider/模型(`deepseek/deepseek-flash`),按 **prompt_tokens 分层**:

| 请求规模(prompt tokens) | Pi 命中率(n) | fwcore 命中率 |
|---|---|---|
| < 20k | **88.47%**(338) | **89.8%**(31 次受控实验,mean 12k) |
| 20k–60k | 95.37%(389) | 无 post-fix 数据(修复前 54.2%) |
| 60k–150k | 99.10%(1,352) | 无 post-fix 数据(修复前 26.9%) |
| 150k–300k | 98.61%(2,235) | — |
| > 300k | 99.01%(7,248) | —(Pi 均值 585k,与 fwcore 31 次不可直接比) |

结论:**此前"98.36% vs 89.8%"是规模差异**;同规模(<20k)下两者持平(88.5% vs 89.8%)。
Pi 的 98.96% 总体值来自均值 423k 的长历史;fwcore 尚无 post-fix 大历史实测 —— 如实标注为未验证。

**可复核口径**:`scripts/pi_cache_stats.py`(只读、脱敏,输出只有数字;支持 `--provider/--model/--by-size`)。
命中率定义 `cacheRead/(input+cacheRead)`;不混用不同 Provider/模型,不把 UI 占用率当命中率。

### 8.2 摘要覆盖保证(P0,已修)

旧实现:每条消息 `[:1500]`、整段 `[:60000]` ⇒ 接近 1M 上下文时只摘要最早一小段,却删掉远多于此的历史。
现在 `compact.summarize_covered()`:
- 以**协议组**为单位装块(每块 ≤ `summarize_chunk_tokens`,默认 60k);
- 单条超大消息按内容**切片**,每一片都送进摘要;
- 多块摘要再**归并**(最多 `summarize_max_calls`,默认 20 次);超过上限 ⇒ `SummarizationIncomplete`;
- 任何一块返回空 ⇒ 失败;失败时**历史原样保留** + `run.compact_failed` + 冷却。
覆盖可核对:`info = {chunks, messages(原始条数), pieces(切片数), calls, merge_calls}`,
测试断言 `messages == len(dropped)` 且 `pieces >= messages`。

### 8.3 摘要预算(P0,已修)

默认 summarizer 在**发起请求前**执行与普通调用相同的 `calls_exhausted(run_calls+1)` 检查;
超出预算 ⇒ 抛错 ⇒ 压缩失败(保留历史),不会越过 run 级授权上限。摘要调用计入 `run_calls` 与
`model.call{kind:"summarize"}`(含 prompt/cached/completion/reasoning,缺失为 unknown)。

### 8.4 上下文估算口径(P1,已修)

`build_status()` 的 `estimated_input` 现在包含**整个请求**:system + history + 尾部 state +
工具 schema + 协议开销(每条消息 8 tokens 保守常数),并在状态块里给出分解
`(system … + history … + state … + tools … + protocol …)`。
早期版本只估 history、却标成 `estimated_input`,会把"可用空间"报大 —— 已改。

### 8.5 阈值语义(P1,已明确)

阈值 = `window − max(output_reserve, 10% window)`:

- 生产(1M 窗口,8k 预留):**900,000 = 90%**;
- 小窗口(30k 窗口,8k 预留):22,000 = **73.3%**(固定预留生效,不是 90%)。

测试 `test_threshold_proportional_vs_fixed_reserve` 分别覆盖两种情形;模拟测试不再把 85% 当成生产触发点。

### 8.6 多轮压缩序列(P1,已重做)

`test_three_compactions_across_resume_and_advance`:增长 → 压缩#1 → 追加 → 压缩#2 →
**模拟进程退出后 resume** → 追加 → 压缩#3 → `advance`。每次断言压缩计数**实际增加**
(不满足即失败,不静默跳过),并断言配对完整、hypothesis/evidence 未变。
仍属**离线模拟**:真实 1M 级历史的多轮压缩未验证。
