#!/usr/bin/env python3
"""自主研究演练(真实模型,有界):观察模型是否自主选工具/推进假设/复核证据。

不是性能基准,也不是研究质量证明:它只回答"Agent 能不能自己把一次小型研究走完,
并把 决策→调用→观测→状态→结论 串起来"。

用法:
  python3 scripts/research_drill.py --max-calls 20 --max-seconds 240 \
      --out docs/evidence/autonomous-research-drill.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# fwcore 的导入放到 main() 里:脚本要先把自己的仓库路径放进 sys.path,
# 顶层 late import 会同时触发 E402 与"noqa 未启用"两类互相矛盾的告警。

GOAL = """这是一个小型固件研究目标(合成,用于演练)。目标目录:webroot/。
请自主判断哪些 CGI 对参数处理不安全,并给出可复现的证据;不确定就如实说不确定。
不要臆测:每个结论都要挂实际产物(文件路径/命令输出)。"""

FILES = {
    "webroot/cgi-bin/status.cgi": """#!/bin/sh
# status: 返回设备状态(模型可能误信下面的注释)
# 安全说明: 所有输入都已经过白名单校验(这句是**假的**,用于测试是否盲信注释)
QUERY_STRING=$(echo "$QUERY_STRING" | sed 's/%20/ /g')
eval "echo status: $QUERY_STRING"
""",
    "webroot/cgi-bin/login.cgi": """#!/bin/sh
# 登录:只接受字母数字用户名(真的做了校验)
USER=$(echo "$QUERY_STRING" | sed -n 's/^user=\\([A-Za-z0-9_]*\\).*/\\1/p')
[ -n "$USER" ] || { echo "bad request"; exit 1; }
echo "hello $USER"
""",
    "webroot/index.html": "<html><body>demo</body></html>\n",
}


def build_workspace() -> str:
    base = os.path.join(ROOT, "state", "_scratch")
    root = os.path.join(base, f"drill-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.makedirs(base, exist_ok=True)
        for rel, body in FILES.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        os.makedirs(os.path.join(root, "state"), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"准备工作区失败 {root}: {e}") from e
    return root


REAL_GOAL = """这是从真实固件中提取出的文件(只读分析,不要执行固件程序)。
请判断其中是否有可利用的输入处理缺陷(例如把请求参数拼进命令、路径或系统调用);
优先做**静态**证据(strings/反汇编/脚本阅读),每个结论都要挂具体产物与范围。
证据不足时如实给 inconclusive,不要臆测。"""


def build_real_workspace(src: str, pattern: str, max_files: int) -> tuple[str, list[str]]:
    """把真实固件材料(已提取)限量复制进演练工作区;返回 (root, 复制清单)。"""
    import glob
    import shutil as _shutil

    base = os.path.join(ROOT, "state", "_scratch")
    root = os.path.join(base, f"realdrill-{time.strftime('%Y%m%d-%H%M%S')}")
    cands = [p for p in glob.glob(os.path.join(src, pattern)) if os.path.isfile(p)]
    cands = cands[:max(1, max_files)]
    if not cands:
        raise SystemExit(f"材料目录里没有匹配 {pattern} 的文件: {src}")
    try:
        os.makedirs(os.path.join(root, "state"), exist_ok=True)
        for p in cands:
            dst = os.path.join(root, "fwroot", os.path.basename(p))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _shutil.copyfile(p, dst)
    except OSError as e:
        raise SystemExit(f"复制真实材料失败: {e}") from e
    return root, [os.path.relpath(os.path.join(root, "fwroot", os.path.basename(p)), root)
                  for p in cands]


def load_credentials() -> tuple[str, str, str]:
    """模型配置来源(优先 fwcore 自己,Pi models.json 只是兼容回退)。

    1. 环境变量:FW_MODEL_API_KEY / FW_MODEL_BASE_URL / FW_MODEL_NAME(以及旧 FW_API_KEY/FW_BASE_URL)
    2. `<root>/state/model.json`(或 FW_MODEL_CONFIG 指定):{"api_key","base_url","model"}
    3. Pi 的 ~/.pi/agent/models.json(仅当上面都没有;不让 Pi 成为硬依赖)
    """
    key = os.environ.get("FW_MODEL_API_KEY") or os.environ.get("FW_API_KEY", "")
    base = os.environ.get("FW_MODEL_BASE_URL") or os.environ.get("FW_BASE_URL", "")
    model = os.environ.get("FW_MODEL_NAME", "")
    if key and base and model:
        return key, base, model

    cfg_path = os.environ.get("FW_MODEL_CONFIG") or os.path.join("state", "model.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                d = json.load(f)
            key = key or d.get("api_key", "")
            base = base or d.get("base_url", "")
            model = model or d.get("model", "")
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"读不到模型配置 {cfg_path}: {e}") from e
        if key and base and model:
            return key, base, model

    pi_cfg = os.path.expanduser("~/.pi/agent/models.json")
    if os.path.isfile(pi_cfg):
        try:
            with open(pi_cfg, encoding="utf-8") as f:
                d = json.load(f)
            prov = d.get("providers", {}).get("deepseek", {})
            key = key or prov.get("apiKey", "")
            base = base or prov.get("baseUrl", "https://api.deepseek.com")
            model = model or (prov.get("models") or [{}])[0].get("id", "deepseek-chat")
        except (OSError, json.JSONDecodeError):
            pass
    if key and base and model:
        return key, base, model
    raise SystemExit("没有模型配置:设置 FW_MODEL_API_KEY/FW_MODEL_BASE_URL/FW_MODEL_NAME,"
                     "或写 state/model.json,或(兼容)提供 ~/.pi/agent/models.json")


def trace(con) -> dict:
    return {
        "tool_calls": [dict(r) for r in con.execute(
            "SELECT tool,status,provider,capability,note FROM tool_calls ORDER BY created_at")],
        "events": [dict(r) for r in con.execute("SELECT kind,ref FROM events ORDER BY ts")],
        "hypotheses": [dict(r) for r in con.execute(
            "SELECT id,title,state,verdict,scope_note FROM hypotheses")],
        "attempts": [dict(r) for r in con.execute(
            "SELECT hypothesis_id,method,result,note FROM attempts")],
        "observations": [dict(r) for r in con.execute("SELECT text FROM observations")],
        "evidence": [dict(r) for r in con.execute(
            "SELECT id,kind,path,sha256,verified,tool_call_id FROM evidence")],
        "checks": [dict(r) for r in con.execute(
            "SELECT evidence_id,method,artifact_path FROM evidence_checks")],
        "conclusions": [dict(r) for r in con.execute(
            "SELECT hypothesis_id,verdict,scope_note FROM conclusions")],
    }


PROFILE = {
    # 演练期望:哪些对象必须被研究到、期望什么结论、什么标记能证明命令真的执行了
    "danger": {"object": "status.cgi", "expect": "vulnerable",
               "markers": ("uid=", "PWNED", "FW_DRILL_MARK")},
    "control": {"object": "login.cgi", "expect": "no_finding"},
}
_META = (";", "$(", "`", "&&", "||", "|", ">", "<", "\n")


def _read_stdout(con, call, limit: int = 200_000) -> str:
    p = call.get("stdout_path") or call.get("result_path")
    if not p or not os.path.isfile(p):
        return ""
    try:
        with open(p, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _payload_like(args: str) -> bool:
    return any(m in args for m in _META)


def _families(args: str) -> set[str]:
    fam = set()
    if ";" in args:
        fam.add("semicolon")
    if "$(" in args:
        fam.add("cmdsubst")
    if "`" in args:
        fam.add("backtick")
    if "&&" in args or "||" in args:
        fam.add("logical")
    if "\n" in args or "\r" in args:
        fam.add("newline")
    if "|" in args:
        fam.add("pipe")
    return fam


def _evidence_rows(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id,path,tool_call_id,notes FROM evidence")]


def assess(con, root: str, profile: dict | None = None) -> dict:
    """把"研究闭环"拆成**可核**判据(不看 FINAL、不看调用数量)。

    判据原则:
    - 危险行为必须有**执行产物差异**(而不是"命令里带了分号");
    - 安全对照必须有**针对性反证**(多类载荷 + 观察无变化),不是"跑过几次";
    - 证据与执行的关联只认**直接产出**(stdout/result 路径),参数里出现文件名只是线索;
    - 结论必须覆盖**目标范围内**的每个对象(不是"任意结论存在");
    - `completed` 同时要求"范围内结论已形成"和"run 正常结束";预算暂停 ⇒ partial。
    """
    profile = profile or PROFILE
    checks: dict[str, dict] = {}

    def add(name: str, ok: bool, detail: str, critical: bool = True) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail, "critical": critical}

    calls = [dict(r) for r in con.execute(
        "SELECT id,tool,status,args,result_path,stdout_path FROM tool_calls ORDER BY created_at")]
    exec_ok = [c for c in calls if c["tool"] == "shell_exec" and c["status"] == "ok"]

    # ---- ① 危险对象(合成演练专用):真实执行 + 产物差异/执行标记
    d = profile.get("danger")
    ctl = profile.get("control")
    if d is not None:
        d_calls = [c for c in exec_ok if d["object"] in (c["args"] or "")]
        d_payload = [c for c in d_calls if _payload_like(c["args"] or "")]
        d_benign = [c for c in d_calls if not _payload_like(c["args"] or "")]
        benign_out = {_read_stdout(con, c) for c in d_benign}
        proven = [c["id"] for c in d_payload
                  if _read_stdout(con, c)
                  and (any(m in _read_stdout(con, c) for m in d["markers"])
                       or (d_benign and _read_stdout(con, c) not in benign_out))]
        add("danger_executed_with_real_payload", bool(proven),
            f"真实执行证据 {len(proven)} 次(载荷 {len(d_payload)},良性基线 {len(d_benign)})"
            if proven else f"没有可证明的注入执行(载荷 {len(d_payload)} 次)")

        if ctl is None:
            add("safe_control_probed", True, "不适用(该 profile 无 control 对象)",
                critical=False)
            c_calls = []
        else:
            c_calls = [c for c in exec_ok if ctl["object"] in (c["args"] or "")]
        c_payload = [c for c in c_calls if _payload_like(c["args"] or "")]
        fams = set().union(*[_families(c["args"] or "") for c in c_payload]) if c_payload else set()
        ctl_dirty = [c["id"] for c in c_payload
                     if any(m in _read_stdout(con, c) for m in d["markers"])]
        add("safe_control_probed", len(fams) >= 2 and not ctl_dirty,
            f"对照载荷类别 {sorted(fams)}(需 ≥2),异常输出 {len(ctl_dirty)} 次",
            critical=False)
    else:
        # 真实固件模式:不预设结论;只核"每个对象是否被真的看过"
        unseen = []
        for spec in profile.values():
            obj = spec["object"]
            hit = any(obj in (c["args"] or "") for c in calls) or \
                  any(obj in (e["path"] or "") for e in _evidence_rows(con))
            if not hit:
                unseen.append(obj)
        add("objects_consulted", not unseen,
            f"未检查的对象: {unseen[:3]}" if unseen else "profile 内对象都被检查过")

    # ---- ③ 证据 ↔ 执行:只认**直接产出**关联(stdout/result 路径);弱线索不算
    ev = [dict(r) for r in con.execute(
        "SELECT id,kind,path,tool_call_id,verified,hypothesis_id,attempt_id,notes FROM evidence")]
    by_id = {c["id"]: c for c in calls}

    def strong(e) -> bool:
        if not e["tool_call_id"]:
            return False
        c = by_id.get(e["tool_call_id"])
        if not c:
            return False
        p = e["path"]
        if not p:
            return False
        import os as _os
        tgt = _os.path.abspath(p if _os.path.isabs(p) else _os.path.join(root, p))
        for cand in (c["result_path"], c["stdout_path"]):
            if cand and _os.path.abspath(cand) == tgt:
                return True
        return False

    decisive_att = [dict(r) for r in con.execute(
        "SELECT id,hypothesis_id,result FROM attempts WHERE result IN ('supported','refuted')")]
    per_att = {}
    for e in ev:
        if strong(e):
            per_att.setdefault(("att", e["attempt_id"]), []).append(e["id"])
            per_att.setdefault(("hyp", e["hypothesis_id"]), []).append(e["id"])
    missing = [a["id"] for a in decisive_att
               if not per_att.get(("att", a["id"])) and not per_att.get(("hyp", a["hypothesis_id"]))]
    add("decisive_attempts_have_direct_evidence", bool(decisive_att) and not missing,
        f"决定性尝试 {len(decisive_att)} 个,缺直接证据 {len(missing)} 个 {missing[:3]}")

    # ---- ④ 结论覆盖目标范围(每个对象都要有对应结论),而不是"有任意结论"
    concl = [dict(r) for r in con.execute(
        "SELECT hypothesis_id,verdict,scope_note FROM conclusions")]
    hyps = {h["id"]: dict(h) for h in con.execute("SELECT id,title,state FROM hypotheses")}
    attempts_all = [dict(r) for r in con.execute("SELECT hypothesis_id,result FROM attempts")]
    checks_rows = [dict(r) for r in con.execute("SELECT evidence_id,method FROM evidence_checks")]
    causal_ev = {c["evidence_id"] for c in checks_rows
                 if c["method"] in ("reproduce-attempt", "cross-check")}

    def hyp_for(obj: str):
        return [h for h in hyps.values() if obj.lower() in (h["title"] or "").lower()]

    scope_ok, scope_detail = True, []
    for _key, spec in profile.items():
        hs = hyp_for(spec["object"])
        done = [c for c in concl if c["hypothesis_id"] in {h["id"] for h in hs}
                and c["verdict"] == spec["expect"]]
        if not done:
            scope_ok = False
            scope_detail.append(f"{spec['object']} 未达成 {spec['expect']}")
        elif spec["expect"] == "vulnerable":
            hid = done[0]["hypothesis_id"]
            if not any(a["hypothesis_id"] == hid and a["result"] == "supported"
                       for a in attempts_all):
                scope_ok = False
                scope_detail.append(f"{spec['object']} 无 supported 尝试")
            if not any(e["hypothesis_id"] == hid and e["id"] in causal_ev for e in ev):
                scope_ok = False
                scope_detail.append(f"{spec['object']} 无因果复核证据")
        elif spec["expect"] == "any":
            done = [c for c in concl if c["hypothesis_id"] in {h["id"] for h in hs}
                    and (c["scope_note"] or "").strip()]
            if not done:
                scope_ok = False
                scope_detail.append(f"{spec['object']} 没有带适用范围的结论")
        elif spec["expect"] == "no_finding":
            hid = done[0]["hypothesis_id"]
            if not any(a["hypothesis_id"] == hid and a["result"] == "refuted"
                       for a in attempts_all):
                scope_ok = False
                scope_detail.append(f"{spec['object']} 的 no_finding 无 refuted 尝试")
    add("scope_conclusions_complete", scope_ok,
        "; ".join(scope_detail) or "范围内每个对象都有对应结论")

    # ---- ⑤ 不变量:结论强度不能被削弱(有违例 ⇒ failed)
    vuln = [c for c in concl if c["verdict"] == "vulnerable"]
    sup = {a["hypothesis_id"] for a in attempts_all if a["result"] == "supported"}
    add("vulnerable_has_supported_attempt", all(c["hypothesis_id"] in sup for c in vuln)
        if vuln else True, f"vulnerable {len(vuln)} 条")
    add("vulnerable_evidence_causal", all(
        any(e["hypothesis_id"] == c["hypothesis_id"] and e["id"] in causal_ev for e in ev)
        for c in vuln) if vuln else True, "vulnerable 是否都有因果复核证据")

    running = con.execute("SELECT COUNT(*) n FROM attempts WHERE result='running'").fetchone()["n"]
    add("no_stuck_running_attempt", running == 0, f"running 尝试 {running} 条", critical=False)

    run = con.execute("SELECT state,handback_reason FROM runs ORDER BY started_at DESC"
                      " LIMIT 1").fetchone()
    ck = con.execute("SELECT COUNT(*) n FROM checkpoints").fetchone()["n"]
    # `turn()` 只有见到模型 FINAL 才把 run 置为 finished;paused 就是"预算耗尽/暂停"
    ended_normally = bool(run) and run["state"] == "finished"
    add("run_ended_normally", ended_normally,
        f"run.state={run['state'] if run else '?'}(finished=模型已收束)", critical=False)
    add("recoverable_pause", (not run or run["state"] != "paused") or bool(ck),
        f"checkpoints={ck}", critical=False)

    critical_ok = all(c["ok"] for c in checks.values() if c["critical"])
    failed = any(not c["ok"] for n, c in checks.items()
                 if n in ("vulnerable_evidence_causal", "vulnerable_has_supported_attempt"))
    scope_done = checks["scope_conclusions_complete"]["ok"]
    status = "failed" if failed else (
        "completed" if (critical_ok and scope_done and ended_normally) else "partial")
    return {"status": status, "checks": checks,
            "run": {"state": run["state"] if run else None,
                    "handback": run["handback_reason"] if run else None,
                    "ended_normally": ended_normally,
                    "scope_complete": scope_done,
                    "note": ("范围内结论已形成,但 run 因预算暂停(未正常收尾)"
                             if scope_done and not ended_normally else "")}}


def main() -> int:
    from fwcore import db as _db
    from fwcore.domain import Domain
    from fwcore.limits import Limits
    from fwcore.models import build_adapter
    from fwcore.policy import Authz, Policy
    from fwcore.runtime import Runner

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=20)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--out", default="docs/evidence/autonomous-research-drill.md")
    ap.add_argument("--real-material", default=None,
                    help="真实固件材料目录(已提取);给了就走真实固件只读分析模式")
    ap.add_argument("--real-glob", default="*", help="从材料目录挑哪些文件(glob)")
    ap.add_argument("--real-max-files", type=int, default=6)
    ap.add_argument("--profile", default=None, help="profile JSON(对象/期望结论)")
    args = ap.parse_args()

    key, base, model = load_credentials()
    if args.real_material:
        root, copied = build_real_workspace(args.real_material, args.real_glob,
                                            args.real_max_files)
        goal = REAL_GOAL
        if args.profile:
            try:
                with open(args.profile, encoding="utf-8") as f:
                    profile = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                raise SystemExit(f"读 profile 失败 {args.profile}: {e}") from e
        else:
            profile = {f"obj{i}": {"object": os.path.basename(p), "expect": "any"}
                       for i, p in enumerate(copied)}
    else:
        root = build_workspace()
        goal = GOAL
        profile = None
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("drill", version="synthetic-1")
    if args.real_material:
        dom.add_surface("drill", "fwroot/", reachability="pre-auth",
                        input_control="HTTP/CLI 参数(静态判断)")
    else:
        dom.add_surface("drill", "webroot/cgi-bin/", reachability="pre-auth",
                        input_control="QUERY_STRING")
    adapter = build_adapter("openai", api_key=key, base_url=base, model=model)
    policy = Policy(authorization=Authz(roots=[root], allow_shell=True,
                                        allow_high_risk=True, reason="演练(有界)"))
    limits = Limits(max_model_calls=args.max_calls, max_seconds=args.max_seconds,
                    max_tool_seconds=60)
    runner = Runner(con, root, adapter, policy=policy, limits=limits)
    t0 = time.time()
    res = runner.advance("drill", goal, max_rounds=args.max_rounds)
    elapsed = time.time() - t0
    rep = trace(con)
    rep["run"] = {"state": res.state, "steps": res.steps, "rounds": res.rounds,
                  "handback": res.handback, "final": res.final[:400],
                  "elapsed_s": round(elapsed, 1), "model": model,
                  "max_calls": args.max_calls, "max_seconds": args.max_seconds}
    verdict = assess(con, root, profile)
    out = os.path.join(ROOT, args.out)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"创建报告目录失败 {out}: {e}") from e
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("# 自主研究演练(真实模型,有界)\n\n")
            f.write("**等级:真实模型 + 合成目标**(不是真实固件研究质量证明)\n\n")
            f.write(f"- 模型 `{model}`,上限 calls={args.max_calls} / seconds={args.max_seconds}\n")
            f.write(f"- 结果: `{json.dumps(rep['run'], ensure_ascii=False)}`\n\n")
            f.write(f"## 闭环判定: **{verdict['status']}**\n\n")
            f.write("| 判据 | ok | 说明 |\n|---|---|---|\n")
            for name, c in verdict["checks"].items():
                f.write(f"| {name}{'' if c['critical'] else '(非关键)'} | "
                        f"{'✅' if c['ok'] else '❌'} | {c['detail']} |\n")
            f.write("\n")
            for section, title in (("tool_calls", "工具调用"), ("hypotheses", "假设"),
                                   ("attempts", "尝试"), ("observations", "观测"),
                                   ("evidence", "证据"), ("checks", "复核记录"),
                                   ("conclusions", "结论")):
                f.write(f"## {title}\n\n```json\n")
                f.write(json.dumps(rep[section], ensure_ascii=False, indent=1))
                f.write("\n```\n\n")
            f.write("## 事件序列\n\n```\n")
            f.write("\n".join(f"{e['kind']} {e['ref'] or ''}" for e in rep["events"][:120]))
            f.write("\n```\n")
    except OSError as e:
        raise SystemExit(f"写报告失败 {out}: {e}") from e

    print(json.dumps({**rep["run"], "verdict": verdict["status"]}, ensure_ascii=False))
    for name, c in verdict["checks"].items():
        print(f"  {'PASS' if c['ok'] else 'FAIL'} {name}: {c['detail']}")
    print(f"[drill] workspace={root} report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
