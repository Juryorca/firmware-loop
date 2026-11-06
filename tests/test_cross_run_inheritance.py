"""跨 run 继承:新 run 必须看到上一 run 的假设/证据/结论,而不是从零开始。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


class Replay:
    name = "replay"

    def __init__(self):
        self.prompts: list[str] = []
        self.done = False

    def chat(self, messages, tools):
        self.prompts.append(messages[0]["content"])
        if self.done:
            return Reply(content="FINAL: 收束")
        self.done = True
        return Reply(content="先看已有结论,不重复分析。")


def test_second_run_sees_prior_research(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "qnap_exec 调用点是否有请求参数", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "清点 23 个调用点")
    rel = "report/artifacts/call_sites.txt"
    os.makedirs(os.path.join(root, "report", "artifacts"), exist_ok=True)
    with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
        f.write("23 call sites\n")
    eid = dom.add_evidence("t1", "log", path=rel, attempt_id=aid, hypothesis_id=hid)
    dom.verify_evidence(eid, method="recheck-sha256")
    dom.finish_attempt(aid, "refuted", "全是字面量", evidence=[eid],
                       obs_outcome="none", obs_reason="与预期一致:无外部输入")
    dom.conclude(hid, "no_finding", "范围:authLogin.cgi 的 23 个调用点")

    model = Replay()
    r = Runner(con, root, model, limits=Limits(max_model_calls=2),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1", "第二阶段:查 op 子处理器")
    r.run(rid, "t1", goal="第二阶段:查 op 子处理器")

    sys_prompt = model.prompts[0]
    # 新 run 的提示里必须带上一轮的结论/假设线索,否则等于重头再来
    assert "qnap_exec" in sys_prompt or hid in sys_prompt, sys_prompt[:400]
    assert "no_finding" in sys_prompt
    # 研究事实仍在库里,新 run 不新建重复假设
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
    assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 1


def test_observation_survives_to_next_run(tmp_path):
    """observation 是"已确认但还不足以成假设"的事实;必须跨 run 可见。"""
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "op 分发是否检查权限", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "跟踪 op 参数")
    oid = dom.add_observation("t1", "0x19908 从 CGI_Find_Parameter(\"op\") 取值后 strtol",
                              attempt_id=aid)
    model = Replay()
    r = Runner(con, root, model, limits=Limits(max_model_calls=2),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "继续"), "t1", goal="继续")
    assert oid in model.prompts[0]
    assert "0x19908" in model.prompts[0]


def test_observation_source_is_validated_and_kept(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    rid = "R-1"
    tid = con.execute("SELECT id FROM targets").fetchone()["id"]
    con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at) VALUES(?,?,?,?,?)",
                (rid, tid, "running", "2026-11-06T00:00:00Z", "2026-11-06T00:00:00Z"))
    os.makedirs(os.path.join(root, "report", "artifacts"), exist_ok=True)
    art = os.path.join(root, "report", "artifacts", "op.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("0x19908 CGI_Find_Parameter(\"op\")\n")
    # 来源必须可证:这条调用的 stdout 就是该文件(shell_exec 的真实形态)
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES('TC-1','call_1','R-1','shell_exec','{}','ok',?,'2026-11-06T00:00:01Z')", (art,))
    oid = dom.add_observation("t1", "0x19908 取 op 后 strtol",
                              tool_call_id="TC-1", path="report/artifacts/op.txt")
    row = con.execute("SELECT tool_call_id,path,sha256 FROM observations WHERE id=?", (oid,)).fetchone()
    assert row["tool_call_id"] == "TC-1" and row["path"].endswith("op.txt") and len(row["sha256"]) == 64
    from fwcore.domain import InvariantViolation, NotFound
    with pytest.raises((InvariantViolation, NotFound)):
        dom.add_observation("t1", "x", tool_call_id="TC-1", path="report/artifacts/missing.txt")
