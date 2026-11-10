"""Session 协议边界：持久化顺序、工具结果一致性、中断/压缩恢复。"""

from __future__ import annotations

import json
import os
import threading
import time

from fwcore import db as _db
from fwcore.domain import ConversationPersistError, Domain
from fwcore.limits import Limits
from fwcore.models import Reply, ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner
from fwcore.streaming import StreamCancelled, StreamError, StreamEvent
from fwcore.tools import ToolSpec


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _runner(con, root, model):
    return Runner(con, root, model, limits=Limits(max_model_calls=20),
                  policy=Policy(allow_high_risk=True), mcp=False, watchdog=False)


def _meta(row) -> dict:
    try:
        return json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        return {}


def test_context_message_is_before_assistant_in_db_and_reload(tmp_path):
    con, _dom, root = _setup(tmp_path)
    model = ScriptedAdapter(script=[
        {"content": "调用工具", "tool_calls": [{
            "id": "c1", "name": "state_add_hypothesis",
            "args": {"target": "t1", "title": "顺序测试"}}]},
        {"content": "FINAL: done", "tool_calls": []},
    ])
    r = _runner(con, root, model)
    rid = r.start("t1", "开始")
    r.run(rid, "t1")

    rows = con.execute("SELECT role,meta FROM conversation_entries WHERE run_id=?"
                       " ORDER BY rowid", (rid,)).fetchall()
    first_assistant = next(i for i, row in enumerate(rows)
                           if row["role"] == "assistant"
                           and _meta(row).get("tool_calls"))
    assert rows[first_assistant - 1]["role"] == "user"
    assert _meta(rows[first_assistant - 1]).get("context_only") is True

    history = r._load_history(rid)
    # Every assistant tool_call is preceded by its context message and followed
    # by matching tool results; no assistant -> context -> tool inversion.
    pending = set()
    for msg in history:
        if msg.get("role") == "assistant":
            pending |= {c["id"] for c in msg.get("tool_calls") or []}
        elif msg.get("role") == "tool":
            assert msg["tool_call_id"] in pending
            pending.discard(msg["tool_call_id"])


def test_large_structured_tool_result_is_identical_after_reload(tmp_path):
    con, _dom, root = _setup(tmp_path)

    def big(ctx, args):
        return {"status": "ok", "structured": {"blob": "x" * 100_000}}

    model = ScriptedAdapter(script=[
        {"content": "", "tool_calls": [{"id": "c1", "name": "big", "args": {}}]},
        {"content": "FINAL: done", "tool_calls": []},
    ])
    r = _runner(con, root, model)
    r.registry.register(ToolSpec("big", "big structured output", {"type": "object"}, big))
    rid = r.start("t1", "读取")
    ran = r.run(rid, "t1")

    stored = con.execute("SELECT content FROM conversation_entries WHERE run_id=?"
                         " AND role='tool' ORDER BY rowid", (rid,)).fetchone()["content"]
    live = next(m["content"] for m in ran.history if m.get("role") == "tool")
    history = r._load_history(rid)
    loaded = next(m["content"] for m in history if m.get("role") == "tool")
    assert stored == live
    assert stored == loaded
    payload = json.loads(loaded)
    path = payload.get("structured_full_output_path")
    if path and os.path.exists(path):
        os.unlink(path)


class _Interrupted(ScriptedAdapter):
    def __init__(self, error):
        super().__init__(script=[])
        self.error = error

    def stream(self, messages, tools, *, control=None):
        yield StreamEvent("start")
        yield StreamEvent("text_delta", text="部分")
        yield StreamEvent("toolcall_start", index=0, call_id="c1",
                          name="fs_read")
        yield StreamEvent("toolcall_delta", index=0, arguments='{"path"')
        raise self.error("流中断")


def test_cancelled_or_errored_tool_call_never_becomes_dangling(tmp_path):
    con, _dom, root = _setup(tmp_path)
    for exc in (StreamCancelled, StreamError):
        r = _runner(con, root, _Interrupted(exc))
        rid = r.start("t1", "开始")
        res = r.run(rid, "t1")
        assert res.state == "paused"
        rows = con.execute("SELECT role,content,status,meta FROM conversation_entries"
                           " WHERE run_id=? ORDER BY rowid", (rid,)).fetchall()
        assistants = [row for row in rows if row["role"] == "assistant"]
        assert assistants
        assert all(not _meta(row).get("tool_calls") for row in assistants)
        for history in (res.history, r._load_history(rid)):
            assert all("tool_calls" not in m for m in history
                       if m.get("role") == "assistant")
            assert not [m for m in history if m.get("role") == "tool"]


def test_invalid_compaction_cut_falls_back_to_raw_session(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, ScriptedAdapter(script=[]))
    rid = r.start("t1", "原始问题")
    dom.add_conversation(rid, "assistant", "原始回答", client_id="a1")
    dom.add_compaction(rid, "旧摘要", "CV-does-not-exist")

    history = r._load_history(rid)
    text = "\n".join(str(m.get("content") or "") for m in history)
    assert "旧摘要" not in text
    assert "原始问题" in text and "原始回答" in text


def test_context_persist_failure_aborts_before_model_call(tmp_path):
    con, _dom, root = _setup(tmp_path)
    model = ScriptedAdapter(script=[{"content": "FINAL: 不应调用", "tool_calls": []}])
    r = _runner(con, root, model)
    rid = r.start("t1", "开始")
    orig = r._conversation_write

    def fail_context(*args, **kwargs):
        if len(args) > 1 and args[1] == "user" and (kwargs.get("meta") or {}).get("context_only"):
            raise ConversationPersistError("模拟 context 写入失败")
        return orig(*args, **kwargs)

    r._conversation_write = fail_context
    res = r.run(rid, "t1")
    assert res.state == "paused"
    assert "context_persist_failed" in (res.handback or "")
    assert con.execute("SELECT COUNT(*) n FROM events WHERE ref=? AND kind='model.call'",
                       (rid,)).fetchone()["n"] == 0
    assert con.execute("SELECT model_calls FROM runs WHERE id=?", (rid,)).fetchone()[0] == 0
    assert not [e for e in _dom.conversation(rid) if e["role"] == "assistant"]


def test_cancel_after_complete_model_reply_adds_cancelled_results(tmp_path):
    con, _dom, root = _setup(tmp_path)

    class CancelAfterReply:
        name = "cancel-after"

        def __init__(self, event):
            self.event = event

        def chat(self, messages, tools):
            self.event.set()
            return Reply(content="", tool_calls=[{
                "id": "c1", "name": "fs_read",
                "args": {"path": "x"}, "args_raw": "{}"}])

    event = threading.Event()
    model = CancelAfterReply(event)
    r = _runner(con, root, model)
    r.stop_event = event
    rid = r.start("t1", "开始")
    res = r.run(rid, "t1")
    assert res.state == "paused"
    rows = con.execute("SELECT role,status,tool_call_id FROM conversation_entries"
                       " WHERE run_id=? ORDER BY rowid", (rid,)).fetchall()
    assert any(row["role"] == "assistant" for row in rows)
    assert any(row["role"] == "tool" and row["tool_call_id"] == "c1"
               and row["status"] == "cancelled" for row in rows)
    for hist in (res.history, r._load_history(rid)):
        pending = set()
        for msg in hist:
            if msg.get("role") == "assistant":
                pending |= {c["id"] for c in msg.get("tool_calls") or []}
            elif msg.get("role") == "tool":
                assert msg["tool_call_id"] in pending
                pending.discard(msg["tool_call_id"])


def test_retain_none_compaction_keeps_entries_appended_after_it(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, ScriptedAdapter(script=[]))
    rid = r.start("t1", "原始问题")
    dom.add_conversation(rid, "assistant", "不应保留的旧回答", client_id="old")
    dom.add_compaction(rid, "retain-none 摘要", None)
    time.sleep(0.01)
    dom.add_conversation(rid, "user", "压缩之后的新消息", client_id="new")

    history = r._load_history(rid)
    text = "\n".join(str(m.get("content") or "") for m in history)
    assert "retain-none 摘要" in text
    assert "压缩之后的新消息" in text
    assert "不应保留的旧回答" not in text


def test_resume_rebuilds_same_model_visible_prefix(tmp_path):
    con, _dom, root = _setup(tmp_path)

    class Capture:
        name = "capture"

        def __init__(self):
            self.requests = []

        def chat(self, messages, tools):
            self.requests.append(messages)
            return Reply(content="FINAL: done")

    first = ScriptedAdapter(script=[
        {"content": "调用", "tool_calls": [{
            "id": "c1", "name": "state_add_hypothesis",
            "args": {"target": "t1", "title": "恢复顺序测试"}}]},
        {"content": "FINAL: first", "tool_calls": []},
    ])
    r = _runner(con, root, first)
    rid = r.start("t1", "")
    r.run(rid, "t1")
    before = r._load_history(rid)

    cap = Capture()
    r2 = _runner(con, root, cap)
    r2.resume(rid)
    assert cap.requests
    got = cap.requests[0]
    assert got[0].get("role") == "system"
    assert got[1:1 + len(before)] == before


def test_invalid_compaction_cut_falls_back_to_previous_valid_one(tmp_path):
    con, dom, root = _setup(tmp_path)
    r = _runner(con, root, ScriptedAdapter(script=[]))
    rid = r.start("t1", "原始问题")
    first = con.execute("SELECT id FROM conversation_entries WHERE run_id=?"
                        " ORDER BY rowid LIMIT 1", (rid,)).fetchone()["id"]
    dom.add_compaction(rid, "有效摘要", first)
    dom.add_conversation(rid, "assistant", "保留回答", client_id="kept")
    dom.add_compaction(rid, "无效摘要", "CV-missing")

    text = "\n".join(str(m.get("content") or "") for m in r._load_history(rid))
    assert "有效摘要" in text
    assert "无效摘要" not in text
    assert "原始问题" in text and "保留回答" in text


def test_stream_assistant_persist_failure_pauses_without_fake_history(tmp_path):
    con, _dom, root = _setup(tmp_path)

    class StreamDone(ScriptedAdapter):
        def stream(self, messages, tools, *, control=None):
            yield StreamEvent("start")
            yield StreamEvent("text_delta", text="部分回复")
            yield StreamEvent("done", finish_reason="stop")

    r = _runner(con, root, StreamDone(script=[]))
    rid = r.start("t1", "开始")
    orig = r._conversation_write

    def fail_assistant(*args, **kwargs):
        if len(args) > 1 and args[1] == "assistant":
            raise ConversationPersistError("模拟 assistant 写入失败")
        return orig(*args, **kwargs)

    r._conversation_write = fail_assistant
    res = r.run(rid, "t1")
    assert res.state == "paused"
    assert "conversation_error" in (res.handback or "")
    assert not [e for e in _dom.conversation(rid) if e["role"] == "assistant"]
