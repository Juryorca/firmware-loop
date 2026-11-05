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


def assess(con, root: str) -> dict:
    """把"研究闭环"拆成可核判据(不看 FINAL、不看调用数量)。

    completed: 全部关键判据通过(危险行为有真实执行证据、安全对照被反证测试过、
               证据与执行/实验/复核连得上、结论与证据强度匹配、预算暂停仍可恢复)
    partial : 执行链路正常,但研究没走完(缺实验/证据/结论)
    failed  : 出现不变量违背或证据造假风险(如 vulnerable 无因果证据、结论无 supported)
    """
    checks: dict[str, dict] = {}

    def add(name: str, ok: bool, detail: str, critical: bool = True) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail, "critical": critical}

    tool_rows = [dict(r) for r in con.execute(
        "SELECT id,tool,status,args,result_path,stdout_path FROM tool_calls ORDER BY created_at")]
    exec_rows = [r for r in tool_rows if r["tool"] == "shell_exec" and r["status"] == "ok"]
    inj = [r for r in exec_rows
           if "status.cgi" in (r["args"] or "") and any(
               m in (r["args"] or "") for m in (";", "$(", "`", "&&", "|"))]
    add("danger_executed_with_real_payload", bool(inj),
        f"命中 {len(inj)} 次针对 status.cgi 的注入执行" if inj else "没有对 status.cgi 的实际注入执行")

    safe_exec = [r for r in exec_rows if "login.cgi" in (r["args"] or "")]
    add("safe_control_probed", len(safe_exec) >= 2,
        f"对 login.cgi 执行了 {len(safe_exec)} 次(对照需要多载荷反证)",
        critical=False)

    ev = [dict(r) for r in con.execute("SELECT id,kind,path,tool_call_id,verified,"
                                       "hypothesis_id,attempt_id FROM evidence")]
    linked = [e for e in ev if e["tool_call_id"]]
    add("evidence_linked_to_execution", bool(linked),
        f"{len(linked)}/{len(ev)} 条证据绑定了工具调用")
    decisive_att = [dict(r) for r in con.execute(
        "SELECT id,hypothesis_id FROM attempts WHERE result IN ('supported','refuted')")]
    linked_att = {e["attempt_id"] for e in ev if e["attempt_id"]}
    linked_hyp = {e["hypothesis_id"] for e in ev if e["hypothesis_id"]}
    missing = [a["id"] for a in decisive_att
               if a["id"] not in linked_att and a["hypothesis_id"] not in linked_hyp]
    add("decisive_attempts_have_evidence", bool(decisive_att) and not missing,
        f"决定性尝试 {len(decisive_att)} 个,缺证据的 {len(missing)} 个 {missing[:3]}")

    checks_rows = [dict(r) for r in con.execute("SELECT evidence_id,method FROM evidence_checks")]
    causal = {c["evidence_id"] for c in checks_rows
              if c["method"] in ("reproduce-attempt", "cross-check")}
    hyp_with_att = {a["hypothesis_id"] for a in con.execute(
        "SELECT hypothesis_id FROM attempts WHERE result!='running'")}
    hyp_verified = {e["hypothesis_id"] for e in ev if e["verified"]}
    add("attempted_hypotheses_have_verified_evidence",
        bool(hyp_with_att) and hyp_with_att <= hyp_verified,
        f"有待决断尝试的假设 {len(hyp_with_att)} 个,已有复核证据的 {len(hyp_verified)} 个")

    concl = [dict(r) for r in con.execute(
        "SELECT hypothesis_id,verdict,scope_note FROM conclusions")]
    vuln = [c for c in concl if c["verdict"] == "vulnerable"]
    sup = {r["hypothesis_id"] for r in con.execute(
        "SELECT hypothesis_id FROM attempts WHERE result='supported'")}
    add("vulnerable_evidence_causal", all(
        any(e["hypothesis_id"] == c["hypothesis_id"] and e["id"] in causal for e in ev)
        for c in vuln) if vuln else True,
        f"vulnerable 结论 {len(vuln)} 条")
    add("vulnerable_has_supported_attempt", all(c["hypothesis_id"] in sup for c in vuln)
        if vuln else True, "vulnerable 是否都有 supported 尝试")

    running = con.execute("SELECT COUNT(*) n FROM attempts WHERE result='running'").fetchone()["n"]
    add("no_stuck_running_attempt", running == 0, f"仍在 running 的尝试 {running} 条",
        critical=False)

    run = con.execute("SELECT state,handback_reason FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    ck = con.execute("SELECT COUNT(*) n FROM checkpoints").fetchone()["n"]
    add("recoverable_pause", bool(ck) if run and run["state"] == "paused" else True,
        f"run={run['state'] if run else '?'} checkpoints={ck}", critical=False)

    add("research_concluded", bool(concl), f"结论 {len(concl)} 条", critical=False)

    critical_ok = all(c["ok"] for c in checks.values() if c["critical"])
    completed = critical_ok and bool(concl) and running == 0
    failed = any(not c["ok"] for n, c in checks.items()
                 if n in ("vulnerable_evidence_causal", "vulnerable_has_supported_attempt"))
    status = "failed" if failed else ("completed" if completed else "partial")
    return {"status": status, "checks": checks,
            "run": {"state": run["state"] if run else None,
                    "handback": run["handback_reason"] if run else None}}


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
    args = ap.parse_args()

    key, base, model = load_credentials()
    root = build_workspace()
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("drill", version="synthetic-1")
    dom.add_surface("drill", "webroot/cgi-bin/", reachability="pre-auth",
                    input_control="QUERY_STRING")
    adapter = build_adapter("openai", api_key=key, base_url=base, model=model)
    policy = Policy(authorization=Authz(roots=[root], allow_shell=True,
                                        allow_high_risk=True, reason="演练(有界)"))
    limits = Limits(max_model_calls=args.max_calls, max_seconds=args.max_seconds,
                    max_tool_seconds=60)
    runner = Runner(con, root, adapter, policy=policy, limits=limits)
    t0 = time.time()
    res = runner.advance("drill", GOAL, max_rounds=args.max_rounds)
    elapsed = time.time() - t0
    rep = trace(con)
    rep["run"] = {"state": res.state, "steps": res.steps, "rounds": res.rounds,
                  "handback": res.handback, "final": res.final[:400],
                  "elapsed_s": round(elapsed, 1), "model": model,
                  "max_calls": args.max_calls, "max_seconds": args.max_seconds}
    verdict = assess(con, root)
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
