"""运行路径不读 Markdown:没有权威 DB 时必须 FAIL,不能静默回退。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "zz-nodb-runtime-probe"


def test_derived_check_fails_without_db():
    tdir = os.path.join(ROOT, "state", NAME)
    shutil.rmtree(tdir, ignore_errors=True)
    os.makedirs(tdir, exist_ok=True)
    try:
        with open(os.path.join(tdir, "notes.md"), "w", encoding="utf-8") as f:
            f.write("## 假设池\n- **H-01** 只有 Markdown,没有 DB\n")
        r = subprocess.run(["bash", "tools/derived_check.sh", NAME], cwd=ROOT,
                           capture_output=True, text=True, timeout=180)
        assert "缺权威状态" in r.stdout
        assert "=== derived_check: FAIL" in r.stdout
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def test_cli_gates_fails_for_unknown_target(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run([sys.executable, "-m", "fwcore", "--root", str(tmp_path),
                        "--db", db, "gates", "no-such-target"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2
    assert "目标不存在" in r.stderr


def _make_target(root):
    from fwcore import db as _db
    from fwcore.domain import Domain
    os.makedirs(os.path.join(root, "state", "t1"), exist_ok=True)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a", disposition="no_finding", note="逐条读过")
    return con


def test_derived_check_no_notes_uses_db(tmp_path):
    root = str(tmp_path)
    _make_target(root)
    r = subprocess.run(["bash", "tools/derived_check.sh", "t1"], cwd=ROOT,
                       env=dict(os.environ, FW_ROOT=root), capture_output=True,
                       text=True, timeout=180)
    assert "=== derived_check: PASS" in r.stdout
    assert "notes.md" not in r.stdout


def test_stale_notes_do_not_override_db(tmp_path):
    root = str(tmp_path)
    _make_target(root)
    with open(os.path.join(root, "state", "t1", "notes.md"), "w", encoding="utf-8") as f:
        f.write("## 假设池\n- **H-01** 旧 notes 说还有未处理项\n")
    r = subprocess.run(["bash", "tools/derived_check.sh", "t1"], cwd=ROOT,
                       env=dict(os.environ, FW_ROOT=root), capture_output=True,
                       text=True, timeout=180)
    assert "=== derived_check: PASS" in r.stdout
    assert "H-01" not in r.stdout.split("队列一致性")[-1]


def test_verify_does_not_compare_notes_sync():
    from pathlib import Path
    text = Path("tools/fwcore_verify.sh").read_text(encoding="utf-8")
    assert "migrate --all --verify" not in text
    assert "SQLite 状态库" in text


def test_legacy_markdown_runtime_files_absent():
    from pathlib import Path
    assert not Path("knowledge/vuln-patterns.md").exists()
    assert not Path("knowledge/misreports.md").exists()
    assert not Path("knowledge/research-knowledge.md").exists()
    assert not Path("tools/kb.py").exists()


def test_derived_check_closure_reads_db_not_notes(tmp_path):
    """收尾检查也必须读 DB；空状态应报 DB 收口阻塞，而不是缺 notes。"""
    root = str(tmp_path)
    _make_target(root)
    r = subprocess.run(["bash", "tools/derived_check.sh", "t1", "--closure"], cwd=ROOT,
                       env=dict(os.environ, FW_ROOT=root), capture_output=True,
                       text=True, timeout=180)
    assert "=== derived_check: FAIL" in r.stdout
    assert "没有任何假设" in r.stdout
    assert "notes.md" not in r.stdout


def test_derived_check_round_logs_are_per_record(tmp_path):
    root = str(tmp_path)
    con = _make_target(root)
    from fwcore.domain import Domain
    dom = Domain(con, root)
    dom.add_log("t1", "round", "轮 1",
                "本轮目标: H-01\n本轮: H-01 → no_finding + evidence")
    dom.add_log("t1", "round", "轮 2",
                "本轮目标: H-02\n本轮: H-02 → no_finding + evidence")
    r = subprocess.run(["bash", "tools/derived_check.sh", "t1", "--closure"], cwd=ROOT,
                       env=dict(os.environ, FW_ROOT=root), capture_output=True,
                       text=True, timeout=180)
    assert "2 条 round 记录逐条检查无异常" in r.stdout
    assert "轮结构: 最近" not in r.stdout


def test_derived_check_falls_through_empty_local_db(tmp_path):
    root = str(tmp_path)
    _make_target(root)
    from fwcore import db as _db
    local = os.path.join(root, "state", "t1", "state", "fwcore.sqlite3")
    _db.connect(local).close()          # local DB exists but has no target
    r = subprocess.run(["bash", "tools/derived_check.sh", "t1"], cwd=ROOT,
                       env=dict(os.environ, FW_ROOT=root), capture_output=True,
                       text=True, timeout=180)
    assert "=== derived_check: PASS" in r.stdout
