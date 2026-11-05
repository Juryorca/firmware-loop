# 持续开发循环:fwcore(固件研究 Agent Framework)

## 事实来源
- 任务清单:**`docs/ROADMAP.md`**(唯一清单,勿另立)
- 分支:**`feat/agent-framework`**;**`main` 绝对不动**(不合并、不改默认分支)
- 工作区:`/home/juryorca/整活/漏洞复现/firmware-loop`

## 每轮契约(一条目)
1. 从 ROADMAP 取**下一个未完成项**(🟡 优先于 ⬜;已 ✅ 的不重复做)
2. 实现一个**可验证的工程增量**(代码 + 反例测试;不做纯文档轮)
3. 跑**受保护的相关测试**(单文件/单用例):
   `mkdir -p .tmp/pytest && timeout 180 python3 -m pytest <相关文件> -q --basetemp=.tmp/pytest; rm -rf .tmp`
   —— **禁跑全量**、禁真模型、禁 `/tmp` 落临时数据
4. 把结果写进 ROADMAP 对应行(状态 + 证据:测试文件名/耗时/关键断言),然后 commit
   (`git add -A && git commit -m "<增量>:<证据>"`)
5. 调 `ralph_done` 结束本轮;下一轮自动继续

## 硬约束
- 遇到**需要用户授权**(外网提交、登录态、动宿主环境)、**安全异常**、**真实阻塞** ⇒ 暂停并说明,不硬撑
- 任何执行保护(limits/watchdog/超时)不得删;不得用"资源不足"当研究结论
- 不输出整体完成标记,除非 ROADMAP 全部验收完成且最终验证可重跑

## 完成判据
ROADMAP 中 P0、第二阶段审计项、第三阶段主线**全部 ✅**,且:
`python3 -m pytest tests/ -q`(受保护运行)通过 + `python3 -m fwcore capabilities` / `gates` 可跑 + `git status` 干净
时才输出 `<promise>COMPLETE</promise>`。
## 进度日志

### Iteration 1(Pi Core 对齐任务的第 0 步:先修看护)
- 增量:**Watchdog 三处真实缺陷**修复(用户点名复核项)
  1. **跨线程 SQLite**:守护线程不再复用主线程 Connection ⇒ 传 `db_path`,每次扫描自开连接、用完即关
  2. **错误空转**:连续失败退避(2^n,上限 60s),连续 5 次失败 ⇒ 守护线程自行停止并记 `stopped_reason`
  3. 测试补:线程内自连接可暂停失联 run(errors==0)、持续失败会停止
- 证据:`tests/test_watchdog.py` **6 passed in 1.63s**
- 已读 Pi 源码基线:`earendil-works/pi` @ **d57c2d2**(agent-loop.ts 959 行已通读结构:runLoop / executeToolCallsSequential|Parallel / shouldTerminateToolBatch / failToolCallsFromTruncatedMessage / prepareToolCallArguments)
- 下一项:**写源码级对照表**(Pi 怎么做 → fwcore 怎么做 → 差距 → 是否改 → 反例测试),再按差距逐项实施

### Iteration 2(Pi 对齐:对照表 + 取消语义)
- 产出:`docs/PI-CORE-ALIGNMENT.md`(8 项对照,基线 `d57c2d2`;明确"现在就改/不改+理由/必须不同")
- 增量:`Runner.cancel()`(置 paused + handback=user_stop + 杀工具进程组;循环每步检查外部停止)
- 证据:`tests/test_cancel.py` 2 passed in 0.38s(安全点停止且不写结论;取消后零步)
- 下一项:轮前准备钩子(可取消/有超时)或工具参数截断显式失败

### Iteration 3(Pi 对齐:工具参数截断显式失败)
- 增量:对齐 Pi 的 `failToolCallsFromTruncatedMessage` —— 模型给的 tool arguments 若**截断/非法 JSON**:
  ① **不执行**该工具;② `tool_calls` 记 `error` 并写明"截断或非法 JSON ⇒ 未执行";
  ③ 历史里回填 tool 消息(不静默吞掉)
- 证据:`tests/test_tool_args_invalid.py` 1 passed in 0.19s(断言:无假设产生 / 记录为 error / 回填含"未执行")
- 下一项:轮前准备钩子(`prepareNextTurn` 语义:可取消 + 有超时)或只读工具并行(按需)

### Iteration 4(Pi 对齐:轮前准备钩子)
- 增量:`Runner(prepare=..., prepare_timeout=...)` 对齐 Pi 的 `prepareNextTurn`:
  每轮模型调用前执行;可用于压缩上下文/取外部状态;返回 `{"context_note": str}` 注入系统提示、
  `{"cancel": True}` 暂停(不写结论);**有超时**(超时只记 `run.prepare_timeout` 并继续),
  钩子异常只记 `run.prepare_error`,不中断研究
- 证据:`tests/test_prepare_hook.py` **4 passed in 2.13s**(注入生效/超时不阻塞/异常不杀 run/取消不收口)
- 下一项:只读工具并行(按需,Pi 的 executeToolCallsParallel 语义)或研究评测加难下一批

### Iteration 5(Pi 对齐:结构化进度快照)
- 增量:`Runner.progress(rid)` + `fw-core progress <run>` —— 给外部监控/Pi 适配器用的只读快照
  (state/steps/rounds/handback/blockers/prepare_timeouts/limits/recent_events),不解析日志
- 证据:`tests/test_prepare_hook.py` **5 passed in 2.27s**(含 progress 断言:blockers 非空、limits 可见)
- 下一项:只读工具并行(按需;若无真实并行需求则标"不采纳+理由");随后 Pi hook 需授权,暂停等用户

### Iteration 6(验证覆盖 + 完成判据记录)
- 增量:`tools/fwcore_verify.sh` 覆盖 Pi 对齐新增的 3 个测试(`test_cancel` / `test_tool_args_invalid` / `test_prepare_hook`)
- **完成判据记录(外部可重跑)**
  - 命令:`bash tools/fwcore_verify.sh`
  - 工作目录:仓库根(`/home/juryorca/整活/漏洞复现/firmware-loop`)
  - 环境:`PYTHONPATH=$ROOT`(脚本内设置)、`PY=python3`;不使用 `/tmp`(临时目录 `.tmp/verify`,跑完自删)
  - 输出摘要:`pytest(8 文件) 74 passed in 17.75s` / `capabilities 10 项` / `gates 如实报阻塞` /
    `评测答案隔离 PASS` / `ROADMAP 一致性 PASS` / `git status clean` / 退出码 0
- 仍未完成(阻塞,不影响本轮增量):Pi hook 接线(待授权)、与 Pi 线对比(待外部条件)

### Iteration 7(Gate 迁移:legacy 桥接真脚本验证)
- 增量:新增集成用例 —— 用**仓库里真实的** `tools/derived_check.sh` 跑 `legacy_compare`,
  断言 `available=True`、`returncode∈{0,1}`、`summary` 以 `=== derived_check:` 开头(桥接不是空壳)
- 证据:`tests/test_gates_db.py` **9 passed in 1.24s**(9 条含 legacy 集成)
- 下一项:无未阻塞项剩余 —— Pi hook 接线(待授权)、与 Pi 线对比(待外部条件)⇒ 按硬约束暂停等用户

### Iteration 8(Watchdog 自动启动 —— 用户点名项)
- 增量:`Runner(watchdog=True, watchdog_poll_s=..., watchdog_heartbeat_timeout=...)`:
  运行开始自动起守护线程(**用自己的 db 连接**)、运行结束(含异常路径)自动停;
  `Watchdog.scans` 计数便于外部验证"确实跑过"
- 证据:`tests/test_watchdog.py` **8 passed in 1.91s**(含:自动启动且 scans≥1、结束后线程为 None 不泄漏、未开启时不起线程)
- 下一项:仍只剩阻塞项(Pi hook 待授权 / Pi 线对比待外部条件)

### Iteration 9(CLI 端到端冒烟)
- 增量:`tests/test_cli_smoke.py` —— 用 subprocess(带超时)验证外部监控真正会跑的入口:
  `--help` / `authz` / `capabilities` / `gates <合成目标>`(有未收口假设必须非零退出)
- 证据:**4 passed in 11.59s**;证明 CLI 路径可用,不只有库函数可用
- 下一项:仍只剩阻塞项(Pi hook 待授权 / Pi 线对比待外部条件)

### Iteration 10(研究规则注入:按优先级选段落,替代盲截)
- 背景:用户此前指出"简单按前 N 字符截断并不能保证关键规则进入上下文"
- 增量:`fwcore/research.py::select_rules(text, budget)` —— 按 `## ` 分段,优先保留决定行为的章节
  (`不变量 / 停·不停 / 闸门 / 轮 / 假设池 / 证据 / 三镜 / 收尾 / Exit Criteria / 判据`),
  其余按原顺序填充;超预算显式标注"已省略 N 段";系统提示改用该函数(不再盲截)
- 证据:`tests/test_rule_selection.py` **4 passed in 0.01s**
  (关键段在文末也能保留 / 省略有标注 / 短文档不动 / 真实 AGENTS.md 压到 6000 仍保留停·不停或 Exit Criteria)
- 下一项:仍只剩阻塞项(Pi hook 待授权 / Pi 线对比待外部条件)

### Iteration 11(Pi hook:默认惰性的代码就位)
- 背景:启用 Pi hook 会改变**研究会话**的 live 行为 ⇒ 必须用户授权。代码可以先就位,但默认必须无害。
- 增量:`fwcore/pi_hook.py` —— 开关是**显式文件** `state/pi_bridge.enabled`(用户手动建/删);
  关闭时 `handle()` 纯 no-op(不写任何状态、不导重资源),打开时委派 `pi_bridge.handle_event`
- 证据:`tests/test_pi_hook_gate.py` **2 passed in 0.27s**(默认关闭且零写入;启用后写入;再关闭又零写入)
- 状态:代码就绪;**启用仍待用户授权**(创建那个文件即生效)

### Iteration 12(验证覆盖面补齐 + 干净的最终验证)
- 增量:`tools/fwcore_verify.sh` 的 pytest 清单补齐后三轮增量
  (`test_cli_smoke` / `test_rule_selection` / `test_pi_hook_gate`),pytest 覆盖由 74 → **87 条**
- **最终验证(外部可重跑)**
  - 命令:`bash tools/fwcore_verify.sh`
  - 工作目录:仓库根(`/home/juryorca/整活/漏洞复现/firmware-loop`)
  - 环境:脚本内 `PYTHONPATH=$ROOT`;`PY=python3`;临时目录 `.tmp/verify`(跑完自删,不用 `/tmp`)
  - 输出摘要:`pytest(8 文件) 87 passed in 30.40s` / `capabilities 10 项` / `gates 如实报阻塞` /
    `评测答案隔离 PASS` / `ROADMAP 一致性 PASS` / `git status clean` / 退出码 0
- 状态:除两项阻塞(Pi hook 启用待授权、与 Pi 线对比待外部条件)外,ROADMAP 全部 ✅

### Iteration 13(Gate legacy 桥接的 CLI 层验证)
- 增量:`tests/test_cli_smoke.py` 增加 `gates --legacy` 用例 —— 用 `--root 仓库` + `--db 临时库`,
  断言 CLI 层能调到**真实** `tools/derived_check.sh`(`legacy.available=True`、`summary` 前缀正确、有阻塞时 rc=1)
- 证据:`tests/test_cli_smoke.py` **5 passed in 12.99s**
- 先例修正:第一版用 `--root <tmp>` 导致脚本不存在(available=False)—— 已改为 root=仓库、DB=临时(不污染 state/)

### Iteration 14(与 Pi 线对比的**工具化**准备)
- 背景:"与 Pi 研究线双跑"需要外部条件(阻塞),但**对比口径**可以先固定 —— 否则将来跑完只能靠感觉。
- 增量:`eval/compare.py` —— 吃结构化输入(`{target, version, conclusions:[{title,verdict,scope,evidence}]}`),
  输出 `only_a / only_b / verdict_mismatch / evidence_gap / same_verdict / summary` 一行判定;
  `load()` 读文件失败会明确报错(不静默返回空)
- 证据:`tests/test_compare.py` **2 passed in 0.01s**(判定分歧与证据缺口都能抓;同一输入为空差异)
- 状态:工具就绪;**双跑仍需外部条件**(待用户指定目标与时间窗口)
