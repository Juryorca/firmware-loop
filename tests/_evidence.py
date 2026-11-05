"""测试用:登记并复核一条文件证据(no_finding 要求 ≥1 条经复核证据)。"""

from __future__ import annotations

import os


def verified_evidence(dom, root: str, target: str, hid: str, aid: str,
                      text: str = "audit notes", name: str | None = None) -> str:
    rel = os.path.join("report", "artifacts", name or f"{hid}-audit.txt")
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    eid = dom.add_evidence(target, "log", path=rel, attempt_id=aid, hypothesis_id=hid)
    dom.verify_evidence(eid, method="recheck-sha256")
    return eid
