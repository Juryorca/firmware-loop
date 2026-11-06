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
        self.systems.append("\n".join(m.get("content") or "" for m in messages))
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


def test_progress_snapshot_is_structured(tmp_path):
    """外部监控/Pi 适配器要能拿到结构化进度(不解析日志)。"""
    con, dom, root = _setup(tmp_path)
    dom.add_hypothesis("t1", "未收口的东西")
    r = Runner(con, root, ScriptedAdapter(script=[FINAL]), limits=Limits(max_model_calls=2),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    p = r.progress(rid)
    assert p["run_id"] == rid and p["target"] == "t1"
    assert p["state"] in ("finished", "paused")
    assert p["blockers"], "有未收口假设 ⇒ blockers 必须非空"
    assert p["limits"]["checkpoints_keep"] > 0 and isinstance(p["recent_events"], list)


def test_prepare_timeout_does_not_wait_for_hung_hook(tmp_path):
    """钩子卡死时主流程必须**立刻**继续(不能等它),并且有可观测计数。"""
    con, dom, root = _setup(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[FINAL]), limits=Limits(max_model_calls=2),
               policy=Policy(allow_high_risk=True),
               prepare=lambda payload: time.sleep(5), prepare_timeout=0.1)
    t0 = time.time()
    res = r.run(r.start("t1"), "t1")
    elapsed = time.time() - t0
    assert elapsed < 3, f"被卡死的钩子拖住了:{elapsed:.1f}s"
    assert res.state == "finished"
    assert r.prepare_timeouts >= 1 and r.prepare_abandoned >= 1
