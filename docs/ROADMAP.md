# 待办与完成证据(唯一清单,勿另立)

> 每一行都要能指到**证据**(测试文件/命令输出/提交),不接受"已做"字样。

## P0 事故(2026-11-04 62GB)—— 已关闭

| 项 | 状态 | 证据 |
|---|---|---|
| 停进程 / 核路径 / 清残留 | ✅ | 无残留进程;`/tmp`、`/var/tmp` 两处 pytest 目录已清;WSL 内部 226G→164G(-62G) |
| 执行边界与研究停止规则分离 | ✅ | `fwcore/limits.py` + `tests/test_resource_guards.py`(断言 hypotheses/conclusions 不变) |
| 检查点不会无限增长 | ✅ | 裁剪旧工具输出 + `checkpoints_keep` + DB 硬顶;`test_checkpoint_pruning_keeps_resume_correct` |
| 工具子进程不失控 | ✅ | `Popen(start_new_session)` + `killpg`;`test_tool_timeout_kills_whole_process_group` |
| **外层独立看护** | ✅ | `fwcore/watchdog.py` + `tests/test_watchdog.py`(卡死/库超限/健康不误伤/守护可停,1.3s) |
| 测试自身受保护 | ✅ | `conftest.py` 每条用例 20s 硬超时;临时目录 `.tmp/pytest`;Makefile `timeout` |
| 事故记录 | ✅ | 项目外:`~/.agents/skills/agent-runtime-safety/SKILL.md`(项目文档不写事故叙事) |

## 第二阶段:639071a 审计项

| 项 | 状态 | 证据 |
|---|---|---|
| AFL guest 架构探针误判 | ✅ | 改"真跑 aarch64 样本"(实测 141 tuples);`file` 判 guest 架构是错的,已在 providers 注释里写明反例 |
| SQLite 升级路径 | ✅ | 索引后建 + 逐版本步骤 + 失败回滚;用真实 v1 库验升级/保真/幂等 |
| 研究轮次不能靠 FINAL 认定 | ✅ | `_round_advanced()`(决定性尝试/收口/放弃才算);`run.marker` 记录"FINAL 但不计轮" |
| 双输出新旧规则冲突 | ✅ | 删除 `research.dual_output_blockers` 目标级规则;`test_dual_output_is_enforced_per_attempt_only` |
| 恢复/幂等 | ✅ | 死 pid 立即回收、活进程绝不回收、跨主机保守、`call_key` 幂等;新增 `tests/test_recovery_idempotency.py` 2 条 0.41s ✓(删检查点+step 回退模拟"副作用已生效但未落盘" ⇒ 恢复不重复执行;running 工具状态如实可见) |
| 权限 vs 沙箱 | ✅(诚实标注) | `Authz` 增加 `isolation`(当前 `none`)与 `describe()`;`fw-core authz` 输出口径;`test_authz_reports_isolation_none_by_default` + `test_authz_root_check_does_not_sandbox_process` 证明"路径检查 ≠ 沙箱"(进程仍可访问 root 之外) |
| Provider 可信度分级 | ✅ | `Probe.evidence`(discovered/configured/reachable/verified);`tests/test_providers.py` |
| 研究评测(除 Pi 线对比) | ✅ | `eval/scoring.py`:对照组(安全实现)抓**假阳性**、`vulnerable` 结论必须有**挂在自己名下**的证据;新增 `eval/tasks/safe-control/`(先验签+全量 MAC+深度上限的安全实现);`tests/test_eval_scoring.py` 3 条 0.48s ✓;诱饵任务 `decoy-comment`(注释与实现相反)已加,score 4 条 0.81s ✓;待:与 Pi 线对比 |

## 第三阶段:主线(按依赖)

| 项 | 状态 | 下一步 |
|---|---|---|
| **Gate 迁移到权威状态**(除 Pi 侧调用) | ✅ | `fwcore/gates_db.py`:覆盖度/收尾/三镜/材料 + **KB 条数 / 附件引用完整性 / legacy 对照**;CLI `fw-core gates <target> [--legacy]`;`tests/test_gates_db.py` 6 条 0.81s ✓;第三步完成:证据**版本一致性**与"结论必须挂自己证据"已进 gate(`evidence_version_mismatch` / unlinked 阻塞),`tests/test_gates_db.py` 9 条 1.24s ✓(含用**真实** `tools/derived_check.sh` 的 legacy 集成);下一步(需授权):Pi 侧调用同一 gate |
| **最终验证命令(可外部重跑)** | ✅ | `bash tools/fwcore_verify.sh`(cwd=仓库根;`PYTHONPATH` 由脚本设;临时目录 `.tmp/verify`,跑完自删);覆盖 8 个测试文件 + capabilities + gates 三项 |
| Pi hook 接线 | ⏸ 待授权 | 需在**研究会话**里启用扩展(会改变 live harness 行为)⇒ 需用户明确同意后再做;桥本体已可用 | 
| Pi 兼容适配器(桥本体) | ✅ | `fwcore/pi_bridge.py` + `fw-core pi-event`(stdin 事件流,幂等,坏事件不写);`tests/test_pi_bridge.py` 4 条 ✓;待:`.pi/extensions/` 里真正的 hook 接线 |
| 独立 MCP 客户端 | ✅ | `fwcore/mcp_client.py`(initialize/tools.list/tools.call;select 等超时;关闭时 killpg)+ `fw-core mcp list|probe|call`;`tests/test_mcp_client.py` 6 条 1.06s ✓(含"server 起不来必须 unreachable") |
| 结构化研究工具 | ✅ | `fwcore/toolkit.py` 加 `OBSERVERS`(derived/queue/signal/fuzz/emu → 字段;解析不出标 `unparsed`)+ `tool_calls` 记录 provider/capability/arch/env;`tests/test_toolkit_structured.py` 4 条 0.53s ✓;IDA `ida_query`、Ghidra `ghidra_query`(能力缺失如实报错)已完成,测试 8 条 1.03s ✓ |
| 研究评测加难 | ✅ | 三个任务:`safe-control`(安全实现测假阳性)、`decoy-comment`(注释与实现相反)、`decoy-log`(过期日志);`eval/scoring.py` 要求 vulnerable 结论挂**自己名下**证据;`tests/test_eval_scoring.py` 5 条 1.30s ✓ |

## 纪律

- 不用"资源不足"当研究结论;不用"没有预算"取消执行保护。
- 每步只跑相关测试(`make test-one T=...`),不跑全量;临时目录只在工作区内。

## Pi Core 对齐(新增架构任务)

| 项 | 状态 | 证据 / 下一步 |
|---|---|---|
| 读真实 Pi 源码(记 SHA) | ✅ | `earendil-works/pi @ d57c2d2`;已取 `agent-loop.ts`(959) / `agent.ts`(623) / `types.ts`(551) / `agent-session.ts`(4532) 到 `.tmp/pi-ref/`(gitignored) |
| Watchdog 复核(跨线程/自动启动/错误空转) | ✅ | 独立连接 + 退避 + 连续失败停止 + **Runner 自动启停**;`tests/test_watchdog.py` 8 passed 1.91s |
| 源码级对照表(8 项) | ✅ | `docs/PI-CORE-ALIGNMENT.md`(基线 `d57c2d2`;每项含 Pi 依据/fwcore 现状/差距/是否改/反例) |
| 取消语义(对齐 AbortSignal) | ✅ | `Runner.cancel()` + 安全点检查;`tests/test_cancel.py` 2 passed 0.38s |
| 按差距逐项实施 | ✅ | ①取消语义 ✅(`Runner.cancel()`;`tests/test_cancel.py` 2 passed 0.38s);②工具参数截断显式失败 ✅(`_exec_tool_call`;`tests/test_tool_args_invalid.py` 1 passed 0.19s);③轮前准备钩子 ✅(`prepare`/`prepare_timeout`;`tests/test_prepare_hook.py` 4 passed 2.13s);④结构化进度快照 ✅(`Runner.progress`/`fw-core progress`;`tests/test_prepare_hook.py` 5 passed 2.27s);⑤只读工具并行:暂不采纳(固件工具多为独占资源,理由已记录) |

## MCP / 执行可靠性 / 授权 / 评测(按用户优先级)

| 项 | 状态 | 证据 / 下一步 |
|---|---|---|
| **MCP 接入 Agent Runtime**(注册→模型选择→执行→记录→清理) | ✅(两次修正后) | `fwcore/mcp_runtime.py`:`MCPBridge.start()` 只注册**可连且 tools/list 成功**的服务器(不可达/未启用如实记状态);`mcp_<server>_<tool>` 名映射;`inputSchema` 直通;`isError`→error、断连→error、per-call 超时→timeout(修了 `call_tool` 忽略 per-call timeout 的 bug);provider 记 `mcp:<server>`。**修正①**:CLI `run/resume/advance` 之前没传 `mcp=True` ⇒ 正式路径根本没接,现已默认接(`--no-mcp` 关);**修正②**:`_finish_state()` 每轮关桥 ⇒ 第 2 轮起 MCP 全废,现改为桥归 `Runner` 所有(`close()` 幂等、`_ensure_mcp()` 跨轮自愈、`mcp.reopened` 留痕);**证据**:`tests/test_mcp_lifecycle.py` 6 passed —— **真实 CLI 子进程**跑 `fw-core run` 断言 `tool_calls.provider=mcp:fake`+`ok`+退出无残留进程、`--no-mcp` 对照失败留痕、`advance()` 两轮均 ok、异常退出桥关/看护停/run=paused、close 幂等 |
| verify 脚本文件数不实/漏新测试 | ✅ | 改为**自动发现** `tests/test_*.py` + 真实计数:`pytest(23 个文件) 174 passed in 46.55s` |
| **`advance()` 跨轮被自己写成"外部停止"** | ✅(真 bug) | 之前 `turn()` 结束时置 `state='finished'`,下一轮 `_external_stop()` 误判 ⇒ **自动继续根本不工作**;修法:turn 开始自标 `running`(外部 cancel 仍靠 `handback=user_stop` 生效);`test_advance_stops_after_idle_rounds` 覆盖 |
| Watchdog 正式路径默认启用 + 旧 PGID 误杀 | ✅ | CLI `run/resume/advance` 默认开看护(`--no-watchdog` 可关,help 可发现);`.pgids` 记录 `pgid + starttime`,杀组前核对 `/proc/<pid>/stat` 的 starttime ⇒ **pid 复用不误杀**;`Watchdog.health()` 暴露 scans/errors/stopped_reason/alive,`Runner.progress()` 带出;`tests/test_watchdog.py` +2 条、`test_cli_smoke.py` +1 条 |
| prepare hook 超时后的线程泄漏 | ✅ | 弃用 `ThreadPoolExecutor`(退出会 `shutdown(wait=True)` ⇒ 被卡死钩子拖住 ✗),改 **daemon 线程 + join(timeout)**:超时即放弃、迟到结果不采纳、`prepare_abandoned` 可观测;`test_prepare_timeout_does_not_wait_for_hung_hook` 断言"5s 钩子不拖住主流程(<3s)" |
| 完整研究授权机制(目标/工作区/时限/撤销/审计) | ✅ | `fwcore/authz.py` + `fw-core authz grant|revoke|show`:`state/<target>/authz.json`(target/roots/expires_at/revoked/note/granted_by);`Runner.start()` 按目标自动加载并写 `authz.loaded` 事件;无授权 ⇒ 默认拒绝且错误里给出授权命令;范围外路径仍拒;`tests/test_authz.py` 5 passed(默认拒绝/grant 放行/过期/撤销/自动加载+审计);授权≠沙箱(`isolation=none` 如实标注) |
| 跨主机 / 跨重启失联判定 | ✅ | schema **v5** `runs.pid_starttime`(`start()` 记录);`recover_stale()`:同主机靠 pid 存活 + **starttime 比对**(pid 复用不再被当活着)、pid 死 ⇒ 立即收敛;跨主机默认不动、`include_remote` 才按 `older_than×factor` 兜底;长工具在飞时两边都保活(`_inflight_tool_grace`,与看护同一判据);收敛时写 `handback_reason=recovered_stale:<原因>`;新增 CLI `fw-core recover [--include-remote]`;`tests/test_cross_host_recovery.py` 6 passed(活跃不误伤 / 死 pid / pid 复用 / 跨主机默认不动 + 显式开启 / 新鲜心跳 / 长工具保活) |
| 长工具执行不被看护误杀 | ✅ | `watchdog._scan`:心跳过期时先查**在飞 `running` 工具调用**,在 `max(max_tool_seconds, heartbeat)+120s` 宽限内只续心跳 + 记 `watchdog.tool_inflight`,不暂停/不杀组;超宽限才照常暂停;`tests/test_long_tool_and_cancel.py` 两侧都测(保活 / 超宽限仍暂停) |
| 取消语义(在飞工具 + 副作用) | ✅ | `cancel()` 立刻写 `handback_reason=user_stop:*`;`shell_exec` 改**切片轮询**(0.2s)⇒ 取消即杀组、返回 `cancelled`(不是 error/timeout);`Registry.call` 再兜一层映射;`test_cancel_marks_inflight_tool_cancelled` 断言 DB 里是 `cancelled` 且 run=paused |
| 工具副作用幂等 | ✅ | 同一 `call_key` 重放返回上次结果(`replayed=True`)不重复副作用;修了 `run_id` 为空时幂等失效(临时调用);`test_same_call_key_does_not_repeat_side_effect` 断言文件只写一行 |
| 连接跨线程 | ✅ | `db.connect(check_same_thread=False)` + 写路径 `BEGIN IMMEDIATE`/busy_timeout 串行化(取消/看护/长工具跨线程读同一连接) |
| 文本类工具(MCP/IDA/Ghidra)观测与原始产物 | ✅ | schema **v6** `tool_calls.result_path/result_sha256/observation`;`Registry.call` 把工具返回的 `text` 落盘(`<tool_call_id>.result.txt`)+ 算 sha、把 `observation` 存 JSON;`add_evidence(tool_call_id=...)` 未给 path/capture 时**自动锚定该次调用的原始产物**(路径相对 root、哈希一致);`_sha256` 读失败返回空串而不是抛错(哈希失败 ≠ 工具失败);`tests/test_evidence_provenance.py` 8 passed(文本结果落盘+哈希+观测 / 证据自动引用产物) |
| 结构化观测 → 统一证据记录(来源/哈希/工具状态/版本/关联实验) | ✅ | schema **v4**:`evidence.source / tool_call_id / verified / notes`(迁移动 `_step4`,索引在 ALTER 后建,v3 库升级后旧证据保持 `verified=1` 不凭空降级);`Domain.add_evidence(..., source, tool_call_id)` 从 `tool_calls` 继承 provider/capability 作来源、失败调用直接拒绝;文件类证据自动 sha256 + 绑定目标版本;**工具输出不得自动升级**:带 `tool_call_id` 的证据一律 `verified=0`,必须 `promote_evidence()` 写复核说明;`conclude(vulnerable)` 与 `gates_db` 都要求 ≥1 条 `verified=1` 证据(否则阻塞收尾);CLI `evidence add --source/--tool-call/--notes` + `evidence promote`;`tests/test_evidence_provenance.py` 6 passed(来源/哈希/版本/失败调用拒绝/未复核拦截/升级后放行/迁移保真) |
| 未知工具调用留痕(审计) | ✅ | 模型幻觉/`--no-mcp` 时的调用不再静默丢弃:`_exec_tool_call` 记 `status=error` + `note`,可审计;`test_cli_no_mcp_control_fails_the_call` 断言到行 |
| scripted 工具调用 id 唯一 | ✅ | `ScriptedAdapter` 之前每轮都从 `call_0` 开始 ⇒ `call_key` 幂等把第 2 轮当成重复而吞掉(测试假象也会掩盖真问题);改全局自增 |
| 本地 `state/mcp.json` 为权威 | ✅ | 有本地配置就**不再合并**用户 `~/.pi/agent/mcp.json`(合并会把无关服务器拉进来、拖慢启动,测试里实测撞到重名) |
| 评测覆盖(发现/负对照/误报/证据因果/范围/工具失败/可复现) | ✅ | `eval/scoring.py` 新增三条判据:`no_conclusion_from_failed_tools`(尝试全是环境失败/无信号/取消 ⇒ 不可能支撑结论)、`evidence_verified_reproducible`(vulnerable 结论必须有 **verified=1 + path + sha256** 的证据)、`scope_present`(已结论的假设必须写适用范围);原有:过概括检测、因果关联、obs_outcome、对照假阳性;`tests/test_eval_scoring.py` 7 passed(新增"只有 capture 不算可复现"与"环境失败不得支撑结论"两条反例) |
| 评测隔离(`eval/run.py` 从答案读 goal) | ✅ | 5 个任务都拆成 `task.json`(**公开 goal**)与 `answers/expected.json`(**私有判据**,已移除 goal);`load_task()` 两文件分离且**拒绝**私有侧带 goal;`prepare_workspace()` 只拷 `bundle/`(工作区无 answers/、无 task.json);`tests/test_eval_isolation.py` **13 passed**(含"goal 不得泄漏答案文本") |

## 阻塞 / 推迟(需授权或外部条件)

| 项 | 状态 | 阻塞原因 | 解锁条件 |
|---|---|---|---|
| Pi hook 接线(在 `.pi/extensions/` 启用桥) | ⏸ 待授权 | 会改变**研究会话**的 live 行为(每条工具事件都写状态) | 用户明确同意后启用;桥本体(`fw-core pi-event` + 幂等)已可用 ✓ |
| 与 Pi 研究线对比(同一目标双跑) | ⏸ 待外部条件 | 需要真实的 Pi 研究会话产出与算力/时间窗口 | 用户指定一个目标并允许跑双线;评分器 `eval/scoring.py` 已可复用 ✓ |

> 这两项**不是**"已完成",也不因本轮迭代结束而消失;它们挡住的是 ROADMAP 的"全部验收完成"。
