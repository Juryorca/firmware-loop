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

文件**不存在**表示未配置(load 返回空 dict);文件存在但读失败/JSON 损坏/
顶层不是对象则抛 ToolConfigError,调用方必须明确报告,不能静默回退 PATH。
"""
from __future__ import annotations

import json
import os
import shlex
from typing import Any

FIELDS = ("path", "python", "cwd", "env")


class ToolConfigError(ValueError):
    """工具元数据文件存在但不可用;与“未配置”严格区分。"""

    def __init__(self, path: str, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def config_path(root: str) -> str:
    env = os.environ.get("FW_TOOL_CONFIG", "").strip()
    return env or os.path.join(root, "state", "tools.json")


def load(root: str) -> dict[str, dict]:
    """读本地工具元数据。

    - 文件不存在 ⇒ 空 dict(未配置);
    - 文件存在但不可读/JSON 损坏/顶层不是对象 ⇒ ToolConfigError。
    """
    path = config_path(root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise ToolConfigError(path, f"无法读取: {type(e).__name__}: {e}") from e
    except json.JSONDecodeError as e:
        raise ToolConfigError(path, f"JSON 解析失败(第 {e.lineno} 行第 {e.colno} 列): {e.msg}") from e
    if not isinstance(raw, dict):
        raise ToolConfigError(path, f"顶层必须是 JSON object,实际是 {type(raw).__name__}")
    # 兼容 {"tools": {...}} 只是为了让手写配置不易写错层;核心仍是扁平的 name -> entry。
    if isinstance(raw.get("tools"), dict):
        raw = raw["tools"]
    out: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ToolConfigError(path, f"工具项 {name!r} 必须是 object")
        clean: dict[str, Any] = {}
        for key in FIELDS:
            value = entry.get(key)
            if key == "env":
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise ToolConfigError(path, f"{name}.env 必须是 object")
                clean[key] = {str(k): str(v) for k, v in value.items()}
            elif isinstance(value, str) and value.strip():
                clean[key] = value.strip()
        if clean:
            out[name] = clean
    return out


def load_safe(root: str) -> tuple[dict[str, dict], str | None]:
    """返回 (配置, 错误文本);错误时配置为空,调用方仍需报告 error。"""
    try:
        return load(root), None
    except ToolConfigError as e:
        return {}, str(e)


def status(root: str) -> dict:
    """只读配置状态:missing / valid / error,用于状态页和前置检查。"""
    path = config_path(root)
    if not os.path.exists(path):
        return {"path": path, "exists": False, "valid": True, "error": None, "count": 0}
    try:
        cfg = load(root)
    except ToolConfigError as e:
        return {"path": path, "exists": True, "valid": False, "error": str(e), "count": 0}
    return {"path": path, "exists": True, "valid": True, "error": None, "count": len(cfg)}


def get(root: str, name: str) -> dict:
    return load(root).get(name, {})


def _rooted(root: str, value: str) -> str:
    value = os.path.expanduser(value)
    return value if os.path.isabs(value) else os.path.join(root, value)


def path_status(root: str, name: str) -> tuple[str | None, str]:
    """返回 (path|None, state)。

    state: ok | unconfigured | no-path | missing | invalid-config。
    """
    try:
        cfg = load(root)
    except ToolConfigError as e:
        return None, f"invalid-config:{e}"
    entry = cfg.get(name)
    if not entry:
        return None, "unconfigured"
    value = entry.get("path")
    if not value:
        return None, "no-path"
    p = _rooted(root, value)
    if not os.path.isfile(p):
        return p, "missing"
    return p, "ok"


def path(root: str, name: str) -> str | None:
    """返回配置中存在的实际可执行路径;未配置/路径不存在返回 None。"""
    p, state = path_status(root, name)
    return p if state == "ok" else None


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


def shell_exports(root: str, name: str) -> str:
    """给 shell wrapper 的安全 export 行;调用方先检查 path_status。"""
    rt = runtime(root, name)
    lines = []
    if rt.get("path"):
        lines.append(f"export FW_TOOL_PATH={shlex.quote(rt['path'])}")
    if rt.get("python"):
        lines.append(f"export FW_TOOL_PYTHON={shlex.quote(rt['python'])}")
    if rt.get("cwd"):
        lines.append(f"export FW_TOOL_CWD={shlex.quote(rt['cwd'])}")
    for key, value in (rt.get("env") or {}).items():
        if key and key.replace("_", "").isalnum() and not key[0].isdigit():
            lines.append(f"export {key}={shlex.quote(str(value))}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m fwcore.toolmeta")
    ap.add_argument("--root", default=os.environ.get("FW_ROOT", os.getcwd()))
    ap.add_argument("--path", metavar="NAME")
    ap.add_argument("--shell", metavar="NAME", help="输出 shell export(含 path/python/cwd/env)")
    a = ap.parse_args(argv)
    try:
        if a.path:
            p, state = path_status(a.root, a.path)
            if p and state == "ok":
                print(p)
                return 0
            if state.startswith("invalid-config:"):
                print(f"TOOL_CONFIG_ERROR: {state.split(':', 1)[1]}", file=sys.stderr)
                return 2
            if state == "missing":
                print(f"TOOL_PATH_MISSING: {a.path} 配置路径不存在: {p}", file=sys.stderr)
                return 3
            print(f"TOOL_UNCONFIGURED: {a.path}", file=sys.stderr)
            return 1
        if a.shell:
            _, state = path_status(a.root, a.shell)
            if state.startswith("invalid-config:"):
                print(f"TOOL_CONFIG_ERROR: {state.split(':', 1)[1]}", file=sys.stderr)
                return 2
            if state == "missing":
                p, _ = path_status(a.root, a.shell)
                print(f"TOOL_PATH_MISSING: {a.shell} 配置路径不存在: {p}", file=sys.stderr)
                return 3
            if state == "unconfigured":
                print(f"TOOL_UNCONFIGURED: {a.shell}", file=sys.stderr)
                return 1
            print(shell_exports(a.root, a.shell))
            return 0
        print(json.dumps(describe(a.root), ensure_ascii=False, indent=2))
        return 0
    except ToolConfigError as e:
        print(f"TOOL_CONFIG_ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
