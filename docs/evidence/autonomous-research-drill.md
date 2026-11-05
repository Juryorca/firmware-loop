# 自主研究演练(真实模型,有界)

**等级:真实模型 + 合成目标**(不是真实固件研究质量证明)

- 模型 `deepseek-flash`,上限 calls=24 / seconds=320.0
- 结果: `{"state": "paused", "steps": 24, "rounds": 0, "handback": null, "final": "", "elapsed_s": 70.2, "model": "deepseek-flash", "max_calls": 24, "max_seconds": 320.0}`

## 闭环判定: **completed**

| 判据 | ok | 说明 |
|---|---|---|
| danger_executed_with_real_payload | ✅ | 命中 3 次针对 status.cgi 的注入执行 |
| safe_control_probed(非关键) | ✅ | 对 login.cgi 执行了 3 次(对照需要多载荷反证) |
| evidence_linked_to_execution | ✅ | 5/5 条证据绑定了工具调用 |
| decisive_attempts_have_evidence | ✅ | 决定性尝试 2 个,缺证据的 0 个 [] |
| attempted_hypotheses_have_verified_evidence | ✅ | 有待决断尝试的假设 2 个,已有复核证据的 3 个 |
| vulnerable_evidence_causal | ✅ | vulnerable 结论 1 条 |
| vulnerable_has_supported_attempt | ✅ | vulnerable 是否都有 supported 尝试 |
| no_stuck_running_attempt(非关键) | ✅ | 仍在 running 的尝试 0 条 |
| recoverable_pause(非关键) | ✅ | run=paused checkpoints=24 |
| research_concluded(非关键) | ✅ | 结论 2 条 |

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
  "tool": "fs_read",
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
  "tool": "state_add_evidence",
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "未知参数: ['hit']"
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
  "status": "error",
  "provider": null,
  "capability": null,
  "note": "InvariantViolation: 该尝试结论是 refuted ⇒ 不能作为复现依据(需要 supported)"
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
  "tool": "shell_exec",
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
  "id": "H-20261105-adbeac20",
  "title": "status.cgi 对 QUERY_STRING 未过滤直接 eval,存在命令注入",
  "state": "concluded",
  "verdict": "vulnerable",
  "scope_note": "范围:本目标(synthetic-1)的 webroot/cgi-bin/status.cgi 全部内容(sha256 b68ae2d4e9c0545ddb94abdbded64b02b030d9c23a6dda6f854f0970082b07a2,6 行 shell)。入口仅 QUERY_STRING 一个;脚本内 `eval \"echo status: $QUERY_STRING\"` 使 ; | $( ) `` 以及 %20 解码后的空格均被 shell 二次解释。证据:O-20261105-32581932 / report/status_cgi_poc.txt(sha256 13e7047b...)+ report/poc/run_status.sh(4e137dca...);`temp=25;id` 真实执行 id 并回显 uid=1000(juryorca),`temp=25;touch /tmp/drill_pwn_status` 生成标记文件。注意边界:(1) 已在 CGI 等价环境 `env -i QUERY_STRING=... sh status.cgi` 复现,目标目录内无 httpd 二进制/配置,未做端到端 HTTP 验证 —— 实际可达性取决于部署的 web server 是否把原始查询串原样传给 CGI(标准 CGI 语义下 QUERY_STRING 不做反转义,元字符可原样到达);(2) 未覆盖其他未随固件提供的 CGI/调用方;(3) 这是合成演练目标,shell 与 /bin/sh 版本固定为本机 dash 语义。"
 },
 {
  "id": "H-20261105-b96d9877",
  "title": "login.cgi 的 user 参数提取是否可被绕过(白名单失效)",
  "state": "concluded",
  "verdict": "no_finding",
  "scope_note": "范围:本目标(synthetic-1)的 webroot/cgi-bin/login.cgi(sha256 5cfd1396fc26f4de9a2f282b33c9c8b8c52cd358c93de6e4277e2d7b469fd66b)在 CGI 等价环境下的 user 参数提取。13 条载荷(;/|/$()/``/``、%0a/%3b/%20 编码、../../etc/passwd、x=1&user=a、空值、多行 QUERY_STRING)全部未能把 [A-Za-z0-9_] 之外的字符带入 $USER,$USER 仅出现在 `echo \"hello $USER\"` 这一带引号的位置,无命令执行、无标记文件:见 O-20261105-ee223ff7 / report/login_cgi_poc.txt(sha256 fd984086..., 已 recheck-sha256)。残留边界(如实记录,不构成漏洞):(a) sed 表达式末尾无锚点且逐行匹配,多行 QUERY_STRING 会让 $USER 含换行并输出多行(\"hello a\\nb\"),字符集仍受限,只影响输出格式;(b) 未测真机 httpd 对 QUERY_STRING 的预处理(目标内无 httpd),也未测 login.cgi 之外是否有程序消费同一参数;(c) 结论只否定\"shell 命令注入/白名单绕过\",不断言其认证逻辑本身正确(该脚本无认证,仅回显)。"
 }
]
```

## 尝试

```json
[
 {
  "hypothesis_id": "H-20261105-adbeac20",
  "method": "在 CGI 拉起的等价环境(env -i QUERY_STRING=<payload> sh cgi-bin/status.cgi)下跑元字符载荷,用 id / 写标记文件判定 eval 是否真的执行命令",
  "result": "supported",
  "note": "supported: env -i QUERY_STRING='temp=25;id'(及 |、$()、``、%20;id 变体)下 status.cgi 打印并由 eval 执行了 id;';touch /tmp/drill_pwn_status' 生成标记文件。命令注入成立。产物 report/status_cgi_poc.txt。"
 },
 {
  "hypothesis_id": "H-20261105-b96d9877",
  "method": "在等价 CGI 环境(env -i QUERY_STRING=<payload> sh cgi-bin/login.cgi)下尝试让 shell 元字符/多行/%编码进入 sed 提取出的 $USER, 并检查是否产生命令执行(标记文件/id 输出);同时记录末尾无锚点导致的截断行为",
  "result": "refuted",
  "note": "refuted: 13 条载荷(元字符/多行/%编码/路径穿越/参数前缀)均未把非 [A-Za-z0-9_] 字符带入 $USER,无命令执行,无标记文件。产物 report/login_cgi_poc.txt。"
 }
]
```

## 观测

```json
[
 {
  "text": "env -i QUERY_STRING='temp=25;id' sh webroot/cgi-bin/status.cgi 输出 \"status: temp=25\" 后跟一行真实 id 结果(uid=1000(juryorca)...);'temp=25|id'、'temp=25$(id)'、'temp=25`id`'、'temp=25%20;id' 同样执行了 id;'temp=25;touch /tmp/drill_pwn_status' 生成了 /tmp/drill_pwn_status(-rw-r--r--, 0 字节)。即 QUERY_STRING 被 eval 二次解析,命令注入成立。产物:report/status_cgi_poc.txt (sha256 13e7047b...)、report/poc/run_status.sh (sha256 4e137dca...)。"
 },
 {
  "text": "13 条载荷下 login.cgi 均未产生命令执行:user=;id / user=../../etc/passwd / user= / x=1&user=a → rc=1 \"bad request\";user=a$(id)、user=a`id`、user=a|id、user=a;touch /tmp/drill_pwn_login、user=a%0aid、user=a%3bid、user=a%20;id → rc=0 \"hello a\"(元字符被 sed 的 [A-Za-z0-9_]* 截断丢弃);多行 QUERY_STRING(\"user=a\\nuser=b;id\") → 输出两行 \"hello a\" + \"b\",但每行仍只保留字母数字,$USER 由引号 echo 使用;标记文件 /tmp/drill_pwn_login 未生成。产物:report/login_cgi_poc.txt (sha256 fd984086...)、report/poc/run_login.sh (sha256 8425bc0a...)。"
 }
]
```

## 证据

```json
[
 {
  "id": "E-20261105-16ba39ea",
  "kind": "source",
  "path": "webroot/cgi-bin/status.cgi",
  "sha256": "b68ae2d4e9c0545ddb94abdbded64b02b030d9c23a6dda6f854f0970082b07a2",
  "verified": 1,
  "tool_call_id": "TC-20261105-aa3a2b7d"
 },
 {
  "id": "E-20261105-d78b9612",
  "kind": "reproduction-output",
  "path": "report/status_cgi_poc.txt",
  "sha256": "13e7047b01798f925c46691fbd027bd5c548da7d5f500c6b037d2377da82c69c",
  "verified": 1,
  "tool_call_id": "TC-20261105-aa3a2b7d"
 },
 {
  "id": "E-20261105-b734882c",
  "kind": "reproduction-output",
  "path": "report/login_cgi_poc.txt",
  "sha256": "fd984086380861bd04f8f61eecf07e35b39cc1ab093a7ef75fa04a79d2d02430",
  "verified": 1,
  "tool_call_id": "TC-20261105-aa3a2b7d"
 },
 {
  "id": "E-20261105-37443f61",
  "kind": "source",
  "path": "webroot/cgi-bin/login.cgi",
  "sha256": "5cfd1396fc26f4de9a2f282b33c9c8b8c52cd358c93de6e4277e2d7b469fd66b",
  "verified": 1,
  "tool_call_id": "TC-20261105-de19bed2"
 },
 {
  "id": "E-20261105-bac80233",
  "kind": "report",
  "path": "report/REPORT.md",
  "sha256": "333925110c056a0722a19cc9ab14f288601eeafe1e38a9ad4acb963bd55b0cff",
  "verified": 1,
  "tool_call_id": "TC-20261105-d2aea666"
 }
]
```

## 复核记录

```json
[
 {
  "evidence_id": "E-20261105-d78b9612",
  "method": "reproduce-attempt",
  "artifact_path": null
 },
 {
  "evidence_id": "E-20261105-16ba39ea",
  "method": "recheck-sha256",
  "artifact_path": "webroot/cgi-bin/status.cgi"
 },
 {
  "evidence_id": "E-20261105-b734882c",
  "method": "recheck-sha256",
  "artifact_path": "report/login_cgi_poc.txt"
 },
 {
  "evidence_id": "E-20261105-37443f61",
  "method": "recheck-sha256",
  "artifact_path": "webroot/cgi-bin/login.cgi"
 },
 {
  "evidence_id": "E-20261105-bac80233",
  "method": "recheck-sha256",
  "artifact_path": "report/REPORT.md"
 }
]
```

## 结论

```json
[
 {
  "hypothesis_id": "H-20261105-adbeac20",
  "verdict": "vulnerable",
  "scope_note": "范围:本目标(synthetic-1)的 webroot/cgi-bin/status.cgi 全部内容(sha256 b68ae2d4e9c0545ddb94abdbded64b02b030d9c23a6dda6f854f0970082b07a2,6 行 shell)。入口仅 QUERY_STRING 一个;脚本内 `eval \"echo status: $QUERY_STRING\"` 使 ; | $( ) `` 以及 %20 解码后的空格均被 shell 二次解释。证据:O-20261105-32581932 / report/status_cgi_poc.txt(sha256 13e7047b...)+ report/poc/run_status.sh(4e137dca...);`temp=25;id` 真实执行 id 并回显 uid=1000(juryorca),`temp=25;touch /tmp/drill_pwn_status` 生成标记文件。注意边界:(1) 已在 CGI 等价环境 `env -i QUERY_STRING=... sh status.cgi` 复现,目标目录内无 httpd 二进制/配置,未做端到端 HTTP 验证 —— 实际可达性取决于部署的 web server 是否把原始查询串原样传给 CGI(标准 CGI 语义下 QUERY_STRING 不做反转义,元字符可原样到达);(2) 未覆盖其他未随固件提供的 CGI/调用方;(3) 这是合成演练目标,shell 与 /bin/sh 版本固定为本机 dash 语义。"
 },
 {
  "hypothesis_id": "H-20261105-b96d9877",
  "verdict": "no_finding",
  "scope_note": "范围:本目标(synthetic-1)的 webroot/cgi-bin/login.cgi(sha256 5cfd1396fc26f4de9a2f282b33c9c8b8c52cd358c93de6e4277e2d7b469fd66b)在 CGI 等价环境下的 user 参数提取。13 条载荷(;/|/$()/``/``、%0a/%3b/%20 编码、../../etc/passwd、x=1&user=a、空值、多行 QUERY_STRING)全部未能把 [A-Za-z0-9_] 之外的字符带入 $USER,$USER 仅出现在 `echo \"hello $USER\"` 这一带引号的位置,无命令执行、无标记文件:见 O-20261105-ee223ff7 / report/login_cgi_poc.txt(sha256 fd984086..., 已 recheck-sha256)。残留边界(如实记录,不构成漏洞):(a) sed 表达式末尾无锚点且逐行匹配,多行 QUERY_STRING 会让 $USER 含换行并输出多行(\"hello a\\nb\"),字符集仍受限,只影响输出格式;(b) 未测真机 httpd 对 QUERY_STRING 的预处理(目标内无 httpd),也未测 login.cgi 之外是否有程序消费同一参数;(c) 结论只否定\"shell 命令注入/白名单绕过\",不断言其认证逻辑本身正确(该脚本无认证,仅回显)。"
 }
]
```

## 事件序列

```
target.created T-20261105-3de9e8cf
surface.added S-20261105-5976d223
run.started R-20261105-7db2dc42
tool.called TC-20261105-b1d2d29f
tool.called TC-20261105-ee066b40
tool.called TC-20261105-5091a2db
tool.called TC-20261105-26f0ed4c
tool.called TC-20261105-a07e598e
tool.called TC-20261105-40a00597
tool.called TC-20261105-ddb6a09d
tool.called TC-20261105-09c9f7a3
hypothesis.added H-20261105-adbeac20
tool.called TC-20261105-f27d7cd2
hypothesis.added H-20261105-b96d9877
tool.called TC-20261105-1dcc8b56
attempt.started A-20261105-96bd1801
tool.called TC-20261105-4452d7d7
tool.called TC-20261105-325d5322
tool.called TC-20261105-93270aec
attempt.started A-20261105-9b9b4e2a
tool.called TC-20261105-7b5df10a
tool.called TC-20261105-cdcef66e
tool.called TC-20261105-9ac89313
tool.called TC-20261105-aa3a2b7d
tool.called 
tool.called TC-20261105-f8548e73
evidence.added E-20261105-16ba39ea
tool.called TC-20261105-32fe41bf
evidence.added E-20261105-d78b9612
tool.called TC-20261105-b1c2b310
evidence.added E-20261105-b734882c
tool.called TC-20261105-e14ca215
observation.added O-20261105-32581932
tool.called TC-20261105-d1c96e43
observation.added O-20261105-ee223ff7
tool.called TC-20261105-de19bed2
attempt.observation_outcome A-20261105-96bd1801
attempt.finished A-20261105-96bd1801
tool.called TC-20261105-e5ed7fb3
attempt.observation_outcome A-20261105-9b9b4e2a
attempt.finished A-20261105-9b9b4e2a
tool.called TC-20261105-e312d9a3
evidence.verified E-20261105-d78b9612
tool.called TC-20261105-d80ff111
tool.called TC-20261105-68b54e02
evidence.verified E-20261105-16ba39ea
tool.called TC-20261105-e7381a63
evidence.verified E-20261105-b734882c
tool.called TC-20261105-fe5dac27
evidence.added E-20261105-37443f61
tool.called TC-20261105-e06d9340
evidence.verified E-20261105-37443f61
tool.called TC-20261105-79a679ed
hypothesis.concluded H-20261105-adbeac20
tool.called TC-20261105-ed2695f7
hypothesis.concluded H-20261105-b96d9877
tool.called TC-20261105-18a6689b
tool.called TC-20261105-d2aea666
mirror.recorded M-20261105-0b7993ed
tool.called TC-20261105-67440dee
tool.called TC-20261105-ca16a72b
evidence.added E-20261105-bac80233
tool.called TC-20261105-cc1cb27d
evidence.verified E-20261105-bac80233
tool.called TC-20261105-3299526e
run.limit_hit R-20261105-7db2dc42
run.finished R-20261105-7db2dc42
```
