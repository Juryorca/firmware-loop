"""权威 Gate 必须能分开呈现来源强度,并对"纯完整性复核的 no_finding"给出提示。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.gates_db import evaluate


def _ws(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    art = os.path.join(root, "report", "artifacts")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "a.txt"), "w", encoding="utf-8") as f:
        f.write("x\n")
    tid = con.execute("SELECT id FROM targets").fetchone()["id"]
    con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at) VALUES(?,?,?,?,?)",
                ("R-1", tid, "running", "2026-11-06T00:00:00Z", "2026-11-06T00:00:00Z"))
    # 直接产出
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES('TC-D','call_d','R-1','shell_exec','{}','ok',?,'2026-11-06T00:00:01Z')",
                (os.path.join(art, "a.txt"),))
    # 弱来源
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES('TC-W','call_w','R-1','shell_exec',?,'ok',?,'2026-11-06T00:00:02Z')",
                ('{"command": "grep x report/artifacts/a.txt"}', os.path.join(art, "b.txt")))
    return con, root, dom, art


def test_gate_reports_provenance_and_integrity_advisory(tmp_path):
    con, root, dom, _art = _ws(tmp_path)
    hid = dom.add_hypothesis("t1", "命令注入", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "审计")
    e1 = dom.add_evidence("t1", "log", path="report/artifacts/a.txt", attempt_id=aid,
                          hypothesis_id=hid, tool_call_id="TC-D")
    dom.link_evidence_source(e1, "TC-D")
    dom.verify_evidence(e1, method="recheck-sha256")      # 只有完整性
    dom.finish_attempt(aid, "refuted", "没有外部输入", evidence=[e1],
                       obs_outcome="none", obs_reason="与预期一致:参数全是字面量")
    dom.conclude(hid, "no_finding", "范围:main 的 23 个调用点")

    rep = evaluate(con, root, "t1")
    assert rep.provenance == {"direct": 1, "weak": 0, "unlinked": 0}
    assert rep.advisories and "完整性" in rep.advisories[0]
    # 只是提示,不阻断(这个空工作区仍有别的阻断项,如没有登记攻击面)
    assert not any("完整性" in b for b in rep.blockers)
    assert evaluate(con, root, "t1").as_dict()["provenance"]["direct"] == 1
