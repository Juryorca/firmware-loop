#!/usr/bin/env python3
"""模拟固件设备里的一个"带签名校验的配置接口"(真实可运行,不是玩具桩)。

用法:
  python3 device.py request <payload-file>   # 模拟收到一次请求,打印处理结果/异常
  python3 device.py info                     # 打印接口信息
"""
import json
import sys

SECRET = b"device-key"


def parse_body(raw: bytes):
    """递归解析(注意:这里对嵌套深度没有任何限制)。"""
    if not raw:
        return None
    head, _, tail = raw.partition(b":")
    if tail.startswith(b"{"):
        return {"k": head, "v": parse_body(tail[1:])}
    return {"k": head, "v": tail}


def verify_signature(raw: bytes, sig: bytes) -> bool:
    """在**解析之后**才调用的签名校验。"""
    return sig == SECRET + raw[:4]


def handle(raw: bytes, sig: bytes = b"") -> dict:
    parsed = parse_body(raw)          # ← 先解析
    if not verify_signature(raw, sig):  # ← 后验签
        raise PermissionError("bad signature")
    return {"ok": True, "parsed": parsed}


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "info":
        print("device: config endpoint (pre-auth reachable)\nparses body, then verifies signature")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "request":
        with open(sys.argv[2], "rb") as fh:
            raw = fh.read()
        try:
            print(json.dumps(handle(raw, b""), ensure_ascii=False)[:200])
        except PermissionError as e:
            print(f"denied: {e}")
        except RecursionError:
            print("CRASH RecursionError (parser aborted before signature check)")
            return 3
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
