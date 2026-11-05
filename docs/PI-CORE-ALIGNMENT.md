# Pi Agent Core 对照与对齐记录

**源码基线**:`earendil-works/pi` @ **`d57c2d21916eaad1158031526ac6ec860eae5f14`**(2026-11-04 取)
读取文件(本地副本在 `.tmp/pi-ref/`,gitignored):
`packages/agent/src/agent-loop.ts`(959 行,已通读结构与主循环)、`agent.ts`(623)、`types.ts`(551)、
`packages/coding-agent/src/core/agent-session.ts`(4532,只抽查会话/事件相关部分)。

> 本表只写**我实际读过**的行为;未核实的点直接标"未核实",不凭印象下结论。

## 1. 逐项对照

| # | 主题 | Pi 怎么做(源码依据) | fwcore 现在怎么做 | 实质差距? | 值得改? | 反例/验证 |
|---|---|---|---|---|---|---|
| 1 | 主循环语义 | `runLoop()`:外层 `while(true)` 处理排队消息;内层 `while(hasMoreToolCalls \|\| pendingMessages.length>0)` 交替"发模型 → 执行工具" | `Runner.turn()`:单层 `while`,每次模型调用后执行该批工具,再进入下一次 | 结构等价(都没有把工具结果拼接回"同一轮"的额外层) | 否 | `tests/test_runtime_core.py::test_run_executes_tool_calls_and_finishes` |
| 2 | 事件生命周期 | 显式事件流 `emit(...)`:`turn_start` / `message_start` / `message_end`;工具执行另有 `toolExecution` 事件 | SQLite `events` 表(kind/ref/payload),`run.limit_hit`/`round.ended`/`wheel` 等 | 形式不同,语义覆盖相近;fwcore 缺"消息级"事件 | 暂否(研究可核性由 domain 不变量覆盖) | `tests/test_watchdog.py`(watchdog.paused 事件) |
| 3 | 流式输出 / Provider 差异 | `streamAssistantResponse()` + `StreamFn`;工具参数 `prepareToolCallArguments()` 统一处理(含截断) | 非流式:`model_client` 一次性取回;tool_call 参数两种形状都归一(`_normalize_call`) | **有差距**:不支持流式与"参数被截断"的显式失败 | 是(低优先) | 已覆盖:真实端点 400(工具名/`reasoning_content`)有测试;流式缺测试 |
| 4 | 取消 / 中途干预 | `runLoop(..., signal: AbortSignal)` ⇒ 用户 ESC 可中断;`failToolCallsFromTruncatedMessage()` 让被截断的调用显式失败 | **本轮新增** `Runner.cancel()`:置 `paused` + `handback=user_stop` + 杀工具进程组;循环在每个安全点检查 `_external_stop()` | 曾经没有(只能靠异常) | 已在本次修 | `tests/test_cancel.py` 2 passed(安全点停止 / 取消后不写结论) |
| 5 | 工具 Hook / 失败传播 | `prepareToolCall()` → 执行 → `finalizeExecutedToolCall()`;失败生成 `error tool result` 消息继续循环 | `Registry.call()`:策略 → 参数校验 → 执行 → 记录真实状态(ok/error/timeout/cancelled);失败也回填 tool 消息 | 基本等价;fwcore 多了"策略+幂等",少了 pre/post hook | 否 | `tests/test_runtime_core.py::test_shell_exec_records_statuses` 等 |
| 6 | 上下文裁剪 / 会话恢复 | 会话层负责 compaction(`agent-session.ts`,未逐行核实);恢复靠会话存储 | 检查点:裁剪旧工具输出 + 保留 N 条 + DB 硬顶;`resume()` 取最新检查点 | **恢复语义有差距**:Pi 有 compaction 钩子点(`prepareNextTurn` 可做长耗时准备) | 是(中优先) | `tests/test_resource_guards.py::test_checkpoint_pruning_keeps_resume_correct` |
| 7 | 错误处理 / 重复调用 / 并发 | `executeToolCallsSequential` 与 `executeToolCallsParallel` 两种批执行;`shouldTerminateToolBatch()` 决定是否终止批次 | **只有顺序执行**;重复调用靠 `call_key` 幂等(重放返回缓存) | 并发批缺失;幂等是我们比 Pi 更严的部分 | 并发:暂否(固件工具多为独占);幂等:保留 | `tests/test_recovery_idempotency.py`(副作用只一次) |
| 8 | 资源边界 vs 研究停止 | Pi 无"研究"概念;循环靠 ESC/队列驱动 | `limits.py`(调用数/墙钟/工具时长/检查点/DB)+ `watchdog`(外层)+ domain 收尾条件 | **fwcore 更强**(必须更强) | 保留 | `tests/test_resource_guards.py` / `test_watchdog.py` / domain 不变量 |

## 2. 结论(是否值得改)

**现在就改(按优先级)**
1. ✅ 取消语义(第 4 项)—— 已实现 + 测试。
2. 上下文/恢复的"准备钩子"(第 6 项):Pi 允许在下一轮前做长耗时准备(`prepareNextTurn`),fwcore 目前只在 `turn` 里同步做。**下一步**评估是否需要"轮前准备 + 可取消"。
3. 工具参数被截断时的显式失败(第 3 项)—— Pi 有 `failToolCallsFromTruncatedMessage`;fwcore 只在 JSON 解析失败时标 `__invalid_json__`(够用,但没有"截断 ⇒ 明确失败"的语义)。

**不改(有理由)**
- 并发工具批(第 7 项):固件工具多为独占资源(仿真端口/容器/IDA 单库),并发收益低且易引入竞态。**若将来出现可安全并行的只读工具,再按 Pi 的 `executeToolCallsParallel` 语义加**。
- 流式输出(第 3 项):研究型 agent 的产出以工具与状态为主;流式的价值在交互体验,不在正确性。保留现状,记为已知差距。
- 事件模型的"消息级事件"(第 2 项):研究可核性由 domain 不变量 + `events` 表覆盖;不追平 Pi 的 UI 级事件粒度。

**必须与 Pi 不同(领域原因)**
- 停止语义:Pi 的"传一句 final 就结束"在固件研究里**不等于收尾**。fwcore 用 `closure_blockers()`(未收口假设/未判定面/running 尝试/材料缺失/三镜超期)决定能否交回控制权,`FINAL` 只结束本轮。
- 执行边界:资源上限只能 `paused`(可恢复),永不写结论;`watchdog` 是**外层**保护,不依赖循环配合。

## 3. 差异清单(保留下来的 fwcore 核心)

SQLite 权威状态 · Hypothesis/Attempt/Observation/Evidence/Conclusion · 证据归属与版本不变量 ·
Capability/Provider/Environment 解析 · 异构固件工具适配 · 长研究可恢复 · 工具失败不得否证漏洞 ·
答案隔离的研究评测(`safe-control` / `decoy-comment` / `decoy-log`)。

## 4. 后续(写入 ROADMAP)

- 轮前准备钩子(可取消、有超时)+ 其反例测试
- 工具参数截断的显式失败语义
- 若引入只读并行工具:按 `executeToolCallsParallel` 语义实现并测并发安全

## 5. 第二轮增量(2026-11-05,基线仍 `d57c2d2`)

这一轮把"工具执行"从"能跑"推到"可审计、可取消、可恢复"。逐项对照 Pi 的既有机制(引用行号 = `packages/agent/src/agent-loop.ts`):

| 机制 | Pi 怎么做 | fwcore 怎么做 | 差距 / 为什么 |
|---|---|---|---|
| 工具批次中途取消 | `executeToolCallsSequential` 每执行完一个调用就检查 `signal?.aborted` 并 `break`(L540–585);并行版在派发前检查(L596+) | `turn()` 的工具循环之间检查 `_external_stop`/`cancel`;`Registry.call` 内层对长命令做 0.2s 切片轮询,发现 `runs.handback_reason=user_stop:*` 就整组杀掉并记 `cancelled` | Pi 靠 AbortSignal 传播;fwcore 的"取消"是**数据库事实**(可跨进程/跨主机),所以取消必须在 DB 里可查——这不冲突,是同一语义的不同载体 |
| 工具生命周期归属 | 工具执行是**单次 loop** 的一部分,由 session 持有 | MCP 桥归 `Runner` 所有:`turn()` 不关桥,`close()` 幂等,`_ensure_mcp()` 跨轮/恢复自愈并记 `mcp.reopened` | 上一版把桥的关闭放在 `_finish_state()` ⇒ 自动继续的第 2 轮 MCP 全废(已修,见 `tests/test_mcp_lifecycle.py`) |
| 工具结果作为事实 | 工具结果进消息流;是否"可信"由上层判断 | 工具结果**落盘 + 哈希**(`tool_calls.result_path/result_sha256`),结构化观测存 `observation`;证据必须 `add_evidence(tool_call_id=...)` 且默认 `verified=0`,复核后 `promote_evidence()` 才能支撑 `vulnerable` | 研究领域的核心差异:工具跑成功 ≠ 漏洞成立。Pi 不需要这层,因为它的"事实"由使用者解释;fwcore 要把"解释"变成程序可核的门 |
| 长工具 vs 停滞 | 无独立看护:传输层超时由工具自己管 | 看护发现心跳过期时先查**在飞 `running` 工具调用**,在 `max(max_tool_seconds, heartbeat)+120s` 内只续心跳,不暂停不杀组 | 外部看护是 fwcore 独有的(事故驱动);判据必须与"研究停滞"区分开,否则会误杀正常的 10 分钟 fuzz |
| 失联判定 | 进程内运行,不存在"失联" | `recover_stale()`:同主机 pid 存活 + `pid_starttime` 比对(pid 复用视为死);跨主机默认不动,`--include-remote` 才按倍数阈值兜底;长工具在飞同样保活 | 固件研究是长时任务、会换会话/换机器继续,所以"谁还活着"必须是可核事实而不是假设 |

仍未采纳(理由同第 2 节):并行工具批次、流式输出、UI 级消息事件。
