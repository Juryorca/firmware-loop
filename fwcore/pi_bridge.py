"""Pi 兼容适配器:把 Pi 侧的研究动作**转成领域写操作**。

原则(避免"第二套研究状态权威")
- Pi 只负责"调用工具/跑实验";研究事实(假设/尝试/证据/结论)一律写进 fwcore 的权威状态。
- 桥只做三件事:校验事件、调 domain 相应操作、用 `client_event_id` 保证幂等(重放不重复)。
- 不解析 Pi 的会话文件,不从自由文本推断事实。

用法(过渡期):
  1) Pi extension 在关键动作后 `spawn python3 -m fwcore pi-event`,把 JSON 写进 stdin;
  2) 或人手动: `echo '{"kind":"hypothesis.add",...}' | fw-core pi-event`
"""

from __future__ import annotations

import json

from . import db as _db
from .domain import Domain, DomainError, InvariantViolation
from .research import Research

KINDS = (
    "hypothesis.add", "attempt.start", "attempt.finish", "observation.add",
    "mirror.record", "user.input_request",
)


def _already_applied(con, client_event_id: str) -> bool:
    row = con.execute("SELECT 1 FROM events WHERE kind='pi.event' AND ref=?", (client_event_id,)).fetchone()
    return row is not None


def handle_event(con, root: str, event: dict) -> dict:
    """应用一条 Pi 事件。返回 {status, ...};幂等:同一 client_event_id 只生效一次。"""
    kind = event.get("kind")
    cid = event.get("client_event_id")
    if kind not in KINDS:
        return {"status": "error", "error": f"未知事件类型: {kind}(允许: {', '.join(KINDS)})"}
    if not cid:
        return {"status": "error", "error": "缺 client_event_id(幂等所需)"}
    if _already_applied(con, cid):
        return {"status": "duplicate", "client_event_id": cid}

    dom = Domain(con, root)
    try:
        if kind == "hypothesis.add":
            hid = dom.add_hypothesis(event["target"], event["title"],
                                     body=event.get("body"), source=event.get("source", "pi"),
                                     priority=int(event.get("priority", 100)),
                                     surface_key=event.get("surface_key"))
            out = {"hypothesis_id": hid}
        elif kind == "attempt.start":
            out = {"attempt_id": dom.start_attempt(event["hypothesis_id"], event["method"],
                                                   event.get("params"))}
        elif kind == "attempt.finish":
            ev_ids = []
            for e in event.get("evidence") or []:
                ev_ids.append(dom.add_evidence(event["target"], e.get("kind", "log"),
                                               path=e.get("path"), capture=e.get("capture"),
                                               attempt_id=event["attempt_id"],
                                               hypothesis_id=event.get("hypothesis_id")))
            dom.finish_attempt(event["attempt_id"], event["result"], event.get("note", ""),
                               evidence=ev_ids, obs_outcome=event.get("obs_outcome"),
                               obs_reason=event.get("obs_reason", ""))
            out = {"attempt_id": event["attempt_id"], "result": event["result"],
                   "evidence": ev_ids}
        elif kind == "observation.add":
            out = {"observation_id": dom.add_observation(event["target"], event["text"],
                                                         attempt_id=event.get("attempt_id"))}
        elif kind == "mirror.record":
            out = {"mirror_id": Research(root, dom).record_mirror(
                event["target"], event["mechanism"], event["author"], event["state"],
                event.get("new_faces", "无(判据: 同机制未找到新面)"))}
        else:  # user.input_request
            dom.request_user_input(event["run_id"], event["reason"])
            out = {"run_id": event["run_id"]}
    except (DomainError, InvariantViolation, KeyError) as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    with _db.Tx(con) as c:
        c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES"
                  "(strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'pi.event',?,?)",
                  (cid, json.dumps({"kind": kind, **out}, ensure_ascii=False)))
    return {"status": "ok", "kind": kind, **out}


def handle_stream(con, root: str, text: str) -> list[dict]:
    """逐行处理(一行一个 JSON 事件)。坏行只报错,不影响其它事件。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            out.append({"status": "error", "line": i, "error": f"非法 JSON: {e}"})
            continue
        r = handle_event(con, root, ev)
        r.setdefault("line", i)
        out.append(r)
    return out
