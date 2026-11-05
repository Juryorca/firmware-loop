"""CLI 端到端冒烟:外部监控真正会跑的入口必须能跑(不只库函数可用)。

覆盖:--help / authz / capabilities / gates(合成目标,如实报阻塞)。
全部用 subprocess + 超时,不联网、不落 /tmp。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "fwcore", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_help_runs():
    r = _run("--help")
    assert r.returncode == 0 and "gates" in r.stdout and "pi-event" in r.stdout


def test_authz_reports_isolation():
    r = _run("authz")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["isolation"] in ("none", "chroot", "container", "bwrap")


def test_capabilities_lists_entries():
    r = _run("capabilities", timeout=180)
    assert r.returncode == 0
    caps = json.loads(r.stdout)
    assert isinstance(caps, list) and len(caps) >= 5
    assert all("best_status" in c for c in caps)


def test_gates_on_synthetic_target_reports_blockers(tmp_path):
    root = str(tmp_path)
    assert _run("--root", root, "init", "smoke", "--version", "t").returncode == 0
    assert _run("--root", root, "hyp", "add", "smoke", "示例假设").returncode == 0
    r = _run("--root", root, "gates", "smoke")
    assert r.returncode == 1, "有未收口假设时 gates 必须非零退出"
    rep = json.loads(r.stdout)
    assert rep["blockers"] and rep["coverage"] == "0/0"


def test_gates_legacy_bridge_through_cli(tmp_path):
    """`gates --legacy` 必须调到**仓库里真实的** derived_check.sh。

    做法:root=仓库根(脚本在那里),DB 放临时目录(不污染 state/)。
    """
    db = str(tmp_path / "lg.sqlite3")
    assert _run("--root", ROOT, "--db", db, "init", "lg", "--version", "t").returncode == 0
    r = _run("--root", ROOT, "--db", db, "gates", "lg", "--legacy", timeout=240)
    assert r.returncode == 1, "有阻塞时退出码应为 1"
    rep = json.loads(r.stdout)
    assert rep["legacy"]["available"] is True, rep.get("legacy")
    assert rep["legacy"]["summary"].startswith("=== derived_check:")
