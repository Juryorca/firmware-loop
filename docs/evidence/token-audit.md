> **注（2026-11-08）**：本页是旧 `<8k` 上限下的历史测量；当前工具输出已改为 Pi 语义（2000 行/50 KiB 尾部 + `full_output_path`），旧数字不要当作当前上限。

# Token 用量审计(真实 run,离线)

数据源:权威 SQLite 的 `events.kind='model.call'`(每次真实调用一条,含 provider usage)。
样本:`R-20261105-3518ef1d`(130 calls/138 tools)与 `R-20261106-0d7f8196`(95/109)。
未启动新的付费调用;所有数字来自已发生的调用。

## 1. 总量与口径

| 指标 | R-…3518ef1d | R-…0d7f8196 |
|---|---|---|
| `model.call` 事件 | 129 | 95 |
| `runs.model_calls` | **130** | 95 |
| prompt_tokens | 10,750,794 | 6,404,904 |
| ├ cached_tokens(命中) | 4,909,056(45.7%) | 294,272(4.6%) |
| └ 未命中(全价) | **5,841,738** | **6,110,632** |
| completion_tokens | 71,588 | 57,311 |
| └ reasoning_tokens | 43,667 | 35,536 |
| total_tokens | 10,822,382 | 6,462,215 |

口径核对(逐条验证,不是假设):

- `total == prompt + completion`:两个 run 全部成立;
- `cached_tokens ⊆ prompt_tokens`:成立(`prompt_tokens_details.cached_tokens` == `prompt_cache_hit_tokens`);
- `reasoning_tokens ⊆ completion_tokens`:成立;
- 因此**不重复累计**:账单口径 = 未命中输入 + 命中输入(折扣) + 输出;
- **差异 1 次**:`R-…3518ef1d` 有 1 次调用因协议错误(400)没有返回、也没记 `model.call`,但 `model_calls` 已 +1
  ⇒ 统计口径差异(见 §4-D),不是漏账。

## 2. 单次分布(prompt tokens)

| run | mean | median | p95 | max |
|---|---|---|---|---|
| `R-…3518ef1d` | 83,339 | 87,521 | 121,100 | 127,170 |
| `R-…0d7f8196` | 67,420 | 73,411 | 110,222 | 113,530 |

Top 5 高消耗调用(两 run 合并,均为**历史膨胀后的工具轮**,不是 FINAL):

| run | step | prompt | msgs | tool_calls |
|---|---|---|---|---|
| 3518ef1d | 124 | 127,170 | 263 | 0 |
| 3518ef1d | 123 | 126,308 | 261 | 1 |
| 3518ef1d | 122 | 124,420 | 259 | 1 |
| 0d7f8196 | 94 | 113,530 | 204 | 0 |
| 0d7f8196 | 93 | 112,937 | 202 | 1 |

## 3. 请求组成(每次调用,字符;字符≠token)

| 组成 | 3518ef1d 均值 | 0d7f8196 均值 | 说明 |
|---|---|---|---|
| system | 5,147 | 5,321 | 规则 + 动态状态(修复后拆开,见 §4) |
| history 合计 | 171,139 | 134,595 | 只追加,不重排 |
| ├ tool 结果 | 92,727 | 82,246 | 单条上限 8k(`_bounded_tool_result`),实测最大 3,280 / 941 |
| └ assistant | 78,412 | 52,350 | 含 `reasoning_content`(thinking 协议要求回传) |
| tool schema | 5,430(15 个工具,≈1.5k tokens) | 同 | 每轮发送,正常机制 |
| 重复工具结果 | 138 条中 133 条唯一(5 条重复) | 109/109 唯一 | 重复来自模型重复读取,非框架重复追加 |

## 4. 根因:system 每轮变化 → 前缀缓存整体失效(已修)

缓存命中率随调用序号的实测(`cached/prompt`):

```text
3518ef1d: 0-69  ~98%   70-119  ~1%   120-128 0/99 交替
0d7f8196: 0-9   44%    10-59   1-9%  90-94   0-1%
```

**证据链**:
1. `3518ef1d` 里 system 长度不变(1 个变更点)的窗口 40–59:calls 平均命中 **98.3%**;
   窗口 80–99:system 变更 1 次后 **1.8%** 再也没恢复;
2. 离线复现(拷贝库、加 1 条 observation 再渲染):system prompt 在**字符偏移 795**处变化
   (`"observations": 19` → `20`、facts 列表多一行)⇒ 前缀从第 795 字符起全不匹配;
3. 结论:动态内容(`derived` 计数 / `已有研究事实` / 进度 / 目标)原本在**第一条 system 消息**里,
   研究一推进就变 ⇒ provider 的前缀缓存无法复用。

**修复**:`Research.rules_prompt()`(静态:角色/不变量/工作方式/方法清单/规则原文)
与 `state_prompt()`(动态:派生量/事实/进度/池/三镜/目标)分开;
runtime 发送 `[system 静态] + history + [user 动态状态]` ⇒ 前缀 = system+history 稳定,
只有尾部状态与新增工具结果未命中。

验证:`tests/test_prompt_cache_layout.py`(system 逐字节一致、历史只追加、动态在尾部)。

## 5. 分类判定

### A 正常必要消耗
- 完整历史每轮重发:Agent 必须带上下文;缓存命中时是折扣价;
- tool schema 5,430 字符/轮(≈1.5k tokens):15 个工具,注册无重复(实测 `describe()` 无同名);
- assistant 的 `reasoning_content` 回传:thinking 协议硬要求,不回传会 400。

### B 可解释、已优化
- 动态 system 导致缓存失效(§4):已修,不删任何研究信息;
- 单条工具结果上限 8k 字符且始终合法 JSON(实测最大 3.3k),没有超长结果。

### C 框架异常
- 旧 run 的 200 次重复 FINAL(已修,见前轮);本轮两 run **无**重复 FINAL;
- 1 次 resume 因 `reasoning_content` 未补而 400(已修:默认补);
- `0d7f8196` 有 4 次完全相同的 python 命令(唯一 60/67):模型重复读同一库,不是框架重复执行;
- 无重复注册、无重复 checkpoint 加载(历史加载点是 resume/advance 各一次,`_load_history` 单次读取)。

### D 统计异常
- `model_calls` 比 `model.call` 多 1(失败调用计入预算但无 usage)⇒ 已在报告中说明,保留计数语义(预算是执行边界)。

## 6. 估算与结论(明确标注为估算)

- 两个 run 未命中输入合计 **11,952,370 tokens**;稳定窗口实测可达 ~98% 命中。
- 若修复后达到 98% 命中,未命中量级 ≈ 12.9M × 2% ≈ **0.26M**(数量级估算,按未命中计价可省约 11.7M 全价输入 tokens;
  实际账单依 provider 定价与缓存策略,不冒充精确值)。
- 该修复**不删历史、不摘要、不减工具定义**,只调整 message 布局 ⇒ 研究能力不受损。

## 7. 剩余问题

1. 没有精确 tokenizer:组成分析用字符数(明确标注),只有 usage 是真实 token;
2. `resume` 时 `_sanitize_history` 可能改写旧坏消息(一次性 cache miss,可接受);
3. `state_prompt` 每轮重算(纯 CPU,无 API 成本);
4. 旧 run(`0b4df34e`)的 usage 未记录,无法回溯(不估算成真实值)。

## 附:早期审计(2026-11-06 上半场,同一数据源)

- 修复前的一次调用实测:`prompt=222,238 / cached=222,080 / miss=158 / completion=1,731(reasoning 871)`
  ⇒ 长上下文确实"命中率极高",但每轮仍全量重发;
- 旧 run 的空转:200 次重复 FINAL(内容相同、无工具调用)把预算烧光;根因是 FINAL 写在**第二段**
  而旧判定只认开头 —— 已修(任意行首 `FINAL:` + 重复内容看护);
- 旧 run checkpoint 473,850→492,771 B(120 条);第二阶段 10,335→384,502 B;
- 结论口径:`total == prompt + completion`、`cached ⊆ prompt`、`reasoning ⊆ completion` 三者在两个
  run 的 224 条 usage 上逐条验证成立,不重复累计。
