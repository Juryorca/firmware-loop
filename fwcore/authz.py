"""研究授权:用户**显式**授予"某目标 + 某些工作区 + 时限"的研究权限。

为什么要有它
- 用户要求:模型要能自主跑分析/逆向/仿真,但**不能**靠"全局放行高风险工具"来绕过 ✗。
- 所以授权是**按目标、可过期、可撤销、留审计**的:授权文件 `state/<target>/authz.json`。
- 没有授权 ⇒ 高风险工具 / shell 仍然拒绝(默认拒绝);有授权 ⇒ 授权范围内的分析自主运行;
  外部动作(MCP 里 submit/upload/login 之类)永远另需人类确认(由 ToolSpec.external 控制)。
- 明确边界:这只是**授权 + 路径检查**,不是文件系统沙箱(见 Authz.isolation)。
"""

from __future__ import annotations

import contextlib
import json
import os
import time

from .policy import Authz

FILE = "authz.json"


def path_for(root: str, target: str) -> str:
    return os.path.join(root, "state", target, FILE)


def grant(root: str, target: str, *, roots: list[str], hours: float = 8.0,
          note: str = "", by: str = "user", allow_shell: bool = True,
          allow_high_risk: bool = True, allow_mcp: bool = True) -> dict:
    """写授权文件。roots 之外的路径一律不允许(即便有授权)。"""
    if not roots:
        raise ValueError("授权必须给出至少一个工作区根(roots)")
    abs_roots = [os.path.realpath(r if os.path.isabs(r) else os.path.join(root, r)) for r in roots]
    data = {
        "target": target,
        "roots": abs_roots,
        "granted_at": _now(),
        "expires_at": time.time() + hours * 3600 if hours and hours > 0 else None,
        "note": note,
        "granted_by": by,
        "allow_shell": allow_shell,
        "allow_high_risk": allow_high_risk,
        "allow_mcp": allow_mcp,
        "revoked": False,
    }
    p = path_for(root, target)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        raise ValueError(f"写授权文件失败 {p}: {e}") from e
    return data


def revoke(root: str, target: str, reason: str = "") -> dict:
    p = path_for(root, target)
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        data["revoked"] = True
        data["revoked_at"] = _now()
        data["revoke_reason"] = reason
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"撤销授权失败 {p}: {e}") from e
    return data


def load(root: str, target: str) -> Authz | None:
    """读授权文件 ⇒ Authz;没有文件返回 None(调用方据此保持默认拒绝)。"""
    p = path_for(root, target)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    az = Authz(
        roots=list(d.get("roots") or []),
        allow_shell=bool(d.get("allow_shell", True)),
        allow_high_risk=bool(d.get("allow_high_risk", True)),
        expires_at=d.get("expires_at"),
        revoked=bool(d.get("revoked")),
        reason=(d.get("note") or "") + (" [revoked]" if d.get("revoked") else ""),
        isolation="none",
    )
    az.source = f"authz.json:{target}"            # type: ignore[attr-defined]
    az.target = target                             # type: ignore[attr-defined]
    return az


def describe(root: str, target: str) -> dict:
    az = load(root, target)
    if az is None:
        return {"target": target, "authorized": False,
                "note": ("无授权文件 ⇒ 高风险工具与 shell 一律拒绝(默认拒绝);"
                         f"授权方式:`fw-core authz grant {target} --roots <dir> --hours 8`")}
    d = az.describe()
    d.update({"target": target, "authorized": az.active(),
              "file": os.path.relpath(path_for(root, target), root)})
    return d


def audit(con, kind: str, target: str, payload: dict | None = None) -> None:
    """授权相关动作写进 events(可审计)。"""
    with contextlib.suppress(Exception):
        con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES"
                    "(strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),?,?,?)",
                    (kind, target, json.dumps(payload or {}, ensure_ascii=False)))


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="seconds")
