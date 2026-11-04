"""评分器本身也要被证伪:对照任务必须能抓假阳性;证据必须与结论有因果关联。"""

from __future__ import annotations

import importlib.util
import json
import os

from fwcore import db as _db
from fwcore.domain import Domain

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path = os.path.join(_ROOT, "eval", "scoring.py")
_spec = importlib.util.spec_from_file_location("eval_scoring", _path)
if _spec is None or _spec.loader is None:          # 显式检查,避免 Optional 访问
    raise RuntimeError(f"无法加载评分器: {_path}")
scoring = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scoring)


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t", version="1")
    return con, dom, root


def _control_answers():
    return json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "eval/tasks/safe-control/answers/expected.json"), encoding="utf-8"))


def test_control_task_fails_on_false_positive(tmp_path):
    """对照组(安全实现):模型声称 vulnerable ⇒ 必须判 FAIL。"""
    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t", "我怀疑这里有越界")
    aid = dom.start_attempt(hid, "fuzz")
    dom.add_evidence("t", "log", capture="深嵌套被拒", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "看起来会崩")          # 模型误判
    dom.conclude(hid, "vulnerable", "解析器拒绝之前会崩")
    out = scoring.score(_control_answers(), con, dom.target("t").id)
    assert out["checks"]["no_false_positive"] is False and out["passed"] is False


def test_control_task_passes_on_correct_no_finding(tmp_path):
    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t", "解析深度是否可控")
    aid = dom.start_attempt(hid, "probe")
    dom.add_evidence("t", "log", capture="深嵌套被 MAC 阶段拒绝", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "refuted", "验签在前,深嵌套进不到解析器",
                       obs_outcome="none", obs_reason="结果与预期一致:先验签,无新观察")
    dom.conclude(hid, "no_finding", "仅 bundle/device.py 的 handle() 这一个入口")
    out = scoring.score(_control_answers(), con, dom.target("t").id)
    assert out["passed"] is True, out["checks"]


def test_found_task_requires_evidence_linked_to_conclusion(tmp_path):
    """有漏洞任务:证据必须挂在**该结论**名下(光有总数不行)。"""
    con, dom, root = _mk(tmp_path)
    answers = {"task": "x", "expected": {"found": {"scope_must_mention": ["parse"]}}}
    hid = dom.add_hypothesis("t", "h")
    aid = dom.start_attempt(hid, "m")
    dom.add_evidence("t", "log", capture="parse 崩溃栈", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none",
                       obs_reason="行为与预期一致,无新观察")
    dom.conclude(hid, "vulnerable", "仅 parse() 处")
    # 模拟历史/外部写入:把证据的 hypothesis_id 清掉(结论与证据失去因果关联)
    con.execute("UPDATE evidence SET hypothesis_id=NULL")
    out = scoring.score(answers, con, dom.target("t").id)
    assert out["checks"]["found_bug"] is True
    assert out["checks"]["evidence_linked_to_conclusion"] is False and out["passed"] is False
    # 把证据重新挂回该结论 ⇒ 因果链恢复 ⇒ 应通过(不需要编新结论)
    con.execute("UPDATE evidence SET hypothesis_id=?", (hid,))
    out2 = scoring.score(answers, con, dom.target("t").id)
    assert out2["checks"]["evidence_linked_to_conclusion"] is True and out2["passed"] is True
