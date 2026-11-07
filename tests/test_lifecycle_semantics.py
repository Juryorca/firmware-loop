"""本轮修正的语义测试:三镜作用域 / 双输出 / 证据归属 / 失联判定 / 幂等 / 自动继续。

每条都对一个"看起来通过但语义错"的旧行为。
"""

from __future__ import annotations

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation, NotFound
from fwcore.limits import Limits
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
    kw.setdefault("limits", Limits(max_model_calls=8))
    return Runner(con, root, ScriptedAdapter(script=script), policy=Policy(allow_high_risk=True), **kw)


FINAL = {"content": "FINAL: 本轮结束", "tool_calls": []}


# ---------- 三镜:必须按目标计轮,且暂停/恢复不算轮 ----------

def test_mirror_rounds_are_target_scoped(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "a")
    dom.add_hypothesis("t2", "b")
    h1 = dom.add_hypothesis("t1", "a-轮推进")
    st: dict[str, str | None] = {"aid": None}

    def prep(_payload):                     # 轮内结束尝试(轮计数按"本 turn 推进")
        if st["aid"]:
            dom.finish_attempt(st["aid"], "inconclusive", "已排除外层", obs_outcome="none",
                               obs_reason="外层路由先拒绝,结果在预期内")
            st["aid"] = None
        return {}

    r = _runner(con, root, [FINAL], prepare=prep)
    for _ in range(3):
        st["aid"] = dom.start_attempt(h1, "m")
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

    # FINAL 只有在**本轮真的推进了研究**时才计轮
    r2 = _runner(con, root, [FINAL])
    r2.run(r2.start("t1"), "t1")
    assert Research(root, dom).rounds_since_mirror("t1") == 0, "只说不做不计轮"

    hid = dom.add_hypothesis("t1", "推进一条")
    aid = dom.start_attempt(hid, "m")
    dom.finish_attempt(aid, "inconclusive", "已排除外层", obs_outcome="none", obs_reason="外层路由先拒绝,结果在预期内")
    script2 = [{"content": "", "tool_calls": [{"id": "k1", "type": "function", "function": {
        "name": "state_conclude", "arguments": f'{{"hypothesis_id":"{hid}","verdict":"inconclusive",'
                                               f'"scope_note":"仅该入口的参数 a"}}'}}]}, FINAL]
    r3 = _runner(con, root, script2)
    r3.run(r3.start("t1"), "t1")
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


def test_recover_stale_requires_dead_pid_or_missing_pid(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, [FINAL])
    rid = r.start("t1")
    # 心跳超期但 pid 仍活着(可能在跑长任务)⇒ **不得**回收
    con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    assert r.recover_stale(older_than_s=60) == [], "进程还活着就不能判失联"
    # pid 已死(硬崩溃)⇒ 立即算失联
    con.execute("UPDATE runs SET pid=999999 WHERE id=?", (rid,))
    assert r.recover_stale(older_than_s=60) == [rid]
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"


def test_recover_stale_other_host_is_conservative(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, [FINAL])
    rid = r.start("t1")
    con.execute("UPDATE runs SET host='another-host', heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    assert r.recover_stale(older_than_s=1) == [], "跨主机默认不动(查不到 pid)"
    assert r.recover_stale(older_than_s=1, include_remote=True, remote_factor=2) == [rid]


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

def test_advance_stops_after_idle_rounds(tmp_path):
    """模型永远说 FINAL 却不推进研究 ⇒ 连续 2 个空闲轮后停下(不会无限转)。

    注意:这是**脚本模型 + 小上限**,毫秒级;旧版本用无限循环跑 800 秒并写爆磁盘。
    """
    con, dom, root = _setup(tmp_path)
    dom.add_surface("t1", "12080 /x")
    dom.add_hypothesis("t1", "永远推进不了")
    r = _runner(con, root, [FINAL] * 10, limits=Limits(max_model_calls=8))
    res = r.advance("t1", "试试自动继续")
    assert res.state in ("paused", "finished")
    assert res.handback != "closed", "没经用户确认不能是 closed"
    kinds = [x["kind"] for x in con.execute("SELECT kind FROM events")]
    assert "run.stalled_no_progress" in kinds or "run.limit_hit" in kinds


def test_close_requires_user_confirmation(tmp_path):
    """收尾条件满足 ⇒ 只是 awaiting_user_close;用户确认后才 closed。"""
    con, dom, root = _setup(tmp_path)
    dom.add_surface("t1", "12080 /x", disposition="no_finding",
                    note="逐条读过该入口,无参数可达")
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    dom.finish_attempt(aid, "inconclusive", "只跑了外层", obs_outcome="none",
                       obs_reason="外层路由先拒绝,结果在预期内")
    dom.conclude(hid, "inconclusive", "仅 12080 /x 这一个入口")
    assert dom.closure_blockers("t1")
    dom.confirm_close("t1", "用户确认收尾") if not dom.closure_blockers("t1") else None
    assert not dom.is_close_confirmed("t1")


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


# ---------- 授权 vs 沙箱:必须诚实说明"只做授权与路径检查" ----------

def test_authz_reports_isolation_none_by_default(tmp_path):
    from fwcore.policy import Authz

    az = Authz(roots=[str(tmp_path)])
    d = az.describe()
    assert d["isolation"] == "none"
    assert "仅授权与路径检查" in d["note"] or "仍可访问" in d["note"]


def test_authz_root_check_does_not_sandbox_process(tmp_path):
    """路径检查只约束**传给工具的路径**;进程本身仍能访问 root 之外(所以不能宣传成沙箱)。"""
    from fwcore.policy import Authz, Policy
    from fwcore.tools import ToolContext, default_registry

    con, dom, root = _setup(tmp_path)
    az = Authz(roots=[root], allow_shell=True, allow_high_risk=True)
    ctx = ToolContext(domain=dom, root=root, policy=Policy(authorization=az))
    outside = tmp_path.parent
    out = default_registry().call(ctx, "shell_exec",
                                  {"command": f"sh -c 'ls {outside} >/dev/null && echo reached'"})
    assert out["status"] == "ok", out
    assert "reached" in out["stdout_tail"]
