"""统一工具注册的契约细节:argv 展开、进程生命周期、合并优先级、适配器实例、能力选择。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.toolreg import (
    Adapter,
    Manifest,
    ScriptAdapter,
    capability_index,
    close_feature_adapters,
    register_feature,
    register_manifests,
    run_process,
    select_tool,
)
from fwcore.tools import Registry, ToolContext
from fwcore.watchdog import Watchdog


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root, rid=None, artifacts=None):
    return ToolContext(domain=dom, root=root, run_id=rid,
                       artifacts_dir=artifacts or os.path.join(root, "state", "artifacts", rid or "local"),
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


def _real_manifest(name: str) -> Manifest:
    with open(os.path.join("tools", "registry", f"{name}.json"), encoding="utf-8") as f:
        return Manifest.from_dict(json.load(f), source=name)


# ---------------- ① argv 展开(真实 manifest) ----------------

@pytest.mark.parametrize("name,args,expected", [
    # 透传参数串:按 shell 规则拆,**不能**再多塞一个原样 {args}
    ("fuzz_cgi", {"cgi": "cgi", "rootfs": "rootfs", "shim": "shim", "rounds": 5},
     ["bash", "tools/cgi_fuzz.sh", "cgi", "rootfs", "shim", "5"]),
    ("emu_run", {"args": '--root "r 1 dir" --cmd ls'},
     ["bash", "tools/emu_run.sh", "--root", "r 1 dir", "--cmd", "ls"]),
    # 路径参数:不拆(含空格也要整体传)
    ("research_signal", {"log": "state/log with space.txt"},
     ["python3", "tools/parse_signal.py", "state/log with space.txt"]),
    ("research_queue", {"target": "t 1"}, ["bash", "tools/queue_state.sh", "t 1"]),
])
def test_real_manifests_build_exact_argv(name, args, expected):
    argv = ScriptAdapter().build_argv(_real_manifest(name), args)
    assert argv == expected
    assert not any("{" in a for a in argv), "占位符没被展开完(会把 {args} 原样传给工具)"


def test_mixed_part_and_empty_values():
    m = Manifest.from_dict({"name": "x", "description": "d", "input_schema": {"type": "object"},
                            "kind": "script", "argv": ["run", "--x={args}", "--lbl={lbl}"],
                            "split_args": ["args"]}, source="t")
    assert ScriptAdapter().build_argv(m, {"args": "a 'b c'", "lbl": ""}) == \
        ["run", "--x=a", "b c", "--lbl="]


# ---------------- ② run_process:pgid 登记 / 清理 / 输出上限 ----------------

def test_run_process_registers_pgid_with_starttime(tmp_path):
    _con, dom, root = _setup(tmp_path)
    r = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], start_new_session=True)
    try:
        # 先登记(与 run_process 内部同一条路径),再核对"能按 starttime 找到这个组"
        from fwcore.toolreg import _proc_starttime, _register_pgid

        art = os.path.join(root, "state", "artifacts", "R1")
        _register_pgid(_ctx(dom, root, rid="R1"), art, os.getpgid(r.pid), r.pid)
        with open(os.path.join(art, ".pgids"), encoding="utf-8") as f:
            line = f.read().strip().split()
        assert int(line[0]) == os.getpgid(r.pid)
        assert int(line[1]) == _proc_starttime(r.pid)
    finally:
        r.kill()


def test_run_process_caps_output_size(tmp_path):
    _con, dom, root = _setup(tmp_path)
    out = run_process(_ctx(dom, root, rid="R1"),
                      ["bash", "-c", "head -c 200000 /dev/zero | tr '\\0' 'a'"], timeout=20,
                      max_output_bytes=1000)
    assert out["status"] == "ok" and out["output_truncated"] is True
    assert len(out["stdout"]) <= 1000 and "stdout_path" not in out


def test_run_process_cancel_kills_group(tmp_path):
    """取消路径:启动后立即取消,整组杀掉并返回 cancelled。"""
    _con, dom, root = _setup(tmp_path)
    out = run_process(_ctx(dom, root, rid="R1"), ["bash", "-c", "sleep 30"],
                      timeout=10, cancel_check=lambda: True)
    assert out["status"] == "cancelled"

def test_watchdog_reaps_inflight_script_tool(tmp_path):
    """主进程被杀后,独立看护按 .pgids 清理在飞脚本工具(不会留 sleep 30)。"""
    con, dom, root = _setup(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             start_new_session=True)
    try:
        out = run_process(_ctx(dom, root, rid="R9"), ["bash", "-c", "sleep 0.1"], timeout=10)
        assert out["status"] == "ok"
        from fwcore.toolreg import _register_pgid

        art = os.path.join(root, "state", "artifacts", "R9")
        _register_pgid(_ctx(dom, root, rid="R9"), art, os.getpgid(child.pid), child.pid)

        dom.create_target("t9", version="1") if False else None
        con.execute("INSERT INTO runs(id,target_id,state,step,rounds,pid,pid_starttime,host,"
                    "heartbeat_at,started_at,updated_at) VALUES('R9',?,'running',0,0,?,0,?,"
                    "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')",
                    (dom.target("t1").id, 999999, "other-host"))
        wd = Watchdog(con=None, root=root, db_path=os.path.join(root, "state", "fwcore.sqlite3"),
                      heartbeat_timeout_s=1, poll_s=0.01)
        acts = wd.scan_once()
        assert acts and acts[0].get("killed_pgids"), acts
        time.sleep(0.5)
        child.poll()
        assert child.returncode is not None, "看护应按 .pgids 杀掉在飞工具组"
    finally:
        if child.poll() is None:
            child.kill()


# ---------------- ③ 同名 manifest:工作区优先,不崩 ----------------

def test_workspace_manifest_overrides_framework(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    reg_dir = os.path.join(root, "tools", "registry")
    os.makedirs(reg_dir, exist_ok=True)
    with open(os.path.join(reg_dir, "research_queue.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "research_queue", "description": "工作区覆盖版",
                   "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
                   "kind": "script", "argv": ["bash", "tools/queue_state.sh", "{target}"]}, f)
    reg = Registry()
    rep = register_manifests(reg, root)          # 工作区目录在前
    assert "research_queue" in rep["registered"]
    assert rep["overridden"] and rep["overridden"][0]["name"] == "research_queue"
    assert reg.specs["research_queue"].description == "工作区覆盖版"


# ---------------- ④ 适配器实例生命周期 ----------------

def test_close_feature_adapters_uses_existing_instances(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    reg_dir = os.path.join(root, "tools", "registry")
    os.makedirs(reg_dir, exist_ok=True)
    with open(os.path.join(reg_dir, "feat.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "feat_tool", "description": "d", "input_schema": {"type": "object"},
                   "kind": "feature", "adapter": "counting"}, f)
    made: list[Counting] = []

    class Counting(Adapter):
        def __init__(self):
            self.closed = 0

        def ensure(self, ctx):
            return None

        def call(self, ctx, m, args):
            return {"status": "ok"}

        def close(self):
            self.closed += 1

        def health(self):
            return {"status": "ok"}

    def factory(_root) -> Adapter:
        inst = Counting()
        made.append(inst)
        return inst

    register_feature("counting", factory)
    reg = Registry()
    register_manifests(reg, root, dirs=[reg_dir])
    assert len(made) == 1
    close_feature_adapters(reg)
    close_feature_adapters(reg)                  # 幂等
    assert len(made) == 1, "close 不能新建适配器实例"
    assert made[0].closed == 1


def test_ida_close_does_not_stop_shared_supervisor_by_default(monkeypatch):
    from fwcore.adapters_ida import IdAdapter

    calls: list[list[str]] = []
    monkeypatch.setattr("fwcore.adapters_ida.run_process", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr("fwcore.adapters_ida._supervisor_up", lambda ctx: True)
    monkeypatch.setenv("FW_IDA_STOP_ON_CLOSE", "0")
    a = IdAdapter(".")
    a._started = True
    a.close()                                    # 默认:不停共享 supervisor
    a._started = True
    monkeypatch.setenv("FW_IDA_STOP_ON_CLOSE", "1")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(list(a[0]) or []))
    a.close()
    assert calls, "显式开启时才调用 ida_stop.sh"


# ---------------- ⑤ 能力索引 vs 选择 ----------------

def _write_manifest(d, **data):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{data['name']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_capability_index_and_selection_are_different(tmp_path):
    root = str(tmp_path)
    d = os.path.join(root, "tools", "registry")
    _write_manifest(d, name="slow_tool", description="d", input_schema={"type": "object"},
                    kind="script", argv=["true"], capability="cap.x", provider="p-slow", priority=5)
    _write_manifest(d, name="fast_tool", description="d", input_schema={"type": "object"},
                    kind="script", argv=["true"], capability="cap.x", provider="p-fast", priority=1,
                    requires_file="tools/nope")
    idx = capability_index(root)
    names = {x["tool"]: x for x in idx["cap.x"]}
    assert set(names) == {"slow_tool", "fast_tool"}
    assert names["fast_tool"]["available"] is False and names["slow_tool"]["available"] is True

    sel = select_tool(root, "cap.x")
    assert sel is not None
    assert sel["selected"] == "slow_tool" and sel["provider"] == "p-slow"
    assert [c["tool"] for c in sel["candidates"]].count("fast_tool") == 1


def test_ok_exit_codes_declare_meaningful_negative_results(tmp_path):
    """有些工具用非 0 表达研究结论(如 signal MISS=1):按工具声明,不全局放宽。"""
    _con, dom, root = _setup(tmp_path)
    script = os.path.join(root, "tools", "miss.sh")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\necho MISS\nexit 1\n")
    d = os.path.join(root, "reg")
    os.makedirs(d, exist_ok=True)
    base = {"description": "d", "input_schema": {"type": "object"}, "kind": "script",
            "argv": ["bash", "tools/miss.sh"], "timeout": 10}
    with open(os.path.join(d, "declared.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "declared", **base, "ok_exit_codes": [0, 1]}, f)
    with open(os.path.join(d, "strict.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "strict", **base}, f)
    reg = Registry()
    assert register_manifests(reg, root, dirs=[d])["registered"] == ["declared", "strict"]
    ctx = _ctx(dom, root, rid="R1")
    assert reg.call(ctx, "declared", {})["status"] == "ok"        # MISS 是有意义的负结果
    assert reg.call(ctx, "strict", {})["status"] == "error"       # 没声明 ⇒ 非 0 视为失败


def test_failed_tool_call_records_reason(tmp_path):
    """失败原因必须落库(否则事后审计与模型复盘都看不出为什么失败)。"""
    con, dom, root = _setup(tmp_path)
    script = os.path.join(root, "tools", "boom.sh")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\necho 'disk exploded' >&2\nexit 3\n")
    d = os.path.join(root, "reg")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "boom.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "boom_tool", "description": "d", "input_schema": {"type": "object"},
                   "kind": "script", "argv": ["bash", "tools/boom.sh"], "timeout": 10}, f)
    reg = Registry()
    register_manifests(reg, root, dirs=[d])
    out = reg.call(_ctx(dom, root, rid="R1"), "boom_tool", {})
    assert out["status"] == "error"
    row = con.execute("SELECT status, note FROM tool_calls WHERE tool='boom_tool'").fetchone()
    assert row["status"] == "error"
    assert row["note"], "失败原因没落库"
    assert "3" in row["note"] or "退出码" in row["note"], row["note"]
