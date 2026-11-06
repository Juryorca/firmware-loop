"""压缩场景模拟(全离线):70/85/90/95% 占用、连续三次压缩、压缩后继续工具调用、resume/advance。

不生成接近 1M 的真实历史:用合成消息 + 可控 summarizer。
"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.compact import (
    compact_history,
    estimate_tokens,
    should_compact,
    threshold_tokens,
)
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner

CTX = 30_000          # 合成窗口(小,便于离线快速触发)


def _unit(i: int, chars: int = 600) -> list[dict]:
    calls = [{"id": f"a{i}", "type": "function",
              "function": {"name": "fs_read", "arguments": json.dumps({"path": f"f{i}.txt"})}},
             {"id": f"b{i}", "type": "function",
              "function": {"name": "shell_exec", "arguments": json.dumps(
                  {"command": f"grep -n x f{i}.txt", "mode": "auto"})}}]
    return [{"role": "assistant", "content": "", "tool_calls": calls},
            {"role": "tool", "tool_call_id": f"a{i}", "content": "x" * chars},
            {"role": "tool", "tool_call_id": f"b{i}", "content": "y" * chars}]


def _history_for_tokens(target: int) -> list[dict]:
    out: list[dict] = []
    while estimate_tokens(out) < target:
        out += _unit(len(out) // 3)
    return out


def _verify_pairs(msgs: list[dict]) -> None:
    pending: set[str] = set()
    for m in msgs:
        if m.get("role") == "assistant":
            pending |= {c["id"] for c in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in pending, f"悬空 tool 结果: {m['tool_call_id']}"
            pending.discard(m["tool_call_id"])
    assert not pending, f"缺失 tool 结果: {pending}"


def test_occupancy_levels_trigger_only_above_threshold():
    thr = threshold_tokens(CTX, 8_000)          # 30000-8000=22000
    for pct in (70, 85, 90, 95):
        target = int(CTX * pct / 100)
        hist = _history_for_tokens(target)
        assert should_compact(hist, CTX, 8_000) is (target > thr), pct


def test_compaction_at_each_level_keeps_pairs_and_reduces():
    for pct in (85, 90, 95):
        hist = _history_for_tokens(int(CTX * pct / 100))
        before = estimate_tokens(hist)
        new, info = compact_history(hist, lambda old: "摘要:已确认 0x1234 处无校验",
                                    keep_recent_tokens=6_000)
        assert info["compacted"] > 0 and estimate_tokens(new) < before
        _verify_pairs(new)


def test_triple_compaction_stays_consistent():
    hist = _history_for_tokens(int(CTX * 0.9))
    sizes = []
    for _ in range(3):
        hist, _ = compact_history(hist, lambda old: "摘要:保留的事实", keep_recent_tokens=4_000)
        _verify_pairs(hist)
        sizes.append(estimate_tokens(hist))
    # 连续压缩不膨胀、不破坏配对;摘要头不重复堆叠
    assert sizes[-1] <= sizes[0]
    assert sum(1 for m in hist if "历史摘要" in (m.get("content") or "")) <= 1


class _M:
    name = "m"

    def __init__(self, chars: int = 1_200):
        self.n = 0
        self.chars = chars

    def chat(self, messages, tools):
        self.n += 1
        if self.n > 6:
            return Reply(content="FINAL: done")
        return Reply(content="", tool_calls=[{
            "id": f"c{self.n}", "name": "fs_read",
            "args": {"path": f"f{self.n}.txt"}, "args_raw": "{}"}])


def _ws(tmp_path):
    root = str(tmp_path)
    for i in range(1, 8):
        with open(os.path.join(root, f"f{i}.txt"), "w", encoding="utf-8") as f:
            f.write("z" * 6_000)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "op 是否有权限检查", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "静态")
    art = os.path.join(root, "report", "artifacts")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "audit.txt"), "w", encoding="utf-8") as f:
        f.write("0x1234: 地址级核对\n")
    ev = dom.add_evidence("t1", "log", path="report/artifacts/audit.txt",
                          attempt_id=aid, hypothesis_id=hid)
    dom.verify_evidence(ev, method="recheck-sha256")
    return con, root, hid, ev


def test_compaction_does_not_touch_research_facts(tmp_path):
    con, root, _hid, ev = _ws(tmp_path)
    before = con.execute("SELECT id,state,verdict FROM hypotheses").fetchall()
    r = Runner(con, root, _M(), limits=Limits(max_model_calls=10, context_tokens=20_000,
                                              keep_recent_tokens=4_000, output_reserve_tokens=8_000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               summarizer=lambda old: "摘要:只写对话连续性")
    r.run(r.start("t1", "压缩"), "t1")
    assert con.execute("SELECT COUNT(*) n FROM events WHERE kind='run.compacted'").fetchone()["n"] >= 1
    after = con.execute("SELECT id,state,verdict FROM hypotheses").fetchall()
    assert [tuple(x) for x in before] == [tuple(x) for x in after]
    assert con.execute("SELECT COUNT(*) n FROM evidence WHERE id=?", (ev,)).fetchone()["n"] == 1
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 1


def test_resume_after_compaction_keeps_prefix_and_pairs(tmp_path):
    con, root, _hid, _ev = _ws(tmp_path)
    r = Runner(con, root, _M(), limits=Limits(max_model_calls=8, context_tokens=20_000,
                                              keep_recent_tokens=4_000, output_reserve_tokens=8_000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               summarizer=lambda old: "摘要:继续核对 0x1234")
    rid = r.start("t1", "压缩后续跑")
    r.run(rid, "t1")
    # 找一个发生过压缩的 checkpoint,模拟 resume 加载
    ck = con.execute("SELECT messages FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 1", (rid,)).fetchone()
    msgs = json.loads(ck["messages"])
    if any("历史摘要" in (m.get("content") or "") for m in msgs):
        _verify_pairs(msgs)
    r2 = Runner(con, root, _M(), limits=Limits(max_model_calls=14, context_tokens=20_000,
                                               keep_recent_tokens=4_000, output_reserve_tokens=8_000),
                policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
                summarizer=lambda old: "摘要:继续")
    res = r2.resume(rid, "继续")
    assert res.state in ("finished", "paused")
