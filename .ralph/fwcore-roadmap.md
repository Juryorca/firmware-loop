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
