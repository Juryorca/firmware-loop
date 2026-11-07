"""Print Mode(对应 Pi 的 runPrintMode):单条消息,输出结果后退出。

走同一条 AgentSession → Runner 路径,不另写 Agent Loop;
工具/状态行写 stderr,最终回答写 stdout(可安全 `| jq` 或重定向)。
"""

from __future__ import annotations

import sys
import time

from ..runtime import AppRuntime


def run_print(runtime: AppRuntime, message: str) -> int:
    sess = runtime.session
    if not sess.target:
        print("错误: 没有可用的固件目标。先运行 `fw-core init <target>`。", file=sys.stderr)
        return 2
    if not message.strip():
        print("错误: print 模式需要一条消息(`fw-core -p \"...\"`)。", file=sys.stderr)
        return 2

    final: str | None = None
    done = False
    sess.submit(message)
    try:
        while not done:
            for t in sess.tool_updates():
                note = (t.get("note") or "").splitlines()[0][:120] if t.get("note") else ""
                print(f"[tool] {t['tool']} {t['status']}" + (f" · {note}" if note else ""),
                      file=sys.stderr)
            for ev in sess.drain_events():
                if ev.kind == "turn_done":
                    final = ev.data.get("final") or final
                    state = ev.data.get("state")
                    hb = ev.data.get("handback")
                    if state != "finished" or hb:
                        print(f"[run] state={state}" + (f" handback={hb}" if hb else ""),
                              file=sys.stderr)
                    done = True
                elif ev.kind == "error":
                    print(f"[error] {ev.text}", file=sys.stderr)
                    done = True
                elif ev.kind == "cancelled":
                    print(f"[cancelled] {ev.text}", file=sys.stderr)
                    done = True
            time.sleep(0.15)
    except KeyboardInterrupt:
        sess.cancel("用户中断(print mode)")
        print("[cancelled] 已请求停止", file=sys.stderr)
        return 130

    if sess.last_error:
        return 1
    if final:
        print(final)
    return 0
