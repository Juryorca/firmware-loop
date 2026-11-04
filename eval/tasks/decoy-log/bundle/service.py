#!/usr/bin/env python3
"""竞争解释任务:bundle 里有一条**过期的日志/注释**声称 SIGSEGV,但实际运行不会崩。

用法:
  python3 service.py run <depth>     # 用给定嵌套深度跑一次(默认 5000)
  python3 service.py log             # 打印那条历史日志
"""
import sys

MAX_DEPTH = 64
STALE_LOG = """# 2026-08-01 旧版本 v0.9 崩溃记录(已修):
#   uncaught signal SIGSEGV in parse() when depth > 32
#   root cause: no depth limit
"""


def parse(body: bytes, depth: int = 0):
    if depth > MAX_DEPTH:
        raise ValueError("depth limit reached")
    head, _, tail = body.partition(b":")
    return {"k": head, "v": parse(tail[1:], depth + 1) if tail.startswith(b"{") else tail}


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "log":
        print(STALE_LOG)
        return 0
    depth = int(sys.argv[2]) if len(sys.argv) >= 3 else 5000
    body = b"a:{" * depth
    try:
        parse(body)
        print("parsed-ok")
        return 0
    except ValueError as e:
        print(f"rejected: {e}")
        return 0
    except RecursionError:
        print("CRASH RecursionError")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
