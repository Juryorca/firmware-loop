"""工具结果给模型看的那份:有界、始终合法 JSON、DB id 与产物路径不丢。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner, _bounded_tool_result


def test_bounded_result_is_valid_and_keeps_provenance():
    out = {"status": "ok", "tool_call_id": "TC-1", "result_path": "state/a.txt",
           "result_sha256": "abc", "text": "x" * 50000}
    s = _bounded_tool_result(out, limit=4000)
    assert len(s) <= 4000
    d = json.loads(s)                    # 必须是合法 JSON(硬截断会在这里炸)
    assert d["db_tool_call_id"] == "TC-1" and d["result_path"] == "state/a.txt"
    assert d["_truncated"] is True and d["_omitted"]["text"] == 50000


def test_small_result_untouched():
    out = {"status": "ok", "tool_call_id": "TC-2", "stdout_tail": "hi"}
    d = json.loads(_bounded_tool_result(out))
    assert d["stdout_tail"] == "hi" and d["db_tool_call_id"] == "TC-2"
    assert "tool_call_id" not in d


class _Model:
    name = "scripted"
    stage = 0

    def chat(self, messages, tools):
        self.stage += 1
        if self.stage == 1:
            return Reply(content="", tool_calls=[{
                "id": "call_x", "name": "fs_read",
                "args": {"path": "big.txt"}, "args_raw": "{}"}])
        return Reply(content="FINAL: ok")


def test_large_fs_read_survives_to_model(tmp_path):
    root = str(tmp_path)
    with open(os.path.join(root, "big.txt"), "w", encoding="utf-8") as f:
        f.write("A" * 20000)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    r = Runner(con, root, _Model(), limits=Limits(max_model_calls=4),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "读大文件"), "t1")
    ck = con.execute("SELECT messages FROM checkpoints ORDER BY step DESC LIMIT 1").fetchone()
    msgs = json.loads(ck["messages"])
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs
    payload = json.loads(tool_msgs[0]["content"])     # 模型看到的是合法 JSON
    assert payload.get("db_tool_call_id", "").startswith("TC-")
    assert "tool_call_id" not in payload              # 内容里不再放协议 id(避免混用)
    assert msgs[[m.get("role") for m in msgs].index("tool")]["tool_call_id"] == "call_x"


def test_default_payload_keeps_pi_sized_tail_without_arbitrary_16k_cut():
    out = {"status": "ok", "stdout_tail": "x" * 30_000,
           "full_output_path": "/tmp/fwcore-output-test.log"}
    d = json.loads(_bounded_tool_result(out))
    assert len(d["stdout_tail"]) == 30_000
    assert d["full_output_path"] == "/tmp/fwcore-output-test.log"
    assert "_truncated" not in d
