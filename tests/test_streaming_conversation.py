"""流式输出 + 多轮对话持久化(全部离线:Mock SSE / ScriptedAdapter)。"""

from __future__ import annotations

import asyncio
import json
import time

from fwapp.args import AppOptions
from fwapp.config import resolve
from fwapp.modes.interactive import FwApp
from fwapp.runtime import AppRuntime
from fwapp.widgets.editor import PromptEditor
from fwapp.widgets.transcript import MessageWidget
from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.model_client import OpenAICompatModel
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner
from fwcore.streaming import StreamError, StreamEvent


def make_ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    con = _db.connect(_db.default_db_path(str(root)))
    Domain(con, str(root)).create_target("t1")
    con.close()
    cfg = resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))
    return root, cfg


def test_mock_sse_events_include_text_tools_usage():
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"state_add_hypothesis","arguments":"{\\"target\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":":\\"t1\\"}"}}]},"finish_reason":"tool_calls"}],'
        '"usage":{"prompt_tokens":7,"completion_tokens":3}}',
        "data: [DONE]",
    ]
    model = OpenAICompatModel(model="m", base_url="https://example.test", api_key="k",
                              stream_transport=lambda url, headers, payload: lines)
    events = list(model.stream([{"role": "user", "content": "hi"}], []))
    kinds = [e.type for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "".join(e.text for e in events if e.type == "text_delta") == "你好"
    assert any(e.type == "toolcall_start" and e.name == "state_add_hypothesis"
               and e.call_id == "c1" for e in events)
    args = "".join(e.arguments for e in events if e.type == "toolcall_delta")
    assert json.loads(args) == {"target": "t1"}
    done = events[-1]
    assert done.finish_reason == "tool_calls"
    assert done.usage["prompt_tokens"] == 7


def test_runner_streams_and_assembles_tool_arguments(tmp_path):
    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    dom = Domain(con, str(root))
    script = [
        {"content": "先登记假设。", "tool_calls": [{
            "id": "c1", "name": "state_add_hypothesis",
            "args": {"target": "t1", "title": "分片工具参数"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]
    seen = []
    r = Runner(con, str(root), ScriptedAdapter(script=script, chunk_size=2),
               policy=Policy(allow_high_risk=True), mcp=False, watchdog=False,
               on_stream=seen.append)
    rid = r.start("t1", "开始")
    res = r.run(rid, "t1")
    assert res.state == "finished"
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
    assert con.execute("SELECT status FROM tool_calls").fetchone()["status"] == "ok"
    # 工具参数是分片到达后**完整**组装的
    deltas = "".join(e.get("text", "") for e in seen if e["type"] == "toolcall_delta")
    assert json.loads(deltas)["title"] == "分片工具参数"
    # 对话记录:user / assistant / tool / assistant
    roles = [e["role"] for e in dom.conversation(rid)]
    assert roles[:3] == ["user", "assistant", "tool"]
    assert dom.conversation(rid)[2]["tool_call_id"] == "c1"
    con.close()


def test_stream_cancel_pauses_and_keeps_partial(tmp_path):
    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    dom = Domain(con, str(root))

    class Slow(ScriptedAdapter):
        def stream(self, messages, tools, *, control=None):
            yield StreamEvent("start")
            for ch in ["一", "二", "三", "四"]:
                if control is not None:
                    control.raise_if_cancelled()
                time.sleep(0.25)
                yield StreamEvent("text_delta", text=ch)
            yield StreamEvent("done", finish_reason="stop")

    r = Runner(con, str(root), Slow(script=[]), mcp=False, watchdog=False)
    rid = r.start("t1", "开始")
    import threading
    th = threading.Thread(target=lambda: r.run(rid, "t1"))
    th.start()
    t0 = time.time()
    while r.stream_control is None and time.time() - t0 < 3:
        time.sleep(0.02)
    time.sleep(0.35)
    assert r.stream_control is not None
    r.stream_control.cancel()
    th.join(timeout=5)
    row = con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "paused"
    entries = dom.conversation(rid)
    assert any(e["role"] == "assistant" and e["status"] == "cancelled" for e in entries)
    assert con.execute("SELECT model_calls FROM runs WHERE id=?", (rid,)).fetchone()[0] == 1
    con.close()


def test_stream_error_partial_and_budget_reserved(tmp_path):
    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    dom = Domain(con, str(root))

    class Broken(ScriptedAdapter):
        def stream(self, messages, tools, *, control=None):
            yield StreamEvent("start")
            yield StreamEvent("text_delta", text="部分")
            raise StreamError("连接断开")

    r = Runner(con, str(root), Broken(script=[]), mcp=False, watchdog=False)
    rid = r.start("t1", "开始")
    res = r.run(rid, "t1")
    assert res.state == "paused"
    assert (res.handback or "").startswith("model_error")
    # 预算:请求已发起就必须记账,断流不退还也不重复
    assert con.execute("SELECT model_calls FROM runs WHERE id=?", (rid,)).fetchone()[0] == 1
    entries = dom.conversation(rid)
    assert any(e["role"] == "assistant" and e["status"] == "error"
               and e["content"] == "部分" for e in entries)
    con.close()


def test_multiturn_user_messages_persisted_and_restored(tmp_path):
    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    dom = Domain(con, str(root))

    class Recorder(ScriptedAdapter):
        requests: list = []

        def chat(self, messages, tools):
            self.requests.append(messages)
            return super().chat(messages, tools)

    rec = Recorder(script=[{"content": "FINAL: 一", "tool_calls": []},
                           {"content": "FINAL: 二", "tool_calls": []}])
    r = Runner(con, str(root), rec, mcp=False, watchdog=False)
    rid = r.start("t1", "第一问")
    r.run(rid, "t1")
    r.resume(rid, goal="第二问", user_client_id="u2")
    users = [e["content"] for e in dom.conversation(rid, roles=("user",))]
    assert users == ["第一问", "第二问"]
    # 幂等:同一 client_id 重试不产生第二条
    before = len(dom.conversation(rid, roles=("user",)))
    dom.add_conversation(rid, "user", "第二问", client_id="u2")
    assert len(dom.conversation(rid, roles=("user",))) == before
    # 模型上下文包含完整多轮用户输入,且当前目标不重复
    text = json.dumps(rec.requests[-1], ensure_ascii=False)
    assert text.count("第一问") == 1 and text.count("第二问") == 1
    con.close()

    # 关闭后重新打开:对话仍在,不重复
    rt = AppRuntime(cfg, target="t1",
                    adapter_factory=lambda name: ScriptedAdapter(
                        script=[{"content": "FINAL: 三", "tool_calls": []}]),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.resume_run(rid)
        items = sess.transcript()
        assert [i.text for i in items if i.kind == "user"] == ["第一问", "第二问"]
        sess.submit("第三问")
        users2: list[str] = []
        t0 = time.time()
        while time.time() - t0 < 10:
            users2 = [e["content"] for e in sess.services.domain.conversation(
                rid, roles=("user",))]
            if users2 == ["第一问", "第二问", "第三问"]:
                break
            time.sleep(0.05)
        assert users2 == ["第一问", "第二问", "第三问"]
        items2 = sess.transcript()
        assert [i.text for i in items2 if i.kind == "user"] == ["第一问", "第二问", "第三问"]
    finally:
        rt.dispose()


def test_tui_shows_streaming_deltas_before_turn_finishes(tmp_path):
    root, cfg = make_ws(tmp_path)

    class SlowStream(ScriptedAdapter):
        def stream(self, messages, tools, *, control=None):
            yield StreamEvent("start")
            time.sleep(0.35)
            yield StreamEvent("text_delta", text="第一段")
            time.sleep(0.35)
            yield StreamEvent("text_delta", text="第二段")
            yield StreamEvent("text_delta", text="\nFINAL: 完成")
            yield StreamEvent("done", finish_reason="stop")

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: SlowStream(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "开始"
            await pilot.press("enter")
            saw_partial = False
            for _ in range(40):
                await pilot.pause(0.05)
                if app._stream_msg is not None and app._stream_msg._text:
                    saw_partial = True
                    break
            assert saw_partial, "token 到达后应立即创建并更新 assistant 消息"
            assert app.sess.busy, "首个 token 到达时 turn 应仍在执行"
            for _ in range(80):
                await pilot.pause(0.05)
                if not app.sess.busy and app._stream_msg is None:
                    break
            await pilot.pause(0.2)
            widgets = [w for w in app.query(MessageWidget) if w.role == "assistant"]
            assert widgets and widgets[-1]._text == "第一段第二段\nFINAL: 完成"
            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_session_cancel_closes_stream_and_pauses(tmp_path):
    root, cfg = make_ws(tmp_path)

    class Slow(ScriptedAdapter):
        def stream(self, messages, tools, *, control=None):
            yield StreamEvent("start")
            for ch in ["a", "b", "c", "d", "e"]:
                if control is not None:
                    control.raise_if_cancelled()
                time.sleep(0.4)
                yield StreamEvent("text_delta", text=ch)
            yield StreamEvent("done", finish_reason="stop")

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("开始")
        t0 = time.time()
        while time.time() - t0 < 3:
            runner = sess._runner_ref
            if sess.busy and runner is not None and runner.stream_control is not None:
                break
            time.sleep(0.02)
        assert sess.busy
        t_cancel = time.time()
        assert sess.cancel("测试取消")
        while sess.busy and time.time() - t_cancel < 2.0:
            time.sleep(0.02)
        assert time.time() - t_cancel < 1.5, "取消应关闭网络请求,而不是等下一片/超时"
        row = sess.services.con.execute(
            "SELECT state,handback_reason FROM runs WHERE id=?", (sess.run_id,)).fetchone()
        assert row["state"] == "paused"
        assert (row["handback_reason"] or "").startswith("user_stop")
        rid = sess.run_id
        assert rid
        entries = sess.services.domain.conversation(rid)
        assert any(e["role"] == "assistant" and e["status"] == "cancelled" for e in entries)
    finally:
        rt.dispose()


def test_queued_message_persisted_then_updates_to_ok(tmp_path):
    root, cfg = make_ws(tmp_path)
    slow = ScriptedAdapter(script=[{"content": "\nFINAL: 一", "tool_calls": []},
                                   {"content": "\nFINAL: 二", "tool_calls": []}],
                           chunk_size=4, stream_delay=0.2)
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: slow,
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("第一问")
        t0 = time.time()
        while (not sess.busy or sess.run_id is None) and time.time() - t0 < 3:
            time.sleep(0.02)
        rid = sess.run_id
        assert rid
        sess.submit("第二问")                 # 运行中 ⇒ 排队并立即持久化
        queued = None
        for _ in range(60):
            rows = sess.services.domain.conversation(rid, roles=("user",))
            if len(rows) == 2:
                queued = rows[-1]
                break
            time.sleep(0.02)
        assert queued is not None and queued["status"] == "queued"
        t0 = time.time()
        while time.time() - t0 < 10:
            rows = sess.services.domain.conversation(rid, roles=("user",))
            if len(rows) == 2 and all(r["status"] == "ok" for r in rows) and not sess.busy:
                break
            time.sleep(0.05)
        rows = sess.services.domain.conversation(rid, roles=("user",))
        assert [r["content"] for r in rows] == ["第一问", "第二问"]
        assert [r["status"] for r in rows] == ["ok", "ok"]
    finally:
        rt.dispose()


def test_cancel_marks_queued_message_cancelled(tmp_path):
    root, cfg = make_ws(tmp_path)
    slow = ScriptedAdapter(script=[{"content": "\nFINAL: 一", "tool_calls": []}],
                           chunk_size=4, stream_delay=0.3)
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: slow,
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("第一问")
        t0 = time.time()
        while (not sess.busy or sess.run_id is None) and time.time() - t0 < 3:
            time.sleep(0.02)
        rid = sess.run_id
        assert rid
        sess.submit("第二问")
        for _ in range(60):
            rows = sess.services.domain.conversation(rid, roles=("user",))
            if len(rows) == 2 and rows[-1]["status"] == "queued":
                break
            time.sleep(0.02)
        assert sess.cancel("取消")
        t0 = time.time()
        while sess.busy and time.time() - t0 < 3:
            time.sleep(0.02)
        rows = sess.services.domain.conversation(rid, roles=("user",))
        assert [r["status"] for r in rows] == ["ok", "cancelled"]
        assert "第二问" in sess.take_returned() or "第二问" in [r["content"] for r in rows]
    finally:
        rt.dispose()
