"""对比工具:只吃结构化输入,输出差异与一行判定(不含解释)。"""

from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("eval_compare", os.path.join(_ROOT, "eval", "compare.py"))
if _spec is None or _spec.loader is None:
    raise RuntimeError("无法加载 eval/compare.py")
cmp_mod = importlib.util.module_from_spec(_spec)
sys.modules["eval_compare"] = cmp_mod          # dataclass 需要能按模块名找到它
_spec.loader.exec_module(cmp_mod)


def _c(title, verdict, ev=1):
    return {"title": title, "verdict": verdict, "scope": "仅 /x",
            "evidence": [{"kind": "log", "capture": f"e{i}"} for i in range(ev)]}


def test_compare_reports_verdict_mismatch_and_evidence_gap():
    a = {"target": "t", "conclusions": [_c("顺序缺陷", "vulnerable", 0), _c("仅 A 有", "no_finding", 1)]}
    b = {"target": "t", "conclusions": [_c("顺序缺陷", "no_finding", 2), _c("仅 B 有", "vulnerable", 1)]}
    out = cmp_mod.compare(a, b)
    assert [m["title"] for m in out["verdict_mismatch"]] == ["顺序缺陷"]
    assert out["evidence_gap"] and out["evidence_gap"][0]["a_evidence"] == 0
    assert out["only_a"] == ["仅 a 有"] and out["only_b"] == ["仅 b 有"]
    assert out["summary"].startswith("A=2 条 / B=2 条")


def test_compare_identical_lines_is_clean():
    a = {"conclusions": [_c("越权", "vulnerable", 1)]}
    out = cmp_mod.compare(a, a)
    assert out["verdict_mismatch"] == [] and out["evidence_gap"] == [] and out["same_verdict"] == 1
