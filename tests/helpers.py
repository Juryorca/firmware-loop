"""测试共用小工具:造可检查的产物 + 走真实复核路径。

注意:复核(`verify_evidence`)要求**可检查的产物**(哈希/关联尝试/交叉验证),
所以测试里也必须给出真文件 —— 这正是"不许只凭声明"的语义。
"""

from __future__ import annotations

import os


def artifact(root: str, name: str, content: str = "artifact") -> str:
    """在 root 下写一个产物文件,返回相对路径(证据用相对 root 的路径)。"""
    full = os.path.join(root, "state", "artifacts", name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.relpath(full, root)


def verify(dom, ev_id: str, root: str, *, name: str = "cross-check.txt",
           detail: str = "对照复现产物与记录,行为一致") -> str:
    """用 cross-check(带产物)把证据复核成 verified。"""
    return dom.verify_evidence(ev_id, "cross-check", artifact=artifact(root, name),
                               detail=detail)
