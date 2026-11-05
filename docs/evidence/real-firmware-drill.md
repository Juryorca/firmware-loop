# 自主研究演练(真实模型)

**等级:长程真实模型演练(质量验收候选;预算≠完成标准)**

- 结果: `{"state": "paused", "steps": 138, "rounds": 0, "handback": null, "final": "", "model": "deepseek-flash", "resumed_from": "R-20261105-0b4df34e", "max_calls": 120, "max_seconds": 2400.0, "counters": {"rounds": 0, "model_calls": 120, "tool_calls": 175, "elapsed_s": 563.51}}`

## 闭环判定: **partial**

| 判据 | ok | 说明 |
|---|---|---|
| objects_consulted | ✅ | profile 内对象都被检查过 |
| decisive_attempts_have_direct_evidence | ❌ | 决定性尝试 3 个,缺直接证据 3 个 ['A-20261105-6f53ed83', 'A-20261105-3fe88e54', 'A-20261105-20124b78'] |
| scope_conclusions_complete | ❌ | authLogin.cgi 未达成 any |
| vulnerable_has_supported_attempt | ✅ | vulnerable 0 条 |
| vulnerable_evidence_causal | ✅ | vulnerable 是否都有因果复核证据 |
| no_stuck_running_attempt(非关键) | ✅ | running 尝试 0 条 |
| run_ended_normally(非关键) | ❌ | run.state=paused(finished=模型已收束) |
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
 }
]
```

## 假设

```json
[
 {
  "id": "H-20261105-50d057be",
  "title": "样本 .text 为不可逆加密/损坏,静态与动态分析都不可行",
  "state": "investigating",
  "verdict": null,
  "scope_note": null
 },
 {
  "id": "H-20261105-01b73acc",
  "title": "CGI 请求参数被拼进 shell 命令行(qnap_exec/qnap_popen 族),存在命令注入",
  "state": "investigating",
  "verdict": null,
  "scope_note": null
 },
 {
  "id": "H-20261105-999e5e7c",
  "title": "登录主题/背景静态文件处理把请求参数拼进文件路径,存在路径穿越/任意文件读取",
  "state": "investigating",
  "verdict": null,
  "scope_note": null
 },
 {
  "id": "H-20261105-5f3348f6",
  "title": "登录前 func 分发(2FA/passwordless/qr_code/approve/verification_code)存在认证绕过",
  "state": "investigating",
  "verdict": null,
  "scope_note": null
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
  "verified": 0,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-2178ab99",
  "kind": "theme-handler-audit",
  "path": "report/artifacts/theme_handler_audit.txt",
  "sha256": "6edec1feb38fa6f2cdc23a07d0b69de8f32bbb7ad40de911f2071dbaec0a30f7",
  "verified": 0,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-3254087a",
  "kind": "func-dispatch-map",
  "path": "report/artifacts/func_dispatch_map.txt",
  "sha256": "2e599509c19eac79cc6c8c4ddb1bfbcc7f9b6c62f88f475022fb6d5b21bc8bbd",
  "verified": 0,
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
 }
]
```

## 结论

```json
[]
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
