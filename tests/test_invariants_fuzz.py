"""系统审计:随机操作序列下,状态机必须始终保持自洽。

不是针对某个 bug 打补丁 —— 而是对"任意合法/非法操作混合"断言一组**全局不变量**:
1. 无论怎么操作,`derived()` 都不抛异常;
2. 已结束的尝试必须有 finished_at;running 的必须没有;
3. 已收口的假设必须有 verdict + scope_note;未收口的必须没有 verdict;
4. 工具调用状态只在枚举内;
5. `closure_blockers()` 为空 ⇒ 一定 0 未收口 / 0 running / 0 未判定面 / 有假设有面 / 材料完整。
"""

from __future__ import annotations

import random

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, DomainError, InvariantViolation

RESULTS = ["supported", "refuted", "inconclusive", "environment_failure", "no_signal", "cancelled"]
VERDICTS = ["vulnerable", "no_finding", "inconclusive"]


def _ops(dom: Domain, rnd: random.Random, target: str, hids: list[str], aids: list[str]) -> None:
    op = rnd.choice(["hyp", "surface", "attempt", "finish", "evidence", "observe", "conclude", "drop", "disp"])
    try:
        if op == "hyp":
            hids.append(dom.add_hypothesis(target, f"随机假设 {rnd.random():.3f}"))
        elif op == "surface":
            dom.add_surface(target, f"s{rnd.randint(0, 5)}", reachability=rnd.choice(["pre-auth", "auth"]))
        elif op == "attempt" and hids:
            aids.append(dom.start_attempt(rnd.choice(hids), "fuzz"))
        elif op == "finish" and aids:
            aid = rnd.choice(aids)
            res = rnd.choice(RESULTS)
            if res == "inconclusive":
                dom.finish_attempt(aid, res, "已排除 A,还需 B")
            else:
                dom.finish_attempt(aid, res, "")
        elif op == "evidence" and hids:
            dom.add_evidence(target, "log", capture="x", hypothesis_id=rnd.choice(hids))
        elif op == "observe":
            dom.add_observation(target, "随机观察")
        elif op == "conclude" and hids:
            dom.conclude(rnd.choice(hids), rnd.choice(VERDICTS), "仅该入口的参数 a")
        elif op == "drop" and hids:
            dom.drop_hypothesis(rnd.choice(hids), "证据不足")
        else:
            dom.set_surface_disposition(target, f"s{rnd.randint(0, 5)}",
                                        rnd.choice(["unjudged", "no_finding"]), note="逐条读过鉴权门")
    except (InvariantViolation, DomainError):
        pass          # 被拒绝是正确行为;这里只关心"拒绝之后状态仍自洽"


@pytest.mark.parametrize("seed", range(25))
def test_random_operation_sequences_keep_invariants(tmp_path, seed):
    rnd = random.Random(seed)
    con = _db.connect(str(tmp_path / f"f{seed}.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    hids: list[str] = []
    aids: list[str] = []
    for _ in range(25):
        _ops(dom, rnd, "t1", hids, aids)

    d = dom.derived("t1")                       # 1. 不抛异常
    assert isinstance(d["closure_blockers"], list)

    for r in con.execute("SELECT result, finished_at FROM attempts"):
        if r["result"] == "running":
            assert r["finished_at"] is None      # 2
        else:
            assert r["finished_at"] is not None
    for h in con.execute("SELECT state, verdict, scope_note FROM hypotheses"):
        if h["state"] == "concluded":
            assert h["verdict"] in VERDICTS and (h["scope_note"] or "").strip()   # 3
        elif h["state"] != "dropped":
            assert h["verdict"] is None
    for t in con.execute("SELECT status FROM tool_calls"):
        assert t["status"] in ("pending", "running", "ok", "error", "timeout", "cancelled")   # 4

    if not d["closure_blockers"]:               # 5
        assert d["hypotheses"]["open"] == 0 and d["hypotheses"]["investigating"] == 0
        assert d["running_attempts"] == 0
        assert d["surfaces"].get("unjudged", 0) == 0
        assert sum(d["surfaces"].values()) > 0
        assert d["material"]["ok"]
