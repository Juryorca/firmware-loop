"""端点 scheme 白名单:配置被改成 file:// 之类时必须拒绝(不静默发请求)。"""

from __future__ import annotations

import pytest

from fwcore.model_client import ModelHTTPError, OpenAICompatModel


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, payload):
        self.calls.append(url)
        return {"choices": [{"message": {"content": "ok"}}]}


def test_http_and_https_allowed():
    for base in ("https://api.example.com/v1", "http://127.0.0.1:8080/v1"):
        rec = _Recorder()
        m = OpenAICompatModel(model="x", base_url=base, api_key="k", transport=rec)
        m.chat([{"role": "user", "content": "hi"}], [])
        assert rec.calls and rec.calls[0].startswith(("http://", "https://"))


def test_other_schemes_rejected_before_request():
    rec = _Recorder()
    m = OpenAICompatModel(model="x", base_url="file:///etc", api_key="k", transport=rec)
    with pytest.raises(ModelHTTPError) as e:
        m.chat([{"role": "user", "content": "hi"}], [])
    assert "拒绝" in str(e.value) and rec.calls == [], "不得把请求发出去"
