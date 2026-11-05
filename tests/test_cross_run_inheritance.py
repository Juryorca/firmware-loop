"""跨 run 继承:新 run 必须看到上一 run 的假设/证据/结论,而不是从零开始。"""

from __future__ import annotations

import os

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
