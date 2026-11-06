"""上下文感知:请求尾部状态块(真实窗口/阈值/未知值)、不入库、不改 system、压缩后更新。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.compact import threshold_tokens
from fwcore.contextinfo import build_status
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply, limits_config
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


def test_status_unknown_window_is_honest():
    st = build_status(model="m", window=None, messages=[{"role": "user", "content": "x" * 200}],
                      output_reserve=8_000, context_tokens=0)
    d = st.as_dict()
    assert d["available"] is None and d["compaction_enabled"] is False
    txt = st.render()
    assert "unknown" in txt and "do not assume it is nearly full" in txt


def test_status_reports_numbers_and_prepare():
    msgs = [{"role": "user", "content": "x" * 1_700_000}]     # ≈850k tokens(2.0)
    st = build_status(model="m", window=1_000_000, messages=msgs,
                      output_reserve=8_000, context_tokens=1_000_000,
                      last_prompt_tokens=1234, last_cached_tokens=1000)
    assert st.estimated_input == 850_000
    assert st.available == 1_000_000 - 850_000 - 8_000
    assert st.threshold == threshold_tokens(1_000_000, 8_000) == 900_000
    assert st.prepare is True
    txt = st.render()
    assert "window=1,000,000" in txt and "last reply usage: prompt 1,234" in txt
    assert "save key progress" in txt


class Rec:
    name = "rec"

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.n = 0

    def chat(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        self.n += 1
        if self.n > 2:
            return Reply(content="FINAL: ok")
        return Reply(content="", tool_calls=[{
            "id": f"c{self.n}", "name": "fs_read",
            "args": {"path": f"n{self.n}.txt"}, "args_raw": "{}"}])


def _ws(tmp_path):
    root = str(tmp_path)
    for i in (1, 2):
        with open(os.path.join(root, f"n{i}.txt"), "w", encoding="utf-8") as f:
            f.write("x" * 200)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    return con, root


def test_context_note_is_tail_only_and_not_persisted(tmp_path):
    con, root = _ws(tmp_path)
    m = Rec()
    r = Runner(con, root, m, limits=Limits(max_model_calls=5, context_tokens=100_000,
                                           output_reserve_tokens=8_000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "感知"), "t1")
    assert m.calls
    for msgs in m.calls:
        assert msgs[0]["role"] == "system"
        assert "[Context status]" not in msgs[0]["content"], "状态块不得进 system"
        assert "[Context status]" in msgs[-1]["content"], "状态块应在尾部"
    # 不写入 checkpoint(不持久化)
    ck = con.execute("SELECT messages FROM checkpoints ORDER BY step DESC LIMIT 1").fetchone()
    assert ck and "[Context status]" not in ck["messages"]


def test_system_prompt_identical_across_calls(tmp_path):
    con, root = _ws(tmp_path)
    m = Rec()
    r = Runner(con, root, m, limits=Limits(max_model_calls=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "静态"), "t1")
    assert len({c[0]["content"] for c in m.calls}) == 1


def test_limits_config_from_model_json(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "state"), exist_ok=True)
    cfg = {"context_tokens": 1_000_000, "output_reserve_tokens": 12_000}
    with open(os.path.join(str(tmp_path), "state", "model.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.environ.pop("FW_CONTEXT_TOKENS", None)
    d = limits_config(str(tmp_path))
    assert d["context_tokens"] == 1_000_000 and d["output_reserve_tokens"] == 12_000
    os.environ["FW_CONTEXT_TOKENS"] = "500000"
    try:
        assert limits_config(str(tmp_path))["context_tokens"] == 500_000   # env 覆盖
    finally:
        os.environ.pop("FW_CONTEXT_TOKENS")
