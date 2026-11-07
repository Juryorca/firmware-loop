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
    assert first["parsed"] == 8 and first["inserted"] == 8
    st = stats(con)
    assert st["total"] == 8
    assert st["by_status"] == {"approved": 8}
    second = migrate_seed(root, con)
    assert second["inserted"] == 0 and second["skipped"] == 8
    assert stats(con)["total"] == 8


def test_search_chinese_english_identifier(tmp_path):
    root, con = _root(tmp_path)
    migrate_seed(root, con)
    assert search(con, "认证")
    assert search(con, "owner")
    assert search(con, "X-Forwarded-For")
    assert search(con, "memcpy")
    assert search(con, "误报")
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
