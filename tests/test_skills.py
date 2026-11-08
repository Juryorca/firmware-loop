"""Skills:目录发现、frontmatter、按需正文/引用、路径安全。"""
from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.skills import SkillError, catalog, discover_skills, read_skill
from fwcore.tools import ToolContext, default_registry


def _mk(root, name="demo", body="正文", ref=True, audience=None):
    d = os.path.join(root, ".agents", "skills", name)
    os.makedirs(d, exist_ok=True)
    extra = f"audience: {audience}\n" if audience else ""
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: 测试用 Skill\n{extra}---\n\n# {body}\n")
    if ref:
        os.makedirs(os.path.join(d, "references"), exist_ok=True)
        with open(os.path.join(d, "references", "detail.md"), "w", encoding="utf-8") as f:
            f.write("引用内容\n")
    return d


def test_discover_catalog_and_read_reference(tmp_path):
    root = str(tmp_path)
    _mk(root)
    skills = discover_skills(root, strict=True)
    assert [s.name for s in skills] == ["demo"]
    assert catalog(root)[0]["description"] == "测试用 Skill"
    out = read_skill(root, "demo")
    assert out["content"].startswith("---") and "正文" in out["content"]
    out2 = read_skill(root, "demo", "references/detail.md")
    assert out2["content"].strip() == "引用内容"


def test_path_traversal_and_missing_are_rejected(tmp_path):
    root = str(tmp_path)
    _mk(root)
    for ref in ("../SKILL.md", "../../../../etc/passwd", "/etc/passwd"):
        try:
            read_skill(root, "demo", ref)
        except SkillError:
            pass
        else:
            raise AssertionError(f"应拒绝: {ref}")
    try:
        read_skill(root, "nope")
    except SkillError:
        pass
    else:
        raise AssertionError("未知 Skill 应报错")


def test_skill_tools(tmp_path):
    root = str(tmp_path)
    _mk(root)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    ctx = ToolContext(domain=dom, root=root)
    reg = default_registry()
    listed = reg.call(ctx, "skills_list", {})
    assert listed["status"] == "ok" and listed["skills"][0]["name"] == "demo"
    got = reg.call(ctx, "skill_read", {"name": "demo", "reference": "references/detail.md"})
    assert got["status"] == "ok" and "引用内容" in got["skill"]["content"]
    bad = reg.call(ctx, "skill_read", {"name": "demo", "reference": "../x"})
    assert bad["status"] == "error"


def test_development_skills_are_hidden_from_research_catalog_and_reader(tmp_path):
    root = str(tmp_path)
    _mk(root, "demo")
    _mk(root, "dev", audience="development")
    assert [s.name for s in discover_skills(root)] == ["demo", "dev"]
    assert [s["name"] for s in catalog(root)] == ["demo"]
    assert [s["name"] for s in catalog(root, include_development=True)] == ["demo", "dev"]
    try:
        read_skill(root, "dev")
    except SkillError:
        pass
    else:
        raise AssertionError("研究 Agent 不应读取 development Skill")
    assert read_skill(root, "dev", include_development=True)["content"].startswith("---")

    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    ctx = ToolContext(domain=dom, root=root)
    reg = default_registry()
    listed = reg.call(ctx, "skills_list", {})
    assert [s["name"] for s in listed["skills"]] == ["demo"]
    blocked = reg.call(ctx, "skill_read", {"name": "dev"})
    assert blocked["status"] == "error"
