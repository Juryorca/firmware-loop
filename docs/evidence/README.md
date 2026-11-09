# 证据索引(开发用,不是用户材料)

用户可见的只有 `state/<目标>/report/`。这里只放**长期有效的工程验证资料**(测量方法/可复核结论);
真实 run 的原始记录在 SQLite(`state/<目标>/state/fwcore.sqlite3`),不在仓库里。

## 准入(硬)

只收:①长期有效的工程验证**方法** ②可复用的性能/缓存**测量结论** ③必要的架构验证资料。
不收:逐轮研究输出、模型运行记录、一次性演练报告、脚本自动生成的评测摘要、能由 SQLite/工具重生成的重复内容。
判据:**半年后重跑一次实验还能用它复核吗?** 不能 ⇒ 写 `state/_scratch/`(不进库)。

自动检查:`tools/fwcore_verify.sh` 的「运行产物边界」步(未跟踪文件/`eval-*.md`/脚本默认输出)。

| 文件 | 内容 | 数据来源 |
|---|---|---|
| `token-audit.md` | 两个真实 run 的 usage 统计、Top 调用、缓存分层(Pi/fwcore)、压缩摘要成本 | `events.kind='model.call'` |
| `qnap-verification.md` | QNAP `.text` 4096 周期 XOR 恢复的独立验证方法(周期一致率/指令解码率/BL 落点) | `tools/verify_recovered_text.py` + 真实 ELF |

**不在这里的东西**(避免双源):
- 研究事实(假设/证据/结论/面)→ SQLite;
- 真实 run 的逐轮报告 → `state/<目标>/report/`(收尾时);
- 演练/端到端输出 → `state/_scratch/`(易失,脚本默认写这里);
- 跨目标经验 → SQLite `knowledge_entries`（`knowledge_search` / `knowledge_get`）。
