"""来源强度:direct-* 与 command-write 在观察/证据/提示/评估里语义一致(弱来源不冒充直接产出)。"""

from __future__ import annotations

import importlib.util
import os
import sys

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.research import Research


def _ws(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    tid = dom.create_target("t1", version="1")
    tid = getattr(tid, "id", tid)
    con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at) VALUES(?,?,?,?,?)",
                ("R-1", tid, "running", "2026-11-06T00:00:00Z", "2026-11-06T00:00:00Z"))
    art = os.path.join(root, "report", "artifacts")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "out.txt"), "w", encoding="utf-8") as f:
        f.write("0x19908 op\n")
    # 直接产出:stdout 就是该文件
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES('TC-D','call_d','R-1','shell_exec','{}','ok',?,'2026-11-06T00:00:01Z')",
                (os.path.join(art, "out.txt"),))
    # 弱来源:只在命令参数里出现,stdout 是别的文件
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES('TC-W','call_w','R-1','shell_exec',?,'ok',?,'2026-11-06T00:00:02Z')",
                ('{"command": "grep x report/artifacts/out.txt"}',
                 os.path.join(art, "other.txt")))
    return con, root, dom, art


def test_observation_relation_is_classified(tmp_path):
    con, root, dom, _art = _ws(tmp_path)
    o1 = dom.add_observation("t1", "直接读到的地址", tool_call_id="TC-D",
                             path="report/artifacts/out.txt")
    o2 = dom.add_observation("t1", "命令里提到过", tool_call_id="TC-W",
                             path="report/artifacts/out.txt")
    rel = {r["id"]: r["relation"] for r in con.execute("SELECT id,relation FROM observations")}
    assert rel[o1] == "direct-stdout"
    assert rel[o2] == "command-write", "只在命令参数里出现不能算直接产出"
    # 提示里按强度标注
    sp = Research(root, dom).system_prompt("t1", None, "R-1")
    assert "[direct-stdout]" in sp and "[command-write]" in sp


def test_assess_does_not_count_weak_as_direct(tmp_path):
    con, root, dom, _art = _ws(tmp_path)
    hid = dom.add_hypothesis("t1", "op 分发是否检查权限", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "静态")
    eid = dom.add_evidence("t1", "log", path="report/artifacts/out.txt", attempt_id=aid,
                           hypothesis_id=hid, tool_call_id="TC-W")
    dom.link_evidence_source(eid, "TC-W", note="按路径判定来源强度")
    dom.verify_evidence(eid, method="cross-check", artifact="report/artifacts/out.txt",
                        detail="与该函数在恢复 ELF 中的反汇编逐条交叉核对,调用点一致")
    dom.finish_attempt(aid, "refuted", "未发现失败路径", evidence=[eid],
                       obs_outcome="none", obs_reason="与预期一致:检查在入口前")
    dom.conclude(hid, "no_finding", "范围:main 的 op 分发 18 个调用点")
    spec = importlib.util.spec_from_file_location("drill3", "scripts/research_drill.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["drill3"] = m
    spec.loader.exec_module(m)
    v = m.assess(con, root, {"o": {"object": "authLogin.cgi", "expect": "any"}})
    checks = v["checks"]
    assert checks["decisive_attempts_have_source_links"]["ok"] is True   # 有(弱)链接
    assert checks["decisive_attempts_have_direct_evidence"]["ok"] is False  # 但没有直接产出
    assert checks["decisive_attempts_have_direct_evidence"]["critical"] is False
    assert checks["provenance_mix"]["ok"] is True
    assert "weak 1" in checks["provenance_mix"]["detail"]


def test_relation_survives_reopen(tmp_path):
    """来源强度落库而不是内存态:重开数据库仍是 direct/weak 原值。"""
    con, root, dom, _art = _ws(tmp_path)
    o1 = dom.add_observation("t1", "直接", tool_call_id="TC-D", path="report/artifacts/out.txt")
    o2 = dom.add_observation("t1", "弱", tool_call_id="TC-W", path="report/artifacts/out.txt")
    con.close()
    con2 = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    rows = {r["id"]: r["relation"] for r in con2.execute("SELECT id,relation FROM observations")}
    assert rows[o1] == "direct-stdout" and rows[o2] == "command-write"
