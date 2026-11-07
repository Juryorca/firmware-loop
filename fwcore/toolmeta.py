"""工具运行时元数据:只记录定位一个已有工具所需的信息。

这里**不搜索** Python 环境队列,也不替 Agent 选择工具。配置默认在
`state/tools.json`(本地,不进 Git),也可用 `FW_TOOL_CONFIG` 覆盖。

最小形状::

    {
      "unblob": {
        "path": "/opt/unblob/bin/unblob",
        "python": "/opt/unblob/bin/python",
        "cwd": ".",
        "env": {"PATH": "/opt/unblob/bin:/usr/bin:/bin"}
      },
      "angr": {"python": "/opt/angr/bin/python", "cwd": "."},
      "qemu-aarch64-patched": {"path": "/opt/qemu/bin/qemu-aarch64-patched"}
    }

字段含义保持单一:
  path    实际可执行文件
  python  Python 工具使用的解释器(不是环境队列)
  cwd     工具运行时工作目录
  env     工具运行时额外环境变量

找不到配置就返回空;调用方按“未配置/路径不存在”如实报告,不回退去猜
Python 环境。系统命令的 PATH 查找由调用方显式选择,不藏在本模块里。
"""
from __future__ import annotations

import json
import os
from typing import Any

FIELDS = ("path", "python", "cwd", "env")


def config_path(root: str) -> str:
    env = os.environ.get("FW_TOOL_CONFIG", "").strip()
    return env or os.path.join(root, "state", "tools.json")


def load(root: str) -> dict[str, dict]:
    """读本地工具元数据;文件不存在/不可解析返回空 dict。"""
    path = config_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # 兼容 {"tools": {...}} 只是为了让手写配置不易写错层;核心仍是扁平的 name -> entry。
    if isinstance(raw.get("tools"), dict):
        raw = raw["tools"]
    out: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        clean: dict[str, Any] = {}
        for key in FIELDS:
            value = entry.get(key)
            if key == "env":
                if isinstance(value, dict):
                    clean[key] = {str(k): str(v) for k, v in value.items()}
            elif isinstance(value, str) and value.strip():
                clean[key] = value.strip()
        if clean:
            out[name] = clean
    return out


def get(root: str, name: str) -> dict:
    return load(root).get(name, {})


def _rooted(root: str, value: str) -> str:
    value = os.path.expanduser(value)
    return value if os.path.isabs(value) else os.path.join(root, value)


def path(root: str, name: str) -> str | None:
    """返回配置中存在的实际可执行路径;配置缺失/路径不存在返回 None。"""
    value = get(root, name).get("path")
    if not value:
        return None
    p = _rooted(root, value)
    return p if os.path.isfile(p) else None


def runtime(root: str, name: str) -> dict:
    """给探针/调用方使用的已解析元数据;不填默认值、不搜索。"""
    entry = get(root, name)
    if not entry:
        return {}
    out = dict(entry)
    if "path" in out:
        out["path"] = _rooted(root, out["path"])
    if "python" in out:
        out["python"] = _rooted(root, out["python"])
    if "cwd" in out:
        out["cwd"] = _rooted(root, out["cwd"])
    return out


def describe(root: str) -> list[dict]:
    """工具状态页用的只读摘要。"""
    cfg = load(root)
    rows = []
    for name in sorted(cfg):
        entry = runtime(root, name)
        rows.append({
            "name": name,
            "path": entry.get("path"),
            "python": entry.get("python"),
            "cwd": entry.get("cwd"),
            "configured": bool(entry.get("path") or entry.get("python")),
            "path_exists": (os.path.isfile(entry["path"]) if entry.get("path") else None),
            "python_exists": (os.path.isfile(entry["python"]) if entry.get("python") else None),
        })
    return rows


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m fwcore.toolmeta")
    ap.add_argument("--root", default=os.environ.get("FW_ROOT", os.getcwd()))
    ap.add_argument("--path", metavar="NAME")
    a = ap.parse_args(argv)
    if a.path:
        p = path(a.root, a.path)
        if p:
            print(p)
            return 0
        print(f"未配置或不存在: {a.path}", file=sys.stderr)
        return 1
    print(json.dumps(describe(a.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
