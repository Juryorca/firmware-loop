"""端到端:大输出工具 -> 模型读到产物路径/DB id -> 记 observation(带来源) -> 下一 run 继承。"""

from __future__ import annotations

import json
import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner


class Model:
    name = "scripted"
    stage = 0
    obs_id = None

    def chat(self, messages, tools):
        for m in messages:
            if m.get("role") != "tool":
                continue
            try:
                d = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                continue
            if d.get("observation_id"):
                self.obs_id = d["observation_id"]
        self.stage += 1
        if self.stage == 1:
            return Reply(content="", tool_calls=[{
                "id": "call_1", "name": "fs_read",
                "args": {"path": "report/artifacts/op.txt"},
                "args_raw": "{}"}])
        if self.stage == 2:
            return Reply(content="", tool_calls=[{
                "id": "call_2", "name": "state_add_observation",
                "args": {"target": "t1", "text": "0x19908 取 op 后 strtol",
                         "tool_call_id": self.db_id, "path": "report/artifacts/op.txt"},
                "args_raw": "{}"}])
        return Reply(content="FINAL: 已记录")

    db_id = None

    def remember(self, messages):
        pass


class Model2(Model):
    """第二阶段:只读一遍历史,不再调工具。"""
    stage = 0

    def chat(self, messages, tools):
        for m in messages:
            if m.get("role") == "tool":
                try:
                    d = json.loads(m.get("content") or "{}")
                except json.JSONDecodeError:
                    continue
                if d.get("db_tool_call_id") and not self.db_id:
                    self.db_id = d["db_tool_call_id"]
        return super().chat(messages, tools)


def _ws(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    os.makedirs(os.path.join(root, "report", "artifacts"), exist_ok=True)
    with open(os.path.join(root, "report", "artifacts", "op.txt"), "w", encoding="utf-8") as f:
        f.write("0x19908 CGI_Find_Parameter(\"op\")\n" * 50)
    return con, root


def test_chain_large_output_observation_and_inheritance(tmp_path):
    con, root = _ws(tmp_path)
    m = Model2()
    r = Runner(con, root, m, limits=Limits(max_model_calls=6),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.run(r.start("t1", "看 op"), "t1")

    # 观察入库,且来源可证明(命令参数里出现该路径)
    row = con.execute("SELECT * FROM observations").fetchone()
    assert row is not None, "模型没登记观察"
    assert row["tool_call_id"] and row["tool_call_id"].startswith("TC-")
    assert row["path"].endswith("op.txt") and row["sha256"]

    # 下一 run:观察与其来源一起出现在系统提示里
    m2 = Model()
    r2 = Runner(con, root, m2, limits=Limits(max_model_calls=2),
                policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    seen = {}

    def spy(messages, tools):
        seen["sys"] = messages[0]["content"]
        return Reply(content="FINAL: ok")
    m2.chat = spy
    r2.run(r2.start("t1", "继续"), "t1", goal="继续")
    assert "0x19908" in seen["sys"]
    assert row["tool_call_id"] in seen["sys"] or "观察 O-" in seen["sys"]
