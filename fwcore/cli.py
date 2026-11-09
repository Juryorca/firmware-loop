"""独立入口:`fw-core <cmd>`(宿主无关,不依赖任何外部 agent 运行时)。

命令:
  init <target> [--version V] [--vendor X] [--sha256 H]   建目标
  hyp add <target> <title> [--body B] [--source S] [--priority N]
  surface add <target> <key> [--disposition D] [--note N]
  attempt start <hyp-id> <method> / attempt finish <attempt-id> <result> [--note N]
  evidence add <target> --kind K (--path P | --capture C) [--attempt A] [--hypothesis H]
  observe <target> --text T [--attempt A]
  conclude <hyp-id> <verdict> --scope "适用范围"
  status <target>        UNKNOWN(无此目标) / EMPTY(有目标无假设) / active + target_status
  archive <target>       标记为已归档（只改生命周期状态，不删研究事实/文件）
  unarchive <target>     恢复为 active（可继续研究）
  derive <target>       派生量 + 收尾阻塞项
  migrate <target> [--notes PATH]
  view <target>         从状态生成人读 Markdown(替代手写 notes)
  run <target> [--goal G] [--model scripted|openai] [--steps N]
  resume <run-id> [--steps N]
"""

# ruff: noqa: E702 (CLI parser table uses compact semicolons by design)
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

from . import db as _db
from .domain import Domain, DomainError
from .models import build_adapter, load_model_config, load_model_options
from .runtime import Runner


def _con(args):
    path = args.db or _db.default_db_path(args.root)
    return _db.connect(path)


_SUBCOMMANDS = frozenset({
    "init", "hyp", "surface", "attempt", "evidence", "observe", "conclude",
    "authz", "ida-env", "watchdog", "recover", "progress", "context", "mcp",
    "gates", "capabilities", "toolset", "knowledge", "status", "derive", "migrate", "view",
    "archive", "unarchive", "run", "resume", "advance",
})


def _app_invocation(argv: list[str]) -> bool:
    """顶层无研究子命令 ⇒ 交给应用层(TUI / print)。

    先跳过全局 `--root/--db`(可多个、可任意顺序),再看第一个有效位置参数:
    是研究子命令 ⇒ 原 CLI;否则 ⇒ 应用层。非 TTY 的降级由 fwapp.main 决定
    (绝不意外进全屏)。
    """
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--root", "--db"):
            i += 2
            continue
        if a in ("-h", "--help"):
            return False          # 顶层帮助仍由原 CLI 提供(含全部研究子命令)
        return a not in _SUBCOMMANDS
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _app_invocation(argv):
        from fwapp.cli import main as app_main
        return app_main(argv)
    ap = argparse.ArgumentParser(
        prog="fw-core", description="固件研究 agent 核心(宿主无关)",
        epilog="交互模式: fw-core(TTY 自动)/ fw-core tui;非交互: fw-core -p \"...\"")
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
    p = sub.add_parser("progress", help="结构化进度快照(只读,给外部监控)")
    p.add_argument("run_id")
    p = sub.add_parser("context", help="上下文状态:模型窗口/估算/阈值/最近压缩(只读)")
    p.add_argument("target")
    p.add_argument("--run", dest="run_id", default=None,
                   help="只看某个 run(默认:该目标全部 run;不同目标互不串)")
    p.add_argument("--context-tokens", type=int, default=None,
                   help="模型上下文窗口(token);不给则读 limits 默认(0=未配置)")
    p = sub.add_parser("mcp", help="独立 MCP:list / probe / call")
    p.add_argument("action", choices=["list", "probe", "call"])
    p.add_argument("--server")
    p.add_argument("--tool")
    p.add_argument("--args", default="{}")
    p = sub.add_parser("gates", help="权威状态版 Gate(覆盖度/收尾/KB/引用;只读 SQLite)")
    p.add_argument("target")
    p = sub.add_parser("capabilities", help="能力/Provider 探针清单(不重装,只发现)")
    p = sub.add_parser("toolset", help="默认工具集完整性(仓库资产/外部依赖/能力;只读探针)")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p = sub.add_parser("knowledge", help="Research Knowledge(检索/版本/审核/经验)")
    sp = p.add_subparsers(dest="knowledge_cmd", required=True)
    q = sp.add_parser("search")
    q.add_argument("query")
    q.add_argument("--kind", choices=["pattern", "misreport", "guideline"])
    q.add_argument("--status", choices=["approved", "deprecated", "superseded"])
    q.add_argument("--limit", type=int, default=20)
    q = sp.add_parser("get")
    q.add_argument("entry_id")
    q = sp.add_parser("revise")
    q.add_argument("entry_id")
    q.add_argument("--title", default=None)
    q.add_argument("--body-file", default=None)
    q.add_argument("--note", default="")
    q = sp.add_parser("stats")
    q = sp.add_parser("deprecate")
    q.add_argument("entry_id")
    p = sub.add_parser("status"); p.add_argument("target")
    p = sub.add_parser("archive"); p.add_argument("target"); p.add_argument("--note", default="")
    p = sub.add_parser("unarchive"); p.add_argument("target")
    p = sub.add_parser("derive"); p.add_argument("target")
    p = sub.add_parser("migrate", help="历史 Markdown → DB(一次性导入;--all 批量/--dry-run 预览)")
    p.add_argument("target", nargs="?", default=None)
    p.add_argument("--all", dest="migrate_all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--reset", action="store_true", help="先清掉上次迁移写入的行(重跑用)")
    p.add_argument("--notes")
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
        elif args.cmd == "context":
            from .compact import threshold_tokens
            from .contextinfo import build_status
            from .limits import Limits
            from .models import limits_config, load_model_config

            dom = Domain(con, args.root)
            t = dom.target(args.target)                   # 目标不存在就直接报错
            cfg = limits_config(args.root)
            lim = Limits(**cfg)
            if args.context_tokens:
                lim = Limits(**{**cfg, "context_tokens": args.context_tokens})
            # 目标/run 隔离:只读本目标的 run;否则别的目标的 checkpoint/事件会串进来
            run_ids = [r["id"] for r in con.execute(
                "SELECT id FROM runs WHERE target_id=? ORDER BY rowid", (t.id,))]
            if args.run_id:
                if args.run_id not in run_ids:
                    print(f"run {args.run_id} 不属于目标 {args.target}", file=sys.stderr)
                    return 2
                run_ids = [args.run_id]
            # 固定 SQL + 绑定参数:run 过滤走 JOIN，不拼接 IN 占位符。
            if args.run_id:
                ck = con.execute(
                    "SELECT c.messages FROM checkpoints c JOIN runs r ON r.id=c.run_id"
                    " WHERE r.target_id=? AND c.run_id=?"
                    " ORDER BY c.created_at DESC, c.rowid DESC LIMIT 1",
                    (t.id, args.run_id)).fetchone()
                last = con.execute(
                    "SELECT payload FROM events WHERE kind='model.call'"
                    " AND ref=? ORDER BY ts DESC, id DESC LIMIT 1",
                    (args.run_id,)).fetchone()
            elif run_ids:
                ck = con.execute(
                    "SELECT c.messages FROM checkpoints c JOIN runs r ON r.id=c.run_id"
                    " WHERE r.target_id=? ORDER BY c.created_at DESC, c.rowid DESC LIMIT 1",
                    (t.id,)).fetchone()
                last = con.execute(
                    "SELECT payload FROM events WHERE kind='model.call'"
                    " AND ref IN (SELECT id FROM runs WHERE target_id=?)"
                    " ORDER BY ts DESC, id DESC LIMIT 1",
                    (t.id,)).fetchone()
            else:
                ck = last = None
            hist = json.loads(ck["messages"]) if ck else []
            lu = (json.loads(last["payload"]).get("usage") or {}) if last else {}
            _k, _b, _m = load_model_config(args.root)
            from .research import Research
            from .toolkit import register_repo_tools
            from .tools import default_registry

            reg = default_registry()
            register_repo_tools(reg, args.root)
            schema = json.dumps(reg.describe(), ensure_ascii=False)
            _research = Research(args.root, dom)
            _sys = _research.rules_prompt(args.target)
            _st = json.dumps(_research.state_prompt(args.target), ensure_ascii=False)
            st = build_status(
                model=_m or str(os.environ.get("FW_MODEL", "") or "unknown"),
                window=lim.context_tokens or None, messages=hist,
                output_reserve=lim.output_reserve_tokens,
                context_tokens=lim.context_tokens,
                last_prompt_tokens=lu.get("prompt_tokens"),
                last_cached_tokens=(lu.get("prompt_tokens_details") or {}).get("cached_tokens"),
                system=_sys, state=_st, schema=schema)
            if args.run_id:
                comp = con.execute(
                    "SELECT payload FROM events WHERE kind IN"
                    " ('run.compacted','run.compact_failed') AND ref=?"
                    " ORDER BY ts DESC, id DESC LIMIT 1",
                    (args.run_id,)).fetchone()
            elif run_ids:
                comp = con.execute(
                    "SELECT payload FROM events WHERE kind IN"
                    " ('run.compacted','run.compact_failed')"
                    " AND ref IN (SELECT id FROM runs WHERE target_id=?)"
                    " ORDER BY ts DESC, id DESC LIMIT 1",
                    (t.id,)).fetchone()
            else:
                comp = None
            out = st.as_dict()
            out["checkpoint_messages"] = len(hist)
            out["run_scope"] = args.run_id or f"{len(run_ids)} run(s)"
            out["threshold_tokens"] = threshold_tokens(lim.context_tokens, lim.output_reserve_tokens)
            out["last_compaction"] = json.loads(comp["payload"]) if comp else None
            out["last_compaction_kind"] = comp is not None and json.loads(comp["payload"]).get("error") is None
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif args.cmd == "mcp":
            from .mcp_client import MCPClient
            from .mcp_client import probe as mcp_probe
            from .mcp_config import discover_mcp_config, load_mcp_config

            sources = discover_mcp_config(args.root)
            cfg = load_mcp_config(sources, base_dir=args.root)
            servers = cfg.servers
            if cfg.errors:
                print("MCP_CONFIG_WARN: " + "\n".join(cfg.errors), file=sys.stderr)
            if args.action == "list":
                print(json.dumps({k: {"transport": v.transport, "enabled": v.enabled,
                                      "source": v.source, "scope": v.scope}
                                  for k, v in servers.items()}, ensure_ascii=False, indent=2))
            elif args.action == "probe":
                names = [args.server] if args.server else list(servers)
                missing = [n for n in names if n not in servers]
                if missing:
                    print("没有配置的 MCP 服务器: " + ", ".join(missing), file=sys.stderr)
                    return 2
                print(json.dumps([mcp_probe(servers[n]) for n in names],
                                 ensure_ascii=False, indent=2))
            else:
                if not args.server or not args.tool:
                    print("需要 --server 与 --tool", file=sys.stderr)
                    return 2
                spec = servers.get(args.server)
                if spec is None:
                    print(f"没有配置的 MCP 服务器: {args.server}", file=sys.stderr)
                    return 2
                if not spec.enabled:
                    print(f"MCP 服务器 {args.server} 已禁用", file=sys.stderr)
                    return 2
                with MCPClient(spec) as c:
                    c.initialize()
                    c.list_tools()
                    print(json.dumps(c.call_tool(args.tool, json.loads(args.args)),
                                     ensure_ascii=False, indent=2))
        elif args.cmd == "gates":
            from .gates_db import dangling_attachment_refs, evaluate
            rep = evaluate(con, args.root, args.target).as_dict()
            rep["dangling_refs"] = dangling_attachment_refs(con, args.root, args.target)
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            ok = rep["ok"] and not rep["dangling_refs"]
            return 0 if ok else 1
        elif args.cmd == "knowledge":
            from .knowledge import get as _kb_get
            from .knowledge import revise as _kb_revise
            from .knowledge import search as _kb_search
            from .knowledge import set_status as _kb_set_status
            from .knowledge import stats as _kb_stats
            if args.knowledge_cmd == "search":
                print(json.dumps(_kb_search(con, args.query, kind=args.kind,
                                            status=args.status, limit=args.limit),
                                 ensure_ascii=False, indent=2))
            elif args.knowledge_cmd == "get":
                print(json.dumps(_kb_get(con, args.entry_id), ensure_ascii=False, indent=2))
            elif args.knowledge_cmd == "stats":
                print(json.dumps(_kb_stats(con), ensure_ascii=False, indent=2))
            elif args.knowledge_cmd == "deprecate":
                print(json.dumps(_kb_set_status(con, args.entry_id, "deprecated"),
                                 ensure_ascii=False, indent=2))
            elif args.knowledge_cmd == "revise":
                body = None
                if args.body_file:
                    with open(args.body_file, encoding="utf-8") as f:
                        body = f.read()
                print(json.dumps(_kb_revise(con, args.entry_id, title=args.title,
                                            body_md=body, change_note=args.note),
                                 ensure_ascii=False, indent=2))
        elif args.cmd == "toolset":
            from .toolset import check_toolset, render_report
            rep = check_toolset(args.root)
            print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json
                  else render_report(rep))
            return 0 if rep["integrity"] == "ready" else 1
        elif args.cmd == "capabilities":
            from .providers import inventory
            from .toolmeta import ToolConfigError
            try:
                data = inventory(args.root)
            except ToolConfigError as e:
                print(f"TOOL_CONFIG_ERROR: {e}", file=sys.stderr)
                return 2
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.cmd == "status":
            print(json.dumps(dom.status(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "archive":
            dom.archive_target(args.target, args.note)
            print(json.dumps(dom.status(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "unarchive":
            dom.unarchive_target(args.target)
            print(json.dumps(dom.status(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "derive":
            print(json.dumps(dom.derived(args.target), ensure_ascii=False, indent=2))
        elif args.cmd == "migrate":
            from .migrate_all import migrate_all, reset_migrated, verify_target

            if args.migrate_all:
                if args.reset and not args.dry_run:
                    reset_migrated(con, args.root)
                out = migrate_all(args.root, dry_run=args.dry_run)
                if args.verify and not args.dry_run:
                    out["verify"] = [verify_target(con, args.root, r["target"])
                                     for r in out["results"]]
                print(json.dumps(out, ensure_ascii=False, indent=2))
                ok = all(v["ok"] for v in out.get("verify", []))
                return 0 if (args.dry_run or not out.get("verify") or ok) else 1
            if not args.target:
                print("migrate 需要 <target> 或 --all", file=sys.stderr)
                return 2
            from .migrate_all import migrate_all as _migrate_all
            out = _migrate_all(args.root, dry_run=args.dry_run, only=[args.target])
            if args.verify and not args.dry_run:
                out["verify"] = [verify_target(con, args.root, args.target)]
            print(json.dumps(out, ensure_ascii=False, indent=2))
            ok = all(v["ok"] for v in out.get("verify", []))
            return 0 if (args.dry_run or not out.get("verify") or ok) else 1
        elif args.cmd == "view":
            print(render_view(con, args.root, args.target))
        elif args.cmd in ("run", "resume", "advance"):
            from .limits import Limits
            from .models import limits_config

            model = _model(args.model, args.root, getattr(args, "script", None))
            limits = Limits(**limits_config(args.root))   # CLI 与运行期同一来源
            runner = Runner(con, args.root, model, max_steps=(args.steps or None),
                            limits=limits,
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
    except (DomainError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


def _model(name: str, root: str, script_path: str | None = None):
    """构造模型适配器。配置错误必须**报出来**,不能悄悄换一个模型。"""
    if name in ("openai", "deepseek", "openai-compat"):
        try:
            key, base, model = load_model_config(root)
        except ValueError as e:                 # 配置文件坏 / 读不了
            raise SystemExit(f"模型配置错误: {e}") from e
        if not (key and base and model):
            raise SystemExit(
                "模型配置不完整:设置 FW_MODEL_API_KEY/FW_MODEL_BASE_URL/FW_MODEL_NAME,"
                f"或写 {os.path.join(root, 'state', 'model.json')}"
                "(字段 api_key/base_url/model)")
        try:
            extra = load_model_options(root)
        except ValueError as e:
            raise SystemExit(f"模型多模态配置错误: {e}") from e
        return build_adapter("openai", api_key=key, base_url=base, model=model,
                             api=extra.get("api", ""),
                             supports_vision=extra.get("supports_vision", False),
                             image_mode=extra.get("image_mode", "none"))
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
    lines.append("- 假设: " + ", ".join(f"{k}={v}" for k, v in d["hypotheses"].items() if v))
    lines.append("- 尝试: " + ", ".join(f"{k}={v}" for k, v in d["attempts"].items() if v))
    lines.append(f"- 证据: {d['evidence']} 条")
    if d["closure_blockers"]:
        lines += ["", "## 收尾阻塞(程序可核)"] + [f"- {b}" for b in d["closure_blockers"]]
    logs = con.execute(
        "SELECT kind,title,created_at FROM research_logs WHERE target_id=?"
        " ORDER BY created_at DESC LIMIT 12", (t.id,)).fetchall()
    if logs:
        lines += ["", "## 最近日志(DB)", ""]
        lines += [f"- [{r['kind']}] {r['title'][:100]} ({r['created_at'][:10]})" for r in logs]
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
