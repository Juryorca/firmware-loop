"""模型回复形态(协议):文字-only / 推理-only / 截断 / 空 / FINAL 在第二段 / 重复内容。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


class Fake:
    """按脚本返回 Reply;可指定 reasoning / finish_reason。"""

    name = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.i >= len(self.script):
            return Reply(content="FINAL: 收束", tool_calls=[])
        item = self.script[self.i]
        self.i += 1
        return Reply(content=item.get("content", ""), tool_calls=item.get("tool_calls") or [],
                     reasoning=item.get("reasoning", ""), finish_reason=item.get("finish_reason", ""))


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, root


def _run(con, root, script, **kw):
    f = Fake(script)
    r = Runner(con, root, f, limits=Limits(max_model_calls=20), stall_repeats=3,
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True), **kw)
    rid = r.start("t1")
    return r.run(rid, "t1"), f, rid


def _events(con, kind):
    return [json.loads(r["payload"] or "{}") for r in
            con.execute("SELECT payload FROM events WHERE kind=?", (kind,))]


def test_text_only_replies_pause_and_emit_classification(tmp_path):
    con, root = _setup(tmp_path)
    script = [{"content": "我还在想(1)"}, {"content": "我还在想(2)"}, {"content": "我还在想(3)"}]
    res, f, _rid = _run(con, root, script)
    assert res.state == "paused" and f.calls == 3
    ev = _events(con, "model.reply")
    assert ev and all(e["class"] == "text_only" and e["tool_calls"] == 0 for e in ev)
    stalled = _events(con, "run.stalled")
    assert stalled and stalled[-1]["reason"] in ("no_tool_no_final", "repeated_content")
    # 暂停前必须落 checkpoint(诊断历史可恢复)
    assert con.execute("SELECT COUNT(*) n FROM checkpoints").fetchone()["n"] >= 1


def test_reasoning_only_is_recorded_not_blank(tmp_path):
    con, root = _setup(tmp_path)
    script = [{"content": "", "reasoning": "先想想"}, {"content": "", "reasoning": "再想想"},
              {"content": "", "reasoning": "还想"},
              {"content": "FINAL: 收束", "tool_calls": []}]
    res, _f, _rid = _run(con, root, script)
    ev = _events(con, "model.reply")
    assert ev and ev[0]["class"] == "reasoning_only" and ev[0]["reasoning_len"] > 0
    rows = con.execute("SELECT meta FROM conversation_entries WHERE role='assistant'").fetchall()
    metas = [json.loads(r["meta"] or "{}") for r in rows]
    assert any((m.get("reasoning") or "") for m in metas), "推理内容不能丢成空白"
    assert res.state in ("paused", "finished")


def test_truncated_reply_is_classified(tmp_path):
    con, root = _setup(tmp_path)
    script = [{"content": "很长的分析……", "finish_reason": "length"},
              {"content": "FINAL: 收束", "tool_calls": []}]
    res, _f, _rid = _run(con, root, script)
    ev = _events(con, "model.reply")
    assert ev and ev[0]["class"] == "truncated" and ev[0]["finish_reason"] == "length"
    assert res.state == "finished"


def test_final_on_second_line_is_recognized(tmp_path):
    """QNAP 实跑里模型把 FINAL 写在第二段 ⇒ 之前被当成没结束,白烧 200 次调用。"""
    con, root = _setup(tmp_path)
    script = [{"content": "本轮已收口,无待办。\n\nFINAL: 研究收口", "tool_calls": []}]
    res, f, _rid = _run(con, root, script)
    assert res.state == "finished", res.state
    assert f.calls == 1


def test_repeated_identical_content_pauses(tmp_path):
    con, root = _setup(tmp_path)
    same = "重复的总结文字"
    script = [{"content": same} for _ in range(6)]
    res, f, _rid = _run(con, root, script)
    assert res.state == "paused"
    assert f.calls == 3, f"重复内容应在 stall_repeats 次后停,实际 {f.calls}"
    assert _events(con, "run.stalled")[-1]["reason"] == "repeated_content"
