# 续跑:fwcore(固件研究 Agent Framework)

任务来源 `docs/ROADMAP.md`;分支 `feat/agent-framework`;`main` 不动。
每轮:取 ROADMAP 下一个未完成项 → 写代码 + 反例测试 → 只跑相关测试
(`mkdir -p .tmp/pytest && timeout 180 python3 -m pytest <文件> -q --basetemp=.tmp/pytest; rm -rf .tmp`)
→ 把证据写进 ROADMAP 行 → commit → `ralph_done`。
不跑全量、不用真模型、不在 /tmp 落数据;遇授权/安全/阻塞即暂停。
只有 ROADMAP 全部验收完成且验证可重跑才输出 <promise>COMPLETE</promise>。

下一项(按 ROADMAP):Gate 迁移第二步 —— 把 KB 条数/引用完整性/材料检查接入 `fwcore/gates_db.py`,
并提供 `--legacy` 比对模式(与 `tools/derived_check.sh` 输出对照),然后做结构化工具观测。