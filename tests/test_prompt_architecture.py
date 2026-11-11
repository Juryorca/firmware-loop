"""System Prompt 组装:core + AGENTS + Skills/Commands 目录；案例/LOOP 不常驻。"""
from __future__ import annotations

import os

from fwcore.prompting import build_system_prompt, core_prompt
from fwcore.research import Research


def test_system_prompt_only_contains_agents_and_catalogs(tmp_path):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt = build_system_prompt(repo)
    assert core_prompt()[:30] in prompt
    assert "## 项目级指令（AGENTS.md）" in prompt
    assert "## 循环守则" in prompt                 # AGENTS 后段完整存在
    assert "## 工具速查" in prompt
    assert "Available Skills" in prompt
    assert "User Slash Commands" in prompt
    assert "反例 1" not in prompt                  # knowledge 正文不常驻
    assert "## 规则原文(LOOP.md)" not in prompt
    assert "FINAL:" in prompt


def test_core_prompt_has_no_tool_manual():
    """常驻 core prompt 只定义身份/协议；IDA 等工具用法属于 manifest/skill。"""
    prompt = core_prompt()
    assert "IDA headless" not in prompt
    assert "tools/ida_start.sh" not in prompt
    assert "tools/ida_call.sh" not in prompt


def test_missing_agents_is_reported_not_faked(tmp_path):
    prompt = build_system_prompt(str(tmp_path))
    assert "未找到根目录 AGENTS.md" in prompt
    assert "FINAL:" in prompt


def test_research_system_prompt_is_static_and_state_is_tail(tmp_path):
    from fwcore import db as _db
    from fwcore.domain import Domain
    con = _db.connect(str(tmp_path / "state" / "fwcore.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    rid = dom.add_hypothesis("t1", "测试假设")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = Research(repo, dom)
    a = r.rules_prompt("t1")
    b = r.rules_prompt("t1")
    assert a == b
    full = r.system_prompt("t1", goal="验证")
    assert full.startswith(a)
    assert "本轮目标" in full and "验证" in full
    assert rid in full


def test_slash_commands_are_registered_and_not_tools():
    from fwapp.commands import COMMANDS
    from fwapp.modes.interactive import FwApp
    names = {c.name for c in COMMANDS}
    for name in ("help", "status", "skills", "knowledge", "continue", "quit"):
        assert name in names
    # prompt catalog 与实际 TUI dispatcher 必须同步：每个登记命令都有 handler。
    for c in COMMANDS:
        assert hasattr(FwApp, f"_cmd_{c.name.replace('-', '_')}"), c.name


def test_opencode_assets_are_gone():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert not (root / "opencode.json").exists()
    assert not (root / "prompts").exists()


def test_all_discovered_skills_are_injected_into_agent_prompt():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt = build_system_prompt(repo)
    # 用户目录里的 Skill 是可用操作知识，不按 audience 隐藏。
    catalog = prompt.split("## Available Skills", 1)[1].split("## User Slash Commands", 1)[0]
    assert "fwcore-development" in catalog
    assert "research-loop" in catalog


def test_pi_prompt_templates_discover_and_expand(tmp_path):
    """Project prompts follow Pi's filename/frontmatter/argument contract."""
    from fwcore.prompt_templates import discover, expand
    prompts = tmp_path / ".pi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "review.md").write_text(
        "---\n"
        "description: Review changes\n"
        "argument-hint: \"[focus]\"\n"
        "---\n"
        "Review $1 with focus ${2:-correctness, security}; "
        "all=$@; tail=${@:2}; tail1=${@:2:1}; "
        "fallback=${@:-nothing}; nested=${2:+(focus: ${2})}\n",
        encoding="utf-8")
    (prompts / "bad.md").write_text("---\n", encoding="utf-8")  # no body

    found = discover(str(tmp_path))
    assert set(found) == {"review"}
    tpl = found["review"]
    assert tpl.description == "Review changes"
    assert tpl.arg_hint == "[focus]"

    text = expand(tpl, 'src "error handling"')
    assert "Review src" in text
    assert "focus error handling" in text
    assert "all=src error handling" in text
    assert "tail=error handling" in text
    assert "tail1=error handling" in text
    assert "fallback=src error handling" in text
    assert "nested=(focus: error handling)" in text

    empty = expand(tpl, "")
    assert "focus correctness, security" in empty
    assert "all=" in empty
    assert "fallback=nothing" in empty
    assert "nested=" in empty


def test_prompt_templates_are_in_the_system_command_catalog(tmp_path):
    """The model-facing command catalog must match what the TUI exposes."""
    prompts = tmp_path / ".pi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "review.md").write_text(
        "---\n"
        "description: Review staged changes\n"
        "argument-hint: \"[focus]\"\n"
        "---\n"
        "Review ${1:-everything}.\n",
        encoding="utf-8")
    prompt = build_system_prompt(str(tmp_path))
    assert "`/review [focus]`: Review staged changes (prompt template)" in prompt
    assert "prompt template" in prompt


def test_prompt_templates_accept_crlf_and_bom(tmp_path):
    from fwcore.prompt_templates import discover, expand
    prompts = tmp_path / ".pi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "windows.md").write_bytes(
        b"\xef\xbb\xbf---\r\ndescription: Windows template\r\n---\r\nUse $1\r\n")
    found = discover(str(tmp_path))
    assert "windows" in found
    assert found["windows"].description == "Windows template"
    assert expand(found["windows"], "one") == "Use one\n"
