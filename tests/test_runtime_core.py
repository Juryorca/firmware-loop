"""工具执行状态 + 运行时恢复 + 迁移保真的测试。"""

from __future__ import annotations

import json
import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.migrate_all import migrate_target, parse_pool, verify_target
from fwcore.models import Reply
from fwcore.policy import Policy
from fwcore.runtime import Runner, ScriptedModel
from fwcore.tools import ToolContext, default_registry


@pytest.fixture()
def setup(tmp_path):
    con = _db.connect(str(tmp_path / "s.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="2.0")
    return con, dom, str(tmp_path)


# ---------------- 工具真实状态 ----------------

def test_shell_exec_records_statuses(setup):
    con, dom, root = setup
    reg = default_registry()
    ctx = ToolContext(domain=dom, root=root, artifacts_dir=os.path.join(root, "art"),
                      policy=Policy(allow_high_risk=True))

    ok = reg.call(ctx, "shell_exec", {"command": "true"})
    assert ok["status"] == "ok" and ok["exit_code"] == 0
    err = reg.call(ctx, "shell_exec", {"command": "false"})
    assert err["status"] == "error" and err["exit_code"] == 1
    tmo = reg.call(ctx, "shell_exec", {"command": "sleep 5", "timeout": 0.3})
    assert tmo["status"] == "timeout"
    # 三种状态都落库,且带 sha256/落盘路径
    rows = con.execute("SELECT status FROM tool_calls ORDER BY created_at").fetchall()
    assert {r["status"] for r in rows} == {"ok", "error", "timeout"}
    assert "stdout_path" not in ok and ok["stdout_tail"] == ""


def test_high_risk_tool_requires_confirmation(setup):
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, artifacts_dir=os.path.join(root, "art"),
                      policy=Policy(confirm=lambda _s: False))
    out = default_registry().call(ctx, "shell_exec", {"command": "echo hi"})
    assert out["status"] == "cancelled"


def test_high_risk_denied_by_default(setup):
    """没有 confirm、也没有 allow_high_risk ⇒ 默认拒绝(不能悄悄放行)。"""
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, policy=Policy())
    out = default_registry().call(ctx, "shell_exec", {"command": "echo hi"})
    assert out["status"] == "cancelled" and "确认" in out["error"]


def test_args_validated_before_handler(setup):
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True))
    out = default_registry().call(ctx, "fs_read", {})            # 缺必填
    assert out["status"] == "error" and "必填" in out["error"]
    out2 = default_registry().call(ctx, "fs_read", {"path": "x", "bogus": 1})  # 未知参数
    assert out2["status"] == "error" and "未知参数" in out2["error"]


def test_path_escape_blocked(setup, tmp_path):
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True))
    out = default_registry().call(ctx, "fs_read", {"path": "../../../../etc/passwd"})
    assert out["status"] == "error" and "越界" in out["error"] or "不存在" in out["error"]


def test_timeout_is_clamped(setup):
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True, max_timeout=1))
    t0 = __import__("time").time()
    out = default_registry().call(ctx, "shell_exec", {"command": "sleep 5", "timeout": 999})
    assert out["status"] == "timeout" and __import__("time").time() - t0 < 4


def test_unknown_tool(setup):
    _con, dom, root = setup
    out = default_registry().call(ToolContext(domain=dom, root=root), "nope", {})
    assert out["status"] == "error"


# ---------------- 运行时:检查点与恢复 ----------------

def _runner(setup, script, steps=10):
    con, dom, root = setup
    return con, dom, root, Runner(con, root, ScriptedModel(script=script), max_steps=steps)


def test_run_executes_tool_calls_and_finishes(setup):
    script = [
        {"content": "登记假设", "tool_calls": [{"name": "state_add_hypothesis",
                                          "args": {"target": "t1", "title": "解析器越界"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]
    con, _dom, _root, r = _runner(setup, script)
    rid = r.start("t1", goal="smoke")
    res = r.run(rid, "t1", goal="smoke")
    assert res["state"] == "finished" and res["final"].startswith("FINAL:")
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"] >= 2


class Boom(Exception):
    pass


class ExplodingModel:
    name = "exploding"

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools) -> Reply:
        self.n += 1
        if self.n == 1:
            return Reply(content="先看看状态", tool_calls=[])
        raise Boom("模拟进程中断")


def test_interruption_pauses_and_keeps_hypothesis_state(setup):
    """进程中断 = 运行事件,不是研究结论;假设状态不得被改动。"""
    con, dom, root = setup
    hid = dom.add_hypothesis("t1", "授权缺陷")
    r = Runner(con, root, ExplodingModel(), max_steps=10)
    rid = r.start("t1")
    with pytest.raises(Boom):
        r.run(rid, "t1")
    run = con.execute("SELECT state,step FROM runs WHERE id=?", (rid,)).fetchone()
    assert run["state"] == "paused"
    assert con.execute("SELECT state FROM hypotheses WHERE id=?", (hid,)).fetchone()["state"] == "open"
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"] >= 1


def test_resume_continues_from_checkpoint(setup):
    con, _dom, _root, r = _runner(setup, [
        {"content": "一步", "tool_calls": []},
        {"content": "FINAL: done", "tool_calls": []},
    ], steps=10)
    rid = r.start("t1")
    r.run(rid, "t1")
    step_before = con.execute("SELECT step FROM runs WHERE id=?", (rid,)).fetchone()["step"]
    res = r.resume(rid)
    assert res["state"] == "finished"
    assert con.execute("SELECT step FROM runs WHERE id=?", (rid,)).fetchone()["step"] >= step_before


def test_step_limit_pauses_not_concludes(setup):
    _con, _dom, _root, r = _runner(setup, [{"content": "循环", "tool_calls": []}] * 5, steps=2)
    rid = r.start("t1")
    res = r.run(rid, "t1")
    assert res["state"] == "paused" and res["steps"] == 2


# ---------------- 迁移:不丢信息、幂等、只映射显式状态 ----------------

NOTES = """# t1

## 假设池(唯一队列)

- **H-01** 未认证可读配置 | 状态: **未处理**
- **H-02** 命令注入 | 状态: **定论:No finding**(判据: 逐条读过)
- **H-03** 待定的口令门 | 状态: 部分完成

## §1 轮

本轮目标: H-01
"""


def test_migrate_is_lossless_and_idempotent(setup, tmp_path):
    con, _dom, root = setup
    p = tmp_path / "state" / "t1" / "notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTES, encoding="utf-8")

    r1 = migrate_target(con, root, "t1")
    assert r1["stats"]["documents"] == 1 and r1["stats"]["hypotheses"] == 3
    assert verify_target(con, root, "t1")["ok"]
    r2 = migrate_target(con, root, "t1")                  # 幂等
    assert sum(v for k, v in r2["stats"].items() if k != "target") == 0
    assert con.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 1


def test_migration_maps_only_explicit_status(setup, tmp_path):
    """显式"定论:No finding"⇒ concluded;模糊"部分完成"⇒ 仍 open + needs_review。"""
    con, _dom, root = setup
    p = tmp_path / "state" / "t1" / "notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTES, encoding="utf-8")
    migrate_target(con, root, "t1")
    rows = list(con.execute("SELECT title,state,verdict FROM hypotheses"))
    assert any(r["state"] == "open" for r in rows)                      # H-01/H-03
    assert any(r["state"] == "concluded" and r["verdict"] == "no_finding" for r in rows)
    assert con.execute("SELECT COUNT(*) n FROM research_logs"
                       " WHERE kind='needs_review'").fetchone()["n"] >= 1


def test_pool_dedupes_same_id_across_blocks():
    text = ("## 假设池(唯一队列)\n- **H-01** a\n  状态: 未处理\n\n"
            "## 旧池\n- **H-01** a\n  状态: 未处理\n")
    pool, _conflicts = parse_pool(text)
    assert sum(1 for e in pool if e["raw_id"] == "H-01") == 1



def test_tool_names_match_openai_pattern(tmp_path):
    """真实端点要求 ^[a-zA-Z0-9_-]+$;带点号会被 400 拒绝(真实踩过)。"""
    import re
    con = _db.connect(str(tmp_path / "n.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1")
    reg = default_registry()
    for t in reg.describe():
        assert re.match(r"^[A-Za-z0-9_-]{1,64}$", t["name"]), t["name"]
    with pytest.raises(ValueError):
        reg.register(__import__("fwcore.tools", fromlist=["ToolSpec"]).ToolSpec(
            "bad.name", "d", {"type": "object", "properties": {}}, lambda c, a: {}))


def test_resume_reowns_run_and_clears_stale_handback(setup):
    """resume 必须接管 owner(pid+starttime)并清掉旧 handback,否则看护会误判 owner_dead 立刻暂停。"""
    from fwcore.watchdog import _proc_starttime

    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    with _db.Tx(con) as c:
        c.execute("UPDATE runs SET state='paused', pid=999999, pid_starttime=1,"
                  " handback_reason='watchdog_paused: owner_dead' WHERE id=?", (rid,))
    r2 = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: y", "tool_calls": []}]),
                policy=Policy(allow_high_risk=True))
    r2.resume(rid)
    row = con.execute("SELECT pid,pid_starttime,handback_reason FROM runs WHERE id=?",
                      (rid,)).fetchone()
    assert row["pid"] == os.getpid()
    assert row["pid_starttime"] == _proc_starttime(os.getpid())
    assert not (row["handback_reason"] or "").startswith("watchdog_paused")


def test_resume_refuses_live_owner(setup):
    """同一 run 已有活主(另一个进程)时,resume 必须拒绝 —— 否则双跑=双倍付费+重复副作用。"""
    import subprocess

    from fwcore.runtime import _host, _proc_starttime

    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    child = subprocess.Popen(["sleep", "30"])
    try:
        con.execute("UPDATE runs SET state='running', pid=?, pid_starttime=?, host=?,"
                    " heartbeat_at=? WHERE id=?",
                    (child.pid, _proc_starttime(child.pid), _host(),
                     "2099-01-01T00:00:00+00:00", rid))
        r2 = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: y", "tool_calls": []}]),
                    policy=Policy(allow_high_risk=True))
        with pytest.raises(RuntimeError):
            r2.resume(rid)
    finally:
        child.kill()


def test_owner_loss_mid_turn_does_not_overwrite(setup):
    """模型调用期间 run 被别的进程接管 ⇒ 旧 owner 不得写 state/checkpoint。"""
    con, _dom, root = setup
    rid_box = {}

    class Steal(ScriptedModel):
        def chat(self, messages, tools):
            rep = super().chat(messages, tools)
            if rid_box.get("done") is None:
                con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?",
                            (rid_box["rid"],))
                rid_box["done"] = True
            return rep

    r = Runner(con, root, Steal(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    rid_box["rid"] = rid
    res = r.run(rid, "t1")
    row = con.execute("SELECT pid,state FROM runs WHERE id=?", (rid,)).fetchone()
    assert res.handback == "owner_lost"
    assert row["pid"] == 999999 and row["state"] != "finished"


def test_cancel_finished_run_keeps_finished(setup):
    """cancel 与 FINAL 竞争:cancel 不能把已 finished 的 run 改回 paused。"""
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    r.cancel(rid, "迟到的取消")
    row = con.execute("SELECT state,handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "finished"
    assert not (row["handback_reason"] or "").startswith("user_stop")


def test_resume_kills_orphan_tool_pgids_of_dead_owner(setup):
    """接管死亡 owner 的 run 时,必须清掉它遗留的工具进程组(否则重复副作用/孤儿进程)。"""
    import subprocess
    import time

    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        from fwcore.watchdog import _proc_starttime as _pst
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)} {_pst(child.pid) or 0}\n")
        con.execute("UPDATE runs SET state='paused', pid=999999, pid_starttime=1,"
                    " handback_reason='watchdog_paused: owner_dead' WHERE id=?", (rid,))
        r2 = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: y", "tool_calls": []}]),
                    policy=Policy(allow_high_risk=True))
        r2.resume(rid)
        time.sleep(0.3)
        assert child.poll() is not None, "接管死亡 owner 后必须杀掉孤儿进程组"
    finally:
        child.kill()


def test_heartbeat_does_not_steal_ownership(setup):
    """B 的下一轮心跳不能用无条件 UPDATE 把 owner 抢回来(否则两进程来回抢)。"""
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[]), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (rid,))
    assert r._heartbeat(rid) is False
    row = con.execute("SELECT pid,pid_starttime FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["pid"] == 999999 and row["pid_starttime"] == 1


def test_owner_loss_cancels_remaining_tool_batch(setup):
    """模型调用期间被接管:同一回复里的后续工具(尤其 state_* 写操作)不得执行。"""
    con, _dom, root = setup
    box = {}

    class Steal:
        name = "steal"

        def chat(self, messages, tools):
            con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (box["rid"],))
            return Reply(content="", tool_calls=[
                {"id": "c1", "name": "state_add_observation",
                 "args": {"target": "t1", "text": "第一条"}},
                {"id": "c2", "name": "state_add_observation",
                 "args": {"target": "t1", "text": "第二条"}},
            ])

    r = Runner(con, root, Steal(), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    box["rid"] = rid
    res = r.run(rid, "t1")
    assert res.handback == "owner_lost"
    assert con.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"] == 0
    assert con.execute("SELECT pid FROM runs WHERE id=?", (rid,)).fetchone()["pid"] == 999999


def test_request_user_input_ignored_on_finished_run(setup):
    """迟到的'请求用户输入'不能改已结束 run 的 handback。"""
    con, dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    before = con.execute("SELECT handback_reason FROM runs WHERE id=?", (rid,)).fetchone()[0]
    dom.request_user_input(rid, "需要工作区外的东西")
    after = con.execute("SELECT handback_reason FROM runs WHERE id=?", (rid,)).fetchone()[0]
    assert after == before


def test_resume_cancels_stale_running_tool_calls(setup):
    """崩溃 owner 留下的 running 工具行:接管时必须标 cancelled(否则状态永远失真/重放被挡)。"""
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at)"
                " VALUES('TC-stale',?,'shell_exec','{}','running','2026-01-01')", (rid,))
    con.execute("UPDATE runs SET state='paused', pid=999999, pid_starttime=1 WHERE id=?", (rid,))
    r2 = Runner(con, root, ScriptedModel(script=[{"content": "FINAL: y", "tool_calls": []}]),
                policy=Policy(allow_high_risk=True))
    r2.resume(rid)
    st = con.execute("SELECT status FROM tool_calls WHERE id='TC-stale'").fetchone()[0]
    assert st == "cancelled"


def test_stale_awaiting_handback_is_not_reported_for_paused_run(setup):
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[]), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    con.execute("UPDATE runs SET state='paused', handback_reason='awaiting_user_close' WHERE id=?",
                (rid,))
    assert r._handback(rid, "paused", []) is None
    con.execute("UPDATE runs SET state='finished' WHERE id=?", (rid,))
    assert r._handback(rid, "finished", []) == "awaiting_user_close"


def test_finish_state_does_not_override_external_pause(setup):
    """取消/看护已把 run 暂停后,迟到的 finished 不能覆盖 paused/handback。"""
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[]), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    con.execute("UPDATE runs SET state='paused', handback_reason='user_stop: x' WHERE id=?", (rid,))
    res = r._finish_state(rid, "t1", "finished", 1, 0, "FINAL: x", [])
    row = con.execute("SELECT state,handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "paused" and row["handback_reason"] == "user_stop: x"
    assert res.state == "paused" and res.handback == "user_stop: x"


def test_limit_hit_does_not_override_external_pause(setup):
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[]), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    con.execute("UPDATE runs SET state='paused', handback_reason='user_stop: x' WHERE id=?", (rid,))
    r._limit_hit(rid, "max_model_calls", {"calls": 1, "limit": 1})
    row = con.execute("SELECT state,handback_reason,budget_note FROM runs WHERE id=?",
                      (rid,)).fetchone()
    assert row["state"] == "paused" and row["handback_reason"] == "user_stop: x"
    assert "last_limit" not in json.loads(row["budget_note"] or "{}"), "不能把预算触发写进已暂停的 run"


def test_persist_never_lowers_counters(setup):
    """迟到的计数写不能把并发推进的更大计数改小(MAX 单调)。"""
    con, _dom, root = setup
    box = {}

    class Bump(ScriptedModel):
        def chat(self, messages, tools):
            rep = super().chat(messages, tools)
            con.execute("UPDATE runs SET model_calls=500 WHERE id=?", (box["rid"],))
            return rep

    r = Runner(con, root, Bump(script=[{"content": "FINAL: x", "tool_calls": []}]),
               limits=Limits(max_model_calls=900), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    box["rid"] = rid
    r.run(rid, "t1")
    assert con.execute("SELECT model_calls FROM runs WHERE id=?", (rid,)).fetchone()[0] >= 500


def test_lifecycle_writes_require_current_owner(setup):
    """接管后:预算预留/计数/checkpoint 都不能由旧进程写入。"""
    con, _dom, root = setup
    r = Runner(con, root, ScriptedModel(script=[]), mcp=False, watchdog=False)
    rid = r.start("t1")
    con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (rid,))
    assert r._reserve_call(rid, 1) is False
    assert r._persist_counters(rid, 9, 9, 9.0) is False
    assert r._checkpoint(rid, 3, []) is False
    row = con.execute("SELECT model_calls,tool_calls,step,state FROM runs WHERE id=?",
                      (rid,)).fetchone()
    assert (row["model_calls"], row["tool_calls"], row["step"], row["state"]) == (0, 0, 0, "running")
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?",
                       (rid,)).fetchone()["n"] == 0


def test_exception_does_not_pause_after_takeover(setup):
    """异常路径也不能无条件 SET paused;旧 owner 不得覆盖新 owner。"""
    con, _dom, root = setup
    box = {}

    class StealThenBoom(ScriptedModel):
        def chat(self, messages, tools):
            con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (box["rid"],))
            raise RuntimeError("boom")

    r = Runner(con, root, StealThenBoom(script=[]), mcp=False, watchdog=False)
    rid = r.start("t1")
    box["rid"] = rid
    with pytest.raises(RuntimeError):
        r.run(rid, "t1")
    row = con.execute("SELECT state,pid FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "running" and row["pid"] == 999999
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?",
                       (rid,)).fetchone()["n"] == 0


def test_turn_refuses_user_stop_and_foreign_owner(setup):
    con, _dom, root = setup
    model = ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}])
    r = Runner(con, root, model, mcp=False, watchdog=False)
    rid = r.start("t1")
    con.execute("UPDATE runs SET state='paused', handback_reason='user_stop: 手工取消'"
                " WHERE id=?", (rid,))
    res = r.turn(rid, "t1", None, [])
    assert res.state == "paused" and res.handback == "user_stop: 手工取消"
    assert model.calls == [], "取消后 turn 不得发起模型调用"

    con.execute("UPDATE runs SET state='paused', handback_reason=NULL, pid=999999,"
                " pid_starttime=1 WHERE id=?", (rid,))
    res2 = r.turn(rid, "t1", None, [])
    assert res2.handback == "owner_lost"
    assert model.calls == []


def _compact_hist():
    def call(cid, path):
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "fs_read", "arguments": json.dumps({"path": path})}}]}

    return [
        call("a1", "a.txt"), {"role": "tool", "tool_call_id": "a1", "content": "x" * 8000},
        call("a2", "b.txt"), {"role": "tool", "tool_call_id": "a2", "content": "y" * 8000},
    ]


def test_reserve_then_takeover_blocks_ordinary_model_call(setup):
    con, _dom, root = setup
    model = ScriptedModel(script=[{"content": "FINAL: 不应调用", "tool_calls": []}])
    r = Runner(con, root, model, mcp=False, watchdog=False)
    rid = r.start("t1")
    orig = r._reserve_call

    def steal(rid, n):
        ok = orig(rid, n)
        if ok:
            con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (rid,))
        return ok

    r._reserve_call = steal
    res = r.run(rid, "t1")
    assert model.calls == [], "预留后接管:不得发起模型请求"
    assert res.handback == "owner_lost"
    row = con.execute("SELECT state,pid FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "running" and row["pid"] == 999999


def test_reserve_then_cancel_blocks_ordinary_model_call(setup):
    con, _dom, root = setup
    model = ScriptedModel(script=[{"content": "FINAL: 不应调用", "tool_calls": []}])
    r = Runner(con, root, model, mcp=False, watchdog=False)
    rid = r.start("t1")
    orig = r._reserve_call

    def cancel_after(rid, n):
        ok = orig(rid, n)
        if ok:
            con.execute("UPDATE runs SET state='paused', handback_reason='user_stop: 测试'"
                        " WHERE id=?", (rid,))
        return ok

    r._reserve_call = cancel_after
    res = r.run(rid, "t1")
    assert model.calls == [], "预留后取消:不得发起模型请求"
    assert res.state == "paused"
    assert (res.handback or "").startswith("user_stop")


def test_summary_reserve_then_takeover_blocks_model_call(setup):
    con, _dom, root = setup
    model = ScriptedModel(script=[])
    r = Runner(con, root, model,
               limits=Limits(max_model_calls=10, context_tokens=1000,
                             output_reserve_tokens=10, keep_recent_tokens=100),
               mcp=False, watchdog=False)
    rid = r.start("t1")
    orig = r._reserve_call
    calls = []

    def steal(rid, n):
        ok = orig(rid, n)
        calls.append(n)
        if len(calls) == 1:
            con.execute("UPDATE runs SET pid=999999, pid_starttime=1 WHERE id=?", (rid,))
        return ok

    r._reserve_call = steal
    res = r.turn(rid, "t1", None, _compact_hist())
    assert len(calls) >= 2, "应先触发摘要预留,再触发主调用预留"
    assert model.calls == [], "摘要预留后接管:不得调用摘要或普通模型"
    assert res.handback == "owner_lost"


def test_summary_reserve_then_cancel_blocks_model_call(setup):
    con, _dom, root = setup
    model = ScriptedModel(script=[])
    r = Runner(con, root, model,
               limits=Limits(max_model_calls=10, context_tokens=1000,
                             output_reserve_tokens=10, keep_recent_tokens=100),
               mcp=False, watchdog=False)
    rid = r.start("t1")
    orig = r._reserve_call
    calls = []

    def cancel_after(rid, n):
        ok = orig(rid, n)
        calls.append(n)
        if len(calls) == 1:
            con.execute("UPDATE runs SET state='paused', handback_reason='user_stop: 测试'"
                        " WHERE id=?", (rid,))
        return ok

    r._reserve_call = cancel_after
    res = r.turn(rid, "t1", None, _compact_hist())
    assert len(calls) >= 2
    assert model.calls == []
    assert res.state == "paused" and (res.handback or "").startswith("user_stop")


def test_runner_refuses_archived_target(setup):
    con, dom, root = setup
    dom.archive_target("t1", "用户归档")
    with pytest.raises(Exception) as e:
        Runner(con, root, ScriptedModel(script=[]), mcp=False, watchdog=False).start("t1")
    assert "归档" in str(e.value) or "active" in str(e.value)


def test_context_status_tool_uses_provider_usage(setup):
    _con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root,
                      context_usage=lambda: {"used": 800, "window": 1000,
                                             "percent": 80.0})
    out = default_registry().call(ctx, "context_status", {})
    assert out["status"] == "ok"
    assert out["usage"]["percent"] == 80.0
    assert "provider prompt usage" in out["note"]
