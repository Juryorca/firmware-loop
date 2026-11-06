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
