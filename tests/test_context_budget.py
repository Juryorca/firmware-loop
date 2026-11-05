"""上下文预算:请求前收敛(不是只在检查点裁剪)+ 每次调用记账。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner, _bound_history, _context_stats


def _big(n: int) -> str:
    return "x" * n


def test_bound_history_shrinks_oldest_and_keeps_structure():
    history: list[dict] = [{"role": "user", "content": "goal"}]
    for i in range(20):
        history.append({"role": "assistant", "content": "",
                        "tool_calls": [{"id": f"c{i}", "type": "function",
                                        "function": {"name": "shell_exec", "arguments": "{}"}}]})
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": _big(2000)})
    out = _bound_history(history, 12000, keep_full=3)
    sizes = [len(m["content"]) for m in out if m["role"] == "tool"]
    assert sum(sizes) <= 12000, sum(sizes)
    # 最近 3 条保持全文,老的被收窄且保留 id/结构
    assert sizes[-3:] == [2000, 2000, 2000]
    assert all(s < 2000 for s in sizes[:-3])
    assert [m.get("tool_call_id") for m in out if m["role"] == "tool"] == [f"c{i}" for i in range(20)]
    touched = [m["content"] for m in out if m["role"] == "tool" and m["tool_call_id"] != "c19"]
    assert any("收敛" in c for c in touched)


def test_bound_history_noop_when_under_budget():
    history = [{"role": "tool", "tool_call_id": "c", "content": _big(100)}]
    out = _bound_history(history, 1000)
    assert out[0]["content"] == _big(100)


def test_context_stats_breakdown():
    s = _context_stats("S" * 50, [
        {"role": "tool", "content": "t" * 30},
        {"role": "assistant", "content": "a" * 10, "reasoning_content": "r" * 5},
    ])
    assert s == {"system": 50, "tool": 30, "assistant": 15, "other": 0, "total": 45}


class _Chatty:
    name = "chatty"

    def __init__(self, calls: int):
        self.calls = 0
        self.left = calls

    def chat(self, messages, tools):
        self.calls += 1
        if self.left <= 0:
            return Reply(content="FINAL: 收束")
        self.left -= 1
        return Reply(content="", tool_calls=[{
            "id": f"c{self.calls}", "name": "shell_exec",
            # 每次命令不同:相同调用会被"重复工具调用"看护正确暂停,不是本测试的目标
            "args": {"command": f"printf 'x%.0s' $(seq 1 3000) # {self.calls}",
                     "mode": "shell"},
            "args_raw": "{}"}])


def test_every_call_logged_and_context_bounded(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    r = Runner(con, root, _Chatty(6),
               limits=Limits(max_model_calls=10, max_context_chars=3000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.run(r.start("t1", "测上下文"), "t1")
    events = [json.loads(x["payload"]) for x in
              con.execute("SELECT payload FROM events WHERE kind='model.call' ORDER BY ts")]
    assert len(events) == 7, f"每次调用都要记账,实际 {len(events)}"
    assert all(e["usage_known"] is False for e in events), "没有 usage 不得猜 token"
    assert all(e["prompt_tokens"] is None for e in events)
    last = events[-1]
    assert last["history_chars"] <= 3000 + 2500, last["history_chars"]
    assert res.state in ("finished", "paused")


def test_default_is_no_trimming():
    """默认 0 = 不裁剪:完整会话历史照常发送,不做"随机字符上限"式截断。"""
    from fwcore.limits import Limits
    assert Limits().max_context_chars == 0
    history: list[dict] = [{"role": "tool", "tool_call_id": f"c{i}",
                            "content": _big(5000)} for i in range(40)]
    out = _bound_history(history, Limits().max_context_chars)
    assert out is history and all(len(m["content"]) == 5000 for m in out)


def test_trim_note_keeps_artifact_path():
    history: list[dict] = [{"role": "tool", "tool_call_id": "c1", "content": _big(3000)}]
    out = _bound_history(history, 1000, keep_full=0,
                         artifact_of=lambda tcid: f"state/artifacts/{tcid}.txt")
    note = out[0]["content"]
    assert "state/artifacts/c1.txt" in note and out[0]["tool_call_id"] == "c1"


def test_trim_keeps_tool_pairing_and_resume(tmp_path):
    """裁剪不得破坏 assistant(tool_calls) ↔ tool(tool_call_id) 配对,checkpoint 恢复后仍成对。"""
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    r = Runner(con, root, _Chatty(4), limits=Limits(max_model_calls=8, max_context_chars=1200),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.run(r.start("t1", "配对"), "t1")
    ck = con.execute("SELECT messages FROM checkpoints ORDER BY step DESC LIMIT 1").fetchone()
    msgs = json.loads(ck["messages"])
    pending: set[str] = set()
    for m in msgs:
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                pending.add(c["id"])
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in pending, "tool 消息必须能对上前面的 tool_call"
            pending.discard(m["tool_call_id"])
    assert res.state in ("finished", "paused")
