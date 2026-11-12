"""前缀缓存布局:system 静态、历史只追加、动态状态在最后一条 user 消息。

依据真实 run 的 usage:system 内含 derived/进度 ⇒ 每次变化让缓存命中从 ~98% 掉到 ~1%。
"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


class Rec:
    name = "rec"

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.n = 0

    def chat(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        self.n += 1
        if self.n > 3:
            return Reply(content="FINAL: ok")
        return Reply(content="", tool_calls=[{
            "id": f"call_{self.n}", "name": "fs_read", "args": {"path": "note.txt"},
            "args_raw": "{}"}])


def _run(tmp_path):
    root = str(tmp_path)
    with open(os.path.join(root, "note.txt"), "w", encoding="utf-8") as f:
        f.write("x" * 300)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_observation("t1", "第一条观察")
    m = Rec()
    r = Runner(con, root, m, limits=Limits(max_model_calls=6),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "布局"), "t1")
    return m


def test_system_is_static_and_state_is_a_turn_input(tmp_path):
    m = _run(tmp_path)
    assert len(m.calls) >= 3
    systems = {c[0]["content"] for c in m.calls}
    assert len(systems) == 1, "system 必须逐字节静态(否则前缀缓存失效)"
    first = m.calls[0]
    assert first[-1]["role"] == "user"
    assert "## 现状" in first[-1]["content"] and "不是用户的新指令" in first[-1]["content"]
    assert "## 现状" not in first[0]["content"], "动态状态不能在 system 里"
    # The state prompt is a turn input.  Subsequent model requests must not
    # append another full copy after every tool result.
    for prev, cur in zip(m.calls, m.calls[1:], strict=False):
        new_messages = cur[len(prev):]
        assert not any(msg.get("_context_only") for msg in new_messages)


def test_history_prefix_is_append_only(tmp_path):
    m = _run(tmp_path)
    for prev, cur in zip(m.calls, m.calls[1:], strict=False):
        assert cur[:len(prev)] == prev, "历史不得原地改写/重排"
