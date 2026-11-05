"""Pi hook 默认必须**完全惰性**:没开关文件就一行状态都不写。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.pi_hook import disable, enable, enabled, handle


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


EV = {"kind": "hypothesis.add", "client_event_id": "e1", "target": "t1", "title": "来自 Pi 的假设"}


def test_disabled_by_default_and_writes_nothing(tmp_path):
    con, dom, root = _setup(tmp_path)
    assert enabled(root) is False
    out = handle(con, root, EV)
    assert out["status"] == "disabled"
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM events WHERE kind='pi.event'").fetchone()["n"] == 0


def test_enable_then_events_apply_then_disable_again(tmp_path):
    con, dom, root = _setup(tmp_path)
    enable(root, "用户授权:仅本目标")
    assert enabled(root) is True
    out = handle(con, root, EV)
    assert out["status"] == "ok" and out.get("via") == "pi_hook"
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1

    disable(root)
    assert enabled(root) is False
    out2 = handle(con, root, {**EV, "client_event_id": "e2", "title": "关闭后不该写入"})
    assert out2["status"] == "disabled"
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
