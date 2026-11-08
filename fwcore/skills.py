"""渐进式 Skills:只读 `.agents/skills/`，发现目录 + 按需加载正文/引用。

边界
- 不扫描任意路径；所有路径 realpath 后必须仍在 `.agents/skills/<name>/` 内。
- Skill 不是 Agent，不创建子代理；只是操作知识。
- catalog 默认返回全部 Skill 的 name/description/audience；正文由 `skill_read` 按需读取。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TEXT_EXT = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".sh", ".py"}
MAX_BYTES = 512 * 1024


class SkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    directory: str
    path: str
    audience: str = "research"


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise SkillError("SKILL.md 缺少 frontmatter")
    lines = text.splitlines()
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        raise SkillError("SKILL.md frontmatter 未闭合")
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def _inside(path: str, root: str) -> bool:
    try:
        p = os.path.realpath(path)
        r = os.path.realpath(root)
        return os.path.commonpath([p, r]) == r
    except (OSError, ValueError):
        return False


def discover_skills(root: str, *, strict: bool = False) -> list[Skill]:
    base = os.path.join(root, ".agents", "skills")
    out: list[Skill] = []
    errors: list[str] = []
    if not os.path.isdir(base):
        return out
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        d = os.path.join(base, name)
        path = os.path.join(d, "SKILL.md")
        if os.path.islink(d) or not os.path.isdir(d) or not os.path.isfile(path):
            continue
        if os.path.islink(path) or not _inside(path, base):
            errors.append(f"{name}: symlink/越界路径")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:  # nosemgrep: python-path-traversal
                text = f.read()
            meta = _frontmatter(text)
        except (OSError, SkillError) as e:
            errors.append(f"{name}: {e}")
            continue
        sname = (meta.get("name") or name).strip()
        desc = (meta.get("description") or "").strip()
        if not NAME_RE.match(sname):
            errors.append(f"{name}: 非法 name {sname!r}")
            continue
        if not desc:
            errors.append(f"{name}: 缺少 description")
            continue
        audience = (meta.get("audience") or "research").strip()
        if _inside(path, base):
            out.append(Skill(sname, desc[:500], d, path, audience))
    names = [s.name for s in out]
    if len(names) != len(set(names)):
        errors.append("Skill name 重复")
    if errors and strict:
        raise SkillError("; ".join(errors))
    return sorted(out, key=lambda s: s.name)


def catalog(root: str) -> list[dict]:
    return [{"name": s.name, "description": s.description, "audience": s.audience}
            for s in discover_skills(root)]


def read_skill(root: str, name: str, reference: str | None = None) -> dict:
    if not NAME_RE.match(name or ""):
        raise SkillError("非法 Skill 名称")
    matches = [s for s in discover_skills(root) if s.name == name]
    if not matches:
        raise SkillError(f"未找到 Skill: {name}")
    s = matches[0]
    target = s.path if not reference else os.path.join(s.directory, reference)
    if not _inside(target, s.directory):
        raise SkillError("拒绝路径穿越: reference 必须位于该 Skill 目录内")
    if os.path.islink(target):
        raise SkillError("拒绝读取 symlink")
    if not os.path.isfile(target):
        raise SkillError(f"引用不存在: {reference}")
    if os.path.splitext(target)[1].lower() not in TEXT_EXT:
        raise SkillError("只允许读取文本类 Skill 引用")
    if os.path.getsize(target) > MAX_BYTES:
        raise SkillError("Skill 文件超过 512KB；请拆分 references")
    try:
        with open(target, encoding="utf-8", errors="replace") as f:  # nosemgrep: python-path-traversal
            body = f.read()
    except OSError as e:
        raise SkillError(f"Skill 引用读取失败: {e}") from e
    return {"name": s.name, "description": s.description,
            "reference": reference or "SKILL.md", "content": body,
            "path": os.path.relpath(target, root).replace(os.sep, "/")}


def register_tools(reg, root: str) -> None:
    """把 Skill 发现/读取注册为真实工具；不把正文塞进 system。"""
    from .tools import ToolSpec

    def _list(ctx, args):
        return {"status": "ok", "skills": catalog(ctx.root or root)}

    def _read(ctx, args):
        try:
            return {"status": "ok", "skill": read_skill(ctx.root or root,
                                                        str(args.get("name") or ""),
                                                        args.get("reference"))}
        except SkillError as e:
            return {"status": "error", "error": str(e)}

    reg.register(ToolSpec(
        "skills_list", "列出可用 Skills(仅名称/描述；正文用 skill_read 按需加载)",
        {"type": "object", "properties": {}}, _list))
    reg.register(ToolSpec(
        "skill_read", "读取某个 Skill 的完整 SKILL.md 或 references/ 下的文本引用；"
        "路径被限制在该 Skill 目录内",
        {"type": "object", "properties": {
            "name": {"type": "string"},
            "reference": {"type": "string", "description": "如 references/loop.md"}},
         "required": ["name"]}, _read))
