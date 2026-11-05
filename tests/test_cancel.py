"""取消/中途干预(对齐 Pi 的 AbortSignal 语义):只能停在安全点,且不写研究结论。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Policy
from fwcore.runtime import Runner


class CancellingAdapter:
    """第 2 次调用时由"用户"发起取消(模拟 ESC / 外部干预)。"""

    name = "cancelling"

    def __init__(self, runner_ref: dict):
        self.n = 0
        self.ref = runner_ref

    def chat(self, messages, tools):
        self.n += 1
        if self.n == 2:
            self.ref["runner"].cancel(self.ref["rid"], "用户按了 ESC")
        return Reply(content=f"step {self.n}", tool_calls=[{
            "id": f"c{self.n}", "name": "state_add_observation",
            "args": {"target": "t1", "text": f"观察 {self.n}"}, "args_raw": None}])


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a")
    return con, dom, root


def test_cancel_stops_at_safe_point_and_marks_user_stop(tmp_path):
    con, dom, root = _setup(tmp_path)
    ref: dict = {}
    runner = Runner(con, root, CancellingAdapter(ref), limits=Limits(max_model_calls=50),
                    policy=Policy(allow_high_risk=True))
    ref["runner"] = runner
    rid = runner.start("t1")
    ref["rid"] = rid
    res = runner.run(rid, "t1")

    assert res.state == "paused"
    row = con.execute("SELECT state, handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "paused" and row["handback_reason"].startswith("user_stop")
    kinds = [r["kind"] for r in con.execute("SELECT kind FROM events")]
    assert "run.cancelled" in kinds and "run.stopped_external" in kinds
    # 不写研究结论:没有 conclusion;假设仍是 open
    assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM hypotheses WHERE state='concluded'").fetchone()["n"] == 0


def test_cancel_before_run_prevents_any_step(tmp_path):
    con, dom, root = _setup(tmp_path)
    runner = Runner(con, root, CancellingAdapter({}), limits=Limits(max_model_calls=10),
                    policy=Policy(allow_high_risk=True))
    rid = runner.start("t1")
    runner.cancel(rid, "用户一开始就取消")
    res = runner.run(rid, "t1")
    assert res.state == "paused" and res.steps == 0
