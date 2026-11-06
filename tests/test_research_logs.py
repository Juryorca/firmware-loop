"""研究日志:叙述进 DB(不再是 Markdown 专属),视图可控。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.cli import render_view
from fwcore.domain import Domain, InvariantViolation
from fwcore.policy import Policy
from fwcore.tools import ToolContext, default_registry, state_add_log


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t", version="1")
    return con, dom, root


def test_add_log_rejects_unknown_kind(tmp_path):
    _con, dom, _root = _mk(tmp_path)
    with pytest.raises(InvariantViolation):
        dom.add_log("t", "random", "标题", "正文")


def test_add_log_persists_and_view_shows_it(tmp_path):
    con, dom, root = _mk(tmp_path)
    lid = dom.add_log("t", "stop", "轮 1 停止", "本轮: H-01 → 定论 no_finding")
    row = con.execute("SELECT kind,body,source,needs_review FROM research_logs WHERE id=?",
                      (lid,)).fetchone()
    assert row["kind"] == "stop" and "H-01" in row["body"]
    assert row["source"] == "agent" and row["needs_review"] == 0
    view = render_view(con, root, "t")
    assert "最近日志" in view and "[stop]" in view and "轮 1 停止" in view


def test_state_add_log_tool_registered_and_writes(tmp_path):
    con, dom, root = _mk(tmp_path)
    assert "state_add_log" in [t["name"] for t in default_registry().describe()]
    ctx = ToolContext(domain=dom, root=root, policy=Policy())
    out = state_add_log(ctx, {"target": "t", "kind": "decision", "title": "选了 A",
                              "body": "理由:B 代价更高", "needs_review": True})
    assert out["status"] == "ok" and out["log_id"]
    row = con.execute("SELECT needs_review FROM research_logs WHERE id=?", (out["log_id"],)).fetchone()
    assert row["needs_review"] == 1
