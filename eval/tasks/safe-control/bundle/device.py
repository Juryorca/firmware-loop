#!/usr/bin/env python3
"""对照组:**安全实现**(对照"有缺陷"的任务,用来测假阳性)。

- 解析有深度上限;
- **先验签,再解析**;
- 签名覆盖整个 body(用 hmac 比较)。
"""
import hashlib
import hmac
import os
import sys

SECRET = os.environ.get("DEVICE_KEY", "device-key").encode()   # 模拟设备:允许环境变量覆盖
MAX_DEPTH = 16


def parse_body(raw: bytes, depth: int = 0):
    if depth > MAX_DEPTH:
        raise ValueError("too deep")
    if not raw:
        return None
    head, _, tail = raw.partition(b":")
    if tail.startswith(b"{"):
        return {"k": head, "v": parse_body(tail[1:], depth + 1)}
    return {"k": head, "v": tail}


def mac(raw: bytes) -> bytes:
    return hashlib.sha256(SECRET + raw).digest()


def handle(raw: bytes, sig: bytes = b"") -> dict:
    if not hmac.compare_digest(sig, mac(raw)):      # ← 先验签
        raise PermissionError("bad signature")
    return {"ok": True, "parsed": parse_body(raw)}   # ← 后解析(且有深度上限)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "info":
        print("device: safe config endpoint (mac over full body, depth-limited parse)")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "request":
        try:
            with open(sys.argv[2], "rb") as f:
                raw = f.read()
        except OSError as e:
            print(f"rejected: {e}")
            return 2
        try:
            print(handle(raw, b""))
        except PermissionError as e:
            print(f"denied: {e}")
        except ValueError as e:
            print(f"rejected: {e}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
