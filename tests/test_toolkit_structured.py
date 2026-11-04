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
    os.chmod(path, 0o755)


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
