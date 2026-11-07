"""用户 Python 环境列表(有序)与轻量探测。

配置只放用户本地:
  1. 环境变量 FW_PYTHON_ENVS(路径用 os.pathsep 分隔)
  2. <root>/state/toolenv.json: {"python_envs": ["/path/to/python", ...]}
  3. 内置兼容默认值(仅作为 fallback,不作为唯一真源)
不安装、不修改环境;安装动作由 tool-management Skill 在执行时完成。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass


def default_envs() -> list[str]:
    home = os.path.expanduser("~")
    return [
        sys.executable,
        os.path.join(home, "ENTER", "envs", "pwn", "bin", "python"),
        os.path.join(home, "venvs", "unblob", "bin", "python"),
    ]


def load(root: str) -> list[str]:
    raw: list[str] = []
    env = os.environ.get("FW_PYTHON_ENVS", "")
    if env:
        raw += [p for p in env.split(os.pathsep) if p.strip()]
    cfg = os.path.join(root, "state", "toolenv.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            data = json.load(f)
        raw += [str(p) for p in (data.get("python_envs") or []) if str(p).strip()]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
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
        r = subprocess.run([path, "-c", code], capture_output=True, text=True, timeout=20)
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


def first_existing(root: str) -> str | None:
    for p in load(root):
        if os.path.isfile(p):
            return p
    return None


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

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
