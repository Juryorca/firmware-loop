#!/usr/bin/env python3
"""真实模型端到端:多轮工具调用 + 状态更新 + 暂停恢复。

与单元测试的区别(必须写清楚):
- 单元测试用脚本化模型 ⇒ 只证明"循环/协议/恢复机制"正确;
- 本脚本用**真实模型**(DeepSeek,OpenAI 兼容)⇒ 证明"模型真的能通过工具改状态并被恢复"。
两者都不能单独证明"研究质量",只能证明工程闭环。

用法: python3 scripts/e2e_real_model.py [--steps 6] [--out docs/evidence/real-model-run.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fwcore import db as _db  # noqa: E402
from fwcore.domain import Domain  # noqa: E402
from fwcore.model_client import to_api_messages  # noqa: E402
from fwcore.models import build_adapter  # noqa: E402
from fwcore.policy import Authz, Policy  # noqa: E402
from fwcore.runtime import Runner  # noqa: E402


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
    if not key:
        raise SystemExit("没有可用的 DeepSeek API key(~/.pi/agent/models.json)")
    return key, base, model


GOAL = (
    "工程闭环验收任务(不是研究任务,请严格照做):1) 用 fs_hash 分别算 a.txt 与 b.txt 的 sha256;"
    "2) 用 state_add_observation 登记一条观察,内容里必须包含两个哈希;"
    "3) 用 state_add_hypothesis 建一条假设,标题写 'b.txt 与 a.txt 内容差异';"
    "4) 对该假设 state_start_attempt(method='fs_hash'),然后 state_finish_attempt,"
    "result 用 inconclusive,note 写清楚'已拿到哈希,还缺什么才能判定';"
    "5) 最后输出 FINAL: 一行小结。"
)


def main() -> int:
    os.environ.setdefault("FW_REASONING_CONTENT", "1")   # DeepSeek thinking 模式必需
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=0, help="0 = 不限(默认;由模型自己收)")
    ap.add_argument("--out", default="docs/evidence/real-model-run.md")
    args = ap.parse_args()

    key, base, model = load_credentials()
    try:
        scratch = os.path.join(ROOT, "state", "_scratch")
        os.makedirs(scratch, exist_ok=True)
        root = tempfile.mkdtemp(prefix="fw-e2e-", dir=scratch)
        os.makedirs(os.path.join(root, "state"), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"准备临时工作区失败: {e}") from e
    try:
        for name, body in (("a.txt", "hello-a\n"), ("b.txt", "hello-b\n")):
            with open(os.path.join(root, name), "w", encoding="utf-8") as f:
                f.write(body)
    except OSError as e:
        raise SystemExit(f"准备测试文件失败: {e}") from e

    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("e2e", version="1.0")
    dom.add_surface("e2e", "a.txt/b.txt", reachability="pre-auth", input_control="文件内容")

    # 与 CLI 走同一条模型适配路径(models.build_adapter),不再有第二套协议实现
    os.environ.setdefault("FW_REASONING_CONTENT", "1")
    adapter = build_adapter("openai", api_key=key, base_url=base, model=model)
    policy = Policy(authorization=Authz(roots=[root], allow_shell=True, allow_high_risk=True,
                                        reason="E2E 临时工作区"))
    runner = Runner(con, root, adapter, max_steps=(args.steps or None), policy=policy)
    rid = runner.start("e2e", goal=GOAL)
    phase1 = runner.run(rid, "e2e", goal=GOAL)
    print(f"[phase1] state={phase1['state']} steps={phase1['steps']} ready={phase1['ready']}")

    # 中断后再恢复:模拟"会话结束/进程重启",从检查点继续
    phase2 = runner.resume(rid, goal=GOAL + "\n(恢复:继续没做完的部分)")
    print(f"[phase2] state={phase2['state']} steps={phase2['steps']} ready={phase2['ready']}")

    # 第三阶段:自动继续(advance)—— 真实模型下的跨轮协议(含"FINAL 但还有未收口项"的续轮)
    phase3 = runner.advance("e2e", goal=GOAL + "\n(自动继续:推进未收口项)", max_rounds=2, run_id=rid)
    print(f"[phase3/advance] state={phase3['state']} rounds={phase3['rounds']} "
          f"steps={phase3['steps']} handback={phase3['handback']}")

    # 必做断言:机制层面成立(不评价研究质量)
    calls = con.execute("SELECT tool,status FROM tool_calls").fetchall()
    hyps = con.execute("SELECT id,title,state FROM hypotheses").fetchall()
    obs = con.execute("SELECT text FROM observations").fetchall()
    atts = con.execute("SELECT id,result,note FROM attempts").fetchall()
    api_ok = True
    try:
        to_api_messages(phase2["history"])
    except ValueError as e:
        api_ok = False
        print("history 不合法:", e)

    try:
        os.makedirs(os.path.dirname(os.path.join(ROOT, args.out)), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"创建输出目录失败: {e}") from e
    lines = [
        "# 真实模型端到端记录(自动生成)",
        "",
        f"- 模型: `{model}` · 端点: `{base}`",
        f"- 目标: `e2e` v1.0 · 两阶段(第一阶段 {args.steps} 步上限后暂停,再恢复)",
        f"- 阶段一: state={phase1['state']} steps={phase1['steps']} ready={phase1['ready']}",
        f"- 阶段二: state={phase2['state']} steps={phase2['steps']} ready={phase2['ready']}",
        f"- FINAL: {phase2['final'][:200]}",
        f"- history 通过 OpenAI 协议校验: {api_ok}",
        "",
        "## 工具调用",
        "",
        "| tool | status |",
        "|---|---|",
        *[f"| {r['tool']} | {r['status']} |" for r in calls],
        "",
        "## 状态更新",
        "",
        f"- 假设: {len(hyps)} 条",
        *[f"  - {h['id']} {h['title'][:80]} [{h['state']}]" for h in hyps],
        f"- 尝试: {len(atts)} 条",
        *[f"  - {a['id']} result={a['result']} note={(a['note'] or '')[:80]}" for a in atts],
        f"- 观察: {len(obs)} 条",
        *[f"  - {o['text'][:120]}" for o in obs],
        "",
        "## 收尾阻塞(程序算)",
        "",
        *[f"- {b}" for b in (phase2["blockers"] or ["(无)"])],
        "",
    ]
    out = os.path.join(ROOT, args.out)
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[warn] 写记录失败: {e}", file=sys.stderr)

    ok = bool(calls) and len(hyps) >= 1 and api_ok and phase2["steps"] > phase1["steps"]
    print(f"[assert] tool_calls={len(calls)} hypotheses={len(hyps)} attempts={len(atts)} "
          f"observations={len(obs)} api_ok={api_ok} → {'PASS' if ok else 'FAIL'}")
    print(f"[artifacts] {root}  |  记录: {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
