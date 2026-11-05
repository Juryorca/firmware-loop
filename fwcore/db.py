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

SCHEMA_VERSION = 7

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
  surface_key TEXT,
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
  -- 双输出的"观察"一侧:pending(未交代) / new(有新观察) / none(无新观察,需理由)
  obs_outcome TEXT NOT NULL DEFAULT 'pending'
     CHECK (obs_outcome IN ('pending','new','none')),
  obs_reason  TEXT,
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
  -- 模型给出的调用 id:同一 run 内重复执行会被唯一索引挡住(恢复时幂等,不重复副作用)
  call_key   TEXT,
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
  provider   TEXT,
  capability TEXT,
  arch       TEXT,
  env_json   TEXT,
  created_at TEXT NOT NULL
);

-- 运行与检查点:进程中断 != 研究停止
CREATE TABLE IF NOT EXISTS runs (
  id         TEXT PRIMARY KEY,
  target_id  TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  state      TEXT NOT NULL DEFAULT 'running'
     CHECK (state IN ('running','paused','finished','failed')),
  step       INTEGER NOT NULL DEFAULT 0,
  rounds     INTEGER NOT NULL DEFAULT 0,
  goal       TEXT,
  pid        INTEGER,
  host       TEXT,
  heartbeat_at TEXT,
  handback_reason TEXT,
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

"""


INDEXES = """
CREATE INDEX IF NOT EXISTS idx_hyp_target_state ON hypotheses(target_id, state);
CREATE INDEX IF NOT EXISTS idx_attempt_hyp ON attempts(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_id);
CREATE INDEX IF NOT EXISTS idx_evidence_verified ON evidence(target_id, verified);
CREATE INDEX IF NOT EXISTS idx_events_ref ON events(ref);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_call_key ON tool_calls(run_id, call_key)
  WHERE call_key IS NOT NULL;
"""


class LockedError(RuntimeError):
    """并发写冲突:另一个写者持有写锁(明确失败,不静默覆盖)。"""


def default_db_path(root: str) -> str:
    return os.path.join(root, "state", "fwcore.sqlite3")


def connect(path: str, *, timeout: float = 5.0) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # check_same_thread=False:取消/看护/长工具会在别的线程里读同一连接。
    # 写路径一律 `BEGIN IMMEDIATE` + busy_timeout 串行化 ⇒ 由 SQLite 兜并发,不靠 Python 线程锁。
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        ms = int(float(timeout) * 1000)
    except (TypeError, ValueError):
        ms = 5000
    con.execute("PRAGMA busy_timeout=%d" % max(0, ms))
    _migrate(con)
    return con


_IDENT = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _add_col(con: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """加列(DDL 不能参数化 ⇒ 标识符走白名单校验,拒绝任何拼进来的怪名字)。"""
    for ident in (table, col):
        if not _IDENT.match(ident):
            raise ValueError(f"非法标识符: {ident!r}")
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _step2(con: sqlite3.Connection) -> None:
    _add_col(con, "surfaces", "judged_by", "TEXT")


def _step7(con: sqlite3.Connection) -> None:
    """证据"复核"必须是**可检查的记录**,不是一个自称的标志位。

    - 新表 `evidence_checks`:每条复核动作(方法/产物/哈希/关联尝试/说明)都留痕;
    - `evidence.verified` 只由这些记录派生(没有 check 行 = 未复核);
    - 历史行(迁移前 `verified=1`)**一律清零**:当时没有检查记录,不能推断它被独立复现过。
    """
    con.execute("""
    CREATE TABLE IF NOT EXISTS evidence_checks (
      id          TEXT PRIMARY KEY,
      evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
      method      TEXT NOT NULL,
      artifact_path TEXT,
      artifact_sha256 TEXT,
      attempt_id  TEXT,
      detail      TEXT,
      created_at  TEXT NOT NULL
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_evidence_checks_ev ON evidence_checks(evidence_id)")
    con.execute("UPDATE evidence SET verified=0 WHERE verified!=0")
    con.execute("UPDATE evidence SET notes = COALESCE(notes || ' | ','') || "
                "'legacy: 迁移前的 verified 无检查记录,不算已验证'")


def _step6(con: sqlite3.Connection) -> None:
    """工具结果的原始产物与结构化观测。

    - `result_path/result_sha256`:工具返回的文本结果也落盘并算哈希(证据可直接引用)
    - `observation`:**解析后的观测**(JSON,来自 toolkit 的 OBSERVERS / MCP)——
      它是"工具说了什么",不是"漏洞成立";要成为证据仍必须 `add_evidence` + 复核
    """
    _add_col(con, "tool_calls", "result_path", "TEXT")
    _add_col(con, "tool_calls", "result_sha256", "TEXT")
    _add_col(con, "tool_calls", "observation", "TEXT")


def _step5(con: sqlite3.Connection) -> None:
    """记录 pid 的启动时间:跨主机/重启后靠它识别 **pid 复用**(否则复活进程会被误当原 run)。"""
    _add_col(con, "runs", "pid_starttime", "INTEGER")


def _step4(con: sqlite3.Connection) -> None:
    """证据溯源:来源(哪个工具/哪次调用)、是否经研究复核。

    `verified` 的语义(硬):
    - 0 = **原始工具输出/未复核**(工具跑成功 ≠ 漏洞成立)
    - 1 = 研究复核过的证据(复现/交叉验证后才提升)
    """
    _add_col(con, "evidence", "source", "TEXT")
    _add_col(con, "evidence", "tool_call_id", "TEXT")
    _add_col(con, "evidence", "verified", "INTEGER NOT NULL DEFAULT 1")
    _add_col(con, "evidence", "notes", "TEXT")


def _step3(con: sqlite3.Connection) -> None:
    _add_col(con, "attempts", "obs_outcome", "TEXT NOT NULL DEFAULT 'pending'")
    _add_col(con, "attempts", "obs_reason", "TEXT")
    _add_col(con, "tool_calls", "call_key", "TEXT")
    _add_col(con, "tool_calls", "provider", "TEXT")
    _add_col(con, "tool_calls", "capability", "TEXT")
    _add_col(con, "tool_calls", "arch", "TEXT")
    _add_col(con, "tool_calls", "env_json", "TEXT")
    _add_col(con, "runs", "rounds", "INTEGER NOT NULL DEFAULT 0")
    _add_col(con, "runs", "pid", "INTEGER")
    _add_col(con, "runs", "host", "TEXT")
    _add_col(con, "runs", "heartbeat_at", "TEXT")
    _add_col(con, "runs", "handback_reason", "TEXT")
    _add_col(con, "hypotheses", "surface_key", "TEXT")


STEPS = {2: _step2, 3: _step3, 4: _step4, 5: _step5, 6: _step6, 7: _step7}


def _migrate(con: sqlite3.Connection) -> None:
    """版本化迁移。

    顺序很关键(2026-11-04 修复):
    - 先建表(不含索引);
    - 逐版本执行 ALTER(每步一个事务;失败回滚**且不改 schema_version**);
    - **最后**才建索引 —— 否则旧库还没有 `call_key` 就去建唯一索引,升级直接失败。
    """
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    try:
        have = int(row["value"]) if row else 0
    except (TypeError, ValueError):
        raise RuntimeError(f"meta.schema_version 不是整数: {row['value']!r}") from None
    if have > SCHEMA_VERSION:
        raise RuntimeError(f"数据库 schema 版本 {have} 高于代码支持 {SCHEMA_VERSION}")
    if row is None:
        con.execute("INSERT INTO meta(key,value) VALUES('schema_version','0')")
    for v in range(have + 1, SCHEMA_VERSION + 1):
        step = STEPS.get(v)
        con.execute("BEGIN IMMEDIATE")
        try:
            if step:
                step(con)
            con.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(v),))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    con.executescript(INDEXES)


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
