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

SCHEMA_VERSION = 17   # 开发期**基线**:不再逐版本迁移(见 _migrate)

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
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  attempt_id   TEXT REFERENCES attempts(id) ON DELETE SET NULL,
  text         TEXT NOT NULL,
  -- 来源:哪次工具调用、哪份产物(可复核);旧观察没有来源就保持 NULL,不补造
  tool_call_id TEXT,
  path         TEXT,
  sha256       TEXT,
  -- 来源强度:direct-stdout / direct-result(强) vs command-write(弱);无来源为 NULL
  relation     TEXT,
  created_at   TEXT NOT NULL
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
  source      TEXT,
  tool_call_id TEXT,
  verified    INTEGER NOT NULL DEFAULT 0,
  notes       TEXT,
  link_relation TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_checks (
  id          TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  method      TEXT NOT NULL,
  artifact_path TEXT,
  artifact_sha256 TEXT,
  attempt_id  TEXT,
  detail      TEXT,
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
  result_path TEXT,
  result_sha256 TEXT,
  observation TEXT,
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
  pid_starttime INTEGER,
  model      TEXT,
  model_calls INTEGER NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0,
  elapsed_s  REAL NOT NULL DEFAULT 0,
  budget_note TEXT,
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

-- 对话历史(面向用户;append-only)。与 checkpoints 职责不同:
--   checkpoints = 模型执行历史(assistant/tool 协议消息,可压缩)
--   conversation_entries = 用户可见的对话(用户输入/assistant 回复/工具结果摘要)
CREATE TABLE IF NOT EXISTS conversation_entries (
  id           TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
  content      TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'ok'
     CHECK (status IN ('ok','queued','partial','error','cancelled')),
  -- 客户端幂等键:同一提交重试不会产生两条用户消息
  client_id    TEXT,
  tool_call_id TEXT,
  parent_id    TEXT,
  meta         TEXT,
  created_at   TEXT NOT NULL,
  UNIQUE (run_id, client_id)
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

-- 长期研究知识(跨目标;与研究状态逻辑隔离)。
-- 知识优先:body_md 是完整、自然的正文;结构化字段只保留最小的分类/检索信息。
-- 不是“每个案例一行”的档案库;目标专属地址/复现细节留在 research state 或原始材料。
CREATE TABLE IF NOT EXISTS knowledge_entries (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  body_md     TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'guideline'
     CHECK (kind IN ('pattern','misreport','guideline')),
  -- 兼容旧枚举；Agent 新写入直接 approved，不产生 draft/needs_review
  status      TEXT NOT NULL DEFAULT 'approved'
     CHECK (status IN ('draft','needs_review','approved','deprecated','superseded')),
  source      TEXT,
  source_hash TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_entry_versions (
  id          TEXT PRIMARY KEY,
  entry_id    TEXT NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
  version     INTEGER NOT NULL,
  title       TEXT NOT NULL,
  body_md     TEXT NOT NULL,
  change_note TEXT,
  created_at  TEXT NOT NULL,
  UNIQUE (entry_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_entry_tags (
  entry_id TEXT NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
  tag      TEXT NOT NULL,
  PRIMARY KEY (entry_id, tag)
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_entry_fts USING fts5(
  entry_id UNINDEXED,
  title,
  body_md,
  tags,
  tokenize='unicode61'
);

-- 研究日志:无法结构化但必须保留的叙述(逐轮记录/停止声明/下一步/决策)
CREATE TABLE IF NOT EXISTS research_logs (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL
     CHECK (kind IN ('round','stop','next','decision','note','needs_review')),
  title        TEXT NOT NULL,
  body         TEXT NOT NULL,
  source       TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  UNIQUE (target_id, kind, title, source)
);

"""


INDEXES = """
CREATE INDEX IF NOT EXISTS idx_hyp_target_state ON hypotheses(target_id, state);
CREATE INDEX IF NOT EXISTS idx_log_target_kind ON research_logs(target_id, kind);
CREATE INDEX IF NOT EXISTS idx_attempt_hyp ON attempts(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_id);
CREATE INDEX IF NOT EXISTS idx_evidence_verified ON evidence(target_id, verified);
CREATE INDEX IF NOT EXISTS idx_events_ref ON events(ref);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_conversation_run ON conversation_entries(run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_call_key ON tool_calls(run_id, call_key)
  WHERE call_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_kind_status ON knowledge_entries(kind,status);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_entries(source);
CREATE INDEX IF NOT EXISTS idx_knowledge_versions ON knowledge_entry_versions(entry_id, version);
CREATE INDEX IF NOT EXISTS idx_knowledge_tags ON knowledge_entry_tags(tag);

"""


class LockedError(RuntimeError):
    """并发写冲突:另一个写者持有写锁(明确失败,不静默覆盖)。"""


def default_db_path(root: str) -> str:
    return os.path.join(root, "state", "fwcore.sqlite3")


def connect(path: str, *, timeout: float = 5.0) -> sqlite3.Connection:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"无法创建数据库目录: {e}") from e
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
    # 纯整数,没有外部输入(PRAGMA 不能参数化);标注给静态扫描器,避免被误判成注入
    con.execute(f"PRAGMA busy_timeout={int(max(0, ms))}")  # nosemgrep: python.lang.security.audit.formatted-sql-query
    _migrate(con)
    return con


_IDENT = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _add_col(con: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """加列(DDL 不能参数化 ⇒ 标识符走白名单校验,拒绝任何拼进来的怪名字)。"""
    for ident in (table, col):
        if not _IDENT.match(ident):
            raise ValueError(f"非法标识符: {ident!r}")
    # 标识符已过白名单正则 ⇒ 这两个 f-string 不接受任意输入(DDL 无法参数化)
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}  # nosemgrep: python.lang.security.audit.formatted-sql-query
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")  # nosemgrep: python.lang.security.audit.formatted-sql-query


# 开发期基线:历史上通过 _stepN 加过的列,一次性列在这里。
# 新库直接由 SCHEMA 建出全部列;旧库用 `tools/db_rebaseline.py`(先备份)一次性补齐。
BASELINE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "observations": [("tool_call_id", "TEXT"), ("path", "TEXT"), ("sha256", "TEXT"),
                     ("relation", "TEXT")],
    "surfaces": [("judged_by", "TEXT")],
    "attempts": [("obs_outcome", "TEXT NOT NULL DEFAULT 'pending'"), ("obs_reason", "TEXT")],
    "tool_calls": [("call_key", "TEXT"), ("provider", "TEXT"), ("capability", "TEXT"),
                   ("arch", "TEXT"), ("env_json", "TEXT"), ("result_path", "TEXT"),
                   ("result_sha256", "TEXT"), ("observation", "TEXT")],
    "runs": [("rounds", "INTEGER NOT NULL DEFAULT 0"), ("pid", "INTEGER"), ("host", "TEXT"),
             ("heartbeat_at", "TEXT"), ("handback_reason", "TEXT"),
             ("pid_starttime", "INTEGER"), ("model", "TEXT"),
             ("model_calls", "INTEGER NOT NULL DEFAULT 0"),
             ("tool_calls", "INTEGER NOT NULL DEFAULT 0"),
             ("elapsed_s", "REAL NOT NULL DEFAULT 0"), ("budget_note", "TEXT")],
    "hypotheses": [("surface_key", "TEXT")],
    "evidence": [("source", "TEXT"), ("tool_call_id", "TEXT"),
                 ("verified", "INTEGER NOT NULL DEFAULT 0"), ("notes", "TEXT"),
                 ("link_relation", "TEXT")],
}


def _migrate(con: sqlite3.Connection) -> None:
    """开发期 schema:只认**当前基线**。

    - 新库:建表 + 直接写版本号(没有任何"逐版本升级"链);
    - 旧库:版本不等于基线 ⇒ 明确报错并指向一次性迁移脚本(先备份)。
      测试库/临时库直接重建;研究工作区用 `python3 tools/db_rebaseline.py <db>`。
    研发期不为历史格式积累升级分支;只有正式发布、出现持久用户数据后才需要版本兼容策略。
    """
    # 先读版本:旧库必须在**任何 DDL 之前**被拒绝,避免"拒绝的同时已经改了库"
    script = SCHEMA
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        row = None                      # 连 meta 都没有 ⇒ 全新的空库
    if row is not None:
        try:
            have = int(row["value"])
        except (TypeError, ValueError):
            raise RuntimeError(f"meta.schema_version 不是整数: {row['value']!r}") from None
        if have not in (0, SCHEMA_VERSION):
            raise RuntimeError(
                f"数据库 schema 版本 {have} ≠ 当前基线 {SCHEMA_VERSION}(开发期不做逐版本升级)。\n"
                f"  测试/临时库:直接重建;研究工作区:先备份,再运行\n"
                f"    python3 tools/db_rebaseline.py {con.execute('PRAGMA database_list').fetchone()[2]}\n"
                f"  该脚本会做一致性快照、补齐基线列、校验完整性与关联。")
    con.executescript(script)
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None or int(row["value"]) == 0:
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),))
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
