# 证据索引(开发用,不是用户材料)

用户可见的只有 `state/<目标>/report/`。这里只放**能复核的测量与验证方法**;
真实 run 的原始记录在 SQLite(`state/<目标>/state/fwcore.sqlite3`),不在仓库里。

| 文件 | 内容 | 数据来源 |
|---|---|---|
| `token-audit.md` | 两个真实 run 的 usage 统计、Top 调用、缓存分层(Pi/fwcore)、压缩摘要成本 | `events.kind='model.call'` |
| `qnap-verification.md` | QNAP `.text` 4096 周期 XOR 恢复的独立验证方法(周期一致率/指令解码率/BL 落点) | `tools/verify_recovered_text.py` + 真实 ELF |

**不在这里的东西**(避免双源):
- 研究事实(假设/证据/结论/面)→ SQLite;
- 真实 run 的逐轮报告 → `state/<目标>/report/`(收尾时);
- 演练/端到端输出 → `state/_scratch/`(易失,脚本默认写这里);
- 跨目标经验 → `knowledge/`。
