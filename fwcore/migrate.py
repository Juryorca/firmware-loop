"""历史材料迁移:`notes.md` → 权威状态(可验证、可重复、不丢信息)。

做法
- 原文**逐字节**存进 `documents`(sha256 唯一键 ⇒ 重复导入是幂等的);
- 能机械确定的只做一件事:把假设池里的 `H-xx` 条目**原样**搬成假设行,并显式标 `migrated:needs_review`
  (不根据"定论/已排除"等词自动判定历史研究已完成 —— 那正是旧设计的问题);
- 其余无法机械判断的内容留在原文里,由人或模型之后核对;
- 迁移结果可通过 `verify()` 复核:原文与库内一致、条数对得上。
"""

from __future__ import annotations

import hashlib
import os
import re

from . import db as _db
from .domain import Domain, new_id


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.timezone.utc).isoformat(timespec="seconds")


RE_POOL_HEAD = re.compile(r"(?m)^(#{2,4})[^\n]*假设池[^\n]*$")
RE_ENTRY = re.compile(r"^\s*[-*]\s*\*\*H-?(\d+)\*\*(.*)$")


def extract_pool_entries(text: str) -> list[tuple[str, str, int]]:
    """返回 [(H 编号, 条目原文, 行号)];只取"唯一队列"优先的那个池块。"""
    heads = list(RE_POOL_HEAD.finditer(text))
    if not heads:
        return []
    h = ([x for x in heads if "唯一队列" in x.group(0)] or heads)[0]
    level = len(h.group(1))
    nxt = re.search(r"(?m)^#{1,%d} " % level, text[h.end():])
    block = text[h.end(): h.end() + (nxt.start() if nxt else len(text) - h.end())]
    base_line = text[: h.end()].count("\n") + 1
    out = []
    for i, ln in enumerate(block.split("\n")):
        m = RE_ENTRY.match(ln)
        if m:
            out.append((f"H-{int(m.group(1)):02d}", ln.strip(), base_line + i))
    return out


def import_notes(con, root: str, target: str, notes_path: str | None = None) -> dict:
    """把 notes.md 导入:原文入库 + 假设条目搬移(标 needs_review)。幂等。"""
    domain = Domain(con, root)
    t = domain.target(target)
    path = notes_path or os.path.join(root, "state", target, "notes.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    raw = open(path, "rb").read()
    text = raw.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw).hexdigest()
    rel = os.path.relpath(path, root)

    with _db.Tx(con) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO documents(id,target_id,path,sha256,body,imported_at) VALUES(?,?,?,?,?,?)",
            (new_id("D"), t.id, rel, sha, text, _now()),
        )
        new_doc = cur.rowcount == 1
        _c = c
        _c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                   (_now(), "migrate.documents", rel, None))

    entries = extract_pool_entries(text)
    created = 0
    new_doc = False
    for hid_num, entry_text, line_no in entries:
        title = f"[待核对] {hid_num}: {entry_text.strip('-* ')[:120]}"
        existing = con.execute(
            "SELECT id FROM hypotheses WHERE target_id=? AND title=?", (t.id, title)
        ).fetchone()
        if existing:
            continue
        with _db.Tx(con) as c:
            c.execute(
                "INSERT INTO hypotheses(id,target_id,title,body,source,priority,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,'open',?,?)",
                (new_id("H"), t.id, title, entry_text, f"migrated:needs_review {rel}:{line_no}", 100,
                 _now(), _now()),
            )
        created += 1
    return {
        "documents_added": 1 if new_doc else 0,
        "pool_entries": len(entries),
        "hypotheses_created": created,
        "sha256": sha,
        "path": rel,
    }


def verify(con, root: str, target: str, notes_path: str | None = None) -> dict:
    """复核迁移:库内原文 == 磁盘原文;假设条数与池条目数一致。"""
    domain = Domain(con, root)
    t = domain.target(target)
    path = notes_path or os.path.join(root, "state", target, "notes.md")
    raw = open(path, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    row = con.execute("SELECT body FROM documents WHERE target_id=? AND sha256=?", (t.id, sha)).fetchone()
    entries = extract_pool_entries(raw.decode("utf-8", errors="replace"))
    migrated = con.execute(
        "SELECT COUNT(*) n FROM hypotheses WHERE target_id=? AND source LIKE 'migrated:needs_review%'", (t.id,)
    ).fetchone()["n"]
    return {
        "document_present": row is not None,
        "byte_identical": row is not None and row["body"].encode("utf-8", errors="replace") == raw,
        "pool_entries": len(entries),
        "migrated_hypotheses": migrated,
        "ok": row is not None and migrated >= len(entries),
    }
