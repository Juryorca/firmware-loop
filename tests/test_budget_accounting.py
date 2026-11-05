"""预算记账:run 级累计(model_calls/tool_calls/elapsed_s),暂停-恢复不重置、不重复计数。

原则(任务书):预算是**执行边界**,不是研究完成标准;到预算 = 暂停 + checkpoint,
恢复后继续消耗同一份预算,不从头开始。
"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


class CountingAdapter:
    """记录自己调用了多少次,便于核对 run 计数是否重复。"""

    name = "counting"

    def __init__(self, script: list[dict]):
        self.inner = ScriptedAdapter(script=script)
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return self.inner.chat(messages, tools)


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _counters(con, rid):
    row = con.execute("SELECT model_calls,tool_calls,elapsed_s,state FROM runs WHERE id=?",
                      (rid,)).fetchone()
    return {"model_calls": row["model_calls"], "tool_calls": row["tool_calls"],
            "elapsed_s": row["elapsed_s"], "state": row["state"]}


def test_model_calls_accumulate_across_resume_without_reset(tmp_path):
    con, dom, root = _setup(tmp_path)
    script = [{"content": "第二句", "tool_calls": []},
              {"content": "第三句", "tool_calls": []},
              {"content": "第四句", "tool_calls": []},
              {"content": "FINAL: 收束", "tool_calls": []}]
    adapter = CountingAdapter(script)
    lim = Limits(max_model_calls=2)
    r = Runner(con, root, adapter, limits=lim, policy=Policy(
        authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1")
    first = r.run(rid, "t1")
    assert first.state == "paused"
    assert _counters(con, rid)["model_calls"] == 2
    assert adapter.calls == 2

    # 恢复:同一 run 的预算继续算(不会又给 2 次),所以第一次检查就再次暂停
    second = r.resume(rid, "t1")
    assert second.state == "paused"
    assert _counters(con, rid)["model_calls"] == 2, "恢复后计数必须延续,不能重置或重复加"
    assert adapter.calls == 2


def test_resume_continues_when_budget_allows(tmp_path):
    con, dom, root = _setup(tmp_path)
    script = [{"content": "a", "tool_calls": []}, {"content": "b", "tool_calls": []},
              {"content": "c", "tool_calls": []}, {"content": "FINAL: 完成", "tool_calls": []}]
    adapter = CountingAdapter(script)
    r = Runner(con, root, adapter, limits=Limits(max_model_calls=10),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1", budget=2)                    # 单轮步数上限 2 ⇒ 暂停
    assert adapter.calls == 2
    r.resume(rid, "t1")                           # 预算充足 ⇒ 继续
    assert _counters(con, rid)["model_calls"] == adapter.calls
    assert adapter.calls > 2


def test_tool_calls_counter_matches_db_rows(tmp_path):
    con, dom, root = _setup(tmp_path)
    script = [{"content": "跑两条", "tool_calls": [
        {"id": "c1", "name": "shell_exec", "args": {"command": "echo a"}},
        {"id": "c2", "name": "shell_exec", "args": {"command": "echo b"}}]},
        {"content": "FINAL: 完成", "tool_calls": []}]
    r = Runner(con, root, ScriptedAdapter(script=script), limits=Limits(max_model_calls=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    n_rows = con.execute("SELECT COUNT(*) n FROM tool_calls WHERE run_id=?", (rid,)).fetchone()["n"]
    assert _counters(con, rid)["tool_calls"] == n_rows == 2


def test_advance_does_not_double_count(tmp_path):
    con, dom, root = _setup(tmp_path)
    script = [{"content": "第一轮", "tool_calls": []},
              {"content": "FINAL: 第一轮结束", "tool_calls": []},
              {"content": "第二轮", "tool_calls": []},
              {"content": "FINAL: 第二轮结束", "tool_calls": []}]
    adapter = CountingAdapter(script)
    r = Runner(con, root, adapter, limits=Limits(max_model_calls=10),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.advance("t1", "目标", max_rounds=3)
    rid = con.execute("SELECT id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()["id"]
    assert _counters(con, rid)["model_calls"] == adapter.calls, "advance 不能重复计数"
    assert res.state in ("finished", "paused")


def test_elapsed_accumulates(tmp_path):
    con, dom, root = _setup(tmp_path)
    script = [{"content": "x", "tool_calls": []}, {"content": "FINAL: 收束", "tool_calls": []}]
    r = Runner(con, root, ScriptedAdapter(script=script), limits=Limits(max_model_calls=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    first = _counters(con, rid)["elapsed_s"]
    r.resume(rid, "t1")
    second = _counters(con, rid)["elapsed_s"]
    assert second >= first, "累计墙钟只能增长"
