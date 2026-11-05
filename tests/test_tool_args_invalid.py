"""工具参数截断/非法 JSON:必须**显式失败且不执行**(对齐 Pi 的 failToolCallsFromTruncatedMessage)。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a")
    return con, dom, root


TRUNCATED = {"id": "c1", "type": "function",
             "function": {"name": "state_add_hypothesis",
                          "arguments": '{"target":"t1","title":"截断的标题'}}   # 故意不闭合


def test_truncated_arguments_fail_explicitly_and_do_not_execute(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "", "tool_calls": [TRUNCATED]},
                                                 {"content": "FINAL: 收到错误后收工", "tool_calls": []}]),
               limits=Limits(max_model_calls=4), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    res = r.run(rid, "t1")

    assert res.state == "finished"
    # ① 工具没有被执行:没有产生假设
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 0
    # ② tool_calls 记录为 error,并且说明是参数问题
    row = con.execute("SELECT status, note FROM tool_calls ORDER BY created_at LIMIT 1").fetchone()
    assert row["status"] == "error" and "截断" in (row["note"] or "")
    # ③ 历史里回填的是 tool 消息(而不是把错误吞掉)
    tool_msgs = [m for m in res.history if m.get("role") == "tool"]
    assert tool_msgs and "未执行" in tool_msgs[0]["content"]
