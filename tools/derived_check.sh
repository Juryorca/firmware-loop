#!/usr/bin/env bash
# derived_check.sh — 派生量重算器(工具,不引入任何新语法)
#
# 用法: bash tools/derived_check.sh [目标目录名] [--closure]
#   --closure: 额外跑**收尾前置**检查(A2 无"未判定" / 误判回溯范围已横扫 / 论点—证据表无未处理缺口)
#   目标缺省 = state/ 下含 report/INDEX.md 的目录(多个时取字典序第一个)
#
# 为什么: "覆盖度 / 条数 / 材料完成"这类值都能从权威处**算**出来;手写复述必漂。
#   本脚本把它们算出来 → 断言一律粘这里的输出,散文里不写总结。
# 检查项:
#   ① knowledge/ 各文件条数 vs knowledge/INDEX.md 的声明
#   ② A2 覆盖度(解析 state/<目标>/notes.md 的 "### 2.3" 表;不要求新语法,用表里现有措辞分类)
#   ③ 每个 finding 的包:advisory 骨架 + **至少 1 处图链且路径可解析** + shots.json/.provenance
# 退出码: 有 FAIL → 1;全 PASS → 0
set -u
PY=/home/juryorca/ENTER/envs/pwn/bin/python
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"
CLOSURE=0
case "${2:-}" in --closure) CLOSURE=1;; esac
if [ -z "$TARGET" ]; then
  # 默认 = 最近修改过 notes.md 的 state 目录(= 活跃目标);多目标并存时不会选错
  TARGET="$(ls -1dt "$ROOT"/state/*/notes.md 2>/dev/null | head -1 | xargs -r dirname | xargs -r basename)"
fi
[ -n "$TARGET" ] || { echo "找不到目标(无 state/*/report/INDEX.md);用法: derived_check.sh <目标目录名>" >&2; exit 2; }
STATE="$ROOT/state/$TARGET"

"$PY" - "$ROOT" "$STATE" "$TARGET" "$CLOSURE" <<'PYEOF'
import os, re, sys
root, state, target = sys.argv[1], sys.argv[2], sys.argv[3]
closure = sys.argv[4] == "1"
fail = 0
def ok(m):   print("OK   " + m)
def bad(m):
    global fail; fail += 1; print("FAIL " + m)

# ---------- ① knowledge 条数 ----------
idx = open(os.path.join(root, "knowledge/INDEX.md"), encoding="utf-8").read()
rows = re.findall(r"^\|\s*`([A-Za-z0-9_.-]+\.md)`\s*\|.*?\|\s*(\d+)\s*\|", idx, re.M)
if not rows:
    bad("knowledge/INDEX.md 里没解析出「文件|条数」行")
for name, declared in rows:
    p = os.path.join(root, "knowledge", name)
    if not os.path.isfile(p):
        bad(f"knowledge/{name} 不存在(INDEX 有登记)"); continue
    body = open(p, encoding="utf-8").read()

    # intel-log 的条目是 "- 20xx-..";其余是 "## "
    got = len(re.findall(r"^- 20\d\d", body, re.M)) if name == "intel-log.md" else len(re.findall(r"^## ", body, re.M))
    if got == int(declared): ok(f"knowledge/{name}: {got} 条(与 INDEX 一致)")
    else: bad(f"knowledge/{name}: INDEX 声明 {declared} 条,实际 {got} 条")

# KB 与证据解耦: knowledge/*.md 里不得出现 state/ 路径(政策原文里的 `state/` 字样除外)
kdir = os.path.join(root, "knowledge")
kfiles = [os.path.join(kdir, x) for x in sorted(os.listdir(kdir)) if x.endswith(".md")]
dirty = [os.path.basename(f) for f in kfiles if "state/" in open(f, encoding="utf-8").read().replace("`state/`", "")]
if dirty: bad(f"KB 引用了 state/ 路径(须与证据解耦,只写'来源: <目标名> <日期>'): {dirty}")
else: ok(f"KB 解耦: knowledge/ 全部 {len(kfiles)} 个文件无 state/ 路径引用")
# KB 应"即查即用":不该出现 `<目标名>/artifacts/…` 这类目标内路径(存量先只警告,见 LOOP ① 知识)
_legacy = sum(len(re.findall(r"[A-Za-z0-9_.-]+/artifacts/", open(f, encoding="utf-8").read())) for f in kfiles)
if _legacy: print(f"  ⚠ KB 里有 {_legacy} 处目标内 `artifacts/` 路径(legacy:违反'即查即用',建议逐步换来源标签)")

# ---------- ①e KB 结构体检(并发/误改防护) ----------
# 判据: 围栏成对 / 无重复小标题 / 无 NUL 字节 / 无 CRLF / 无合并冲突标记 / 非截断(末行有换行)
# 出处: 2026-10-04 用户对"多会话并发写 knowledge/ 可能改坏"的关切;项挂在既有闸门上,不新增命令
_kbm = os.path.join(root, "knowledge")
if os.path.isdir(_kbm):
    _kbp = []
    for _n in sorted(os.listdir(_kbm)):
        if not _n.endswith(".md"): continue
        _b = open(os.path.join(_kbm, _n), "rb").read(); _t = _b.decode("utf-8", errors="replace")
        _ln = _t.split("\n"); _hh = re.findall(r"(?m)^## (.+)$", _t)
        _iss = []
        if sum(1 for _l in _ln if _l.strip().startswith("```")) % 2: _iss.append("围栏奇数")
        if [h for h in set(_hh) if _hh.count(h) > 1]: _iss.append("重复小标题")
        if b"\x00" in _b: _iss.append("含NUL")
        if b"\r\n" in _b: _iss.append("CRLF")
        if re.search(r"(?m)^(<<<<<<<|=======|>>>>>>>)", _t): _iss.append("冲突标记")
        if not _t.endswith("\n"): _iss.append("末行无换行")
        if _iss: _kbp.append(f"{_n}: {'/'.join(_iss)}")
    if _kbp: bad(f"KB 结构体检: {'; '.join(_kbp)}")
    else: ok(f"KB 结构体检: {len([n for n in os.listdir(_kbm) if n.endswith('.md')])} 个文件无围栏/标题/NUL/CRLF/冲突/截断问题")

# ---------- ①b 引用完整性(断链必须就地标注) ----------
KIND = ("已释放", "需按配方重建", "重下见", "SOURCE_URL", "已随目标清理", "公式重建")
stroot = os.path.join(root, "state")
tgts = [d for d in os.listdir(stroot) if os.path.isdir(os.path.join(stroot, d)) and d != "artifacts"]
# ↑ 排除叫 `artifacts` 的目录: 它会被当成目标名 ⇒ 把「相对引用 `artifacts/…`」误判成断链(2026-10-04 实测: 26 处全假)
refpat = re.compile(r"(?:\.\./)?(" + "|".join(map(re.escape, tgts)) + r")/([A-Za-z0-9._/*-]+)")
scanned = [os.path.join(stroot, d, "notes.md") for d in tgts if os.path.isfile(os.path.join(stroot, d, "notes.md"))]
for d in tgts:
    rdir = os.path.join(stroot, d, "report")
    if os.path.isdir(rdir):
        scanned += [os.path.join(rdir, x) for x in os.listdir(rdir) if x.endswith(".md")]
dangle = []
for f in scanned:
    lines = open(f, encoding="utf-8", errors="ignore").read().split("\n")
    for i, ln in enumerate(lines):
        for m in refpat.finditer(ln):
            p = os.path.join(stroot, m.group(1), m.group(2).rstrip("/"))
            if "*" in p:
                par, pre = os.path.split(p.split("*")[0])
                exists = os.path.isdir(par) and any(x.startswith(os.path.basename(pre)) for x in os.listdir(par))
            else:
                exists = os.path.exists(p)
            if exists: continue
            if any(k in "\n".join(lines) for k in KIND): continue   # 文件级注明即可
            dangle.append((f, f"{m.group(1)}/{m.group(2)[:40]}"))
if dangle:
    _fs = sorted(set(os.path.relpath(f, root) for f, _ in dangle))
    bad(f"断链引用未标注({len(dangle)} 处 / {len(_fs)} 个文件): 需在文件内注明'已释放/需按配方重建/重下见 SOURCE_URL.txt' → {_fs[:6]}")
else: ok(f"引用完整性: {len(scanned)} 个文件里无'未标注断链'")

# ---------- ①d 容器化复现包:用了 docker 就必须带 Dockerfile(或 compose) ----------
# 判据(LOOP「容器化复现包」/ 模板「容器化复现」): 复现依赖容器 ⇒ 包内必须带 Dockerfile(或 docker-compose.yml)+ 一行 build/run。
_att2 = os.path.join(state, "report", "attachments")
if os.path.isdir(_att2):
    _nod: list = []
    for _d in sorted(os.listdir(_att2)):
        _p = os.path.join(_att2, _d)
        if not os.path.isdir(_p): continue
        _uses = False
        for _f in sorted(os.listdir(_p)):
            if not _f.endswith((".sh", ".py", ".md")): continue
            try: _t = open(os.path.join(_p, _f), encoding="utf-8", errors="ignore").read()
            except Exception: continue
            if re.search(r"docker\s+(exec|run|build|import)", _t): _uses = True; break
        if not _uses: continue
        if not any(x.startswith(("Dockerfile", "docker-compose")) for x in os.listdir(_p)): _nod.append(_d)
    if _nod:
        bad(f"容器化复现包缺 Dockerfile/compose(LOOP「容器化复现包」): {_nod}")
    else:
        ok("容器化复现包: 用到 docker 的包均带 Dockerfile/compose")

# ---------- ①c PoC 依赖(运行载体/派生物判据:删掉它 poc.sh 就跑不起来) ----------
# 判据(LOOP「④/⑤ 的判据」):poc.sh 引用到的目标内路径**必须存在**;缺 = 运行载体被释放 ⇒ FAIL。
_att = os.path.join(state, "report", "attachments")
_pocdeps = []
if os.path.isdir(_att):
    _tname = os.path.basename(state)
    for _d in sorted(os.listdir(_att)):
        _p = os.path.join(_att, _d)
        if not os.path.isdir(_p): continue
        for _f in sorted(os.listdir(_p)):
            if not _f.startswith(("poc", "run", "exploit")): continue
            try: _txt = open(os.path.join(_p, _f), encoding="utf-8", errors="ignore").read()
            except Exception: continue
            for _m in re.finditer(r"\$(?:\{)?T\}?/([A-Za-z0-9_.-]+)|state/" + re.escape(_tname) + r"/([A-Za-z0-9_.-]+)", _txt):
                _sub = _m.group(1) or _m.group(2)
                if _sub and _sub not in ("report", "notes.md"):
                    _pocdeps.append((_d, _sub))
    _missing = sorted({(a, b) for a, b in _pocdeps if not os.path.exists(os.path.join(state, b))})
    if _missing:
        bad("PoC 依赖的路径缺失(按 LOOP 判据=运行载体不该被判②释放): " + "; ".join(f"{a}→{b}" for a, b in _missing[:6]))
    elif _pocdeps:
        _dirs = sorted({b for _, b in _pocdeps})
        ok(f"PoC 依赖完整: {len(set(a for a,_ in _pocdeps))} 个 PoC 引用 {len(_dirs)} 个目标内路径,全部存在({', '.join(_dirs[:6])})")
    else:
        ok("PoC 依赖: 无 poc/run/exploit 脚本引用目标内路径(跳过)")

# ---------- ② A2 覆盖度 ----------
notes = os.path.join(state, "notes.md")

def pool_block(ntext):
    """取 notes 中最后一处含'假设池'的标题起的块(到下一个同级/更高级标题)"""
    hs = list(re.finditer(r"(?m)^(#{2,4})[^\n]*假设池[^\n]*$", ntext))
    if not hs:
        return ""
    pref = [x for x in hs if "唯一队列" in x.group(0)]   # 优先规范标签节
    h = pref[0] if pref else hs[0]; lvl = len(h.group(1)); start = h.end()
    nxt = re.search(r"(?m)^#{1,%d} " % lvl, ntext[start:])
    return ntext[start:start + (nxt.start() if nxt else len(ntext))]

def a2_unjudged(asf_path):
    """A2 表中 disposition=未判定 的行,返回其入口点编号"""
    if not os.path.isfile(asf_path):
        return []
    ids = []
    for ln in open(asf_path, encoding="utf-8"):
        if "未判定" in ln and ln.lstrip().startswith("|"):
            cells = [c.strip().strip("*` ") for c in ln.strip().strip("|").split("|")]
            if cells and cells[0]:
                ids.append(cells[0])
    return ids

def pool_open(blk):
    """池中未处理条目: 以 - 开头的条目段落内不含任何结论/降级关键词"""
    ents, cur = [], None
    for ln in blk.split("\n"):
        if re.match(r"^\s*[-*]\s", ln):
            if cur is not None: ents.append(cur)
            cur = ln
        elif cur is not None:
            cur += "\n" + ln
    if cur: ents.append(cur)
    kw = ("定论", "No finding", "已排除", "降级", "成立", "Blocked", "结论", "已收口", "MISS", "已并入")
    out = []
    for e in ents:
        m = re.search(r"状态\s*\**\s*[:：]?\s*\**\s*([^\n*]*)", e)
        st = m.group(1) if m else ""
        if re.search(r"未处理|进行中|未开始|TODO", st):     # 显式"未处理" ⇒ 队列项
            handled = False
        elif re.search(r"定论|已收口|No finding|已排除|降级|Blocked|已并入|完成", st):
            handled = True
        else:                                               # 状态行缺结论词 ⇒ 退回关键词扫描
            handled = any(k in e for k in kw)
        if not handled:
            out.append(e.strip().split("\n")[0][:80])
    return out
def _db_surface_counts():
    """权威状态优先:surfaces 表按 disposition 计数;找不到(未迁移)返回 None。"""
    for _cand in (os.path.join(state, "state", "fwcore.sqlite3"),
                  os.path.join(root, "state", "fwcore.sqlite3")):
        if not os.path.isfile(_cand):
            continue
        try:
            import sqlite3 as _s
            _c = _s.connect(_cand)
            _t = _c.execute("SELECT id FROM targets WHERE name=?", (target,)).fetchone()
            if not _t:
                continue
            _r = _c.execute("SELECT disposition, COUNT(*) FROM surfaces"
                            " WHERE target_id=? GROUP BY disposition", (_t[0],)).fetchall()
            if _r:
                return {d: n for d, n in _r}
        except Exception:
            continue
    return None
_dbc = _db_surface_counts()

a2 = ""
if os.path.isfile(notes):
    t = open(notes, encoding="utf-8").read()
    _asf = os.path.join(state, "report", "attack-surface.md")
    _pb = pool_block(t); _un = a2_unjudged(_asf if os.path.isfile(_asf) else notes)
    if _pb:
        # 降级理由合规: 不得是"成本类";必须是"判定类"
        _bad, _okr = [], []
        for _e in re.findall(r"(?ms)^- \*\*H-\d+.*?(?=^- \*\*H-\d+|\Z)", _pb):
            _st = (re.search(r"状态\s*\**\s*[:：]?\s*\**\s*([^\n*]*)", _e) or [None, ""])[1] if True else ""
            if "降级" not in _st: continue   # 只查"状态=降级"的条目(避免历史措辞误报)
            _cost = re.search(r"成本|太重|太贵|复杂|预算|时间不够|工作量大", _e)
            _judg = re.search(r"判据|不可达|上游|设计内|已排除|证伪|需工作区外|需用户|真机", _e)
            _onlyrounds = re.search(r"(?:\d|两|几)\s*轮", _e) and not re.search(r"判据", _e)
            if (_cost and not _judg) or _onlyrounds:
                _bad.append(_e.strip().split("\n")[0][:70])
        if _bad: bad(f"降级理由违规(成本类不算理由,应保持未处理续做): {_bad[0]}")
        elif "降级" in _pb: ok("降级理由合规(判定类)")
        _op = pool_open(_pb)
        # 状态一致性: 收尾态下"下一步第一条"不得再指向某个 H 条目(除非写成可选/需裁决)
        _last = ""
        for _ln in open(notes, encoding="utf-8").read().split("\n"):
            if "下一步第一条" in _ln: _last = _ln
        if _last and not _op and re.search(r"H-\d+", _last) and not re.search(r"可选|需用户|待用户|裁决", _last):
            bad(f"状态矛盾: 收尾态但'下一步第一条'仍指向假设池条目 → {_last.strip()[:80]}")
        elif _last: ok("状态一致性: '下一步第一条'与池状态不矛盾")
        print(f"队列一致性: A2 未判定 {len(_un)} 行 / 假设池未处理 {len(_op)} 条"
              + ("(应为 0:未判定面须先入池)" if len(_un) and not _op else ""))
        miss = [i for i in _un if i not in _pb]
        if miss: print(f"  ⚠ 未入池的未判定面: {miss[:8]} (收尾时按 --closure 判定 FAIL)")
    else:
        print("队列一致性: notes 中未找到「假设池」节(唯一队列缺失)")
    m = re.search(r"^###\s*2\.3\b(.*?)(?=^###\s|\Z)", t, re.M | re.S)
    a2 = m.group(1) if m else ""
    if not a2:  # 收尾后 surface map 已移动到 report/attack-surface.md ⇒ 以它为准
        _asf2 = os.path.join(state, "report", "attack-surface.md")
        if os.path.isfile(_asf2):
            _t2 = open(_asf2, encoding="utf-8").read()
            _m2 = re.search(r"^###\s*2\.3\b(.*?)(?=^###\s|^##\s|\Z)", _t2, re.M | re.S)
            a2 = _m2.group(1) if _m2 else _t2
# 判据: A2 的权威载体必须存在(notes §2.3 或 report/attack-surface.md);两者都缺 ⇒ 无法核对"A2 未判定 0"
#        ⇒ 直接 FAIL(否则会出现"空集假绿":缺文件时算出 0 行,让会话误以为可以宣布队列清空)
if not a2.strip() and _dbc is None:
    bad("缺 A2 载件: 既无权威 surfaces 记录,也无 notes §2.3 / report/attack-surface.md")

buckets = {"Vulnerable": [], "Part": [], "NoFind": [], "Unjudged": [], "Out": [], "UNPARSED": []}
# 布局自适应:【逐表】解析 —— 每张表按自己的表头认"入口列/判定列"(各目标 attack-surface.md 列序不同:
# 有的首位是 `#` 序号列;entry 可能叫"入口点/入口/端口/Service/Path")。文本里可能有多张表(A1 服务表 + A2 判定表),
# 只解析“含判定列”的那些表;全篇都没有判定列 ⇒ 明确报出来(而不是把序号列当判定列误报 UNPARSED ✗)。
DISP_RE = re.compile(r"disposition|处置|判定", re.I)
ENTRY_RE = re.compile(r"入口点|入口|端口|服务|service|path|路径|组件|entry", re.I)
def _rows_by_table(text):
    """把 markdown 表切分:(header_cells, body_rows) 列表"""
    out, hdr, body = [], None, []
    for ln in text.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):        # 分隔行 ⇒ 表体开始
                continue
            if hdr is None:
                hdr = cells
            else:
                body.append(cells)
        else:
            if hdr is not None:
                out.append((hdr, body)); hdr, body = None, []
    if hdr is not None: out.append((hdr, body))
    return out
def _parse_rows(text):
    """从一段 markdown 里抽出 (入口, 判定) 行(只认含判定列的表)"""
    rows = []
    for _hdr, _body in _rows_by_table(text):
        if not any(DISP_RE.search(c) for c in _hdr): continue      # 只认含判定列的表
        _d = next(k for k, c in enumerate(_hdr) if DISP_RE.search(c))
        _e = next((k for k, c in enumerate(_hdr) if ENTRY_RE.search(c) and k != _d), None)
        if _e is None:
            _e = next((k for k, c in enumerate(_hdr) if c and not c.startswith("#") and k != _d), 0)
        for cells in _body:
            if len(cells) <= max(_e, _d): continue
            if not cells[_e] or not cells[_d]: continue
            rows.append((cells[_e], cells[_d]))
    return rows
_rows = [] if _dbc is not None else _parse_rows(a2)
if not _rows:      # notes 的 §2.3 表为空/不含判定列(或已收尾改成指针)⇒ 回退到权威文件
    # 出处:2026-10-04 QNAP-TS-464 收尾 —— notes §2.3 为指针 ⇒ 旧版不回退,覆盖度数成 0,而文件里有 11 行
    _asf3 = os.path.join(state, "report", "attack-surface.md")
    if os.path.isfile(_asf3):
        _rows = _parse_rows(open(_asf3, encoding="utf-8").read())

GROUP_RE = re.compile(r"^\s*(?:见|并入|拆分为|同上|另见|n/?a|—|--|/)")
_group = 0
for entry, disp in _rows:
    _d_plain = re.sub(r"[*`~]", "", disp)          # 去掉 markdown 强调再判
    if GROUP_RE.search(_d_plain) or re.search(r"见\s*(?:行)?\s*[0-9]", _d_plain) or re.fullmatch(r"[0-9a-z,\s–-]+", _d_plain or ""):
        _group += 1; continue                       # 分组/指针行(父项,无自身判定)⇒ 不计
    if   re.search(r"Vulnerable|VR-\d", disp): buckets["Vulnerable"].append(entry)
    elif re.search(r"Out of scope|范围外|暂缓", disp): buckets["Out"].append(entry)
    elif re.search(r"部分", disp): buckets["Part"].append(entry)
    elif re.search(r"No finding|已审", disp): buckets["NoFind"].append(entry)
    elif re.search(r"未审|未判定", disp): buckets["Unjudged"].append(entry)
    else: buckets["UNPARSED"].append(entry)
if _dbc is not None:
    buckets = {"Vulnerable": [], "Part": [], "NoFind": [], "Unjudged": [], "Out": [], "UNPARSED": []}
    _map = {"vulnerable": "Vulnerable", "no_finding": "NoFind",
            "out_of_scope": "Out", "unjudged": "Unjudged"}
    for _d, _n in _dbc.items():
        buckets[_map.get(_d, "UNPARSED")] += [_d] * _n
    print(f"覆盖度来源: 权威 surfaces 表 {_dbc}")
if a2 or _dbc is not None:
    total = sum(len(v) for v in buckets.values())
    print(f"A2 覆盖度(解析 {total} 个入口点行" + (f";另有 {_group} 个分组/指针行未计" if _group else "") + f";权威 = state/{target}/report/attack-surface.md):")
    for k in ("Vulnerable", "Part", "NoFind", "Unjudged", "Out", "UNPARSED"):
        print(f"   {k:<10} {len(buckets[k])}" + ("" if not buckets[k] else "  ← " + "; ".join(e[:46] for e in buckets[k])))
    print(f"覆 盖 度 摘 要(可直接粘): Vulnerable={len(buckets['Vulnerable'])} Part={len(buckets['Part'])} "
          f"NoFind={len(buckets['NoFind'])} Unjudged={len(buckets['Unjudged'])} OutOfScope={len(buckets['Out'])}"
          + (f" | 未解析={len(buckets['UNPARSED'])}" if buckets["UNPARSED"] else ""))
    if buckets["UNPARSED"]: bad("§2.3 有 %d 行判定列无法归类 → 看 disposition 写法是否用了本文档承认的四种(Vulnerable/No finding/Out of scope/未判定)" % len(buckets["UNPARSED"]))
else:
    print("A2: 未找到 notes §2.3 表(跳过)")

# ---------- ③ finding 包 ----------
rd = os.path.join(state, "report")
SKIP = {"INDEX.md", "CVE-request-mitre.md", "attack-surface.md", "CLOSURE.md"}
NEED = ["Summary", "Root Cause", "Steps to Reproduce", "Evidence"]
advs = [f for f in sorted(os.listdir(rd)) if f.endswith(".md") and f not in SKIP] if os.path.isdir(rd) else []
if not advs: print("finding: 0 个 advisory(本轮无 finding)")
for adv in advs:
    name = adv[:-3]
    t = open(os.path.join(rd, adv), encoding="utf-8").read()
    miss = [s for s in NEED if not re.search(r"^##\s+" + re.escape(s), t, re.M)]
    if miss: bad(f"{adv}: advisory 缺节 {miss}")
    links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", t)
    if not links: bad(f"{adv}: 无 markdown 图链(有动态验证却无图链 = 材料未完成)")
    stray = [ln.strip()[:70] for ln in t.splitlines()
             if "attachments/" in ln and "](attachments/" not in ln]
    if stray: bad(f"{adv}: 附件路径以纯文本出现(图片只允许 markdown 链接): {stray[0]}")
    for L in links:
        if os.path.isfile(os.path.join(rd, L)): ok(f"{adv}: 图链可解析 → {L}")
        else: bad(f"{adv}: 图链指向不存在的文件 → {L}")
        d = os.path.dirname(os.path.join(rd, L))
        if not os.path.isfile(os.path.join(d, "shots.json")): bad(f"{adv}: {os.path.basename(d)}/ 缺 shots.json")
        if not [f for f in os.listdir(d) if f.endswith(".png.provenance")]: bad(f"{adv}: {os.path.basename(d)}/ 缺 *.png.provenance")
        # 包必须自含:符号链接 = 指向包外(提交后必坏)✗;README 缺失 = 别人跑不起来 ✗
        _links = []
        for _r, _ds, _fs in os.walk(d):
            for _n in _ds + _fs:
                _p2 = os.path.join(_r, _n)
                if os.path.islink(_p2): _links.append(os.path.relpath(_p2, d) + " -> " + os.readlink(_p2))
        if _links: bad(f"{adv}: {os.path.basename(d)}/ 含符号链接(包不自含 ⇒ 压缩/上传后必坏): " + "; ".join(_links[:3]) + " ⇒ 删链接,让 PoC 用参数(默认 ./rootfs)并在 README 写明怎么准备")
        if not any(f.lower().startswith("readme") for f in os.listdir(d)):
            bad(f"{adv}: {os.path.basename(d)}/ 缺 README(须说明依赖怎么准备、怎么跑)")
    if not miss and links and all(os.path.isfile(os.path.join(rd, L)) for L in links):
        ok(f"{adv}: 骨架 {len(NEED)}/{(len(NEED))} 节 + 图链 + 包完整")

# ---------- ④ 收尾前置(仅 --closure) ----------
if closure:
    asf = os.path.join(rd, "attack-surface.md")
    if os.path.isfile(asf):
        t2 = open(asf, encoding="utf-8").read()
    else:
        # 收尾前 surface map 还活在 notes 里 ⇒ 也要按它判(否则检查空转)
        t2 = open(notes, encoding="utf-8").read() if os.path.isfile(notes) else ""
    rows = [ln for ln in t2.splitlines() if re.match(r"^\|\s*\d+[a-z]?", ln)]
    unde = [ln[:70] for ln in rows if "未判定" in ln]
    # 队列不变量: A2 未判定面 ⊆ 假设池(唯一队列);池中未处理条目不许无解释
    _pb = pool_block(open(notes, encoding="utf-8").read() if os.path.isfile(notes) else ""); _un = a2_unjudged(asf if os.path.isfile(asf) else notes)
    if not _pb:
        bad("notes 缺「假设池」节 —— 唯一队列缺失,无法判定'还有没打的'")
    else:
        _miss = [i for i in _un if i not in _pb]
        if _miss: bad(f"队列不变量: 这些 A2 未判定面不在假设池里(未入队): {_miss[:8]}")
        else: ok(f"队列不变量: A2 未判定 {len(_un)} 行均在假设池内")
        _op = pool_open(_pb)
        if _op: bad(f"假设池有 {len(_op)} 条未处理(收尾须逐条给结论或显式降级): {_op[0]}")
        elif _pb: ok("假设池: 无未处理条目")
    stale = [ln[:70] for ln in rows
             if re.search(r"无(任何)?监听|面不存在", ln) and "对账" not in ln]
    if unde: bad(f"attack-surface.md 仍有 '未判定' 行({len(unde)}) → 三选一必填: {unde[0]}")
    elif rows: ok(f"attack-surface.md: {len(rows)} 行入口点,无 '未判定'")
    if stale:
        bad(f"误判回溯(谓词: '无监听/面不存在'且无复核引用)命中 {len(stale)} 行未处理: {stale[0]}")
        if "误判回溯" not in open(notes, encoding="utf-8").read():
            bad("命中误判回溯却缺 notes「误判回溯」节(须记 错误类 + 谓词 + 命中集 + 处置)")
        elif rows: ok("误判回溯: 命中行已登记谓词与处置")
    elif rows: ok("误判回溯: 谓词『无监听/面不存在』无未处理命中")

    if advs:
        nt = open(notes, encoding="utf-8").read() if os.path.isfile(notes) else ""
        # ③ 就绪态清单(A1 的输入): 存在 + 带就绪判据 + 端口在 A2 表里能找到
        import glob as _glob
        cand = [f for pat in ("*listening*", "*liveness*", "*ready*", "*listening-sockets*")
                for f in _glob.glob(os.path.join(state, "artifacts/recon", pat))]
        if not cand:
            # 两档口径:未收尾 ⇒ 实测口径(必须有清单);已收尾 ⇒ 收尾口径(CLOSURE 声明取得情况即可,文件可已释放)
            _cl = os.path.join(state, "report", "CLOSURE.md")
            if os.path.isfile(_cl):
                _ct = open(_cl, encoding="utf-8", errors="ignore").read()
                if re.search(r"就绪态[^\n]{0,40}(取得|不可得|静态候选|未实测)", _ct):
                    ok("就绪态清单: 收尾口径 —— CLOSURE 已声明取得情况(实测文件可已释放)")
                else:
                    bad("收尾口径: CLOSURE 未声明就绪态清单的取得情况(应写'取得'或'未取得+原因',并入 Untested surface)")
            else:
                bad("缺就绪态清单(artifacts/recon/*listening*|*ready*): 未收尾目标必须留实测清单,A1 输入不能靠事后对账")
        else:
            txt = "".join(open(f, encoding="utf-8", errors="ignore").read() for f in cand)
            if not re.search(r"ready|running|就绪|判据", txt):
                bad(f"就绪态清单 {os.path.basename(cand[0])} 里没有就绪判据/取值 → 无法证明「数的时候它起完了」")
            else:
                asf_txt = open(asf, encoding="utf-8").read() if os.path.isfile(asf) else ""
                body = "\n".join(l for l in txt.split("\n") if not l.lstrip().startswith("#"))  # 忽略注释(命令行里的宿主端口)
                ports = {m.group(1) for m in re.finditer(r":(\d{2,5})\b", body) if int(m.group(1)) in (80, 443, 161) or int(m.group(1)) > 1024}
                miss = sorted(p for p in ports if p not in asf_txt)
                if miss: bad(f"A1 完整性: 就绪态清单里这些端口在 attack-surface.md 找不到(未判定): {miss[:12]}")
                else: ok(f"A1 完整性: 就绪态清单 {len(ports)} 个端口均在 A2 表内(带就绪判据)")
                # location 级(代理/路由): 清单里的 HTTP 路径也要能在 A2 表里找到
                _fs = ("/data/", "/etc/", "/usr/", "/var/", "/tmp/", "/lib/", "/opt/", "/bin/", "/sbin/", "/proc/", "/sys/")
                ipaths = {m.group(0).rstrip(".,;:") for m in re.finditer(r"/[a-zA-Z0-9_][a-zA-Z0-9_./*-]{3,}", body)}
                ipaths = {q for q in ipaths if q.count("/") >= 2 and not q.startswith(_fs)}
                ipaths = {q for q in ipaths if any(q.startswith(pre) for pre in ("/api/", "/proxy/", "/internal/", "/v", "/ws", "/guest/", "/upload", "/ucs/", "/proxy/"))} if ipaths else set()
                if ipaths:
                    pmiss = sorted(q for q in ipaths if q not in asf_txt)
                    if pmiss: bad(f"A1 location 完整性: 清单里的这些路径在 A2 表找不到(未判定): {pmiss[:10]}")
                    else: ok(f"A1 location 完整性: {len(ipaths)} 条路径均在 A2 表内")
                else:
                    bad("A1 location 完整性: 清单里没有任何 HTTP 路径(location/路由)记录 → 只导了端口层,未做分层全量导出")
        sec = re.search(r"(?ms)^##\s*论点—证据表.*?(?=^##\s|\Z)", nt)
        gaps = [l for l in (sec.group(0) if sec else "").splitlines() if "缺口" in l]
        unresolved = [l for l in gaps if not re.search(r"限定|降级|Blocked|已闭合|不升级|不构成|不产|无法|未见|未证|备查", l)]
        if unresolved: bad(f"论点—证据表有未处理缺口({len(unresolved)}): {unresolved[0].strip()[:70]}")
        elif gaps: ok(f"论点—证据表: {len(gaps)} 处缺口均已限定/降级")
    else:
        print("收尾前置: 无 finding ⇒ 误判回溯/缺口检查跳过")

# ---------- ④c 轮结构(一条 H 一轮;finding 即收口) ----------
# 判据(LOOP「一条 H 一轮」): 最后一个轮次节内出现 ≥2 个 `本轮目标: H-xx` ⇒ 把后续工作塞进了同一轮 ✗
#   进行中的目标:日常跑 ⚠ 提示,--closure ⇒ FAIL;已收尾目标(有 CLOSURE)豁免。
if os.path.isfile(notes) and not os.path.isfile(os.path.join(state, "report", "CLOSURE.md")):
    _n4 = open(notes, encoding="utf-8").read().split("\n")
    _r4 = 0
    for _i4, _l4 in enumerate(_n4):
        if re.match(r"^#{2,3}\s*§?\d+", _l4) and not re.search(r"假设池|排除清单", _l4): _r4 = _i4
    _t4 = "\n".join(_n4[_r4:])
    _goals = re.findall(r"本轮目标\s*[:：]\s*\**\s*(H-\d+)", _t4)
    if len(_goals) >= 2:
        _msg = f"最后一个轮次节里声明了 {len(_goals)} 个『本轮目标』({', '.join(_goals)})⇒ **一条 H 一轮**;若其中已有 finding ⇒ 该 finding 即本轮收口,后续工作另起一轮"
        bad("轮结构: " + _msg) if closure else print("  ⚠ " + _msg)
    elif len(_goals) == 1:
        ok(f"轮结构: 最后轮次节只声明 1 个目标({_goals[0]})✓")
    # 尾部精准核(2026-10-03 加;口径同 LOOP「轮的声明格式」):
    #   ①收口无对应目标 ②同一 H 重复收口 ③收口行不带证据引用
    #   ⚠ 不做"每个 H 都要挂证据"的全量核 —— 2026-10-03 度量: 它在 5 个已收尾目标上误报 32 处(把"池表/新面去向里提到降级"当收口)
    _cl4 = re.findall(r"(?m)^>?\s*本轮\s*[:：]\s*\**\s*(H-\d+)", _t4)
    _dup4 = sorted({h for h in _cl4 if _cl4.count(h) > 1})
    _orph4 = sorted({h for h in _cl4 if h not in _goals})
    _l4s = _t4.split("\n")
    _noev4 = [x.strip()[:40] for i, x in enumerate(_l4s)
              if re.match(r"^>?\s*本轮\s*[:：]\s*\**\s*H-\d+", x)
              and not re.search(r"§|证据|判据|HIT:|MISS|命令|结果|curl|qemu|unblob|\.py|\.sh|复现|截图|日志", " ".join(_l4s[i:i+3]))]
    if _orph4 or _dup4 or _noev4:
        _msg4 = "尾部: " + "; ".join(x for x in [
            f"无目标的收口 {_orph4}" if _orph4 else "",
            f"同一 H 重复收口 {_dup4}" if _dup4 else "",
            f"收口行缺证据引用 {_noev4[:2]}" if _noev4 else ""] if x)
        bad("轮结构: " + _msg4) if closure else print("  ⚠ " + _msg4)
    # 生成侧:有收口就必须有「新观察」行(双输出的后半;口径见 LOOP「轮的声明格式」)
    if _cl4 and not re.search(r"(?m)^\s*>?\s*\**\s*新观察\s*[:：]", _t4):
        _msg5 = "尾部有 " + str(len(_cl4)) + " 条收口,但无『新观察』行 ⇒ 双输出缺后半(新观察≠无就升 H 入池;写『无』要带判据)"
        bad("轮结构: " + _msg5) if closure else print("  ⚠ " + _msg5)

# ---------- ④b 三镜刷新(面/格收口时 1 行,下限每 3 轮;进行中的目标)= "队列刷新"的实际压力点 ----------
# 判据(LOOP「三镜刷新」): **最近 3 轮**内应有一行 `三镜: 机制=…|作者=…|状态=…|新面=…`;
#   `新面=H-xx` ⇒ 该 H 必须在假设池里(当场入池)。日常跑只提示(⚠),--closure 才 FAIL;已收尾目标(有 CLOSURE)豁免。
if os.path.isfile(notes) and not os.path.isfile(os.path.join(state, "report", "CLOSURE.md")):
    _raw3 = open(notes, encoding="utf-8").read()
    _nl3 = _raw3.split("\n")
    _rs3s = [ _i3 for _i3, _l3 in enumerate(_nl3)
              if re.match(r"^#{2,3}\s*§?\d+", _l3) and not re.search(r"假设池|排除清单", _l3) ]
    _rs3 = _rs3s[-3] if len(_rs3s) >= 3 else (_rs3s[0] if _rs3s else 0)   # 最近 3 轮
    _tail3 = "\n".join(_nl3[_rs3:])
    _m3 = None
    for _l3 in _tail3.split("\n"):
        if re.match(r"^\s*三镜\s*[:：]", _l3): _m3 = _l3
    _ok3 = bool(_m3) and all(k in _m3 for k in ("机制", "作者", "状态", "新面"))
    _pool3h = set(re.findall(r"H-\d+", pool_block(_raw3)))
    _dest3 = re.findall(r"H-\d+", _m3) if _m3 else []
    _miss3 = [h for h in _dest3 if h not in _pool3h]
    if not _ok3:
        _msg = "最近 3 轮内没有『三镜: 机制=…|作者=…|状态=…|新面=…』(面/格收口时 1 行,下限每 3 轮,见 LOOP「三镜刷新」)"
        bad("收尾前置: " + _msg) if closure else print("  ⚠ " + _msg)
    elif _miss3:
        _msg = f"三镜的『新面』提到 {_miss3} 但不在假设池 ⇒ 队列冻结(须当场入池)"
        bad("收尾前置: " + _msg) if closure else print("  ⚠ " + _msg)
    else:
        ok(f"三镜刷新: 最近 3 轮有三镜行,新面={'入池 ' + ','.join(_dest3) if _dest3 else '无(判据)'}")

print("=== derived_check: " + ("PASS" if fail == 0 else f"FAIL x{fail}") + " ===")
sys.exit(1 if fail else 0)
PYEOF
