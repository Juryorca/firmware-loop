"""Research Knowledge:curated seed、FTS、版本、提案/审核边界。"""
from __future__ import annotations

import os
import shutil

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.knowledge import get, migrate_seed, propose, revise, search, stats
from fwcore.tools import ToolContext, default_registry


def _root(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "knowledge"), exist_ok=True)
    shutil.copy("knowledge/research-knowledge.md",
                os.path.join(root, "knowledge", "research-knowledge.md"))
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    return root, con


def test_migrate_seed_is_idempotent_and_minimal(tmp_path):
    root, con = _root(tmp_path)
    first = migrate_seed(root, con)
    assert first["parsed"] == 14 and first["inserted"] == 14
    st = stats(con)
    assert st["total"] == 14
    assert st["by_status"] == {"approved": 14}
    second = migrate_seed(root, con)
    assert second["inserted"] == 0 and second["skipped"] == 14
    assert stats(con)["total"] == 14


def test_search_chinese_english_identifier(tmp_path):
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    assert search(con, "认证")
    assert search(con, "owner")
    assert search(con, "X-Forwarded-For")
    assert search(con, "memcpy")
    assert search(con, "误报")
    assert search(con, "qemu")
    assert search(con, "unblob")
    hit = search(con, "owner")[0]
    assert hit["kind"] == "pattern" and hit["title"]
    body = get(con, hit["id"])
    assert body and "适用" in body["body_md"] and "owner" in body["body_md"]


def test_propose_records_directly_as_approved(tmp_path):
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    e = propose(con, kind="guideline", title="测试新知识",
                body_md="适合跨目标复用的判断经验。", tags=["test"])
    assert e["status"] == "approved"
    assert any(v["version"] == 1 for v in e["versions"])
    loaded = get(con, e["id"])
    assert loaded is not None and loaded["status"] == "approved"
    try:
        propose(con, kind="guideline", title="测试新知识",
                body_md="适合跨目标复用的判断经验。", tags=["test"])
    except ValueError:
        pass
    else:
        raise AssertionError("重复提案应报错")


def test_tools_search_get_propose(tmp_path):
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    dom = Domain(con, root)
    ctx = ToolContext(domain=dom, root=root)
    reg = default_registry()
    s = reg.call(ctx, "knowledge_search", {"query": "owner"})
    assert s["status"] == "ok" and s["count"] >= 1
    g = reg.call(ctx, "knowledge_get", {"entry_id": s["results"][0]["id"]})
    assert g["status"] == "ok" and g["entry"]["body_md"]
    p = reg.call(ctx, "knowledge_propose", {
        "kind": "guideline", "title": "工具提案测试", "body_md": "可复用经验。",
        "tags": ["test"]})
    assert p["status"] == "ok" and p["entry"]["status"] == "approved"
    stats_out = reg.call(ctx, "knowledge_stats", {})
    assert stats_out["status"] == "ok" and stats_out["stats"]["total"] == 15
    rev = reg.call(ctx, "knowledge_revise", {
        "entry_id": p["entry"]["id"], "body_md": "可复用经验 v2", "change_note": "test"})
    assert rev["status"] == "ok" and rev["entry"]["status"] == "approved"
    dep = reg.call(ctx, "knowledge_deprecate", {"entry_id": p["entry"]["id"]})
    assert dep["status"] == "ok" and dep["entry"]["status"] == "deprecated"


def test_revision_keeps_history_and_requires_review(tmp_path):
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    hit = search(con, "认证")[0]
    revised = revise(con, hit["id"], body_md="新的知识正文", change_note="更新判据")
    assert revised["status"] == "approved"
    versions = revised["versions"]
    assert len(versions) == 2
    assert versions[-1]["body_md"] == "新的知识正文"
    assert versions[0]["body_md"] != versions[-1]["body_md"]


def test_migrate_tool_quirks_merges_and_is_idempotent(tmp_path):
    from fwcore.knowledge import migrate_tool_quirks
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    src = os.path.join(root, "knowledge", "tool-quirks.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write("""# 工具怪癖与解法

## 本机环境 | 禁止 conda 命令;Python 一律 pwn env 绝对路径 | (2026-09/用户规则)
旧规则。

## 工具 A | A 的坑 | (2026-09)
正文 A。

## ida-pro-mcp 无头 | B 的坑 | (2026-09)
正文 B。

## 工具 C | C 的坑 | (2026-09)
正文 C。

## 工具 D | D 的坑 | (2026-09)
正文 D。

## qemu-user 直调 CGI | 补齐 httpd 注入 env | (2026-09/DIR850L 实测)
完整正文。
""")
    first = migrate_tool_quirks(root, con)
    assert first["parsed"] == 6
    assert first["merged"] == 2
    assert first["inserted"] == 4
    assert first["retired"] == 1
    by_idx = {e["index"]: e for e in first["entries"]}
    assert by_idx[3]["action"] == "merge"
    assert by_idx[6]["action"] == "merge"
    assert by_idx[2]["status"] == "deprecated"
    merged = get(con, by_idx[6]["id"])
    assert merged and "补齐 httpd 注入 env" in merged["body_md"]
    assert "tool-quirk" in merged["tags"]
    second = migrate_tool_quirks(root, con)
    assert second["inserted"] == 0 and second["merged"] == 0
    assert second["skipped"] == 6
    assert stats(con)["total"] == 18


def test_tool_quirks_source_is_gone_and_report_has_94_rows():
    import re
    assert not os.path.exists("knowledge/tool-quirks.md")
    with open("docs/tool-quirks-migration.md", encoding="utf-8") as f:
        text = f.read()
    destinations = text.split("## 94 条逐条去向", 1)[1]
    rows = re.findall(r"(?m)^\|\s*\d+\s*\|\s*`KN-[0-9a-f]+`\s*\|", destinations)
    assert len(rows) == 94


def test_current_tool_quirks_are_searchable_after_migration(tmp_path):
    """本机权威 DB 的迁移验收；没有 DB 的干净环境跳过。"""
    import sqlite3
    db = "state/fwcore.sqlite3"
    if not os.path.isfile(db):
        return
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        n = con.execute("SELECT count(*) FROM knowledge_entries WHERE source=?",
                        ("knowledge/tool-quirks.md",)).fetchone()[0]
        assert n == 94
        assert con.execute("SELECT count(*) FROM knowledge_entries WHERE status='deprecated'"
                           ).fetchone()[0] >= 3
        for q in ("qemu-user", "ida", "afl", "unblob", "截图", "代理"):
            assert search(con, q), q
    finally:
        con.close()
