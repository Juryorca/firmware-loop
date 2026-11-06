#!/usr/bin/env python3
"""一次性把开发期旧库升级到**当前基线**(先备份,保留全部数据)。

这是给"确实要保留的研究工作区"用的离线脚本,不是运行时的兼容层:
- 复制 `<db>` → `<db>.bak-<时间戳>`(含 -wal/-shm)
- 按 `fwcore.db.SCHEMA` 建缺失表、按 `BASELINE_COLUMNS` 补缺失列
- 写回 schema_version = 当前基线,重建索引
- 打印关键表行数(迁移前后一致才算成功)

用法:
  python3 tools/db_rebaseline.py state/<目标>/state/fwcore.sqlite3 [更多 db...]
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fwcore.db import BASELINE_COLUMNS, INDEXES, SCHEMA, SCHEMA_VERSION

TABLES = ("targets", "surfaces", "hypotheses", "attempts", "observations",
          "evidence", "evidence_checks", "conclusions", "tool_calls", "runs",
          "checkpoints", "events", "documents", "mirror_checks", "research_logs")


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in TABLES:                      # 表名来自本文件的固定白名单,不含外部输入
        if t not in TABLES:
            raise ValueError(f"非法表名: {t!r}")
        try:
            # 表名来自本文件固定白名单(TABLES),不含外部输入

            out[t] = con.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()[0]  # nosemgrep: python.lang.security.audit.formatted-sql-query
        except sqlite3.OperationalError:
            out[t] = -1
    return out


def rebaseline(db: str) -> int:
    if not os.path.isfile(db):
        print(f"[skip] 不存在: {db}")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{db}.bak-{stamp}"
    # 用 SQLite 在线备份 API 做**一致性快照**(直接拷 db/-wal/-shm 在写入时可能不一致)
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(backup)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    print(f"[backup] {backup}(sqlite backup API 一致性快照)")

    con = sqlite3.connect(db, isolation_level=None)
    con.row_factory = sqlite3.Row
    before = counts(con)
    have = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    print(f"[info] {db}: schema_version={have['value'] if have else 'none'}, 目标={SCHEMA_VERSION}")

    con.executescript(SCHEMA)
    for table, cols in BASELINE_COLUMNS.items():
        if table not in TABLES:
            raise ValueError(f"BASELINE_COLUMNS 里有未知表: {table!r}")
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}  # nosemgrep: python.lang.security.audit.formatted-sql-query
        for col, decl in cols:
            if not col.replace("_", "").isalnum():
                raise ValueError(f"非法列名: {col!r}")
            if col not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")  # nosemgrep: python.lang.security.audit.formatted-sql-query
                print(f"[add] {table}.{col}")
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),))
    con.executescript(INDEXES)
    after = counts(con)
    bad = [t for t in TABLES if before.get(t, 0) >= 0 and before[t] != after[t]]
    print("[counts]", " ".join(f"{t}={after[t]}" for t in TABLES if after[t]))

    problems: list[str] = []
    if bad:
        problems.append(f"行数变化: {bad}")
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        problems.append(f"外键不成立 {len(fk)} 条(示例: {tuple(fk[0])[:3]})")
    # 关键关联:证据必须挂到存在的目标/尝试;尝试必须挂到存在的假设;run 必须挂到存在的目标
    for sql, label in (
            (("SELECT COUNT(*) FROM evidence e LEFT JOIN targets t ON t.id=e.target_id "
              "WHERE t.id IS NULL"), "证据指向不存在的目标"),
            (("SELECT COUNT(*) FROM attempts a LEFT JOIN hypotheses h ON h.id=a.hypothesis_id "
              "WHERE h.id IS NULL"), "尝试指向不存在的假设"),
            (("SELECT COUNT(*) FROM runs r LEFT JOIN targets t ON t.id=r.target_id "
              "WHERE t.id IS NULL"), "run 指向不存在的目标"),
            (("SELECT COUNT(*) FROM evidence_checks c LEFT JOIN evidence e ON e.id=c.evidence_id "
              "WHERE e.id IS NULL"), "复核记录指向不存在的证据")):
        n = con.execute(sql).fetchone()[0]  # nosemgrep: python.lang.security.audit.formatted-sql-query
        if n:
            problems.append(f"{label}: {n} 条")
    con.close()
    if problems:
        print("[FAIL] " + "; ".join(problems), file=sys.stderr)
        print(f"       备份仍在: {backup}", file=sys.stderr)
        return 2
    print("[ok] 完整性/外键/关键关联校验通过,行数一致")
    return 0


def main() -> int:
    dbs = sys.argv[1:]
    if not dbs:
        print(__doc__)
        return 2
    rc = 0
    for db in dbs:
        rc = max(rc, rebaseline(os.path.abspath(db)))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
