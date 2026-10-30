#!/usr/bin/env python3
"""kb.py — 知识库(knowledge/)的安全编辑工具。

为什么存在:手改(read+write)最容易把知识库改坏(重复标题 / 围栏奇数 / 截断 / 并发互踩)。
本工具把"结构约束 + 原子写 + 改后自检 + 坏了回滚 + INDEX 计数同步"做成机械动作。

用法:
  kb.py check [file...]                  结构体检(同 derived_check ①e);有问题则退出码 1
  kb.py list <file>                      列出条目(序号/标题/行数)
  kb.py add <file> --title T [--body F|-] 追加新条目(标题须唯一、单行、不含目标内路径)
  kb.py append-line <file> --entry KEY --line TEXT   往既有条目追加一行(如"实例"行)
  kb.py replace <file> --entry KEY --old S --new S   条目内替换一处措辞(须恰好命中 1 行)
  kb.py rewrite <file> --entry KEY --body FILE       整条重写正文(保留标题;条目数不变)
  kb.py downgrade <file> --entry KEY --note "..."    降级:条目内插 `> 降级说明(日期): …` + INDEX 状态列改"降级(仅核对)"
  kb.py index-sync                       重算各文件条数与最后更新,写回 INDEX.md
  kb.py snapshot [--note X]              打包快照到 knowledge/.snapshots/
  kb.py restore <snapshot.tar.gz>        从快照恢复(恢复前自动再打一个 pre-restore 快照)
  kb.py selftest                         在临时副本上自检(不碰真库)

通用: --root DIR 指定知识库目录(默认 <repo>/knowledge;自检用)
安全: flock 串行化并发写 → 临时文件 + os.replace 原子替换 → 结构自检 → 失败自动回滚原字节
"""

import argparse, contextlib, datetime, fcntl, glob, hashlib, os, re, shutil, subprocess, sys, tarfile, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(os.path.dirname(HERE), "knowledge")
FILES_ORDER = ["vuln-patterns.md", "misreports.md", "methodology.md", "tool-quirks.md",
               "exploits-validated.md", "sdk-bloodlines.md", "intel-log.md"]

def today():
    return datetime.date.today().strftime("%Y-%m-%d")

# ---------------- 结构检查(与 derived_check ①e 同一套判据) ----------------
def check_text_issues(t):
    ln = t.split("\n"); hh = re.findall(r"(?m)^## (.+)$", t)
    iss = []
    if sum(1 for l in ln if l.strip().startswith("```")) % 2: iss.append("围栏奇数")
    dup = [h for h in set(hh) if hh.count(h) > 1]
    if dup: iss.append("重复小标题: " + " | ".join(d[:40] for d in dup[:2]))
    if "\x00" in t: iss.append("含 NUL 字节")
    if "\r\n" in t: iss.append("CRLF")
    if re.search(r"(?m)^(<<<<<<<|=======|>>>>>>>)", t): iss.append("合并冲突标记")
    if not t.endswith("\n"): iss.append("末行无换行(可能截断)")
    return iss

def file_issues(path):
    b = open(path, "rb").read()
    t = b.decode("utf-8", errors="replace")
    ln = t.split("\n")
    hh = re.findall(r"(?m)^## (.+)$", t)
    iss = []
    if sum(1 for l in ln if l.strip().startswith("```")) % 2: iss.append("围栏奇数")
    dup = [h for h in set(hh) if hh.count(h) > 1]
    if dup: iss.append("重复小标题: " + " | ".join(d[:40] for d in dup[:2]))
    if b"\x00" in b: iss.append("含 NUL 字节")
    if b"\r\n" in b: iss.append("CRLF")
    if re.search(r"(?m)^(<<<<<<<|=======|>>>>>>>)", t): iss.append("合并冲突标记")
    if not t.endswith("\n"): iss.append("末行无换行(可能截断)")
    return iss

def cmd_check(root, files):
    fs = files or [os.path.join(root, n) for n in sorted(os.listdir(root)) if n.endswith(".md")]
    bad = 0
    for f in fs:
        if not os.path.isfile(f): print(f"  ✗ 缺文件 {f}"); bad += 1; continue
        iss = file_issues(f)
        if iss: print(f"  FAIL {os.path.basename(f)}: {'; '.join(iss)}"); bad += 1
        else:   print(f"  OK   {os.path.basename(f)}")
    if not bad: print("  KB 结构体检: 全部通过 ✓")
    return 1 if bad else 0

# ---------------- INDEX 计数同步 ----------------
def index_sync(root, verbose=True):
    idx = os.path.join(root, "INDEX.md")
    t = open(idx, encoding="utf-8").read()
    for n in sorted(os.listdir(root)):
        if not n.endswith(".md") or n == "INDEX.md": continue
        body = open(os.path.join(root, n), encoding="utf-8").read()
        if not re.search(r"(?m)^## ", body):
            if verbose: print(f"  {n}: 无 `## ` 小节 ⇒ 跳过计数(不用 0 覆盖 INDEX)")
            continue
        cnt = len(re.findall(r"(?m)^## ", body))
        def _sub(m, cnt=cnt):
            return f"{m.group(1)}{cnt}{m.group(3)}{today()}"
        t2, k = re.subn(r"(\| `" + re.escape(n) + r"` \|[^|]*\| )(\d+)( \|[^|]*\| )\d{4}-\d{2}-\d{2}",
                        _sub, t)
        if k and verbose: print(f"  {n}: 条数→{cnt} 更新→{today()}")
        if not k: print(f"  ⚠ INDEX.md 里没有 {n} 的登记行(不静默)")
        t = t2
    write_atomic(idx, t)

def write_atomic(path, text):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".kb-tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)

# ---------------- 条目定位/插入 ----------------
def entries(text):
    """返回 [(标题, 起始行, 结束行)] —— 以 `## ` 分节"""
    ls = text.split("\n"); out = []; cur = None
    for i, l in enumerate(ls):
        if l.startswith("## "):
            if cur: out.append((ls[cur], cur, i))
            cur = i
    if cur is not None: out.append((ls[cur], cur, len(ls)))
    return out

def find_entry(text, key):
    for h, s, e in entries(text):
        if key in h: return h, s, e
    return None

def content_invariant(op, old, new):
    """追加型操作的内容不变量 —— 防"静默丢内容"这类自检抓不到的坏法"""
    iss = []
    # 拦"截断"(丢内容),不拦"改措辞":replace 允许小幅缩短;其它操作要求不缩短
    shrink = len(old) - len(new)
    tol = len(old) if op == "rewrite" else (max(200, int(len(old) * 0.02)) if op == "replace" else 0)
    if shrink > tol: iss.append(f"内容变短({len(old)}→{len(new)} 字节,容差 {tol})")
    o = [h for h, _, _ in entries(old)]; n = [h for h, _, _ in entries(new)]
    lost = [h for h in o if h not in n]
    if lost and op != "rewrite":   # rewrite 允许改标题(条目数仍受下面校验)
        iss.append("条目丢失: " + " | ".join(x[:30] for x in lost[:2]))
    want = 1 if op == "add" else 0
    if len(n) - len(o) != want: iss.append(f"条目数变化异常({len(o)}→{len(n)},应为+{want})")
    return iss

def mutate(root, name, fn, op="edit"):
    """带锁 + 原子写 + 自检 + 回滚的安全改动"""
    path = os.path.join(root, name)
    lock = os.path.join(root, ".kb.lock")
    with open(lock, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        orig = open(path, "rb").read()
        text = orig.decode("utf-8")
        new = fn(text)
        if new == text:
            print("  (无改动)"); return 0
        # ① 先验**候选内容**(不是磁盘文件):结构 + 内容不变量
        _iss = check_text_issues(new) + content_invariant(op, text, new)
        if _iss:
            print(f"  ✗ 候选内容不合格({'; '.join(_iss)})⇒ 未落盘"); return 2
        # ② 原子替换 + 落盘后再验一次(磁盘侧兜底)
        write_atomic(path, new)
        iss = file_issues(path)
        if iss:                                     # 坏了 ⇒ 原字节回滚
            with open(path, "wb") as fh: fh.write(orig)
            print(f"  ✗ 落盘后结构异常({'; '.join(iss)})⇒ 已回滚,未写入"); return 2
        index_sync(root, verbose=False)
        iss2 = file_issues(os.path.join(root, "INDEX.md"))
        if iss2:
            with open(path, "wb") as fh: fh.write(orig)
            print(f"  ✗ INDEX 同步异常({'; '.join(iss2)})⇒ 已回滚"); return 2
        _log_change(root, name, op, len(orig), len(new.encode("utf-8")),
                    hashlib.sha256(orig).hexdigest()[:8], hashlib.sha256(new.encode("utf-8")).hexdigest()[:8])
        print(f"  ✓ {name} 已更新(结构自检 + 内容不变量 + INDEX 同步通过)")
        return 0

def _log_change(root, name, op, before, after, sha0, sha1):
    """变更日志(审计线,恒定几百字节): 时间 / 文件 / 操作 / 字节 / 前后 sha8"""
    sd = os.path.join(root, ".snapshots"); os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "CHANGELOG.tsv"), "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\t{name}\t{op}\t{before}->{after}\t{sha0}->{sha1}\n")

def cmd_log(root, tail):
    f = os.path.join(root, ".snapshots", "CHANGELOG.tsv")
    if not os.path.isfile(f): print("  (暂无变更日志)"); return 0
    ls = open(f, encoding="utf-8").read().rstrip("\n").split("\n")
    for l in ls[-tail:]: print("  " + l)
    return 0

# ---------------- 子命令 ----------------
def cmd_list(root, name):
    t = open(os.path.join(root, name), encoding="utf-8").read()
    for i, (h, s, e) in enumerate(entries(t), 1):
        print(f"  {i:>3}. {h[3:][:96]}   ({e - s} 行)")

def cmd_add(root, name, title, body):
    title = title.strip()
    if "\n" in title: print("  ✗ 标题必须单行"); return 2
    if re.search(r"(state/|artifacts/|notes\.md|intel/firmware)", title): print("  ✗ 标题含目标内路径(KB 须即查即用)"); return 2
    if body in (None, "-"):
        if sys.stdin.isatty(): print("  ✗ 需要 --body FILE(或把内容接进 stdin)"); return 2
        body_text = sys.stdin.read()
    else:
        body_text = open(body, encoding="utf-8").read()
    def fn(text):
        if any(title[:24] in h for h, _, _ in entries(text)):
            raise SystemExit("  ✗ 标题重复(用 append-line 往既有条目追加)")
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + f"## {title}\n" + body_text.strip() + "\n"
    try: return mutate(root, name, fn, op="add")
    except SystemExit as e: print(str(e)); return 2

def cmd_append_line(root, name, key, line):
    if "\n" in line: print("  ✗ --line 必须单行"); return 2
    def fn(text):
        hit = find_entry(text, key)
        if not hit: raise SystemExit(f"  ✗ 找不到条目: {key}")
        h, s, e = hit
        ls = text.split("\n")
        end = e
        while end > s and ls[end - 1].strip() == "": end -= 1   # 插在尾部空行之前,保住文件末尾换行
        return "\n".join(ls[:end] + ["- " + line.strip()] + ls[end:])
    try: return mutate(root, name, fn, op="append-line")
    except SystemExit as ex: print(str(ex)); return 2

def cmd_rewrite(root, name, key, body_path, new_title=None):
    body = open(body_path, encoding="utf-8").read().strip()
    if not body: print("  ✗ 正文为空"); return 2
    def fn(text):
        hit = find_entry(text, key)
        if not hit: raise SystemExit(f"  ✗ 找不到条目: {key}")
        h, s, e = hit
        ls = text.split("\n")
        if new_title:
            ls[s] = "## " + new_title.strip()
        tail = []
        for j in range(e - 1, s, -1):
            if ls[j].strip() == "": tail.append(ls[j])
            else: break
        return "\n".join(ls[:s + 1] + body.split("\n") + list(reversed(tail)) + ls[e:])
    try: rc = mutate(root, name, fn, op="rewrite")
    except SystemExit as ex: print(str(ex)); return 2
    if rc == 0 and new_title:
        idx = os.path.join(root, "INDEX.md")
        try:
            s = open(idx, encoding="utf-8").read()
            if key in s:
                open(idx, "w", encoding="utf-8").write(s.replace(key, new_title.strip(), 1))
        except Exception:
            pass
    return rc


def cmd_replace(root, name, key, old, new):
    if "\n" in old or "\n" in new: print("  ✗ --old/--new 必须单行"); return 2
    if not old.strip(): print("  ✗ --old 不能为空"); return 2
    def fn(text):
        hit = find_entry(text, key)
        if not hit: raise SystemExit(f"  ✗ 找不到条目: {key}")
        h, s, e = hit
        ls = text.split("\n")
        hits = [i for i in range(s, e) if old in ls[i]]
        if len(hits) != 1:
            raise SystemExit(f"  ✗ 条目内命中 {len(hits)} 行(需恰好 1)")
        i = hits[0]
        ls[i] = ls[i].replace(old, new, 1)
        return "\n".join(ls)
    try: return mutate(root, name, fn, op="replace")
    except SystemExit as ex: print(str(ex)); return 2


def cmd_downgrade(root, name, key, note):
    def fn(text):
        hit = find_entry(text, key)
        if not hit: raise SystemExit(f"  ✗ 找不到条目: {key}")
        h, s, e = hit
        ls = text.split("\n")
        return "\n".join(ls[:s + 1] + [f"> 降级说明({today()}): {note}"] + ls[s + 1:])
    try: rc = mutate(root, name, fn, op="downgrade")
    except SystemExit as ex: print(str(ex)); return 2
    if rc: return rc
    # INDEX 状态列改"降级(仅核对)"
    idx = os.path.join(root, "INDEX.md"); t = open(idx, encoding="utf-8").read()
    t2, k = re.subn(r"(\| `" + re.escape(name) + r"` \|[^|]*\|[^|]*\| )有效( \|)", r"\1降级(仅核对)\2", t)
    if k: write_atomic(idx, t2); print("  ✓ INDEX 状态列 → 降级(仅核对)")
    return 0

def _kb_digest(root):
    h = hashlib.sha256()
    for n in sorted(os.listdir(root)):
        if n.endswith(".md"):
            h.update(n.encode()); h.update(open(os.path.join(root, n), "rb").read())
    return h.hexdigest()

def _newest_snapshot(sd, kind="knowledge"):
    fs = sorted(f for f in os.listdir(sd) if f.startswith(kind) and f.endswith(".tar.gz"))
    return os.path.join(sd, fs[-1]) if fs else None

def cmd_snapshot(root, note, force=False, keep=1):
    """内容去重:与最新快照的 sha 相同则跳过(恒定占用);变化才新建并轮换"""
    sd = os.path.join(root, ".snapshots"); os.makedirs(sd, exist_ok=True)
    dig = _kb_digest(root)
    prev = _newest_snapshot(sd)
    if prev:
        pv = os.path.join(sd, os.path.basename(prev).replace(".tar.gz", ".sha256"))
        if os.path.isfile(pv) and open(pv).read().strip() == dig and not force:
            print("  ✓ 内容未变,快照已是最新(去重跳过)"); return 0
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(sd, f"knowledge-{ts}.tar.gz")
    names = [os.path.join(root, n) for n in sorted(os.listdir(root)) if n.endswith(".md")]
    with tarfile.open(out, "w:gz") as tf:
        for f in names: tf.add(f, arcname=os.path.join("knowledge", os.path.basename(f)))
    open(out.replace(".tar.gz", ".sha256"), "w").write(dig + "\n")
    if note: open(out.replace(".tar.gz", ".note"), "w").write(note + "\n")
    # 轮换:常规快照只留最新 keep 个(+ 各自 sha;pre-restore 另算)
    regs = sorted(f for f in os.listdir(sd) if f.startswith("knowledge-") and f.endswith(".tar.gz"))
    for old in regs[:-keep]:
        for ext in (".tar.gz", ".sha256", ".note"):
            with contextlib.suppress(FileNotFoundError): os.remove(os.path.join(sd, old.replace(".tar.gz", ext)))
    print(f"  ✓ 快照 {os.path.basename(out)}  ({os.path.getsize(out)} B, {len(names)} 文件, 保留 {keep} 个)")
    return 0

def cmd_restore(root, snap):
    if not os.path.isfile(snap): print("  ✗ 快照不存在"); return 2
    cmd_snapshot(root, note=f"pre-restore (before restoring {os.path.basename(snap)})", force=True)
    with tarfile.open(snap, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.endswith(".md")]
        for m in members:
            f = tf.extractfile(m)
            if f is None: continue
            open(os.path.join(root, os.path.basename(m.name)), "wb").write(f.read())
            print(f"  ✓ 恢复 {os.path.basename(m.name)}")
    cmd_check(root, None)
    return 0

def cmd_selftest():
    """在临时副本上验证:正常写入 + INDEX 同步 + **写坏自动回滚**(核心保证)"""
    tmp = tempfile.mkdtemp(prefix="kb-selftest-")
    root = os.path.join(tmp, "knowledge")
    shutil.copytree(DEFAULT_ROOT, root, ignore=shutil.ignore_patterns(".snapshots", ".kb.lock"))
    me = os.path.abspath(__file__)
    def run(*args):
        return subprocess.run([sys.executable, me, "--root", root, *args],
                              capture_output=True, text=True)
    def rowcount(name):
        m = re.search(r"\| `" + re.escape(name) + r"` \|[^|]*\| (\d+) \|",
                      open(os.path.join(root, "INDEX.md"), encoding="utf-8").read())
        return int(m.group(1)) if m else -1
    ok = True; steps = []
    r = run("check");            ok &= r.returncode == 0;                     steps.append(("check 通过", r.returncode == 0, r.stdout.strip()[:60]))
    good = os.path.join(tmp, "good.md"); open(good, "w").write("- **判据**: 自检用\n- **实证**: 自检\n")
    before = rowcount("tool-quirks.md")
    r = run("add", "tool-quirks.md", "--title", "SELFTEST 临时条目", "--body", good)
    after = rowcount("tool-quirks.md")
    wrote = "SELFTEST 临时条目" in open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    ok &= (r.returncode == 0 and wrote and after == before + 1)
    steps.append(("add 成功 + INDEX 条数 +1", r.returncode == 0 and wrote and after == before + 1, f"{before}→{after}"))
    bad = os.path.join(tmp, "bad.md"); open(bad, "w").write("- 体\n```\n")   # 未闭合围栏 ⇒ 结构必坏
    r = run("add", "tool-quirks.md", "--title", "SELFTEST 坏条目", "--body", bad)
    _now = open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    rolled = ("未落盘" in r.stdout or "回滚" in r.stdout) and ("SELFTEST 坏条目" not in _now)
    ok &= (r.returncode == 2 and rolled)
    steps.append(("写坏 ⇒ 落盘前拦下(或回滚)", r.returncode == 2 and rolled, r.stdout.strip()[:56]))
    r = run("append-line", "tool-quirks.md", "--entry", "SELFTEST 临时条目", "--line", "实例: 自检追加")
    app = "实例: 自检追加" in open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    ok &= (r.returncode == 0 and app); steps.append(("append-line 可用", r.returncode == 0 and app, ""))
    r = run("downgrade", "tool-quirks.md", "--entry", "SELFTEST 临时条目", "--note", "自检")
    dg = "降级说明(" in open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    ok &= (r.returncode == 0 and dg); steps.append(("downgrade 可用", r.returncode == 0 and dg, ""))
    r = run("check");            ok &= r.returncode == 0;                     steps.append(("改后 check 仍通过", r.returncode == 0, ""))
    # ③ 不变量:内容变短 / 丢条目 必须被拒
    whole = open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    iv = content_invariant("append-line", whole, whole[: int(len(whole) * 0.5)])
    ok &= bool(iv); steps.append(("内容变短/丢条目 ⇒ 被拒", bool(iv), (iv[0] if iv else "")))
    # ④ 并发:4 路 add 并行 ⇒ 全部落盘 + INDEX 恰好 +4(锁生效才可能)
    import concurrent.futures
    cb = os.path.join(tmp, "c.md"); open(cb, "w").write("- 并发测试条目\n")
    before = rowcount("tool-quirks.md")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        rs = list(ex.map(lambda i: run("add", "tool-quirks.md", "--title", f"SELFTEST 并发 {i}", "--body", cb), range(4)))
    after = rowcount("tool-quirks.md")
    txt = open(os.path.join(root, "tool-quirks.md"), encoding="utf-8").read()
    cok = all(r.returncode == 0 for r in rs) and after == before + 4 and all(f"SELFTEST 并发 {i}" in txt for i in range(4)) and not file_issues(os.path.join(root, "tool-quirks.md"))
    ok &= cok; steps.append(("4 路并发 add 全部落盘 + INDEX 恰 +4", cok, f"{before}→{after}"))
    print("  === kb.py 自检(临时副本,不碰真库)===")
    for name, good_, extra in steps:
        print(f"   {'✓' if good_ else '✗'} {name}{('  ' + extra) if extra else ''}")
    shutil.rmtree(tmp, ignore_errors=True)
    print("  selftest:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1

def main():
    ap = argparse.ArgumentParser(prog="kb.py", add_help=True)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("files", nargs="*")
    p = sub.add_parser("list"); p.add_argument("file")
    p = sub.add_parser("add"); p.add_argument("file"); p.add_argument("--title", required=True); p.add_argument("--body", default="-")
    p = sub.add_parser("append-line"); p.add_argument("file"); p.add_argument("--entry", required=True); p.add_argument("--line", required=True)
    p = sub.add_parser("rewrite"); p.add_argument("file"); p.add_argument("--entry", required=True); p.add_argument("--body", required=True); p.add_argument("--title", default=None)
    p = sub.add_parser("replace"); p.add_argument("file"); p.add_argument("--entry", required=True); p.add_argument("--old", required=True); p.add_argument("--new", required=True)
    p = sub.add_parser("downgrade"); p.add_argument("file"); p.add_argument("--entry", required=True); p.add_argument("--note", required=True)
    sub.add_parser("index-sync")
    p = sub.add_parser("snapshot"); p.add_argument("--note", default=""); p.add_argument("--keep", type=int, default=1)
    p = sub.add_parser("log"); p.add_argument("--tail", type=int, default=20)
    p = sub.add_parser("restore"); p.add_argument("snapshot")
    sub.add_parser("selftest")
    a = ap.parse_args()
    r = a.root
    if a.cmd == "check":      return cmd_check(r, a.files)
    if a.cmd == "list":       return cmd_list(r, a.file) or 0
    if a.cmd == "add":        return cmd_add(r, a.file, a.title, a.body)
    if a.cmd == "append-line":return cmd_append_line(r, a.file, a.entry, a.line)
    if a.cmd == "downgrade":  return cmd_downgrade(r, a.file, a.entry, a.note)
    if a.cmd == "replace":    return cmd_replace(r, a.file, a.entry, a.old, a.new)
    if a.cmd == "rewrite":    return cmd_rewrite(r, a.file, a.entry, a.body, a.title)
    if a.cmd == "index-sync":
        with open(os.path.join(r, ".kb.lock"), "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX); index_sync(r)
        return 0
    if a.cmd == "snapshot":   return cmd_snapshot(r, a.note, keep=a.keep)
    if a.cmd == "log":        return cmd_log(r, a.tail)
    if a.cmd == "restore":
        with open(os.path.join(r, ".kb.lock"), "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX); return cmd_restore(r, a.snapshot)
    if a.cmd == "selftest":   return cmd_selftest()

if __name__ == "__main__":
    sys.exit(main())
