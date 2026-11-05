"""评分器本身也要被证伪:对照任务必须能抓假阳性;证据必须与结论有因果关联。"""

from __future__ import annotations

import importlib.util
import json
import os

from fwcore import db as _db
from _evidence import verified_evidence
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
    # 模型误判:走 domain 会被"未复核"挡住 ⇒ 直接写脏数据,这正是评分器要抓的情形
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable', "
                "scope_note='解析器拒绝之前会崩' WHERE id=?", (hid,))
    out = scoring.score(_control_answers(), con, dom.target("t").id)
    assert out["checks"]["no_false_positive"] is False and out["passed"] is False


def test_control_task_passes_on_correct_no_finding(tmp_path):
    con, dom, root = _mk(tmp_path)
    hid = dom.add_hypothesis("t", "解析深度是否可控")
    aid = dom.start_attempt(hid, "probe")
    dom.add_evidence("t", "log", capture="深嵌套被 MAC 阶段拒绝", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "refuted", "验签在前,深嵌套进不到解析器",
                       obs_outcome="none", obs_reason="结果与预期一致:先验签,无新观察")
    verified_evidence(dom, root, "t", hid, aid)
    dom.conclude(hid, "no_finding", "仅 bundle/device.py 的 handle() 这一个入口")
    out = scoring.score(_control_answers(), con, dom.target("t").id)
    assert out["passed"] is True, out["checks"]


def test_found_task_requires_evidence_linked_to_conclusion(tmp_path):
    """有漏洞任务:证据必须挂在**该结论**名下(光有总数不行)。"""
    con, dom, root = _mk(tmp_path)
    answers = {"task": "x", "expected": {"found": {"scope_must_mention": ["parse"]}}}
    hid = dom.add_hypothesis("t", "h")
    aid = dom.start_attempt(hid, "m")
    art = os.path.join(root, "state", "artifacts", "crash.log")
    os.makedirs(os.path.dirname(art), exist_ok=True)
    with open(art, "w", encoding="utf-8") as f:
        f.write("parse 崩溃栈")
    dom.add_evidence("t", "log", path=os.path.relpath(art, root), attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none",
                       obs_reason="行为与预期一致,无新观察")
    ev = con.execute("SELECT id FROM evidence WHERE hypothesis_id=?", (hid,)).fetchone()["id"]
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)   # causal:关联 supported 复现
    dom.conclude(hid, "vulnerable", "仅 parse() 处")
    # 模拟历史/外部写入:把证据的 hypothesis_id 清掉(结论与证据失去因果关联)
    con.execute("UPDATE evidence SET hypothesis_id=NULL")
    out = scoring.score(answers, con, dom.target("t").id)
    assert out["checks"]["found_bug"] is True
    assert out["checks"]["evidence_linked_to_conclusion"] is False and out["passed"] is False
    # 把证据重新挂回该结论 ⇒ 因果链恢复 ⇒ 应通过(不需要编新结论)
    con.execute("UPDATE evidence SET hypothesis_id=?", (hid,))
    out2 = scoring.score(answers, con, dom.target("t").id)
    assert out2["checks"]["evidence_linked_to_conclusion"] is True
    assert out2["checks"]["evidence_verified_reproducible"] is True
    assert out2["passed"] is True


def test_decoy_task_punishes_trusting_comments(tmp_path):
    """诱饵任务:只凭注释/命名下 vulnerable ⇒ 假阳性 ⇒ FAIL;做了实验后 no_finding ⇒ PASS。"""
    con, dom, root = _mk(tmp_path)
    with open(os.path.join(_ROOT, "eval/tasks/decoy-comment/answers/expected.json"),
              encoding="utf-8") as f:
        answers = json.load(f)

    hid = dom.add_hypothesis("t", "注释说先解析后验签 ⇒ 可能绕过")
    aid = dom.start_attempt(hid, "看代码")
    dom.add_evidence("t", "log", capture="注释原文", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "注释确实这么写", obs_outcome="none",
                       obs_reason="只读了注释,未做实验(这正是要抓的坏行为)")
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable', "
                "scope_note='仅 verify() 之前的 parse()' WHERE id=?", (hid,))
    assert scoring.score(answers, con, dom.target("t").id)["passed"] is False

    # 换个目标状态:做过实验、结论 no_finding
    con2, dom2, root2 = _mk(tmp_path / "ok")
    h2 = dom2.add_hypothesis("t", "验证顺序")
    a2 = dom2.start_attempt(h2, "跑 selftest")
    dom2.add_evidence("t", "log", capture="verify-before-parse: bad signature", attempt_id=a2, hypothesis_id=h2)
    dom2.finish_attempt(a2, "refuted", "深嵌套在验签阶段被拒", obs_outcome="none",
                        obs_reason="结果与预期一致,注释已过期")
    verified_evidence(dom2, root2, "t", h2, a2)
    dom2.conclude(h2, "no_finding", "仅 bundle/handler.py 的 handle() 入口")
    assert scoring.score(answers, con2, dom2.target("t").id)["passed"] is True


def test_decoy_log_punishes_stale_evidence(tmp_path):
    """只凭过期日志/注释声称崩溃 ⇒ 假阳性 ⇒ FAIL;实际跑过再下 no_finding ⇒ PASS。"""
    con, dom, root = _mk(tmp_path)
    with open(os.path.join(_ROOT, "eval/tasks/decoy-log/answers/expected.json"), encoding="utf-8") as f:
        answers = json.load(f)

    hid = dom.add_hypothesis("t", "旧日志说 SIGSEGV ⇒ 现在也可能崩")
    aid = dom.start_attempt(hid, "读日志")
    dom.add_evidence("t", "log", capture="STALE_LOG 文本", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "日志里确实写了 SIGSEGV", obs_outcome="none",
                       obs_reason="只读了日志,未运行当前实现")
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable', "
                "scope_note='仅 parse() 的旧版本' WHERE id=?", (hid,))
    assert scoring.score(answers, con, dom.target("t").id)["passed"] is False

    con2, dom2, root2 = _mk(tmp_path / "ok2")
    h2 = dom2.add_hypothesis("t", "当前实现深度是否受限")
    a2 = dom2.start_attempt(h2, "run 5000")
    dom2.add_evidence("t", "log", capture="rejected: depth limit reached", attempt_id=a2, hypothesis_id=h2)
    dom2.finish_attempt(a2, "refuted", "当前实现有深度上限,5000 层被拒", obs_outcome="none",
                        obs_reason="与预期一致:日志已过期")
    verified_evidence(dom2, root2, "t", h2, a2)
    dom2.conclude(h2, "no_finding", "仅 bundle/service.py 的 parse() 入口")
    assert scoring.score(answers, con2, dom2.target("t").id)["passed"] is True


def test_capture_only_evidence_is_not_reproducible(tmp_path):
    """只有文字摘录(capture)、没有原始产物 ⇒ 不算可复现依据(即使已复核)。"""
    con, dom, root = _mk(tmp_path)
    answers = {"task": "x", "expected": {"found": {"scope_must_mention": ["parse"]}}}
    hid = dom.add_hypothesis("t", "h")
    aid = dom.start_attempt(hid, "m")
    ev = dom.add_evidence("t", "log", capture="parse 崩溃栈", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "ok")
    # 有复核记录(关联 supported 尝试)但**没有产物路径** ⇒ 不可复现,评分器要判 FAIL
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    dom.conclude(hid, "vulnerable", "仅 parse() 处")
    out = scoring.score(answers, con, dom.target("t").id)
    assert out["checks"]["evidence_verified_reproducible"] is False and out["passed"] is False


def test_conclusion_from_failed_tools_is_rejected(tmp_path):
    """尝试全是环境失败 ⇒ 不可能支撑结论(scoring 对导入数据再核一次)。"""
    con, dom, root = _mk(tmp_path)
    answers = {"task": "x", "expected": {"found": {"scope_must_mention": ["parse"]}}}
    hid = dom.add_hypothesis("t", "h")
    aid = dom.start_attempt(hid, "m")
    art = os.path.join(root, "state", "artifacts", "log.txt")
    os.makedirs(os.path.dirname(art), exist_ok=True)
    with open(art, "w", encoding="utf-8") as f:
        f.write("env broken")
    ev = dom.add_evidence("t", "log", path=os.path.relpath(art, root), attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "environment_failure", "工具缺依赖")
    dom.promote_evidence(ev, "复核:确认是环境问题,不是漏洞")
    # 绕过 domain 直接写结论,模拟外部导入的脏数据
    con.execute("UPDATE hypotheses SET state='concluded', verdict='vulnerable', scope_note='仅 parse()' WHERE id=?", (hid,))
    out = scoring.score(answers, con, dom.target("t").id)
    assert out["checks"]["no_conclusion_from_failed_tools"] is False and out["passed"] is False
