"""端到端(等级:模拟模型 + 模拟 MCP 服务器;非真实模型/非真实固件)。

链路:MCP 工具 → Runner 执行 → 工具产物落盘+哈希 → Evidence(绑定该次调用)
      → 复核(可检查记录,verified=1) → Conclusion(vulnerable) → Gates 认账。

这里**不用关键词脚本**,而是用一个"会读工具结果的模型替身":它必须真的解析
tool_call_id / hypothesis_id / attempt_id,才能把证据与结论连起来。
"""

from __future__ import annotations

import json
import os
import sys

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.gates_db import evaluate
from fwcore.models import Reply
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner

FAKE = r'''
import json, sys

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

TOOLS = [{"name": "decompile", "description": "反编译", "inputSchema": {
    "type": "object", "properties": {"path": {"type": "string"}}}}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
              "serverInfo": {"name": "fake", "version": "0.1"}, "capabilities": {}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [
            {"type": "text", "text": "decompiled: strcpy(dst, argv[1])  // no bound check"}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
'''


class ChainModel:
    """模型替身:解析上一条工具结果,推进研究链路(证明链路本身可走通)。"""

    name = "chain"

    def __init__(self) -> None:
        self.stage = 0
        self.seq = 0
        self.ids: dict[str, str] = {}

    def _call(self, name: str, args: dict) -> Reply:
        self.seq += 1
        return Reply(content="", tool_calls=[{"id": f"c{self.seq}", "name": name, "args": args}])

    def chat(self, messages, tools):
        for m in messages:
            if m.get("role") != "tool":
                continue
            try:
                data = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                continue
            if m.get("name") == "mcp_fake_decompile" and data.get("tool_call_id"):
                # 只认 MCP 那次调用的 id:所有工具结果都带自己的 tool_call_id,别被后来者覆盖
                self.ids["tool_call_id"] = data["tool_call_id"]
            for key in ("hypothesis_id", "attempt_id", "evidence_id", "check_id"):
                if data.get(key):
                    self.ids[key] = data[key]

        if self.stage == 0:
            self.stage += 1
            return self._call("mcp_fake_decompile", {"path": "bundle/app.bin"})
        if self.stage == 1 and "tool_call_id" in self.ids:
            self.stage += 1
            return self._call("state_add_hypothesis", {
                "target": "t1", "title": "argv[1] 直接 strcpy", "source": "[RE] 反编译",
                "body": "偏差:未做长度检查;可证伪:超长参数触发崩溃"})
        if self.stage == 2 and "hypothesis_id" in self.ids:
            self.stage += 1
            return self._call("state_start_attempt", {
                "hypothesis_id": self.ids["hypothesis_id"], "method": "poc"})
        if self.stage == 3 and "attempt_id" in self.ids:
            self.stage += 1
            return self._call("state_add_evidence", {
                "target": "t1", "kind": "tool_output",
                "tool_call_id": self.ids["tool_call_id"],
                "attempt_id": self.ids["attempt_id"],
                "hypothesis_id": self.ids["hypothesis_id"]})
        if self.stage == 4 and "evidence_id" in self.ids:
            self.stage += 1
            return self._call("state_finish_attempt", {
                "attempt_id": self.ids["attempt_id"], "result": "supported",
                "note": "复现成功", "obs_outcome": "none",
                "obs_reason": "与预期一致,无新观察"})
        if self.stage == 5 and "evidence_id" in self.ids:
            self.stage += 1
            # 用"关联复现尝试"复核(causal),不是只做哈希完整性检查
            return self._call("state_verify_evidence", {
                "evidence_id": self.ids["evidence_id"], "method": "reproduce-attempt",
                "attempt_id": self.ids["attempt_id"]})
        if self.stage == 6:
            self.stage += 1
            return self._call("state_conclude", {
                "hypothesis_id": self.ids["hypothesis_id"], "verdict": "vulnerable",
                "scope_note": "仅 bundle/app.bin 的 parse() 入口"})
        return Reply(content="FINAL: 链路完成", tool_calls=[])


def _target(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="5.0")
    srv = tmp_path / "fake.py"
    srv.write_text(FAKE, encoding="utf-8")
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"fake": {
        "command": sys.executable, "args": [str(srv)], "external": False}}}), encoding="utf-8")
    return con, dom, root, str(cfg)


def test_mcp_to_conclusion_end_to_end(tmp_path):
    con, dom, root, cfg = _target(tmp_path)
    r = Runner(con, root, ChainModel(), mcp=True, mcp_config_paths=[cfg],
               limits=__import__("fwcore.limits", fromlist=["Limits"]).Limits(max_model_calls=12),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    res = r.run(r.start("t1", "找参数溢出"), "t1", goal="找参数溢出")

    assert res.state == "finished", (res.state, res.blockers)

    # ① MCP 调用真的执行了,并留下原始产物+哈希
    tc = con.execute("SELECT * FROM tool_calls WHERE tool='mcp_fake_decompile'").fetchone()
    assert tc and tc["status"] == "ok" and tc["provider"] == "mcp:fake"
    assert tc["result_path"] and os.path.isfile(tc["result_path"]) and tc["result_sha256"]

    # ② 证据绑定到该次调用,并指向同一份产物
    ev = con.execute("SELECT * FROM evidence").fetchone()
    assert ev["tool_call_id"] == tc["id"]
    assert os.path.basename(ev["path"] or "") == os.path.basename(tc["result_path"])
    assert ev["sha256"] == tc["result_sha256"]
    assert ev["source"].startswith("tool:")

    # ③ 复核是可检查记录(recheck-sha256),而不是一句声明
    assert ev["verified"] == 1
    chk = con.execute("SELECT * FROM evidence_checks WHERE evidence_id=?", (ev["id"],)).fetchone()
    assert chk["method"] == "reproduce-attempt" and chk["attempt_id"]

    # ④ 结论成立且 gates 不再因此报阻塞
    hyp = con.execute("SELECT verdict, scope_note FROM hypotheses").fetchone()
    assert hyp["verdict"] == "vulnerable" and "parse()" in hyp["scope_note"]
    rep = evaluate(con, root, "t1")
    assert rep.findings and rep.findings[0]["verified"] >= 1
    assert not [b for b in rep.blockers if "未复核" in b]


def test_chain_stops_if_verification_is_missing(tmp_path):
    """反例:不做复核就下结论 ⇒ domain 拒绝(端到端也堵住)。"""
    con, dom, root, cfg = _target(tmp_path)
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    ev = dom.add_evidence("t1", "log", capture="看到崩溃", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "supported", "ok", obs_outcome="none", obs_reason="与预期一致,无新观察")
    import pytest

    from fwcore.domain import InvariantViolation

    with pytest.raises(InvariantViolation):
        dom.conclude(hid, "vulnerable", "仅 parse()")
    dom.verify_evidence(ev, "reproduce-attempt", attempt_id=aid)
    assert dom.conclude(hid, "vulnerable", "仅 parse()")
