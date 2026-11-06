"""上下文策略:完整历史照常发送(不做字符上限裁剪)+ 每次调用记账。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner, _context_stats


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
        self.sizes: list[int] = []

    def chat(self, messages, tools):
        self.calls += 1
        self.sizes.append(sum(len(m.get("content") or "") for m in messages))
        if self.left <= 0:
            return Reply(content="FINAL: 收束")
        self.left -= 1
        return Reply(content="", tool_calls=[{
            "id": f"c{self.calls}", "name": "shell_exec",
            "args": {"command": f"printf 'x%.0s' $(seq 1 3000) # {self.calls}",
                     "mode": "shell"},
            "args_raw": "{}"}])


def test_full_history_is_sent_and_every_call_logged(tmp_path):
    """历史不做预算裁剪:旧工具输出不会被悄悄缩短(长程研究的上下文就是资产)。"""
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    model = _Chatty(5)
    r = Runner(con, root, model, limits=Limits(max_model_calls=10),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.run(r.start("t1", "测上下文"), "t1")
    events = [json.loads(x["payload"]) for x in
              con.execute("SELECT payload FROM events WHERE kind='model.call' ORDER BY ts")]
    assert len(events) == 6, f"每次调用都要记账,实际 {len(events)}"
    assert all(e["usage_known"] is False for e in events), "没有 usage 不得猜 token"
    assert all(e["prompt_tokens"] is None for e in events)
    # 上下文单调增长(没有被裁剪的迹象),且统计与实际发送一致
    assert model.sizes == sorted(model.sizes)
    assert events[-1]["history_chars"] > events[0]["history_chars"]
    assert res.state in ("finished", "paused")


def test_tool_pairing_survives_checkpoint_resume(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    r = Runner(con, root, _Chatty(4), limits=Limits(max_model_calls=8),
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


def test_checkpoint_shrink_keeps_stdout_path(tmp_path):
    """磁盘保护式截断:shell_exec 的输出文件要走 stdout_path,恢复后能回原文。"""
    from fwcore.runtime import _shrink_history

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    tgt = dom.create_target("t1", version="1")
    tid = getattr(tgt, "id", tgt)
    rid = "R-test"
    con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at) VALUES(?,?,?,?,?)",
                (rid, tid, "running", "2026-11-06T00:00:00Z", "2026-11-06T00:00:00Z"))
    stdout = os.path.join(root, "state", "artifacts", "TC-1.stdout.txt")
    os.makedirs(os.path.dirname(stdout), exist_ok=True)
    with open(stdout, "w", encoding="utf-8") as f:
        f.write("x" * 500)
    con.execute("INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,stdout_path,created_at)"
                " VALUES(?,?,?,?,?,'ok',?,?)",
                ("TC-1", "k1", rid, "shell_exec", "{}", stdout, "2026-11-06T00:00:01Z"))
    r = Runner(con, root, _Chatty(0), limits=Limits(max_model_calls=1))
    history = [{"role": "tool", "tool_call_id": "TC-1", "content": "y" * 400}]
    out = _shrink_history(history, keep_full=0, artifact_of=r._artifact_of)
    assert "state/artifacts/TC-1.stdout.txt" in out[0]["content"]
    assert out[0]["tool_call_id"] == "TC-1"


def test_sanitize_history_repairs_legacy_truncated_json(tmp_path):
    """旧 checkpoint 里的非法 JSON 工具回复:加载时包成合法结构,原文节选保留。"""
    from fwcore.runtime import _sanitize_history

    history = [{"role": "tool", "tool_call_id": "call_1",
                "content": '{"status": "ok", "text": "AAAA…(截断'}]
    out = _sanitize_history(history)
    d = json.loads(out[0]["content"])
    assert d["history_corrupt"] is True and "AAAA" in d["excerpt"]
    # 合法 JSON 不动
    ok = [{"role": "tool", "tool_call_id": "c2", "content": '{"status":"ok"}'}]
    assert _sanitize_history(ok)[0]["content"] == '{"status":"ok"}'
