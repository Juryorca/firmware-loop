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