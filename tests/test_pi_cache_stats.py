"""Pi 会话缓存统计脚本:只读、脱敏、可过滤(用合成会话验证,不碰真实会话)。"""

from __future__ import annotations

import json
import subprocess
import sys


def _write(path: str, provider: str, model: str, inp: int, cached: int, secret: str) -> None:
    rec = {"type": "message", "message": {"role": "assistant", "provider": provider,
           "model": model, "content": [{"type": "text", "text": secret}],
           "usage": {"input": inp, "cacheRead": cached, "cacheWrite": 0,
                     "output": 10, "reasoning": 3}}}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_pi_cache_stats_filter_and_desensitize(tmp_path):
    sess = tmp_path / "sess"
    sess.mkdir()
    p = sess / "a.jsonl"
    _write(str(p), "deepseek", "deepseek-flash", 100, 900, "SECRET-ONE")
    _write(str(p), "deepseek", "deepseek-flash", 200, 800, "SECRET-TWO")
    _write(str(p), "other", "deepseek-flash", 1000, 0, "SECRET-THREE")
    out = subprocess.run(
        [sys.executable, "scripts/pi_cache_stats.py", "--sessions-dir", str(sess),
         "--provider", "deepseek", "--json"],
        capture_output=True, text=True, check=True)
    d = json.loads(out.stdout)
    assert d["calls"] == 2 and d["input_tokens"] == 300 and d["cache_read_tokens"] == 1700
    assert abs(d["hit_percent"] - 1700 / 2000 * 100) < 1e-6
    assert "SECRET" not in out.stdout and "content" not in out.stdout
    assert d["filter"] == {"provider": "deepseek", "model": None}
