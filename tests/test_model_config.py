"""模型配置契约:环境变量 > state/model.json;缺配置/坏配置必须明确报错,不静默换模型。"""

from __future__ import annotations

import json
import os

import pytest

from fwcore import models
from fwcore.cli import _model


def _capture(monkeypatch):
    seen = {}

    def fake_build(kind, **kw):
        seen.update({"kind": kind, **kw})
        return object()

    monkeypatch.setattr("fwcore.cli.build_adapter", fake_build)
    return seen


def test_env_wins_over_state_file(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "model.json").write_text(json.dumps(
        {"api_key": "file-key", "base_url": "https://file.example", "model": "file-model"}))
    for k, v in (("FW_MODEL_API_KEY", "env-key"), ("FW_MODEL_BASE_URL", "https://env.example"),
                 ("FW_MODEL_NAME", "env-model")):
        monkeypatch.setenv(k, v)
    seen = _capture(monkeypatch)
    _model("openai", str(tmp_path))
    assert seen["api_key"] == "env-key" and seen["model"] == "env-model"


def test_state_file_used_when_env_absent(tmp_path, monkeypatch):
    for k in ("FW_MODEL_API_KEY", "FW_MODEL_BASE_URL", "FW_MODEL_NAME", "FW_API_KEY", "FW_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    state = tmp_path / "state"
    state.mkdir()
    (state / "model.json").write_text(json.dumps(
        {"api_key": "file-key", "base_url": "https://file.example", "model": "file-model"}))
    seen = _capture(monkeypatch)
    _model("openai", str(tmp_path))
    assert seen == {"kind": "openai", "api_key": "file-key",
                    "base_url": "https://file.example", "model": "file-model"}


def test_missing_config_is_reported(tmp_path, monkeypatch):
    for k in ("FW_MODEL_API_KEY", "FW_MODEL_BASE_URL", "FW_MODEL_NAME", "FW_API_KEY", "FW_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        _model("openai", str(tmp_path))
    assert "state" in str(e.value) and "model.json" in str(e.value)


def test_broken_config_is_reported_not_swallowed(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "model.json").write_text("{not json")
    with pytest.raises(SystemExit) as e:
        _model("openai", str(tmp_path))
    assert "配置" in str(e.value)


def test_load_model_config_reads_pi_free_file(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "model.json").write_text(json.dumps(
        {"api_key": "k", "base_url": "https://b", "model": "m"}))
    assert models.load_model_config(str(tmp_path)) == ("k", "https://b", "m")


def test_duplicate_conclude_is_rejected(tmp_path):
    from fwcore import db as _db
    from fwcore.domain import Domain, InvariantViolation
    from tests._evidence import verified_evidence

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "m")
    ev = dom.add_evidence("t1", "log", capture="x", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "refuted", "打不进去", evidence=[ev],
                       obs_outcome="none", obs_reason="与预期一致,无新观察")
    verified_evidence(dom, root, "t1", hid, aid)
    dom.conclude(hid, "no_finding", "仅 authLogin.cgi 的 func 分支入口")
    with pytest.raises(InvariantViolation) as e:
        dom.conclude(hid, "no_finding", "仅 authLogin.cgi 的 func 分支入口")
    assert "已收口" in str(e.value)
    assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 1


def test_assess_any_mode_needs_scope_not_title_match(tmp_path):
    """真实模式:expect=any 只看"有带范围的结论",不要求标题里出现文件名。"""
    import importlib.util
    import sys

    from fwcore import db as _db
    from fwcore.domain import Domain

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("drill", version="1")
    hid = dom.add_hypothesis("drill", "认证前分发可能存在绕过", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "审计")
    ev = dom.add_evidence("drill", "log", capture="分支清单", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "inconclusive", "未完成", evidence=[ev],
                       obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(hid, "inconclusive", "仅覆盖 5 个入口中的 2 个")

    spec = importlib.util.spec_from_file_location("drillmod", "scripts/research_drill.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["drillmod"] = m
    spec.loader.exec_module(m)
    verdict = m.assess(con, root, {"obj0": {"object": "authLogin.cgi", "expect": "any"}})
    assert verdict["checks"]["scope_conclusions_complete"]["ok"] is True


def test_assess_any_multi_object_needs_each_object_covered(tmp_path):
    """多对象 profile:一条结论不能算覆盖所有对象(真实反例)。"""
    import importlib.util
    import sys

    from fwcore import db as _db
    from fwcore.domain import Domain

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("drill", version="1")
    hid = dom.add_hypothesis("drill", "认证前分发存在绕过", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "审计")
    ev = dom.add_evidence("drill", "log", capture="分支清单", attempt_id=aid,
                          hypothesis_id=hid, path=None)
    dom.finish_attempt(aid, "inconclusive", "未完成", evidence=[ev],
                       obs_outcome="none", obs_reason="与预期一致,无新观察")
    dom.conclude(hid, "inconclusive", "仅覆盖 5 个入口中的 2 个")

    spec = importlib.util.spec_from_file_location("drillmod2", "scripts/research_drill.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["drillmod2"] = m
    spec.loader.exec_module(m)
    one = m.assess(con, root, {"o0": {"object": "authLogin.cgi", "expect": "any"}})
    assert one["checks"]["scope_conclusions_complete"]["ok"] is True
    two = m.assess(con, root, {"o0": {"object": "authLogin.cgi", "expect": "any"},
                               "o1": {"object": "other.bin", "expect": "any"}})
    assert two["checks"]["scope_conclusions_complete"]["ok"] is False
