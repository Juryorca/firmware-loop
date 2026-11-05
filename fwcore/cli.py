"""独立入口:`fw-core <cmd>`(不依赖 Pi)。

命令:
  init <target> [--version V] [--vendor X] [--sha256 H]   建目标
  hyp add <target> <title> [--body B] [--source S] [--priority N]
  surface add <target> <key> [--disposition D] [--note N]
  attempt start <hyp-id> <method> / attempt finish <attempt-id> <result> [--note N]
  evidence add <target> --kind K (--path P | --capture C) [--attempt A] [--hypothesis H]
  observe <target> --text T [--attempt A]
  conclude <hyp-id> <verdict> --scope "适用范围"
  status <target>        UNKNOWN(无此目标) / EMPTY(有目标无假设) / active
  derive <target>       派生量 + 收尾阻塞项
  migrate <target> [--notes PATH]
  view <target>         从状态生成人读 Markdown(替代手写 notes)
  run <target> [--goal G] [--model scripted|openai] [--steps N]
  resume <run-id> [--steps N]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

from . import db as _db
from .domain import Domain, DomainError
from .migrate import import_notes, verify
from .models import build_adapter, load_model_config
from .runtime import Runner


def _con(args):
    path = args.db or _db.default_db_path(args.root)
    return _db.connect(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fw-core", description="固件研究 agent 核心(宿主无关)")
    ap.add_argument("--root", default=os.environ.get("FW_ROOT", os.getcwd()))
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("target")
    p.add_argument("--vendor"); p.add_argument("--product"); p.add_argument("--version")
    p.add_argument("--arch"); p.add_argument("--source-url"); p.add_argument("--sha256")

    p = sub.add_parser("hyp"); sp = p.add_subparsers(dest="hyp_cmd", required=True)
    q = sp.add_parser("add"); q.add_argument("target"); q.add_argument("title")
    q.add_argument("--body"); q.add_argument("--source"); q.add_argument("--priority", type=int, default=100)

    p = sub.add_parser("surface"); sp = p.add_subparsers(dest="surf_cmd", required=True)
    q = sp.add_parser("add"); q.add_argument("target"); q.add_argument("key")
    q.add_argument("--disposition", default="unjudged"); q.add_argument("--note")
    q.add_argument("--reachability"); q.add_argument("--input-control")

    p = sub.add_parser("attempt"); sp = p.add_subparsers(dest="att_cmd", required=True)
    q = sp.add_parser("start"); q.add_argument("hypothesis"); q.add_argument("method")
    q = sp.add_parser("finish"); q.add_argument("attempt"); q.add_argument("result"); q.add_argument("--note", default="")

    p = sub.add_parser("evidence"); sp = p.add_subparsers(dest="ev_cmd", required=True)
    q = sp.add_parser("add"); q.add_argument("target"); q.add_argument("--kind", required=True)
    q.add_argument("--path"); q.add_argument("--capture"); q.add_argument("--attempt")
    q.add_argument("--hypothesis"); q.add_argument("--version")
    q.add_argument("--source", default=None, help="证据来源(工具/会话/手工)")
    q.add_argument("--tool-call", default=None, dest="tool_call",
                   help="来自某次工具调用:该证据会被标为未复核(verified=0)")
    q.add_argument("--notes", default=None)
    pr = sp.add_parser("promote")
    pr.add_argument("evidence_id"); pr.add_argument("--note", required=True,
                                                    help="复核说明:怎么确认它成立(复现/交叉验证)")
    vf = sp.add_parser("verify", help="留下可检查的复核记录(机器可核的一面)然后标记 verified")
    vf.add_argument("evidence_id")
    vf.add_argument("--method", required=True,
                    choices=["recheck-sha256", "reproduce-attempt", "cross-check"])
    vf.add_argument("--artifact", default=None)
    vf.add_argument("--attempt", default=None, dest="attempt_id")
    vf.add_argument("--detail", default="")
    st = sp.add_parser("status", help="看证据的复核记录(审计)")
    st.add_argument("evidence_id")

    p = sub.add_parser("observe"); p.add_argument("target"); p.add_argument("--text", required=True)
    p.add_argument("--attempt")

    p = sub.add_parser("conclude"); p.add_argument("hypothesis"); p.add_argument("verdict")
    p.add_argument("--scope", required=True)

    p = sub.add_parser("authz", help="研究授权:grant / revoke / show(按目标、可过期、可撤销)")
    sp = p.add_subparsers(dest="authz_cmd", required=True)
    g = sp.add_parser("grant"); g.add_argument("target"); g.add_argument("--roots", nargs="+", required=True)
    g.add_argument("--hours", type=float, default=8.0); g.add_argument("--note", default="")
    g.add_argument("--no-shell", action="store_true"); g.add_argument("--no-high-risk", action="store_true")
    rv = sp.add_parser("revoke"); rv.add_argument("target"); rv.add_argument("--reason", default="")
    sh = sp.add_parser("show"); sh.add_argument("target")
    p = sub.add_parser("ida-env", help="IDA/Windows 接入配置(单一来源;可导出给 shell)")
    p.add_argument("--sh", action="store_true", help="输出 export 语句(供 tools/ida_*.sh eval)")
    p = sub.add_parser("watchdog", help="独立看护进程(主进程崩了也能兜住)")
    p.add_argument("--once", action="store_true", help="只扫一轮后退出(测试/定时任务用)")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--heartbeat-timeout", type=float, default=900.0)
    p = sub.add_parser("recover", help="收敛确实失联的 run(活跃/长工具不误伤)")
    p.add_argument("--older-than", type=float, default=300.0)
    p.add_argument("--include-remote", action="store_true")
    p.add_argument("--remote-factor", type=float, default=6.0)
    p = sub.add_parser("progress", help="结构化进度快照(只读,给外部监控/Pi 适配器)")
    p.add_argument("run_id")
    p = sub.add_parser("mcp", help="独立 MCP:list / probe / call(不依赖 Pi)")
    p.add_argument("action", choices=["list", "probe", "call"])
    p.add_argument("--server")
    p.add_argument("--tool")
    p.add_argument("--args", default="{}")
    p = sub.add_parser("pi-event", help="从 stdin 读 Pi 研究事件(一行一个 JSON)并写入权威状态")
    p = sub.add_parser("gates", help="权威状态版 Gate(覆盖度/收尾/KB/引用;可选 --legacy 对照)")
    p.add_argument("target")
    p.add_argument("--legacy", action="store_true")
    p = sub.add_parser("capabilities", help="能力/Provider 探针清单(不重装,只发现)")
    p = sub.add_parser("status"); p.add_argument("target")
    p = sub.add_parser("derive"); p.add_argument("target")
    p = sub.add_parser("migrate"); p.add_argument("target"); p.add_argument("--notes")
    p = sub.add_parser("view"); p.add_argument("target")
    def _wd_args(parser):
        """三条运行类子命令共用:看护默认开(正式路径必须有外层保护)。"""
        parser.add_argument("--no-watchdog", dest="watchdog", action="store_false",
                            help="关闭外层看护(仅调试用)")
        parser.add_argument("--watchdog-poll", type=float, default=5.0)
        parser.set_defaults(watchdog=True)
        # MCP:正式路径默认接入(不可达服务器会如实记录,不影响本地工具)
        parser.add_argument("--no-mcp", dest="mcp", action="store_false",
                            help="本次不接入 MCP 服务器")
        parser.add_argument("--mcp-call-timeout", type=float, default=120.0)
        parser.set_defaults(mcp=True)
        parser.add_argument("--script", default=None,
                            help="scripted 模型的脚本 JSON 文件(测试/复现用)")

    p = sub.add_parser("run"); p.add_argument("target"); p.add_argument("--goal")
    p.add_argument("--model", default="scripted"); p.add_argument("--steps", type=int, default=0,
                   help="0 = 不限(默认;由模型自己决定何时收)")
    _wd_args(p)
    p = sub.add_parser("resume"); p.add_argument("run_id")
    p.add_argument("--model", default="scripted"); p.add_argument("--goal")
    p.add_argument("--steps", type=int, default=0, help="0 = 不限")
    _wd_args(p)
    p = sub.add_parser("advance"); p.add_argument("target"); p.add_argument("--goal")
    p.add_argument("--model", default="scripted"); p.add_argument("--rounds", type=int, default=None)
    p.add_argument("--steps", type=int, default=0, help="0 = 不限")
    _wd_args(p)

    args = ap.parse_args(argv)
    con = _con(args)
    dom = Domain(con, args.root)
    try:
        if args.cmd == "init":
            tid = dom.create_target(args.target, vendor=args.vendor, product=args.product,
                                    version=args.version, arch=args.arch,
                                    source_url=args.source_url, source_sha256=args.sha256)
            print(f"target {args.target} ({tid})")
        elif args.cmd == "hyp":
            print(dom.add_hypothesis(args.target, args.title, body=args.body,
                                     source=args.source, priority=args.priority))
        elif args.cmd == "surface":
            print(dom.add_surface(args.target, args.key, disposition=args.disposition,
                                  note=args.note, reachability=args.reachability,
                                  input_control=args.input_control))
        elif args.cmd == "attempt":
            if args.att_cmd == "start":
                print(dom.start_attempt(args.hypothesis, args.method))
            else:
                dom.finish_attempt(args.attempt, args.result, args.note)
                print(f"attempt {args.attempt} → {args.result}")
        elif args.cmd == "evidence":
            if args.ev_cmd == "promote":
                dom.promote_evidence(args.evidence_id, args.note)
                print(json.dumps({"id": args.evidence_id, "verified": True}, ensure_ascii=False))
            elif args.ev_cmd == "verify":
                cid = dom.verify_evidence(args.evidence_id, args.method, artifact=args.artifact,
                                          attempt_id=args.attempt_id, detail=args.detail)
                print(json.dumps({"id": args.evidence_id, "check": cid, "method": args.method},
                                 ensure_ascii=False))
            elif args.ev_cmd == "status":
                print(json.dumps(dom.evidence_status(args.evidence_id), ensure_ascii=False, indent=2))
            else:
                print(dom.add_evidence(args.target, args.kind, path=args.path, capture=args.capture,
                                       attempt_id=args.attempt, hypothesis_id=args.hypothesis,
                                       version=args.version, source=args.source,
                                       tool_call_id=args.tool_call, notes=args.notes))
        elif args.cmd == "observe":
            print(dom.add_observation(args.target, args.text, attempt_id=args.attempt))
        elif args.cmd == "conclude":
            print(dom.conclude(args.hypothesis, args.verdict, args.scope))
        elif args.cmd == "authz":
            from . import authz as _authz
            if args.authz_cmd == "grant":
                d = _authz.grant(args.root, args.target, roots=list(args.roots), hours=args.hours,
                                 note=args.note, allow_shell=not args.no_shell,
                                 allow_high_risk=not args.no_high_risk)
                _authz.audit(con, "authz.granted", args.target, {"roots": d["roots"], "hours": args.hours})
                print(json.dumps(d, ensure_ascii=False, indent=2))
            elif args.authz_cmd == "revoke":
                d = _authz.revoke(args.root, args.target, args.reason)
                _authz.audit(con, "authz.revoked", args.target, {"reason": args.reason})
                print(json.dumps(d, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(_authz.describe(args.root, args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "ida-env":
            from .winenv import load as _ida_load

            cfg = _ida_load(args.root)
            if args.sh:
                print(cfg.shell_exports())
            else:
                print(json.dumps(cfg.describe(), ensure_ascii=False, indent=2))
        elif args.cmd == "watchdog":
            import signal as _signal
            import threading as _th

            from .limits import Limits
            from .watchdog import Watchdog

            wd = Watchdog(con=None, root=args.root, limits=Limits(),
                          db_path=con.execute("PRAGMA database_list").fetchone()[2],
                          heartbeat_timeout_s=args.heartbeat_timeout, poll_s=args.poll)
            if args.once:
                acts = wd.scan_once()
                print(json.dumps({"actions": acts, "health": wd.health()}, ensure_ascii=False))
            else:
                stop = _th.Event()
                for sig in (_signal.SIGTERM, _signal.SIGINT):
                    with contextlib.suppress(Exception):
                        _signal.signal(sig, lambda *_: stop.set())
                th = wd.start_daemon(stop)
                th.join()
                print(json.dumps({"actions": [], "health": wd.health()}, ensure_ascii=False))
        elif args.cmd == "recover":
            from .models import ScriptedAdapter
            rec = Runner(con, args.root, ScriptedAdapter(script=[]))
            fixed = rec.recover_stale(older_than_s=args.older_than,
                                      include_remote=args.include_remote,
                                      remote_factor=args.remote_factor)
            print(json.dumps({"recovered": fixed}, ensure_ascii=False))
        elif args.cmd == "progress":
            from .models import ScriptedAdapter

            r = Runner(con, args.root, ScriptedAdapter(script=[]))
            print(json.dumps(r.progress(args.run_id), ensure_ascii=False, indent=2))
        elif args.cmd == "mcp":
            from .mcp_client import MCPClient, load_servers, probe as mcp_probe
            cfgs = [os.environ.get("FW_MCP_CONFIG") or
                    os.path.join(args.root, "state", "mcp.json")]
            servers = load_servers(cfgs)
            if args.action == "list":
                print(json.dumps({k: {"transport": v.transport, "enabled": v.enabled}
                                  for k, v in servers.items()}, ensure_ascii=False, indent=2))
            elif args.action == "probe":
                names = [args.server] if args.server else list(servers)
                print(json.dumps([mcp_probe(servers[n]) for n in names if n in servers],
                                 ensure_ascii=False, indent=2))
            else:
                if not args.server or not args.tool:
                    print("需要 --server 与 --tool", file=sys.stderr)
                    return 2
                with MCPClient(servers[args.server]) as c:
                    c.initialize()
                    c.list_tools()
                    print(json.dumps(c.call_tool(args.tool, json.loads(args.args)),
                                     ensure_ascii=False, indent=2))
        elif args.cmd == "pi-event":
            import sys as _sys

            from .pi_bridge import handle_stream
            res = handle_stream(con, args.root, _sys.stdin.read())
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if all(r.get("status") in ("ok", "duplicate") for r in res) else 1
        elif args.cmd == "gates":
            from .gates_db import dangling_attachment_refs, evaluate, kb_counts, legacy_compare
            rep = evaluate(con, args.root, args.target).as_dict()
            rep["kb"] = kb_counts(args.root)
            rep["dangling_refs"] = dangling_attachment_refs(con, args.root, args.target)
            if args.legacy:
                rep["legacy"] = legacy_compare(con, args.root, args.target)
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            ok = rep["ok"] and all(x["status"] == "ok" for x in rep["kb"]) and not rep["dangling_refs"]
            return 0 if ok else 1
        elif args.cmd == "capabilities":
            from .providers import inventory
            print(json.dumps(inventory(args.root), ensure_ascii=False, indent=2))
        elif args.cmd == "status":
            print(json.dumps(dom.status(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "derive":
            print(json.dumps(dom.derived(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "migrate":
            print(json.dumps(import_notes(con, args.root, args.target, args.notes), ensure_ascii=False, indent=2))
            print(json.dumps(verify(con, args.root, args.target, args.notes), ensure_ascii=False, indent=2))
        elif args.cmd == "view":
            print(render_view(con, args.root, args.target))
        elif args.cmd in ("run", "resume", "advance"):
            model = _model(args.model, getattr(args, "script", None))
            runner = Runner(con, args.root, model, max_steps=(args.steps or None),
                            watchdog=getattr(args, "watchdog", False),
                            watchdog_poll_s=getattr(args, "watchdog_poll", 5.0),
                            mcp=getattr(args, "mcp", False),
                            mcp_call_timeout=getattr(args, "mcp_call_timeout", 120.0))
            if args.cmd == "advance":
                res = runner.advance(args.target, args.goal, max_rounds=args.rounds)
            elif args.cmd == "run":
                rid = runner.start(args.target, args.goal)
                res = runner.run(rid, args.target, goal=args.goal)
            else:
                res = runner.resume(args.run_id)
            print(json.dumps({k: v for k, v in res.items() if k != "history"}, ensure_ascii=False, indent=2))
        return 0
    except DomainError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


def _model(name: str, script_path: str | None = None):
    if name in ("openai", "deepseek", "openai-compat"):
        try:
            key, base, model = load_model_config(args.root)
        except Exception:
            key, base, model = os.environ.get("FW_API_KEY", ""), os.environ.get("FW_BASE_URL", ""), "deepseek-chat"
        return build_adapter("openai", api_key=key, base_url=base, model=model)
    # scripted:默认脚本 = 登记一条假设后收束(便于本地冒烟,不产生外部调用);
    # 给了 --script 就用文件里的脚本(测试/复现"模型决定了什么"时唯一确定的手段)
    if script_path:
        import json as _json

        try:
            with open(script_path, encoding="utf-8") as f:
                script = _json.load(f)
        except (OSError, _json.JSONDecodeError) as e:
            raise SystemExit(f"读脚本失败 {script_path}: {e}") from e
        if not isinstance(script, list):
            raise SystemExit(f"脚本必须是 JSON 数组: {script_path}")
        return build_adapter("scripted", script=script)
    return build_adapter("scripted", script=[
        {"content": "先登记一个待验证的方向。", "tool_calls": [
            {"name": "state_add_hypothesis",
             "args": {"target": os.environ.get("FW_TARGET", ""), "title": "(scripted) 冒烟假设"}}]},
        {"content": "FINAL: 冒烟完成", "tool_calls": []},
    ])


def render_view(con, root: str, target: str) -> str:
    """从权威状态生成人读 Markdown(notes 变成视图,而不是数据库)。"""
    dom = Domain(con, root)
    d = dom.derived(target)
    t = dom.target(target)
    lines = [f"# {t.name} — 状态视图(由 fw-core 生成,勿手改)", ""]
    lines.append(f"- 版本: {d['version'] or '未知'}")
    lines.append(f"- 面覆盖: {d['surface_coverage']}  (未判定 {d['surfaces'].get('unjudged', 0)})")
    lines.append(f"- 假设: " + ", ".join(f"{k}={v}" for k, v in d["hypotheses"].items() if v))
    lines.append(f"- 尝试: " + ", ".join(f"{k}={v}" for k, v in d["attempts"].items() if v))
    lines.append(f"- 证据: {d['evidence']} 条")
    if d["closure_blockers"]:
        lines += ["", "## 收尾阻塞(程序可核)"] + [f"- {b}" for b in d["closure_blockers"]]
    rows = con.execute(
        "SELECT id,title,state,verdict,scope_note FROM hypotheses WHERE target_id=? ORDER BY created_at",
        (t.id,)).fetchall()
    if rows:
        lines += ["", "## 假设", "", "| id | 标题 | 状态 | 判定 | 适用范围 |", "|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['id']} | {r['title'][:60]} | {r['state']} | {r['verdict'] or ''} | {(r['scope_note'] or '')[:40]} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
