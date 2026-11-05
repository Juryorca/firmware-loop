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
| 研究评测 | 🟡 对照组 + 因果评分已加 | `eval/scoring.py`:对照组(安全实现)抓**假阳性**、`vulnerable` 结论必须有**挂在自己名下**的证据;新增 `eval/tasks/safe-control/`(先验签+全量 MAC+深度上限的安全实现);`tests/test_eval_scoring.py` 3 条 0.48s ✓;诱饵任务 `decoy-comment`(注释与实现相反)已加,score 4 条 0.81s ✓;待:与 Pi 线对比 |

## 第三阶段:主线(按依赖)

| 项 | 状态 | 下一步 |
|---|---|---|
| **Gate 迁移到权威状态** | 🟡 第二步完成 | `fwcore/gates_db.py`:覆盖度/收尾/三镜/材料 + **KB 条数 / 附件引用完整性 / legacy 对照**;CLI `fw-core gates <target> [--legacy]`;`tests/test_gates_db.py` 6 条 0.81s ✓;第三步完成:证据**版本一致性**与"结论必须挂自己证据"已进 gate(`evidence_version_mismatch` / unlinked 阻塞),`tests/test_gates_db.py` 8 条 1.24s ✓;下一步(需授权):Pi 侧调用同一 gate |
| **最终验证命令(可外部重跑)** | ✅ | `bash tools/fwcore_verify.sh`(cwd=仓库根;`PYTHONPATH` 由脚本设;临时目录 `.tmp/verify`,跑完自删);覆盖 8 个测试文件 + capabilities + gates 三项 |
| Pi hook 接线 | ⏸ 待授权 | 需在**研究会话**里启用扩展(会改变 live harness 行为)⇒ 需用户明确同意后再做;桥本体已可用 | 
| Pi 兼容适配器 | 🟡 桥已实现 | `fwcore/pi_bridge.py` + `fw-core pi-event`(stdin 事件流,幂等,坏事件不写);`tests/test_pi_bridge.py` 4 条 ✓;待:`.pi/extensions/` 里真正的 hook 接线 |
| 独立 MCP 客户端 | ✅ | `fwcore/mcp_client.py`(initialize/tools.list/tools.call;select 等超时;关闭时 killpg)+ `fw-core mcp list|probe|call`;`tests/test_mcp_client.py` 6 条 1.06s ✓(含"server 起不来必须 unreachable") |
| 结构化研究工具 | 🟡 仓库工具 + IDA + Ghidra 已结构化 | `fwcore/toolkit.py` 加 `OBSERVERS`(derived/queue/signal/fuzz/emu → 字段;解析不出标 `unparsed`)+ `tool_calls` 记录 provider/capability/arch/env;`tests/test_toolkit_structured.py` 4 条 0.53s ✓;IDA `ida_query`、Ghidra `ghidra_query`(能力缺失如实报错)已完成,测试 8 条 1.03s ✓ |
| 研究评测加难 | ✅ | 三个任务:`safe-control`(安全实现测假阳性)、`decoy-comment`(注释与实现相反)、`decoy-log`(过期日志);`eval/scoring.py` 要求 vulnerable 结论挂**自己名下**证据;`tests/test_eval_scoring.py` 5 条 1.30s ✓ |

## 纪律

- 不用"资源不足"当研究结论;不用"没有预算"取消执行保护。
- 每步只跑相关测试(`make test-one T=...`),不跑全量;临时目录只在工作区内。

## Pi Core 对齐(新增架构任务)

| 项 | 状态 | 证据 / 下一步 |
|---|---|---|
| 读真实 Pi 源码(记 SHA) | ✅ | `earendil-works/pi @ d57c2d2`;已取 `agent-loop.ts`(959) / `agent.ts`(623) / `types.ts`(551) / `agent-session.ts`(4532) 到 `.tmp/pi-ref/`(gitignored) |
| Watchdog 复核(跨线程/自动启动/错误空转) | ✅ | 独立连接 + 退避 + 连续失败停止;`tests/test_watchdog.py` 6 passed 1.63s |
| 源码级对照表(8 项:循环/事件/流式/取消/Hook/上下文恢复/并发与副作用/资源边界) | 🟡 下一步 | 产出 `docs/PI-CORE-ALIGNMENT.md`(含"是否值得改 + 反例测试") |
| 按差距逐项实施 | ⬜ | 依赖对照表结论;一次一项,不做大重写 |

## 阻塞 / 推迟(需授权或外部条件)

| 项 | 状态 | 阻塞原因 | 解锁条件 |
|---|---|---|---|
| Pi hook 接线(在 `.pi/extensions/` 启用桥) | ⏸ 待授权 | 会改变**研究会话**的 live 行为(每条工具事件都写状态) | 用户明确同意后启用;桥本体(`fw-core pi-event` + 幂等)已可用 ✓ |
| 与 Pi 研究线对比(同一目标双跑) | ⏸ 待外部条件 | 需要真实的 Pi 研究会话产出与算力/时间窗口 | 用户指定一个目标并允许跑双线;评分器 `eval/scoring.py` 已可复用 ✓ |

> 这两项**不是**"已完成",也不因本轮迭代结束而消失;它们挡住的是 ROADMAP 的"全部验收完成"。
