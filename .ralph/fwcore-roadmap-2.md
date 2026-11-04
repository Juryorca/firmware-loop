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

### Iteration 3/5
- 完成:IDA 结构化封装 —— `ida_query` 工具(复用 `tools/ida_start.sh` 生命周期 + `ida_call.sh`),
  输出结构化(JSON 优先 → 文本抽函数名 → 否则 unparsed),`tool_calls` 记录 provider/capability/arch
- 顺带修:`eval/run.py` 丢失的 `_write_evidence`(编译失败);测试 stub 脚本权限收到 0o700
- 证据:`tests/test_toolkit_structured.py` 6 passed in 0.66s
- 下一项:诱饵/竞争解释评测任务;随后 Pi hook 接线

### Iteration 4/5
- 完成:**诱饵/竞争解释任务** `eval/tasks/decoy-comment/`(注释写"先解析后验签",实现已安全;
  只有跑 `selftest` 或读实现才能证伪注释)
- 评分器复用对照组模式:只凭注释下 vulnerable ⇒ 假阳性 ⇒ FAIL;做实验后 no_finding ⇒ PASS
- 顺带修:`eval/run.py` 输出/目录操作加保护;`tests/test_eval_scoring.py` 全部改 context manager
- 证据:`tests/test_eval_scoring.py` 4 passed in 0.81s
- 下一项:Pi hook 接线(Pi 事件 → `fw-cored pi-event`);之后做最终可重跑验证

### Iteration 5/5
- 完成:**恢复幂等**对抗用例 —— 模拟"工具已产生副作用、检查点未落盘":
  删检查点 + `runs.step` 回退 + 死 pid ⇒ `recover_stale()` 暂停 ⇒ `resume()` 重放同一 `call_key`
  命中幂等缓存,**副作用只发生一次**;running 的工具状态如实可见(不伪装 ok)
- 顺带:最终验证命令 `bash tools/fwcore_verify.sh`(cwd=仓库根;PYTHONPATH 由脚本设;`.tmp/verify` 跑完自删)
  实测:`pytest(8 文件) 52 passed in 9.62s` / `capabilities 9 项` / `gates 如实报阻塞` / `git clean` ⇒ **PASS**
- 证据:`tests/test_recovery_idempotency.py` 2 passed in 0.41s;`tools/fwcore_verify.sh` 退出码 0
- 仍未完成(不属于本轮 5 次迭代可闭环):Pi hook 接线(需用户授权改 live harness)、与 Pi 线对比、Ghidra/emu 结构化

### Iteration 6/5(续跑,超出原 5 次上限的一次增量)
- 完成:**Ghidra 结构化封装** `ghidra_query`(能力缺失时**如实报错**,不静默成功)+ 能力清单加 `debug.ghidra`
- 修真实 bug:注册处用 `isfile` 判**目录** `tools/ghidra_scripts` ⇒ 永不注册(改为 `isdir`)
- 证据:`tests/test_toolkit_structured.py` 8 passed in 1.03s(含"缺能力必须报错"与"有 runner 则结构化")
- 仍未完成(需授权/外部条件):Pi hook 接线(改 live harness)、与 Pi 线对比

### Iteration 7(授权口径诚实化)
- 完成:权限 vs 沙箱口径 —— `Authz.isolation`(默认 `none`)+ `describe()` + `fw-core authz`;
  用例证明"路径检查 ≠ 沙箱"(受权 shell 仍能访问 root 之外)
- 证据:`tests/test_lifecycle_semantics.py -k authz` 2 passed in 0.19s
- 完成判据#2 记录:`bash tools/fwcore_verify.sh`(cwd=仓库根;脚本自设 PYTHONPATH;临时目录 `.tmp/verify` 自删)
  实测输出:`pytest(8 文件) 61 passed in 15.20s` / `capabilities 10 项` / `gates 如实报阻塞` / `git clean` / rc=0
- 仍未完成(需授权/外部条件):Pi hook 接线、与 Pi 线对比

### Iteration 8(Gate 证据强度)
- 完成:Gate 增加两条**程序可核**的收尾阻塞 ——
  ① 证据版本必须与目标版本一致(否则列 `evidence_version_mismatch`)；
  ② `vulnerable` 结论必须挂**自己名下**的证据(否则列 unlinked)
- 证据:`tests/test_gates_db.py` 8 passed in 1.24s(新增两条:旧版证据→阻塞；证据失联→阻塞)
- 仍未完成(需授权/外部条件):Pi hook 接线、与 Pi 线对比
