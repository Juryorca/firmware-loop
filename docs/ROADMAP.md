# ROADMAP(fwcore)

> 唯一工程清单。规则见 `.agents/skills/fwcore-development/SKILL.md`。
> 数字来自 SQLite/脚本,不手写估计;已完成项只留一行 + 证据指针。

## 当前状态(产品事实)

- **独立产品**:fwcore 不依赖 Pi(`.pi/` 不入库);模型调用/工具/状态/证据/Watchdog/checkpoint/resume 全在自己代码里。
- **权威状态**:SQLite(schema v17)存假设/尝试/观察/证据/结论/面/run/checkpoint/tool_calls/events;
  `notes.md` 是可选历史/人工摘要，不承担研究状态；对外材料在 `report/`，原件在 `intel/`。
- **模型协议**:`finish_reason`/`usage` 贯通;每次调用 `model.call` 记账;FINAL 任意行首;重复内容与无工具空转看护;
  thinking 模式默认回传 `reasoning_content`。
- **工具**:统一 manifest(`tools/registry/*.json`,8 个)+ 内置 `state_*`/`shell_exec`/`fs_*` + IDA feature 适配器 + MCP;
  结果是 Pi 风格有界输出(2000 行/50 KiB 尾部);截断时带 `full_output_path`,并保留 `db_tool_call_id`/产物路径/哈希。
- **上下文**:静态 system + append-only history + 尾部状态(含 `[Context status]`);阈值压缩(90%@1M 窗口),
  分块摘要**覆盖全部被丢弃历史**,失败保留原历史 + 冷却;摘要调用计入预算。
- **研究质量门**:`vulnerable` 需 supported + 因果复核;`no_finding` 需 ≥1 条已复核证据;面处置需证据/理由;
  `assess` 区分 completed/partial 与来源强度。
- **生命周期/并发**:run 有**单 owner 租约**(CAS 接管;活主存在则拒绝并发 resume);
  心跳 CAS(不抢回所有权);接管死亡 owner 时清孤儿进程组与遗留 `running` 工具行;
  看护的 hard/stale 两分支都先赢下 `state='running'` 竞争再 kill/写事件。
- **存储边界**:研究事实只认 SQLite;运行产物只写 `state/_scratch/`(不进 Git);`docs/evidence/` 只收长期工程资料;
  研究事实、决策、下一步只认 SQLite；`notes.md` 可选；`report/` 是交付；覆盖度/队列判定 DB 优先。
- **全量迁移**:14 个历史目标的 notes/attack-surface 已导入 DB(`documents` 保原文;
  面/假设/观察/证据/复核/结论/三镜进结构化表;叙述与模糊内容进 `research_logs`);
  `fw-core migrate --all` 幂等,`--verify` 逐目标核对;运行路径**无 Markdown 回退**。
- **Agent 内容架构**:`fwcore/prompts/system.md` + 根 `AGENTS.md` 组成 system;LOOP 迁入 `research-loop` Skill;
  `.agents/skills/` 渐进式加载;Slash Commands 保留并由 fwapp 分发;SQLite `knowledge_entries` 保存 curated 研究知识；旧案例 Markdown 已移出当前树，只保留 Git 历史。
- **验证**:`bash tools/fwcore_verify.sh` = pytest(74 文件)+ capabilities + gates + 评测隔离 + ROADMAP 一致性 + 运行产物边界 + SQLite 状态库完整性 + git clean；历史迁移幂等由 pytest 覆盖，不再比较当前 notes/DB 同步。

## 待办

### P1 认证控制流:`op` 值 → 分发 → 权限检查 → 失败分支 ⛔ 仅缺授权

- **已有**:11 个 op 子处理器入口(0x196d8/0x197d8/0x19908/0x19a68/0x19b08 分发到 0x14d60…0x12d78)、
  门禁类型(口令/session 三重校验/Qtoken/verify_code)、`_notes/s3`+`all_calls.txt`、库内门禁原语哈希;
  **op 分发表**(qr_code 2/7/4;approve 1/7/4/3;verification_code 1/7/12/3/13;passwordless 8)、
  `strtol(param,NULL,10)` 无 endptr(缺省/非法 ⇒ 空响应),verification_code op=1 失败分支
  `error_code=1` 且 session/verify_code 缓冲区清零(无泄露),成功需 getpwnam+load_user_config+
  `Verify_Qtoken_DB_by_Client_ID`+两个配置位。
- **缺**:动态验证须由 agent 在受控 run 内完成(本机手工 `qemu-user` 报 SIGILL,不作为准绳);
  各 handler 门禁表的动态复核。
- **验收**:地址级链路 + ≥1 条带来源 observation;判不了写 inconclusive。
- **预算**:`R-20261105-3518ef1d` 已用 **130 calls / 571.9 s**;`runs.budget_note` 持久化生效边界
  (实测 resume 后 `via=resume`、limits=200 calls/1800 s);按此 ⇒ 余 ~70 calls / ~1228 s;
  不得新建 run 绕开预算。
- **恢复就绪**:租约/心跳/看护竞态已修,`resume` 在 drill 库副本实测:计数从 130 延续、状态 finished、
  `handback=awaiting_user_close`(修复前会被误判 owner_dead 暂停)。
- **阻塞**:续跑需用户批准付费模型(离线静态已完成并入库:观察 + 2 条 static-note 证据)。
- **批准后的下一步(命令已备好,勿新建 run)**:
  `python3 -m fwcore --root state/_scratch/realdrill-20261105-164147 resume R-20261105-3518ef1d --model openai`
  goal 用 run 里已存的那条(继续 op 子处理器,勿重做静态);只做:
  ①`op` 边界(缺失/非数字/负数/超大/重复参数)在四个分发器的实际响应;
  ②`verification_code op=1` 失败分支(无 qtoken / client_id 不匹配)的 `error_code` 与会话字段;
  ③把结果写成 observation/evidence(带来源),不要凭响应猜漏洞成立。

### P4 依赖库/样本边界(需 P1 运行结果)

- 目标:缺失库/动态入口导致无法判定时,给出明确边界而非猜测;边界写进 `scope_note` 并有证据。

## 已完成(一行 + 指针)

| 项 | 证据 |
|---|---|
| P2 工具结果→研究状态→下一 run 复用 | `tests/test_research_e2e_chain.py`、`test_cross_run_inheritance.py`、`test_tool_result_payload.py` |
| P3 证据强度分层 | `tests/test_provenance_strength.py`、`test_gate_provenance.py`;`gates.provenance{direct,weak,unlinked}` |
| P5 `passwordless op=8` 未认证信息暴露(vulnerable) | `H-20261106-33991ba5`(范围/弱来源已标注);按用户要求**未出 advisory** |
| P6 Token 用量审计 | `docs/evidence/token-audit.md`(两个真实 run + 分类 A/B/C/D) |
| P7 Runtime 架构对照 | `docs/ARCHITECTURE.md`(与 Pi 逐项对照 + 生命周期) |
| P8 上下文生命周期 + 上下文感知 | `compact.py`/`contextinfo.py`;`test_compaction*.py`、`test_context_awareness.py`;CLI `context` |
| Agent 内容架构重构 | `docs/AGENT_ARCHITECTURE.md`;`fwcore/prompts/system.md`;`fwcore/prompting.py`;`fwcore/skills.py`;SQLite `knowledge_entries`;`test_prompt_architecture.py`、`test_skills.py`、`test_knowledge.py` |
| 默认工具集与工具接入 | `fwcore/toolset.py`;`fwcore/adapters_ghidra.py`;`fwcore/adapters_qemu.py`;`tools/ghidra_run.sh`;`tools/registry/qemu_user.json`;`test_toolset.py` |
| P11 全量迁移 + 去运行时 legacy | `fwcore/migrate_all.py`;`docs/MIGRATION.md`;schema v14 `research_logs`;`tests/test_migrate_all.py`、`test_no_markdown_fallback.py`;`derived_check` 缺库即 FAIL |
| P10 存储与 Git 边界规范 | `eval/run.py`(默认不写仓库);`fwcore_verify`「运行产物边界」;`docs/ARCHITECTURE.md` §3;`tests/test_cli_context_scope.py`、`test_state_authority.py` |
| P9 Pi 真实基准 + 分层缓存对比 + 压缩验收纠偏 | `docs/ARCHITECTURE.md` §6/§7;`scripts/pi_cache_stats.py`;`test_compaction_sim.py` |

## 已取消(不属于产品)

Pi Hook / Pi 桥 / Pi 适配器 / Pi 护栏开关;Pi 侧调用 Gate、Pi 配置回退;Ralph、工具适配 Agent、额外编排平台。

## 已知限制(如实)

1. 真实 >500k 长历史的稳态缓存命中、真实多轮压缩后的 API 重建:未测(只有离线模拟)。
2. checkpoint 每步全量快照(写入放大);`checkpoints_keep` 只按 run 裁剪;无 append-only 迁移。
3. 摘要无增量更新;长历史压缩需多次摘要调用(计入预算)。
4. 单模型(无切换/自动重试);无 Web UI(只有 CLI)。
5. 真实证据多为 `command-write` 弱来源,报告必须标注。
