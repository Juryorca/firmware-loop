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
    st = build_status(model="m", window=None, output_reserve=8_000, context_tokens=0)
    d = st.as_dict()
    assert d["available"] is None and d["compaction_enabled"] is False
    assert d["used_input"] is None and d["used_source"] == "unknown"
    txt = st.render()
    assert "unknown" in txt and "do not assume it is nearly full" in txt


def test_status_reports_provider_usage_and_prepare():
    st = build_status(model="m", window=1_000_000,
                      output_reserve=8_000, context_tokens=1_000_000,
                      last_prompt_tokens=950_000, last_cached_tokens=900_000)
    assert st.used_input == 950_000 and st.used_source == "provider"
    assert st.available == 42_000
    assert st.threshold == threshold_tokens(1_000_000, 8_000) == 992_000
    assert st.prepare is True
    txt = st.render()
    assert "window=1,000,000" in txt and "context_used=950,000 (provider prompt)" in txt
    assert "estimated_input" not in txt
    assert "Hard compaction limit" in txt and "compact_now" in txt


def test_status_without_provider_usage_is_unknown():
    st = build_status(model="m", window=1_048_576,
                      output_reserve=8_000, context_tokens=1_048_576)
    assert st.used_input is None and st.used_source == "unknown"
    assert st.available is None and st.prepare is False
    txt = st.render()
    assert "context_used=unknown (provider usage unavailable)" in txt
    assert "estimated_input" not in txt
    assert "capacity is unknown" in txt


def test_status_uses_provider_prompt_only():
    """789k provider usage must not be inflated by a 972k chars/2.0 estimate."""
    st = build_status(model="m", window=1_048_576,
                      output_reserve=8_000, context_tokens=1_048_576,
                      last_prompt_tokens=789_176, last_cached_tokens=786_688)
    assert st.used_input == 789_176 and st.used_source == "provider"
    assert st.available == 1_048_576 - 789_176 - 8_000
    assert st.prepare is True                  # 75% is the Pi preparation boundary
    assert st.hard is False
    txt = st.render()
    assert "context_used=789,176 (provider prompt)" in txt
    assert "estimated_input" not in txt
    assert "Context is getting full" in txt
    assert "Plenty of room" not in txt


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


def test_context_note_is_appended_and_persisted(tmp_path):
    con, root = _ws(tmp_path)
    m = Rec()
    r = Runner(con, root, m, limits=Limits(max_model_calls=5, context_tokens=100_000,
                                           output_reserve_tokens=8_000),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    rid = r.start("t1", "感知")
    r.run(rid, "t1")
    assert m.calls
    assert m.calls[0][0]["role"] == "system"
    assert "## 现状" not in m.calls[0][0]["content"], "状态块不得进 system"
    assert "## 现状" in m.calls[0][-1]["content"], "状态卡应在首个请求尾部"
    # 状态卡是 turn/advance 输入，不是每个 tool result 后都要追加一份。
    for prev, cur in zip(m.calls, m.calls[1:], strict=False):
        assert not any("## 现状" in (msg.get("content") or "")
                       for msg in cur[len(prev):])
    # 动态 context 块不写入 session;session 只保留用户/assistant/tool 条目。
    assert r._load_history(rid)


def test_system_prompt_identical_across_calls(tmp_path):
    con, root = _ws(tmp_path)
    m = Rec()
    r = Runner(con, root, m, limits=Limits(max_model_calls=5),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "静态"), "t1")
    assert len({c[0]["content"] for c in m.calls}) == 1


def test_limits_config_from_model_json(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "state"), exist_ok=True)
    cfg = {"context_tokens": 1_000_000, "output_reserve_tokens": 12_000,
           "max_model_calls": 1000}  # legacy key must be ignored
    with open(os.path.join(str(tmp_path), "state", "model.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.environ.pop("FW_CONTEXT_TOKENS", None)
    d = limits_config(str(tmp_path))
    assert d["context_tokens"] == 1_000_000 and d["output_reserve_tokens"] == 12_000
    assert "max_model_calls" not in d
    os.environ["FW_CONTEXT_TOKENS"] = "500000"
    os.environ["FW_MAX_MODEL_CALLS"] = "1500"
    try:
        d2 = limits_config(str(tmp_path))
        assert d2["context_tokens"] == 500_000   # env 覆盖
        assert "max_model_calls" not in d2
    finally:
        os.environ.pop("FW_CONTEXT_TOKENS")
        os.environ.pop("FW_MAX_MODEL_CALLS")


def test_non_object_model_config_is_rejected(tmp_path, monkeypatch):
    from fwcore.models import limits_config, load_model_config
    p = tmp_path / "model.json"
    p.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("FW_MODEL_CONFIG", str(p))
    import pytest
    with pytest.raises(ValueError):
        limits_config(str(tmp_path))
    with pytest.raises(ValueError):
        load_model_config(str(tmp_path))


def _usage_event(con, rid, usage, *, kind=None):
    payload = {"usage": usage, "prompt_tokens": usage.get("prompt_tokens"),
               "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}
    if kind:
        payload["kind"] = kind
    con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                ("2026-01-01T00:00:00+00:00", "model.call", rid, json.dumps(payload)))


def test_latest_main_without_usage_is_unknown_not_older_usage(tmp_path):
    con, root = _ws(tmp_path)
    r = Runner(con, root, Rec(), mcp=False, watchdog=False)
    rid = r.start("t1", "usage ordering")
    _usage_event(con, rid, {"prompt_tokens": 11})
    _usage_event(con, rid, {})
    _usage_event(con, rid, {"prompt_tokens": 99_999}, kind="summarize")
    assert r._last_main_usage(rid) == {}
    assert r._last_main_prompt_tokens(rid) is None


def test_summarize_usage_is_ignored_for_last_main_usage(tmp_path):
    con, root = _ws(tmp_path)
    r = Runner(con, root, Rec(), mcp=False, watchdog=False)
    rid = r.start("t1", "usage ordering")
    _usage_event(con, rid, {"prompt_tokens": 11})
    for _ in range(40):
        _usage_event(con, rid, {"prompt_tokens": 99_999}, kind="summarize")
    assert r._last_main_usage(rid)["prompt_tokens"] == 11
