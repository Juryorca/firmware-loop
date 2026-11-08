#!/usr/bin/env bash
# derived_check.sh — DB 权威派生量/材料检查
#
# 用法: bash tools/derived_check.sh <目标目录名> [--closure]
# 目标必须显式给出；不通过 notes.md 或最近修改时间猜目标。
# 研究事实、假设池、三镜、证据/收口只读 SQLite；notes.md 是可选的旧历史，不参与判定。
set -u
CODE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${FW_ROOT:-$CODE_ROOT}"
TARGET="${1:-}"
CLOSURE=0
case "${2:-}" in --closure) CLOSURE=1;; esac
if [ -z "$TARGET" ]; then
  echo "用法: derived_check.sh <目标目录名> [--closure]" >&2
  echo "目标必须显式指定；不会根据 notes.md 的修改时间猜目标。" >&2
  exit 2
fi
STATE="$ROOT/state/$TARGET"
PY="${PYTHON:-python3}"
"$PY" - "$CODE_ROOT" "$ROOT" "$STATE" "$TARGET" "$CLOSURE" <<'PYEOF'
import glob
import os
import re
import sqlite3
import sys

code_root, root, state, target = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
closure = sys.argv[5] == "1"
sys.path.insert(0, code_root)
from fwcore.gates_db import evaluate, dangling_attachment_refs  # noqa: E402

fail = 0
def ok(msg):
    print("OK   " + msg)
def bad(msg):
    global fail
    fail += 1
    print("FAIL " + msg)

if not os.path.isdir(state):
    # DB-only 目标（例如刚用 `fw-core init` 创建、尚无 notes/report/工作副本）
    # 是合法状态；不能把缺少工作目录本身当成 FAIL。
    print(f"NOTE 没有 state/{target} 工作目录；以 SQLite 状态继续检查")
con = None
target_row = None
for cand in (os.path.join(state, "state", "fwcore.sqlite3"),
             os.path.join(root, "state", "fwcore.sqlite3")):
    if not os.path.isfile(cand):
        continue
    _c = sqlite3.connect(cand)
    _c.row_factory = sqlite3.Row
    try:
        target_row = _c.execute("SELECT id FROM targets WHERE name=?", (target,)).fetchone()
    except sqlite3.DatabaseError:
        target_row = None
    if target_row is not None:
        con = _c
        break
    _c.close()
if con is None:
    bad(f"缺权威状态: {target} 不在任何 fwcore.sqlite3 里(运行路径只读 SQLite)")
    print("=== derived_check: FAIL ===")
    sys.exit(1)
t = target_row
if t is None:
    bad(f"缺权威状态: targets 表没有 {target}")
    print("=== derived_check: FAIL ===")
    sys.exit(1)
tid = t["id"]
try:
    rep = evaluate(con, root, target)
except Exception as e:
    bad(f"Gate 计算失败: {type(e).__name__}: {e}")
    rep = None

# ---------- 引用完整性：只检查正式 report 交付物；notes 是可选历史，不参与 ----------
tgts = [d for d in os.listdir(os.path.join(root, "state"))
        if os.path.isdir(os.path.join(root, "state", d)) and d != "artifacts"]
refpat = re.compile(r"(?:\.\./)?(" + "|".join(map(re.escape, tgts)) + r")/([A-Za-z0-9._/*-]+)")
scanned = []
rdir = os.path.join(state, "report")
if os.path.isdir(rdir):
    scanned = [os.path.join(rdir, x) for x in os.listdir(rdir) if x.endswith(".md")]
dangle = []
for f in scanned:
    lines = open(f, encoding="utf-8", errors="ignore").read().split("\n")
    for ln in lines:
        for m in refpat.finditer(ln):
            p = os.path.join(root, "state", m.group(1), m.group(2).rstrip("/"))
            if "*" in p:
                par, pre = os.path.split(p.split("*")[0])
                exists = os.path.isdir(par) and any(
                    x.startswith(os.path.basename(pre)) for x in os.listdir(par))
            else:
                exists = os.path.exists(p)
            if exists:
                continue
            # 豁免只看当前引用所在行，不能因为文件任意处出现一次“已释放”
            # 就放过同一文件里的其他断链。
            if any(k in ln for k in
                   ("已释放", "需按配方重建", "重下见", "SOURCE_URL", "已随目标清理", "公式重建")):
                continue
            dangle.append((f, f"{m.group(1)}/{m.group(2)[:40]}"))
if dangle:
    where = sorted({f"{os.path.relpath(f, root)}:{ref}" for f, ref in dangle})
    bad(f"断链引用未标注({len(dangle)} 处): {where[:6]}")
else:
    ok(f"引用完整性: {len(scanned)} 个 report 文件无未标注断链")

# ---------- A2 / 队列 / 降级：SQLite 权威 ----------
buckets = {"Vulnerable": [], "Part": [], "NoFind": [], "Unjudged": [], "Out": [], "UNPARSED": []}
for r in con.execute("SELECT disposition, COUNT(*) n FROM surfaces WHERE target_id=? GROUP BY disposition", (tid,)):
    key = {"vulnerable": "Vulnerable", "no_finding": "NoFind",
           "out_of_scope": "Out", "unjudged": "Unjudged"}.get(r["disposition"])
    (buckets[key] if key else buckets["UNPARSED"]).extend([r["disposition"]] * r["n"])
_un = [r["key"] for r in con.execute(
    "SELECT key FROM surfaces WHERE target_id=? AND disposition='unjudged'", (tid,))]
_op = [dict(r) for r in con.execute(
    "SELECT id,title,surface_key FROM hypotheses WHERE target_id=? AND state IN ('open','investigating')", (tid,))]
_dr = [dict(r) for r in con.execute(
    "SELECT id,title,scope_note FROM hypotheses WHERE target_id=? AND state='dropped'", (tid,))]
total = sum(len(v) for v in buckets.values())
print(f"A2 覆盖度(权威 = SQLite surfaces;{total} 个面)")
for k in ("Vulnerable", "Part", "NoFind", "Unjudged", "Out", "UNPARSED"):
    print(f"   {k:<10} {len(buckets[k])}")
print(f"覆 盖 度 摘 要(可直接粘): Vulnerable={len(buckets['Vulnerable'])} Part={len(buckets['Part'])} "
      f"NoFind={len(buckets['NoFind'])} Unjudged={len(buckets['Unjudged'])} OutOfScope={len(buckets['Out'])}"
      + (f" | 未解析={len(buckets['UNPARSED'])}" if buckets["UNPARSED"] else ""))
if buckets["UNPARSED"]:
    bad(f"surfaces.disposition 有非法值: {buckets['UNPARSED'][:3]}")
_bad_drop = [h["id"] for h in _dr if not (h["scope_note"] or "").strip()
             or re.search(r"成本|太重|太贵|复杂|预算|时间不够|工作量大", h["scope_note"] or "")]
if _bad_drop:
    bad(f"降级理由违规/缺失(成本类不算理由): {_bad_drop[0]}")
elif _dr:
    ok(f"降级理由合规(dropped {len(_dr)} 条均有判定类理由)")
_mapped = {h["surface_key"] for h in _op if h["surface_key"]}
_miss = [k for k in _un if k not in _mapped]
print(f"队列一致性(DB): 未判定面 {len(_un)} / 未收口假设 {len(_op)}"
      + ("(应为 0:未判定面须先入池)" if _un and not _op else ""))
if _miss:
    print(f"  ⚠ 未入池的未判定面: {_miss[:8]} (收尾时 FAIL)")
elif _un:
    ok("未判定面均已进入假设池")

# ---------- 材料/report 包（正式交付物） ----------
SKIP = {"INDEX.md", "CVE-request-mitre.md", "attack-surface.md", "CLOSURE.md"}
NEED = ["Summary", "Root Cause", "Steps to Reproduce", "Evidence"]
advs = []
if os.path.isdir(rdir):
    for _f in sorted(os.listdir(rdir)):
        if not _f.endswith(".md") or _f in SKIP:
            continue
        try:
            _body = open(os.path.join(rdir, _f), encoding="utf-8").read()
        except OSError:
            continue
        if _f.startswith("VR-") or "## Summary" in _body:
            advs.append(_f)
if not advs:
    print("finding: 0 个 advisory(本轮无 finding)")
for adv in advs:
    t2 = open(os.path.join(rdir, adv), encoding="utf-8").read()
    miss = [s for s in NEED if not re.search(r"^##\s+" + re.escape(s), t2, re.M)]
    if miss:
        bad(f"{adv}: advisory 缺节 {miss}")
    links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", t2)
    if not links:
        bad(f"{adv}: 无 markdown 图链")
    stray = [ln.strip()[:70] for ln in t2.splitlines()
             if "attachments/" in ln and "](attachments/" not in ln]
    if stray:
        bad(f"{adv}: 附件路径以纯文本出现: {stray[0]}")
    for L in links:
        target_path = os.path.join(rdir, L)
        d = os.path.dirname(target_path)
        if not os.path.isfile(target_path):
            bad(f"{adv}: 图链指向不存在文件 → {L}")
            continue
        ok(f"{adv}: 图链可解析 → {L}")
        if not os.path.isfile(os.path.join(d, "shots.json")):
            bad(f"{adv}: {os.path.basename(d)}/ 缺 shots.json")
        if not [f for f in os.listdir(d) if f.endswith(".png.provenance")]:
            bad(f"{adv}: {os.path.basename(d)}/ 缺 *.png.provenance")
        links2 = []
        for r, ds, fs in os.walk(d):
            for n in ds + fs:
                p2 = os.path.join(r, n)
                if os.path.islink(p2):
                    links2.append(os.path.relpath(p2, d) + " -> " + os.readlink(p2))
        if links2:
            bad(f"{adv}: 包内符号链接: {links2[:3]}")
        if not any(n.lower().startswith("readme") for n in os.listdir(d)):
            bad(f"{adv}: {os.path.basename(d)}/ 缺 README")
    if not miss and links and all(os.path.isfile(os.path.join(rdir, L)) for L in links):
        ok(f"{adv}: 骨架 {len(NEED)}/{len(NEED)} 节 + 图链 + 包完整")

# ---------- DB Gate 阻断项（收尾必须清零） ----------
if rep is not None:
    if rep.material_missing:
        print("  ⚠ 报告材料缺口: " + "; ".join(rep.material_missing[:6]))
    for b in rep.blockers:
        if closure:
            bad("收口阻塞: " + b)
        else:
            print("  ⚠ " + b)
    if rep.mirror_due:
        msg = f"三镜刷新超期({rep.rounds_since_mirror} 轮,下限 3)"
        bad(msg) if closure else print("  ⚠ " + msg)
    else:
        ok("三镜刷新: 未超期")
    if rep.advisories:
        for a in rep.advisories:
            print("  NOTE " + a)

# ---------- 收尾专属：队列 / attack-surface 视图 / 轮结构（DB research_logs） ----------
if closure:
    if _miss:
        bad(f"队列不变量: 未判定面不在假设池: {_miss[:8]}")
    else:
        ok(f"队列不变量(DB): 未判定面 {len(_un)} 个均已入池")
    if _op:
        bad(f"假设池有 {len(_op)} 条未处理: {_op[0]['id']}")
    else:
        ok("假设池(DB): 无未处理条目")
    asf = os.path.join(rdir, "attack-surface.md")
    rows = []
    if os.path.isfile(asf):
        rows = [ln for ln in open(asf, encoding="utf-8").read().splitlines()
                if re.match(r"^\|\s*\d+[a-z]?", ln)]
        unde = [ln[:70] for ln in rows if "未判定" in ln]
        if unde:
            print(f"  ⚠ attack-surface.md 视图仍有 '未判定' 行({len(unde)});权威以 DB 为准")
    stale = [ln[:70] for ln in rows if re.search(r"无(任何)?监听|面不存在", ln) and "对账" not in ln]
    if stale:
        hit = con.execute(
            "SELECT id FROM research_logs WHERE target_id=? AND "
            "(title LIKE '%误判回溯%' OR body LIKE '%误判回溯%' OR "
            " (body LIKE '%错误类%' AND body LIKE '%谓词%' AND body LIKE '%处置%')) LIMIT 1",
            (tid,)).fetchone()
        if hit:
            ok(f"误判回溯: 已在 SQLite research_logs 登记({hit['id']})")
        else:
            bad(f"误判回溯: attack-surface 有 {len(stale)} 行可疑但 research_logs 无谓词/处置记录")
    if advs:
        cand = [f for pat in ("*listening*", "*liveness*", "*ready*", "*listening-sockets*")
                for f in glob.glob(os.path.join(state, "artifacts/recon", pat))]
        cl = os.path.join(rdir, "CLOSURE.md")
        if not cand:
            if os.path.isfile(cl) and re.search(r"就绪态[^\n]{0,40}(取得|不可得|静态候选|未实测)",
                                                 open(cl, encoding="utf-8", errors="ignore").read()):
                ok("就绪态清单: CLOSURE 已声明取得情况")
            else:
                bad("缺就绪态清单，且 CLOSURE 未声明取得情况")
        else:
            txt = "".join(open(f, encoding="utf-8", errors="ignore").read() for f in cand)
            if not re.search(r"ready|running|就绪|判据", txt):
                bad("就绪态清单没有就绪判据/取值")
            else:
                asf_txt = open(asf, encoding="utf-8").read() if os.path.isfile(asf) else ""
                body = "\n".join(l for l in txt.split("\n") if not l.lstrip().startswith("#"))
                ports = {m.group(1) for m in re.finditer(r":(\d{2,5})\b", body)
                         if int(m.group(1)) in (80, 443, 161) or int(m.group(1)) > 1024}
                missp = sorted(p for p in ports if p not in asf_txt)
                if missp:
                    bad(f"A1 完整性: 这些端口在 attack-surface.md 找不到: {missp[:12]}")
                else:
                    ok(f"A1 完整性: {len(ports)} 个端口均在 A2 表内")
                fs_prefix = ("/data/", "/etc/", "/usr/", "/var/", "/tmp/", "/lib/", "/opt/", "/bin/", "/sbin/", "/proc/", "/sys/")
                ipaths = {m.group(0).rstrip(".,;:") for m in re.finditer(r"/[a-zA-Z0-9_][a-zA-Z0-9_./*-]{3,}", body)}
                ipaths = {q for q in ipaths if q.count("/") >= 2 and not q.startswith(fs_prefix)}
                if ipaths:
                    pmiss = sorted(q for q in ipaths if q not in asf_txt)
                    if pmiss:
                        bad(f"A1 location 完整性: 路径不在 A2 表内: {pmiss[:10]}")
                    else:
                        ok(f"A1 location 完整性: {len(ipaths)} 条路径均在 A2 表内")
                else:
                    bad("A1 location 完整性: 清单里没有 HTTP 路径(location)记录")
        # 论点—证据缺口：由 Gate 的 blockers/finding 证据链承担，不再解析 notes
        if rep is not None and not rep.blockers:
            ok("论点—证据: Gate 未报缺口/未复核证据")
    else:
        print("收尾前置: 无 finding ⇒ 误判回溯/缺口检查跳过")

# ---------- 轮结构（DB research_logs；缺日志的新目标不判 FAIL） ----------
round_logs = [dict(r) for r in con.execute(
    "SELECT title,body FROM research_logs WHERE target_id=? AND kind='round'"
    " ORDER BY created_at DESC LIMIT 5", (tid,))]
if round_logs:
    anomalies = []
    checked = 0
    for row in round_logs:
        txt = (row["title"] or "") + "\n" + (row["body"] or "")
        goals = re.findall(r"本轮目标\s*[:：]\s*\**\s*(H-\d+)", txt)
        closes = re.findall(r"(?m)^>?\s*本轮\s*[:：]\s*\**\s*(H-\d+)", txt)
        orphan = sorted({h for h in closes if h not in goals})
        dup = sorted({h for h in closes if closes.count(h) > 1})
        checked += 1
        if len(goals) >= 2:
            anomalies.append(f"记录#{checked} 声明 {len(goals)} 个目标({', '.join(goals)})")
        elif orphan or dup:
            anomalies.append(f"记录#{checked} 无目标收口={orphan} 重复收口={dup}")
    if anomalies:
        msg = "轮结构: " + "; ".join(anomalies)
        bad(msg) if closure else print("  ⚠ " + msg)
    else:
        ok(f"轮结构(DB): {checked} 条 round 记录逐条检查无异常")
else:
    print("轮结构: 无 research_logs(kind=round)；新目标不因缺 notes 判 FAIL")

print("=== derived_check: " + ("PASS" if fail == 0 else f"FAIL x{fail}") + " ===")
sys.exit(1 if fail else 0)
PYEOF
