# Token 消耗审计(2026-11-06)

范围:`fwcore` 研究 Agent 调用真实模型(QNAP 演练)的消耗 vs. 开发会话(Pi/DS)的消耗。
数据源:权威 SQLite(`events.kind='model.call'` 于本轮新增)、`checkpoints`、`runs`。

## 1. 两类消耗,分开看

| 类别 | 可观察数据 | 结论 |
|---|---|---|
| fwcore → DeepSeek(QNAP 演练) | 见 §2/§3(`model.call` 事件、runs 计数) | **异常来源已定位**:同一份历史每次全量重发,且历史在 run 内无限增长 |
| Pi/DS 开发会话(读码/测试/分析) | 本会话无法读取 Pi 的 usage 账本(harness 不落盘 token 账) | **未知**,不估算;但已核对:测试不会打真实 API(见 §5) |

不混合两类数据;下文 §2–§4 只谈 fwcore。

## 2. QNAP 各阶段调用的实际 usage

| run | 阶段 | model_calls | tool_calls | 有 usage 记录的调用 | 备注 |
|---|---|---|---|---|---|
| `R-20261105-0b4df34e` | 旧 run(含 200 次空转) | 421 | 219 | **0**(当时未记 usage) | 仅 checkpoint 体积可查:120 个,473,850→492,771 B(均值 483,310 B) |
| `R-20261105-f640454a` | scripted 冒烟(误用) | 2 | 1 | 0 | 不是研究 run;已从 ROADMAP 更正 |
| `R-20261105-984803d9` | 第二阶段真实研究 | 90 | 134 | **1**(收尾那次 FINAL) | checkpoint 90 个,10,335→384,502 B(均值 218,912 B) |

唯一一条真实 usage(第二阶段收尾调用,`events.kind='model.reply'`):

```
prompt_tokens=222238  completion_tokens=1731  total_tokens=223969
prompt_tokens_details.cached_tokens=222080   prompt_cache_miss_tokens=158
completion_tokens_details.reasoning_tokens=871
```

推断(标注为推断):该 run 结束时上下文体量 ≈ 384 KB JSON / 222,238 prompt tokens ≈ **1.73 B/token**。
用同比例对整 run 做**数量级**估计:上下文从 10 KB 线性涨到 384 KB,90 次调用 ≈ 17.7 MB JSON ≈ **10M prompt tokens**(命中缓存的占绝大多数)。旧 run 491 KB ≈ 28 万 tokens/次 × 421 次 ≈ **1.2 亿量级**,其中 200 次空转的重复 FINAL 每次同样携带近全量历史 —— 这就是"消耗明显高于预期"的主因,而非模型"话多"。

## 3. 根因(代码位置)

1. `turn()` 原来是 `msgs = [system] + history`,**history 全量发送**;
2. `_shrink_history()` 只在 `_checkpoint()` 落库时裁剪 ⇒ **下一次请求仍然带全量旧工具输出**;
3. 工具输出每条最多 2000 字符(tail),134 条 ≈ 268 KB,加上 assistant 文本与 system prompt,线性增长;
4. `research.system_prompt()` 每轮重建并注入派生状态(`derived` + `progress_brief`,截断到 3000 字符),与 history 有部分重叠,但不是主因(≈1–3k 字符/次);
5. 工具 Schema 每次全量发送(`registry.describe()`),仅注册可用工具,体积远小于历史,不是主因。

## 4. 修复(最小,不做记忆平台)

| 项 | 改动 | 证据 |
|---|---|---|
| 不做字符裁剪 | **定稿:历史全量发送**。缓存命中 99.93% 说明长上下文是正常机制,不是费用异常;不设"字符上限冒充 token 上限"的旋钮 | `tests/test_context_budget.py::test_full_history_is_sent_and_every_call_logged` |
| 每次调用记账 | `model.call` 事件:step / n_messages / system_chars / history_chars / tool_chars / assistant_chars / usage / usage_known / prompt·completion·cached·reasoning tokens / finish_reason / tool_calls | 同上(7 次调用 = 7 条事件) |
| 不猜 token | 服务端没给 usage ⇒ `usage_known=false`,`prompt_tokens=null` | 同上(断言全为 false) |

**定稿**:不裁剪历史(长上下文是资产);仅记录每次请求体积与 usage,用于发现"重复调用/空转"这类真异常。

## 5. 无效调用 / 真实 API 误触

- 测试:全部走 scripted adapter 或 fake transport;`tests/test_model_config.py` 只改环境变量做配置解析,不建连接。本轮新增的协议测试用模拟 HTTP transport。
- 误触一次真实调用:用 `fw-core run drill` 默认 scripted 冒烟(2 次调用,`R-…f640454a`),发现后改用 `--model openai` 重跑;该 run 保留在库里作为执行事实,但**不计入研究统计**。
- 空转:200 次重复 FINAL 已由 FINAL 识别修复 + 重复内容看护阻止;不是靠把调用数从 200 降到 20。

## 6. 剩余问题(如实)

1. 旧 run 的 421 次调用**没有 usage 记录**,只能给数量级估计,不能给精确值;不补跑、不猜。
2. Pi/DS 开发会话的 token 账本不可得 ⇒ 无法给出数字;只能确认测试不触发真实付费调用。
3. 缓存命中率依赖服务端(`prompt_cache_hit_tokens`),本轮之后才有记录;旧数据不可回溯。
4. 没有任何 token/字符上限控制:接近模型上下文上限时由 API 报错(见 §2 的 usage 记录判断),不做猜测式裁剪。
