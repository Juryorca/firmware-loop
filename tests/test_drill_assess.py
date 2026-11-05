"""研究演练验收器:用合成数据库构造反例,证明不会误报 completed。"""

from __future__ import annotations

import importlib.util
import os

from fwcore import db as _db
from fwcore.domain import Domain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "research_drill", os.path.join(ROOT, "scripts", "research_drill.py"))
if _spec is None or _spec.loader is None:
    raise RuntimeError("无法加载 scripts/research_drill.py")
drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drill)


def _db_setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("drill", version="synthetic-1")
    con.execute("INSERT INTO runs(id,target_id,state,step,rounds,pid,host,heartbeat_at,"
                "started_at,updated_at) VALUES('R1',?,'paused',5,0,1,'h','2026-01-01',"
                "'2026-01-01','2026-01-01')", (dom.target("drill").id,))
    return con, dom, root


def _stdout(root, name, text):
    p = os.path.join(root, "state", "artifacts", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _call(con, cid, args, stdout_path, status="ok"):
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES(?, 'R1','shell_exec',?,?,?,'2026-01-01T00:00:00+00:00')",
                (cid, args, status, stdout_path))


def _hyp(dom, title):
    hid = dom.add_hypothesis("drill", title, body="b", source="[RE]")
    return hid


def _evidence(con, eid, hid, att, path, tc_id, notes=None):
    con.execute("INSERT INTO evidence(id,target_id,hypothesis_id,attempt_id,kind,path,"
                "tool_call_id,verified,notes,created_at) VALUES(?,?,?,?,'log',?,?,0,?,"
                "'2026-01-01T00:00:00+00:00')",
                (eid, con.execute("SELECT id FROM targets").fetchone()[0], hid, att, path,
                 tc_id, notes))


def _check(con, eid, method):
    con.execute("INSERT INTO evidence_checks(id,evidence_id,method,created_at) VALUES(?,?,?,?)",
                (f"EC-{eid}", eid, method, "2026-01-01T00:00:00+00:00"))
    con.execute("UPDATE evidence SET verified=1 WHERE id=?", (eid,))


def test_payload_in_args_but_no_execution_is_not_proof(tmp_path):
    """命令里有分号但产物没变化 ⇒ 不能判"真实执行"。"""
    con, dom, root = _db_setup(tmp_path)
    benign = _stdout(root, "b.txt", "status: name=x\n")
    same = _stdout(root, "p.txt", "status: name=x\n")     # 与良性一致 ⇒ 注入没生效
    _call(con, "TC1", "bash -lc 'QUERY_STRING=name=x sh status.cgi'", benign)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", same)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    out = drill.assess(con, root)
    assert out["checks"]["danger_executed_with_real_payload"]["ok"] is False
    assert out["status"] != "completed"


def test_marker_in_stdout_counts_as_real_execution(tmp_path):
    con, dom, root = _db_setup(tmp_path)
    benign = _stdout(root, "b.txt", "status: name=x\n")
    hit = _stdout(root, "p.txt", "status: name=x\nuid=0(root) gid=0(root)\n")
    _call(con, "TC1", "bash -lc 'QUERY_STRING=name=x sh status.cgi'", benign)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", hit)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    out = drill.assess(con, root)
    assert out["checks"]["danger_executed_with_real_payload"]["ok"] is True


def test_weak_link_does_not_satisfy_direct_evidence(tmp_path):
    """证据 tool_call_id 指向调用,但路径不是该调用的产物 ⇒ 不算直接证据。"""
    con, dom, root = _db_setup(tmp_path)
    b = _stdout(root, "b.txt", "x\n")
    h = _stdout(root, "p.txt", "uid=0\n")
    _call(con, "TC1", "bash -lc 'sh status.cgi'", b)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", h)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    other = _stdout(root, "other.txt", "无关输出")     # 不是 TC2 的产物
    _evidence(con, "E1", hid, att, other, "TC2")
    _check(con, "E1", "cross-check")
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    out = drill.assess(con, root)
    assert out["checks"]["decisive_attempts_have_direct_evidence"]["ok"] is False


def test_direct_evidence_passes(tmp_path):
    con, dom, root = _db_setup(tmp_path)
    b = _stdout(root, "b.txt", "x\n")
    h = _stdout(root, "p.txt", "uid=0\n")
    _call(con, "TC1", "bash -lc 'sh status.cgi'", b)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", h)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    _evidence(con, "E1", hid, att, h, "TC2")
    _check(con, "E1", "cross-check")
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    out = drill.assess(con, root)
    assert out["checks"]["decisive_attempts_have_direct_evidence"]["ok"] is True


def test_scope_requires_every_profile_object(tmp_path):
    """只做了一个对象的结论(危险)⇒ 范围未完成,不能 completed。"""
    con, dom, root = _db_setup(tmp_path)
    b = _stdout(root, "b.txt", "x\n")
    h = _stdout(root, "p.txt", "uid=0\n")
    _call(con, "TC1", "bash -lc 'sh status.cgi'", b)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", h)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    _evidence(con, "E1", hid, att, h, "TC2")
    _check(con, "E1", "cross-check")
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(hid, "vulnerable", "仅 status.cgi")
    out = drill.assess(con, root)
    assert out["checks"]["scope_conclusions_complete"]["ok"] is False
    assert out["status"] == "partial"


def test_vulnerable_without_causal_check_is_failed(tmp_path):
    con, dom, root = _db_setup(tmp_path)
    hid = _hyp(dom, "status.cgi 命令注入")
    att = dom.start_attempt(hid, "poc")
    ev = dom.add_evidence("drill", "log", capture="看到输出", attempt_id=att, hypothesis_id=hid)
    _check(con, ev, "recheck-sha256")          # 只有完整性复核
    dom.finish_attempt(att, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    # 绕过 domain 直接写"坏结论",模拟导入/历史数据 —— 验收器必须抓这种
    con.execute("INSERT INTO conclusions(id,target_id,hypothesis_id,verdict,scope_note,created_at)"
                " VALUES('C1',(SELECT id FROM targets),?,'vulnerable','仅 status.cgi',"
                "'2026-01-01T00:00:00+00:00')", (hid,))
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable',"
                " scope_note='仅 status.cgi' WHERE id=?", (hid,))
    out = drill.assess(con, root)
    assert out["checks"]["vulnerable_evidence_causal"]["ok"] is False
    assert out["status"] == "failed"


def test_scope_complete_but_run_paused_is_partial(tmp_path):
    """范围内结论都形成了,但 run 因预算暂停(没有 FINAL)⇒ partial,不混写为 completed。"""
    con, dom, root = _db_setup(tmp_path)
    b = _stdout(root, "b.txt", "x\n")
    h = _stdout(root, "p.txt", "uid=0\n")
    ctl = _stdout(root, "c.txt", "hello alice\n")
    _call(con, "TC1", "bash -lc 'sh status.cgi'", b)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", h)
    _call(con, "TC3", "bash -lc 'QUERY_STRING=\"user=alice\" sh login.cgi'", ctl)
    _call(con, "TC4", "bash -lc 'QUERY_STRING=\"x;id\" sh login.cgi'", ctl)
    _call(con, "TC5", "bash -lc 'QUERY_STRING=\"$(id)\" sh login.cgi'", ctl)
    h1 = _hyp(dom, "status.cgi 命令注入")
    a1 = dom.start_attempt(h1, "poc")
    _evidence(con, "E1", h1, a1, h, "TC2")
    _check(con, "E1", "cross-check")
    dom.finish_attempt(a1, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(h1, "vulnerable", "仅 status.cgi")
    h2 = _hyp(dom, "login.cgi 参数注入")
    a2 = dom.start_attempt(h2, "反证")
    _evidence(con, "E2", h2, a2, ctl, "TC4")
    _check(con, "E2", "cross-check")
    dom.finish_attempt(a2, "refuted", "载荷都没绕过", evidence=["E2"],
                       obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(h2, "no_finding", "仅 login.cgi")
    out = drill.assess(con, root)
    assert out["checks"]["scope_conclusions_complete"]["ok"] is True
    assert out["checks"]["run_ended_normally"]["ok"] is False
    assert out["status"] == "partial"
    assert "预算暂停" in (out["run"]["note"] or "")


def test_completed_requires_finished_run(tmp_path):
    con, dom, root = _db_setup(tmp_path)
    con.execute("UPDATE runs SET state='finished'")
    b = _stdout(root, "b.txt", "x\n")
    h = _stdout(root, "p.txt", "uid=0\n")
    ctl = _stdout(root, "c.txt", "hello alice\n")
    _call(con, "TC1", "bash -lc 'sh status.cgi'", b)
    _call(con, "TC2", "bash -lc 'QUERY_STRING=\"x;id\" sh status.cgi'", h)
    _call(con, "TC3", "bash -lc 'QUERY_STRING=\"user=alice\" sh login.cgi'", ctl)
    _call(con, "TC4", "bash -lc 'QUERY_STRING=\"x;id\" sh login.cgi'", ctl)
    _call(con, "TC5", "bash -lc 'QUERY_STRING=\"$(id)\" sh login.cgi'", ctl)
    h1 = _hyp(dom, "status.cgi 命令注入")
    a1 = dom.start_attempt(h1, "poc")
    _evidence(con, "E1", h1, a1, h, "TC2")
    _check(con, "E1", "cross-check")
    dom.finish_attempt(a1, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(h1, "vulnerable", "仅 status.cgi")
    h2 = _hyp(dom, "login.cgi 参数注入")
    a2 = dom.start_attempt(h2, "反证")
    _evidence(con, "E2", h2, a2, ctl, "TC4")
    _check(con, "E2", "cross-check")
    dom.finish_attempt(a2, "refuted", "载荷都没绕过", evidence=["E2"],
                       obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(h2, "no_finding", "仅 login.cgi")
    out = drill.assess(con, root)
    assert out["status"] == "completed", out
