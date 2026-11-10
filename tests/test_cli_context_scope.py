"""`fw-core context` 的目标/run 隔离 + 只使用 provider usage。

回归背景:context 曾读**全局最新** checkpoint / model.call 事件 ⇒ 多目标同库时
会把别的目标的上下文规模报进来(误导压缩判断);也不再暴露字符估算接口。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from fwcore import db as _db
from fwcore.domain import Domain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-01-01T00:00:00.000000"


def _run(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "fwcore", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout, check=False)


def _mk_workspace(tmp_path) -> tuple[str, str]:
    """两个目标:alpha 小上下文 / beta 大上下文;各自一个 run + provider usage 事件。"""
    root = str(tmp_path)
    db = os.path.join(root, "state", "fwcore.sqlite3")
    con = _db.connect(db)
    dom = Domain(con, root)
    alpha = dom.create_target("alpha", version="1")
    beta = dom.create_target("beta", version="1")
    cases = [
        ("R-A", alpha, [{"role": "user", "content": "a" * 200}],
         {"prompt_tokens": 11, "prompt_tokens_details": {"cached_tokens": 5}}),
        ("R-B", beta, [{"role": "user", "content": "b" * 40_000}],
         {"prompt_tokens": 99_999, "prompt_tokens_details": {"cached_tokens": 1}}),
    ]
    for rid, tid, msgs, usage in cases:
        con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at)"
                    " VALUES(?,?,?,?,?)", (rid, tid, "finished", NOW, NOW))
        con.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at)"
                    " VALUES(?,?,?,?,?)", (f"CK-{rid}", rid, 1, json.dumps(msgs), NOW))
        con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                    (NOW, "model.call", rid, json.dumps({"usage": usage})))
    con.commit()
    con.close()
    return root, db


def test_context_reads_only_own_target(tmp_path):
    root, db = _mk_workspace(tmp_path)
    r = _run("--root", root, "--db", db, "context", "alpha")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["last_prompt_tokens"] == 11                  # 不是 beta 的 99,999
    assert out["used_input"] == 11 and out["used_source"] == "provider"
    assert "checkpoint_messages" not in out   # checkpoints 只是 step 指针,不是模型历史
    assert out["run_scope"] == "1 run(s)"
    assert "parts" not in out and "estimated_input" not in out


def test_context_rejects_foreign_run(tmp_path):
    root, db = _mk_workspace(tmp_path)
    r = _run("--root", root, "--db", db, "context", "alpha", "--run", "R-B")
    assert r.returncode == 2
    assert "不属于目标" in r.stderr


def test_context_ignores_latest_summarize_usage(tmp_path):
    root, db = _mk_workspace(tmp_path)
    con = _db.connect(db)
    for i in range(40):
        con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                    (NOW, "model.call", "R-A",
                     json.dumps({"kind": "summarize", "seq": i,
                                 "usage": {"prompt_tokens": 999_999}})))
    con.commit()
    con.close()

    r = _run("--root", root, "--db", db, "context", "alpha")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["last_prompt_tokens"] == 11
    assert out["used_input"] == 11 and out["used_source"] == "provider"
