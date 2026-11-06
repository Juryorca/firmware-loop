"""协议格式 + 硬崩溃恢复(真实子进程)+ 控制流语义。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.model_client import OpenAICompatModel, to_api_messages
from fwcore.research import Research
from fwcore.runtime import Runner, ScriptedModel

# ---------------- ① 协议:请求体形状 + 历史合法性 ----------------

class FakeTransport:
    def __init__(self, reply: dict | None = None):
        self.payloads: list[dict] = []
        self.reply = reply or {"choices": [{"message": {"content": "ok", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "fs_hash", "arguments": '{"path": "a.txt"}'}}]}}]}

    def __call__(self, url, headers, payload):
        self.payloads.append(payload)
        return self.reply


def test_request_payload_matches_openai_schema():
    ft = FakeTransport()
    m = OpenAICompatModel(model="x", api_key="k", transport=ft)
    out = m.chat(
        [{"role": "system", "content": "s"},
         {"role": "user", "content": "u"}],
        [{"name": "fs_hash", "description": "d", "input_schema":
          {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}],
    )
    p = ft.payloads[0]
    assert p["messages"][0] == {"role": "system", "content": "s"}
    assert p["tools"][0]["type"] == "function"
    assert p["tools"][0]["function"]["parameters"]["required"] == ["path"]
    assert p["tool_choice"] == "auto"
    # 返回被规范化成内部结构,且 arguments 仍是原始 JSON 字符串
    assert out["tool_calls"][0]["function"]["name"] == "fs_hash"
    assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}


def test_history_with_tool_calls_is_valid_for_api():
    hist = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs_read", "arguments": '{"path":"a"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}'},
    ]
    api = to_api_messages(hist)
    assert api[1]["tool_calls"][0]["id"] == "c1"
    assert api[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}'}


def test_orphan_tool_message_rejected():
    with pytest.raises(ValueError):
        to_api_messages([{"role": "tool", "tool_call_id": "nope", "content": "x"}])


def test_missing_tool_result_rejected():
    with pytest.raises(ValueError):
        to_api_messages([{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c9", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}]}])


def test_run_produces_api_valid_history_with_real_tools(tmp_path):
    con = _db.connect(str(tmp_path / "p.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    from fwcore.policy import Policy
    r = Runner(con, str(tmp_path), ScriptedModel(script=[
        {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "fs_hash", "arguments": '{"path":"a.txt"}'}}]},
        {"content": "FINAL: done", "tool_calls": []},
    ]), policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    res = r.run(rid, "t1")
    api = to_api_messages(res["history"])          # 不抛异常 = 历史合法
    assert any(m["role"] == "tool" and m["tool_call_id"] == "c1" for m in api)


# ---------------- ② 控制流:FINAL / 步数耗尽 ----------------

def test_final_does_not_imply_ready(tmp_path):
    """FINAL 只结束本轮;有 open 假设/未判定面时 ready 必须为 False。"""
    con = _db.connect(str(tmp_path / "f.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    dom.add_hypothesis("t1", "未完成")
    r = Runner(con, str(tmp_path), ScriptedModel(script=[{"content": "FINAL: 我先收工", "tool_calls": []}]))
    rid = r.start("t1")
    res = r.run(rid, "t1")
    assert res["state"] == "finished" and res["final"].startswith("FINAL:")
    assert res["ready"] is False and res["blockers"]


def test_step_exhaustion_with_tool_calls_pauses(tmp_path):
    con = _db.connect(str(tmp_path / "s2.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    from fwcore.policy import Policy
    script = [{"content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                                             "function": {"name": "fs_hash", "arguments": '{"path":"a.txt"}'}}]}
              for i in range(5)]
    r = Runner(con, str(tmp_path), ScriptedModel(script=script), max_steps=2,
               policy=Policy(allow_high_risk=True))
    rid = r.start("t1")
    res = r.run(rid, "t1")
    assert res["state"] == "paused" and res["steps"] == 2


# ---------------- ③ 硬崩溃(子进程 os._exit)后恢复 ----------------

CHILD = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, sys.argv[3])
    from fwcore import db
    from fwcore.runtime import Runner

    class Crash:
        def __init__(self): self.n = 0
        def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return {"content": "看看", "tool_calls": []}
            os._exit(9)          # 模拟进程被硬杀:没有任何清理

    root = sys.argv[1]; rid = sys.argv[2]
    con = db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    Runner(con, root, Crash(), max_steps=10).run(rid, "t1")
''')


def test_hard_crash_then_recover_and_resume(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    rid = Runner(con, root, ScriptedModel(script=[]), max_steps=5).start("t1")
    con.close()

    child = os.path.join(root, "child.py")
    with open(child, "w", encoding="utf-8") as f:
        f.write(CHILD)
    proc = subprocess.run([sys.executable, child, root, rid, os.getcwd()], capture_output=True, text=True)
    assert proc.returncode == 9, proc.stderr

    con2 = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    row = con2.execute("SELECT state,step FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "running"            # 硬崩溃:状态悬挂
    assert con2.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"] >= 1

    from fwcore.policy import Policy
    r = Runner(con2, root, ScriptedModel(script=[{"content": "FINAL: 恢复后收工", "tool_calls": []}]),
               max_steps=5, policy=Policy(allow_high_risk=True))
    assert r.recover_stale() == [rid]
    assert con2.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"
    res = r.resume(rid)
    assert res["state"] == "finished" and res["final"].startswith("FINAL:")
    to_api_messages(res["history"])             # 恢复后的历史仍合法


# ---------------- ④ 研究载体:池排序 / 三镜 / 双输出 / 工具发现 ----------------

def _mk(root):
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="11")
    return con, dom


def test_pool_ordering_prefers_preauth_and_parsers(tmp_path):
    con, dom = _mk(str(tmp_path))
    dom.add_surface("t1", "12080 /api/parse", reachability="pre-auth",
                    input_control="报文", complexity="解析器")
    dom.add_surface("t1", "443 /admin", reachability="auth")
    a = dom.add_hypothesis("t1", "12080 /api/parse 的反序列化越界", source="观察")
    b = dom.add_hypothesis("t1", "443 /admin 的 CSRF", source="观察")
    q = Research(str(tmp_path), dom).pool("t1")
    assert [h["id"] for h in q][0] == a and q[-1]["id"] == b


def test_mirror_due_and_blocker(tmp_path):
    from fwcore.policy import Policy
    con, dom = _mk(str(tmp_path))
    dom.add_hypothesis("t1", "h")
    st: dict[str, str | None] = {"aid": None}

    def prep(_payload):                     # 轮内结束尝试(轮计数按"本 turn 推进")
        if st["aid"]:
            dom.finish_attempt(st["aid"], "inconclusive", "已排除外层", obs_outcome="none",
                               obs_reason="外层路由先拒绝,结果在预期内")
            st["aid"] = None
        return {}

    r = Runner(con, str(tmp_path), ScriptedModel(script=[{"content": "FINAL: x", "tool_calls": []}]),
               policy=Policy(allow_high_risk=True), prepare=prep)
    hid = dom.add_hypothesis("t1", "轮推进")
    for _ in range(3):
        st["aid"] = dom.start_attempt(hid, "m")
        rid = r.start("t1")
        r.run(rid, "t1")
    assert Research(str(tmp_path), dom).mirror_due("t1") is True
    mid = Research(str(tmp_path), dom).record_mirror("t1", "机制:签名前解析", "作者:校验在解析后", "状态:未测 beta")
    assert mid and Research(str(tmp_path), dom).rounds_since_mirror("t1") == 0



def test_dual_output_is_enforced_per_attempt_only(tmp_path):
    """旧的"目标级没有任何 observation"规则已删:全部 Attempt 合法声明 none 也应放行。"""
    con, dom = _mk(str(tmp_path))
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "diff")
    dom.finish_attempt(aid, "inconclusive", "只跑了外层")            # 未交代 ⇒ 领域层阻塞
    assert any("双输出" in b for b in dom.closure_blockers("t1"))
    # 双输出规则已按 attempt 强制(obs_outcome);目标级桩函数已删除
    dom.note_observation_outcome(aid, "none", "外层路由先拒绝,结果在预期内")
    assert not any("双输出" in b for b in dom.closure_blockers("t1"))


def test_repo_tools_are_discoverable(tmp_path):
    con, dom = _mk(str(tmp_path))
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = Runner(con, repo_root, ScriptedModel(script=[]))     # 用真实仓库根 ⇒ 才能发现 tools/*
    names = {t["name"] for t in r.registry.describe()}
    assert {"research_derived", "research_queue"} <= names      # 仓库里存在这些脚本
    assert all("input_schema" in t for t in r.registry.describe())


def test_reasoning_content_round_trips():
    """DeepSeek thinking 模式要求 reasoning_content 原样回传(真实踩过 400)。"""
    from fwcore.model_client import assistant_message
    hist = [
        assistant_message("", [{"id": "c1", "type": "function",
                                "function": {"name": "fs_read", "arguments": '{"path":"a"}'}}],
                          reasoning="我想先读文件"),
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]
    api = to_api_messages(hist)
    assert api[0]["reasoning_content"] == "我想先读文件"
    assert api[0]["tool_calls"][0]["id"] == "c1"


def test_empty_reasoning_content_is_preserved():
    """空串也必须回传(thinking 模式逐条要求;真实踩过 400)。"""
    hist = [{"role": "assistant", "content": "", "reasoning_content": "",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "fs_read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    api = to_api_messages(hist)
    assert "reasoning_content" in api[0] and api[0]["reasoning_content"] == ""


# ---------------- ⑤ 研究载体:提示确实来自仓库规则/池/三镜/KB ----------------

def test_system_prompt_is_built_from_repo_rules(tmp_path):
    con, dom = _mk(str(tmp_path))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hid = dom.add_hypothesis("t1", "签名前的解析路径仍要独立判断")
    prompt = Research(repo, dom).system_prompt("t1", goal="验证解析器")
    # 静态头不再复述规则(避免与 AGENTS/LOOP 双源);规则只从文件加载一次
    head = prompt.split("## 规则原文(AGENTS.md)")[0]
    assert "尝试结果六种" not in head
    assert "AGENTS.md" in prompt and "FINAL:" in prompt
    assert hid in prompt                                  # 池进了提示
    assert "## 规则原文(AGENTS.md)" in prompt              # 规则从文件读取,不复制进代码
    assert "假设池" in prompt                              # 原文内容真的在里面
    assert "可选方法/判据" in prompt                        # 方法清单来自 knowledge/(开放)
    assert "本轮目标" in prompt and "验证解析器" in prompt
