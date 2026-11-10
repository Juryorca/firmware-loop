"""模型目录:从 provider 的 /models 自动发现;不维护硬编码模型白名单。"""

from __future__ import annotations

from fwapp.config import AppConfig, adapter_for, available_models
from fwcore.limits import Limits
from fwcore.models import (
    ModelInfo,
    fetch_model_catalog,
    parse_model_info,
    split_context_annotation,
)


def test_context_annotation_is_split_from_api_id():
    assert split_context_annotation("vendor/model[1m]") == ("vendor/model", 1_000_000)
    assert split_context_annotation("model[256k]") == ("model", 256_000)
    info = parse_model_info({"id": "vendor/model[1m]", "name": "Vendor Model"})
    assert info is not None
    assert info.api_id == "vendor/model" and info.context_window == 1_000_000


def test_provider_metadata_wins_over_annotation():
    info = parse_model_info({"id": "m[1m]", "context_window": 128_000,
                             "max_output_tokens": 16_384, "owned_by": "vendor"})
    assert info is not None
    assert info.context_window == 128_000
    assert info.max_output_tokens == 16_384
    assert info.owned_by == "vendor"


def test_fetch_catalog_parses_openai_style_data(monkeypatch):
    monkeypatch.setattr("fwcore.models.list_models", lambda *a, **k: [
        {"id": "a", "context_window": 128000},
        {"id": "b[1m]", "name": "B"},
        {"not_a_model": True},
    ])
    rows = fetch_model_catalog("https://example.test/v1", "key")
    assert [x.api_id for x in rows] == ["a", "b"]
    assert rows[0].context_window == 128000 and rows[1].context_window == 1_000_000


def test_available_models_fetches_and_keeps_configured_fallback(monkeypatch):
    cfg = AppConfig(
        root=".", db_path=":memory:", model_name="configured",
        limits=Limits(context_tokens=1_000),
        api_key="key", base_url="https://example.test/v1", model="configured",
    )
    calls = []
    def fake_fetch(base, key, **kw):
        calls.append((base, key))
        return [ModelInfo(id="live", label="Live", api_id="live", context_window=200_000)]
    monkeypatch.setattr("fwapp.config.fetch_model_catalog", fake_fetch)
    rows = available_models(cfg, refresh=True)
    assert [x.api_id for x in rows] == ["scripted", "live", "configured"]
    assert calls == [("https://example.test/v1", "key")]
    assert cfg.model_catalog_error == ""


def test_adapter_for_uses_selected_catalog_model(monkeypatch):
    cfg = AppConfig(root=".", db_path=":memory:", model_name="default",
                    limits=Limits(), api_key="key",
                    base_url="https://example.test/v1", model="default")
    seen = {}
    def fake_build(kind, **kw):
        seen.update({"kind": kind, **kw})
        return object()
    monkeypatch.setattr("fwapp.config.build_adapter", fake_build)
    adapter_for(cfg, "another-model")
    assert seen["kind"] == "openai" and seen["model"] == "another-model"
