#!/usr/bin/env python3
"""答案隔离的研究评测:模型只看 bundle/,评分用 answers/(两者物理分开)。

为什么要这样:如果目标答案写进 agent 可读的规则/提示,就等于自己给自己发卷子 —— 此前的
"真实 E2E" 只证明了工具链路,不证明研究能力。这里:
- 工作区 = 临时目录 + `bundle/`(设备模拟程序) + 目标文本;
- 评分 = 事后读权威状态(结论/证据/尝试/观察交代),对照 `answers/expected.json` 的**判据**;
- 评测过程记录落 `docs/evidence/eval-<task>.md`(含被判定的结论原文),供人复核。

用法:
  python3 eval/run.py --task sig-after-parse [--rounds 3] [--steps 12] [--model deepseek]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fwcore import db as _db  # noqa: E402
from fwcore.domain import Domain  # noqa: E402
from fwcore.models import build_adapter, load_model_config  # noqa: E402
from fwcore.policy import Authz, Policy  # noqa: E402
from fwcore.runtime import Runner  # noqa: E402

TASKS = os.path.join(ROOT, "eval", "tasks")


def load_task(task: str) -> tuple[dict, dict]:
    """返回 (公开任务元数据, 私有答案)。

    **隔离原则**:`goal` 只从 `task.json`(公开)读取;`answers/expected.json` 只用于评分,
    绝不允许把私有文件内容喂给 agent(也不能把它的目录拷进工作区)。
    """
    tdir = os.path.join(TASKS, task)
    pub_path = os.path.join(tdir, "task.json")
    priv_path = os.path.join(tdir, "answers", "expected.json")
    for p_, label in ((pub_path, "公开 task.json"), (priv_path, "私有 answers/expected.json")):
        if not os.path.isfile(p_):
            raise SystemExit(f"任务缺少{label}: {p_}")
    try:
        with open(pub_path, encoding="utf-8") as f:
            public = json.load(f)
        with open(priv_path, encoding="utf-8") as f:
            private = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"读任务文件失败: {e}") from e
    if not public.get("goal"):
        raise SystemExit(f"{pub_path} 缺 goal(goal 必须来自公开侧)")
    if "goal" in private:
        raise SystemExit(f"{priv_path} 里还有 goal ✗(goal 必须只在公开侧)")
    return public, private


def prepare_workspace(task: str) -> str:
    """建临时工作区:只拷 `bundle/`,**不拷** answers/。"""
    tdir = os.path.join(TASKS, task)
    scratch = os.path.join(ROOT, "state", "_scratch")
    try:
        os.makedirs(scratch, exist_ok=True)
        work = tempfile.mkdtemp(prefix=f"fweval-{task}-", dir=scratch)
        shutil.copytree(os.path.join(tdir, "bundle"), os.path.join(work, "bundle"))
        os.makedirs(os.path.join(work, "state"), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"准备工作区失败: {e}") from e
    if os.path.exists(os.path.join(work, "answers")):
        raise SystemExit("工作区里出现了 answers/ ⇒ 隔离被破坏")
    return work


def _write_evidence(out: str, task: str, model: str, workdir: str, score: dict, run) -> None:
    """把一次评测写成可复查的 Markdown(含模型结论原文与评分依据)。"""
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError as e:
        print(f"[warn] 无法创建输出目录: {e}", file=sys.stderr)
    lines = [f"# 研究评测:{task}", "", f"- 模型: `{model}` · workdir: `{workdir}`"]
    if run:
        lines.append(f"- run: state={run.state} rounds={run.rounds} handback={run.handback}")
    lines += [f"- 判定: **{'PASS' if score['passed'] else 'FAIL'}**", "", "## 检查项", ""]
    lines += [f"- {k}: {'✓' if v else '✗'}" for k, v in score["checks"].items()]
    lines += ["", "## 模型给出的结论(原文)", ""]
    for c in score["conclusions"]:
        lines += [f"### [{c['verdict']}] {c['title']}", "", f"适用范围: {c['scope']}", ""]
    lines += ["## 期望(评测侧,不进入 agent 上下文)", "", score.get("expected_why", ""), ""]
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[warn] 写记录失败: {e}", file=sys.stderr)


def _score(task: str, answers: dict, con, target_id: str) -> dict:
    """委派给 eval/scoring.py(单一评分实现;支持对照任务与结论↔证据因果检查)。"""
    import sys as _sys

    if os.path.join(ROOT, "eval") not in _sys.path:
        _sys.path.insert(0, os.path.join(ROOT, "eval"))
    import scoring  # noqa: PLC0415

    out = scoring.score(answers, con, target_id)
    out["expected_why"] = out.get("expected_why") or answers["expected"].get("why", "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--steps", type=int, default=0, help="0 = 不限(默认)")
    ap.add_argument("--model", default="deepseek")
    ap.add_argument("--max-seconds", type=float, default=1800.0, help="执行安全上限(不是研究预算)")
    ap.add_argument("--rescore", default=None, help="对已有 workdir 重新评分(不调用模型)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    public, answers = load_task(args.task)

    if args.rescore:
        con0 = _db.connect(os.path.join(args.rescore, "state", "fwcore.sqlite3"))
        tid0 = con0.execute("SELECT id FROM targets LIMIT 1").fetchone()["id"]
        score0 = _score(args.task, answers, con0, tid0)
        out0 = args.out or os.path.join(ROOT, "docs", "evidence", f"eval-{args.task}.md")
        _write_evidence(out0, args.task, args.model, args.rescore, score0, None)
        print(json.dumps({k: v for k, v in score0.items() if k != "conclusions"}, ensure_ascii=False, indent=2))
        print(f"[evidence] {out0}(重评,未调用模型)")
        return 0 if score0["passed"] else 1

    work = prepare_workspace(args.task)      # 内部已建好 state/_scratch 并只拷 bundle/

    con = _db.connect(os.path.join(work, "state", "fwcore.sqlite3"))
    dom = Domain(con, work)
    tid = dom.create_target(args.task, version="sim-1")
    dom.add_surface(args.task, "bundle/", reachability="pre-auth", input_control="请求报文")

    if args.model == "scripted":
        adapter = build_adapter("scripted", script=[{"content": "FINAL: (scripted 不做研究)", "tool_calls": []}])
    else:
        key, base, model = load_model_config(ROOT)
        os.environ.setdefault("FW_REASONING_CONTENT", "1")
        adapter = build_adapter("openai", api_key=key, base_url=base, model=model)

    authz = Authz(roots=[work], allow_shell=True, allow_high_risk=True, reason="评测授权(仅本临时工作区)")
    runner = Runner(con, work, adapter, max_steps=(args.steps or None),
                    policy=Policy(authorization=authz), timeout=60, max_seconds=args.max_seconds)
    res = runner.advance(args.task, public["goal"], max_rounds=args.rounds)
    score = _score(args.task, answers, con, tid)
    score.update({"run": {"state": res.state, "rounds": res.rounds, "handback": res.handback,
                          "final": res.final[:300]}, "workdir": work})

    out = args.out or os.path.join(ROOT, "docs", "evidence", f"eval-{args.task}.md")
    _write_evidence(out, args.task, args.model, work, score, res)
    print(json.dumps({k: v for k, v in score.items() if k != "conclusions"}, ensure_ascii=False, indent=2))
    print(f"[evidence] {out}")
    return 0 if score["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
