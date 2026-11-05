"""规则注入:预算内**按优先级选段落**,不是盲截前 N 字符。

背景:AGENTS/LOOP 的关键约束(停/不停、闸门、轮格式)常写在文件末尾;
盲截会把它们切掉,模型看到的就变成"没有硬约束的规则书"。
"""

from __future__ import annotations

from fwcore.research import select_rules


def _doc(filler_sections: int = 30, section_chars: int = 800) -> str:
    parts = ["# 规则书\n\n开头说明。\n"]
    for i in range(filler_sections):
        parts.append(f"## 附录 {i}\n" + ("填充内容。" * (section_chars // 5)) + "\n")
    # 关键段落放在**最后**(模拟 AGENTS 的 Exit Criteria / 停·不停 在末尾)
    parts.append("## 停 / 不停(硬)\n只有四种情况才停:...\n")
    parts.append("## 闸门(硬)\n派生量必须算,不能写。\n")
    return "".join(parts)


def test_priority_sections_survive_budget():
    doc = _doc()
    out = select_rules(doc, budget=3000)
    assert "停 / 不停" in out, "关键约束被截掉了"
    assert "闸门" in out
    assert len(out) <= 3000 + 300, "必须尊重预算(允许省略标记的少量开销)"


def test_omission_is_marked():
    out = select_rules(_doc(), budget=2000)
    assert "已省略" in out, "省略了内容就必须显式标注,不能静默丢"


def test_short_doc_untouched():
    short = "## 只有一节\n内容\n"
    assert select_rules(short, budget=4000) == short


def test_real_repo_rules_keep_critical_sections():
    """真实 AGENTS.md:预算压到 6000 时,至少保留"停 / 不停"或"Exit Criteria"其一。"""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "AGENTS.md"), encoding="utf-8") as f:
        text = f.read()
    out = select_rules(text, budget=6000)
    assert ("停 / 不停" in out) or ("Exit Criteria" in out), "关键约束段必须留下"
    assert len(out) <= 6000 + 300
