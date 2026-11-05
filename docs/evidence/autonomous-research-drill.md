# 自主研究演练(真实模型,有界)

**等级:真实模型 + 合成目标**(不是真实固件研究质量证明)

- 模型 `deepseek-flash`,上限 calls=18 / seconds=240.0
- 结果: `{"state": "paused", "steps": 18, "rounds": 0, "handback": null, "final": "", "elapsed_s": 66.4, "model": "deepseek-flash", "max_calls": 18, "max_seconds": 240.0}`

## 工具调用

```json
[
 {
  "tool": "shell_exec",
  "status": "error",
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
  "tool": "fs_read",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "error",
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
  "tool": "fs_read",
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
  "tool": "fs_read",
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
  "status": "error",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "state_add_evidence",
  "status": "error",
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
  "tool": "state_add_evidence",
  "status": "ok",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "error",
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
  "tool": "shell_exec",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": null
 },
 {
  "tool": "shell_exec",
  "status": "error",
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
 }
]
```

## 假设

```json
[
 {
  "id": "H-20261105-2d01b735",
  "title": "status.cgi 对 QUERY_STRING 未过滤即 eval ⇒ 命令注入",
  "state": "investigating",
  "verdict": null,
  "scope_note": null
 },
 {
  "id": "H-20261105-0d674940",
  "title": "login.cgi 的 user 参数处理安全(白名单限制为字母数字下划线)",
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
  "hypothesis_id": "H-20261105-2d01b735",
  "method": "在受控 harness 中以 QUERY_STRING 环境变量直接调用 webroot/cgi-bin/status.cgi(sh 解释),先跑良性基线确认正常输出,再注入 shell 元字符(;,命令替换,后台)并观测是否被执行;用\"写入文件标记\"作为命令确实执行的客观证据。",
  "result": "running",
  "note": null
 },
 {
  "hypothesis_id": "H-20261105-0d674940",
  "method": "反证式测试 login.cgi:假定其\"安全\"结论可能错,主动用 shell 元字符(;/$(...)//反引号)、多行注入、空捕获、选项注入、超长输入、前置/后置垃圾字符等载荷冲击 sed 白名单,检查是否出现命令执行或白名单逃逸。",
  "result": "running",
  "note": null
 }
]
```

## 观测

```json
[]
```

## 证据

```json
[
 {
  "id": "E-20261105-90fb6459",
  "kind": "command-output",
  "path": "/home/juryorca/整活/漏洞复现/firmware-loop/state/_scratch/drill-20261105-154426/state/artifacts/R-20261105-a5b3649b/OUT-20261105-3d5f3538.stdout",
  "sha256": "320ada4f01f2cbad116fdecbe3af91c691b73f5fbae6877704683b5e20da6a87",
  "verified": 0,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-2eb6febd",
  "kind": "artifact-file",
  "path": "/home/juryorca/整活/漏洞复现/firmware-loop/state/_scratch/drill-20261105-154426/pwn_proof.txt",
  "sha256": "b431d8f8669d5ffcc553122f82491f0f06a813ebb93222bd30098745b2dabbb7",
  "verified": 0,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-6234846d",
  "kind": "command-output",
  "path": "/home/juryorca/整活/漏洞复现/firmware-loop/state/_scratch/drill-20261105-154426/state/artifacts/R-20261105-a5b3649b/OUT-20261105-ae4f54cb.stdout",
  "sha256": "c15537c234ca4238fe7ed53532a42567f3260fd7aebf8c1500db8eece6e89f8d",
  "verified": 0,
  "tool_call_id": null
 },
 {
  "id": "E-20261105-a9e69384",
  "kind": "command-output",
  "path": "/home/juryorca/整活/漏洞复现/firmware-loop/state/_scratch/drill-20261105-154426/state/artifacts/R-20261105-a5b3649b/OUT-20261105-6a9dd4a7.stdout",
  "sha256": "d1048f3eec864b05577cabd711cbdd052c968eb1a5f8db60392d99db910da538",
  "verified": 0,
  "tool_call_id": null
 }
]
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
target.created T-20261105-a544ba64
surface.added S-20261105-dc67dbb3
run.started R-20261105-a5b3649b
tool.called TC-20261105-70f4b18c
tool.called TC-20261105-7c7b5cea
tool.called TC-20261105-a550bdfb
tool.called TC-20261105-bd8ebeb1
tool.called TC-20261105-4d242cc2
tool.called TC-20261105-31329d7b
tool.called TC-20261105-4f653ba5
tool.called TC-20261105-44e77403
tool.called TC-20261105-4f65cbf2
tool.called TC-20261105-b2573908
tool.called TC-20261105-27464998
tool.called TC-20261105-8b39dc94
tool.called TC-20261105-e68dcffd
tool.called TC-20261105-6e681292
tool.called TC-20261105-76c96aa2
tool.called TC-20261105-076fbb52
hypothesis.added H-20261105-2d01b735
tool.called TC-20261105-7d8d5661
hypothesis.added H-20261105-0d674940
tool.called TC-20261105-cd391989
tool.called TC-20261105-1f16ab7c
attempt.started A-20261105-86045115
tool.called TC-20261105-fbeb96ad
tool.called TC-20261105-3dca84bd
tool.called TC-20261105-6509ccb8
tool.called TC-20261105-e60bdc9f
tool.called TC-20261105-e6978057
tool.called TC-20261105-0fcd608b
tool.called TC-20261105-3ebf5a0a
tool.called TC-20261105-abd6ee2a
tool.called TC-20261105-2536383d
tool.called TC-20261105-015d55ec
tool.called TC-20261105-ce1b6c6b
evidence.added E-20261105-90fb6459
tool.called TC-20261105-d47d8ad6
tool.called TC-20261105-7e6a284c
tool.called TC-20261105-34ffcade
evidence.added E-20261105-2eb6febd
tool.called TC-20261105-a2569a7d
attempt.started A-20261105-6861a235
tool.called TC-20261105-5098d044
evidence.added E-20261105-6234846d
tool.called TC-20261105-fd791f76
evidence.added E-20261105-a9e69384
tool.called TC-20261105-f9da49bb
tool.called TC-20261105-77c1f514
tool.called TC-20261105-79215ba2
tool.called TC-20261105-29d64628
tool.called TC-20261105-2e96153d
tool.called TC-20261105-13549828
tool.called TC-20261105-67df39eb
tool.called TC-20261105-2943fa32
run.limit_hit R-20261105-a5b3649b
run.finished R-20261105-a5b3649b
```
