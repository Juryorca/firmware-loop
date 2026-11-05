"""研究链路反馈:shell 语义、证据自动关联、进度呈现(演练复盘发现的三处缺陷)。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.research import Research
from fwcore.tools import ToolContext, default_registry


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    r = default_registry()
    return con, dom, root, r


def _ctx(dom, root, rid=None):
    return ToolContext(domain=dom, root=root, run_id=rid,
                       artifacts_dir=os.path.join(root, "state", "artifacts", rid or "local"),
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


def test_shell_exec_auto_uses_shell_for_metacharacters(tmp_path):
    """模型天然按 shell 写命令;不该因为 `; |` 静默失败(演练里 7 次失败源于此)。"""
    con, dom, root, reg = _setup(tmp_path)
    ctx = _ctx(dom, root, rid="R1")
    out = reg.call(ctx, "shell_exec", {"command": "echo A; echo b"})
    assert out["status"] == "ok", out
    assert out.get("shell") is True
    with open(out["stdout_path"], encoding="utf-8") as f:
        body = f.read()
    assert "A" in body and "b" in body


def test_shell_exec_plain_command_stays_argv(tmp_path):
    con, dom, root, reg = _setup(tmp_path)
    out = reg.call(_ctx(dom, root, rid="R1"), "shell_exec", {"command": "echo hi"})
    assert out["status"] == "ok" and out.get("shell") is False


def test_evidence_auto_links_to_producing_tool_call(tmp_path):
    """模型只给 path 时,按产物路径回溯到产生它的工具调用(可审计,不猜)。"""
    con, dom, root, reg = _setup(tmp_path)
    ctx = _ctx(dom, root, rid="R1")
    tc = reg.call(ctx, "shell_exec", {"command": "echo PWNED > proof.txt", "cwd": "."})
    assert tc["status"] == "ok"
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    # ① 弱线索:文件只在命令参数里出现(echo > proof.txt)⇒ **不**建立来源关联
    weak = reg.call(ctx, "state_add_evidence", {
        "target": "t1", "kind": "note", "attempt_id": aid,
        "hypothesis_id": hid, "path": os.path.join(root, "proof.txt")})
    assert weak["status"] == "ok"
    assert weak["linked_tool_call_id"] is None
    assert (weak.get("link") or "").startswith("weak-args")
    # ② 直接产出:证据路径就是该调用的 stdout ⇒ 建立关联 + 关系标注
    strong = reg.call(ctx, "state_add_evidence", {
        "target": "t1", "kind": "command-output", "attempt_id": aid,
        "hypothesis_id": hid, "path": tc["stdout_path"]})
    assert strong["linked_tool_call_id"] == tc["tool_call_id"], strong
    # 关系按路径判定:stdout ⇒ direct-stdout;result ⇒ direct-result(不再猜)
    row = con.execute("SELECT tool_call_id, verified, link_relation FROM evidence WHERE id=?",
                      (strong["evidence_id"],)).fetchone()
    assert row["tool_call_id"] == tc["tool_call_id"] and row["verified"] == 0
    assert row["link_relation"] in ("direct-stdout", "direct-result"), row["link_relation"]


def test_evidence_without_matching_tool_call_does_not_fake_link(tmp_path):
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    p = os.path.join(root, "manual.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("hand written")
    ev = reg.call(_ctx(dom, root, rid="R1"), "state_add_evidence", {
        "target": "t1", "kind": "note", "attempt_id": aid, "hypothesis_id": hid, "path": p})
    assert ev["status"] == "ok" and ev["linked_tool_call_id"] is None


def test_progress_brief_surfaces_unfinished_work(tmp_path):
    """演练复盘:模型跑完实验不知道要结束 attempt / 复核证据 / 下结论。"""
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "命令注入", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    ev_id = dom.add_evidence("t1", "log", capture="看过回显", attempt_id=aid, hypothesis_id=hid)
    brief = "\n".join(Research(root, dom).progress_brief("t1"))
    assert aid in brief and "state_finish_attempt" in brief
    assert ev_id in brief and "state_verify_evidence" in brief
    assert hid in brief and "state_conclude" in brief


def test_progress_brief_says_when_ready_to_conclude(tmp_path):
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "注入", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    ev = dom.add_evidence("t1", "log", capture="payload 被执行", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "复现成功", obs_outcome="none",
                       obs_reason="与预期一致,无新观察")
    brief = "\n".join(Research(root, dom).progress_brief("t1"))
    assert "未复核" in brief, "证据没复核时应先提示复核"
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    brief2 = "\n".join(Research(root, dom).progress_brief("t1"))
    assert "已具备收口条件" in brief2 and "state_conclude" in brief2


def test_verify_evidence_points_to_conclusion_when_ready(tmp_path):
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    ev = dom.add_evidence("t1", "log", capture="payload 执行", attempt_id=aid, hypothesis_id=hid)
    art = os.path.join(root, "cross.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("对照记录")
    # 实验还没结束 ⇒ 复核成功,但提示"先结束实验"
    out = reg.call(_ctx(dom, root, rid="R1"), "state_verify_evidence",
                   {"evidence_id": ev, "method": "cross-check", "artifact": art,
                    "detail": "对照崩溃栈与反编译源码,边界与偏移一致"})
    assert out["status"] == "ok", out
    assert "running" in (out.get("next") or ""), out.get("next")
    # 结束实验后再复核 ⇒ 明确提示可以收口
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none",
                       obs_reason="与预期一致,无新观察")
    out2 = reg.call(_ctx(dom, root, rid="R1"), "state_verify_evidence",
                    {"evidence_id": ev, "method": "cross-check", "artifact": art,
                     "detail": "再次对照崩溃栈与反编译源码,结论一致"})
    assert out2["status"] == "ok", out2
    assert "state_conclude" in (out2.get("next") or ""), out2.get("next")


def test_ready_hint_requires_causal_verification(tmp_path):
    """只有 recheck-sha256(完整性)时不能提示"可以漏洞收口"。"""
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "注入", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    art = os.path.join(root, "x.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("payload 输出")
    ev = dom.add_evidence("t1", "tool_output", path=art, attempt_id=aid, hypothesis_id=hid)
    dom.verify_evidence(ev, "recheck-sha256")
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none",
                       obs_reason="与预期一致,无新观察")
    brief = "\n".join(Research(root, dom).progress_brief("t1"))
    assert "因果复核" in brief and "只有 recheck-sha256 不够" in brief
    # 补一次因果复核 ⇒ 才提示可以收口
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    brief2 = "\n".join(Research(root, dom).progress_brief("t1"))
    assert "可 `state_conclude(" in brief2


def test_only_one_file_can_be_the_direct_output_of_a_call(tmp_path):
    """强关联按产物路径唯一匹配:同一次调用最多对 stdout/result 两个文件负责。"""
    con, dom, root, reg = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    ctx = _ctx(dom, root, rid="R1")
    reg.call(ctx, "shell_exec", {"command": "echo one > a.txt; echo two > b.txt"})
    a = reg.call(ctx, "state_add_evidence", {"target": "t1", "kind": "note", "attempt_id": aid,
                                             "hypothesis_id": hid,
                                             "path": os.path.join(root, "a.txt")})
    b = reg.call(ctx, "state_add_evidence", {"target": "t1", "kind": "note", "attempt_id": aid,
                                             "hypothesis_id": hid,
                                             "path": os.path.join(root, "b.txt")})
    # 两个文件都不是该调用的 stdout ⇒ 只能是弱线索,不能都绑到同一个调用
    assert a["linked_tool_call_id"] is None and b["linked_tool_call_id"] is None
    assert (a.get("link") or "").startswith("weak-args")


def test_shell_mode_contract(tmp_path):
    """`* ? ~` 不触发 shell;显式 mode 决定解释方式。"""
    con, dom, root, reg = _setup(tmp_path)
    ctx = _ctx(dom, root, rid="R1")
    # 引号内的通配符:argv 模式 ⇒ 原样传给命令(不展开)
    out = reg.call(ctx, "shell_exec", {"command": "printf '%s\\n' '*.c'", "mode": "argv"})
    assert out["status"] == "ok" and out["mode"] == "argv" and out["shell"] is False
    with open(out["stdout_path"], encoding="utf-8") as f:
        assert "*.c" in f.read()
    # 真正的 shell 语法 ⇒ auto 走 shell
    out2 = reg.call(ctx, "shell_exec", {"command": "echo hi | tr a-z A-Z"})
    assert out2["shell"] is True and out2["mode"] == "auto"
    # 显式 shell 也允许(即使没有元字符)
    out3 = reg.call(ctx, "shell_exec", {"command": "echo $((1+1))", "mode": "shell"})
    assert out3["status"] == "ok" and out3["shell"] is True


def test_tool_calls_per_turn_are_capped(tmp_path):
    """一次模型回复刷出很多调用时,执行数有上限且被显式告知(演练里出现过 121 次)。"""
    from fwcore.limits import Limits
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    con, dom, root, _reg = _setup(tmp_path)
    calls = [{"id": f"c{i}", "name": "shell_exec", "args": {"command": f"echo {i}"}}
             for i in range(12)]
    script = [{"content": "批量", "tool_calls": calls},
              {"content": "FINAL: 结束", "tool_calls": []}]
    r = Runner(con, root, ScriptedAdapter(script=script),
               limits=Limits(max_model_calls=4, max_tool_calls_per_turn=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.run(r.start("t1"), "t1")
    n = con.execute("SELECT COUNT(*) n FROM tool_calls WHERE tool='shell_exec'").fetchone()["n"]
    assert n == 5, f"单轮应只执行 5 个,实际 {n}"
    kinds = [row["kind"] for row in con.execute("SELECT kind FROM events")]
    assert "run.tool_cap" in kinds
    skipped = [m for m in res.history if m.get("role") == "tool"
               and "单轮工具调用上限" in (m.get("content") or "")]
    assert len(skipped) == 7
