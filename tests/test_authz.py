"""研究授权:按目标授予/过期/撤销/范围,默认拒绝,并留审计。"""

from __future__ import annotations

import os
import time

from fwcore import authz as az_mod
from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner
from fwcore.tools import ToolContext, default_registry


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root, az=None):
    return ToolContext(domain=dom, root=root, policy=Policy(authorization=az))


def test_default_deny_without_grant(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = default_registry().call(_ctx(dom, root), "shell_exec", {"command": "echo hi"})
    assert out["status"] == "cancelled"
    assert "authz grant" in out["error"], "默认拒绝时必须告诉用户怎么授权(可发现性)"


def test_grant_allows_shell_within_roots(tmp_path):
    con, dom, root = _setup(tmp_path)
    az_mod.grant(root, "t1", roots=[root], hours=1, note="本目标研究")
    az = az_mod.load(root, "t1")
    assert az is not None and az.active()
    out = default_registry().call(_ctx(dom, root, az), "shell_exec", {"command": "echo hi"})
    assert out["status"] == "ok", out
    # 范围外路径仍然拒绝
    bad = default_registry().call(_ctx(dom, root, az), "fs_read", {"path": "../../../etc/hostname"})
    assert bad["status"] == "error"


def test_expired_and_revoked_deny(tmp_path):
    con, dom, root = _setup(tmp_path)
    az_mod.grant(root, "t1", roots=[root], hours=1)
    az = az_mod.load(root, "t1")
    assert az is not None
    az.expires_at = time.time() - 1
    out = default_registry().call(_ctx(dom, root, az), "shell_exec", {"command": "echo hi"})
    assert out["status"] == "cancelled"

    az_mod.revoke(root, "t1", "用户撤回")
    az2 = az_mod.load(root, "t1")
    assert az2 is not None and not az2.active()
    out2 = default_registry().call(_ctx(dom, root, az2), "shell_exec", {"command": "echo hi"})
    assert out2["status"] == "cancelled"


def test_runner_autoloads_grant_and_audits(tmp_path):
    con, dom, root = _setup(tmp_path)
    az_mod.grant(root, "t1", roots=[root], hours=1, note="CI 授权")
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "FINAL: ok", "tool_calls": []}]),
               limits=Limits(max_model_calls=2))       # 注意:policy 未显式给授权
    rid = r.start("t1")
    assert r.policy.authorization is not None, "启动时应按目标加载授权文件"
    r.run(rid, "t1")
    kinds = [x["kind"] for x in con.execute("SELECT kind FROM events")]
    assert "authz.loaded" in kinds


def test_describe_reports_authorized_state(tmp_path):
    con, dom, root = _setup(tmp_path)
    assert az_mod.describe(root, "t1")["authorized"] is False
    az_mod.grant(root, "t1", roots=[root], hours=1)
    d = az_mod.describe(root, "t1")
    assert d["authorized"] is True and d["isolation"] == "none" and d["file"].endswith("authz.json")
