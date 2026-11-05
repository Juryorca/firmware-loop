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
| 看护优先级:硬保护不可被长工具宽限绕过 | ✅ | `watchdog._scan()` 重构:先算 `max_db_bytes` / `checkpoints` / `owner_dead`(pid 死或 starttime 复用)三个硬项 ⇒ 直接暂停 + 杀组,`continue` 之后才谈"停滞宽限";长工具只豁免"心跳停滞",不豁免磁盘/检查点;主进程死时独立看护也能收敛;新增 `fw-core watchdog [--once]`(独立进程,不与主运行同生共死);`tests/test_watchdog_priority.py` 4 passed(在飞工具+DB 超限 / +检查点超限 / 无超限仍保活 / 独立进程收敛死 run) |
| 跨主机 / 跨重启失联判定 | ✅ | schema **v5** `runs.pid_starttime`(`start()` 记录);`recover_stale()`:同主机靠 pid 存活 + **starttime 比对**(pid 复用不再被当活着)、pid 死 ⇒ 立即收敛;跨主机默认不动、`include_remote` 才按 `older_than×factor` 兜底;长工具在飞时两边都保活(`_inflight_tool_grace`,与看护同一判据);收敛时写 `handback_reason=recovered_stale:<原因>`;新增 CLI `fw-core recover [--include-remote]`;`tests/test_cross_host_recovery.py` 6 passed(活跃不误伤 / 死 pid / pid 复用 / 跨主机默认不动 + 显式开启 / 新鲜心跳 / 长工具保活) |
| 端到端链路:MCP → Runner → 产物 → Evidence → 复核 → Conclusion | ✅(等级:模拟模型 + 模拟 MCP) | `tests/test_e2e_mcp_evidence.py` **2 passed**:模型替身必须解析 `tool_call_id/hypothesis_id/attempt_id` 才能推进(不是关键词脚本);断言 MCP 调用 `ok`+`provider=mcp:fake`+`result_path/sha256`、证据绑定同一次调用并指向同一产物、`verified=1` 来自 `evidence_checks(method=recheck-sha256)`、`gates.findings[0].verified>=1` 且无"未复核"阻塞;反例:不复核直接下结论被 domain 拒 |
| 新工具:`state_verify_evidence` | ✅ | agent **自主**复核(不需要人工确认):recheck-sha256 / reproduce-attempt / cross-check,结果入库可审计 |
| 真实模型协议验证(run / resume / advance) | ✅(等级:真实模型,scripted 之外的唯一一档) | `scripts/e2e_real_model.py` 增加 **phase3 = advance**;实跑 DeepSeek `deepseek-flash`:`[phase1] paused → [phase2] finished → [phase3/advance] rounds=1 steps=12`,`tool_calls=15 hypotheses=1 attempts=2 observations=3 api_ok=True → PASS`;记录 `docs/evidence/real-model-run.md` |
| 验收等级必须分清 | ✅(口径) | ①scripted 单测(机制)②模拟 MCP(链路+授权)③真实模型(协议:run/resume/advance)④真实逆向工具(IDA/Ghidra,受 capability 探测)**⑤真实固件研究质量** —— 前 3 档本轮已跑;④未跑(需按需起 IDA/ghidra,且不在本轮任务内);⑤**未跑**,不能用 ①~③ 的成绩代替 |
| **统一工具注册契约**(manifest + 适配器 + 生命周期) | ✅ 第一阶段 | 新增 `fwcore/toolreg.py`:`Manifest`(身份/描述/schema/kind/argv/timeout/risk/external/requires/capability/provider/observer/requires_file/capability_required,未知字段直接拒)+ 三种适配器(`python` = 模块:函数(白名单前缀)、`script` = argv 模板 + 参数拆分、`feature` = 深度适配)+ `run_process`(独立进程组 / 取消即杀组 / 产物落盘 + sha256 / timeout 语义)+ `capability_index`(manifest 是"能力→工具"唯一来源,`providers.inventory` 只读)+ `register_manifests` 返回 `registered/unavailable/skipped` 报告。**新增已有执行类型的工具 = 一个 JSON**(`tests/test_toolreg.py` 用临时 registry 验证:不加任何核心代码即可注册/调用新工具) |
| 注册契约审查(argv/进程/合并/实例/选择) | ✅(本轮修复) | **①argv**:`build_argv` 不再在 split 展开后追加原 `{args}`;混合形式(`--x={args}`)其余 token 排在 part 后;`research_signal.log` 改为不拆(含空格路径整体传);真实 manifest 的 5 组参数化测试 + 混合/空值。**②进程**:`run_process` 补 PGID+starttime 登记(`state/artifacts/<run>/.pgids`,与 `shell_exec` 同格式)、`BaseException` 杀组再抛、输出上限(8MiB 截尾)⇒ 独立看护可清理在飞脚本工具(`test_watchdog_reaps_inflight_script_tool`)。**③合并**:同名 manifest **工作区优先**、不重复注册、报告 `overridden`。**④实例**:`ToolSpec.adapter_instance`;`close_feature_adapters` 只关**真实实例**且幂等(不再造新实例);IDA 默认不停共享 supervisor(`FW_IDA_STOP_ON_CLOSE=1` 才停)。**⑤选择**:`select_tool()`(可用性过滤 + priority 排序 + 候选清单)、`providers.inventory.selection`(索引 ≠ 已选择) |
| **Windows/IDA 接入配置化**(单一来源) | ✅ | 新增 `fwcore/winenv.py`:`IDA_WIN_DIR/MCP_EXE/WIN_STAGE/WIN_PYTHON/STAGE/URL/LOG/START_TIMEOUT/PROBE_TIMEOUT/STOP_ON_CLOSE/WIN_INSTANCES` 集中定义,优先级 **环境变量 `FW_IDA_*`**(含旧 `IDA_MCP_URL`)> `state/ida.json`(`FW_IDA_CONFIG` 可改)> 兼容默认值(可完全覆盖);双向路径转换用同一张盘符表(`/proc` 无关),支持任意盘符 + 含空格路径 + UNC 原样上报;`fw-core ida-env [--sh]` 导出给 shell;`providers.windows_ida_provider` 与 `adapters_ida` 都读同一份配置;**配置错误报"缺哪个 exe/目录/挂载点"**(不再伪装成 IDA 分析失败)。`tests/test_ida_config.py` 8 passed |
| IDA shell 脚本去硬编码 | ✅ | `tools/ida_env.sh`(公共前置,`fw-core ida-env --sh` 驱动,缺配置明确报错)、`ida_mcp_lib.sh`(MCP 会话/调用);`ida_start/open/call/list/stop/clean/tools` 全部改用配置变量;JSON-RPC 请求改用 python 组包(Windows 反斜杠/引号不再被手拼转义破坏);`tests/test_ida_config.py::test_scripts_have_no_hardcoded_paths_or_ports` 断言脚本内无 `/mnt/d/`、`Users/30104`、`python313`、`:8745` |
| IDA 收尾安全(共享 Supervisor 不被误停) | ✅ | 默认只停 **pidfile 记录的** supervisor;worker 注册只处理 `idb_path/input_path` 落在**本暂存目录**下的条目;`--all`(按镜像名)与 `--purge`(删 IDB)必须显式;`IdAdapter.close()` 默认不停(配置 `FW_IDA_STOP_ON_CLOSE=1` 才停),`Runner.close()` 已接通 `close_feature_adapters()`(幂等);测试覆盖 "本环境被杀 / 其它实例注册不动" |
| IDA 真实链路验收(配置化之后) | ✅ | `ida_start.sh`(从 URL 解析 host/port、pidfile、日志进工作区)→ `supervisor ready (127.0.0.1:8745, pid=…)`;`ida_open.sh /bin/true` → session 建立(`input_path=D:\ida_test\true`);`lookup_funcs` → `main@0x2710 size=0xd4`;`ida_stop.sh`(默认模式)→ `stopped supervisor pid=…`,`http=000`,**暂存区 IDB 未删**(`true.id0..` 保留) |
| 按 manifest 扩展研究能力(不改核心) | ✅ | 新增 `tools/fw_extract.sh` + `tools/registry/fw_extract.json`(binwalk -Me + unblob 双提取、摘要输出、`capability=extract.binwalk`、risk=high、timeout=900)+ `toolkit.OBSERVERS["fw_extract"]`(摘要→`files/dirs`,解析不出标 unparsed);**没有改 Agent Loop/中央清单**;真实运行(gzip 样本):`unblob` 产出 + `files=1` 摘要;`tests/test_fw_extract_tool.py` 5 passed(manifest/argv/执行+观测/能力索引+选择/仓库注册) |
| 研究工具从硬编码迁到 manifest | ✅ | `fwcore/toolkit.py::register_repo_tools` 不再逐个 lambda 注册,改为 `register_manifests`;7 个 manifest 落在 `tools/registry/*.json`(research_derived/queue/signal、fuzz_cgi、emu_run、ghidra_query、ida_query),`Registry.registration` 暴露注册报告;缺入口脚本的工具**不注册并给出原因**(ghidra_query 目前 = 缺 `tools/ghidra_run.sh`);`tests/test_toolreg.py` 9 passed + `test_toolkit_structured.py` 8 passed 无退化 |
| IDA 特色适配保留并修缺陷 | ✅ | `fwcore/adapters_ida.py::IdAdapter(Adapter)`:ensure = 幂等启动(`tools/ida_start.sh`)+ **检查启动结果** + HTTP 探测 supervisor(`:8745`,旧实现**不检查启动返回**就继续调用 ⇒ 错误归因错);call = `tools/ida_call.sh`;health/close 可查(默认不停共享 supervisor,`FW_IDA_STOP_ON_CLOSE=1` 才停);Agent Loop 只看到 `ida_query` 这个普通工具,Windows/端口/暂存细节留在适配器内 |
| 真实工具验收:IDA(Windows 自动启动链路) | ✅ 第三阶段 | 走**统一 Registry 的 `ida_query`**(不是 shell):`tools/ida_start.sh` 自动拉起 Windows `idalib-mcp`(`supervisor ready (8745)`)、`ida_open.sh` 开库(`true`,`auto_analysis_ready/hexrays_ready=true`)、真实分析调用(`lookup_funcs` → `main@0x2710 size=0xd4`);Registry 记录 `provider=ida:supervisor`/`capability=debug.ida`/`status=ok`,观测解包 JSON-RPC 信封(`fields=[status,uptime_sec]`);收尾 `ida_stop.sh` 后 `http=000`、无 `idalib-mcp` 进程 |
| 真实工具验收:Ghidra(能力缺失如实报) | ✅(如实) | 本机 `analyzeHeadless` 不存在 ⇒ `debug.ghidra=missing`,manifest `requires_file=tools/ghidra_run.sh` 不存在 ⇒ **工具不注册**(不伪造 ready),`providers.inventory` 里 `debug.ghidra.tools` 为空;`tests/test_toolkit_structured.py` 断言能力缺失时 `ghidra_query` 如实报错 |
| e2e 报告与实际一致(不重复付费调用) | ✅ | `scripts/e2e_real_model.py` 重构:`render()` 只从 **SQLite 事实**生成报告(阶段表/工具调用/失败明细/事件时间线/证据与复核记录/结局),`--from-run DIR` 用**已有运行目录**重新渲染(本轮用它更新 `docs/evidence/real-model-run.md`,**零模型调用**);`--dry-run` 用 scripted 跑同一条 main 路径做回归;`runs.model`(schema v8)记录跑的哪个模型 |
| 验收等级口径(写死在 ROADMAP) | ✅ | ①scripted 单测 ②模拟 MCP ③**真实模型协议**(已跑)④**真实逆向工具**(IDA 已跑/Ghidra 缺失如实)⑤**真实固件研究质量**(未跑,不能用前 4 档代替) |
| 跨进程 resume(新进程接着跑) | ✅ 第二阶段 | `Runner._load_authorization()` 抽成入口级:`start/resume/advance(run_id=…)` 都调用 ⇒ **新进程**恢复授权 + MCP(此前只有 `start()` 加载 ⇒ `resume` 在新进程里以为"没授权",MCP 不启动/高危工具被拒 = 恢复不完整);`resume` 写 `run.resumed` 事件带 mcp 状态;`tests/test_cross_process_resume.py` **2 passed**(真实子进程:A 跑 MCP→暂停退出,B 同 run_id 恢复 ⇒ MCP 重新发现、新调用 ok、**同 call_key 不重复副作用**(服务器计数不变);A 在 `sleep 30` 中被 SIGKILL ⇒ `fw-core watchdog --once` 收敛并写 `watchdog_paused: owner_dead` ⇒ B 恢复继续) |
| 证据证明力分级(哈希 ≠ 漏洞验证) | ✅ | `Domain.INTEGRITY_METHODS/CAUSAL_METHODS`:`recheck-sha256` = **产物完整性**;`cross-check`/`reproduce-attempt` = 因果;`conclude(vulnerable)` 只有完整性复核 ⇒ 拒绝并说明该用哪种;`conclusion_support(hid)` 给 integrity/causal 摘要;`gates_db` 对"只有完整性证据的 vulnerable"同样报阻塞(导入/历史数据也抓得到);`tests/test_evidence_verification.py` 9 passed |
| 证据复核语义(可检查,不是自称) | ✅ | schema **v7**:新表 `evidence_checks`(method/artifact/sha256/attempt/detail) + 索引;`evidence.verified` 只由 check 记录派生;`add_evidence` **一律** verified=0(手工证据也一样);`verify_evidence(ev, method, ...)` 三种可核方法:`recheck-sha256`(重算产物哈希,变了就拒)、`reproduce-attempt`(必须关联 **supported** 且同假设的尝试)、`cross-check`(必须有可检查产物 + ≥12 字比对说明);`promote_evidence` 保留为 cross-check 兼容,捕获式证据(无文件)直接拒;`evidence_status()` + `evidence verify/status` CLI 可审计;`tests/test_evidence_verification.py` 7 passed(篡改产物被拒 / 文字声明无效 / refuted 不能当复现 / CLI 真路径) |
| 历史 `verified=1` 不得推断为已复现 | ✅ | `_step7` 迁移把存量 `verified` 全部清零,并在 `notes` 写入 `legacy: 迁移前的 verified 无检查记录,不算已验证`;`tests/test_evidence_provenance.py::test_migration_from_v3_preserves_existing_rows` 断言"旧事实保留 + 标志清零 + 说明存在" |
| MCP 启动的授权边界(启动前必须授权) | ✅ | `Runner.__init__` **不再启动** MCP(启动有副作用:子进程/外网/登录态);`start()` 在按目标加载授权**之后**才 `_ensure_mcp()`;`_mcp_permitted()` 要求 授权存在 + `active()` + `allow_mcp`;不满足 ⇒ 不启动 + 记 `mcp.skipped`(原因含授权命令),已启动的桥会被关掉;`progress()["mcp_skipped"]` 可观测;`tests/test_mcp_authz_boundary.py` 覆盖 无授权/过期/撤销/`allow_mcp=false` 且断言**服务器进程没被 spawn**(pidfile 不存在) |
| MCP external 判定不靠名字启发式 | ✅ | `ServerSpec.external`/`authz.allow_external_mcp` 才是判定依据:配置声明 `external:false` ⇒ 授权内自主执行;未声明 ⇒ 默认 external ⇒ 需 `confirm`(逐次)或授权显式 `allow_external_mcp=true`(窗口内自主,仍留审计);关键词匹配只写进 `status.external_hint` 作审计,**不参与**授权;`ToolSpec.requires="mcp"` 让 `Policy.decide` 有一道可核的授权门 |
| MCP 工具名碰撞处理 | ✅ | `tool_name()` 规范化后撞名时用 `disambiguated_name()`(sha256 后缀,确定性、跨轮稳定);消歧后仍与既有工具同名 ⇒ 该工具**明确拒绝**并记 `status.name_collisions`,绝不静默绑到别的服务器工具;同一 `(server,tool)` 重开桥幂等 |
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
