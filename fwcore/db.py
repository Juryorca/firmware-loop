"""数据库与迁移(schema 版本化)。

设计要点
- 单一事实源:研究状态存在这里;`notes.md` 只是可读视图/历史材料(见 migrate.py)。
- 程序独占写权:所有写入走 domain.py 的操作接口,外部不直接 SQL。
- 一致性:WAL + `BEGIN IMMEDIATE` + busy_timeout ⇒ 多进程并发写会明确失败(LockedError),不会静默覆盖。
- 可恢复:每次运行有 checkpoint(见 schema 的 runs/checkpoints)。
"""

from __future__ import annotations

import os
import sqlite3

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS targets (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  vendor        TEXT,
  product       TEXT,
  version       TEXT,
  arch          TEXT,
  source_url    TEXT,
  source_sha256 TEXT,
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active'
);

-- 攻击面:一个入口点 = 一行;disposition 只允许 enumerated 值(机器协议,不代表研究世界只有这些)
CREATE TABLE IF NOT EXISTS surfaces (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  key          TEXT NOT NULL,
  reachability TEXT,
  input_control TEXT,
  complexity   TEXT,
  understanding TEXT,
  disposition  TEXT NOT NULL DEFAULT 'unjudged'
     CHECK (disposition IN ('unjudged','vulnerable','no_finding','out_of_scope')),
  note         TEXT,
  judged_by    TEXT,
  UNIQUE (target_id, key)
);

-- 假设:研究意图;来源自由文本(不做封闭枚举)
CREATE TABLE IF NOT EXISTS hypotheses (
  id          TEXT PRIMARY KEY,
  target_id   TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  body        TEXT,
  source      TEXT,
  priority    INTEGER NOT NULL DEFAULT 100,
  state       TEXT NOT NULL DEFAULT 'open'
     CHECK (state IN ('open','investigating','concluded','dropped')),
  verdict     TEXT
     CHECK (verdict IS NULL OR verdict IN ('vulnerable','no_finding','inconclusive')),
  scope_note  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 一次尝试(不等于假设本身);result 区分"工具/环境失败"与"研究结论"
CREATE TABLE IF NOT EXISTS attempts (
  id          TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  method      TEXT NOT NULL,
  params      TEXT,
  result      TEXT NOT NULL DEFAULT 'running'
     CHECK (result IN ('running','supported','refuted','inconclusive','environment_failure','no_signal','cancelled')),
  note        TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT
);

-- 观察:可来自任何活动;可提升为新假设(见 domain.promote_observation)
CREATE TABLE IF NOT EXISTS observations (
  id         TEXT PRIMARY KEY,
  target_id  TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  attempt_id TEXT REFERENCES attempts(id) ON DELETE SET NULL,
  text       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 证据:必须绑定 目标/版本/来源调用/材料哈希
CREATE TABLE IF NOT EXISTS evidence (
  id          TEXT PRIMARY KEY,
  target_id   TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  attempt_id  TEXT REFERENCES attempts(id) ON DELETE SET NULL,
  hypothesis_id TEXT REFERENCES hypotheses(id) ON DELETE SET NULL,
  kind        TEXT NOT NULL,
  version     TEXT,
  path        TEXT,
  sha256      TEXT,
  capture     TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conclusions (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  hypothesis_id TEXT REFERENCES hypotheses(id) ON DELETE SET NULL,
  verdict      TEXT NOT NULL CHECK (verdict IN ('vulnerable','no_finding','inconclusive')),
  scope_note   TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

-- 工具调用:真实执行状态(不把"发起"当"完成")
CREATE TABLE IF NOT EXISTS tool_calls (
  id         TEXT PRIMARY KEY,
  run_id     TEXT,
  tool       TEXT NOT NULL,
  args       TEXT,
  status     TEXT NOT NULL DEFAULT 'pending'
     CHECK (status IN ('pending','running','ok','error','timeout','cancelled')),
  exit_code  INTEGER,
  duration_ms INTEGER,
  stdout_path TEXT,
  stderr_path TEXT,
  note       TEXT,
  created_at TEXT NOT NULL
);

-- 运行与检查点:进程中断 != 研究停止
CREATE TABLE IF NOT EXISTS runs (
  id         TEXT PRIMARY KEY,
  target_id  TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  state      TEXT NOT NULL DEFAULT 'running'
     CHECK (state IN ('running','paused','finished','failed')),
  step       INTEGER NOT NULL DEFAULT 0,
  goal       TEXT,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
  id        TEXT PRIMARY KEY,
  run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  step      INTEGER NOT NULL,
  messages  TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 事件流(审计/回放;只追加)
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  kind      TEXT NOT NULL,
  ref       TEXT,
  payload   TEXT
);

-- 历史材料:notes.md 等原文原样保存(迁移不丢信息)
CREATE TABLE IF NOT EXISTS documents (
  id         TEXT PRIMARY KEY,
  target_id  TEXT,
  path       TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  body       TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  UNIQUE (path, sha256)
);

-- 三镜刷新:面/格收口时的一行记录(程序可核:距上次超过 N 轮 ⇒ 收尾阻塞)
CREATE TABLE IF NOT EXISTS mirror_checks (
  id         TEXT PRIMARY KEY,
  target_id  TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  mechanism  TEXT NOT NULL,
  author     TEXT NOT NULL,
  state      TEXT NOT NULL,
  new_faces  TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hyp_target_state ON hypotheses(target_id, state);
CREATE INDEX IF NOT EXISTS idx_attempt_hyp ON attempts(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_id);
CREATE INDEX IF NOT EXISTS idx_events_ref ON events(ref);
"""


class LockedError(RuntimeError):
    """并发写冲突:另一个写者持有写锁(明确失败,不静默覆盖)。"""


def default_db_path(root: str) -> str:
    return os.path.join(root, "state", "fwcore.sqlite3")


def connect(path: str, *, timeout: float = 5.0) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    cur = con.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        con.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    else:
        have = int(row["value"])
        if have > SCHEMA_VERSION:
            raise RuntimeError(f"数据库 schema 版本 {have} 高于代码支持 {SCHEMA_VERSION}")
        if have < 2:
            cols = {r[1] for r in con.execute("PRAGMA table_info(surfaces)")}
            if "judged_by" not in cols:
                con.execute("ALTER TABLE surfaces ADD COLUMN judged_by TEXT")
            con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")


class Tx:
    """写事务:`BEGIN IMMEDIATE` ⇒ 并发写者拿不到锁就直接报 LockedError。"""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self) -> sqlite3.Connection:
        try:
            self.con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:  # database is locked
            raise LockedError(str(e)) from e
        return self.con

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.con.execute("COMMIT")
        else:
            self.con.execute("ROLLBACK")
        return False
