"""Gate 权威状态版:必须只读 SQLite 事实(不依赖 notes.md 正则)。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.gates_db import evaluate, notes_vs_state


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="9")
    return con, dom, root


def test_coverage_and_closure_from_state_only(tmp_path):
    con, dom, root = _mk(tmp_path)
    dom.add_surface("t1", "/a", disposition="no_finding", note="逐条读过该入口,无参数可达")
    dom.add_surface("t1", "/b")                                   # unjudged
    hid = dom.add_hypothesis("t1", "h", surface_key="/a")
    aid = dom.start_attempt(hid, "m")
    dom.add_evidence("t1", "log", capture="打不进去", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "refuted", "拒绝")
    rep = evaluate(con, root, "t1")
    assert rep.coverage == "1/2"
    assert rep.surfaces.get("unjudged") == 1
    assert any("未判定" in b for b in rep.blockers)
    # 没有任何 notes.md 也能算(证明不依赖 Markdown)
    assert notes_vs_state(con, root, "t1")["notes_present"] is False


def test_findings_require_evidence(tmp_path):
    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t1", "越权")
    aid = dom.start_attempt(hid, "diff")
    dom.add_evidence("t1", "http", capture="200 + admin", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "行为不同")
    dom.conclude(hid, "vulnerable", "仅 /api/x")
    rep = evaluate(con, root, "t1")
    assert rep.findings and rep.findings[0]["ok"] is True and rep.findings[0]["evidence"] >= 1


def test_mirror_due_is_target_scoped(tmp_path):
    con, dom, root = _mk(tmp_path)
    t = dom.target("t1")
    for _ in range(3):
        con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES"
                    "(strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'round.ended',?,'{}')", (t.id,))
    con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES"
                "(strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'round.ended','other','{}')")
    rep = evaluate(con, root, "t1")
    assert rep.rounds_since_mirror == 3 and rep.mirror_due is True
