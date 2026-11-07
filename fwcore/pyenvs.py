"""Agent 自己运行 Python 代码时使用的有序解释器队列。

用途只有两个:
  1. Agent 执行临时 Python 代码时选择一个能 import 所需模块的解释器;
  2. 决定 Python 包应该安装到哪个环境。

它**不是工具注册表**,不用于发现 unblob/angr/Ghidra 等已有工具。已有工具
通过 `fwcore/toolmeta.py` 的运行时元数据定位(见 state/tools.json)。

配置(只放本地,不进 Git):
  1. 环境变量 FW_PYTHON_ENVS(路径用 os.pathsep 分隔)
  2. <root>/state/pyenvs.json: {"python_envs": ["/path/to/python", ...]}
  3. 兼容旧的 <root>/state/toolenv.json
  4. 最后保留当前解释器作为兜底
顺序即优先级;缺失/依赖不满足就跳过,不修改环境。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass


def _config_paths(root: str) -> list[str]:
    return [
        os.path.join(root, "state", "pyenvs.json"),
        os.path.join(root, "state", "toolenv.json"),
    ]


def default_envs() -> list[str]:
    """只兜底当前解释器;不假设用户有 pwn/unblob 等固定目录。"""
    return [sys.executable]


def load(root: str) -> list[str]:
    raw: list[str] = []
    env = os.environ.get("FW_PYTHON_ENVS", "")
    if env:
        raw += [p for p in env.split(os.pathsep) if p.strip()]
    for cfg in _config_paths(root):
        try:
            with open(cfg, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        raw += [str(p) for p in (data.get("python_envs") or []) if str(p).strip()]
        break
    raw += default_envs()
    out: list[str] = []
    seen: set[str] = set()
    for p in raw:
        p = os.path.expanduser(p.strip())
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


@dataclass
class Probe:
    path: str
    status: str       # ready | missing | deps_missing
    detail: str = ""
    version: str | None = None


def probe(path: str, modules: tuple[str, ...] = ()) -> Probe:
    if not os.path.isfile(path):
        return Probe(path, "missing", "解释器不存在")
    try:
        if not os.access(path, os.X_OK):
            return Probe(path, "missing", "解释器不可执行")
    except OSError:
        return Probe(path, "missing", "解释器不可访问")
    code = "import sys;print(sys.version.split()[0])"
    if modules:
        code += ";import " + ",".join(modules)
    try:
        r = subprocess.run([path, "-c", code], capture_output=True, text=True,
                           timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return Probe(path, "deps_missing", f"{type(e).__name__}: {e}")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else ""
        return Probe(path, "deps_missing", detail[:300])
    version = (r.stdout.strip().splitlines() or [""])[-1]
    return Probe(path, "ready", "", version=version or None)


def select(root: str, modules: tuple[str, ...] = ()) -> dict:
    """按配置顺序选择第一个能 import 全部 modules 的环境;失败项保留原因。"""
    attempts = []
    for p in load(root):
        pr = probe(p, modules)
        attempts.append({"path": p, "status": pr.status, "detail": pr.detail, "version": pr.version})
        if pr.status == "ready":
            return {"selected": p, "version": pr.version, "attempts": attempts}
    return {"selected": None, "attempts": attempts}


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m fwcore.pyenvs")
    ap.add_argument("--root", default=os.environ.get("FW_ROOT", os.getcwd()))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--select", default=None, metavar="MODULE")
    a = ap.parse_args(argv)
    if a.select:
        out = select(a.root, tuple(x for x in a.select.split(",") if x))
        if out["selected"]:
            print(out["selected"])
            return 0
        print(json.dumps(out, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps([{"path": p, "probe": probe(p).__dict__} for p in load(a.root)],
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
