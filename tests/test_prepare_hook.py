"""轮前准备钩子(对齐 Pi 的 prepareNextTurn):可注入上下文、有超时、可取消。"""

from __future__ import annotations

import os
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner


class RecordingAdapter(ScriptedAdapter):
    """记录每次收到的 system 提示,便于断言钩子注入生效。"""

    def __init__(self, script):
        super().__init__(script=script)
        self.systems: list[str] = []

    def chat(self, messages, tools):
        self.systems.append(messages[0]["content"])
        return super().chat(messages, tools)


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a")
    return con, dom, root


FINAL = {"content": "FINAL: 收工", "tool_calls": []}


def test_prepare_note_is_injected_into_system_prompt(tmp_path):
    con, dom, root = _setup(tmp_path)
    ad = RecordingAdapter([FINAL])
    r = Runner(con, root, ad, limits=Limits(max_model_calls=3), policy=Policy(allow_high_risk=True),
               prepare=lambda payload: {"context_note": "上次尝试的关键未知:X 未验证"})
    rid = r.start("t1")
    r.run(rid, "t1")
    assert ad.systems and "上次尝试的关键未知" in ad.systems[0]


def test_prepare_timeout_does_not_block_research(tmp_path):
    con, dom, root = _setup(tmp_path)
    ad = RecordingAdapter([FINAL])
    r = Runner(con, root, ad, limits=Limits(max_model_calls=3), policy=Policy(allow_high_risk=True),
               prepare=lambda payload: time.sleep(1.5) or {}, prepare_timeout=0.1)
    rid = r.start("t1")
    t0 = time.time()
    res = r.run(rid, "t1")
    assert time.time() - t0 < 5 and r.prepare_timeouts >= 1
    assert res.state == "finished"
    assert con.execute("SELECT COUNT(*) n FROM events WHERE kind='run.prepare_timeout'").fetchone()["n"] >= 1


def test_prepare_error_does_not_kill_run(tmp_path):
    con, dom, root = _setup(tmp_path)
    def boom(_payload):
        raise RuntimeError("钩子坏了")
    r = Runner(con, root, ScriptedAdapter(script=[FINAL]), limits=Limits(max_model_calls=3),
               policy=Policy(allow_high_risk=True), prepare=boom)
    res = r.run(r.start("t1"), "t1")
    assert res.state == "finished"
    assert con.execute("SELECT COUNT(*) n FROM events WHERE kind='run.prepare_error'").fetchone()["n"] >= 1


def test_prepare_cancel_pauses_without_conclusion(tmp_path):
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "不该被收口")
    r = Runner(con, root, ScriptedAdapter(script=[FINAL]), limits=Limits(max_model_calls=3),
               policy=Policy(allow_high_risk=True), prepare=lambda payload: {"cancel": True, "why": "上游要求暂停"})
    res = r.run(r.start("t1"), "t1")
    assert res.state == "paused"
    assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM hypotheses WHERE state='concluded'").fetchone()["n"] == 0
