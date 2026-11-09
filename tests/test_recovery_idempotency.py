"""恢复幂等:工具**已产生副作用**但检查点未落盘时,恢复不能重复执行。

构造(确定性,不用真崩溃进程):
1) 跑一轮,让工具真实执行(假设已入库,tool_calls 记录 call_key);
2) **删掉检查点**并把 runs.step 回退 —— 模拟"副作用已生效、检查点没来得及落盘";
3) 恢复时模型重发同一个 call_key ⇒ 必须命中幂等缓存,不产生第二条副作用;run 能继续。
"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner

CALL = {"id": "c1", "type": "function",
        "function": {"name": "state_add_hypothesis",
                     "arguments": '{"target":"t1","title":"副作用只应发生一次"}'}}


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a")
    return con, dom, root


def test_resume_does_not_repeat_side_effect_after_lost_checkpoint(tmp_path):
    con, _dom, root = _setup(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "", "tool_calls": [CALL]},
                                                 {"content": "FINAL: 第一轮结束", "tool_calls": []}]),
               limits=Limits(max_model_calls=4), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1

    # 模拟"检查点未落盘":删检查点 + step 回退 + run 仍 running(硬崩溃后状态)
    con.execute("DELETE FROM checkpoints WHERE run_id=?", (rid,))
    con.execute("UPDATE runs SET step=0, state='running', pid=999999 WHERE id=?", (rid,))

    r2 = Runner(con, root, ScriptedAdapter(script=[{"content": "", "tool_calls": [CALL]},
                                                   {"content": "FINAL: 恢复后结束", "tool_calls": []}]),
                limits=Limits(max_model_calls=4), policy=Policy(allow_high_risk=True))
    assert r2.recover_stale() == [rid]           # pid 已死 ⇒ 判失联、暂停
    res = r2.resume(rid)
    assert res.state == "finished"

    # 关键断言:副作用没有重复
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1, \
        "重放同一 call_key 不得产生第二条假设"
    row = con.execute("SELECT status FROM tool_calls WHERE call_key='c1'").fetchall()
    assert len(row) == 1 and row[0]["status"] == "ok"


def test_partial_tool_state_is_visible_not_silently_ok(tmp_path):
    """工具调用若停在 running(进程被杀),状态必须如实为 running,不能伪装成 ok。"""
    con, _dom, _root = _setup(tmp_path)
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,created_at)"
                " VALUES('TC-x','crashed-call','R-x','shell_exec','{}','running','2026-01-01')")
    st = con.execute("SELECT status FROM tool_calls WHERE id='TC-x'").fetchone()["status"]
    assert st == "running"
