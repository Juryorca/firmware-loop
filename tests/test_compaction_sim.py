"""压缩场景模拟(全离线):70/85/90/95% 占用、连续三次压缩、压缩后继续工具调用、resume/advance。

不生成接近 1M 的真实历史:用合成消息 + 可控 summarizer。
"""

from __future__ import annotations

import json
import os

import pytest

from fwcore import db as _db
from fwcore.compact import (
    SummarizationIncomplete,
    compact_history,
    estimate_tokens,
    find_cut,
    should_compact,
    summarize_chunks,
    summarize_covered,
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


def test_three_compactions_across_resume_and_advance(tmp_path):
    """真实序列:增长→压缩#1→追加→压缩#2→进程退出/resume→追加→压缩#3→advance。

    每一步都断言**确实发生了压缩**(不允许条件不成立就静默跳过)。
    """
    root = str(tmp_path)
    for i in range(1, 25):
        with open(os.path.join(root, f"f{i}.txt"), "w", encoding="utf-8") as f:
            f.write("q" * 8_000)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "op 权限检查", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "静态")
    art = os.path.join(root, "report", "artifacts")
    os.makedirs(art, exist_ok=True)
    with open(os.path.join(art, "a.txt"), "w", encoding="utf-8") as f:
        f.write("0x1234\n")
    ev = dom.add_evidence("t1", "log", path="report/artifacts/a.txt", attempt_id=aid,
                          hypothesis_id=hid)
    dom.verify_evidence(ev, method="recheck-sha256")
    before_h = [tuple(r) for r in con.execute("SELECT id,state,verdict FROM hypotheses")]

    def n_compact():
        return con.execute("SELECT COUNT(*) n FROM events WHERE kind='run.compacted'"
                           ).fetchone()["n"]

    base = Limits(context_tokens=20_000, keep_recent_tokens=4_000,
                  output_reserve_tokens=8_000, summarize_chunk_tokens=4_000,
                  summarize_max_calls=20)
    seq = {"i": 0}

    class Reader:
        name = "reader"

        def chat(self, messages, tools):
            seq["i"] += 1
            if seq["i"] > 60:
                return Reply(content="FINAL: done")
            return Reply(content="", tool_calls=[{
                "id": f"r{seq['i']}", "name": "fs_read",
                "args": {"path": f"f{seq['i'] % 24 + 1}.txt"}, "args_raw": "{}"}])

    def mk(calls):
        return Runner(con, root, Reader(),
                      limits=Limits(**{**base.__dict__, "max_model_calls": calls}),
                      policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
                      summarizer=lambda chunk: f"摘要:覆盖 {len(chunk)} 条")

    # 阶段 1:增长到阈值 → 压缩 #1
    rid = mk(6).start("t1", "多轮压缩")
    mk(6).run(rid, "t1")
    c1 = n_compact()
    assert c1 >= 1, f"阶段1 未发生压缩(compacted={c1})"

    # 阶段 2:继续追加 → 压缩 #2
    mk(10).resume(rid, "继续读")
    c2 = n_compact()
    assert c2 > c1, f"阶段2 未新增压缩({c1} -> {c2})"

    # 阶段 3:模拟进程退出(全新 Runner)→ resume → 压缩 #3
    mk(14).resume(rid, "进程重启后继续")
    c3 = n_compact()
    assert c3 > c2, f"阶段3 未新增压缩({c2} -> {c3})"

    # 阶段 4:advance 继续(同一 run)
    mk(18).advance("t1", "advance 继续", run_id=rid, max_rounds=1)
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] in (
        "paused", "finished")

    # 三次压缩后:从 append-only session 重建的上下文协议配对完整
    probe = Runner(con, root, _M(), limits=Limits(max_model_calls=1),
                   policy=Policy(allow_high_risk=True))
    _verify_pairs(probe._load_history(rid))
    # 研究事实未被压缩改动
    assert [tuple(r) for r in con.execute("SELECT id,state,verdict FROM hypotheses")] == before_h
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 1


def test_threshold_matches_pi_fixed_reserve():
    # Pi: compact when contextTokens > contextWindow - reserveTokens
    assert threshold_tokens(1_000_000, 8_000) == 992_000
    assert threshold_tokens(100_000, 8_000) == 92_000
    assert threshold_tokens(30_000, 8_000) == 22_000
    assert threshold_tokens(0, 8_000) == 0


def test_summarize_coverage_fails_loudly():
    hist = _history_for_tokens(6_000)
    with pytest.raises(SummarizationIncomplete):
        compact_history(hist, lambda chunk: "", keep_recent_tokens=1_000,
                        chunk_tokens=1_000, max_calls=50)          # 空摘要
    with pytest.raises(SummarizationIncomplete):
        compact_history(hist, lambda chunk: "s", keep_recent_tokens=1_000,
                        chunk_tokens=100, max_calls=2)             # 块数超上限


def test_summarize_covered_processes_every_message():
    hist = _history_for_tokens(9_000)
    cut = find_cut(hist, 1_000)
    old = hist[:cut]
    seen: list[int] = []
    summary, info = summarize_covered(old, lambda chunk: (seen.append(len(chunk)) or "s"),
                                      chunk_tokens=2_000, max_calls=40)
    assert summary == "s"
    # messages = 原始消息数(覆盖保证);pieces = 实际送进模型的片段数(超大消息会被切片)
    assert info["messages"] == len(old)
    assert info["messages"] == len(old)
    # 所有送进模型的片段 = 首轮分块片段 + 归并调用;首轮片段覆盖每条原消息
    assert info["pieces"] >= len(old)
    assert sum(seen) == info["pieces"] + info["merge_calls"], (info, sum(seen))


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
    # 模拟 resume 加载:必须从 append-only session + compaction 重建
    msgs = r._load_history(rid)
    if any("历史摘要" in (m.get("content") or "") for m in msgs):
        _verify_pairs(msgs)
    r2 = Runner(con, root, _M(), limits=Limits(max_model_calls=14, context_tokens=20_000,
                                               keep_recent_tokens=4_000, output_reserve_tokens=8_000),
                policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
                summarizer=lambda old: "摘要:继续")
    res = r2.resume(rid, "继续")
    assert res.state in ("finished", "paused")


def test_merge_calls_share_chunk_budget():
    """分块摘要与归并摘要共用 max_calls(旧实现给归并私加 2x 额度)。"""
    hist = _history_for_tokens(8_000)
    old = hist[:find_cut(hist, 500)]
    chunks = summarize_chunks(old, chunk_tokens=500)
    n = len(chunks)
    assert n >= 2, n
    with pytest.raises(SummarizationIncomplete):
        summarize_covered(old, lambda c: "s", chunk_tokens=500, max_calls=n)
    summary, info = summarize_covered(old, lambda c: "s", chunk_tokens=500, max_calls=n + 5)
    assert summary == "s"
    assert info["merge_calls"] >= 1
    assert info["calls"] == n + info["merge_calls"] <= n + 5
