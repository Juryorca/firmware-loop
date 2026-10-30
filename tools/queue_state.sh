#!/usr/bin/env bash
# queue_state.sh — "继续护栏"用的只读状态探针(不写任何东西)
# 输出一行: OPEN=<未处理条数> BAD=<降级理由不合规条数> NEXT=<最后一条'下一步第一条'(截断)>
# 判据与 derived_check.sh 保持一致:
#   · H 条目"状态"含 未处理/进行中/未开始 ⇒ 队列项(OPEN)
#   · 含"降级"但理由是成本类(成本/太重/复杂/预算/时间/工作量大)且无判据类词,或只靠"N轮" ⇒ 不合规(BAD)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="${1:-}"
if [ -z "$T" ]; then
  T="$(ls -1dt "$ROOT"/state/*/notes.md 2>/dev/null | head -1 | xargs -r dirname | xargs -r basename)"
fi
N="$ROOT/state/$T/notes.md"
if [ ! -f "$N" ]; then echo "OPEN=0 BAD=0 NEXT="; exit 0; fi
python3 - "$N" <<'PY'
import re, sys, os
t = open(sys.argv[1], encoding="utf-8", errors="ignore").read()

# ---- 池块:优先"唯一队列"标签节,否则第一处"假设池" ----
hs = list(re.finditer(r"(?m)^(#{2,4})[^\n]*假设池[^\n]*$", t))
blk = ""
if hs:
    h = ([x for x in hs if "唯一队列" in x.group(0)] or hs)[0]
    lvl = len(h.group(1))
    nxt = re.search(r"(?m)^#{1,%d} " % lvl, t[h.end():])
    blk = t[h.end(): h.end() + (nxt.start() if nxt else len(t) - h.end())]

# ---- 条目切分: 以 "- **H" 或 "- " 开头者为一条 ----
ents, cur = [], None
for ln in blk.split("\n"):
    if re.match(r"^\s*[-*]\s", ln):
        if cur is not None: ents.append(cur)
        cur = ln
    elif cur is not None:
        cur += "\n" + ln
if cur: ents.append(cur)

DONE = ("定论", "No finding", "已排除", "降级", "成立", "Blocked", "结论", "已收口", "MISS", "已并入")
open_n = bad_n = 0
for e in ents:
    m = re.search(r"状态\s*\**\s*[:：]?\s*\**\s*([^\n*]*)", e)
    st = m.group(1) if m else ""
    if re.search(r"未处理|进行中|未开始|TODO", st):        open_n += 1
    elif re.search(r"定论|已收口|No finding|已排除|降级|Blocked|已并入|完成", st): pass
    elif not any(k in e for k in DONE):                    open_n += 1
    if "降级" in st:
        cost = re.search(r"成本|太重|太贵|复杂|预算|时间不够|工作量大|超时|太慢|耗时|导入慢|工程量大", e)
        judg = re.search(r"判据|不可达|上游|设计内|已排除|证伪|需工作区外|需用户|真机", e)
        onlyr = re.search(r"(?:\d|两|几)\s*轮", e) and not re.search(r"判据", e)
        if (cost and not judg) or onlyr: bad_n += 1

# NEXT 取**真正的"下一步第一条"行**(行首即该词/带 * 或 - 前缀),避免抓到术语解释里的举例 ✗
last = ""
for ln in t.split("\n"):
    if "下一步第一条" not in ln: continue
    _pre = ln.split("下一步第一条")[0]          # 排除"举例/括注"里的提及(前面紧跟 ( 或 " 的)
    if _pre.rstrip().endswith(("(", "（", "\"", "\u201c")): continue
    last = ln.strip()
# STOP 只在**最后一轮**(最后一个 ##/### 标题之后)里认 —— 否则会把上一轮的老声明当本轮理由 ✗
# (2026-09-29 实测踩过:VR-01 那轮的"判据满足"被当成 H-02 轮的理由 ⇒ 误放行)
_lines = t.split("\n")
_last = 0
for _i, _ln in enumerate(_lines):
    if re.match(r"^#{2,3}\s", _ln): _last = _i
stop = ""
for ln in _lines[_last:]:
    if ln.strip().startswith("本轮停止") or ln.strip().startswith("> 本轮停止"): stop = ln.strip()
# ROUND: 最后一个轮次节是否把某条 H 推到"结论"(**半轮检测**)—— 定义见 LOOP「轮 = 把一条假设推进到 oracle 结果或判定性结论」
_rr = 0
for _i, _ln in enumerate(_lines):
    if re.match(r"^#{2,3}\s*§?\d+", _ln) and not re.search(r"假设池|排除清单", _ln): _rr = _i
_rt = "\n".join(_lines[_rr:])
# 成本类降级:同一行里"成本词 + 决策词"同现(且无判定类词)⇒ BAD(成本不是降级理由)
for _l5 in _rt.split("\n"):
    if re.search(r"成本|超时|太慢|耗时|导入慢|工程量大", _l5) and re.search(r"降级|止于|不做|不继续|放弃|Out of scope|收口|工程成本", _l5) \
       and not re.search(r"判据|不可达|已排除|证伪|需工作区外|需用户", _l5):
        bad_n += 1; print(f"  ⚠ 成本类降级(最后一轮): {_l5.strip()[:90]}", file=sys.stderr); break
_m2 = re.search(r"本轮目标\s*[:：]\s*\**\s*(H-\d+)", _rt)      # 显式声明(有新规则才有)
if _m2:
    _h = _m2.group(1)
    _done = bool(re.search(r"本轮\s*[:：][^\n]*" + _h + r"[^\n]*(定论|判负|No finding|降级|结论|未证伪|否证)", _rt)) \
            or bool(re.search(_h + r"[^\n]*(定论|判负|No finding|降级)", _rt))
    roundstate = "done" if _done else "half"
else:
    roundstate = "?"      # 没声明 ⇒ 不判半轮(避免误报)
# 轮结构(尾部): 收口无对应目标 / 同一 H 重复收口 ⇒ BAD(口径同 derived_check ④c 与 LOOP「轮的声明格式」)
_cg = re.findall(r"本轮目标\s*[:：]\s*\**\s*(H-\d+)", _rt)
_cc = re.findall(r"(?m)^>?\s*本轮\s*[:：]\s*\**\s*(H-\d+)", _rt)
_cdup = sorted({h for h in _cc if _cc.count(h) > 1})
_corph = sorted({h for h in _cc if h not in _cg})
if _corph or _cdup:
    bad_n += 1
    print(f"  ⚠ 轮结构(尾部): 无目标的收口={_corph} 同一 H 重复收口={_cdup}", file=sys.stderr)
# POOL = 池条目数 / 面数(A2 入口点行): 比值 <1 = 枚举停在面层(端点层没展开) —— 见 LOOP「池的下限(三来源)」
_pool = len(set(re.findall(r"H-(\d+)", blk))) if blk else 0
_faces = 0
try:
    _asf = os.path.join(os.path.dirname(sys.argv[1]), "report", "attack-surface.md")   # notes.md 的上一级 = 目标目录
    if os.path.isfile(_asf):
        _faces = sum(1 for _l in open(_asf, encoding="utf-8", errors="ignore") if re.match(r"^\|\s*\d+[a-z]?\s*\|", _l))
except Exception as _e:
    _faces = 0
    print(f"  ⚠ 面数解析失败({type(_e).__name__}): POOL 比值不可用", file=sys.stderr)
# 只在"本轮宣称队列清空/收尾"时才算不合规(否则池小只是过程态,不报)
if _faces and _pool < _faces and re.search(r"队列清空|全部收口|收尾", _rt):
    bad_n += 1
    print(f"  ⚠ 池比面少(池={_pool} / 面={_faces})且本轮宣称收尾/队列清空 ⇒ 端点层没展开(先导该面的端点清单再入池)", file=sys.stderr)
print(f"OPEN={open_n} BAD={bad_n} ROUND={roundstate} STOP={stop[:120]} POOL={_pool}/{_faces} NEXT={last[:160]}")
PY
