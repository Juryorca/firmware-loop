"""研究载体:把 main 里的研究循环(规则/池/三镜/双输出/既有工具)接进运行时。

设计边界
- **规则不复制**:`AGENTS.md` / `LOOP.md` / `knowledge/*.md` 是唯一来源,这里只**读取并裁剪**,
  不把规则改写成代码里的第二份(否则必然漂)。
- **程序只做可核的事**:池排序、三镜计数、双输出是否登记、收尾阻塞;不替模型决定研究策略。
- **方法保持开放**:方法卡来自 `knowledge/` 的条目标题 + 工具描述,不是封闭技术表。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .db import Tx
from .domain import Domain, _event, _now, new_id

RULES_LIMIT = 12000  # 每个规则文件的字符预算
# 按段落选而不是盲截:优先保留"决定行为"的段落(用户批评过"截断可能把关键规则切掉")
RULE_PRIORITY = ("不变量", "停 / 不停", "停/不停", "闸门", "轮", "假设池", "证据", "三镜",
                 "收尾", "Exit Criteria", "判据", "交付原则", "例外")


def select_rules(text: str, budget: int = RULES_LIMIT) -> str:
    """在预算内选段落:优先"决定行为"的章节,其余按原顺序填充;超预算时标注省略数量。

    为什么不用"前 N 字符截断":AGENTS/LOOP 的关键约束(停/不停、闸门、轮格式)散落在末尾,
    盲截会把它们切掉 ⇒ 模型看到的是"没有硬约束的规则书"。
    """
    if len(text) <= budget:
        return text
    import re as _re

    parts = _re.split(r"(?m)^(?=## )", text)
    head = parts[0] if parts and not parts[0].startswith("## ") else ""
    sections = [s for s in parts if s.startswith("## ")]
    if not sections:
        return text[:budget] + "\n…(截断)"

    def score(s: str) -> int:
        title = s.split("\n", 1)[0]
        return 0 if any(k in title for k in RULE_PRIORITY) else 1

    order = sorted(range(len(sections)), key=lambda i: (score(sections[i]), i))
    kept: dict[int, str] = {}
    used = len(head)
    for i in order:
        if used + len(sections[i]) <= budget:
            kept[i] = sections[i]
            used += len(sections[i])
    omitted = len(sections) - len(kept)
    out = head + "".join(kept[i] for i in sorted(kept))
    if omitted:
        out += f"\n…(已省略 {omitted} 段:超出预算且优先级较低)\n"
    return out[: budget + 200]


def _read(path: str, limit: int = RULES_LIMIT) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return ""
    if len(txt) <= limit:
        return txt
    cut = txt[:limit]
    nl = cut.rfind("\n")
    return cut[: nl if nl > 0 else limit] + "\n…(截断)"


def kb_titles(root: str) -> list[str]:
    """知识库条目标题 = 方法/判据的**开放**清单(不是封闭技术表)。"""
    out: list[str] = []
    kdir = os.path.join(root, "knowledge")
    if not os.path.isdir(kdir):
        return out
    try:
        names = sorted(os.listdir(kdir))
    except OSError as e:
        return [f"(knowledge/ 不可读: {e})"]
    for name in names:
        if not name.endswith(".md") or name == "INDEX.md":
            continue
        try:
            body = _read(os.path.join(kdir, name), 10 ** 6)
        except OSError:
            continue
        for m in re.finditer(r"(?m)^## (.+)$", body):
            out.append(f"{name}: {m.group(1).strip()[:110]}")
    return out


def _clip(s, n: int) -> str:
    return (s or "").strip().replace("\n", " ")[:n]


def _facts_lines(hyps, evs, obs, budget: int = 2900) -> list[str]:
    """按**类别配额**呈现研究事实:任何一类都不能被别的类别挤到完全看不见。

    只给"索引 + 关键内容":标题/范围截断、证据给路径与来源、观察给文本与来源;
    全文仍在数据库与磁盘(模型可用 fs_read 按路径取)。
    """
    def h_lines():
        out = []
        for h in hyps:
            line = f"- {h['id']} [{h['state']}/{h['verdict'] or '-'}] {_clip(h['title'], 70)}"
            if h["scope_note"]:
                line += f"\n  范围: {_clip(h['scope_note'], 60)}"
            out.append(line)
        return out

    def e_lines():
        return [f"- {e['id']} {_clip(e['path'] or '(capture)', 70)}"
                f" [{e['link_relation'] or 'unlinked'}]"
                + (f" (来源 {e['tool_call_id']})" if e["tool_call_id"] else "")
                for e in evs]

    def o_lines():
        return [f"- 观察 {o['id']}: {_clip(o['text'], 150)}"
                + (f" (来源 {o['tool_call_id']})" if o["tool_call_id"] else "")
                + (f" [{o['relation'] or 'linked'}]" if o["tool_call_id"] else " [no-source]")
                + (f" 文件 {_clip(o['path'], 50)}" if o["path"] else "")
                for o in obs]

    cats = [("假设", h_lines(), 0.40), ("证据", e_lines(), 0.28), ("观察", o_lines(), 0.32)]
    picked = {name: [] for name, _, _ in cats}
    left = budget
    for name, lines, share in cats:
        quota = int(budget * share)
        used = 0
        for ln in lines:
            if used + len(ln) + 1 > quota:
                break
            picked[name].append(ln)
            used += len(ln) + 1
        left -= used
    # 剩余预算再按顺序补(前面的类别先补),避免某一类明明还有内容却被饿死
    for name, lines, _ in cats:
        for ln in lines[len(picked[name]):]:
            if len(ln) + 1 > left:
                break
            picked[name].append(ln)
            left -= len(ln) + 1
    out: list[str] = []
    for name, _, _ in cats:
        if picked[name]:
            out.append(f"### {name}")
            out += picked[name]
    return out


@dataclass
class Research:
    """研究循环的载体:构建系统提示 + 管理池/三镜/双输出。"""

    root: str
    domain: Domain
    rules_files: tuple[str, ...] = ("AGENTS.md", "LOOP.md",
                                    "knowledge/misreports.md", "knowledge/vuln-patterns.md")
    mirror_every: int = 3  # 下限每 3 轮一次三镜

    # ---------- 池:唯一队列,程序排序 ----------
    def pool(self, target: str, limit: int = 12) -> list[dict]:
        t = self.domain.target(target)
        rows = self.domain.con.execute(
            """SELECT h.id, h.title, h.source, h.priority, h.state, h.surface_key
                 FROM hypotheses h WHERE h.target_id=? AND h.state IN ('open','investigating')
                ORDER BY h.priority ASC, h.created_at ASC""", (t.id,)).fetchall()
        out = [dict(r) for r in rows]
        # 排序因子(可推翻):未认证面 > 认证后;报文级 > 配置级;复杂解析/状态机 > 简单
        rank = {"pre-auth": 0, "lan": 1, "auth": 2, "post-auth": 2, None: 3}
        surf = {r["key"]: dict(r) for r in self.domain.con.execute(
            "SELECT key, reachability, input_control, complexity, disposition FROM surfaces WHERE target_id=?",
            (t.id,)).fetchall()}
        def score(h: dict) -> tuple:
            text = (h["title"] or "") + " " + (h["source"] or "")
            key = h.get("surface_key")
            r = rank.get(surf[key]["reachability"], 3) if key in surf else 3
            if key is None:  # 未归属既有面的新方向:不惩罚,但排在已归属之后
                r = 3
            complex_bonus = 0 if re.search(r"解析|parser|状态机|协议|反序列化|XML|JSON|解压|签名", text, re.IGNORECASE) else 1
            unknown = 0 if not re.search(r"未理解|unknown|没看懂", text, re.IGNORECASE) else -1
            return (r, complex_bonus, unknown, h["priority"])
        out.sort(key=score)
        return out[:limit]

    # ---------- 三镜刷新(面/格收口时 1 行) ----------
    def record_mirror(self, target: str, mechanism: str, author: str, state: str,
                      new_faces: str = "无(判据: 同机制未找到新面)") -> str:
        t = self.domain.target(target)
        for k, v in (("mechanism", mechanism), ("author", author), ("state", state)):
            if not (v or "").strip():
                raise ValueError(f"三镜缺项: {k}")
        mid = new_id("M")
        with Tx(self.domain.con) as c:
            c.execute("INSERT INTO mirror_checks(id,target_id,mechanism,author,state,new_faces,created_at)"
                      " VALUES(?,?,?,?,?,?,?)", (mid, t.id, mechanism, author, state, new_faces, _now()))
            _event(c, "mirror.recorded", mid, {"new_faces": new_faces})
        return mid

    def rounds_since_mirror(self, target: str) -> int:
        """**该目标**自上次三镜以来结束的研究轮次数。

        轮 = `round.ended` 事件(一轮 = 一条假设被推到判定性结果);
        run 的暂停/恢复/结束**不计**轮次 —— 它们只是执行事件。
        """
        t = self.domain.target(target)
        last = self.domain.con.execute(
            "SELECT created_at FROM mirror_checks WHERE target_id=? ORDER BY created_at DESC LIMIT 1", (t.id,)
        ).fetchone()
        # 单条参数化查询(不拼接 SQL;`? IS NULL` 表示"没有三镜记录时不限时间")
        since = last["created_at"] if last else None
        return self.domain.con.execute(
            "SELECT COUNT(*) n FROM events WHERE kind='round.ended' AND ref=?"
            " AND (? IS NULL OR ts > ?)", (t.id, since, since)).fetchone()["n"]

    def mirror_due(self, target: str) -> bool:
        return self.rounds_since_mirror(target) >= self.mirror_every

    # ---------- 双输出(判定 + 新观察) ----------
    def observations(self, target: str) -> int:
        t = self.domain.target(target)
        return self.domain.con.execute(
            "SELECT COUNT(*) n FROM observations WHERE target_id=?", (t.id,)).fetchone()["n"]

    # ---------- 研究进度(给模型看的"当前手头有什么没做完") ----------
    def progress_brief(self, target: str, run_id: str | None = None) -> list[str]:
        """把**未完成的动作**摆到模型面前:它是数据,不是脚本步骤。

        演练复盘发现:模型跑完实验后不知道要结束 attempt / 复核 evidence / 下结论,
        于是 18 次调用后仍 0 结论。这里只呈现"待办状态 + 对应工具",不替模型做选择。
        """
        con = self.domain.con
        t = self.domain.target(target)
        out: list[str] = []

        running = con.execute(
            "SELECT a.id, a.hypothesis_id, a.method, h.title FROM attempts a"
            " JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE h.target_id=? AND a.result='running' ORDER BY a.started_at", (t.id,)).fetchall()
        if running:
            out.append("### 运行中的实验(需要你结束它,否则假设无法收口)")
            for r in running:
                out.append(f"- {r['id']}(属于 {r['hypothesis_id']}: {r['title'][:60]})"
                           f" 方法: {(r['method'] or '')[:80]}")
            out.append("  结束方式:`state_finish_attempt(attempt_id, result=supported|refuted|"
                       "inconclusive|environment_failure|no_signal|cancelled, note=…, obs_outcome=new|none,"
                       " obs_reason=…)`")

        orphan_ev = con.execute(
            "SELECT id, substr(path,-40) p FROM evidence WHERE target_id=? AND tool_call_id IS NULL"
            "   AND verified=1 ORDER BY created_at DESC LIMIT 5", (t.id,)).fetchall()
        if orphan_ev:
            out.append("### 已复核但**没有来源关联**的证据(审计会打折)")
            for r in orphan_ev:
                out.append(f"- {r['id']} {r['p']} ⇒ 用 `state_link_evidence(evidence_id='{r['id']}',"
                           " tool_call_id=…)` 补来源(**不新建证据**);若只是命令里写了这个路径会是"
                           " command-write 弱来源")
        pending_ev = con.execute(
            "SELECT id, kind, path FROM evidence WHERE target_id=? AND verified=0"
            " ORDER BY created_at DESC LIMIT 8", (t.id,)).fetchall()
        if pending_ev:
            out.append("### 未复核的证据(不能支撑结论,先复核)")
            for r in pending_ev:
                out.append(f"- {r['id']} [{r['kind']}] {os.path.basename(r['path'] or '')}")
            out.append("  复核方式:`state_verify_evidence(evidence_id, method=recheck-sha256|"
                       "reproduce-attempt|cross-check, …)`;哈希只证明产物完整性,"
                       "因果结论要用 reproduce-attempt / cross-check")

        open_h = con.execute(
            "SELECT id, title, state FROM hypotheses WHERE target_id=?"
            " AND state IN ('open','investigating') ORDER BY created_at LIMIT 8", (t.id,)).fetchall()
        if open_h:
            out.append("### 未收口的假设")
            for r in open_h:
                out.append(f"- {r['id']} [{r['state']}] {r['title'][:80]}")
            out.append("  收口方式:`state_conclude(hypothesis_id, verdict=vulnerable|no_finding|"
                       "inconclusive, scope_note=…)`(vulnerable 需 supported 尝试 + 已复核的因果证据)")

        # 收口就绪(程序可核):决定性尝试都在,证据都复核过 ⇒ 明确提示可以下结论。
        # 只提示"条件已满足",不替模型选 verdict。
        ready = con.execute(
            "SELECT h.id, h.title,"
            "  SUM(CASE WHEN a.result='supported' THEN 1 ELSE 0 END) sup,"
            "  SUM(CASE WHEN a.result='refuted' THEN 1 ELSE 0 END) ref,"
            "  SUM(CASE WHEN a.result IN ('inconclusive','environment_failure','no_signal',"
            "                          'cancelled') THEN 1 ELSE 0 END) nondec,"
            "  (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id) ev,"
            "  (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=1) ev_ok,"
            "  (SELECT COUNT(DISTINCT c.evidence_id) FROM evidence_checks c"
            "     JOIN evidence e2 ON e2.id=c.evidence_id"
            "    WHERE e2.hypothesis_id=h.id AND c.method IN"
            "          ('reproduce-attempt','cross-check')) ev_causal,"
            "  (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=0) ev_open"
            " FROM hypotheses h LEFT JOIN attempts a ON a.hypothesis_id=h.id"
            " WHERE h.target_id=? AND h.state IN ('open','investigating')"
            " GROUP BY h.id", (t.id,)).fetchall()
        hints = []
        for r in ready:
            if r["sup"] and r["ev_causal"]:
                hints.append(f"- {r['id']} 有 supported 尝试且已有 {r['ev_causal']} 条因果复核证据"
                             f"(reproduce-attempt/cross-check)⇒ 可 `state_conclude("
                             f"hypothesis_id='{r['id']}', verdict='vulnerable', scope_note=…"
                             f"`(scope 要写清只管哪个入口/参数/版本)")
            elif r["ref"] and r["ev_ok"]:
                hints.append(f"- {r['id']} 有 refuted 尝试且证据已复核 ⇒ 可 `state_conclude("
                             f"hypothesis_id='{r['id']}', verdict='no_finding', scope_note=…"
                             f"`(否证也要写清范围)")
            elif r["sup"] and not r["ev_causal"]:
                hints.append(f"- {r['id']} 已有 supported 尝试,但只有 {r['ev_ok']} 条复核证据、"
                             "其中 0 条是因果复核 ⇒ 先 state_verify_evidence("
                             "method=reproduce-attempt|cross-check);**只有 recheck-sha256 不够**")
            if r["ev_open"] and r["ev_ok"]:
                hints.append(f"- {r['id']} 另有 {r['ev_open']} 条证据未复核:不影响收口,"
                             "但结论不得引用未复核证据")
        if hints:
            out = ["### 已具备收口条件(程序按事实判断;verdict 由你定)", *hints,
                   ""] + out

        no_attempt = con.execute(
            "SELECT h.id, h.title FROM hypotheses h WHERE h.target_id=?"
            "   AND h.state IN ('open','investigating')"
            "   AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.hypothesis_id=h.id)"
            " ORDER BY h.created_at LIMIT 5", (t.id,)).fetchall()
        if no_attempt:
            out.append("### 已提出但还没开始验证的假设")
            for r in no_attempt:
                out.append(f"- {r['id']} {r['title'][:70]} ⇒ 先 `state_start_attempt("
                           f"hypothesis_id='{r['id']}', method=…)` 记录你打算怎么验证")
        fails = con.execute(
            "SELECT tool, status, note FROM tool_calls WHERE run_id IN"
            " (SELECT id FROM runs WHERE target_id=?) AND status!='ok'"
            " ORDER BY created_at DESC LIMIT 3", (t.id,)).fetchall()
        if fails:
            out.append("### 最近的工具失败(先换方法,不要把失败当研究结论)")
            for r in fails:
                out.append(f"- {r['tool']} → {r['status']}: {(r['note'] or '')[:120]}")
        if run_id:
            n_tc = con.execute("SELECT COUNT(*) n FROM tool_calls WHERE run_id=?",
                               (run_id,)).fetchone()["n"]
            n_ev = con.execute(
                "SELECT COUNT(*) n FROM evidence e JOIN tool_calls t ON t.id=e.tool_call_id"
                " WHERE t.run_id=?", (run_id,)).fetchone()["n"]
            n_obs = con.execute(
                "SELECT COUNT(*) n FROM observations WHERE target_id=? AND created_at > "
                "(SELECT COALESCE(MAX(started_at),'1970') FROM runs WHERE id=?)",
                (t.id, run_id)).fetchone()["n"]
            if n_tc >= 10 and n_ev == 0 and n_obs == 0:
                out.append("### 本 run 的产出还没入账(提醒,不是配额)")
                out.append(f"- 本 run 已执行 {n_tc} 次工具调用,但还没有任何 observation/evidence;"
                           "**已确认的逆向事实**(地址/字符串/调用关系)用 "
                           "state_add_observation(target, text=…) 记下来;"
                           "有可追溯产物再用 state_add_evidence(path=…, "
                           "tool_call_id=<db_tool_call_id>) 登记并复核;纯探索不必登记。")
        return out

    # ---------- 系统提示 ----------
    # ---------- 系统提示 ----------
    def rules_prompt(self, target: str) -> str:
        """**静态**系统提示:逐字节稳定(它是请求的第一条消息,一变就让前缀缓存整体失效)。

        实测:同一 run 内 system 一变,下一次调用的缓存命中从 ~98% 掉到 ~1%
        (数据见 docs/evidence/token-audit.md)。动态状态一律走 `state_prompt()`。
        """
        cards = kb_titles(self.root)
        parts = [
            "你是固件漏洞研究 agent。程序负责机器事实的可靠性与不变量,你负责研究判断与行动。",
            "",
            "## 不变量(程序会拒绝,不是建议)",
            "- 尝试结果六种:supported / refuted / inconclusive / environment_failure / no_signal / cancelled。",
            "  后三种**不能**推出结论;refuted 必须挂证据;结论必须写 scope_note;",
            "  vulnerable 需要 supported 尝试 + 证据;no_finding 需要 refuted 尝试 + 至少一条经复核证据;"
            "inconclusive 要写清还缺什么。",
            "- 面判 vulnerable 需要 evidence_id;判 no_finding / out_of_scope 要写理由。",
            "- 工具失败/超时/没打到目标 ≠ 漏洞不存在。",
            "",
            "## 工作方式",
            "- 优先解决最可能改变判断的关键未知;主动找能推翻当前解释的反例;",
            "- 不用抽样代表整体;结论写清适用范围;",
            "- 需要结束时输出 `FINAL: <小结>` —— 但这只结束本轮,是否可收尾由程序判定。",
        ]
        if cards:
            parts += ["", "## 可选方法/判据(开放清单,来自 knowledge/,不是封闭技术表)", ""]
            parts += [f"- {c}" for c in cards[:40]]
        for rel in self.rules_files:
            body = select_rules(_read(os.path.join(self.root, rel), 10 ** 6), RULES_LIMIT)
            if body:
                parts += ["", f"## 规则原文({rel})", body]
        return "\n".join(parts)

    def state_prompt(self, target: str, goal: str | None = None,
                     run_id: str | None = None, context_note: str | None = None) -> str:
        """**动态**状态:放在消息**末尾**(历史之后),变化只影响尾部,不破坏前缀缓存。"""
        d = self.domain.derived(target)
        pool = self.pool(target)
        parts = ["## 现状(程序生成,不是用户的新指令)", "```json",
                 __import__("json").dumps(d, ensure_ascii=False, indent=1)[:3000], "```"]
        # 跨 run 继承:把已有假设/证据**事实**摆出来(不是让你重做);
        # 只有新 run 看得到这些,才不会从零开始重复第一阶段分析
        t = self.domain.target(target)
        hyps = list(self.domain.con.execute(
            "SELECT id,state,verdict,title,scope_note FROM hypotheses"
            " WHERE target_id=? ORDER BY updated_at DESC LIMIT 25", (t.id,)))
        evs = list(self.domain.con.execute(
            "SELECT id,path,link_relation,tool_call_id FROM evidence WHERE target_id=?"
            " ORDER BY created_at DESC LIMIT 25", (t.id,)))
        # 观察 = 已确认但还不足以成假设的技术事实;不注入的话跨 run 就丢了
        obs = list(self.domain.con.execute(
            "SELECT id,text,tool_call_id,path,relation FROM observations WHERE target_id=?"
            " ORDER BY created_at DESC LIMIT 15", (t.id,)))
        if hyps or evs or obs:
            facts = ["## 已有研究事实(程序从数据库读出;先复用,不要重做)"]
            facts += _facts_lines(hyps, evs, obs)
            parts += ["\n".join(facts)[:3200]]
        brief = self.progress_brief(target, run_id)
        if brief:
            parts += ["", "## 研究进度(程序按数据库事实生成;这些是待办,不是必须按顺序执行的脚本)",
                      *brief]
        if goal:
            parts += ["", f"## 本轮目标\n{goal}"]
        if pool:
            parts += ["", "## 假设池(唯一队列,程序按可达性/复杂度排序;你可以推翻排序)", ""]
            for h in pool:
                parts.append(f"- {h['id']} [{h['state']}] {h['title'][:120]}")
        if self.mirror_due(target):
            parts += ["", f"## 三镜刷新(due:{self.rounds_since_mirror(target)} 轮未做)",
                      "面/格收口时用 state.record_mirror 记一行:机制=… / 作者=… / 状态=… / 新面=…"]
        if context_note:
            # 上下文事实:只在这里(尾部),不进 system、不写 history/checkpoint
            parts += ["", context_note]
        return "\n".join(parts)

    def system_prompt(self, target: str, goal: str | None = None,
                      run_id: str | None = None) -> str:
        """兼容入口 = 静态规则 + 动态状态(运行时把动态部分放到消息末尾)。"""
        return self.rules_prompt(target) + "\n\n" + self.state_prompt(target, goal, run_id)
