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

from fwcore import db as _db  # noqa: E402
from fwcore.domain import Domain  # noqa: E402
from fwcore.limits import Limits  # noqa: E402
from fwcore.models import build_adapter  # noqa: E402
from fwcore.policy import Authz, Policy  # noqa: E402
from fwcore.runtime import Runner  # noqa: E402

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
    path = os.path.expanduser("~/.pi/agent/models.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"读不到模型配置 {path}: {e}") from e
    prov = d.get("providers", {}).get("deepseek", {})
    key = prov.get("apiKey") or os.environ.get("FW_API_KEY", "")
    base = prov.get("baseUrl") or "https://api.deepseek.com"
    model = (prov.get("models") or [{}])[0].get("id") or "deepseek-chat"
    return key, base, model


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


def main() -> int:
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
    out = os.path.join(ROOT, args.out)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        fh = open(out, "w", encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"写报告失败 {out}: {e}") from e
    with fh as f:
        f.write("# 自主研究演练(真实模型,有界)\n\n")
        f.write("**等级:真实模型 + 合成目标**(不是真实固件研究质量证明)\n\n")
        f.write(f"- 模型 `{model}`,上限 calls={args.max_calls} / seconds={args.max_seconds}\n")
        f.write(f"- 结果: `{json.dumps(rep['run'], ensure_ascii=False)}`\n\n")
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
    print(json.dumps(rep["run"], ensure_ascii=False))
    print(f"[drill] workspace={root} report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
