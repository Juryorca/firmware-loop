"""运行路径不读 Markdown:没有权威 DB 时必须 FAIL,不能静默回退。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "zz-nodb-runtime-probe"


def test_derived_check_fails_without_db():
    tdir = os.path.join(ROOT, "state", NAME)
    shutil.rmtree(tdir, ignore_errors=True)
    os.makedirs(tdir, exist_ok=True)
    try:
        with open(os.path.join(tdir, "notes.md"), "w", encoding="utf-8") as f:
            f.write("## 假设池\n- **H-01** 只有 Markdown,没有 DB\n")
        r = subprocess.run(["bash", "tools/derived_check.sh", NAME], cwd=ROOT,
                           capture_output=True, text=True, timeout=180)
        assert "缺权威状态" in r.stdout
        assert "=== derived_check: FAIL" in r.stdout
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def test_cli_gates_fails_for_unknown_target(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run([sys.executable, "-m", "fwcore", "--root", str(tmp_path),
                        "--db", db, "gates", "no-such-target"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2
    assert "目标不存在" in r.stderr
