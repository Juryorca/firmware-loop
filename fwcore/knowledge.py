"""Research Knowledge(跨目标、可复用、可审核/版本化)。

知识优先:
- `body_md` 是完整、自然的正文;结构化字段只保留 kind/status/tags/source 等最小检索信息。
- 不按“一个案例一行”存储;只收纳能缩短未来分析路径、避免踩坑、判断成立/排除条件的经验。
- 目标专属地址、复现记录、当前目标的证据留在 research state / 原始材料,不进入这里。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime

from . import db as _db
from .db import Tx

KINDS = ("pattern", "misreport", "guideline")
STATUSES = ("approved", "deprecated", "superseded")  # Agent 直接记录；不设中间审核态
SEED_FILE = "knowledge/research-knowledge.md"
HEADING = re.compile(r"(?m)^## (.+)$")
TAG_WORDS = {
    "authz": ("认证", "授权", "owner", "权限", "IDOR", "对象"),
    "proxy": ("代理", "转发", "Host", "XFF", "拓扑", "中继"),
    "injection": ("注入", "shell", "命令", "参数", "转义"),
    "memory": ("内存", "溢出", "解析", "长度", "边界", "memcpy", "XML"),
    "url": ("URL", "路径", "重定向", "Host"),
    "dos": ("速率", "限流", "拒绝服务", "DoS", "锁定", "资源"),
    "false-positive": ("误报", "反例", "判据", "排除", "设计内"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _stable_id(kind: str, title: str) -> str:
    h = hashlib.sha256(f"{kind}|{title}".encode("utf-8", "replace")).hexdigest()[:14]
    return f"KN-{h}"


def parse_seed(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    marks = list(HEADING.finditer(text))
    out: list[dict] = []
    for i, m in enumerate(marks):
        title = m.group(1).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.start():end].strip()
        if not title or not body:
            continue
        kind = "misreport" if ("误报" in title or "反例" in title) else "pattern"
        if "研究" in title and "知识" in title:
            kind = "guideline"
        if title.startswith(("工具判据", "环境判据")):
            kind = "guideline"
        tags = {"curated", kind}
        for tag, words in TAG_WORDS.items():
            if any(w.lower() in (title + "\n" + body).lower() for w in words):
                tags.add(tag)
        out.append({"kind": kind, "title": title, "body_md": body,
                    "source": path.replace(os.sep, "/"), "source_hash": _sha(body),
                    "tags": sorted(tags)})
    return out


def _fts_upsert(con: sqlite3.Connection, entry_id: str, title: str, body: str,
                tags: list[str]) -> None:
    con.execute("DELETE FROM knowledge_entry_fts WHERE entry_id=?", (entry_id,))
    con.execute(
        "INSERT INTO knowledge_entry_fts(entry_id,title,body_md,tags) VALUES(?,?,?,?)",
        (entry_id, title, body, " ".join(tags)),
    )


def _insert_version(con: sqlite3.Connection, entry_id: str, version: int, title: str,
                    body: str, note: str) -> None:
    con.execute(
        "INSERT INTO knowledge_entry_versions(id,entry_id,version,title,body_md,change_note,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (f"KNV-{hashlib.sha256(f'{entry_id}|{version}'.encode()).hexdigest()[:16]}",
         entry_id, version, title, body, note, _now()),
    )


def migrate_seed(root: str, con: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """把 curated seed 导入知识库;幂等,内容变化才产生新版本。

    seed 由 Agent 在提炼时判断为可复用知识,直接是 approved;不设人工/中间审核态。
    """
    path = os.path.join(root, SEED_FILE)
    entries = parse_seed(path)
    stats = {"seed": SEED_FILE, "parsed": len(entries), "inserted": 0,
             "updated": 0, "skipped": 0, "versions": 0,
             "dry_run": bool(dry_run), "entries": []}
    for e in entries:
        eid = _stable_id(e["kind"], e["title"])
        e["id"] = eid
        e["source"] = SEED_FILE
        stats["entries"].append({"id": eid, "title": e["title"], "kind": e["kind"]})
    if dry_run:
        return stats
    for e in entries:
        old = con.execute("SELECT * FROM knowledge_entries WHERE id=?", (e["id"],)).fetchone()
        if old and old["source_hash"] == e["source_hash"]:
            # 旧库若还是 needs_review/draft,迁移时按 Agent 直接记录语义收敛到 approved
            if old["status"] != "approved":
                with Tx(con) as tx:
                    tx.execute("UPDATE knowledge_entries SET status='approved',updated_at=?"
                               " WHERE id=?", (_now(), e["id"]))
            stats["skipped"] += 1
            continue
        with Tx(con) as tx:
            now = _now()
            if old:
                ver = (tx.execute("SELECT COALESCE(MAX(version),0) v FROM knowledge_entry_versions"
                                  " WHERE entry_id=?", (e["id"],)).fetchone()["v"] + 1)
                tx.execute("UPDATE knowledge_entries SET title=?,body_md=?,source=?,source_hash=?,"
                           "updated_at=? WHERE id=?",
                           (e["title"], e["body_md"], e["source"], e["source_hash"], now, e["id"]))
                stats["updated"] += 1
            else:
                ver = 1
                tx.execute(
                    "INSERT INTO knowledge_entries(id,title,body_md,kind,status,source,source_hash,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (e["id"], e["title"], e["body_md"], e["kind"], "approved",
                     e["source"], e["source_hash"], now, now),
                )
                stats["inserted"] += 1
            _insert_version(tx, e["id"], ver, e["title"], e["body_md"],
                            "curated seed import (agent-approved)")
            stats["versions"] += 1
            tx.execute("DELETE FROM knowledge_entry_tags WHERE entry_id=?", (e["id"],))
            for tag in e["tags"]:
                tx.execute("INSERT OR IGNORE INTO knowledge_entry_tags(entry_id,tag) VALUES(?,?)",
                           (e["id"], tag[:80]))
            _fts_upsert(tx, e["id"], e["title"], e["body_md"], e["tags"])
    return stats


def _row(con: sqlite3.Connection, entry_id: str) -> dict | None:
    r = con.execute("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(r) if r else None


def search(con: sqlite3.Connection, query: str, *, kind: str | None = None,
           status: str | None = None, limit: int = 20) -> list[dict]:
    """FTS5 优先,失败/无结果时退回 LIKE;返回知识摘要而非整篇正文。"""
    q = (query or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    if not q:
        return []
    if kind and kind not in KINDS:
        raise ValueError(f"未知 kind: {kind}")
    if status and status not in STATUSES:
        raise ValueError(f"未知 status: {status}")
    fts_q = '"' + q.replace('"', '""') + '"'
    try:
        rows = con.execute(
            "SELECT e.id,e.title,e.kind,e.status,e.source,e.updated_at,"
            " substr(e.body_md,1,900) AS excerpt FROM knowledge_entry_fts f"
            " JOIN knowledge_entries e ON e.id=f.entry_id"
            " WHERE knowledge_entry_fts MATCH ?"
            " AND (? IS NULL OR e.kind=?) AND (? IS NULL OR e.status=?)"
            " ORDER BY rank LIMIT ?",
            (fts_q, kind, kind, status, status, limit)).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass
    like = "%" + q + "%"
    rows = con.execute(
        "SELECT e.id,e.title,e.kind,e.status,e.source,e.updated_at,"
        " substr(e.body_md,1,900) AS excerpt FROM knowledge_entries e"
        " WHERE (e.title LIKE ? OR e.body_md LIKE ?)"
        " AND (? IS NULL OR e.kind=?) AND (? IS NULL OR e.status=?)"
        " ORDER BY e.updated_at DESC LIMIT ?",
        (like, like, kind, kind, status, status, limit)).fetchall()
    return [dict(r) for r in rows]


def get(con: sqlite3.Connection, entry_id: str) -> dict | None:
    e = _row(con, entry_id)
    if not e:
        return None
    e["tags"] = [r["tag"] for r in con.execute(
        "SELECT tag FROM knowledge_entry_tags WHERE entry_id=? ORDER BY tag", (entry_id,)).fetchall()]
    e["versions"] = [dict(r) for r in con.execute(
        "SELECT id,entry_id,version,title,body_md,change_note,created_at FROM knowledge_entry_versions"
        " WHERE entry_id=? ORDER BY version", (entry_id,)).fetchall()]
    return e


def propose(con: sqlite3.Connection, *, kind: str, title: str, body_md: str,
            tags: list[str] | None = None, source: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"未知 kind: {kind}")
    title, body_md = (title or "").strip(), (body_md or "").strip()
    if not title or not body_md:
        raise ValueError("title/body_md 不能为空")
    eid = _stable_id(kind, title)
    if _row(con, eid):
        raise ValueError(f"知识条目已存在: {eid}")
    tag_list: list[str] = sorted({"proposed", kind, *(tags or [])})
    now = _now()
    with Tx(con) as tx:
        tx.execute(
            "INSERT INTO knowledge_entries(id,title,body_md,kind,status,source,source_hash,"
            "created_at,updated_at) VALUES(?,?,?,?,'approved',?,?,?,?)",
            (eid, title, body_md, kind, source, _sha(body_md), now, now),
        )
        _insert_version(tx, eid, 1, title, body_md, "agent-recorded")
        for tag in tag_list:
            tx.execute("INSERT OR IGNORE INTO knowledge_entry_tags(entry_id,tag) VALUES(?,?)",
                       (eid, tag[:80]))
        _fts_upsert(tx, eid, title, body_md, tag_list)
    out = get(con, eid)
    if not out:
        raise RuntimeError(f"知识条目写入后读取失败: {eid}")
    return out


def revise(con: sqlite3.Connection, entry_id: str, *, title: str | None = None,
           body_md: str | None = None, change_note: str = "",
           status: str | None = None) -> dict:
    """修订知识正文/标题:追加新版本,不覆盖历史;Agent 直接判断仍可复用则 approved。"""
    old = _row(con, entry_id)
    if not old:
        raise KeyError(entry_id)
    new_title = (title if title is not None else old["title"]).strip()
    new_body = (body_md if body_md is not None else old["body_md"]).strip()
    if not new_title or not new_body:
        raise ValueError("title/body_md 不能为空")
    new_status = status or "approved"
    if new_status not in STATUSES:
        raise ValueError(f"未知 status: {new_status}")
    with Tx(con) as tx:
        ver = (tx.execute("SELECT COALESCE(MAX(version),0) v FROM knowledge_entry_versions"
                          " WHERE entry_id=?", (entry_id,)).fetchone()["v"] + 1)
        tx.execute("UPDATE knowledge_entries SET title=?,body_md=?,source_hash=?,status=?,updated_at=?"
                   " WHERE id=?",
                   (new_title, new_body, _sha(new_body), new_status, _now(), entry_id))
        _insert_version(tx, entry_id, ver, new_title, new_body,
                        change_note or "manual revision")
        tags = [r["tag"] for r in tx.execute(
            "SELECT tag FROM knowledge_entry_tags WHERE entry_id=?", (entry_id,)).fetchall()]
        _fts_upsert(tx, entry_id, new_title, new_body, tags)
    out = get(con, entry_id)
    if not out:
        raise RuntimeError(f"知识条目不存在: {entry_id}")
    return out


def set_status(con: sqlite3.Connection, entry_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"未知 status: {status}")
    if not _row(con, entry_id):
        raise KeyError(entry_id)
    with Tx(con) as tx:
        tx.execute("UPDATE knowledge_entries SET status=?,updated_at=? WHERE id=?",
                   (status, _now(), entry_id))
    out = get(con, entry_id)
    if not out:
        raise RuntimeError(f"知识条目不存在: {entry_id}")
    return out


def stats(con: sqlite3.Connection) -> dict:
    out = {"total": 0, "by_kind": {}, "by_status": {}}
    for r in con.execute("SELECT kind,status,COUNT(*) n FROM knowledge_entries"
                         " GROUP BY kind,status").fetchall():
        out["total"] += r["n"]
        out["by_kind"][r["kind"]] = out["by_kind"].get(r["kind"], 0) + r["n"]
        out["by_status"][r["status"]] = out["by_status"].get(r["status"], 0) + r["n"]
    return out


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fwcore.knowledge")
    ap.add_argument("--root", default=os.environ.get("FW_ROOT", os.getcwd()))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("migrate")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("get")
    p.add_argument("entry_id")
    p = sub.add_parser("revise")
    p.add_argument("entry_id")
    p.add_argument("--title", default=None)
    p.add_argument("--body-file", default=None)
    p.add_argument("--note", default="")
    p = sub.add_parser("stats")
    p = sub.add_parser("deprecate")
    p.add_argument("entry_id")
    a = ap.parse_args(argv)
    con = _db.connect(os.path.join(a.root, "state", "fwcore.sqlite3"))
    try:
        if a.cmd == "migrate":
            print(json.dumps(migrate_seed(a.root, con, dry_run=a.dry_run),
                             ensure_ascii=False, indent=2))
        elif a.cmd == "search":
            print(json.dumps(search(con, a.query, kind=a.kind, status=a.status,
                                    limit=a.limit), ensure_ascii=False, indent=2))
        elif a.cmd == "get":
            print(json.dumps(get(con, a.entry_id), ensure_ascii=False, indent=2))
        elif a.cmd == "revise":
            body = None
            if a.body_file:
                with open(a.body_file, encoding="utf-8") as f:
                    body = f.read()
            print(json.dumps(revise(con, a.entry_id, title=a.title, body_md=body,
                                    change_note=a.note), ensure_ascii=False, indent=2))
        elif a.cmd == "stats":
            print(json.dumps(stats(con), ensure_ascii=False, indent=2))
        elif a.cmd == "deprecate":
            print(json.dumps(set_status(con, a.entry_id, "deprecated"),
                             ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
