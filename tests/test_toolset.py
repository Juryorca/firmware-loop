"""默认工具集:仓库资产/外部依赖/能力状态与 QEMU/Ghidra 真实入口。"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.providers import READY
from fwcore.toolkit import register_repo_tools
from fwcore.tools import Registry, ToolContext
from fwcore.toolset import check_toolset, render_report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_default_toolset_report_and_repo_assets():
    rep = check_toolset(ROOT)
    assert rep["required_total"] >= 10
    assert rep["optional_total"] >= 1
    assert rep["repo_ok"] is True, [r for r in rep["items"] if r["repo"] and r["status"] != READY]
    ids = {x["id"] for x in rep["items"]}
    for key in ("ida", "ghidra", "extraction", "qemu-user", "research-helpers",
                "screenshot"):
        assert key in ids
    qemu = next(x for x in rep["items"] if x["id"] == "qemu-user")
    assert qemu["status"] == READY               # 普通 QEMU 是默认必需项
    patched = next(x for x in rep["items"] if x["id"] == "patched-qemu")
    assert patched["optional"] is True
    text = render_report(rep)
    assert "Default Toolset Integrity" in text
    assert "qemu" in text.lower()


def test_repo_assets_are_executable():
    for rel in ("tools/derived_check.sh", "tools/ghidra_run.sh",
                "tools/check.sh", "tools/term" + "shot.sh"):
        assert os.access(os.path.join(ROOT, rel), os.X_OK), rel


def test_qemu_user_tool_real(tmp_path):
    if not shutil.which("qemu-x86_64"):
        pytest.skip("qemu-x86_64 未安装")
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    reg = Registry()
    register_repo_tools(reg, root)
    ctx = ToolContext(domain=dom, root=root,
                      policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
                      artifacts_dir=os.path.join(root, "state", "artifacts", "local"))
    out = reg.call(ctx, "qemu_user", {"arch": "x86_64", "binary": "/bin/true", "timeout": 10})
    assert out["status"] == "ok" and out["exit_code"] == 0
    assert out["meta"]["capability"] == "emu.user"


def test_ghidra_wrapper_help():
    if not os.access(os.path.join(ROOT, "tools/ghidra_run.sh"), os.X_OK):
        pytest.skip("ghidra_run.sh 不可执行")
    r = subprocess.run([os.path.join(ROOT, "tools/ghidra_run.sh"), "-help"],
                       capture_output=True, text=True, timeout=30)
    out = r.stdout + r.stderr
    if "GHIDRA_MISSING" in out:
        pytest.skip("本机未安装 Ghidra")
    assert "Headless Analyzer Usage" in out


def test_tools_command_is_registered():
    from fwapp.commands import COMMANDS
    from fwapp.modes.interactive import FwApp
    assert "tools" in {c.name for c in COMMANDS}
    assert "tool-setup" in {c.name for c in COMMANDS}
    assert hasattr(FwApp, "_cmd_tools")
    assert hasattr(FwApp, "_cmd_tool_setup")
