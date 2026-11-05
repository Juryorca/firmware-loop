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
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fwcore.db import BASELINE_COLUMNS, INDEXES, SCHEMA, SCHEMA_VERSION  # noqa: E402

TABLES = ("targets", "surfaces", "hypotheses", "attempts", "observations",
          "evidence", "evidence_checks", "conclusions", "tool_calls", "runs",
          "checkpoints", "events", "documents", "mirror_checks")


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in TABLES:                      # 表名来自本文件的固定白名单,不含外部输入
        if t not in TABLES:
            raise ValueError(f"非法表名: {t!r}")
        try:
            out[t] = con.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()[0]  # nosemgrep: python.lang.security.audit.formatted-sql-query
        except sqlite3.OperationalError:
            out[t] = -1
    return out


def rebaseline(db: str) -> int:
    if not os.path.isfile(db):
        print(f"[skip] 不存在: {db}")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = db + suffix
        if os.path.isfile(src):
            shutil.copy2(src, f"{db}.bak-{stamp}{suffix}")
    print(f"[backup] {db}.bak-{stamp}")

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
    con.close()
    print("[counts]", " ".join(f"{t}={after[t]}" for t in TABLES if after[t]))
    if bad:
        print(f"[FAIL] 行数变化: {bad}", file=sys.stderr)
        return 2
    print("[ok] 数据行数保持一致")
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
