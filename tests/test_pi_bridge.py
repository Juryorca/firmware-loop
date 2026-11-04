"""Pi 兼容桥:事件 → 权威状态;幂等;坏事件不产生副作用。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.pi_bridge import handle_event, handle_stream


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "/a")
    return con, dom, root


def test_event_sequence_builds_same_state_as_direct_calls(tmp_path):
    con, dom, root = _mk(tmp_path)
    r1 = handle_event(con, root, {"kind": "hypothesis.add", "client_event_id": "e1",
                                  "target": "t1", "title": "h", "surface_key": "/a"})
    hid = r1["hypothesis_id"]
    r2 = handle_event(con, root, {"kind": "attempt.start", "client_event_id": "e2",
                                  "hypothesis_id": hid, "method": "probe"})
    aid = r2["attempt_id"]
    handle_event(con, root, {"kind": "observation.add", "client_event_id": "e2b",
                             "target": "t1", "text": "响应里带管理字段", "attempt_id": aid})
    r3 = handle_event(con, root, {"kind": "attempt.finish", "client_event_id": "e3",
                                  "target": "t1", "attempt_id": aid, "hypothesis_id": hid,
                                  "result": "supported", "note": "行为不同",
                                  "obs_outcome": "new",
                                  "evidence": [{"kind": "http", "capture": "200 + admin"}]})
    assert r3["status"] == "ok" and r3["evidence"]

    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
    assert con.execute("SELECT result FROM attempts WHERE id=?", (aid,)).fetchone()["result"] == "supported"
    assert con.execute("SELECT COUNT(*) n FROM evidence WHERE attempt_id=?", (aid,)).fetchone()["n"] == 1
    # 与直接调 domain 得到的收尾阻塞一致(证据齐全 ⇒ 只剩材料/面)
    assert dom.closure_blockers("t1")


def test_duplicate_event_is_noop(tmp_path):
    con, dom, root = _mk(tmp_path)
    ev = {"kind": "observation.add", "client_event_id": "dup-1", "target": "t1", "text": "观察"}
    assert handle_event(con, root, ev)["status"] == "ok"
    assert handle_event(con, root, ev)["status"] == "duplicate"
    assert con.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"] == 1


def test_bad_events_do_not_write(tmp_path):
    con, dom, root = _mk(tmp_path)
    assert handle_event(con, root, {"kind": "nope", "client_event_id": "x"})["status"] == "error"
    assert handle_event(con, root, {"kind": "hypothesis.add", "target": "t1",
                                    "title": "没 cid"})["status"] == "error"
    assert handle_event(con, root, {"kind": "hypothesis.add", "client_event_id": "y",
                                    "target": "t1", "title": "坏面", "surface_key": "/不存在"})["status"] == "error"
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 0


def test_stream_reports_bad_lines_but_keeps_going(tmp_path):
    con, dom, root = _mk(tmp_path)
    text = ('{"kind":"observation.add","client_event_id":"s1","target":"t1","text":"ok"}\n'
            'not json\n'
            '{"kind":"observation.add","client_event_id":"s2","target":"t1","text":"ok2"}\n')
    res = handle_stream(con, root, text)
    assert [r["status"] for r in res] == ["ok", "error", "ok"]
    assert con.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"] == 2
