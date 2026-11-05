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


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
            "open": self._count("hypotheses_open", t.id),
            "attempts_running": self._count("attempts_running", t.id),
            "surfaces_unjudged": self._count("surfaces_unjudged", t.id),
        }

    _COUNT_SQL = {
        "hypotheses_open": "SELECT COUNT(*) n FROM hypotheses WHERE target_id=? AND state IN ('open','investigating')",
        "surfaces_unjudged": "SELECT COUNT(*) n FROM surfaces WHERE target_id=? AND disposition='unjudged'",
        "attempts_running": ("SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
                             " WHERE h.target_id=? AND a.result='running'"),
    }

    def _count(self, key: str, target_id: str) -> int:
        """计数只走**白名单 SQL**(不做字符串插值,避免注入与被误用)。"""
        sql = self._COUNT_SQL.get(key)
        if sql is None:
            raise DomainError(f"未登记的计数项: {key}")
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
            reason_txt = (note or "").strip()
            if len(reason_txt) < 8:
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
                     hypothesis_id: str | None = None, version: str | None = None,
                     source: str | None = None, tool_call_id: str | None = None,
                     notes: str | None = None) -> str:
        """证据必须绑定目标与版本;文件类证据自动算 sha256。

        **工具输出 ≠ 漏洞证据**(硬):带 `tool_call_id` 建出来的证据一律 `verified=0`,
        必须研究复核后用 `promote_evidence()` 提升;`conclude(vulnerable)` 只认 `verified=1`。
        手工采集(path/capture 由研究自己给出)默认视为已复核(那是研究动作本身),但仍要记来源。
        """
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
        # 证据创建时**永远**未复核:复核是另一条独立动作(verify_evidence),要留可检查的记录
        verified = 0
        if tool_call_id:
            tc = self.con.execute("SELECT status, provider, capability, result_path FROM tool_calls"
                                  " WHERE id=?", (tool_call_id,)).fetchone()
            if not tc:
                raise NotFound(f"工具调用不存在: {tool_call_id}")
            if tc["status"] != "ok":
                raise InvariantViolation(
                    f"工具调用状态是 {tc['status']}(不是 ok)⇒ 不能作为证据来源"
                    "(失败/超时/取消都只是执行事实,不是研究证据)")
            source = source or "tool:" + (tc["provider"] or tc["capability"] or "unknown")
            if path is None and capture is None and tc["result_path"]:
                # 证据默认锚定该次调用的原始产物(路径相对 root 存,便于跨机复核)
                rp = tc["result_path"]
                path = os.path.relpath(rp, self.root) if os.path.isabs(rp) else rp
        sha = None
        if path:
            full = path if os.path.isabs(path) else os.path.join(self.root, path)
            if not os.path.isfile(full):
                raise NotFound(f"证据文件不存在: {path}")
            h = hashlib.sha256()
            try:
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            except OSError as e:
                raise DomainError(f"读证据文件失败: {e}") from e
            sha = h.hexdigest()
        if capture is None and sha is None:
            raise InvariantViolation("证据需要 path 或 capture 至少其一")
        eid = new_id("E")
        with Tx(self.con) as c:
            c.execute(
                "INSERT INTO evidence(id,target_id,attempt_id,hypothesis_id,kind,version,path,sha256,capture,"
                "source,tool_call_id,verified,notes,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, t.id, attempt_id, hypothesis_id, kind, ver, path, sha, capture,
                 source, tool_call_id, verified, notes, _now()),
            )
            _event(c, "evidence.added", eid, {"kind": kind, "attempt": attempt_id, "path": path,
                                              "source": source, "verified": verified})
        return eid

    METHODS = ("recheck-sha256", "reproduce-attempt", "cross-check")

    def verify_evidence(self, ev_id: str, method: str, *, artifact: str | None = None,
                        attempt_id: str | None = None, detail: str = "") -> str:
        """给证据留下**可检查的复核记录**,并据此把它标为 verified。

        三种方法(都能由 agent 自主执行,但都要有机器可核的一面):
        - `recheck-sha256`:重算证据文件哈希并与记录比对(纯机器检查;证明"产物没变过")
        - `reproduce-attempt`:关联一次 **supported** 的尝试(有过程记录,不是一句声明)
        - `cross-check`:交叉验证 —— 必须给 `artifact`(可检查的产物)且写明比对对象(detail)

        只靠文字声称"我验证过了"**不成立**:没有 check 行就没有 verified。
        """
        if method not in self.METHODS:
            raise InvariantViolation(f"复核方法非法: {method}(允许: {', '.join(self.METHODS)})")
        ev = self.con.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone()
        if not ev:
            raise NotFound(f"证据不存在: {ev_id}")

        art_path = art_sha = None
        if method == "recheck-sha256":
            if not ev["path"]:
                raise InvariantViolation("recheck-sha256 需要证据本身是文件(path);纯文字摘录给不出哈希")
            full = ev["path"] if os.path.isabs(ev["path"]) else os.path.join(self.root, ev["path"])
            if not os.path.isfile(full):
                raise NotFound(f"证据文件不存在: {ev['path']}")
            art_sha = _sha256_file(full)
            if not ev["sha256"]:
                raise InvariantViolation("证据没有记录哈希 ⇒ 无法复核(先按文件重新登记证据)")
            if art_sha != ev["sha256"]:
                raise InvariantViolation(
                    f"哈希不匹配:记录 {ev['sha256'][:12]}… 实际 {art_sha[:12]}… "
                    "⇒ 产物已变,旧结论不能沿用")
            art_path = ev["path"]
        elif method == "reproduce-attempt":
            if not attempt_id:
                raise InvariantViolation("reproduce-attempt 必须给出 attempt_id(哪次复现)")
            row = self.con.execute("SELECT result, hypothesis_id FROM attempts WHERE id=?",
                                   (attempt_id,)).fetchone()
            if not row:
                raise NotFound(f"尝试不存在: {attempt_id}")
            if row["result"] != "supported":
                raise InvariantViolation(
                    f"该尝试结论是 {row['result']} ⇒ 不能作为复现依据(需要 supported)")
            if ev["hypothesis_id"] and row["hypothesis_id"] != ev["hypothesis_id"]:
                raise InvariantViolation("复现尝试与证据不属于同一个假设(不能跨假设凑证据)")
        else:  # cross-check
            if not artifact:
                raise InvariantViolation(
                    "cross-check 必须给 artifact:可检查的产物(截图/日志/脚本输出),"
                    "只写文字说明不算复核")
            full = artifact if os.path.isabs(artifact) else os.path.join(self.root, artifact)
            if not os.path.isfile(full):
                raise NotFound(f"交叉验证产物不存在: {artifact}")
            if len((detail or "").strip()) < 12:
                raise InvariantViolation("cross-check 要写明比对了什么(≥12 字)")
            art_path, art_sha = artifact, _sha256_file(full)

        cid = new_id("EC")
        with Tx(self.con) as c:
            c.execute("INSERT INTO evidence_checks(id,evidence_id,method,artifact_path,artifact_sha256,"
                      "attempt_id,detail,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (cid, ev_id, method, art_path, art_sha, attempt_id, detail.strip() or None, _now()))
            c.execute("UPDATE evidence SET verified=1 WHERE id=?", (ev_id,))
            _event(c, "evidence.verified", ev_id,
                   {"method": method, "artifact": art_path, "attempt": attempt_id,
                    "detail": detail.strip()[:200]})
        return cid

    def promote_evidence(self, ev_id: str, note: str) -> None:
        """兼容旧调用:等价于 `cross-check`,但**必须**有可检查的产物(证据文件或显式 artifact)。"""
        ev = self.con.execute("SELECT path FROM evidence WHERE id=?", (ev_id,)).fetchone()
        if not ev:
            raise NotFound(f"证据不存在: {ev_id}")
        if not ev["path"]:
            raise InvariantViolation(
                "只靠文字不能复核:这条证据没有文件产物 ⇒ 用 "
                "`verify_evidence(method='cross-check', artifact=...)` 给出可检查的产物")
        self.verify_evidence(ev_id, "cross-check", artifact=ev["path"], detail=note)

    def evidence_status(self, ev_id: str) -> dict:
        """证据的复核状态与全部检查记录(审计用)。"""
        ev = self.con.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone()
        if not ev:
            raise NotFound(f"证据不存在: {ev_id}")
        checks = [dict(r) for r in self.con.execute(
            "SELECT method, artifact_path, artifact_sha256, attempt_id, detail, created_at"
            " FROM evidence_checks WHERE evidence_id=? ORDER BY created_at", (ev_id,))]
        return {"id": ev_id, "verified": bool(ev["verified"]), "sha256": ev["sha256"],
                "path": ev["path"], "checks": checks}

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
        ev_verified = self.con.execute(
            "SELECT COUNT(*) n FROM evidence WHERE verified=1 AND (hypothesis_id=? OR attempt_id IN "
            "(SELECT id FROM attempts WHERE hypothesis_id=?))", (hid, hid)).fetchone()["n"]
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
        if verdict == "vulnerable" and ev_verified == 0:
            raise InvariantViolation(
                f"证据 {ev} 条但**没有一条经研究复核**(工具跑成功 ≠ 漏洞成立):"
                "先用 promote_evidence() 写清怎么复核的(复现/交叉验证)")
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

    def confirm_close(self, target: str, note: str = "") -> None:
        """用户显式确认收尾。`closed` 只在这一步之后成立(见 runtime._handback)。"""
        t = self.target(target)
        blockers = self.closure_blockers(target)
        if blockers:
            raise InvariantViolation("还有收尾阻塞,不能确认收尾: " + "; ".join(blockers[:4]))
        with Tx(self.con) as c:
            c.execute("UPDATE targets SET status='closed_confirmed' WHERE id=?", (t.id,))
            _event(c, "target.close_confirmed", t.id, {"note": note[:200]})

    def is_close_confirmed(self, target: str) -> bool:
        row = self.con.execute("SELECT status FROM targets WHERE name=?", (target,)).fetchone()
        return bool(row and row["status"] == "closed_confirmed")

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
        try:
            entries = sorted(os.listdir(rdir))
        except OSError as e:
            return {"advisories": [], "missing": [f"report/ 不可读: {e}"], "ok": False}
        for name in entries:
            if not name.endswith(".md") or name in ("INDEX.md", "attack-surface.md", "CLOSURE.md"):
                continue
            advisories.append(name)
            try:
                with open(os.path.join(rdir, name), encoding="utf-8", errors="replace") as _f:
                    body = _f.read()
            except OSError as e:
                missing.append(f"{name}: 读取失败({e})")
                continue
            gone = [s for s in need if f"## {s}" not in body]
            if gone:
                missing.append(f"{name}: 缺 {', '.join(gone)}")
        # 附件包:符号链接 = 不自含;缺 README = 别人跑不起来
        att = os.path.join(rdir, "attachments")
        if os.path.isdir(att):
            try:
                pkgs = sorted(os.listdir(att))
            except OSError as e:
                pkgs = []
                missing.append(f"attachments/ 不可读: {e}")
            for d in pkgs:
                pkg = os.path.join(att, d)
                if not os.path.isdir(pkg):
                    continue
                try:
                    for r, dirs, files in os.walk(pkg):
                        for n in dirs + files:
                            p2 = os.path.join(r, n)
                            if os.path.islink(p2):
                                missing.append(f"{d}: 含符号链接({os.path.relpath(p2, pkg)})")
                    if not any(x.lower().startswith("readme") for x in os.listdir(pkg)):
                        missing.append(f"{d}: 缺 README")
                except OSError as e:
                    missing.append(f"{d}: 扫描失败({e})")
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
