"""Queued user input is delivered at the next model boundary, not after a whole turn."""
from __future__ import annotations

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply, ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner


class ToolThenFinal:
    name = "test"

    def __init__(self):
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return Reply(content="", tool_calls=[{
                "id": "c1", "name": "shell_exec",
                "args": {"command": "true"},
            }])
        return Reply(content="FINAL: done", tool_calls=[])


def _setup(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    con = _db.connect(_db.default_db_path(str(root)))
    Domain(con, str(root)).create_target("t1")
    return con, str(root)


def test_runner_injects_steering_before_the_next_model_request(tmp_path):
    con, root = _setup(tmp_path)
    adapter = ToolThenFinal()
    pending = {"text": "steer during the tool batch", "client_id": "u2"}

    def peek(rid, target):
        # First callback runs before the first model request; only make the
        # message visible after that request has started, like a live user.
        return dict(pending) if adapter.calls else None

    def ack(client_id):
        if pending.get("client_id") == client_id:
            pending.clear()

    try:
        runner = Runner(
            con, root, adapter, limits=Limits(max_model_calls=5),
            policy=Policy(allow_high_risk=True),
            on_steering=peek, on_steering_ack=ack,
        )
        rid = runner.start("t1", "first")
        result = runner.run(rid, "t1", "first")
        assert result.state == "finished"
        assert len(adapter.calls) == 2
        second = adapter.calls[1]
        assert any(m.get("role") == "user" and "steer during" in (m.get("content") or "")
                   for m in second)
        rows = Domain(con, root).conversation(rid, roles=("user",))
        assert [r["content"] for r in rows][-1] == "steer during the tool batch"
        assert rows[-1]["status"] == "ok"
    finally:
        con.close()


def test_delivered_queue_row_gets_current_parent_and_time(tmp_path):
    con, root = _setup(tmp_path)
    dom = Domain(con, root)
    try:
        runner = Runner(con, root, ScriptedAdapter(script=[]), mcp=False, watchdog=False)
        rid = runner.start("t1", "first")
        before = dom.add_conversation(rid, "assistant", "before", client_id="a1")
        queued = dom.add_conversation(
            rid, "user", "queued", status="queued", client_id="q1",
            meta={"source": "tui-queue"})
        after = dom.add_conversation(rid, "assistant", "after", client_id="a2")
        assert dom.add_conversation(
            rid, "user", "queued", status="ok", client_id="q1",
            meta={"source": "tui-queue"}) == queued
        row = con.execute(
            "SELECT id,status,parent_id,created_at FROM conversation_entries WHERE id=?",
            (queued,)).fetchone()
        assert row["status"] == "ok"
        assert row["parent_id"] == after
        assert row["parent_id"] != before
        assert con.execute(
            "SELECT COUNT(*) n FROM conversation_entries WHERE run_id=? AND client_id=?",
            (rid, "q1")).fetchone()["n"] == 1
        assert [r["id"] for r in dom.conversation(rid, roles=("user",))][-1] == queued
    finally:
        con.close()
