#!/usr/bin/env bash
# queue_state.sh — "继续护栏"用的只读状态探针(不写任何东西)
# 唯一事实源 = state/fwcore.sqlite3；不读 notes.md、不解析 Markdown。
# 输出一行: OPEN=<未处理假设> BAD=<不合规降级/轮结构> ROUND=<done|half|?> STOP=<最近停止声明> POOL=<假设/面> NEXT=<最近下一步>
set -u
ROOT="${FW_LOOP_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
T="${1:-}"
# Local kill switch for the current Pi process: the guard calls this probe on
# every settle, so this disables live reminders without restarting Pi.
if [ -f "$ROOT/.pi/continue-guard.disabled" ]; then
    echo "OPEN=0 BAD=0 ROUND=? STOP= POOL=0/0 NEXT="
    exit 0
fi
exec python3 - "$ROOT" "$T" <<'PY'
import os
import re
import sqlite3
import sys

root, target = sys.argv[1], sys.argv[2]
db_path = os.environ.get("FW_QUEUE_DB") or os.path.join(root, "state", "fwcore.sqlite3")
if not os.path.isfile(db_path):
    print("OPEN=0 BAD=0 ROUND=? STOP= POOL=0/0 NEXT=")
    raise SystemExit(0)

con = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row

def one(sql, args=()):
    return con.execute(sql, args).fetchone()

def scalar(sql, args=(), default=0):
    row = one(sql, args)
    return row[0] if row and row[0] is not None else default

# 目标:显式参数优先；否则取最近 run，再退到最近日志/目标。全程不碰 notes.md。
if not target:
    row = one(
        "SELECT t.name FROM runs r JOIN targets t ON t.id=r.target_id"
        " WHERE r.state='running' ORDER BY r.started_at DESC LIMIT 1")
    if row is None:
        row = one(
            "SELECT t.name FROM runs r JOIN targets t ON t.id=r.target_id"
            " ORDER BY r.started_at DESC LIMIT 1")
    if row is None:
        row = one(
            "SELECT t.name FROM research_logs l JOIN targets t ON t.id=l.target_id"
            " ORDER BY l.created_at DESC LIMIT 1")
    if row is None:
        row = one("SELECT name FROM targets ORDER BY created_at DESC LIMIT 1")
    target = row["name"] if row else ""

t = one("SELECT id,name FROM targets WHERE name=?", (target,)) if target else None
if t is None:
    print("OPEN=0 BAD=0 ROUND=? STOP= POOL=0/0 NEXT=")
    raise SystemExit(0)
tid = t["id"]

open_n = scalar(
    "SELECT COUNT(*) FROM hypotheses WHERE target_id=?"
    " AND state IN ('open','investigating')", (tid,))
pool = scalar("SELECT COUNT(*) FROM hypotheses WHERE target_id=?", (tid,))
faces = scalar("SELECT COUNT(*) FROM surfaces WHERE target_id=?", (tid,))

# 降级理由只认“最近的轮/停止声明”；不把历史 dropped 全部翻出来误报。
# 成本类/只靠 N 轮且无判定词 -> BAD；判定类理由（不可达/已排除/证伪/需用户...）放行。
cost = re.compile(r"成本|太重|太贵|复杂|预算|时间不够|工作量大|超时|太慢|耗时|导入慢|工程量大")
judg = re.compile(r"判据|不可达|上游|设计内|已排除|证伪|需工作区外|需用户|真机")
only_rounds = re.compile(r"(?:\d|两|几)\s*轮")
bad = 0
recent = con.execute(
    "SELECT body,title FROM research_logs WHERE target_id=?"
    " AND kind IN ('round','stop') ORDER BY created_at DESC LIMIT 1", (tid,)).fetchone()
if recent:
    text = (recent["body"] or "") + "\n" + (recent["title"] or "")
    for line in text.splitlines():
        if not re.search(r"降级|放弃|drop|Out of scope|收口", line, re.I):
            continue
        if (cost.search(line) and not judg.search(line)) or (
                only_rounds.search(line) and not judg.search(line)):
            bad += 1
            break

# 最近一轮：从 DB research_logs(kind=round) 的结构化叙述判断是否推到结论。
rows = con.execute(
    "SELECT title,body FROM research_logs WHERE target_id=? AND kind='round'"
    " ORDER BY created_at DESC LIMIT 2", (tid,)).fetchall()
round_state = "?"
if rows:
    text = (rows[0]["body"] or "") + "\n" + (rows[0]["title"] or "")
    m = re.search(r"本轮目标\s*[:：]\s*([A-Za-z0-9_-]+)", text)
    if m:
        hid = re.escape(m.group(1))
        done = re.search(
            r"本轮\s*[:：][^\n]*" + hid +
            r"[^\n]*(定论|判负|No finding|降级|结论|未证伪|否证)", text)
        round_state = "done" if done else "half"
        # 同一轮日志里“收口 H”没有对应“目标 H”/同一 H 重复收口 -> 结构 BAD。
        goals = re.findall(r"本轮目标\s*[:：]\s*([A-Za-z0-9_-]+)", text)
        closes = re.findall(r"(?m)^>?\s*本轮\s*[:：]\s*([A-Za-z0-9_-]+)", text)
        if any(h not in goals for h in closes) or any(closes.count(h) > 1 for h in closes):
            bad += 1

stop_row = one(
    "SELECT title,body FROM research_logs WHERE target_id=? AND kind='stop'"
    " ORDER BY created_at DESC LIMIT 1", (tid,))
stop_text = ""
if stop_row:
    stop_text = (stop_row["body"] or stop_row["title"] or "").replace("\n", " ")
next_row = one(
    "SELECT title,body FROM research_logs WHERE target_id=? AND kind='next'"
    " ORDER BY created_at DESC LIMIT 1", (tid,))
next_text = ""
if next_row:
    next_text = (next_row["body"] or next_row["title"] or "").replace("\n", " ")

print(f"OPEN={open_n} BAD={bad} ROUND={round_state} STOP={stop_text[:120]} "
      f"POOL={pool}/{faces} NEXT={next_text[:160]}")
PY
