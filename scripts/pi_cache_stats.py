#!/usr/bin/env python3
"""只读、脱敏地统计 Pi 会话的缓存命中(Pi 是外部开发工具;本脚本不写任何东西)。

输出只有**数字**,不包含消息内容/路径/凭据:
  - 命中率 = cacheRead / (input + cacheRead)  ← Pi 的 usage 口径
  - 可按 provider/model 过滤、按调用序号分段(冷启动 vs 稳态)

用法:
  python3 scripts/pi_cache_stats.py [--sessions-dir DIR] [--provider P] [--model M]
                                    [--bucket 1000] [--json]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys


def _num(v) -> int:
    """usage 字段可能是 str/float/None;非数字按 0(不猜)。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def iter_usage(paths: list[str]):
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    m = d.get("message") or {}
                    u = m.get("usage")
                    if not isinstance(u, dict) or "cacheRead" not in u:
                        continue
                    yield {"provider": m.get("provider"), "model": m.get("model"),
                           "input": _num(u.get("input")),
                           "cacheRead": _num(u.get("cacheRead")),
                           "cacheWrite": _num(u.get("cacheWrite")),
                           "output": _num(u.get("output")),
                           "reasoning": _num(u.get("reasoning"))}
        except OSError:
            continue


SIZE_BUCKETS = [(0, 20_000), (20_000, 60_000), (60_000, 150_000),
                (150_000, 300_000), (300_000, 10 ** 12)]


def hit_of(rs: list[dict]) -> float | None:
    i = sum(r["input"] for r in rs)
    c = sum(r["cacheRead"] for r in rs)
    return (c / (i + c) * 100) if (i + c) else None


def by_size(rows: list[dict]) -> list[dict]:
    """按**请求长度**分层(冷启动 vs 稳态不可比;不同规模也不可直接比)。"""
    out = []
    for lo, hi in SIZE_BUCKETS:
        rs = [r for r in rows if lo <= (r["input"] + r["cacheRead"]) < hi]
        if rs:
            out.append({"lo": lo, "hi": hi, "calls": len(rs), "hit_percent": hit_of(rs),
                        "prompt_mean": int(statistics.mean(
                            [r["input"] + r["cacheRead"] for r in rs]))})
    return out


def summarize(rows: list[dict], bucket: int) -> dict:
    def hit(rs):
        i = sum(r["input"] for r in rs)
        c = sum(r["cacheRead"] for r in rs)
        return (c / (i + c) * 100) if (i + c) else None

    prompts = [r["input"] + r["cacheRead"] for r in rows]
    buckets = []
    for start in range(0, len(rows), bucket):
        chunk = rows[start:start + bucket]
        buckets.append({"calls": len(chunk), "start": start, "hit_percent": hit(chunk)})
    return {
        "calls": len(rows),
        "prompt_tokens": sum(prompts),
        "input_tokens": sum(r["input"] for r in rows),
        "cache_read_tokens": sum(r["cacheRead"] for r in rows),
        "cache_write_tokens": sum(r["cacheWrite"] for r in rows),
        "output_tokens": sum(r["output"] for r in rows),
        "reasoning_tokens": sum(r["reasoning"] for r in rows),
        "hit_percent": hit(rows),
        "prompt_mean": int(statistics.mean(prompts)) if prompts else 0,
        "prompt_median": int(statistics.median(prompts)) if prompts else 0,
        "buckets": buckets,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pi 会话缓存统计(只读/脱敏)")
    ap.add_argument("--sessions-dir", default=os.path.expanduser(
        "~/.pi/agent/sessions"))
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--bucket", type=int, default=1000)
    ap.add_argument("--by-size", action="store_true",
                    help="按请求长度分层(不同规模的命中率不可直接比较)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(a.sessions_dir, "*", "*.jsonl"))
                   + glob.glob(os.path.join(a.sessions_dir, "*.jsonl")))
    rows = list(iter_usage(paths))
    if a.provider:
        rows = [r for r in rows if r["provider"] == a.provider]
    if a.model:
        rows = [r for r in rows if r["model"] == a.model]
    out = summarize(rows, a.bucket)
    if a.by_size:
        out["by_size"] = by_size(rows)
    out["sessions_scanned"] = len(paths)
    out["filter"] = {"provider": a.provider, "model": a.model}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"calls={out['calls']} prompt={out['prompt_tokens']:,} "
              f"input={out['input_tokens']:,} cacheRead={out['cache_read_tokens']:,} "
              f"hit={out['hit_percent']:.2f}% prompt_mean={out['prompt_mean']:,}")
        for b in out.get("by_size", []):
            print(f"  size {b['lo']:,}-{b['hi'] if b['hi'] < 10**12 else 'inf'}: "
                  f"calls={b['calls']} hit={b['hit_percent']:.2f}% "
                  f"prompt_mean={b['prompt_mean']:,}")
        for b in out["buckets"][:8]:
            print(f"  calls {b['start']}-{b['start'] + b['calls'] - 1}: "
                  f"hit={b['hit_percent']:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
