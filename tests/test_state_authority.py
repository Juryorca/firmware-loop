"""权威口径一致性:`derive` / `gates` / `state_prompt` 三处读同一份 SQLite,数字必须一致。

回归背景:面覆盖度曾有两套来源(notes §2.3 解析 vs surfaces 表)。现在唯一权威是 DB,
这条测试钉住"三个入口看到的是同一组计数"。
"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.gates_db import evaluate
from fwcore.research import Research


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t", version="1")
    dom.add_surface("t", "http/", disposition="vulnerable")
    dom.add_surface("t", "ssh/", disposition="no_finding")
    dom.add_surface("t", "udp/")                        # unjudged
    dom.add_hypothesis("t", "未判定面要打", surface_key="udp/")
    return root, con, dom, dom.derived("t"), evaluate(con, root, "t")


def test_derive_and_gates_agree(tmp_path):
    _root, _con, _dom, d, rep = _mk(tmp_path)
    nonzero = {k: v for k, v in d["surfaces"].items() if v}
    assert nonzero == rep.surfaces
    assert set(d["closure_blockers"]) == set(rep.blockers)


def test_state_prompt_uses_same_counts(tmp_path):
    root, _con, dom, _d, _rep = _mk(tmp_path)
    sp = Research(root, dom).state_prompt("t")
    assert '"unjudged": 1' in sp
    assert '"vulnerable": 1' in sp
    assert '"no_finding": 1' in sp


def test_state_card_is_compact_and_points_to_details(tmp_path):
    root, _con, dom, _d, _rep = _mk(tmp_path)
    card = Research(root, dom).state_card("t")
    assert "## 现状" in card
    assert "目标/版本: t / 1" in card
    assert "假设:" in card and "面:" in card and "收尾阻塞:" in card
    assert "research_queue / research_derived" in card
    assert len(card) < 1200
    assert "已有研究事实" not in card
    assert "用户对话历史" not in card
