# 续跑:fwcore(固件研究 Agent Framework)

任务来源 `docs/ROADMAP.md`;分支 `feat/agent-framework`;`main` 不动。
每轮:取 ROADMAP 下一个未完成项 → 写代码 + 反例测试 → 只跑相关测试
(`mkdir -p .tmp/pytest && timeout 180 python3 -m pytest <文件> -q --basetemp=.tmp/pytest; rm -rf .tmp`)
→ 把证据写进 ROADMAP 行 → commit → `ralph_done`。
不跑全量、不用真模型、不在 /tmp 落数据;遇授权/安全/阻塞即暂停。
只有 ROADMAP 全部验收完成且验证可重跑才输出 <promise>COMPLETE</promise>。

下一项(按 ROADMAP):Gate 迁移第二步 —— 把 KB 条数/引用完整性/材料检查接入 `fwcore/gates_db.py`,
并提供 `--legacy` 比对模式(与 `tools/derived_check.sh` 输出对照),然后做结构化工具观测。
## 进度日志(每轮追加)

### Iteration 1/5
- 完成:仓库工具**结构化观测**(`fwcore/toolkit.py::OBSERVERS`)+ `tool_calls` 记录 provider/capability/arch/env
- 证据:`tests/test_toolkit_structured.py` 4 passed in 0.53s
- 提交:`abfad63`
- ROADMAP 已勾选(Gate 迁移第二步 ✅ / 结构化观测 🟡 仓库工具已结构化)
- 下一项:**评测加难 —— 无漏洞对照任务**(测假阳性)+ 评分要能核对"结论↔证据"因果

### Iteration 2/5
- 完成:评测加难第一步 —— 对照组任务 `eval/tasks/safe-control/`(安全实现:先验签+全量 MAC+深度上限)
  + 评分器 `eval/scoring.py`(对照组抓假阳性;`vulnerable` 结论必须挂**自己名下**证据 = 因果检查)
- 证据:`tests/test_eval_scoring.py` 3 passed in 0.48s
- 下一项:诱饵/竞争解释任务;随后 Pi hook 接线(Pi 事件 → fw-core pi-event)
