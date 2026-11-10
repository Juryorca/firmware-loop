"""工具结果给模型看的那份:有界、始终合法 JSON、DB id 与产物路径不丢。"""

from __future__ import annotations

import json
import os
import tempfile

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
    rid = r.start("t1", "读大文件")
    r.run(rid, "t1")
    msgs = r._load_history(rid)
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


def test_payload_drops_raw_stdout_when_tail_exists():
    out = {"status": "ok", "stdout": "raw-internal", "stdout_tail": "tail"}
    d = json.loads(_bounded_tool_result(out))
    assert "stdout" not in d and d["stdout_tail"] == "tail"


def test_large_text_is_capped_to_pi_tail():
    out = {"status": "ok", "text": "x" * (2 * 1024 * 1024)}
    d = json.loads(_bounded_tool_result(out))
    assert len(d["text"].encode("utf-8")) <= 50 * 1024
    assert d["text_truncated"] is True


def test_large_structured_is_projected_and_full_json_saved():
    out = {"status": "ok", "structured": {"blob": "x" * 100_000}}
    d = json.loads(_bounded_tool_result(out))
    assert d["structured"]["_truncated"] is True
    path = d["structured_full_output_path"]
    try:
        assert path and os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == out["structured"]
        assert len(json.dumps(d["structured"], ensure_ascii=False)) < 1000
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def test_framework_temp_output_is_readable_but_other_temp_is_not(tmp_path):
    from fwcore.tools import ToolContext, fs_read
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True))
    fd, ok_path = tempfile.mkstemp(prefix="fwcore-output", suffix=".txt")
    os.write(fd, b"full tool output")
    os.close(fd)
    fd, bad_path = tempfile.mkstemp(prefix="not-fwcore-", suffix=".txt")
    os.write(fd, b"not allowed")
    os.close(fd)
    try:
        assert fs_read(ctx, {"path": ok_path})["status"] == "ok"
        assert fs_read(ctx, {"path": bad_path})["status"] == "error"
    finally:
        os.unlink(ok_path)
        os.unlink(bad_path)


def test_fs_read_can_read_a_later_slice_of_full_output(tmp_path):
    from fwcore.tools import ToolContext, fs_read
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    ctx = ToolContext(domain=dom, root=root, policy=Policy(allow_high_risk=True))
    fd, path = tempfile.mkstemp(prefix="fwcore-output", suffix=".txt")
    os.write(fd, b"HEAD" + b"x" * 9000 + b"TAIL")
    os.close(fd)
    try:
        out = fs_read(ctx, {"path": path, "offset": 9004, "limit": 100})
        assert out["status"] == "ok"
        assert out["offset"] == 9004 and out["text"].startswith("TAIL")
    finally:
        os.unlink(path)


def test_restored_oversized_tool_message_is_rebounded():
    from fwcore.runtime import _sanitize_history
    history = [{"role": "tool", "tool_call_id": "call_old",
                "content": json.dumps({"status": "ok",
                                       "structured": {"blob": "x" * 100_000}})}]
    out = _sanitize_history(history)
    payload = json.loads(out[0]["content"])
    assert payload["structured"]["_truncated"] is True
    path = payload["structured_full_output_path"]
    try:
        assert path and os.path.isfile(path)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
