"""统一工具注册契约:manifest 驱动、适配器、生命周期、失败语义。

本文件要证明的关键点:**新增一个已有执行类型的工具,只需要一个 manifest**
(必要时加适配器函数),不改 Agent Loop、不改中央清单、不加核心分支。
"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.toolreg import (
    Adapter,
    Manifest,
    ManifestError,
    capability_index,
    load_manifests,
    register_feature,
    register_manifests,
)
from fwcore.tools import Registry, ToolContext


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root, rid=None):
    return ToolContext(domain=dom, root=root, run_id=rid,
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


def _write_manifest(d, **data):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{data['name']}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


# ---------------- ① 声明校验:坏定义在加载期就拒 ----------------

def test_manifest_validation_rejects_bad_definitions(tmp_path):
    d = str(tmp_path / "reg")
    _write_manifest(d, name="ok_tool", description="d", input_schema={"type": "object"},
                    kind="script", argv=["true"])
    cases = {
        "bad name": {"name": "bad name!", "description": "d", "input_schema": {"type": "object"},
                     "kind": "script", "argv": ["true"]},
        "bad kind": {"name": "x", "description": "d", "input_schema": {"type": "object"},
                     "kind": "telepathy"},
        "python without adapter": {"name": "y", "description": "d", "input_schema": {"type": "object"},
                                   "kind": "python"},
        "unknown field": {"name": "z", "description": "d", "input_schema": {"type": "object"},
                          "kind": "script", "argv": ["true"], "typo_field": 1},
        "bad schema": {"name": "w", "description": "d", "input_schema": {"type": "array"},
                       "kind": "script", "argv": ["true"]},
        "argv string": {"name": "v", "description": "d", "input_schema": {"type": "object"},
                        "kind": "script", "argv": "true"},
        "bad timeout": {"name": "u", "description": "d", "input_schema": {"type": "object"},
                        "kind": "script", "argv": ["true"], "timeout": "soon"},
        "bad env": {"name": "s", "description": "d", "input_schema": {"type": "object"},
                    "kind": "script", "argv": ["true"], "env": []},
    }
    for label, data in cases.items():
        p = _write_manifest(str(tmp_path / "bad"), **{**data, "name": data["name"]})
        good, skipped = load_manifests(p)
        assert not good, f"{label} 竟然被接受了"
        assert skipped and skipped[0]["reason"], label
    good, skipped = load_manifests(d)
    assert [m.name for m in good] == ["ok_tool"] and not skipped


def test_manifest_roundtrip_is_strict(tmp_path):
    m = Manifest.from_dict({"name": "a", "description": "d",
                            "input_schema": {"type": "object"}, "kind": "script",
                            "argv": ["true"]}, source="t")
    assert m.name == "a"
    try:
        Manifest.from_dict({"name": "a"}, source="t")
    except ManifestError:
        pass
    else:
        raise AssertionError("缺字段的 manifest 必须被拒")


# ---------------- ② 新工具只需 manifest(不碰核心) ----------------

def test_new_script_tool_needs_only_a_manifest(tmp_path):
    con, dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    script = os.path.join(root, "tools", "hello.sh")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write('#!/usr/bin/env bash\necho "hello $1"\n')
    _write_manifest(d, name="hello_tool", description="打招呼",
                    input_schema={"type": "object", "properties": {"who": {"type": "string"}},
                                  "required": ["who"]},
                    kind="script", argv=["bash", "tools/hello.sh", "{who}"],
                    timeout=10, capability="demo.hello", provider="repo:hello.sh")

    reg = Registry()
    rep = register_manifests(reg, root, dirs=[d])
    assert rep["registered"] == ["hello_tool"], rep

    out = reg.call(_ctx(dom, root), "hello_tool", {"who": "fwcore"})
    assert out["status"] == "ok"
    assert "hello fwcore" in out["output_tail"]
    row = con.execute("SELECT provider, capability FROM tool_calls").fetchone()
    assert row["provider"] == "repo:hello.sh" and row["capability"] == "demo.hello"


def test_python_adapter_tool(tmp_path):
    _con, dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    _write_manifest(d, name="py_echo", description="py 适配器",
                    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                    kind="python", adapter="tests.helpers:echo_tool")
    reg = Registry()
    assert register_manifests(reg, root, dirs=[d])["registered"] == ["py_echo"]
    out = reg.call(_ctx(dom, root), "py_echo", {"text": "hi"})
    assert out["status"] == "ok" and out["output_tail"].startswith("echo:hi")


def test_python_adapter_prefix_allowlist(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    _write_manifest(d, name="evil", description="x",
                    input_schema={"type": "object"}, kind="python",
                    adapter="subprocess:run")
    reg = Registry()
    rep = register_manifests(reg, root, dirs=[d])
    assert "evil" not in list(reg.specs)
    assert rep["unavailable"] and "白名单" in rep["unavailable"][0]["reason"]


def test_missing_entry_is_reported_not_faked(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    _write_manifest(d, name="needs_bin", description="x",
                    input_schema={"type": "object"}, kind="script",
                    argv=["bash", "tools/nope.sh"], requires_file="tools/nope.sh")
    reg = Registry()
    rep = register_manifests(reg, root, dirs=[d])
    assert "needs_bin" not in reg.specs
    assert any(u["name"] == "needs_bin" and "tools/nope.sh" in u["reason"]
               for u in rep["unavailable"])


# ---------------- ③ 执行语义:超时/取消/取消杀组 ----------------

def test_script_tool_timeout_kills_process_group(tmp_path):
    _con, dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    script = os.path.join(root, "tools", "slow.sh")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    pidfile = os.path.join(root, "child.pid")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f'#!/usr/bin/env bash\nsleep 30 &\necho $! > "{pidfile}"\nwait\n')
    _write_manifest(d, name="slow_tool", description="慢", input_schema={"type": "object"},
                    kind="script", argv=["bash", "tools/slow.sh"], timeout=0.5)
    reg = Registry()
    register_manifests(reg, root, dirs=[d])
    out = reg.call(_ctx(dom, root), "slow_tool", {})
    assert out["status"] == "timeout", out
    if os.path.isfile(pidfile):
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("超时后子进程仍然活着(没杀进程组)")


# ---------------- ④ 特色适配器:启动失败必须显式 ----------------

def test_feature_adapter_start_failure_is_explicit(tmp_path):
    _con, dom, root = _setup(tmp_path)
    d = str(tmp_path / "reg")
    _write_manifest(d, name="feat_tool", description="feature", input_schema={"type": "object"},
                    kind="feature", adapter="fakefeat")
    calls: list[int] = []

    class Fake(Adapter):
        def ensure(self, ctx):
            return {"status": "error", "error": "启动失败: 端口不通"}

        def call(self, ctx, m, args):
            calls.append(1)
            return {"status": "ok"}

    register_feature("fakefeat", lambda root: Fake())
    reg = Registry()
    register_manifests(reg, root, dirs=[d])
    out = reg.call(_ctx(dom, root), "feat_tool", {})
    assert out["status"] == "error" and "启动失败" in out["error"]
    assert not calls, "启动失败后不许继续调用(旧实现就是这里没检查)"


# ---------------- ⑤ capability 索引(给 providers 用) ----------------

def test_capability_index_from_manifests(tmp_path):
    root = str(tmp_path)
    d = os.path.join(root, "tools", "registry")
    _write_manifest(d, name="t1", description="d", input_schema={"type": "object"},
                    kind="script", argv=["true"], capability="cap.a", provider="p1")
    _write_manifest(d, name="t2", description="d", input_schema={"type": "object"},
                    kind="script", argv=["true"], capability="cap.a", provider="p2",
                    requires_file="tools/missing")
    idx = capability_index(root)
    assert [x["tool"] for x in idx["cap.a"]] == ["t1", "t2"]
    assert [x["available"] for x in idx["cap.a"]] == [True, False]
