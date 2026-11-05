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
    ev = dom.add_evidence("t1", "http", capture="200 + admin", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "行为不同")
    from tests.helpers import verify
    verify(dom, ev, root)
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


def test_kb_counts_detects_mismatch(tmp_path):
    from fwcore.gates_db import kb_counts

    k = tmp_path / "knowledge"
    k.mkdir()
    (k / "INDEX.md").write_text("| `a.md` | x | 3 |\n", encoding="utf-8")
    (k / "a.md").write_text("## one\n## two\n", encoding="utf-8")
    rows = kb_counts(str(tmp_path))
    assert rows[0]["status"] == "mismatch" and rows[0]["declared"] == 3 and rows[0]["actual"] == 2


def test_dangling_attachment_refs(tmp_path):
    from fwcore.gates_db import dangling_attachment_refs

    con, dom, root = _mk(tmp_path)
    rdir = tmp_path / "state" / "t1" / "report"
    rdir.mkdir(parents=True)
    (rdir / "a.md").write_text("![x](attachments/pkg/a.bin)\n", encoding="utf-8")
    (rdir / "attachments" / "pkg").mkdir(parents=True)
    (rdir / "attachments" / "pkg" / "a.bin").write_bytes(b"x")
    assert dangling_attachment_refs(con, root, "t1") == []
    (rdir / "b.md").write_text("详见 attachments/缺/p.bin\n", encoding="utf-8")
    assert any("缺/p.bin" in b for b in dangling_attachment_refs(con, root, "t1"))


def test_legacy_compare_reports_unavailable_honestly(tmp_path):
    from fwcore.gates_db import legacy_compare

    con, dom, root = _mk(tmp_path)
    out = legacy_compare(con, root, "t1")
    assert out["available"] is False and "error" not in out


def test_evidence_version_mismatch_is_a_blocker(tmp_path):
    from fwcore.gates_db import evaluate

    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    dom.add_evidence("t1", "log", capture="旧版证据", attempt_id=aid, hypothesis_id=hid, version="0.9")
    rep = evaluate(con, root, "t1")
    assert rep.evidence_version_mismatch and any("版本" in b for b in rep.blockers)


def test_vulnerable_without_linked_evidence_is_blocker(tmp_path):
    from fwcore.gates_db import evaluate

    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t1", "越权")
    aid = dom.start_attempt(hid, "diff")
    ev = dom.add_evidence("t1", "http", capture="200", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    from tests.helpers import verify
    verify(dom, ev, root)
    dom.conclude(hid, "vulnerable", "仅 /api/x")
    con.execute("UPDATE evidence SET hypothesis_id=NULL")     # 模拟历史/外部写入导致的失联
    rep = evaluate(con, root, "t1")
    assert any("没有挂自己的证据" in b for b in rep.blockers)


def test_legacy_compare_against_real_script(tmp_path):
    """迁移期桥接:用**仓库里真实的** tools/derived_check.sh 跑一次,验证能拿到 summary。

    （合成目标不需要真实研究现场;脚本会如实报 FAIL,但 summary 必须存在且 exit 码可读）
    """
    import os as _os

    from fwcore import db as _db2
    from fwcore.domain import Domain as _D
    from fwcore.gates_db import legacy_compare

    repo = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    con = _db2.connect(_os.path.join(str(tmp_path), "l.sqlite3"))
    _D(con, repo).create_target("legacy-probe", version="t")
    out = legacy_compare(con, repo, "legacy-probe")
    assert out["available"] is True
    assert "returncode" in out and out["returncode"] in (0, 1)
    assert out["summary"].startswith("=== derived_check:"), out
