"""证据复核语义:"声称已验证"不算,必须有可检查、可审计、可复现的复核记录。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation, NotFound
from tests.helpers import artifact


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="5.0")
    return con, dom, root


def _evidence_with_file(dom, root):
    rel = artifact(root, "poc.log", "sigsegv at 0x41414141")
    hid = dom.add_hypothesis("t1", "参数 a 溢出")
    aid = dom.start_attempt(hid, "poc")
    ev = dom.add_evidence("t1", "poc_log", path=rel, attempt_id=aid, hypothesis_id=hid)
    return hid, aid, ev, rel


def test_recheck_sha256_proves_artifact_unchanged(tmp_path):
    con, dom, root = _setup(tmp_path)
    _hid, _aid, ev, _rel = _evidence_with_file(dom, root)
    dom.verify_evidence(ev, "recheck-sha256")
    st = dom.evidence_status(ev)
    assert st["verified"] is True
    assert [c["method"] for c in st["checks"]] == ["recheck-sha256"]
    kinds = {r["kind"] for r in con.execute("SELECT kind FROM events")}
    assert "evidence.verified" in kinds


def test_recheck_sha256_rejects_tampered_artifact(tmp_path):
    con, dom, root = _setup(tmp_path)
    _hid, _aid, ev, rel = _evidence_with_file(dom, root)
    with open(os.path.join(root, rel), "a", encoding="utf-8") as f:
        f.write("\n(后来改过)")
    with pytest.raises(InvariantViolation) as e:
        dom.verify_evidence(ev, "recheck-sha256")
    assert "哈希不匹配" in str(e.value)
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 0


def test_text_only_claim_cannot_verify(tmp_path):
    con, dom, _root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    ev = dom.add_evidence("t1", "log", capture="我看到了崩溃", attempt_id=aid, hypothesis_id=hid)
    with pytest.raises(InvariantViolation):
        dom.promote_evidence(ev, "我确认过了,真的崩了")          # 只写文字
    with pytest.raises(InvariantViolation):
        dom.verify_evidence(ev, "cross-check", artifact=None, detail="我看过了真的")
    with pytest.raises(NotFound):
        dom.verify_evidence(ev, "cross-check", artifact="state/artifacts/nope.txt",
                            detail="对比了崩溃栈与源码")
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 0


def test_cross_check_needs_artifact_and_detail(tmp_path):
    _con, dom, root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    ev = dom.add_evidence("t1", "log", capture="x", attempt_id=aid, hypothesis_id=hid)
    art = artifact(root, "cross.txt", "对照记录")
    with pytest.raises(InvariantViolation):
        dom.verify_evidence(ev, "cross-check", artifact=art, detail="短")
    dom.verify_evidence(ev, "cross-check", artifact=art, detail="与反编译结果对照,边界值一致")
    assert dom.evidence_status(ev)["verified"] is True


def test_reproduce_attempt_requires_supported_same_hypothesis(tmp_path):
    con, dom, root = _setup(tmp_path)
    hid, aid, ev, _rel = _evidence_with_file(dom, root)
    refuted = dom.start_attempt(hid, "重试")
    dom.add_evidence("t1", "log", capture="这次没崩", attempt_id=refuted, hypothesis_id=hid)
    dom.finish_attempt(refuted, "refuted", "第二次没复现", evidence=[
        con.execute("SELECT id FROM evidence WHERE attempt_id=?", (refuted,)).fetchone()["id"]])
    with pytest.raises(InvariantViolation) as e:
        dom.verify_evidence(ev, "reproduce-attempt", attempt_id=refuted)
    assert "supported" in str(e.value)

    dom.finish_attempt(aid, "supported", "复现成功")
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    assert dom.evidence_status(ev)["verified"] is True


def test_unknown_method_is_rejected(tmp_path):
    _con, dom, root = _setup(tmp_path)
    _hid, _aid, ev, _rel = _evidence_with_file(dom, root)
    with pytest.raises(InvariantViolation):
        dom.verify_evidence(ev, "looks-good-to-me")


def test_cli_verify_and_status_are_real_path(tmp_path):
    """真实 CLI 路径:add → verify(recheck-sha256) → status 能看到检查记录。"""
    import json
    import subprocess
    import sys

    root = str(tmp_path)
    db = os.path.join(root, "state", "fwcore.sqlite3")
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def cli(*a):
        return subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db, *a],
                              capture_output=True, text=True, timeout=120, env=env, check=False)

    assert cli("init", "t1", "--version", "5.0").returncode == 0
    hid = cli("hyp", "add", "t1", "参数溢出").stdout.strip()      # 输出是 id 文本
    aid = cli("attempt", "start", hid, "poc").stdout.strip()
    os.makedirs(os.path.join(root, "state", "artifacts"), exist_ok=True)
    with open(os.path.join(root, "state", "artifacts", "p.log"), "w", encoding="utf-8") as f:
        f.write("sigsegv")
    ev = cli("evidence", "add", "t1", "--kind", "poc_log",
             "--path", "state/artifacts/p.log", "--attempt", aid, "--hypothesis", hid).stdout.strip()
    assert json.loads(cli("evidence", "status", ev).stdout)["verified"] is False
    r = cli("evidence", "verify", ev, "--method", "recheck-sha256")
    assert r.returncode == 0, r.stderr[-400:]
    st = json.loads(cli("evidence", "status", ev).stdout)
    assert st["verified"] is True and st["checks"][0]["method"] == "recheck-sha256"


def test_hash_only_verification_cannot_support_vulnerable(tmp_path):
    """只做 recheck-sha256(产物完整性)⇒ 不能给 vulnerable 结论当证明。"""
    _con, dom, root = _setup(tmp_path)
    hid, aid, ev, _rel = _evidence_with_file(dom, root)
    dom.finish_attempt(aid, "supported", "复现成功", obs_outcome="none",
                       obs_reason="与预期一致,无新观察")
    dom.verify_evidence(ev, "recheck-sha256")
    assert dom.evidence_status(ev)["verified"] is True
    with pytest.raises(InvariantViolation) as e:
        dom.conclude(hid, "vulnerable", "仅 /cgi-bin/x 的参数 a")
    assert "完整性" in str(e.value)
    sup = dom.conclusion_support(hid)
    assert sup["integrity_only"] is True and sup["integrity"] == ["recheck-sha256"]

    # 用因果复核补上(关联 supported 复现)⇒ 才能下 vulnerable
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    assert dom.conclusion_support(hid)["causal"] == ["reproduce-attempt"]
    assert dom.conclude(hid, "vulnerable", "仅 /cgi-bin/x 的参数 a")


def test_gates_flag_integrity_only_vulnerable(tmp_path):
    from fwcore.gates_db import evaluate

    con, dom, root = _setup(tmp_path)
    hid, aid, ev, _rel = _evidence_with_file(dom, root)
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.verify_evidence(ev, "recheck-sha256")
    # 绕过 domain 直接写结论,模拟导入/历史数据 ⇒ gates 必须报出来
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable', scope_note='仅 parse()'"
                " WHERE id=?", (hid,))
    rep = evaluate(con, root, "t1")
    assert any("完整性" in b for b in rep.blockers), rep.blockers
