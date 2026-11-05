#!/usr/bin/env python3
"""独立核验"周期 XOR 恢复 .text"这类声明(确定性、离线,不依赖模型)。

用法:
  python3 tools/verify_recovered_text.py --original <原始二进制> --recovered <恢复出的 .text>
       [--elf <恢复后的 ELF>] [--period 4096] [--expect-len 121588]

检查:
  1. 长度与哈希(可选对 SHA256SUMS)
  2. 周期 XOR:key = 原文 XOR 恢复文本,统计 key[i]==key[i+p] 的比例(命中率)
  3. AArch64 解码质量:objdump 反汇编,统计 udf/invalid、函数序言、BL 数
  4. 交叉引用:BL 目标是否落在 .text 范围内
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys


def sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_range(elf: str) -> tuple[int, int]:
    """用 readelf 取 .text 的 offset/size。"""
    out = subprocess.run(["readelf", "-SW", elf], capture_output=True, text=True, timeout=60).stdout
    for line in out.splitlines():
        m = re.match(r"\s*\[\s*\d+\]\s+\.text\s+\w+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", line)
        if m:
            return int(m.group(1), 16), int(m.group(2), 16)
    raise SystemExit(f"找不到 .text: {elf}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--recovered", required=True)
    ap.add_argument("--elf", default=None)
    ap.add_argument("--period", type=int, default=4096)
    ap.add_argument("--expect-len", type=int, default=None)
    args = ap.parse_args()

    with open(args.recovered, "rb") as f:
        rec = f.read()
    with open(args.original, "rb") as f:
        orig_all = f.read()
    elf = args.elf or args.original
    off, size = text_range(elf)
    print(f"# 核验 {os.path.basename(args.recovered)}")
    print(f"- 恢复文本长度: {len(rec)}" + (f"(期望 {args.expect_len})" if args.expect_len else ""))
    print(f"- .text offset=0x{off:x} size={size}")
    print(f"- sha256(recovered) = {sha256(args.recovered)}")
    ok_len = (args.expect_len is None or len(rec) == args.expect_len)
    print(f"- 长度匹配: {'PASS' if ok_len else 'FAIL'}")

    region = orig_all[off:off + len(rec)]
    n = min(len(region), len(rec))
    key = bytes(region[i] ^ rec[i] for i in range(n))

    def hit(period: int) -> float:
        if period >= len(key):
            return 0.0
        same = sum(1 for i in range(len(key) - period) if key[i] == key[i + period])
        return same / (len(key) - period)

    print("\n## 周期 XOR 检验(key = 原文 XOR 恢复文本)")
    for p in (1024, 2048, args.period, args.period * 2):
        print(f"- period {p:6d}: key 一致率 {hit(p):.4f}")
    best = max((1024, 2048, args.period, args.period * 2), key=hit)
    print(f"- 最一致周期: {best} (一致率 {hit(best):.4f})")

    if args.elf and os.path.isfile(args.elf):
        print("\n## AArch64 解码质量")
        dis = subprocess.run(["aarch64-linux-gnu-objdump", "-d", "--no-show-raw-insn", args.elf],
                             capture_output=True, text=True, timeout=300).stdout
        lines = [ln for ln in dis.splitlines() if re.match(r"\s*[0-9a-f]+:", ln)]
        udf = [ln for ln in lines if re.search(r"\b(udf|unallocated|\.inst)\b", ln)]
        pro = [ln for ln in lines if "stp\tx29, x30" in ln or "stp x29, x30" in ln]
        bls = re.findall(r"\bbl\s+([0-9a-f]+)", dis)
        print(f"- 反汇编指令数: {len(lines)};疑似非法/未定义: {len(udf)} "
              f"({(100.0 * len(udf) / max(1, len(lines))):.2f}%)")
        print(f"- 函数序言(stp x29, x30): {len(pro)};BL 调用: {len(bls)}")
        inside = [int(x, 16) for x in bls if off <= int(x, 16) < off + len(rec)]
        print(f"- BL 目标落在 .text 内: {len(inside)}/{len(bls)}")
    return 0 if ok_len else 1


if __name__ == "__main__":
    sys.exit(main())
