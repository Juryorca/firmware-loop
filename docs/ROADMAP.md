# ROADMAP(fwcore)

> 唯一工程清单。规则见 `.agents/skills/fwcore-development/SKILL.md`。
> 数字来自 SQLite/脚本,不手写估计;已完成项只留一行 + 证据指针。

## 当前状态(产品事实)

- **独立产品**:fwcore 不依赖 Pi(`.pi/` 不入库);模型调用/工具/状态/证据/Watchdog/checkpoint/resume 全在自己代码里。
- **权威状态**:SQLite(schema v13)存假设/尝试/观察/证据/结论/面/run/checkpoint/tool_calls/events;
  文件只放叙述(notes.md)、对外材料(report/)与原件(intel/)。
- **模型协议**:`finish_reason`/`usage` 贯通;每次调用 `model.call` 记账;FINAL 任意行首;重复内容与无工具空转看护;
  thinking 模式默认回传 `reasoning_content`。
- **工具**:统一 manifest(`tools/registry/*.json`,8 个)+ 内置 `state_*`/`shell_exec`/`fs_*` + IDA feature 适配器 + MCP;
  结果是 ≤8k 合法 JSON,带 `db_tool_call_id`/产物路径/哈希。
- **上下文**:静态 system + append-only history + 尾部状态(含 `[Context status]`);阈值压缩(90%@1M 窗口),
  分块摘要**覆盖全部被丢弃历史**,失败保留原历史 + 冷却;摘要调用计入预算。
- **研究质量门**:`vulnerable` 需 supported + 因果复核;`no_finding` 需 ≥1 条已复核证据;面处置需证据/理由;
  `assess` 区分 completed/partial 与来源强度。
- **验证**:`bash tools/fwcore_verify.sh` = pytest(57 文件)+ capabilities + gates + 评测隔离 + ROADMAP 一致性 + git clean。

## 待办

### P1 认证控制流:`op` 值 → 分发 → 权限检查 → 失败分支 ⛔ 仅缺授权

- **已有**:11 个 op 子处理器入口(0x196d8/0x197d8/0x19908/0x19a68/0x19b08 分发到 0x14d60…0x12d78)、
  门禁类型(口令/session 三重校验/Qtoken/verify_code)、`_notes/s3`+`all_calls.txt`、库内门禁原语哈希。
- **缺**:`op` 取值异常处理、各 handler 内权限检查点的完整数据流、失败分支可达性;无动态验证。
- **验收**:地址级链路 + ≥1 条带来源 observation;判不了写 inconclusive。
- **预算**:`R-20261105-3518ef1d` run 级累计上限 200 calls / 7200 s,耗尽即暂停。
- **阻塞**:需用户批准付费模型续跑(离线部分已完成)。

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
| P9 Pi 真实基准 + 分层缓存对比 + 压缩验收纠偏 | `docs/ARCHITECTURE.md` §6/§7;`scripts/pi_cache_stats.py`;`test_compaction_sim.py` |

## 已取消(不属于产品)

Pi Hook / Pi 桥 / Pi 适配器 / Pi 护栏开关;Pi 侧调用 Gate、Pi 配置回退;Ralph、工具适配 Agent、额外编排平台。

## 已知限制(如实)

1. 真实 >500k 长历史的稳态缓存命中、真实多轮压缩后的 API 重建:未测(只有离线模拟)。
2. checkpoint 每步全量快照(写入放大);`checkpoints_keep` 只按 run 裁剪;无 append-only 迁移。
3. 摘要无增量更新;长历史压缩需多次摘要调用(计入预算)。
4. 单模型(无切换/自动重试);无 Web UI(只有 CLI)。
5. 真实证据多为 `command-write` 弱来源,报告必须标注。
