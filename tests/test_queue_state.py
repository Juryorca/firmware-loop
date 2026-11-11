"""queue_state 必须只读 SQLite，不能退回 notes.md / Markdown 正则。"""

from __future__ import annotations

import os
import subprocess

from fwcore import db as _db
from fwcore.domain import Domain

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "tools", "queue_state.sh")


def _run(root: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, FW_LOOP_ROOT=root)
    return subprocess.run(["bash", SCRIPT, *args], check=False, cwd=root, env=env,
                          text=True, capture_output=True, timeout=20)


def _target(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return dom


def test_queue_state_reads_sqlite_without_notes(tmp_path):
    dom = _target(tmp_path)
    dom.add_hypothesis("t1", "待验证", body="b", source="test")
    dom.add_log("t1", "next", "下一步第一条", "从 SQLite 继续取队首")

    r = _run(str(tmp_path), "t1")
    assert r.returncode == 0, r.stderr
    assert "OPEN=1" in r.stdout
    assert "NEXT=从 SQLite 继续取队首" in r.stdout
    assert not os.path.exists(os.path.join(str(tmp_path), "state", "t1", "notes.md"))


def test_queue_state_flags_cost_only_stop_reason(tmp_path):
    dom = _target(tmp_path)
    dom.add_log("t1", "stop", "停止", "本轮: 降级: 成本太高")

    r = _run(str(tmp_path), "t1")
    assert r.returncode == 0, r.stderr
    assert "BAD=1" in r.stdout
