"""用户 Python 环境列表:顺序、缺失跳过、依赖选择。"""
from __future__ import annotations

import json
import os
import sys

from fwcore import pyenvs


def test_config_order_and_missing_skip(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"))
    with open(os.path.join(root, "state", "pyenvs.json"), "w", encoding="utf-8") as f:
        json.dump({"python_envs": ["/no/such/python", sys.executable]}, f)
    envs = pyenvs.load(root)
    assert envs[0] == "/no/such/python"
    assert sys.executable in envs
    picked = pyenvs.select(root, ("json",))
    assert picked["selected"] == sys.executable
    assert any(a["path"] == "/no/such/python" and a["status"] == "missing"
               for a in picked["attempts"])


def test_env_var_has_priority(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setenv("FW_PYTHON_ENVS", os.pathsep.join(["/first/python", sys.executable]))
    envs = pyenvs.load(root)
    assert envs[:2] == ["/first/python", sys.executable]


def test_defaults_do_not_hardcode_tool_venvs():
    assert pyenvs.default_envs() == [sys.executable]


def test_legacy_toolenv_is_still_read(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"))
    with open(os.path.join(root, "state", "toolenv.json"), "w", encoding="utf-8") as f:
        json.dump({"python_envs": ["/legacy/python", sys.executable]}, f)
    assert pyenvs.load(root)[0] == "/legacy/python"


def test_corrupt_config_is_reported_not_silently_fallback(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"))
    with open(os.path.join(root, "state", "pyenvs.json"), "w", encoding="utf-8") as f:
        f.write("{bad json")
    try:
        pyenvs.load(root)
    except pyenvs.PyEnvConfigError as e:
        assert "JSON" in str(e)
    else:
        raise AssertionError("corrupt pyenvs.json must raise PyEnvConfigError")
