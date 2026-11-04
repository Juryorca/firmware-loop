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
| 恢复/幂等 | ⚠️ 部分 | 已:死 pid 立即回收、活进程绝不回收、跨主机保守、`call_key` 幂等;待:工具已副作用+未落检查点的显式用例 |
| 权限 vs 沙箱 | ⚠️ 部分 | `Authz` 已实现(范围/时限/可撤销、external 需确认);待:文档明确"cwd 检查 ≠ 沙箱",并给出隔离建议 |
| Provider 可信度分级 | ✅ | `Probe.evidence`(discovered/configured/reachable/verified);`tests/test_providers.py` |
| 研究评测 | 🟡 对照组 + 因果评分已加 | `eval/scoring.py`:对照组(安全实现)抓**假阳性**、`vulnerable` 结论必须有**挂在自己名下**的证据;新增 `eval/tasks/safe-control/`(先验签+全量 MAC+深度上限的安全实现);`tests/test_eval_scoring.py` 3 条 0.48s ✓;诱饵任务 `decoy-comment`(注释与实现相反)已加,score 4 条 0.81s ✓;待:与 Pi 线对比 |

## 第三阶段:主线(按依赖)

| 项 | 状态 | 下一步 |
|---|---|---|
| **Gate 迁移到权威状态** | 🟡 第二步完成 | `fwcore/gates_db.py`:覆盖度/收尾/三镜/材料 + **KB 条数 / 附件引用完整性 / legacy 对照**;CLI `fw-core gates <target> [--legacy]`;`tests/test_gates_db.py` 6 条 0.81s ✓;下一步:把 findings/证据强度也纳入禁写测试,并让 Pi 侧调用同一 gate |
| Pi 兼容适配器 | 🟡 桥已实现 | `fwcore/pi_bridge.py` + `fw-core pi-event`(stdin 事件流,幂等,坏事件不写);`tests/test_pi_bridge.py` 4 条 ✓;待:`.pi/extensions/` 里真正的 hook 接线 |
| 独立 MCP 客户端 | ✅ | `fwcore/mcp_client.py`(initialize/tools.list/tools.call;select 等超时;关闭时 killpg)+ `fw-core mcp list|probe|call`;`tests/test_mcp_client.py` 6 条 1.06s ✓(含"server 起不来必须 unreachable") |
| 结构化研究工具 | 🟡 仓库工具已结构化 | `fwcore/toolkit.py` 加 `OBSERVERS`(derived/queue/signal/fuzz/emu → 字段;解析不出标 `unparsed`)+ `tool_calls` 记录 provider/capability/arch/env;`tests/test_toolkit_structured.py` 4 条 0.53s ✓;待:IDA/Ghidra 的结构化封装 |
| 研究评测加难 | ⬜ | 加"无漏洞对照"任务(测假阳性)、诱饵注释、竞争解释;评分要能核对结论↔证据因果 |

## 纪律

- 不用"资源不足"当研究结论;不用"没有预算"取消执行保护。
- 每步只跑相关测试(`make test-one T=...`),不跑全量;临时目录只在工作区内。
