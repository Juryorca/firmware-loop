"""评测隔离:公开 goal(task.json)与私有答案(answers/)必须真正分开。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("eval_run", os.path.join(_ROOT, "eval", "run.py"))
if _spec is None or _spec.loader is None:
    raise RuntimeError("无法加载 eval/run.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

TASKS = sorted(d for d in os.listdir(os.path.join(_ROOT, "eval", "tasks")))


@pytest.mark.parametrize("task", TASKS)
def test_goal_is_public_and_answers_are_private(task):
    pub_path = os.path.join(_ROOT, "eval", "tasks", task, "task.json")
    priv_path = os.path.join(_ROOT, "eval", "tasks", task, "answers", "expected.json")
    with open(pub_path, encoding="utf-8") as f:
        pub = json.load(f)
    with open(priv_path, encoding="utf-8") as f:
        priv = json.load(f)
    assert pub.get("goal"), "公开侧必须有 goal"
    assert "goal" not in priv, "私有答案里不许留 goal(否则等于把答案当提示)"


@pytest.mark.parametrize("task", TASKS)
def test_load_task_separates_and_rejects_leak(task):
    public, private = ev.load_task(task)
    assert public["goal"] and "goal" not in private


def test_load_task_rejects_goal_in_answers(tmp_path, monkeypatch):
    tdir = tmp_path / "tasks" / "x"
    (tdir / "bundle").mkdir(parents=True)
    (tdir / "answers").mkdir()
    (tdir / "task.json").write_text(json.dumps({"task": "x", "goal": "公开目标"}), encoding="utf-8")
    (tdir / "answers" / "expected.json").write_text(
        json.dumps({"task": "x", "goal": "泄漏的目标", "expected": {}}), encoding="utf-8")
    monkeypatch.setattr(ev, "TASKS", str(tmp_path / "tasks"))
    with pytest.raises(SystemExit):
        ev.load_task("x")


def test_workspace_contains_only_bundle():
    work = ev.prepare_workspace("safe-control")
    try:
        assert os.path.isdir(os.path.join(work, "bundle"))
        assert not os.path.exists(os.path.join(work, "answers")), "工作区不许出现 answers/"
        assert not os.path.exists(os.path.join(work, "task.json")), "工作区不许出现私有/任务元数据"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_goal_does_not_leak_answer_text():
    """粗判:答案里的"为什么"整段不应出现在公开 goal 里。"""
    for task in TASKS:
        public, private = ev.load_task(task)
        why = (private.get("expected") or {}).get("why") or ""
        if len(why) >= 20:
            assert why not in public["goal"], f"{task}: 答案文本泄漏进 goal"
