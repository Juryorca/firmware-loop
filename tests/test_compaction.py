"""上下文压缩(对齐 Pi 的 compaction):阈值触发、安全切点、摘要保留事实、配对不破坏。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.compact import compact_history, estimate_tokens, find_cut, should_compact
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


def _pair(i: int, size: int = 800) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "fs_read", "arguments": json.dumps({"path": f"f{i}.txt"})}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
    ]


def _history(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out += _pair(i)
    return out


def test_estimate_and_threshold():
    assert estimate_tokens([{"role": "user", "content": "x" * 350}]) == 175   # 2.0 字符/token(实测校准)
    assert should_compact(_history(50), 0) is False          # 0 = 不做阈值压缩
    assert should_compact(_history(50), 5_000) is True
    assert should_compact([{"role": "user", "content": "hi"}], 5_000) is False


def test_should_compact_prefers_last_main_provider_usage():
    """A 915k local estimate must not force compaction when the provider saw 756k."""
    hist = _history(50)
    assert estimate_tokens(hist) > 20_000
    assert should_compact(hist, 20_000, used_tokens=4_000) is False
    assert should_compact(hist, 20_000, used_tokens=13_000) is True


def test_find_cut_never_lands_on_tool_message():
    hist = _history(12)
    cut = find_cut(hist, keep_recent_tokens=1_000)
    assert cut > 0 and hist[cut]["role"] != "tool", "切点落在 tool 上会破坏配对"


def test_compact_keeps_recent_and_calls_summarizer_once():
    hist = _history(12)
    calls = []

    def fake(old):
        calls.append(len(old))
        return "已确认:op 分发在 0x19908;产物 report/artifacts/x.txt"

    new, info = compact_history(hist, fake, keep_recent_tokens=1_500)
    assert len(calls) == 1 and info["compacted"] == calls[0]
    assert new[0]["role"] == "assistant" and "历史摘要" in new[0]["content"]
    assert "0x19908" in new[0]["content"]
    # 保留的尾部仍是完整的 assistant/tool 配对
    pending: set[str] = set()
    for m in new[1:]:
        if m.get("role") == "assistant":
            pending |= {c["id"] for c in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in pending
            pending.discard(m["tool_call_id"])


class _ToolModel:
    name = "toolmodel"

    def __init__(self):
        self.n = 0
        self.seen_summary = False

    def chat(self, messages, tools):
        if any("历史摘要" in (m.get("content") or "") for m in messages):
            self.seen_summary = True
        self.n += 1
        if self.n > 4:
            return Reply(content="FINAL: 收束")
        return Reply(content="", tool_calls=[{
            "id": f"c{self.n}", "name": "fs_read",
            # 每次不同:相同调用会被"重复工具调用"看护正常暂停(不是本测试目标)
            "args": {"path": f"big{self.n}.txt"}, "args_raw": "{}"}])


def test_runtime_compacts_when_context_tokens_reached(tmp_path):
    root = str(tmp_path)
    for i in range(1, 6):
        with open(os.path.join(root, f"big{i}.txt"), "w", encoding="utf-8") as f:
            f.write("y" * 20_000)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    seen = []

    def summarizer(old):
        seen.append(len(old))
        return "摘要:读过大文件,未得出结论"

    model = _ToolModel()
    r = Runner(con, root, model, limits=Limits(max_model_calls=8, context_tokens=8_000,
                                                     keep_recent_tokens=2_000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               summarizer=summarizer)
    r.run(r.start("t1", "压缩"), "t1")
    assert seen, "到阈值必须压缩"
    ev = [json.loads(x["payload"]) for x in
          con.execute("SELECT payload FROM events WHERE kind='run.compacted'")]
    assert ev and ev[0]["after_tokens"] < ev[0]["before_tokens"]
    # 摘要头出现在后续请求里(不是写死的历史),且只出现一次
    assert model.seen_summary, "压缩后的摘要必须进入后续请求"


def _multi_tool_group(i: int, tools: int = 3) -> list[dict]:
    calls = [{"id": f"c{i}_{k}", "type": "function",
              "function": {"name": "fs_read", "arguments": json.dumps({"path": f"{i}_{k}.txt"})}}
             for k in range(tools)]
    out = [{"role": "assistant", "content": "", "tool_calls": calls}]
    out += [{"role": "tool", "tool_call_id": c["id"], "content": "y" * 400} for c in calls]
    return out


def test_cut_keeps_multi_tool_groups_whole():
    hist: list[dict] = []
    for i in range(8):
        hist += _multi_tool_group(i)
    cut = find_cut(hist, keep_recent_tokens=1_200)
    kept = hist[cut:]
    # 保留段不能以 tool 开头,且每个 assistant 的 tool_calls 必须全有对应 tool 结果
    assert kept[0]["role"] != "tool"
    pending = set()
    for m in kept:
        if m.get("role") == "assistant":
            pending |= {c["id"] for c in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in pending, "tool 结果必须属于保留段里的 assistant"
            pending.discard(m["tool_call_id"])
    assert not pending, "保留段里不得有缺失结果的 tool_call"


def test_summarizer_failure_keeps_history_and_cools_down(tmp_path):
    root = str(tmp_path)
    for i in range(1, 6):
        with open(os.path.join(root, f"big{i}.txt"), "w", encoding="utf-8") as f:
            f.write("z" * 20_000)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    calls = {"n": 0, "summaries": 0}

    def bad_summary(old):
        calls["summaries"] += 1
        raise RuntimeError("summarizer down")

    class M(_ToolModel):
        def chat(self, messages, tools):
            calls["n"] += 1
            return super().chat(messages, tools)

    r = Runner(con, root, M(), limits=Limits(max_model_calls=10, context_tokens=8_000,
                                             keep_recent_tokens=2_000,
                                             compact_retry_cooldown_calls=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               summarizer=bad_summary)
    rid = r.start("t1", "失败")
    r.run(rid, "t1")
    ev = [json.loads(x["payload"]) for x in
          con.execute("SELECT payload FROM events WHERE kind='run.compact_failed'")]
    assert ev, "压缩失败必须留事件"
    assert "summarizer down" in ev[0]["error"]
    # 冷却:失败后不会每轮重试(test 的调用数应远小于模型调用数)
    assert calls["summaries"] <= 2, calls
    # 历史没被删:append-only session 里仍有 tool 消息,且没有空摘要头
    msgs = r._load_history(rid)
    assert any(m.get("role") == "tool" for m in msgs)
    assert not any("历史摘要" in (m.get("content") or "") for m in msgs)
