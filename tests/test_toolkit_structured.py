"""结构化工具观测:输出 → 字段;并记录 provider/capability(不只看文本尾巴)。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Policy
from fwcore.toolkit import OBSERVERS, register_repo_tools
from fwcore.tools import Registry, ToolContext


def _stub(root: str, rel: str, body: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o700)


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    _stub(root, "tools/derived_check.sh",
          '#!/bin/sh\necho "OK   a"\necho "FAIL b: 缺图链"\necho "=== derived_check: FAIL x1 ==="\n')
    _stub(root, "tools/queue_state.sh",
          '#!/bin/sh\necho "OPEN=3 BAD=1 ROUND=done STOP= POOL=7/4 NEXT=下一步第一条: x"\n')
    _stub(root, "tools/parse_signal.py",
          '#!/usr/bin/env python3\nprint("HIT:shell_exec")\n')
    _stub(root, "tools/cgi_fuzz.sh",
          '#!/bin/sh\necho "tuples=141"\necho "uncaught signal SIGSEGV"\n')
    reg = Registry()
    register_repo_tools(reg, root)
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True),
                      artifacts_dir=os.path.join(root, "state", "artifacts", "R1"))
    return con, dom, root, reg, ctx


def test_derived_and_queue_are_structured(tmp_path):
    con, dom, root, reg, ctx = _setup(tmp_path)
    d = reg.call(ctx, "research_derived", {"target": "t1"})
    obs = d["observation"]
    assert obs["status"] == "fail" and obs["fail_count"] == 1 and obs["ok_count"] == 1

    q = reg.call(ctx, "research_queue", {"target": "t1"})["observation"]
    assert (q["open"], q["bad"], q["pool"], q["faces"]) == (3, 1, 7, 4)

    row = con.execute("SELECT provider, capability FROM tool_calls WHERE tool='research_queue'"
                      " ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["provider"] == "repo:queue_state.sh" and row["capability"] == "state.queue"


def test_signal_and_fuzz_structured(tmp_path):
    con, dom, root, reg, ctx = _setup(tmp_path)
    sig = reg.call(ctx, "research_signal", {"log": "/dev/null"})["observation"]
    assert sig["status"] == "hit" and sig["hit_count"] == 1
    fz = reg.call(ctx, "fuzz_cgi", {"args": "--x", "timeout": 5})["observation"]
    assert fz["tuples"] == 141 and fz["crash"] is True


def test_unparsable_output_is_reported_not_guessed():
    obs = OBSERVERS["fuzz_cgi"]("完全不认识的一坨输出")
    assert obs["status"] == "unparsed", "解析不出就必须如实标 unparsed,不能编字段"


def test_missing_script_is_error_not_silent(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "x.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1")
    reg = Registry()
    register_repo_tools(reg, root)          # 没有 tools/* ⇒ 不注册任何仓库工具
    assert not [s for s in reg.describe() if s["name"].startswith(("research_", "fuzz_", "emu_"))]


def test_ida_query_structured_json_and_fallback(tmp_path):
    """IDA 输出:JSON 优先结构化;纯文本抽取函数名;都不行标 unparsed(不编字段)。"""
    from fwcore.toolkit import OBSERVERS

    json_out = OBSERVERS["ida_query"]('{"functions": ["sub_401000"], "count": 1}')
    assert json_out["status"] == "ok" and json_out["format"] == "json" and "count" in json_out["fields"]

    text_out = OBSERVERS["ida_query"]("hmm sub_4010a0 called at loc_402000 and sub_4010a0 again")
    assert text_out["status"] == "ok" and text_out["function_count"] == 2

    assert OBSERVERS["ida_query"]("什么都没有")["status"] == "unparsed"


def test_ida_tool_registered_and_uses_lifecycle(tmp_path):
    con, dom, root, reg, ctx = _setup(tmp_path)
    _stub(root, "tools/ida_start.sh", '#!/bin/sh\necho started\n')
    _stub(root, "tools/ida_call.sh", '#!/bin/sh\necho "{\\"functions\\": [], \\"count\\": 0}"\n')
    reg2 = Registry()
    register_repo_tools(reg2, root)
    assert "ida_query" in {s["name"] for s in reg2.describe()}
    out = reg2.call(ctx, "ida_query", {"args": "--x"})
    assert out["observation"]["status"] == "ok"
    row = con.execute("SELECT provider, capability FROM tool_calls WHERE tool='ida_query'"
                      " ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["provider"] == "repo:ida_call.sh" and row["capability"] == "debug.ida"


def test_ghidra_missing_capability_is_honest_error(tmp_path, monkeypatch):
    """能力缺失 ⇒ 工具必须报错并说明不可用(不能伪装成功)。不依赖本机是否装 Ghidra。"""
    import fwcore.providers as pv

    class _Missing:
        def best(self):
            return (None, type("P", (), {"status": "missing", "detail": "未安装"})())

    monkeypatch.setattr(pv, "default_capabilities", lambda root: {"debug.ghidra": _Missing()})
    con, dom, root, reg, ctx = _setup(tmp_path)
    _stub(root, "tools/ghidra_scripts/dummy.java", "// stub\n")
    reg2 = Registry()
    register_repo_tools(reg2, root)
    assert "ghidra_query" in {s["name"] for s in reg2.describe()}
    out = reg2.call(ctx, "ghidra_query", {"args": "x"})
    assert out["status"] == "error" and "能力不可用" in out["error"]


def test_ghidra_structured_when_runner_exists(tmp_path, monkeypatch):
    """存在 tools/ghidra_run.sh 且能力可用时:输出结构化 + 记录 provider。"""
    con, dom, root, reg, ctx = _setup(tmp_path)
    _stub(root, "tools/ghidra_scripts/dummy.java", "// stub\n")
    _stub(root, "tools/ghidra_run.sh", '#!/bin/sh\necho "{\\"functions\\": 3, \\"decompiled\\": true}"\n')
    # 让能力探针假装可用(不依赖本机是否装 Ghidra)
    import fwcore.providers as pv
    import fwcore.toolkit as tk

    class _Cap:
        def best(self):
            return (object(), type("P", (), {"status": "ready"})())

    monkeypatch.setattr(pv, "default_capabilities", lambda root: {"debug.ghidra": _Cap()})
    reg2 = Registry()
    tk.register_repo_tools(reg2, root)
    out = reg2.call(ctx, "ghidra_query", {"args": "x"})
    assert out["observation"]["status"] == "ok" and out["observation"]["format"] == "json"
    row = con.execute("SELECT provider FROM tool_calls WHERE tool='ghidra_query'"
                      " ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["provider"] == "repo:ghidra_run.sh"
