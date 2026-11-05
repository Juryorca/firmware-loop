# 自主研究演练(真实模型,有界)

**等级:真实模型 + 合成目标**(不是真实固件研究质量证明)

- 模型 `deepseek-flash`,上限 calls=18 / seconds=240.0
- 结果: `{"state": "paused", "steps": 18, "rounds": 0, "handback": null, "final": "", "elapsed_s": 38.1, "model": "deepseek-flash", "max_calls": 18, "max_seconds": 240.0}`

## 闭环判定: **partial**

| 判据 | ok | 说明 |
|---|---|---|
| objects_consulted | ✅ | profile 内对象都被检查过 |
| decisive_attempts_have_direct_evidence | ❌ | 决定性尝试 0 个,缺直接证据 0 个 [] |
| scope_conclusions_complete | ❌ | authLogin.cgi 未达成 any |
| vulnerable_has_supported_attempt | ✅ | vulnerable 0 条 |
| vulnerable_evidence_causal | ✅ | vulnerable 是否都有因果复核证据 |
| no_stuck_running_attempt(非关键) | ✅ | running 尝试 0 条 |
| run_ended_normally(非关键) | ❌ | run.state=paused(finished=模型已收束) |
| recoverable_pause(非关键) | ✅ | checkpoints=18 |

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
 }
]
```

## 假设

```json
[]
```

## 尝试

```json
[]
```

## 观测

```json
[]
```

## 证据

```json
[]
```

## 复核记录

```json
[]
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
```
