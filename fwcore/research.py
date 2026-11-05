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
from dataclasses import dataclass, field

from .domain import Domain, new_id, _event, _now
from .db import Tx

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
            complex_bonus = 0 if re.search(r"解析|parser|状态机|协议|反序列化|XML|JSON|解压|签名", text, re.I) else 1
            unknown = 0 if not re.search(r"未理解|unknown|没看懂", text, re.I) else -1
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
        q = "SELECT COUNT(*) n FROM events WHERE kind='round.ended' AND ref=?"
        args: list = [t.id]
        if last:
            q += " AND ts > ?"
            args.append(last["created_at"])
        return self.domain.con.execute(q, args).fetchone()["n"]

    def mirror_due(self, target: str) -> bool:
        return self.rounds_since_mirror(target) >= self.mirror_every

    # ---------- 双输出(判定 + 新观察) ----------
    def observations(self, target: str) -> int:
        t = self.domain.target(target)
        return self.domain.con.execute(
            "SELECT COUNT(*) n FROM observations WHERE target_id=?", (t.id,)).fetchone()["n"]

    def dual_output_blockers(self, target: str) -> list[str]:
        """已废弃的目标级规则(保留函数以兼容调用方,永远返回空)。

        为什么删:领域层已按 **Attempt** 要求 `obs_outcome=new|none(+理由)`。
        再叠加"目标级没有任何 observation 就算缺失"会造成冲突:当所有 Attempt 都
        合法声明了 `none` 时,旧规则仍会阻止收尾,并逼模型伪造 observation。
        """
        return []

    # ---------- 系统提示 ----------
    def system_prompt(self, target: str, goal: str | None = None) -> str:
        d = self.domain.derived(target)
        pool = self.pool(target)
        cards = kb_titles(self.root)
        parts = [
            "你是固件漏洞研究 agent。程序负责机器事实的可靠性与不变量,你负责研究判断与行动。",
            "",
            "## 不变量(程序会拒绝,不是建议)",
            "- 尝试结果六种:supported / refuted / inconclusive / environment_failure / no_signal / cancelled。",
            "  后三种**不能**推出结论;refuted 必须挂证据;结论必须写 scope_note;",
            "  vulnerable 需要 supported 尝试 + 证据;no_finding 需要 refuted 尝试;inconclusive 要写清还缺什么。",
            "- 面判 vulnerable 需要 evidence_id;判 no_finding / out_of_scope 要写理由。",
            "- 工具失败/超时/没打到目标 ≠ 漏洞不存在。",
            "",
            "## 现状(程序算出的派生量)",
            "```json",
            __import__("json").dumps(d, ensure_ascii=False, indent=1)[:3000],
            "```",
        ]
        if goal:
            parts += ["", f"## 本轮目标\n{goal}"]
        if pool:
            parts += ["", "## 假设池(唯一队列,程序按可达性/复杂度排序;你可以推翻排序)", ""]
            for h in pool:
                parts.append(f"- {h['id']} [{h['state']}] {h['title'][:120]}")
        if self.mirror_due(target):
            parts += ["", f"## 三镜刷新(due:{self.rounds_since_mirror(target)} 轮未做)",
                      "面/格收口时用 state.record_mirror 记一行:机制=… / 作者=… / 状态=… / 新面=…"]
        blocks = self.dual_output_blockers(target)
        if blocks:
            parts += ["", "## 双输出提醒", *[f"- {b}" for b in blocks],
                      "任何观察(尤其是与预期不符的)用 state.add_observation 登记,并考虑升成新假设。"]
        if cards:
            parts += ["", "## 可选方法/判据(开放清单,来自 knowledge/,不是封闭技术表)", ""]
            parts += [f"- {c}" for c in cards[:40]]
        parts += ["", "## 工作方式",
                  "- 优先解决最可能改变判断的关键未知;主动找能推翻当前解释的反例;",
                  "- 不用抽样代表整体;结论写清适用范围;",
                  "- 需要结束时输出 `FINAL: <小结>` —— 但这只结束本轮,是否可收尾由程序判定。"]
        # 规则原文(唯一来源;截断到预算)
        for rel in self.rules_files:
            body = select_rules(_read(os.path.join(self.root, rel), 10 ** 6), RULES_LIMIT)
            if body:
                parts += ["", f"## 规则原文({rel})", body]
        return "\n".join(parts)
