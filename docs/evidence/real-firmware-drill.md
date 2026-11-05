# 自主研究演练(真实模型)

**等级:长程真实模型演练(质量验收候选;预算≠完成标准)**

- 结果: `{"state": "finished", "steps": 438, "rounds": 0, "handback": null, "final": "本轮已收口，无待办工具调用。\n\nFINAL: 研究收口。弱混淆定性、99.4% 代码还原与三面审计完成；认证绕过 inconclusive，其余四项 no_finding；状态与材料齐备。", "model": "deepseek-flash", "resumed_from": "R-20261105-0b4df34e", "max_calls": 432, "max_seconds": 7200.0, "counters": {"rounds": 0, "model_calls": 421, "tool_calls": 219, "elapsed_s": 1099.86}}`

## 闭环判定: **partial**

| 判据 | ok | 说明 |
|---|---|---|
| objects_consulted | ✅ | profile 内对象都被检查过 |
| decisive_attempts_have_source_links | ❌ | 决定性尝试 4 个,无任何来源链接 3 个 ['A-20261105-3fe88e54', 'A-20261105-20124b78', 'A-20261105-62d5f2d1'] |
| decisive_attempts_have_direct_evidence(非关键) | ❌ | 直接产出证据(source-link)覆盖 0/4 |
| scope_conclusions_complete | ❌ | authLogin.cgi 没有与结论关联的证据 |
| vulnerable_has_supported_attempt | ✅ | vulnerable 0 条 |
| vulnerable_evidence_causal | ✅ | vulnerable 是否都有因果复核证据 |
| no_stuck_running_attempt(非关键) | ✅ | running 尝试 0 条 |
| run_ended_normally(非关键) | ✅ | run.state=finished(finished=模型已收束) |
| recoverable_pause(非关键) | ✅ | checkpoints=120 |

## 工具调用

```json
[
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "capabilities_list",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "state/artifacts/R-20261105-0b4df34e/OUT-20261105-99b81a66.stdout 是框架内部文件(状态库/产物目录),不是研究对象。看汇总用 research_derived / research_queue;读工具输出用返回里的 stdout_path;二进制内容用 shell_exec 的 strings/file 而不是 fs_read"
 },
 {
  "tool": "fs_read",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "state/artifacts/R-20261105-0b4df34e/OUT-20261105-4accaf95.stdout 是框架内部文件(状态库/产物目录),不是研究对象。看汇总用 research_derived / research_queue;读工具输出用返回里的 stdout_path;二进制内容用 shell_exec 的 strings/file 而不是 fs_read"
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "文件不存在: _notes/tree.txt"
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "路径不合法: 路径越界(不在允许的根内): /tmp/drill/tree.txt"
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_command",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "工具不存在: shell_command"
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_hypothesis",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_hypothesis",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_hypothesis",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_hypothesis",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_record_mirror",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_hypothesis",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_link_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_record_mirror",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_start_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_observation",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_verify_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_finish_attempt",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_conclude",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 }
]
```

## 假设

```json
[
 {
  "id": "H-20261105-50d057be",
  "title": "样本 .text 为不可逆加密/损坏,静态与动态分析都不可行",
  "state": "concluded",
  "verdict": "no_finding",
  "scope_note": "适用范围:仅本样本 fwroot/authLogin.cgi(sha256 29eb3bdafdeeaf464f4dc4e0944f067b398ab01cb469225907a7f82dc8ba354d)的 .text(0xed00-0x2c7f4)。结论:\"不可逆加密/损坏、静态与动态均不可行\"不成立——保护是 4096 字节周期 XOR 填充,已完整统计还原并通过 1785 处 .rodata 字符串引用交叉验证。边界:(a) 残留约 123 个未解字(对齐/填充缝隙与个别低置信填充字节),细到单条指令级的引用需复核;(b) 本样本按现状不可执行(entry 0x128a8 落在密文区、无解包器),动态验证仍未做,也未被本结论覆盖。"
 },
 {
  "id": "H-20261105-01b73acc",
  "title": "CGI 请求参数被拼进 shell 命令行(qnap_exec/qnap_popen 族),存在命令注入",
  "state": "concluded",
  "verdict": "no_finding",
  "scope_note": "适用范围:还原后的 authLogin.cgi 自身代码中 qnap_exec/qnap_exec_pipe/qnap_popen 的全部 23 处调用点(E-20261105-9278b603)。结论:未见把 CGI 参数/cookie 值拼接成 shell 命令字符串的构造点,实参为字面量程序路径与开关,唯二格式化实参是 SMTP 口令与 0x40 上限提取的语言串。明确不覆盖:qnap_exec* 的实现位于 libuLinux_Util(工作区无该 .so),其内部若自行 shell 化则本结论不适用;也不覆盖未审计的认证前 func 分支内新引入的调用。"
 },
 {
  "id": "H-20261105-999e5e7c",
  "title": "登录主题/背景静态文件处理把请求参数拼进文件路径,存在路径穿越/任意文件读取",
  "state": "concluded",
  "verdict": "no_finding",
  "scope_note": "适用范围:还原代码中 0x287f0-0x28d70 的登录主题/背景文件服务及其 CGI_Get_Parameter 取值路径(E-20261105-2178ab99)。结论:参数值先过 strcmp 白名单 {loginBG,desktopBG,loginWelImg,standard_bg,standard_logo},数值项经 strtol 后进 bg%d,路径分量全为常量,fopen64 仅打开白名单内固定文件,未见穿越。/home/httpd/cgi-bin/loginTheme 的 \"%s/%s\" 拼接同样只接常量。不覆盖:其它 fopen64 调用点(语言包、配置文件等)与库函数内部的路径处理。"
 },
 {
  "id": "H-20261105-5f3348f6",
  "title": "登录前 func 分发(2FA/passwordless/qr_code/approve/verification_code)存在认证绕过",
  "state": "concluded",
  "verdict": "inconclusive",
  "scope_note": "适用范围:main@0xf470 的 func 分发链及 5 个 pre-auth handler(E-20261105-bda192a8,已在还原 ELF 上交叉复核)。已确认的静态事实:(1) func 分发前先取 \"user\" 并调 Is_Guest_User,访客在 0xf7dc 直接跳到输出/退出路径,不进任何分支;(2) 六条分支 qr_code→0x196d8、approve→0x197d8、verification_code→0x19908、passwordless→0x19a68、get_server_info→0x19b08、get_login_notify→0x182f8,前四者首步均为 \"op\" 的 strtol(base10) 子分发,未把字符串拼进 sink;(3) get_server_info 用 \"sid\"→auth_get_session 与 \"session\"→snprintf(0x21)→device_auth_get_session→strcmp 双路校验;(4) 登录主路径:user 就地截断 32 字节、pwd 经 strncpy 0x81、\"qtoken\"+\"qtokencheck\"→Verify_Qtoken_DB_by_Client_ID。未发现明显跳过校验的分支。仍未判定(缺什么):device_auth_*/auth_get_session/Verify_Qtoken_DB_by_Client_ID 为库函数,其参数契约、失败返回值语义与 sb 侧 qtoken 存储格式都在工作区缺失的 QNAP .so 中;本样本 .text 需按报告方法还原且当前不可执行,无法动态验证绕过。故:既无绕过证据,也不能排除。"
 },
 {
  "id": "H-20261105-2c8cb3bd",
  "title": "0xed00-0xefff(首个非满页 768 字节)与 .text 其余部分使用同一周期填充",
  "state": "concluded",
  "verdict": "no_finding",
  "scope_note": "适用范围:本样本 .text 的起始非满页 0xed00-0xefff(768 字节)。结论:\"与其余部分同一周期填充\"不成立——该段在 4..384 字节各位移上均无周期性(熵 7.46-7.78、零字节率 0.1-0.8%),10 例 mod-4096 不一致全部一端落在此段且解出乱码,故它使用独立的一次性密钥,不可统计还原。周期填充(4096 字节)自 0xf000 起生效并覆盖到 0x2c7f4,该范围内还原可信。因此:本报告的代码级结论覆盖 .text 的 99.4%(121588 字节中的 120820 字节),不覆盖这 768 字节(约 192 条指令,.text 最前面的函数体);若漏洞恰好落在该段,本报告的方法无法判定。"
 }
]
```

## 尝试

```json
[
 {
  "hypothesis_id": "H-20261105-50d057be",
  "method": "熵/周期性分析 → 判定保护类型 → 用统计攻击还原 .text(逐字节 MLE + 词级坐标上升)→ 以 .rodata 字符串交叉引用做验证",
  "result": "refuted",
  "note": "推翻\"不可逆\"。周期性检验确定保护为 4096 字节周期 XOR 填充;利用 .text 长度约为填充的 30 倍,做逐字节(按字节道先验)MLE + 词级坐标上升,完整还原 121588 字节 .text。验证:1785 组 adrp+add/ldr 引用精确落在可打印 .rodata 字符串上,177 个 ret、188 个 stp x29,x30 序言、30274/30397 字可解码;还原结果可回填进 ELF 并用 objdump 正常解析 PLT。残留约 123 字未解(对齐/填充缝隙与个别低置信填充字节),不影响结论。"
 },
 {
  "hypothesis_id": "H-20261105-01b73acc",
  "method": "在还原后的 authLogin.recovered.elf 上枚举 qnap_exec/qnap_exec_pipe/qnap_popen 全部调用点,逐一检查每个 argv 元素是否为字面量/snprintf 产物,并回溯是否来自 CGI 参数或 cookie",
  "result": "refuted",
  "note": "23 处调用点逐一审计:argv 式为字面量,唯二格式化实参是 SMTP 口令与本地化语言串(0x40 上限提取),无请求参数进入命令字符串。静态层面未见命令注入构造点。适用边界:qnap_exec* 的实现位于 libuLinux_Util(不在工作区),其内部是否 shell 化无法静态排除。"
 },
 {
  "hypothesis_id": "H-20261105-999e5e7c",
  "method": "审计 0x287f0-0x28d70 的登录主题/背景文件服务处理:检查 CGI_Get_Parameter 取值后进入 snprintf/fopen64 前是否有白名单与非路径化处理",
  "result": "refuted",
  "note": "参数先过 strcmp 白名单、数值输入经 strtol 后走 bg%d,路径分量全为常量;fopen64 只打开白名单内固定文件。未发现可穿越路径。"
 },
 {
  "hypothesis_id": "H-20261105-5f3348f6",
  "method": "从 main 的 func 分发表出发,沿每个 qr_code/approve/verification_code/passwordless/get_server_info 分支做控制流审计,检查认证状态与返回值使用",
  "result": "inconclusive",
  "note": "已定位 5 个认证前 func 分支入口与其后的 device_auth_*/qtoken 调用群,但未完成逐分支认证状态机审计(含返回值使用与前置条件);既不能给出绕过证据,也不能排除。"
 },
 {
  "hypothesis_id": "H-20261105-2c8cb3bd",
  "method": "对起点 768 字节单独做 shift-XOR 周期性检验(4..384 字节位移),并与其余部分的填充一致性对照;再逐例复原 10 个 mod-4096 不一致位点",
  "result": "refuted",
  "note": "推翻\"首段与其余同一填充\"。起始 0xed00-0xefff 的 768 字节在 4..384 字节所有位移上都没有周期性(熵 7.46-7.78、零字节率 0.1-0.8%),而 0x1000 位移在其余部分是 7.3% 零字节率;10 例 mod-4096 不一致全部一端落在该段并解出乱码。故该段使用独立的一次性密钥,768 字节不可还原,周期填充自 0xf000 起覆盖到 .text 末尾。"
 },
 {
  "hypothesis_id": "H-20261105-5f3348f6",
  "method": "从 main 的 func 分发点逐条追分支目标,检查每个 handler 的前置条件(是否要求已认证/sid 校验)与关键返回值的使用",
  "result": "inconclusive",
  "note": "六个 func 分支的入口与前置条件已走通(访客先被 Is_Guest_User 拦下并跳去输出路径;四个 handler 用 \"op\" 的 strtol 子分发,无字符串 sink;get_server_info 用 sid/session 两路做 auth_get_session / device_auth_get_session + strcmp)。登录主路径也读到 user 就地截断 32 字节、pwd strncpy 0x81、qtoken+qtokencheck→Verify_Qtoken_DB_by_Client_ID。未发现跳过校验的分支,但 device_auth_*/auth_get_session/Verify_Qtoken_DB_by_Client_ID 的实现与参数契约在缺失的 QNAP .so 中,无法判定是否存在逻辑绕过;且本样本不可执行,无法动态验证。"
 }
]
```

## 观测

```json
[
 {
  "text": ".text 的保护是\"明文 XOR 4096 字节周期填充\":713 个重复 32 位字中 703 个仅出现在 mod 4096 相同的位置;cipher⊕cipher(shift 0x1000/0x2000/0x4000/0x8000) 的零字节率从 0.4% 升到 7.3%,而 shift 0x400/0x800 无此现象(⇒周期恰为 0x1000)。文件内不存在高熵密钥材料,因此不是有密钥的加密,而是可统计还原的弱混淆。"
 },
 {
  "text": "还原代码中 qnap_exec/qnap_exec_pipe/qnap_popen 共 23 处调用点,调用形态是 argv 式(每个参数一个指针,末尾 0/NULL)。全部实参均为字面量程序路径与开关(/bin/grep、/bin/mount、/bin/kill、/usr/bin/xargs、/bin/rm -rf、/sbin/dns_resolve、/sbin/del_user_session、/sbin/br_util、/sbin/user_cmd、/usr/sbin/sendmail、/usr/local/bin/get_device_id、/sbin/curl 的固定参数序列等)。只有 2 处传入 snprintf 产物:'-ap%s'(SMTP 口令)与 /sbin/user_cmd 的 '%s'(本地化登录页字符串,来自 cookie/QLANG,且经 0x40 上限提取)。没有任何一处把请求参数拼进 shell 命令字符串。"
 },
 {
  "text": "0x287f0-0x28d70 的主题/背景处理:CGI_Get_Parameter 取到的值先经 strcmp 与固定集合 {loginBG, desktopBG, loginWelImg, standard_bg, standard_logo} 比对(0x289b0/0x289d8/0x289ec/0x28a00),不匹配直接 404;唯一数值输入走 strtol 后 snprintf 进 \"/home/httpd/cgi-bin/images/desktop/bg/static/bg%d\"(dst 长度 0x101)。其余路径全部由常量 \"%s/%s\" + 常量目录构成,fopen64/fread(0x1000)/fwrite 只读取白名单内的固定文件。请求参数不进入路径分量。"
 },
 {
  "text": "main@0xf470(13368 字节)在认证前解析 app/func/user/pwd/service/client_id/client_app/sid,并对 func 值 qr_code、approve、verification_code、passwordless、get_server_info 做 strcmp 分发(0xf7ec/0xf814/0xf828/0xf83c/0xf850);2FA/token 逻辑集中在导入的 device_auth_* 与 qtoken/vtoken 系列。已定位分支入口,但还没有逐个分支跑完认证状态机,故尚未能判定是否存在绕过。"
 },
 {
  "text": "10 例 mod-4096 不一致全部呈同一形态:一端落在 .text 起始的 0xed00-0xefff(768 字节的非满页),解出乱码(str q5,[x13,#0xb260] / ld1rod / bl 远址 / undefined),另一端解出标准指令(mov x0,x20 / mov w1,#0 / mov x29,sp / add x2,x2,#0xa08)。对 0xed00-0xf000 单独做 4..384 字节位移 XOR:熵稳定在 7.456-7.777、零字节率 0.1-0.8%,没有任何位移出现像 0x1000 位移那样的结构性下降(那里零字节率 7.3%)。结论:该 768 字节不是周期填充,而是用了一段独立的一次性密钥,不可统计还原;周期填充从 0xf000 起(文件页边界)生效,一直覆盖到 .text 末尾 0x2c7f4(__libc_csu_init/fini 的两个 ret 都被正确还原)。"
 },
 {
  "text": "func 分发链已逐条走通:main 在 0xf7b0 先取 \"user\" 并调 Is_Guest_User,若为访客则 0xf7dc 直接跳到 0xf70c(输出/退出),不再进入任何 func 分支;随后按序 strcmp func=qr_code(→0x196d8)、approve(→0x197d8)、verification_code(→0x19908)、passwordless(→0x19a68)、get_server_info(→0x19b08)、get_login_notify(→0x182f8)。四个 handler 的第一步都是从 \"op\" 参数取 strtol(base 10) 做子分发,不把字符串拼进任何 sink。get_server_info 内部走 device_auth_init_session → \"sid\"→auth_get_session / \"session\"→snprintf(dst,0x21,\"%s\")→device_auth_get_session → strcmp 校验;登录主路径 0xffd0-0x10104 依次做 Is_Guest_User、IsLocalUser、user 就地截断到 32 字节、\"pwd\" 经 strncpy(dst,src,0x81)、\"qtoken\"+\"qtokencheck\"→Verify_Qtoken_DB_by_Client_ID。未见到跳过校验的分支,但 device_auth_*/auth_get_session/Verify_Qtoken_DB_by_Client_ID 的语义在缺失的 .so 里,无法断定参数契约与失败语义。"
 }
]
```

## 证据

```json
[
 {
  "id": "E-20261105-37336d78",
  "kind": "xor-pad-period-proof",
  "path": "report/artifacts/xor_pad_period_proof.txt",
  "sha256": "64f1516a5d4feadd42c98a3c81341d705688caf94a4813019cd05d78513aa613",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-b36feb81",
  "kind": "xor-pad-keystream-test",
  "path": "report/artifacts/xor_pad_keystream_test.txt",
  "sha256": "d4f2d41c187dbcaba557349fd2b3ed8094c06baadcedd053ad7f7b99ee697498",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-837e2450",
  "kind": "recovered-plaintext + validation",
  "path": "report/artifacts/recovered_text.bin",
  "sha256": "7e2afa30ed65ea55bc5a2b4ba73aa2200480ffc94d4a2c36a6c582b844974897",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-a5b56772",
  "kind": "recovery-validation",
  "path": "report/artifacts/recovery_validation.txt",
  "sha256": "d167324ff1eee57896b58b4ae8f33b4665dc534da1eb40ca89241371c3cf1263",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-9278b603",
  "kind": "qnap_exec-call-site-audit",
  "path": "report/artifacts/qnap_exec_call_sites.txt",
  "sha256": "d8cf97d0b8a4e524dff7c7c81765c3966b6fa130dde08290cce5cd9dda27cb87",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-2178ab99",
  "kind": "theme-handler-audit",
  "path": "report/artifacts/theme_handler_audit.txt",
  "sha256": "6edec1feb38fa6f2cdc23a07d0b69de8f32bbb7ad40de911f2071dbaec0a30f7",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-3254087a",
  "kind": "func-dispatch-map",
  "path": "report/artifacts/func_dispatch_map.txt",
  "sha256": "2e599509c19eac79cc6c8c4ddb1bfbcc7f9b6c62f88f475022fb6d5b21bc8bbd",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-5ec9ea11",
  "kind": "xor-pad-collision-explained",
  "path": "report/artifacts/xor_pad_collision_explained.txt",
  "sha256": "5339d27f785bca20211d0782236a36560c7d15747a9b18f9094bb13618463322",
  "verified": 1,
  "tool_call_id": "TC-20261105-92cc7803"
 },
 {
  "id": "E-20261105-e8e75ade",
  "kind": "head-chunk-otp-proof",
  "path": "report/artifacts/head_chunk_otp_proof.txt",
  "sha256": "d2e42820298b6e2c7fee97ebfa070e8b056237e0f55c19603f4d66d49627c2b5",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-c84f6705",
  "kind": "research-report",
  "path": "report/report.md",
  "sha256": "41d39fb889b963d5835b697b2712fbf4de8844b5cd596fe53fba3abd3b068a49",
  "verified": 1,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-bda192a8",
  "kind": "preauth-func-branch-audit",
  "path": "report/artifacts/preauth_func_branch_audit.txt",
  "sha256": "d3b56a5de62ae10b76850323be08983b90c0a6c8623e780649c4ac821049f2ad",
  "verified": 1,
  "tool_call_id": null
 }
]
```

## 复核记录

```json
[
 {
  "evidence_id": "E-20261105-837e2450",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/recovered_text.bin"
 },
 {
  "evidence_id": "E-20261105-a5b56772",
  "method": "cross-check",
  "artifact_path": "report/artifacts/recovered_text.bin"
 },
 {
  "evidence_id": "E-20261105-37336d78",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/xor_pad_period_proof.txt"
 },
 {
  "evidence_id": "E-20261105-b36feb81",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/xor_pad_keystream_test.txt"
 },
 {
  "evidence_id": "E-20261105-9278b603",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/qnap_exec_call_sites.txt"
 },
 {
  "evidence_id": "E-20261105-2178ab99",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/theme_handler_audit.txt"
 },
 {
  "evidence_id": "E-20261105-3254087a",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/func_dispatch_map.txt"
 },
 {
  "evidence_id": "E-20261105-e8e75ade",
  "method": "recheck-sha256",
  "artifact_path": "report/artifacts/head_chunk_otp_proof.txt"
 },
 {
  "evidence_id": "E-20261105-5ec9ea11",
  "method": "cross-check",
  "artifact_path": "report/artifacts/recovered_text.bin"
 },
 {
  "evidence_id": "E-20261105-c84f6705",
  "method": "recheck-sha256",
  "artifact_path": "report/report.md"
 },
 {
  "evidence_id": "E-20261105-bda192a8",
  "method": "cross-check",
  "artifact_path": "report/artifacts/authLogin.recovered.elf"
 }
]
```

## 结论

```json
[
 {
  "hypothesis_id": "H-20261105-50d057be",
  "verdict": "no_finding",
  "scope_note": "适用范围:仅本样本 fwroot/authLogin.cgi(sha256 29eb3bdafdeeaf464f4dc4e0944f067b398ab01cb469225907a7f82dc8ba354d)的 .text(0xed00-0x2c7f4)。结论:\"不可逆加密/损坏、静态与动态均不可行\"不成立——保护是 4096 字节周期 XOR 填充,已完整统计还原并通过 1785 处 .rodata 字符串引用交叉验证。边界:(a) 残留约 123 个未解字(对齐/填充缝隙与个别低置信填充字节),细到单条指令级的引用需复核;(b) 本样本按现状不可执行(entry 0x128a8 落在密文区、无解包器),动态验证仍未做,也未被本结论覆盖。"
 },
 {
  "hypothesis_id": "H-20261105-01b73acc",
  "verdict": "no_finding",
  "scope_note": "适用范围:还原后的 authLogin.cgi 自身代码中 qnap_exec/qnap_exec_pipe/qnap_popen 的全部 23 处调用点(E-20261105-9278b603)。结论:未见把 CGI 参数/cookie 值拼接成 shell 命令字符串的构造点,实参为字面量程序路径与开关,唯二格式化实参是 SMTP 口令与 0x40 上限提取的语言串。明确不覆盖:qnap_exec* 的实现位于 libuLinux_Util(工作区无该 .so),其内部若自行 shell 化则本结论不适用;也不覆盖未审计的认证前 func 分支内新引入的调用。"
 },
 {
  "hypothesis_id": "H-20261105-999e5e7c",
  "verdict": "no_finding",
  "scope_note": "适用范围:还原代码中 0x287f0-0x28d70 的登录主题/背景文件服务及其 CGI_Get_Parameter 取值路径(E-20261105-2178ab99)。结论:参数值先过 strcmp 白名单 {loginBG,desktopBG,loginWelImg,standard_bg,standard_logo},数值项经 strtol 后进 bg%d,路径分量全为常量,fopen64 仅打开白名单内固定文件,未见穿越。/home/httpd/cgi-bin/loginTheme 的 \"%s/%s\" 拼接同样只接常量。不覆盖:其它 fopen64 调用点(语言包、配置文件等)与库函数内部的路径处理。"
 },
 {
  "hypothesis_id": "H-20261105-5f3348f6",
  "verdict": "inconclusive",
  "scope_note": "适用范围:main@0xf470 中对 func=qr_code/approve/verification_code/passwordless/get_server_info 的分发入口(E-20261105-3254087a)及紧随其后的 device_auth_*/qtoken/vtoken 调用群。缺什么:(1) 5 条分支的完整认证状态机审计(前置条件、返回值是否被检查、失败路径是否仍继续);(2) QNAP 侧库(deviceauth、casauth、qtoken 相关 .so)与真实请求语义,用于确认参数契约;(3) 可执行环境做动态验证(本样本 .text 需先按报告方法还原并补齐依赖库)。因此既无绕过证据也未能否证。"
 },
 {
  "hypothesis_id": "H-20261105-2c8cb3bd",
  "verdict": "no_finding",
  "scope_note": "适用范围:本样本 .text 的起始非满页 0xed00-0xefff(768 字节)。结论:\"与其余部分同一周期填充\"不成立——该段在 4..384 字节各位移上均无周期性(熵 7.46-7.78、零字节率 0.1-0.8%),10 例 mod-4096 不一致全部一端落在此段且解出乱码,故它使用独立的一次性密钥,不可统计还原。周期填充(4096 字节)自 0xf000 起生效并覆盖到 0x2c7f4,该范围内还原可信。因此:本报告的代码级结论覆盖 .text 的 99.4%(121588 字节中的 120820 字节),不覆盖这 768 字节(约 192 条指令,.text 最前面的函数体);若漏洞恰好落在该段,本报告的方法无法判定。"
 },
 {
  "hypothesis_id": "H-20261105-5f3348f6",
  "verdict": "inconclusive",
  "scope_note": "适用范围:main@0xf470 的 func 分发链及 5 个 pre-auth handler(E-20261105-bda192a8,已在还原 ELF 上交叉复核)。已确认的静态事实:(1) func 分发前先取 \"user\" 并调 Is_Guest_User,访客在 0xf7dc 直接跳到输出/退出路径,不进任何分支;(2) 六条分支 qr_code→0x196d8、approve→0x197d8、verification_code→0x19908、passwordless→0x19a68、get_server_info→0x19b08、get_login_notify→0x182f8,前四者首步均为 \"op\" 的 strtol(base10) 子分发,未把字符串拼进 sink;(3) get_server_info 用 \"sid\"→auth_get_session 与 \"session\"→snprintf(0x21)→device_auth_get_session→strcmp 双路校验;(4) 登录主路径:user 就地截断 32 字节、pwd 经 strncpy 0x81、\"qtoken\"+\"qtokencheck\"→Verify_Qtoken_DB_by_Client_ID。未发现明显跳过校验的分支。仍未判定(缺什么):device_auth_*/auth_get_session/Verify_Qtoken_DB_by_Client_ID 为库函数,其参数契约、失败返回值语义与 sb 侧 qtoken 存储格式都在工作区缺失的 QNAP .so 中;本样本 .text 需按报告方法还原且当前不可执行,无法动态验证绕过。故:既无绕过证据,也不能排除。"
 }
]
```

## 事件序列

```
target.created T-20261105-15dab0c0
surface.added S-20261105-2ef3adb2
run.started R-20261105-0b4df34e
tool.called TC-20261105-e0077c9f
tool.called TC-20261105-46bbfd9f
tool.called TC-20261105-42c7ab15
tool.called TC-20261105-b56effae
tool.called TC-20261105-7a5fe334
tool.called TC-20261105-38d7e0bd
tool.called TC-20261105-50290a56
tool.called TC-20261105-cd50ac01
tool.called TC-20261105-0eefb45f
tool.called TC-20261105-8483f080
tool.called TC-20261105-823537eb
tool.called TC-20261105-fd707347
tool.called TC-20261105-45d47af5
tool.called TC-20261105-e11ebd25
tool.called TC-20261105-d400f0a0
tool.called TC-20261105-1673f3ce
tool.called TC-20261105-56759724
tool.called TC-20261105-8c0b23e7
tool.called TC-20261105-246bd5a4
tool.called TC-20261105-a5c279e1
tool.called TC-20261105-b9f2c72e
tool.called TC-20261105-eae031fd
tool.called TC-20261105-655e87c7
tool.called TC-20261105-99047656
tool.called TC-20261105-55472504
run.limit_hit R-20261105-0b4df34e
run.finished R-20261105-0b4df34e
budget.legacy_unknown 
run.resumed R-20261105-0b4df34e
tool.called TC-20261105-5ae8dfd5
tool.called TC-20261105-f96d268f
tool.called TC-20261105-748f8904
tool.called TC-20261105-ccc62c4c
tool.called TC-20261105-4544179f
tool.called TC-20261105-2cf1f697
tool.called TC-20261105-d31f084f
tool.called TC-20261105-65135496
tool.called TC-20261105-810bce5a
tool.called TC-20261105-ebf5462b
tool.called TC-20261105-e119f505
tool.called TC-20261105-9e3d7f57
run.limit_hit R-20261105-0b4df34e
run.finished R-20261105-0b4df34e
run.resumed R-20261105-0b4df34e
tool.called TC-20261105-8e037ff9
tool.called TC-20261105-6fb8fa49
tool.called TC-20261105-437b6474
tool.called TC-20261105-654feca6
tool.called TC-20261105-096c1773
tool.called TC-20261105-278f7dec
tool.called TC-20261105-0e8cac1d
tool.called TC-20261105-85cf43fc
tool.called TC-20261105-91470550
tool.called TC-20261105-0466b578
tool.called TC-20261105-46751e38
tool.called TC-20261105-25324ba6
tool.called TC-20261105-74e70948
tool.called TC-20261105-62dfd2f2
tool.called TC-20261105-da800cb1
tool.called TC-20261105-7e5ac284
tool.called TC-20261105-d4c2ef32
tool.called TC-20261105-19ec7c87
tool.called TC-20261105-2b072805
tool.called TC-20261105-c971a534
tool.called TC-20261105-2d320695
tool.called TC-20261105-21d0a4e9
tool.called TC-20261105-6ce76d59
tool.called TC-20261105-39d31cd0
tool.called TC-20261105-70af0f10
tool.called TC-20261105-edb401e1
tool.called TC-20261105-86edb28d
tool.called TC-20261105-ac64de34
tool.called TC-20261105-93e7dad5
tool.called TC-20261105-0688978d
tool.called TC-20261105-c08fdb1b
tool.called TC-20261105-c582ed9e
tool.called TC-20261105-145b9e57
tool.called TC-20261105-8af90a87
tool.called TC-20261105-ef6a5735
tool.called TC-20261105-c82e6b1c
tool.called TC-20261105-ea965739
tool.called 
tool.called TC-20261105-9b742ebf
tool.called TC-20261105-8176347b
tool.called TC-20261105-c5246fab
tool.called TC-20261105-cc0861ee
tool.called TC-20261105-e498b1fc
tool.called TC-20261105-fa274677
tool.called TC-20261105-b9d97844
tool.called TC-20261105-fa0ac8a6
tool.called TC-20261105-acab732f
tool.called TC-20261105-b837a46d
tool.called TC-20261105-2bc24d5f
tool.called TC-20261105-5b8bb7ac
tool.called TC-20261105-e284e1ef
tool.called TC-20261105-1e9acc34
tool.called TC-20261105-735dc3bd
tool.called TC-20261105-6c860a34
tool.called TC-20261105-0030a029
tool.called TC-20261105-0f6357fd
tool.called TC-20261105-646b9feb
tool.called TC-20261105-caa23845
tool.called TC-20261105-ef1a8f8c
tool.called TC-20261105-43229b2c
tool.called TC-20261105-a53ec8af
tool.called TC-20261105-dfedb56e
tool.called TC-20261105-f2057a2e
tool.called TC-20261105-0cf0efda
tool.called TC-20261105-dbb0272e
tool.called TC-20261105-84755cda
tool.called TC-20261105-ff195cce
tool.called TC-20261105-06ff3cb5
tool.called TC-20261105-42be492a
tool.called TC-20261105-d5b24339
tool.called TC-20261105-a58fdb4d
tool.called TC-20261105-2782b1b9
tool.called TC-20261105-d4b6a985
```
