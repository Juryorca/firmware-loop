"""P0 回归:失控执行的真反例(2026-11-04 62GB 事故)。

事故:模型不输出 FINAL + `advance()` 无空闲检测 + 检查点每步存全量历史 ⇒
一次测试把 SQLite 撑到 33GB、`/var/tmp` 堆到 62GB。

这组测试断言:执行边界**只暂停**、检查点有上限、工具子进程会被整组杀掉、
恢复在裁剪后仍然正确。
"""

from __future__ import annotations

import os
import subprocess
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply, ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner


class EndlessAdapter:
    """永远不输出 FINAL,而且每次换不同的工具调用参数(躲过"重复调用"检测)。"""

    name = "endless"

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools):
        self.n += 1
        return Reply(content=f"还在想 {self.n}", tool_calls=[{
            "id": f"c{self.n}", "name": "state_add_observation",
            "args": {"target": "t1", "text": f"第 {self.n} 次观察(便宜调用,只为触发状态更新)"},
            "args_raw": None}], reasoning="")


def _setup(root):
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "bundle/")
    hid = dom.add_hypothesis("t1", "某条假设")
    return con, dom, hid


def test_endless_model_pauses_at_call_limit_without_touching_research(tmp_path):
    con, _dom, hid = _setup(str(tmp_path))
    r = Runner(con, str(tmp_path), EndlessAdapter(), limits=Limits(max_model_calls=5),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    res = r.run(rid, "t1")

    assert res.state == "paused", "触顶只能暂停,不能变成'完成'"
    assert con.execute("SELECT COUNT(*) n FROM events WHERE kind='run.limit_hit'").fetchone()["n"] >= 1
    # 研究事实不被触碰
    assert con.execute("SELECT state FROM hypotheses WHERE id=?", (hid,)).fetchone()["state"] == "open"
    assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 0
    # 检查点与库体积受控
    n_ck = con.execute("SELECT COUNT(*) n FROM checkpoints").fetchone()["n"]
    assert n_ck <= 5 + 1
    assert os.path.getsize(os.path.join(str(tmp_path), "state", "fwcore.sqlite3")) < 32 * 1024 * 1024


def test_checkpoint_pruning_keeps_resume_correct(tmp_path):
    con, _dom, _hid = _setup(str(tmp_path))
    r = Runner(con, str(tmp_path), EndlessAdapter(), limits=Limits(max_model_calls=12),
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    r.run(rid, "t1")

    n_ck = con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"]
    assert n_ck <= 2, "只保留少量 step 指针;完整历史在 conversation_entries"

    # 裁剪后恢复仍然可用,且历史仍然合法
    from fwcore.models import to_api_messages

    # 预算是 **run 级**的:第一次已用掉 12 次 ⇒ 恢复时必须给出更大的预算才能继续
    # (给更小的预算会立即暂停 —— 这是有意语义,见 tests/test_budget_accounting.py)
    r2 = Runner(con, str(tmp_path), ScriptedAdapter(script=[{"content": "FINAL: 恢复后收工", "tool_calls": []}]),
                limits=Limits(max_model_calls=20), policy=Policy(allow_high_risk=True))
    res = r2.resume(rid)
    assert res.state == "finished"
    to_api_messages(res.history)


def test_tool_timeout_kills_whole_process_group(tmp_path):
    """超时必须杀掉整组子进程,不能留下继续写盘的进程。"""
    from fwcore.tools import ToolContext, default_registry

    _con, dom, _hid = _setup(str(tmp_path))
    ctx = ToolContext(domain=dom, root=str(tmp_path), policy=Policy(allow_high_risk=True))
    marker = os.path.join(str(tmp_path), "child.pid")
    out = default_registry().call(ctx, "shell_exec", {
        "command": f"sh -c 'sleep 120 & echo $! > {marker}; wait'", "timeout": 0.6})
    assert out["status"] == "timeout"
    time.sleep(0.3)
    assert os.path.isfile(marker), "子进程应先起来(否则这条测试没意义)"
    with open(marker, encoding="utf-8") as f:
        child = int(f.read().strip())
    alive = subprocess.run(["kill", "-0", str(child)], capture_output=True, check=False).returncode == 0
    assert not alive, "超时后子进程必须已被杀掉"
