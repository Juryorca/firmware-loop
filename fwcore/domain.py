"""领域层:研究事实的唯一写入口 + 不变量。

职责边界(见 docs/ARCHITECTURE.md)
- **模型**决定研究什么、怎么研究;**本层不决定研究策略**,只保证机器事实可靠。
- 本层持有:目标 / 攻击面 / 假设 / 尝试 / 观察 / 证据 / 结论 / 事件。
- 只有本层能写这些表;外部一律调用这里的操作。

关键不变量(每条都有测试)
1. 一次 **环境失败 / 信号不足 / 超时 / 取消** 的尝试,不得把假设变成结论。
2. `refuted`(否证)必须有该尝试名下的证据;否则拒绝(避免"文字收口")。
3. 结论必须带 scope(适用范围);`vulnerable` 必须挂证据。
4. 目标不存在 ⇒ 状态 `unknown`;存在但没有任何假设 ⇒ `empty`。二者不混。
5. 有未处理假设 / 未判定面 / 仍在 running 的尝试 ⇒ 不允许"收尾"。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass

from .db import Tx

ATTEMPT_RESULTS = (
    "supported",
    "refuted",
    "inconclusive",
    "environment_failure",
    "no_signal",
    "cancelled",
)
# 这些结果**不能**推导出研究结论(只表示"这次没得到判定力")
NON_DECISIVE = ("environment_failure", "no_signal", "cancelled")
DISPOSITIONS = ("unjudged", "vulnerable", "no_finding", "out_of_scope")


class DomainError(RuntimeError):
    pass


class NotFound(DomainError):
    pass


class InvariantViolation(DomainError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{_dt.datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4)}"


def _event(con, kind: str, ref: str | None, payload: dict | None = None) -> None:
    con.execute(
        "INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
        (_now(), kind, ref, json.dumps(payload or {}, ensure_ascii=False)),
    )


@dataclass
class Target:
    id: str
    name: str
    version: str | None


class Domain:
    def __init__(self, con: sqlite3.Connection, root: str = "."):
        self.con = con
        self.root = root

    # ---------------- 目标 ----------------
    def create_target(
        self,
        name: str,
        *,
        vendor: str | None = None,
        product: str | None = None,
        version: str | None = None,
        arch: str | None = None,
        source_url: str | None = None,
        source_sha256: str | None = None,
    ) -> str:
        tid = new_id("T")
        with Tx(self.con) as c:
            try:
                c.execute(
                    "INSERT INTO targets(id,name,vendor,product,version,arch,source_url,source_sha256,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (tid, name, vendor, product, version, arch, source_url, source_sha256, _now()),
                )
            except sqlite3.IntegrityError as e:
                raise InvariantViolation(f"目标已存在: {name}") from e
            _event(c, "target.created", tid, {"name": name, "version": version})
        return tid

    def target(self, name: str) -> Target:
        row = self.con.execute("SELECT id,name,version FROM targets WHERE name=?", (name,)).fetchone()
        if not row:
            raise NotFound(f"目标不存在: {name}")
        return Target(row["id"], row["name"], row["version"])

    def status(self, name: str) -> dict:
        """UNKNOWN(没有这个目标) ≠ EMPTY(目标在,但还没有假设)。"""
        try:
            t = self.target(name)
        except NotFound:
            return {"state": "unknown", "target": name}
        n_hyp = self.con.execute(
            "SELECT COUNT(*) n FROM hypotheses WHERE target_id=?", (t.id,)
        ).fetchone()["n"]
        return {
            "state": "empty" if n_hyp == 0 else "active",
            "target": t.name,
            "version": t.version,
            "hypotheses": n_hyp,
            "open": self._count("hypotheses", t.id, "state IN ('open','investigating')"),
            "attempts_running": self._count("attempts a JOIN hypotheses h ON h.id=a.hypothesis_id",
                                            t.id, "a.result='running'"),
            "surfaces_unjudged": self._count("surfaces", t.id, "disposition='unjudged'"),
        }

    def _count(self, table: str, target_id: str, where: str) -> int:
        col = "target_id"
        sql = f"SELECT COUNT(*) n FROM {table} WHERE {col}=? AND {where}"
        return self.con.execute(sql, (target_id,)).fetchone()["n"]

    # ---------------- 攻击面 ----------------
    def add_surface(self, target: str, key: str, **attrs) -> str:
        t = self.target(target)
        sid = new_id("S")
        disp = attrs.get("disposition", "unjudged")
        if disp not in DISPOSITIONS:
            raise InvariantViolation(f"disposition 非法: {disp}")
        with Tx(self.con) as c:
            try:
                c.execute(
                    "INSERT INTO surfaces(id,target_id,key,reachability,input_control,complexity,"
                    "understanding,disposition,note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (sid, t.id, key, attrs.get("reachability"), attrs.get("input_control"),
                     attrs.get("complexity"), attrs.get("understanding"), disp, attrs.get("note")),
                )
            except sqlite3.IntegrityError as e:
                raise InvariantViolation(f"面已存在: {key}") from e
            _event(c, "surface.added", sid, {"key": key, "disposition": disp})
        return sid

    def set_surface_disposition(self, target: str, key: str, disposition: str, *,
                                evidence_id: str | None = None, note: str | None = None) -> None:
        """面处置不是随手改的状态:判 vulnerable 必须有证据,判 no_finding/out_of_scope 必须写理由。"""
        if disposition not in DISPOSITIONS:
            raise InvariantViolation(f"disposition 非法: {disposition}")
        t = self.target(target)
        if disposition == "vulnerable":
            if not evidence_id:
                raise InvariantViolation("面判 vulnerable 必须给 evidence_id")
            ev = self.con.execute("SELECT target_id FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if not ev:
                raise NotFound(f"证据不存在: {evidence_id}")
            if ev["target_id"] != t.id:
                raise InvariantViolation("证据不属于该目标")
        elif disposition in ("no_finding", "out_of_scope"):
            if not (note or "").strip() or len(note.strip()) < 8:
                raise InvariantViolation(f"面判 {disposition} 必须写理由(≥8 字):管到哪些输入/为什么不做")
        with Tx(self.con) as c:
            cur = c.execute(
                "UPDATE surfaces SET disposition=?, judged_by=?, note=COALESCE(?, note)"
                " WHERE target_id=? AND key=?",
                (disposition, evidence_id or (note or "")[:200] or None, note, t.id, key),
            )
            if cur.rowcount == 0:
                raise NotFound(f"面不存在: {key}")
            _event(c, "surface.disposition", key,
                   {"disposition": disposition, "evidence": evidence_id})

    # ---------------- 假设 ----------------
    def add_hypothesis(self, target: str, title: str, *, body: str | None = None,
                       source: str | None = None, priority: int = 100,
                       surface_key: str | None = None) -> str:
        t = self.target(target)
        hid = new_id("H")
        now = _now()
        with Tx(self.con) as c:
            if surface_key:
                ok = c.execute("SELECT 1 FROM surfaces WHERE target_id=? AND key=?",
                               (t.id, surface_key)).fetchone()
                if not ok:
                    raise InvariantViolation(f"关联的攻击面不存在: {surface_key}")
            c.execute(
                "INSERT INTO hypotheses(id,target_id,title,body,source,priority,state,surface_key,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,'open',?,?,?)",
                (hid, t.id, title, body, source, priority, surface_key, now, now),
            )
            _event(c, "hypothesis.added", hid, {"title": title, "source": source})
        return hid

    def hypothesis(self, hid: str) -> sqlite3.Row:
        row = self.con.execute("SELECT * FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if not row:
            raise NotFound(f"假设不存在: {hid}")
        return row

    def start_attempt(self, hid: str, method: str, params: dict | None = None) -> str:
        self.hypothesis(hid)
        aid = new_id("A")
        with Tx(self.con) as c:
            c.execute(
                "INSERT INTO attempts(id,hypothesis_id,method,params,result,started_at)"
                " VALUES(?,?,?,?,'running',?)",
                (aid, hid, method, json.dumps(params or {}, ensure_ascii=False), _now()),
            )
            c.execute("UPDATE hypotheses SET state='investigating', updated_at=? WHERE id=? AND state='open'",
                      (_now(), hid))
            _event(c, "attempt.started", aid, {"hypothesis": hid, "method": method})
        return aid

    def _validate_evidence_ids(self, ids: list[str], *, target_id: str,
                               attempt_id: str | None = None, hypothesis_id: str | None = None) -> list[str]:
        """证据 id 必须存在、属于该目标,且(若给了 attempt/hypothesis)确实挂在其名下。"""
        out = []
        for eid in ids or []:
            row = self.con.execute(
                "SELECT target_id, attempt_id, hypothesis_id FROM evidence WHERE id=?", (eid,)
            ).fetchone()
            if not row:
                raise NotFound(f"证据不存在: {eid}")
            if row["target_id"] != target_id:
                raise InvariantViolation(f"证据 {eid} 不属于该目标")
            if attempt_id and row["attempt_id"] not in (attempt_id, None):
                raise InvariantViolation(f"证据 {eid} 不属于该尝试")
            if hypothesis_id and row["hypothesis_id"] not in (hypothesis_id, None):
                raise InvariantViolation(f"证据 {eid} 不属于该假设")
            out.append(eid)
        return out

    def note_observation_outcome(self, aid: str, outcome: str, reason: str = "") -> None:
        """双输出的观察一侧:new(已登记观察)/ none(无新观察,需理由)。"""
        if outcome not in ("new", "none"):
            raise InvariantViolation("obs_outcome 只能是 new 或 none")
        row = self.con.execute(
            "SELECT a.id, a.hypothesis_id, h.target_id FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE a.id=?", (aid,)).fetchone()
        if not row:
            raise NotFound(f"尝试不存在: {aid}")
        if outcome == "none" and len((reason or "").strip()) < 8:
            raise InvariantViolation("声明「无新观察」必须写理由(为什么这次结果在预期内)")
        if outcome == "new":
            n = self.con.execute("SELECT COUNT(*) n FROM observations WHERE attempt_id=?", (aid,)).fetchone()["n"]
            if n == 0:
                raise InvariantViolation("声明「有新观察」但没登记任何 observation")
        with Tx(self.con) as c:
            c.execute("UPDATE attempts SET obs_outcome=?, obs_reason=? WHERE id=?", (outcome, reason or None, aid))
            _event(c, "attempt.observation_outcome", aid, {"outcome": outcome})

    def finish_attempt(self, aid: str, result: str, note: str = "", evidence: list[str] | None = None,
                       obs_outcome: str | None = None, obs_reason: str = "") -> None:
        """结束一次尝试。

        不变量:
        - result 必须合法;
        - `refuted` 必须至少有一条挂在该尝试下的证据(否则视为"文字否证");
        - 非判定性结果(环境失败/信号不足/取消)不会把假设关掉。
        """
        if result not in ATTEMPT_RESULTS:
            raise InvariantViolation(f"result 非法: {result}(允许: {', '.join(ATTEMPT_RESULTS)})")
        row = self.con.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
        if not row:
            raise NotFound(f"尝试不存在: {aid}")
        if row["result"] != "running":
            raise InvariantViolation(f"尝试已结束({row['result']}),不能重复结束")
        tgt = self.con.execute(
            "SELECT h.target_id FROM hypotheses h JOIN attempts a ON a.hypothesis_id=h.id WHERE a.id=?",
            (aid,)).fetchone()
        ev_ids = self._validate_evidence_ids(evidence or [], target_id=tgt["target_id"],
                                            attempt_id=aid, hypothesis_id=row["hypothesis_id"])
        if obs_outcome:
            self.note_observation_outcome(aid, obs_outcome, obs_reason)
        with Tx(self.con) as c:
            if result == "inconclusive" and not (note or "").strip():
                raise InvariantViolation(
                    "inconclusive 必须写清楚:已排除什么 / 还缺什么才能判定(否则这条不算研究进展)"
                )
            if result == "refuted":
                n = c.execute("SELECT COUNT(*) n FROM evidence WHERE attempt_id=?", (aid,)).fetchone()["n"]
                if n + len(ev_ids) == 0:
                    raise InvariantViolation("refuted 需要证据:先 add_evidence(绑 attempt)再否证")
            c.execute("UPDATE attempts SET result=?, note=?, finished_at=? WHERE id=?",
                      (result, note, _now(), aid))
            if result == "supported":
                c.execute("UPDATE hypotheses SET state='investigating', updated_at=? WHERE id=? AND state!='concluded'",
                          (_now(), row["hypothesis_id"]))
            elif result in NON_DECISIVE:
                # 保持 open/investigating —— 工具或环境失败不是研究结论
                c.execute(
                    "UPDATE hypotheses SET updated_at=? WHERE id=? AND state='investigating'",
                    (_now(), row["hypothesis_id"]),
                )
            _event(c, "attempt.finished", aid, {"result": result, "hypothesis": row["hypothesis_id"]})

    # ---------------- 观察 / 证据 ----------------
    def add_observation(self, target: str, text: str, *, attempt_id: str | None = None) -> str:
        t = self.target(target)
        oid = new_id("O")
        with Tx(self.con) as c:
            c.execute("INSERT INTO observations(id,target_id,attempt_id,text,created_at) VALUES(?,?,?,?,?)",
                      (oid, t.id, attempt_id, text, _now()))
            _event(c, "observation.added", oid, {"attempt": attempt_id})
        return oid

    def promote_observation(self, oid: str, title: str, *, priority: int = 100) -> str:
        """观察 → 新假设(来源保持开放:任何活动产生的观察都能入池)。"""
        row = self.con.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
        if not row:
            raise NotFound(f"观察不存在: {oid}")
        t = self.con.execute("SELECT name FROM targets WHERE id=?", (row["target_id"],)).fetchone()
        return self.add_hypothesis(t["name"], title, body=row["text"],
                                   source=f"observation:{oid}", priority=priority)

    def add_evidence(self, target: str, kind: str, *, path: str | None = None,
                     capture: str | None = None, attempt_id: str | None = None,
                     hypothesis_id: str | None = None, version: str | None = None) -> str:
        """证据必须绑定目标与版本;文件类证据自动算 sha256。"""
        t = self.target(target)
        if attempt_id:
            a = self.con.execute(
                "SELECT h.target_id FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id WHERE a.id=?",
                (attempt_id,)).fetchone()
            if not a:
                raise NotFound(f"尝试不存在: {attempt_id}")
            if a["target_id"] != t.id:
                raise InvariantViolation("attempt 不属于该目标(证据不能跨目标关联)")
        if hypothesis_id:
            h = self.con.execute("SELECT target_id FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone()
            if not h:
                raise NotFound(f"假设不存在: {hypothesis_id}")
            if h["target_id"] != t.id:
                raise InvariantViolation("hypothesis 不属于该目标")
        ver = version if version is not None else (t.version or "unknown")
        sha = None
        if path:
            full = path if os.path.isabs(path) else os.path.join(self.root, path)
            if not os.path.isfile(full):
                raise NotFound(f"证据文件不存在: {path}")
            h = hashlib.sha256()
            with open(full, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            sha = h.hexdigest()
        if capture is None and sha is None:
            raise InvariantViolation("证据需要 path 或 capture 至少其一")
        eid = new_id("E")
        with Tx(self.con) as c:
            c.execute(
                "INSERT INTO evidence(id,target_id,attempt_id,hypothesis_id,kind,version,path,sha256,capture,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (eid, t.id, attempt_id, hypothesis_id, kind, ver, path, sha, capture, _now()),
            )
            _event(c, "evidence.added", eid, {"kind": kind, "attempt": attempt_id, "path": path})
        return eid

    # ---------------- 结论 ----------------
    def conclude(self, hid: str, verdict: str, scope_note: str) -> str:
        """给假设下结论。程序可核的前置条件在这里强制。"""
        if verdict not in ("vulnerable", "no_finding", "inconclusive"):
            raise InvariantViolation(f"verdict 非法: {verdict}")
        if not scope_note or len(scope_note.strip()) < 8:
            raise InvariantViolation("结论必须写适用范围(scope_note):这条结论管到哪个函数/入口/版本")
        h = self.hypothesis(hid)
        atts = self.con.execute("SELECT result FROM attempts WHERE hypothesis_id=?", (hid,)).fetchall()
        if not atts:
            raise InvariantViolation("没有任何尝试就下结论(先 start_attempt/finish_attempt)")
        if any(a["result"] == "running" for a in atts):
            raise InvariantViolation("还有 running 的尝试:先结束或取消")
        decisive = [a["result"] for a in atts if a["result"] in ("supported", "refuted", "inconclusive")]
        if not decisive:
            raise InvariantViolation(
                "尝试全是环境失败/信号不足/取消 ⇒ 不构成结论(保持 open,继续找有判定力的方法)"
            )
        ev = self.con.execute("SELECT COUNT(*) n FROM evidence WHERE hypothesis_id=? OR attempt_id IN "
                              "(SELECT id FROM attempts WHERE hypothesis_id=?)", (hid, hid)).fetchone()["n"]
        kinds = set(decisive)
        if verdict == "vulnerable" and "supported" not in kinds:
            raise InvariantViolation("判 vulnerable 需要至少一次 supported 尝试(当前只有 "
                                     + ", ".join(sorted(kinds)) + ")")
        if verdict == "no_finding" and "refuted" not in kinds:
            raise InvariantViolation("判 no_finding 需要至少一次 refuted 尝试(有证据的否证);"
                                     "当前只有 " + ", ".join(sorted(kinds)) + " —— 不能靠 inconclusive 收口")
        if verdict == "inconclusive" and "inconclusive" not in kinds:
            raise InvariantViolation("判 inconclusive 需要一次明确 inconclusive 的尝试")
        if verdict == "vulnerable" and ev == 0:
            raise InvariantViolation("判 vulnerable 必须挂证据")
        cid = new_id("C")
        with Tx(self.con) as c:
            c.execute("INSERT INTO conclusions(id,target_id,hypothesis_id,verdict,scope_note,created_at)"
                      " VALUES(?,?,?,?,?,?)", (cid, h["target_id"], hid, verdict, scope_note, _now()))
            c.execute("UPDATE hypotheses SET state='concluded', verdict=?, scope_note=?, updated_at=? WHERE id=?",
                      (verdict, scope_note, _now(), hid))
            _event(c, "hypothesis.concluded", hid, {"verdict": verdict, "scope": scope_note[:200]})
        return cid

    def drop_hypothesis(self, hid: str, reason: str) -> None:
        if not reason.strip():
            raise InvariantViolation("放弃假设必须写理由")
        self.hypothesis(hid)
        with Tx(self.con) as c:
            c.execute("UPDATE hypotheses SET state='dropped', scope_note=?, updated_at=? WHERE id=?",
                      (reason, _now(), hid))
            _event(c, "hypothesis.dropped", hid, {"reason": reason[:200]})

    def request_user_input(self, run_id: str, reason: str) -> None:
        """模型主动交回控制权(合法停止之一:需要工作区外的具体东西)。"""
        if len((reason or "").strip()) < 8:
            raise InvariantViolation("请求用户输入必须写清:要什么 / 卡在哪一步")
        with Tx(self.con) as c:
            c.execute("UPDATE runs SET handback_reason=?, updated_at=? WHERE id=?",
                      ("needs_user_input: " + reason, _now(), run_id))
            _event(c, "run.request_user_input", run_id, {"reason": reason[:300]})

    # ---------------- 派生量(程序算,不靠文字) ----------------
    def derived(self, target: str) -> dict:
        t = self.target(target)
        rows = self.con.execute(
            "SELECT state, verdict, COUNT(*) n FROM hypotheses WHERE target_id=? GROUP BY state, verdict", (t.id,)
        ).fetchall()
        hyp = {"open": 0, "investigating": 0, "concluded": 0, "dropped": 0,
               "vulnerable": 0, "no_finding": 0, "inconclusive": 0}
        for r in rows:
            hyp[r["state"]] = hyp.get(r["state"], 0) + r["n"]
            if r["verdict"]:
                hyp[r["verdict"]] = hyp.get(r["verdict"], 0) + r["n"]
        surf = self.con.execute(
            "SELECT disposition, COUNT(*) n FROM surfaces WHERE target_id=? GROUP BY disposition", (t.id,)
        ).fetchall()
        surfaces = {r["disposition"]: r["n"] for r in surf}
        att = self.con.execute(
            "SELECT a.result, COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE h.target_id=? GROUP BY a.result", (t.id,)
        ).fetchall()
        attempts = {r["result"]: r["n"] for r in att}
        ev = self.con.execute("SELECT COUNT(*) n FROM evidence WHERE target_id=?", (t.id,)).fetchone()["n"]
        material = self.material_check(target)
        total_surf = sum(surfaces.values())
        judged = total_surf - surfaces.get("unjudged", 0)
        return {
            "target": t.name,
            "version": t.version,
            "hypotheses": hyp,
            "surfaces": surfaces,
            "surface_coverage": f"{judged}/{total_surf}",
            "attempts": attempts,
            "running_attempts": attempts.get("running", 0),
            "evidence": ev,
            "material": material,
            "closure_blockers": self.closure_blockers(target),
        }

    def material_check(self, target: str) -> dict:
        """报告材料完整性(程序可核的部分):advisory 骨架 / 图链 / 附件自含 / README。"""
        t = self.target(target)
        rdir = os.path.join(self.root, "state", t.name, "report")
        need = ("Summary", "Root Cause", "Steps to Reproduce", "Evidence")
        missing: list[str] = []
        advisories = []
        if not os.path.isdir(rdir):
            return {"advisories": [], "missing": ["report/ 不存在"], "ok": False}
        for name in sorted(os.listdir(rdir)):
            if not name.endswith(".md") or name in ("INDEX.md", "attack-surface.md", "CLOSURE.md"):
                continue
            advisories.append(name)
            body = open(os.path.join(rdir, name), encoding="utf-8").read()
            gone = [s for s in need if f"## {s}" not in body]
            if gone:
                missing.append(f"{name}: 缺 {', '.join(gone)}")
        # 附件包:符号链接 = 不自含;缺 README = 别人跑不起来
        att = os.path.join(rdir, "attachments")
        if os.path.isdir(att):
            for d in sorted(os.listdir(att)):
                pkg = os.path.join(att, d)
                if not os.path.isdir(pkg):
                    continue
                for r, dirs, files in os.walk(pkg):
                    for n in dirs + files:
                        p = os.path.join(r, n)
                        if os.path.islink(p):
                            missing.append(f"{d}: 含符号链接({os.path.relpath(p, pkg)})")
                if not any(x.lower().startswith("readme") for x in os.listdir(pkg)):
                    missing.append(f"{d}: 缺 README")
        return {"advisories": advisories, "missing": missing, "ok": not missing}

    def closure_blockers(self, target: str) -> list[str]:
        """程序能确认的收尾前置;都不能靠"文字收口"绕过。"""
        t = self.target(target)
        b: list[str] = []
        n_open = self.con.execute(
            "SELECT COUNT(*) n FROM hypotheses WHERE target_id=? AND state IN ('open','investigating')", (t.id,)
        ).fetchone()["n"]
        if n_open:
            b.append(f"{n_open} 条假设未收口(open/investigating)")
        n_run = self.con.execute(
            "SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE h.target_id=? AND a.result='running'", (t.id,)
        ).fetchone()["n"]
        if n_run:
            b.append(f"{n_run} 个尝试仍在 running(先结束/取消)")
        n_un = self.con.execute(
            "SELECT COUNT(*) n FROM surfaces WHERE target_id=? AND disposition='unjudged'", (t.id,)
        ).fetchone()["n"]
        if n_un:
            b.append(f"{n_un} 个面未判定(disposition=unjudged)")
        n_hyp = self.con.execute("SELECT COUNT(*) n FROM hypotheses WHERE target_id=?", (t.id,)).fetchone()["n"]
        if n_hyp == 0:
            b.append("没有任何假设(空研究状态不算完成)")
        n_surf = self.con.execute("SELECT COUNT(*) n FROM surfaces WHERE target_id=?", (t.id,)).fetchone()["n"]
        if n_surf == 0:
            b.append("没有登记任何攻击面(A1 枚举缺失)")
        n_pending = self.con.execute(
            "SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE h.target_id=? AND a.result IN ('supported','refuted','inconclusive')"
            "   AND a.obs_outcome='pending'", (t.id,)).fetchone()["n"]
        if n_pending:
            b.append(f"{n_pending} 次决定性尝试没交代观察(new/none + 理由)——双输出缺失")
        mat = self.material_check(target)
        if mat["missing"]:
            b.append("材料不完整: " + "; ".join(mat["missing"][:5]))
        return b
