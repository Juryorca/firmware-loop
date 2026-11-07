"""一次应用会话的**共享服务**(Pi 的 AgentSessionServices 对应物)。

只组合现有 Core:数据库连接、Domain、Research、Limits、Tool Registry。
这里不实现研究逻辑,也不新建第二套状态。
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.research import Research
from fwcore.toolkit import register_repo_tools
from fwcore.tools import Registry, default_registry

from .config import AppConfig


@dataclass
class Services:
    cfg: AppConfig
    con: sqlite3.Connection
    domain: Domain
    research: Research
    registry: Registry

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.con.close()


def build_services(cfg: AppConfig) -> Services:
    con = _db.connect(cfg.db_path)
    domain = Domain(con, cfg.root)
    research = Research(cfg.root, domain)
    registry = default_registry()
    register_repo_tools(registry, cfg.root)
    return Services(cfg=cfg, con=con, domain=domain, research=research, registry=registry)


def target_names(con) -> list[str]:
    return [r["name"] for r in con.execute("SELECT name FROM targets ORDER BY name").fetchall()]


def latest_run(con, target: str) -> str | None:
    row = con.execute(
        "SELECT r.id FROM runs r JOIN targets t ON t.id=r.target_id"
        " WHERE t.name=? ORDER BY r.started_at DESC, r.rowid DESC LIMIT 1", (target,)).fetchone()
    return row["id"] if row else None


def state_store_rows(con, target: str, run_id: str | None) -> dict:
    """给 UI 的只读快照(所有字段都来自权威 SQLite)。"""
    out: dict = {"target": target, "run": None, "hypotheses": [], "observations": [],
                 "evidence": [], "blockers": [], "derived": None}
    t = con.execute("SELECT * FROM targets WHERE name=?", (target,)).fetchone()
    if not t:
        return out
    out["target_row"] = dict(t)
    out["derived"] = None
    if run_id:
        r = con.execute("SELECT * FROM runs WHERE id=? AND target_id=?", (run_id, t["id"])).fetchone()
    else:
        r = con.execute("SELECT * FROM runs WHERE target_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                        (t["id"],)).fetchone()
    out["run"] = dict(r) if r else None
    out["hypotheses"] = [dict(x) for x in con.execute(
        "SELECT id,title,state,verdict,priority,surface_key,scope_note,created_at,updated_at"
        " FROM hypotheses WHERE target_id=? ORDER BY"
        " CASE state WHEN 'open' THEN 0 WHEN 'investigating' THEN 1 ELSE 2 END,"
        " priority, created_at", (t["id"],)).fetchall()]
    out["observations"] = [dict(x) for x in con.execute(
        "SELECT id,text,created_at,path,tool_call_id FROM observations WHERE target_id=?"
        " ORDER BY created_at DESC LIMIT 100", (t["id"],)).fetchall()]
    out["evidence"] = [dict(x) for x in con.execute(
        "SELECT id,kind,path,capture,verified,version,created_at FROM evidence WHERE target_id=?"
        " ORDER BY created_at DESC LIMIT 100", (t["id"],)).fetchall()]
    out["conclusions"] = [dict(x) for x in con.execute(
        "SELECT c.id,c.hypothesis_id,c.verdict,c.scope_note,c.created_at FROM conclusions c"
        " JOIN hypotheses h ON h.id=c.hypothesis_id WHERE h.target_id=?"
        " ORDER BY c.created_at DESC LIMIT 50", (t["id"],)).fetchall()]
    out["logs"] = [dict(x) for x in con.execute(
        "SELECT kind,title,created_at FROM research_logs WHERE target_id=?"
        " ORDER BY created_at DESC LIMIT 20", (t["id"],)).fetchall()]
    return out
