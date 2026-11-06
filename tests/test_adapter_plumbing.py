"""诊断字段贯通:模拟 API 响应 → OpenAICompatAdapter → Runner → model.reply 事件。

不直接构造 Reply:走真实 adapter 与真实 HTTP 响应解析,确保 finish_reason/usage 不中途丢失。
"""

from __future__ import annotations

import json
import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import OpenAICompatAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


def _resp(message: dict, finish_reason: str = "stop", usage: dict | None = None) -> dict:
    return {"choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


class Api:
    """按脚本返回 API 响应 dict;记录我们实际发了什么。"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.sent: list[dict] = []

    def __call__(self, url, headers, payload):
        self.sent.append(payload)
        if self.i >= len(self.script):
            return _resp({"content": "FINAL: 收束"})
        item = self.script[self.i]
        self.i += 1
        return item


def _adapter(api) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(model="fake-model", api_key="k", transport=api)


def _runner(tmp_path, adapter):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Domain(con, root).create_target("t1", version="1")
    r = Runner(con, root, adapter, limits=Limits(max_model_calls=10), stall_repeats=3,
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    return con, r, r.start("t1")


def _replies(con):
    return [json.loads(x["payload"] or "{}") for x in
            con.execute("SELECT payload FROM events WHERE kind='model.reply'")]


def test_finish_reason_and_usage_survive_to_event(tmp_path):
    api = Api([
        _resp({"content": "分析到一半", "reasoning_content": "想了很多"}, "length",
              {"prompt_tokens": 3, "completion_tokens": 99, "total_tokens": 102}),
        _resp({"content": "本轮已收口\n\nFINAL: 完成"}),
    ])
    con, runner, rid = _runner(tmp_path, _adapter(api))
    res = runner.run(rid, "t1")
    ev = _replies(con)
    assert ev, "必须有 model.reply 诊断"
    assert ev[0]["class"] == "truncated", ev[0]
    assert ev[0]["finish_reason"] == "length"
    assert ev[0]["usage"]["completion_tokens"] == 99
    assert ev[0]["reasoning_len"] > 0
    assert res.state == "finished"


def test_reasoning_only_roundtrip_keeps_protocol_field(tmp_path):
    api = Api([_resp({"content": "", "reasoning_content": "只有推理"}),
               _resp({"content": "FINAL: 收束"})])
    adapter = _adapter(api)
    con, runner, rid = _runner(tmp_path, adapter)
    res = runner.run(rid, "t1")
    assert _replies(con)[0]["class"] == "reasoning_only"
    # 第二轮请求里,那条 assistant 必须带 reasoning_content 字段(thinking 协议)
    msgs = api.sent[1]["messages"]
    assistants = [m for m in msgs if m.get("role") == "assistant"]
    assert assistants and assistants[-1].get("reasoning_content") == "只有推理"
    assert res.state == "finished"


def test_tool_call_shape_from_api(tmp_path):
    tc = {"id": "c1", "type": "function",
          "function": {"name": "state_add_evidence", "arguments": json.dumps(
              {"kind": "log", "capture": "x"})}}
    # 工具不存在于注册表 ⇒ 仍应把 tool_call 记进诊断并继续,而不是崩
    api = Api([_resp({"content": "", "tool_calls": [tc]}, "tool_calls"),
               _resp({"content": "FINAL: 收束"})])
    con, runner, rid = _runner(tmp_path, _adapter(api))
    res = runner.run(rid, "t1")
    assert res.state == "finished"
    assert not _replies(con), "有工具调用时不该记 model.reply 诊断"


def test_second_paragraph_final_finishes_in_one_call(tmp_path):
    api = Api([_resp({"content": "总结如下……\n\nFINAL: 研究收口"})])
    con, runner, rid = _runner(tmp_path, _adapter(api))
    res = runner.run(rid, "t1")
    assert res.state == "finished"
    assert len(api.sent) == 1
    assert api.sent[0]["messages"][0]["role"] == "system"


def test_reasoning_content_padded_when_env_unset(monkeypatch):
    """resume 到新进程(env 没继承)也必须补 reasoning_content,否则 DeepSeek thinking 400。"""
    from fwcore.model_client import to_api_messages

    monkeypatch.delenv("FW_REASONING_CONTENT", raising=False)
    msgs = [{"role": "assistant", "content": "hi"}]
    assert to_api_messages(msgs)[0].get("reasoning_content") == ""


def test_reasoning_content_can_be_disabled_explicitly(monkeypatch):
    from fwcore.model_client import to_api_messages

    monkeypatch.setenv("FW_REASONING_CONTENT", "0")
    msgs = [{"role": "assistant", "content": "hi"}]
    assert "reasoning_content" not in to_api_messages(msgs)[0]


def test_http_transport_rejects_bad_host_and_port():
    """配置里的坏 URL 必须给清晰错误,而不是 AttributeError/ValueError 冒到调用方。"""
    from fwcore.model_client import ModelHTTPError, http_transport

    for url in ("http:///x", "http://127.0.0.1:bad/v1"):
        with pytest.raises(ModelHTTPError):
            http_transport(url, {}, {})
