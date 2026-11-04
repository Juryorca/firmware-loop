#!/usr/bin/env python3
"""模拟设备上一个带鉴权的管理接口(真实可运行)。

用法:
  python3 gate.py request <path> <token>    # 模拟一次请求,打印 status
  python3 gate.py routes                    # 打印路由表(仅路径,不暴露逻辑)
"""
import sys

ROUTES = {
    "/api/admin/status": {"auth": True},
    "/api/admin/legacy": {"auth": False},   # 旧路径,遗漏鉴权
}
TOKENS = {"admin-token"}


def request(path: str, token: str) -> tuple[int, str]:
    r = ROUTES.get(path)
    if not r:
        return 404, "not found"
    if r["auth"] and token not in TOKENS:
        return 401, "unauthorized"
    return 200, f"admin data for {path}"


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "routes":
        print("\n".join(sorted(ROUTES)))
        return 0
    if len(sys.argv) >= 4 and sys.argv[1] == "request":
        code, body = request(sys.argv[2], sys.argv[3])
        print(code, body)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
