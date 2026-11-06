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
