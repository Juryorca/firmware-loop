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
                          capture_output=True, text=True, timeout=timeout, check=False)


def test_help_runs():
    r = _run("--help")
    assert r.returncode == 0 and "gates" in r.stdout


def test_authz_show_reports_isolation_and_authorization(tmp_path):
    root = str(tmp_path)
    db = str(tmp_path / "az.sqlite3")
    assert _run("--root", root, "--db", db, "init", "t", "--version", "1").returncode == 0
    r = _run("--root", root, "--db", db, "authz", "show", "t")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["authorized"] is False, "没有授权文件时必须如实报未授权"
    assert "authz grant" in d["note"], "要告诉用户怎么授权"

    g = _run("--root", root, "--db", db, "authz", "grant", "t", "--roots", root, "--hours", "1")
    assert g.returncode == 0
    r2 = _run("--root", root, "--db", db, "authz", "show", "t")
    d2 = json.loads(r2.stdout)
    assert d2["authorized"] is True and d2["isolation"] in ("none", "chroot", "container", "bwrap")


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


def test_gates_rejects_removed_legacy_flag(tmp_path):
    """legacy 对照模式已删除:运行路径只读 SQLite,`--legacy` 必须被 argparse 拒绝。"""
    db = str(tmp_path / "lg.sqlite3")
    assert _run("--root", ROOT, "--db", db, "init", "lg", "--version", "t").returncode == 0
    r = _run("--root", ROOT, "--db", db, "gates", "lg", "--legacy", timeout=120)
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr


def test_run_command_works_end_to_end(tmp_path):
    """反例回归:`fw-core run` 曾因 main() 内局部 import 遮蔽模块级 Runner ⇒ UnboundLocalError。

    这条用例直接跑 CLI 的 run(脚本模型),确保"CLI 能真正驱动一次运行"。
    """
    root = str(tmp_path)
    db = str(tmp_path / "run.sqlite3")
    assert _run("--root", root, "--db", db, "init", "t", "--version", "1").returncode == 0
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t")
    r = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                        "run", "t", "--model", "scripted"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["state"] in ("finished", "paused")
    assert "UnboundLocalError" not in r.stderr


def test_resume_and_advance_cli_paths(tmp_path):
    """同一个 UnboundLocalError 也会挂 resume/advance 分支 ⇒ 两条都要有反例覆盖。"""
    root = str(tmp_path)
    db = str(tmp_path / "ra.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t")
    assert _run("--root", root, "--db", db, "init", "t", "--version", "1").returncode == 0

    r1 = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                         "run", "t", "--model", "scripted", "--steps", "1"],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r1.returncode == 0, r1.stderr[-500:]
    rid = json.loads(r1.stdout)["run_id"]

    r2 = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                         "resume", rid, "--model", "scripted", "--steps", "1"],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r2.returncode == 0, r2.stderr[-500:]
    assert "UnboundLocalError" not in r2.stderr

    r3 = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                         "advance", "t", "--model", "scripted", "--rounds", "1", "--steps", "1"],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r3.returncode == 0, r3.stderr[-500:]
    assert "UnboundLocalError" not in r3.stderr


def test_cli_watchdog_flags_exist_and_no_watchdog_runs(tmp_path):
    """正式路径默认开看护;`--no-watchdog` 必须能显式关掉(调试用)。"""
    root = str(tmp_path)
    db = str(tmp_path / "wd.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t")
    assert _run("--root", root, "--db", db, "init", "t", "--version", "1").returncode == 0
    r = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                        "run", "t", "--model", "scripted", "--steps", "1", "--no-watchdog"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr[-500:]
    # 帮助里必须出现默认开关(可发现性)
    h = subprocess.run([sys.executable, "-m", "fwcore", "run", "--help"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert "--no-watchdog" in h.stdout and "--watchdog-poll" in h.stdout
