"""本轮修正的语义测试:三镜作用域 / 双输出 / 证据归属 / 失联判定 / 幂等 / 自动继续。

每条都对一个"看起来通过但语义错"的旧行为。
"""

from __future__ import annotations

import os
import time

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation, NotFound
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.research import Research
from fwcore.runtime import Runner


def _setup(tmp_path):
    con = _db.connect(str(tmp_path / "l.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    dom.create_target("t2", version="1")
    return con, dom, str(tmp_path)


def _runner(con, root, script, **kw):
    return Runner(con, root, ScriptedAdapter(script=script), policy=Policy(allow_high_risk=True), **kw)


FINAL = {"content": "FINAL: 本轮结束", "tool_calls": []}


# ---------- 三镜:必须按目标计轮,且暂停/恢复不算轮 ----------

def test_mirror_rounds_are_target_scoped(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "a")
    dom.add_hypothesis("t2", "b")
    r = _runner(con, root, [FINAL])
    for _ in range(3):
        r.run(r.start("t1"), "t1")
    res = Research(root, dom)
    assert res.rounds_since_mirror("t1") == 3
    assert res.rounds_since_mirror("t2") == 0        # 不串目标
    assert res.mirror_due("t1") is True
    assert res.mirror_due("t2") is False


def test_pause_and_resume_do_not_count_as_rounds(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "a")
    think = {"content": "想想", "tool_calls": []}
    r = _runner(con, root, [think, think], max_steps=1)   # 脚本里没有 FINAL ⇒ 两次都是预算耗尽
    rid = r.start("t1")
    r.run(rid, "t1")          # 预算耗尽 ⇒ paused
    r.resume(rid)             # 恢复 ⇒ 仍 paused
    assert Research(root, dom).rounds_since_mirror("t1") == 0

    r2 = _runner(con, root, [FINAL])
    r2.run(r2.start("t1"), "t1")
    assert Research(root, dom).rounds_since_mirror("t1") == 1


# ---------- 双输出:按 Attempt 交代,不能用"目标级有一条观察"糊过去 ----------

def test_dual_output_is_per_attempt(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_observation("t1", "前期某次随便看了下")           # 存在一条目标级观察
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "diff")
    dom.finish_attempt(aid, "inconclusive", "只跑了外层")
    assert any("双输出" in b for b in dom.closure_blockers("t1")), "决定性尝试没交代观察 ⇒ 必须阻塞"

    dom.note_observation_outcome(aid, "none", "外层路由先拒绝,结果在预期内")
    assert not any("双输出" in b for b in dom.closure_blockers("t1"))


def test_observation_outcome_new_requires_actual_observation(tmp_path):
    con, dom, root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "diff")
    with pytest.raises(InvariantViolation):
        dom.note_observation_outcome(aid, "new")            # 没登记观察就说 new
    dom.add_observation("t1", "错误包导致 500,不符合预期", attempt_id=aid)
    dom.note_observation_outcome(aid, "new")


def test_no_observation_requires_reason(tmp_path):
    con, dom, root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "diff")
    with pytest.raises(InvariantViolation):
        dom.note_observation_outcome(aid, "none", "x")


# ---------- 证据:ID 必须真实存在且归属正确 ----------

def test_finish_attempt_rejects_bogus_evidence_ids(tmp_path):
    con, dom, root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    with pytest.raises(NotFound):
        dom.finish_attempt(aid, "refuted", "没打进去", evidence=["E-不存在"])


def test_finish_attempt_rejects_foreign_evidence(tmp_path):
    con, dom, root = _setup(tmp_path)
    h1 = dom.add_hypothesis("t1", "a")
    a1 = dom.start_attempt(h1, "m")
    h2 = dom.add_hypothesis("t1", "b")
    a2 = dom.start_attempt(h2, "m")
    ev_other = dom.add_evidence("t1", "log", capture="别的尝试的证据", attempt_id=a2)
    with pytest.raises(InvariantViolation):
        dom.finish_attempt(a1, "refuted", "x", evidence=[ev_other])


def test_hypothesis_surface_link_must_exist(tmp_path):
    con, dom, root = _setup(tmp_path)
    with pytest.raises(InvariantViolation):
        dom.add_hypothesis("t1", "h", surface_key="不存在的面")
    dom.add_surface("t1", "12080 /x")
    hid = dom.add_hypothesis("t1", "h", surface_key="12080 /x")
    row = con.execute("SELECT surface_key FROM hypotheses WHERE id=?", (hid,)).fetchone()
    assert row["surface_key"] == "12080 /x"
    # 未归属的新方向允许
    assert dom.add_hypothesis("t1", "model 新提出的方向")


# ---------- 失联判定:不误伤活跃运行 ----------

def test_recover_stale_ignores_active_runs(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, [FINAL])
    rid = r.start("t1")                       # 刚启动 ⇒ 心跳新鲜、pid = 本进程
    assert r.recover_stale() == []
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "running"


def test_recover_stale_marks_old_heartbeat(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, [FINAL])
    rid = r.start("t1")
    con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    assert r.recover_stale(older_than_s=60) == [rid]
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"


def test_recover_stale_ignores_other_host(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, [FINAL])
    rid = r.start("t1")
    con.execute("UPDATE runs SET host='another-host', heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    assert r.recover_stale(older_than_s=1) == [rid]     # 心跳超期仍然算失联
    con.execute("UPDATE runs SET state='running', host='another-host', heartbeat_at=? WHERE id=?",
                (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), rid))
    assert r.recover_stale(older_than_s=60) == []        # 心跳新鲜 ⇒ 不动(别的主机无法查 pid)


# ---------- 幂等:同一 call_key 不重复副作用 ----------

def test_duplicate_tool_call_is_not_executed_twice(tmp_path):
    con, dom, root = _setup(tmp_path)
    call = {"id": "call_dup", "type": "function",
            "function": {"name": "state_add_hypothesis",
                         "arguments": '{"target":"t1","title":"幂等测试"}'}}
    r = _runner(con, root, [{"content": "", "tool_calls": [call]}, FINAL])
    rid = r.start("t1")
    r.run(rid, "t1")
    n = con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"]
    assert n == 1, "同一次调用不应产生两条假设"

    # 模拟"恢复时模型重发了同一调用" ⇒ 命中缓存,不再产生副作用
    from fwcore.tools import ToolContext
    ctx = ToolContext(domain=dom, root=root, run_id=rid, policy=Policy(allow_high_risk=True))
    out = r.registry.call(ctx, "state_add_hypothesis", {"target": "t1", "title": "幂等测试"},
                          call_key="call_dup")
    assert out.get("replayed") is True
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1


# ---------- 自动继续:有未收口项时不许直接交回 ----------

def test_advance_continues_until_closed(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_surface("t1", "12080 /x", reachability="pre-auth")
    hid = dom.add_hypothesis("t1", "签名前解析", surface_key="12080 /x")

    # 第一轮:FINAL,但还有未收口 ⇒ 不得 handback;第二轮把假设收口
    script = [
        FINAL,
        {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "state_start_attempt", "arguments": f'{{"hypothesis_id":"{hid}","method":"m"}}'}}]},
        {"content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {
            "name": "state_finish_attempt",
            "arguments": f'{{"attempt_id":"__AID__","result":"inconclusive","note":"只跑了外层",'
                         f'"obs_outcome":"none","obs_reason":"结果在预期内"}}'}}]},
        {"content": "", "tool_calls": [{"id": "c3", "type": "function", "function": {
            "name": "state_conclude",
            "arguments": f'{{"hypothesis_id":"{hid}","verdict":"inconclusive",'
                         f'"scope_note":"仅 12080 /x 这一条路径"}}'}}]},
        {"content": "", "tool_calls": [{"id": "c4", "type": "function", "function": {
            "name": "set_surface_judged", "arguments": '{"target":"t1","key":"12080 /x",'
                                                       '"disposition":"no_finding","note":"逐条读过鉴权门,无参数可达"}'}}]},
        FINAL,
    ]
    r = _runner(con, root, script, max_steps=10)
    res = r.advance("t1", "把 /x 推到定论", max_rounds=3)
    assert res.rounds >= 2, "有未收口项时必须自动继续到下一轮"
    # 该目标仍有材料缺失 ⇒ handback 不是 closed;但绝不是"说完 FINAL 就走"
    assert res.handback != "closed"
    assert res.state in ("finished", "paused")


def test_request_user_input_is_legit_handback(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "需要真机才能确认")
    script = [{"content": "", "tool_calls": [{"id": "u1", "type": "function", "function": {
        "name": "state_request_user_input",
        "arguments": '{"reason":"需要用户提供该型号的串口日志才能越过校验分支"}'}}]}, FINAL]
    r = _runner(con, root, script)
    res = r.advance("t1", "确认校验分支", max_rounds=2)
    assert res.handback and res.handback.startswith("needs_user_input")


# ---------- 授权:授权范围内自主运行,越界/外部动作仍需确认 ----------

def test_authorized_shell_runs_without_per_call_confirm(tmp_path):
    from fwcore.policy import Authz, Policy
    from fwcore.tools import ToolContext, default_registry
    con, dom, root = _setup(tmp_path)
    az = Authz(roots=[root], allow_shell=True, allow_high_risk=True, reason="研究授权")
    ctx = ToolContext(domain=dom, root=root, policy=Policy(authorization=az))
    out = default_registry().call(ctx, "shell_exec", {"command": "echo hi"})
    assert out["status"] == "ok", out


def test_revoked_or_expired_authorization_denies(tmp_path):
    import time as _t

    from fwcore.policy import Authz, Policy
    from fwcore.tools import ToolContext, default_registry
    con, dom, root = _setup(tmp_path)
    for az in (Authz(roots=[root], revoked=True, reason="用户撤回"),
               Authz(roots=[root], expires_at=_t.time() - 1, reason="过期")):
        ctx = ToolContext(domain=dom, root=root, policy=Policy(authorization=az))
        out = default_registry().call(ctx, "shell_exec", {"command": "echo hi"})
        assert out["status"] == "cancelled" and "授权" in out["error"]


def test_external_tool_always_needs_confirm(tmp_path):
    from fwcore.policy import Authz, Policy
    from fwcore.tools import Registry, ToolContext, ToolSpec
    con, dom, root = _setup(tmp_path)
    reg = Registry()
    reg.register(ToolSpec("submit_report", "公开提交(外部)", {"type": "object", "properties": {}},
                          lambda ctx, a: {"status": "ok"}, risk="low", external=True))
    az = Authz(roots=[root], allow_shell=True, allow_high_risk=True)
    ctx = ToolContext(domain=dom, root=root, policy=Policy(authorization=az))
    assert reg.call(ctx, "submit_report", {})["status"] == "cancelled"
    ctx2 = ToolContext(domain=dom, root=root, policy=Policy(authorization=az, confirm=lambda s: True))
    assert reg.call(ctx2, "submit_report", {})["status"] == "ok"
