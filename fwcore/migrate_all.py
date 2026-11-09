"""批量迁移:把历史目标的 Markdown 研究状态导入 SQLite。

这是一次性**数据导入**,不是运行时回退 —— 运行路径只读 DB(`derived_check` 缺 DB 直接 FAIL)。

原则
- 原文逐字节进 `documents`(sha256 去重);迁移后原文件保留为只读对照;
- 能机械确定的进结构化表(面/假设/尝试/观察/证据/复核/结论/三镜/日志);
- 不从模糊措辞推断"漏洞成立/否证" —— 只有**显式状态行**才映射 verdict,其余标 needs_review;
- 幂等:确定性 id + `INSERT OR IGNORE`;重复执行不新增行;
- 每个目标迁移后用 `verify_target()` 复核行数与原文一致。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3

from . import db as _db

SKIP_DIRS = {"_scratch", "ida"}
MD_TOP = ("notes.md",)
MD_REPORT = ("report/attack-surface.md",)
RE_BULLET_H = re.compile(r"^- \*\*(H-?\d+|N\d+)\b(.*)$")
# "H-11 的最终口径/定论/状态回滚…" 是对既有假设的**状态重述**,不是新假设
RE_RESTATE = re.compile(r"^\s*(的|定论|结论|判定|状态|收口|维持|已|仍|→|含|注|说明|本轮|新观察|改为|更新|=)")
RE_STATUS = re.compile(r"状态\s*[:：]\s*(.+)$")
RE_DATE = re.compile(r"20\d\d-\d\d-\d\d")
RE_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")


# ---------------- 基础工具 ----------------

def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="seconds")


def _det(prefix: str, *parts: str) -> str:
    h = hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8", "replace")).hexdigest()[:16]
    return f"{prefix}-{h}"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _date_of(text: str, fallback: str) -> str:
    m = RE_DATE.search(text or "")
    return m.group(0) + "T00:00:00+00:00" if m else fallback


def list_targets(root: str) -> list[str]:
    sdir = os.path.join(root, "state")
    out = []
    try:
        entries = sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []
    except OSError:
        return []
    for name in entries:
        if name in SKIP_DIRS or not os.path.isdir(os.path.join(sdir, name)):
            continue
        if os.path.isfile(os.path.join(sdir, name, "notes.md")):
            out.append(name)
    return out


def _meta(name: str, notes: str) -> dict:
    ver = re.search(r"(\d+\.\d+(?:\.\d+){0,3})", name)
    if not ver:      # 目录名没有版本时,从笔记里的"版本/固件/V"行取
        ver = re.search(r"(?i)(?:版本|firmware|固件|\bfw\b|\bv)\s*[:：\-]?\s*[Vv]?"
                        r"(\d+\.\d+(?:\.\d+){0,3})", notes)
    arch = next((a for a in ("arm64", "aarch64", "x86_64", "mips", "mipsel") if a in name), None)
    vendor = name.split("-")[0]
    product = re.sub(r"-\d+\.\d+.*$", "", name)
    return {
        "vendor": vendor,
        "product": product,
        "version": ver.group(1) if ver else None,
        "arch": arch,
    }


# ---------------- Markdown 结构 ----------------

def _tables(text: str) -> list[dict]:
    """切 markdown 表:返回 [{header, rows, line}]。"""
    out, header, rows, start = [], None, [], 0
    for i, ln in enumerate(text.split("\n"), 1):
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if header is None:
                header, start = cells, i
            elif RE_TABLE_SEP.match(ln.strip()):
                continue
            else:
                rows.append(cells)
        else:
            if header is not None:
                out.append({"header": header, "rows": rows, "line": start})
                header, rows = None, []
    if header is not None:
        out.append({"header": header, "rows": rows, "line": start})
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", s or "")).strip()


def _disp_of(s: str) -> str | None:
    s = s or ""
    if re.search(r"Vulnerable|VR-\d", s):
        return "vulnerable"
    if re.search(r"Out of scope|范围外|不在范围", s):
        return "out_of_scope"
    if re.search(r"No finding|已审|无发现|无漏洞|已排除", s):
        return "no_finding"
    if re.search(r"未判定|未审|待定|未覆盖", s):
        return "unjudged"
    return None


def _verdict_of(status: str) -> tuple[str, str | None]:
    s = status or ""
    if re.search(r"成立|vulnerable|VR-\d", s, re.IGNORECASE):
        return "concluded", "vulnerable"
    if re.search(r"定论|结论", s, re.IGNORECASE) and re.search(
            r"No finding|无发现|无漏洞|否证|已解除|已覆盖|已解决|已排除", s, re.IGNORECASE):
        return "concluded", "no_finding"
    if re.search(r"降级|Blocked|放弃|搁置", s, re.IGNORECASE):
        return "dropped", None
    return "open", None


def _sections(text: str) -> list[dict]:
    """按 2-3 级标题切段:返回 [{level,title,body,line}]。"""
    lines = text.split("\n")
    heads = [(i, len(m.group(1)), m.group(2))
             for i, ln in enumerate(lines) if (m := re.match(r"^(#{2,3})\s+(.*)$", ln))]
    out = []
    for k, (i, lvl, title) in enumerate(heads):
        end = len(lines)
        for j, l2, _t2 in heads[k + 1:]:
            if l2 <= lvl:
                end = j
                break
        out.append({"level": lvl, "title": title.strip(), "body": "\n".join(lines[i + 1:end]),
                    "line": i + 1})
    return out


# ---------------- 各类抽取 ----------------

def parse_surfaces(tdir: str, notes: str) -> tuple[list[dict], list[str]]:
    """优先 report/attack-surface.md(权威导出);没有才看 notes。返回 (面, 冲突)。"""
    paths = [os.path.join(tdir, "report", "attack-surface.md")]
    text, src = "", ""
    for p in paths:
        if os.path.isfile(p):
            text, src = _read(p), os.path.relpath(p, tdir)
            break
    if not text:
        text, src = notes, "notes.md"
    out, conflicts = [], []
    for tb in _tables(text):
        hdr = [h.lower() for h in tb["header"]]
        d = next((k for k, h in enumerate(hdr) if re.search(r"disposition|判定|处置", h)), None)
        if d is None:
            continue
        e = next((k for k, h in enumerate(hdr) if re.search(r"入口|entry|端口|服务|path|组件|载体", h)), None)
        if e is None:
            e = 1 if len(hdr) > 1 else 0
        ev = next((k for k, h in enumerate(hdr) if re.search(r"证据|evidence|载体", h)), None)
        for ri, row in enumerate(tb["rows"], 1):
            if len(row) <= max(d, e):
                continue
            disp = _disp_of(row[d])
            if disp is None:
                conflicts.append(f"面定义无法解析 disposition: {_clean(row[e])[:60]} → {row[d][:40]}")
                continue
            key = _clean(row[e])[:160] or f"row{ri}"
            out.append({"key": key, "disposition": disp,
                        "note": _clean(row[ev])[:500] if ev is not None and ev < len(row) else "",
                        "source": f"{src}:{tb['line'] + ri}"})
    # 去重(同 key 保留第一条)
    seen, uniq = set(), []
    for s in out:
        if s["key"] in seen:
            continue
        seen.add(s["key"])
        uniq.append(s)
    return uniq, conflicts


def parse_pool(notes: str) -> tuple[list[dict], list[str]]:
    """假设池:两种历史格式(bullet H-n / 表格)。"""
    out, conflicts = [], []
    lines = notes.split("\n")
    cur = None
    for i, ln in enumerate(lines, 1):
        m = RE_BULLET_H.match(ln)
        if m:
            if cur:
                out.append(cur)
            rest = m.group(2).replace("**", "")
            # 只去掉**优先级标记**(N1(高)/H-3(P1));括号里是标题时不能剥(实测 H-12 标题被吃掉)
            rest = re.sub(r"^\s*\((?:高|中|低|P\d+)\)", "", rest)
            rest = re.sub(r"^[\s:：\-–—]+", "", rest)
            if RE_RESTATE.match(rest):
                conflicts.append(f"状态重述 {m.group(1)}: {_clean(rest)[:100]}")
                continue                                        # 不建重复假设(原文仍在 documents)
            cur = {"raw_id": m.group(1), "title": _clean(rest)[:200],
                   "body": [ln.strip()], "line": i}
            continue
        if cur:
            if re.match(r"^#{1,4}\s", ln) or re.match(r"^- \*\*(?!H-|N\d)", ln):
                out.append(cur)
                cur = None
            else:
                cur["body"].append(ln.rstrip())
    if cur:
        out.append(cur)
    res = []
    for e in out:
        body = "\n".join(e["body"]).strip()
        st = RE_STATUS.search(body)
        if st:
            status = st.group(1).strip()
        else:
            m2 = re.search(r"(定论|结论)\s*[:：=]\s*([^\n]*)", body)
            status = ("定论: " + m2.group(2).strip()) if m2 else ""
        state, verdict = _verdict_of(status)
        if status and state == "open" and not re.search(r"未处理|进行中|待验证|TODO", status):
            conflicts.append(f"假设 {e['raw_id']} 状态措辞无法映射: {status[:50]}")
        res.append({"raw_id": e["raw_id"], "title": e["title"], "body": body,
                    "status": status, "state": state, "verdict": verdict, "line": e["line"]})
    # 表格池
    for tb in _tables(notes):
        hdr = [h.lower() for h in tb["header"]]
        if not any("假设" in h or "hypothes" in h for h in hdr):
            continue
        s = next((k for k, h in enumerate(hdr) if re.search(r"状态|status", h)), None)
        t = next((k for k, h in enumerate(hdr) if re.search(r"假设|hypothes", h)), None)
        if s is None or t is None:
            continue
        for ri, row in enumerate(tb["rows"], 1):
            if len(row) <= max(s, t):
                continue
            raw_id = _clean(row[0]) or f"T{ri}"
            status = _clean(row[s])
            title = _clean(row[t])
            if not title or title.startswith("="):
                _cand = [_clean(c) for k, c in enumerate(row) if k not in (0, s)]
                title = max(_cand, key=len) if _cand else raw_id
            if RE_RESTATE.match(title or ""):
                conflicts.append(f"状态重述 {raw_id}: {_clean(title)[:100]}")
                continue
            state, verdict = _verdict_of(status)
            res.append({"raw_id": raw_id, "title": title[:200],
                        "body": " | ".join(_clean(c) for c in row),
                        "status": status, "state": state, "verdict": verdict,
                        "line": tb["line"] + ri})
    # 同一 (raw_id, title) 在多个池块重复出现 ⇒ 保留"有状态/更晚"的那条
    dedup: dict[tuple[str, str], dict] = {}
    for e in res:
        key = (e["raw_id"], e["title"])
        old = dedup.get(key)
        if old is None or (bool(e["status"]) and not old["status"]) or e["line"] > old["line"]:
            dedup[key] = e
    return list(dedup.values()), conflicts


def parse_observations(notes: str) -> list[dict]:
    out = []
    lines = notes.split("\n")
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^\*\*(OBS-\d+)\s*(.*?)\*\*\s*$", ln)
        if m:
            body = [ln]
            for nxt in lines[i:i + 12]:
                if not nxt.strip() or re.match(r"^#{1,3}\s|^\*\*OBS-", nxt):
                    break
                body.append(nxt)
            out.append({"raw_id": m.group(1), "text": "\n".join(body).strip()[:4000], "line": i})
            continue
        m2 = re.match(r"^\s*[-*]\s*观察\s*[:：]\s*(.+)$", ln)
        if m2:
            out.append({"raw_id": "", "text": m2.group(1).strip()[:2000], "line": i})
    return out


RE_PATH = re.compile(r"`([^`\n]*(?:artifacts|report|poc|extract|rootfs)[^`\n]*)`")
_CMD_PREFIX = ("bash ", "sh ", "python", "curl", "qemu", "objdump", "grep", "cat ", "tail ",
               "unsquashfs", "binwalk", "unblob", "cp ", "mv ", "dd ", "tar ", "find ")


def _material_path(tdir: str, tok: str) -> str | None:
    """只把**材料文件**当证据路径:目录/命令行/花括号占位/无扩展名 → 不收(原文仍在 notes)。"""
    tok = tok.strip()
    if not tok or tok.startswith(_CMD_PREFIX):
        return None
    if any(ch in tok for ch in " \t{}<>|;"):
        return None
    if tok.endswith("/"):
        return None
    full = tok if os.path.isabs(tok) else os.path.join(tdir, tok)
    state_root = os.path.realpath(os.path.dirname(os.path.dirname(tdir)))
    real = os.path.realpath(full)
    if real != state_root and not real.startswith(state_root + os.sep):
        return None          # 只接受 state/ 内的路径(notes 里可能写 ../.. 指到宿主文件)
    if os.path.isfile(full):
        return tok
    # 已释放目标:文件可能被清掉;只有"像文件"的才保留为引用
    return tok if re.search(r"\.[A-Za-z0-9]{1,8}(?:$|:|#)", tok) else None


def parse_evidence(notes: str, tdir: str = "") -> list[dict]:
    """带路径的"证据/复核"行;不猜结论,只记材料与关联。"""
    out, cur_h = [], ""
    for i, ln in enumerate(notes.split("\n"), 1):
        m = RE_BULLET_H.match(ln)
        if m:
            cur_h = m.group(1)
        if not re.search(r"证据|evidence|复核|复现|截图", ln, re.IGNORECASE):
            continue
        for p in RE_PATH.findall(ln):
            path = _material_path(tdir, p)
            if not path:
                continue
            out.append({"path": path, "text": _clean(ln)[:500],
                        "hypothesis_raw": cur_h, "line": i,
                        "kind": ("screenshot" if re.search(r"\.(png|jpe?g|gif|webp)$", path, re.IGNORECASE)
                                 else "poc" if re.search(r"poc|exploit", path, re.IGNORECASE) else "file")})
    return out


def parse_mirror(notes: str) -> tuple[list[dict], list[str]]:
    out, conflicts = [], []
    for i, ln in enumerate(notes.split("\n"), 1):
        m = re.match(r"^三镜\s*[:：]\s*(.+)$", ln.strip())
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        if len(parts) < 3:
            conflicts.append(f"三镜行无法拆成 3 段(行 {i})")
            out.append({"mechanism": m.group(1)[:1500], "author": "(未解析)", "state": "(未解析)",
                        "line": i})
            continue
        def _pick(kw: str, _parts: list[str] = parts) -> str:
            hit = next((p for p in _parts if p.startswith(kw)), "")
            return hit[len(kw):].lstrip("=: ").strip() if hit else ""
        out.append({"mechanism": _pick("机制") or parts[0],
                    "author": _pick("作者") or parts[1],
                    "state": _pick("状态") or parts[2], "line": i})
    return out, conflicts


def parse_logs(notes: str) -> list[dict]:
    out = []
    for s in _sections(notes):
        t = s["title"]
        kind = None
        if "停止声明" in t:
            kind = "stop"
        elif re.search(r"轮\s*\d+|轮记录|§3\.\d+", t):
            kind = "round"
        elif "下一步" in t or "待办" in t or "TODO" in t:
            kind = "next"
        elif re.search(r"决策|取舍|方法学|结论|排除", t):
            kind = "decision"
        elif len(s["body"]) > 80 and re.search(r"假设|证据|结论|判定|状态|面", s["body"]):
            kind = "needs_review"
        if kind:
            out.append({"kind": kind, "title": t[:200], "body": s["body"][:40000],
                        "line": s["line"], "needs_review": 0 if kind != "needs_review" else 1})
    return out


# ---------------- 单目标迁移 ----------------

def migrate_target(con: sqlite3.Connection, root: str, name: str, dry_run: bool = False) -> dict:
    tdir = os.path.join(root, "state", name)
    notes_path = os.path.join(tdir, "notes.md")
    notes = _read(notes_path)
    rel = os.path.relpath(notes_path, root)
    fallback = _now()
    created = _date_of(notes, fallback)
    meta = _meta(name, notes)

    stats = dict.fromkeys(("target", "documents", "surfaces", "hypotheses", "observations",
                           "evidence", "evidence_checks", "mirror_checks", "logs",
                           "logs_needs_review", "conclusions", "conflicts"), 0)
    conflicts: list[str] = []

    # 目标
    row = con.execute("SELECT id FROM targets WHERE name=?", (name,)).fetchone()
    if row:
        tid = row["id"]
        # 版本可能是后来从笔记补出来的 ⇒ 已有目标也更新一次(只更新空值,不覆盖人工值)
        if meta["version"] and not dry_run:
            con.execute("UPDATE targets SET version=? WHERE id=? AND (version IS NULL OR version='')",
                        (meta["version"], tid))
    else:
        tid = _det("T", name)
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO targets(id,name,vendor,product,version,arch,created_at)"
                          " VALUES(?,?,?,?,?,?,?)",
                          (tid, name, meta["vendor"], meta["product"], meta["version"],
                           meta["arch"], created))
                c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                          (_now(), "migrate.target", tid, None))
        stats["target"] = 1

    # 文档(原文)
    doc_files = [notes_path]
    for extra in MD_REPORT:
        p = os.path.join(tdir, extra)
        if os.path.isfile(p):
            doc_files.append(p)
    try:
        tops = sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []
    except OSError:
        tops = []
    for p in tops:
        if p.endswith(".md") and p not in ("notes.md",):
            doc_files.append(os.path.join(tdir, p))
    for p in doc_files:
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except OSError as e:
            conflicts.append(f"文档读不了: {p}: {e}")
            continue
        sha = hashlib.sha256(raw).hexdigest()
        body = raw.decode("utf-8", errors="replace")
        drel = os.path.relpath(p, root)
        did = _det("D", drel, sha)
        exists = con.execute("SELECT 1 FROM documents WHERE path=? AND sha256=?", (drel, sha)).fetchone()
        if exists:
            continue
        stats["documents"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO documents(id,target_id,path,sha256,body,imported_at)"
                          " VALUES(?,?,?,?,?,?)", (did, tid, drel, sha, body, _now()))

    # 面
    surfaces, c1 = parse_surfaces(tdir, notes)
    conflicts += c1
    for s in surfaces:
        sid = _det("S", name, s["key"])
        if con.execute("SELECT 1 FROM surfaces WHERE id=?", (sid,)).fetchone():
            continue
        stats["surfaces"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO surfaces(id,target_id,key,disposition,note,judged_by)"
                          " VALUES(?,?,?,?,?,?)",
                          (sid, tid, s["key"], s["disposition"], s["note"],
                           f"migrated:{s['source']}"))

    # 假设 + 结论
    pool, c2 = parse_pool(notes)
    conflicts += c2
    hid_map = {}
    for h in pool:
        hid = _det("H", name, h["raw_id"], h["title"][:120])   # 稳定:不随行号改;同 id 不同标题各保留
        hid_map.setdefault(h["raw_id"], hid)
        if con.execute("SELECT 1 FROM hypotheses WHERE id=?", (hid,)).fetchone():
            continue
        stats["hypotheses"] += 1
        src = f"migrated:{rel}:{h['line']} (原 {h['raw_id']})"
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute(
                    "INSERT OR IGNORE INTO hypotheses(id,target_id,title,body,source,priority,state,"
                    "verdict,scope_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (hid, tid, h["title"] or h["raw_id"], h["body"], src, 100, h["state"],
                     h["verdict"], (h["status"] or None), created, created))
        if h["verdict"]:
            cid = _det("C", name, h["raw_id"], h["title"][:120], h["verdict"])
            if not con.execute("SELECT 1 FROM conclusions WHERE id=?", (cid,)).fetchone():
                stats["conclusions"] += 1
                if not dry_run:
                    with _db.Tx(con) as c:
                        c.execute("INSERT OR IGNORE INTO conclusions(id,target_id,hypothesis_id,verdict,"
                                  "scope_note,created_at) VALUES(?,?,?,?,?,?)",
                                  (cid, tid, hid, h["verdict"], (h["status"] or "")[:500], created))

    # 观察
    for o in parse_observations(notes):
        oid = _det("O", name, o["raw_id"] or o["text"][:200])
        if con.execute("SELECT 1 FROM observations WHERE id=?", (oid,)).fetchone():
            continue
        stats["observations"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO observations(id,target_id,text,created_at)"
                          " VALUES(?,?,?,?)", (oid, tid, o["text"], created))

    # 证据 + 复核
    seen_ev = set()
    for ev in parse_evidence(notes, tdir):
        key = (ev["path"], ev["kind"])
        if key in seen_ev:
            continue
        seen_ev.add(key)
        full = os.path.join(tdir, ev["path"]) if not os.path.isabs(ev["path"]) else ev["path"]
        sha = _sha256_file(full) if os.path.isfile(full) else None
        eid = _det("E", name, ev["path"], ev["kind"])
        if con.execute("SELECT 1 FROM evidence WHERE id=?", (eid,)).fetchone():
            continue
        stats["evidence"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute(
                    "INSERT OR IGNORE INTO evidence(id,target_id,hypothesis_id,kind,version,path,sha256,"
                    "notes,verified,created_at) VALUES(?,?,?,?,?,?,?,?,0,?)",
                    (eid, tid, hid_map.get(ev["hypothesis_raw"]), ev["kind"], meta["version"],
                     ev["path"], sha, ev["text"], created))
                if re.search(r"复核|复现|verify", ev["text"], re.IGNORECASE):
                    chk = _det("K", name, ev["path"], ev["line"])
                    c.execute("INSERT OR IGNORE INTO evidence_checks(id,evidence_id,method,"
                              "artifact_path,artifact_sha256,detail,created_at) VALUES(?,?,?,?,?,?,?)",
                              (chk, eid, "migrated:notes", ev["path"], sha, ev["text"][:500], created))
                    stats["evidence_checks"] = stats.get("evidence_checks", 0) + 1

    # 三镜
    mirrors, c3 = parse_mirror(notes)
    conflicts += c3
    for m in mirrors:
        mid = _det("M", name, m["mechanism"][:200], m["author"][:100])
        if con.execute("SELECT 1 FROM mirror_checks WHERE id=?", (mid,)).fetchone():
            continue
        stats["mirror_checks"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO mirror_checks(id,target_id,mechanism,author,state,"
                          "created_at) VALUES(?,?,?,?,?,?)",
                          (mid, tid, m["mechanism"][:4000], m["author"][:2000],
                           m["state"][:2000], created))

    # 研究日志
    for lg in parse_logs(notes):
        lid = _det("L", name, lg["kind"], lg["title"])
        if con.execute("SELECT 1 FROM research_logs WHERE id=?", (lid,)).fetchone():
            continue
        stats["logs"] += 1
        if lg["needs_review"]:
            stats["logs_needs_review"] += 1
        if not dry_run:
            with _db.Tx(con) as c:
                c.execute("INSERT OR IGNORE INTO research_logs(id,target_id,kind,title,body,source,"
                          "needs_review,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (lid, tid, lg["kind"], lg["title"], lg["body"],
                           f"{rel}:{lg['line']}", lg["needs_review"], created))

    # 冲突也落库(needs_review):扫描器/人之后能看到"哪一条没能自动归类"
    for c in conflicts:
        lid = _det("L", name, "conflict", c[:200])
        if con.execute("SELECT 1 FROM research_logs WHERE id=?", (lid,)).fetchone():
            continue
        stats["conflicts"] = stats.get("conflicts", 0) + 1
        if not dry_run:
            with _db.Tx(con) as c2:
                c2.execute("INSERT OR IGNORE INTO research_logs(id,target_id,kind,title,body,source,"
                           "needs_review,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (lid, tid, "needs_review", c[:200], c, f"migration:{rel}", 1, created))

    return {"target": name, "target_id": tid, "stats": stats, "conflicts": conflicts}


def reset_migrated(con: sqlite3.Connection, root: str, only: list[str] | None = None) -> dict:
    """清掉上一次迁移写入的行(重跑用;documents/targets 保留 —— 原文与目标不重复写)。"""
    names = only or list_targets(root)
    tids: list[str] = []
    for name in names:                      # 逐个查(不用拼接 SQL 占位符串)
        row = con.execute("SELECT id FROM targets WHERE name=?", (name,)).fetchone()
        if row:
            tids.append(row["id"])
    # 静态 SQL 白名单(表名不拼接):扫描器与人都能一眼核对
    sql = {
        "conclusions": "DELETE FROM conclusions WHERE target_id=?",
        "evidence": "DELETE FROM evidence WHERE target_id=?",
        "observations": "DELETE FROM observations WHERE target_id=?",
        "mirror_checks": "DELETE FROM mirror_checks WHERE target_id=?",
        "research_logs": "DELETE FROM research_logs WHERE target_id=?",
        "hypotheses": "DELETE FROM hypotheses WHERE target_id=?",
        "surfaces": "DELETE FROM surfaces WHERE target_id=?",
    }
    out = {}
    with _db.Tx(con) as c:
        for tbl, stmt in sql.items():
            n = 0
            for tid in tids:
                n += c.execute(stmt, (tid,)).rowcount
            out[tbl] = n
        # evidence_checks 只能经 evidence 关联删除
        n = 0
        for tid in tids:
            n += c.execute("DELETE FROM evidence_checks WHERE evidence_id IN"
                           " (SELECT id FROM evidence WHERE target_id=?)", (tid,)).rowcount
        out["evidence_checks"] = n
        # 文档:同一 path 只留最新版本(重跑不堆历史副本;原文仍逐字节保存在最新版)
        for tid in tids:
            rows = c.execute("SELECT id,path FROM documents WHERE target_id=? ORDER BY imported_at DESC",
                             (tid,)).fetchall()
            seen = set()
            for r in rows:
                if r["path"] in seen:
                    c.execute("DELETE FROM documents WHERE id=?", (r["id"],))
                    out["documents"] = out.get("documents", 0) + 1
                else:
                    seen.add(r["path"])
    return out


def migrate_all(root: str, dry_run: bool = False, only: list[str] | None = None) -> dict:
    con = _db.connect(_db.default_db_path(root))
    try:
        names = only or list_targets(root)
        results = [migrate_target(con, root, n, dry_run=dry_run) for n in names]
        total = {k: sum(r["stats"].get(k, 0) for r in results) for k in
                 ("documents", "surfaces", "hypotheses", "observations", "evidence",
                  "mirror_checks", "logs", "logs_needs_review", "conclusions", "conflicts")}
        return {"dry_run": dry_run, "targets": len(results), "total": total,
                "results": results,
                "conflicts": [f"{r['target']}: {c}" for r in results for c in r["conflicts"]]}
    finally:
        con.close()


def verify_target(con: sqlite3.Connection, root: str, name: str) -> dict:
    """迁移后复核:原文逐字节在库、行数非零、无孤立关联。"""
    tdir = os.path.join(root, "state", name)
    notes_path = os.path.join(tdir, "notes.md")
    try:
        with open(notes_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return {"target": name, "ok": False, "error": f"notes 读不了: {e}"}
    sha = hashlib.sha256(raw).hexdigest()
    t = con.execute("SELECT id FROM targets WHERE name=?", (name,)).fetchone()
    if not t:
        return {"target": name, "ok": False, "error": "target 未迁移"}
    tid = t["id"]

    def n(sql: str) -> int:
        return con.execute(sql, (tid,)).fetchone()["n"]

    doc = con.execute("SELECT body FROM documents WHERE target_id=? AND sha256=?", (tid, sha)).fetchone()
    counts = {
        "surfaces": n("SELECT COUNT(*) n FROM surfaces WHERE target_id=?"),
        "hypotheses": n("SELECT COUNT(*) n FROM hypotheses WHERE target_id=?"),
        "observations": n("SELECT COUNT(*) n FROM observations WHERE target_id=?"),
        "evidence": n("SELECT COUNT(*) n FROM evidence WHERE target_id=?"),
        "mirror_checks": n("SELECT COUNT(*) n FROM mirror_checks WHERE target_id=?"),
        "logs": n("SELECT COUNT(*) n FROM research_logs WHERE target_id=?"),
        "conclusions": n("SELECT COUNT(*) n FROM conclusions WHERE target_id=?"),
    }
    orphan = n("SELECT COUNT(*) n FROM evidence e WHERE e.target_id=? AND e.hypothesis_id IS NOT NULL"
               " AND NOT EXISTS (SELECT 1 FROM hypotheses h WHERE h.id=e.hypothesis_id)")
    # 证据路径不得逃出 state/(迁移只收工作区内的材料;越界 = 解析器回归)
    state_root = os.path.realpath(os.path.join(root, "state"))
    escape = 0
    for e in con.execute("SELECT path FROM evidence WHERE target_id=? AND path IS NOT NULL", (tid,)):
        full = e["path"] if os.path.isabs(e["path"]) else os.path.join(tdir, e["path"])
        real = os.path.realpath(full)
        if real != state_root and not real.startswith(state_root + os.sep):
            escape += 1
    byte_identical = doc is not None and doc["body"].encode() == raw
    return {"target": name,
            "ok": byte_identical and orphan == 0 and escape == 0,
            "byte_identical": byte_identical, "counts": counts,
            "orphan_links": orphan, "path_escapes": escape}
