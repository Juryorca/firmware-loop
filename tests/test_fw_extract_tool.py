"""manifest 驱动的新研究工具 fw_extract:注册、argv、观测、能力索引(不碰核心代码)。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.toolkit import OBSERVERS, register_repo_tools
from fwcore.toolreg import Manifest, ScriptAdapter, capability_index, select_tool
from fwcore.tools import Registry, ToolContext


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root):
    return ToolContext(domain=dom, root=root,
                       artifacts_dir=os.path.join(root, "state", "artifacts", "R1"),
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


def test_manifest_declares_argv_and_capability():
    with open("tools/registry/fw_extract.json", encoding="utf-8") as f:
        m = Manifest.from_dict(json.load(f), source="fw_extract.json")
    argv = ScriptAdapter().build_argv(m, {"firmware": "fw.bin", "outdir": "out dir"})
    assert argv == ["bash", "tools/fw_extract.sh", "fw.bin", "out dir"]
    assert m.capability == "extract.binwalk" and m.risk == "high" and m.observer == "fw_extract"


def test_tool_registers_and_runs_with_stub_script(tmp_path):
    """把入口换成假脚本(真脚本要跑 binwalk,单元测试不跑重的),证明注册/执行/观测都通。"""
    con, dom, root = _setup(tmp_path)
    script = os.path.join(root, "tools", "fw_extract.sh")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write('#!/usr/bin/env bash\necho "FIRMWARE=$1"\necho "OUTDIR=$2"\n'
                'echo "state/_scratch/x"\necho "files=42"\n')
    reg = Registry()
    reg_dir = os.path.join(root, "tools", "registry")
    os.makedirs(reg_dir, exist_ok=True)
    with open(os.path.join(reg_dir, "fw_extract.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "fw_extract", "description": "d",
                   "input_schema": {"type": "object", "properties": {
                       "firmware": {"type": "string"}, "outdir": {"type": "string"}},
                       "required": ["firmware", "outdir"]},
                   "kind": "script", "argv": ["bash", "tools/fw_extract.sh", "{firmware}", "{outdir}"],
                   "timeout": 30, "observer": "fw_extract", "risk": "high",
                   "capability": "extract.binwalk", "provider": "repo:fw_extract.sh",
                   "requires_file": "tools/fw_extract.sh"}, f)
    from fwcore.toolreg import register_manifests

    rep = register_manifests(reg, root, dirs=[reg_dir])
    assert rep["registered"] == ["fw_extract"], rep
    out = reg.call(_ctx(dom, root), "fw_extract", {"firmware": "fw.bin", "outdir": "o ut"})
    assert out["status"] == "ok", out
    assert "FIRMWARE=fw.bin" in out["output_tail"] and "OUTDIR=o ut" in out["output_tail"]
    assert out["observation"]["files"] == 42
    row = con.execute("SELECT provider, capability FROM tool_calls WHERE tool='fw_extract'").fetchone()
    assert row["provider"] == "repo:fw_extract.sh" and row["capability"] == "extract.binwalk"


def test_observer_parses_real_summary_and_falls_back():
    obs = OBSERVERS["fw_extract"]("== summary ==\nstate/_scratch/fwx\nfiles=7\n")
    assert obs["status"] == "ok" and obs["files"] == 7 and obs["dirs"] == ["state/_scratch/fwx"]
    assert OBSERVERS["fw_extract"]("完全看不懂")["status"] == "unparsed"


def test_capability_index_lists_new_tool(tmp_path):
    """框架自带 registry:新工具进能力索引,可被选择(索引本身不等于选择)。"""
    idx = capability_index(".")
    assert [x["tool"] for x in idx["extract.binwalk"]] == ["fw_extract"]
    sel = select_tool(".", "extract.binwalk")
    assert sel is not None and sel["selected"] == "fw_extract"


def test_repo_registry_registers_fw_extract():
    reg = Registry()
    rep = register_repo_tools(reg, ".")
    assert "fw_extract" in rep["registered"]
    assert "fw_extract" in reg.specs
