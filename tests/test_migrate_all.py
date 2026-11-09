"""批量迁移:结构化抽取、幂等、复核,以及"运行时不读 Markdown"的边界。"""

from __future__ import annotations

import os

from fwcore import db as _db
from fwcore.migrate_all import migrate_all, migrate_target, verify_target

NOTES = """# t — notes

## 立项 (2026-01-02)
型号 t,版本 1.2.3。

## 攻击面 (A1)
| 入口 | disposition |
|---|---|
| `/a` | No finding |
| `/b` | 未判定 |

## 假设池 (= 唯一队列)
- **H-01** `/a` 越权读
  偏差陈述: 认证后应校验对象属主,实测未校验。
  状态: **成立**(VR-01)
- **H-02** `/b` 的口令门
  状态: 部分完成

三镜: 机制=同机制复发 | 作者=漏了对象属主 | 状态=换主体

### 停止声明(轮 1)
本轮停止: 本轮新成立 finding —— VR-01。

## 下一步
- 先打 `/b`。
"""


def _mk(tmp_path):
    root = str(tmp_path)
    tdir = os.path.join(root, "state", "t")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(NOTES)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    return root, con


def test_migrate_extracts_structured_facts(tmp_path):
    root, con = _mk(tmp_path)
    out = migrate_target(con, root, "t")
    s = out["stats"]
    assert s["target"] == 1
    assert s["surfaces"] == 2 and s["hypotheses"] == 2 and s["conclusions"] == 1
    assert s["mirror_checks"] == 1 and s["logs"] >= 2 and s["documents"] == 1
    tid = con.execute("SELECT id FROM targets WHERE name='t'").fetchone()["id"]
    assert con.execute("SELECT COUNT(*) n FROM surfaces WHERE target_id=?", (tid,)).fetchone()["n"] == 2
    st = {r["state"]: r["n"] for r in con.execute(
        "SELECT state, COUNT(*) n FROM hypotheses WHERE target_id=? GROUP BY state", (tid,))}
    assert st == {"concluded": 1, "open": 1}
    assert con.execute("SELECT verdict FROM conclusions").fetchone()["verdict"] == "vulnerable"
    # 模糊状态(H-02 部分完成)必须留 needs_review,不能猜结论
    assert con.execute("SELECT COUNT(*) n FROM research_logs WHERE kind='needs_review'").fetchone()["n"] >= 1
    assert verify_target(con, root, "t")["ok"] is True


def test_migrate_is_idempotent(tmp_path):
    root, con = _mk(tmp_path)
    migrate_target(con, root, "t")
    again = migrate_target(con, root, "t")
    assert sum(v for k, v in again["stats"].items() if k != "target") == 0
    # 全量入口同样幂等
    total = migrate_all(root)["total"]
    assert all(v == 0 for v in total.values()), total


def test_migrate_all_skips_scratch(tmp_path):
    root, _con = _mk(tmp_path)
    os.makedirs(os.path.join(root, "state", "_scratch", "x"), exist_ok=True)
    out = migrate_all(root)
    assert out["targets"] == 1 and out["results"][0]["target"] == "t"


def test_reset_then_remigrate_after_notes_edit(tmp_path):
    """改 notes 后:reset 清迁移行再重导 ⇒ 不产生重复行(不能靠内容 id 硬碰)。"""
    from fwcore.migrate_all import reset_migrated

    root, con = _mk(tmp_path)
    migrate_target(con, root, "t")
    with open(os.path.join(root, "state", "t", "notes.md"), "a", encoding="utf-8") as f:
        f.write("\n- **H-03** 新条目\n  状态: 未处理\n")
    reset_migrated(con, root, only=["t"])
    out = migrate_target(con, root, "t")
    assert out["stats"]["hypotheses"] == 3, out["stats"]
    again = migrate_target(con, root, "t")
    assert all(v == 0 for k, v in again["stats"].items() if k != "target"), again["stats"]
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 3


def test_evidence_paths_keep_only_material_files(tmp_path):
    """目录/命令行/花括号/越界路径都不算证据文件(原文仍在 notes/documents)。"""
    from fwcore.migrate_all import parse_evidence

    root, _con = _mk(tmp_path)
    tdir = os.path.join(root, "state", "t")
    os.makedirs(os.path.join(tdir, "artifacts"), exist_ok=True)
    with open(os.path.join(tdir, "artifacts", "poc.py"), "w", encoding="utf-8") as f:
        f.write("x")
    notes = ("证据: `artifacts/poc.py`\n"
             "证据: `rootfs/`\n"
             "证据: `bash artifacts/x.sh`\n"
             "证据: `artifacts/{a,b}.sh`\n"
             "证据: `../../../../etc/passwd.txt`\n")
    ev = parse_evidence(notes, tdir)
    assert [e["path"] for e in ev] == ["artifacts/poc.py"]


def test_verify_flags_evidence_path_escape(tmp_path):
    """证据路径逃出 state/ ⇒ verify 必须报错(解析器回归保护)。"""
    root, con = _mk(tmp_path)
    migrate_target(con, root, "t")
    tid = con.execute("SELECT id FROM targets WHERE name='t'").fetchone()["id"]
    with _db.Tx(con) as c:
        c.execute("INSERT INTO evidence(id,target_id,kind,path,verified,created_at)"
                  " VALUES('E-x',?,'file','../../outside.txt',0,'2026-01-01')", (tid,))
    out = verify_target(con, root, "t")
    assert out["path_escapes"] == 1 and out["ok"] is False


def test_pool_restatement_is_not_a_new_hypothesis():
    """`- **H-11 的最终口径…**` 是对既有假设的状态重述 ⇒ 不建新假设,只记 needs_review 冲突。"""
    from fwcore.migrate_all import parse_pool

    text = ("## 假设池\n"
            "- **H-11** 日志产者只来自 CGI 面\n  状态: 未处理\n"
            "- **H-11 的最终口径(本轮定)**: 8/8 死导入\n"
            "- **H-11 定论(负)**: 无证据\n")
    pool, conflicts = parse_pool(text)
    assert [e["raw_id"] for e in pool] == ["H-11"]
    assert sum(1 for c in conflicts if "状态重述" in c) == 2


def test_pool_keeps_parenthetical_title():
    """`H-12(长标题)` 的括号是标题,不能被当优先级标记剥掉。"""
    from fwcore.migrate_all import parse_pool

    text = "## 假设池\n- **H-12(控制台 pre-auth 认证链 `/api/auth/*`)**: 说明\n"
    pool, _ = parse_pool(text)
    assert pool and "控制台" in pool[0]["title"]


def test_connect_adds_target_status_to_legacy_schema_17_db(tmp_path):
    """旧库可能已有 schema_version=17 但 targets 还没有 status 列。"""
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES('schema_version','17');"
        "CREATE TABLE targets(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
        " created_at TEXT NOT NULL);"
        "INSERT INTO targets VALUES('T-1','legacy','2026-01-01');"
    )
    con.close()
    con = _db.connect(str(path))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(targets)")}
        assert "status" in cols
        assert con.execute("SELECT status FROM targets WHERE name='legacy'").fetchone()[0] == "active"
    finally:
        con.close()
