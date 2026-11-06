# 历史数据迁移(notes → SQLite)

> 一次性数据导入(`fwcore/migrate_all.py`),不是运行时回退。运行路径只读 SQLite;
> 缺库/缺目标 ⇒ `derived_check`/`gates` 直接 FAIL,没有 Markdown 回退。

## 1. 审计:内容分类与落点

| 内容类 | 历史位置 | DB 落点 | 重复/冲突处理 | 程序读取 |
|---|---|---|---|---|
| 假设池 | `notes.md` §4(`- **H-n**` 条目 或 表格) | `hypotheses` | 同一 `(id,title)` 跨池重复只留一条(保留有状态/更晚) | ✅ 队列/覆盖度/gates |
| 攻击面 | `report/attack-surface.md` 表(优先),回退 notes 表 | `surfaces` | 两处并存时**只取导出表**;无法解析的 disposition 记 needs_review | ✅ 覆盖度/收尾 |
| 尝试记录 | notes 轮记录里的验证叙述 | `attempts`(历史笔记没有结构化 attempt 行 ⇒ 0 条) | 叙述保留在 `research_logs(kind=round)` | ✅(新运行) |
| 观察 | `**OBS-xx**` 块、`观察:` 行 | `observations` | 其他散文观察 → needs_review(不猜) | ✅ 事实块 |
| 证据 | `证据:`/`evidence` 行里的路径 | `evidence`(+sha256) | 同 `(path,kind)` 去重 | ✅ |
| 证据复核 | `复核:`/`复现:` 行 | `evidence_checks` | 同证据可多条 | ✅ |
| 结论 | 假设状态行的显式 `定论/成立/已解除/已覆盖` | `conclusions` | 只认显式措辞;模糊状态 → needs_review | ✅ gates/评估 |
| 三镜 | `三镜: 机制=…\|作者=…\|状态=…` 行 | `mirror_checks` | 无法拆 3 段 ⇒ 记 needs_review | ✅ 收尾前置 |
| 研究日志/停止声明/下一步/决策 | notes 各节 | `research_logs`(round/stop/next/decision) | 标题+来源唯一 | 视图/审计 |
| 无法归类 | notes 各节、冲突清单 | `research_logs(kind=needs_review)` | 保留原文片段,不丢弃 | 视图 |
| 立项/指纹/提取/时间线等叙述 | notes 全文 | `documents`(逐字节 + sha256) | 同 path 只留最新版本 | 原文对照 |

`documents` **只是原文存档**,不是"迁移完成"的证据;结构化事实必须进各自表。

## 2. 迁移规则

- 只从**显式状态行**映射 verdict;不从"可能/大概/部分"推断成立或否证;
- 无法可靠分类 ⇒ `needs_review`(含 42 条冲突:disposition 写法、状态措辞);
- 确定性 id(内容/编号,不含行号)⇒ 幂等;`--reset` 可重跑;
- 原文保留在 `documents`,原文件继续留在 `state/<目标>/` 作只读对照;
- 不迁移 `report/` 交付物(格式不变)。

## 3. 结果(2026-11)

- 目录扫描:15 个 `state/*/`(`ida` 是 IDA 暂存区,不是研究目标);**14 个目标**全部迁移。
- 文档 26 份(14 notes + 10 attack-surface + 2 附加笔记)。
- 结构化行:surfaces **264**、hypotheses **241**、observations **68**、
  evidence **100**(只收材料文件;目录/命令行/越界路径不入 evidence)、evidence_checks **17**、
  mirror_checks **87**、conclusions **138**(vulnerable 38 /
  no_finding 100)、research_logs **1101**
  (needs_review 686 / round 239 / stop 86 /
  decision 59 / next 31)。
- 幂等:第二次 `migrate --all` 全表增量为 **0**;`--verify` **14/14 ok**(原文逐字节、无孤立关联)。
- 重复清理:迁移期内 8 组重复假设 → 0;**状态重述**(`H-11 的最终口径/定论…`)不建重复假设,
  改记 needs_review;文档版本 50 → 26(同 path 只留最新)。
- 语义抽检:241 条假设的 `source` 行定位与 notes 原文一致(mismatch 0);可疑标题 0。

| 目标 | 版本 | S | H | O | E | K | M | L | 其中 needs_review | C |
|---|---|---|---|---|---|---|---|---|---|---|
| AX21 | 5.6 | 14 | 0 | 0 | 0 | 0 | 0 | 4 | 1 | 0 |
| DIR850L | 1.10 | 0 | 0 | 0 | 1 | 1 | 0 | 14 | 7 | 0 |
| E8450 | 1.2.00 | 24 | 12 | 0 | 2 | 2 | 0 | 18 | 16 | 4 |
| QNAP-TS-233 | 5.2.11.3661 | 18 | 41 | 0 | 7 | 0 | 37 | 292 | 119 | 32 |
| QNAP-TS-464 | 5.2.10.3577 | 11 | 30 | 0 | 5 | 0 | 32 | 59 | 13 | 27 |
| QNAP-TS-932PX | 5.2.10.3577 | 14 | 29 | 0 | 0 | 0 | 0 | 30 | 13 | 15 |
| R6700v3 | 1.0.5.128 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 8 | 0 |
| RouterOS-7.24.4 | 7.24.4 | 30 | 15 | 68 | 19 | 2 | 0 | 133 | 104 | 3 |
| Synology-7.2.2 | 7.2.2 | 23 | 13 | 0 | 5 | 1 | 0 | 57 | 46 | 3 |
| Synology-7.3.2 | 7.3.2 | 13 | 26 | 0 | 23 | 6 | 0 | 151 | 127 | 22 |
| Synology-SRM | 1.3.2 | 28 | 35 | 0 | 2 | 0 | 0 | 70 | 59 | 15 |
| UDMPro | 5.1.33 | 36 | 10 | 0 | 5 | 2 | 9 | 34 | 30 | 0 |
| UniFiOSServer | 5.1.42 | 40 | 10 | 0 | 28 | 3 | 0 | 200 | 116 | 4 |
| UniFi-Access | 4.3.12 | 13 | 20 | 0 | 3 | 0 | 9 | 28 | 27 | 13 |

## 4. 验证

```bash
python3 -m fwcore migrate --all --dry-run     # 统计 + 冲突清单(不写)
python3 -m fwcore migrate --all --reset       # 清上次迁移行后重导(幂等)
python3 -m fwcore migrate --all --verify      # 逐目标:原文逐字节 + 行数 + 关联
python3 -m pytest tests/test_migrate_all.py tests/test_no_markdown_fallback.py
```

## 5. 已知边界(如实)

- 历史笔记没有结构化 `attempts` 行 ⇒ 该表 0 条;验证叙述在 `research_logs(kind=round)` 里,未编造。
- 660 条 `needs_review`:多为无法机械判定的状态措辞/散文观察/汇总节;原文在 `documents`,可逐条复核。
- `notes.md` 保留为只读原始记录(不是第二事实源);清理需用户确认后再做。
