"""Research Knowledge：SQLite 检索、版本、提案/审核边界。"""
from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.knowledge import get, propose, revise, search, stats
from fwcore.tools import ToolContext, default_registry


def _root(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    entries = [
        ("pattern", "认证/授权：对象级 owner 与权限语义",
         "适用：对象级 owner 权限判断。owner 不等于知道 id。", ["authz"]),
        ("pattern", "认证/授权：代理、转发头与默认拓扑",
         "适用：X-Forwarded-For 等代理头的信任边界。", ["proxy"]),
        ("pattern", "命令与参数注入：从输入到 shell 的边界",
         "适用：命令注入先证明参数可控与 sink。", ["injection"]),
        ("pattern", "解析器与内存安全：长度、边界与部分覆盖",
         "适用：memcpy 长度与边界算术。", ["memory"]),
        ("pattern", "路径、URL 与 Host 解析",
         "适用：URL 与 Host 解析差异。", ["url"]),
        ("pattern", "速率限制、锁定与拒绝服务",
         "适用：限流设计与 DoS 判据。", ["dos"]),
        ("misreport", "误报判据：能力、证据与设计内行为",
         "适用：误报与设计内行为的排除条件。", ["false-positive"]),
        ("guideline", "qemu-user 的环境边界",
         "适用：qemu-user 与 CGI 环境。", ["tool-quirk"]),
        ("guideline", "unblob 的提取环境",
         "适用：unblob 提取器缺失与重跑。", ["tool-quirk"]),
    ]
    for kind, title, body, tags in entries:
        propose(con, kind=kind, title=title, body_md=body, tags=tags)
    return root, con


def test_search_chinese_english_identifier(tmp_path):
    _root_path, con = _root(tmp_path)
    for query in ("认证", "owner", "X-Forwarded-For", "memcpy", "误报", "qemu", "unblob"):
        assert search(con, query), query
    hit = search(con, "owner")[0]
    assert hit["kind"] == "pattern" and hit["title"]
    body = get(con, hit["id"])
    assert body and "适用" in body["body_md"] and "owner" in body["body_md"]


def test_propose_records_directly_as_approved(tmp_path):
    _root_path, con = _root(tmp_path)
    before = stats(con)["total"]
    e = propose(con, kind="guideline", title="测试新知识",
                body_md="适合跨目标复用的判断经验。", tags=["test"])
    assert e["status"] == "approved"
    assert any(v["version"] == 1 for v in e["versions"])
    loaded = get(con, e["id"])
    assert loaded is not None and loaded["status"] == "approved"
    assert stats(con)["total"] == before + 1
    try:
        propose(con, kind="guideline", title="测试新知识",
                body_md="适合跨目标复用的判断经验。", tags=["test"])
    except ValueError:
        pass
    else:
        raise AssertionError("重复提案应报错")


def test_tools_search_get_propose(tmp_path):
    _root_path, con = _root(tmp_path)
    dom = Domain(con, _root_path)
    ctx = ToolContext(domain=dom, root=_root_path)
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
    assert stats_out["status"] == "ok" and stats_out["stats"]["total"] == 10
    rev = reg.call(ctx, "knowledge_revise", {
        "entry_id": p["entry"]["id"], "body_md": "可复用经验 v2", "change_note": "test"})
    assert rev["status"] == "ok" and rev["entry"]["status"] == "approved"
    dep = reg.call(ctx, "knowledge_deprecate", {"entry_id": p["entry"]["id"]})
    assert dep["status"] == "ok" and dep["entry"]["status"] == "deprecated"


def test_revision_keeps_history_and_requires_review(tmp_path):
    _root_path, con = _root(tmp_path)
    hit = search(con, "认证")[0]
    revised = revise(con, hit["id"], body_md="新的知识正文", change_note="更新判据")
    assert revised["status"] == "approved"
    versions = revised["versions"]
    assert len(versions) == 2
    assert versions[-1]["body_md"] == "新的知识正文"
    assert versions[0]["body_md"] != versions[-1]["body_md"]
