"""Pi 风格输出边界的契约测试：2000 行 / 50 KiB 尾部 + full_output_path。"""

from __future__ import annotations

import os

from fwcore.output import (
    DEFAULT_MAX_BYTES,
    truncate_middle,
    DEFAULT_MAX_LINES,
    OutputAccumulator,
    truncate_head,
    truncate_tail,
)


def test_small_output_is_unchanged():
    text = "alpha\nbeta\n"
    r = truncate_tail(text)
    assert r["content"] == text
    assert r["truncated"] is False
    assert r["total_lines"] == 2


def test_tail_keeps_last_lines_and_marks_line_limit():
    text = "".join(f"{i}\n" for i in range(1, 5001))
    r = truncate_tail(text)
    assert r["total_lines"] == 5000
    assert r["output_lines"] == DEFAULT_MAX_LINES
    assert r["truncated"] is True
    assert r["truncated_by"] == "lines"
    assert r["content"].startswith("3001\n")
    assert r["content"].endswith("5000")


def test_tail_keeps_last_bytes_for_one_huge_line():
    text = "x" * (DEFAULT_MAX_BYTES + 10_000)
    r = truncate_tail(text)
    assert r["truncated"] is True
    assert r["truncated_by"] == "bytes"
    assert r["last_line_partial"] is True
    assert len(r["content"].encode("utf-8")) == DEFAULT_MAX_BYTES


def test_accumulator_creates_full_output_only_when_truncated(tmp_path):
    small = OutputAccumulator()
    small.append(b"hello\n")
    small.finish()
    small_snap = small.snapshot(persist_if_truncated=True)
    assert small_snap["truncation"]["truncated"] is False
    assert small_snap["full_output_path"] is None
    small.close()

    acc = OutputAccumulator()
    acc.append("".join(f"{i}\n" for i in range(1, 5001)).encode())
    acc.finish()
    snap = acc.snapshot(persist_if_truncated=True)
    path = snap["full_output_path"]
    assert path and os.path.isfile(path)
    try:
        full = acc.read_full_output(max_bytes=1024 * 1024)
        assert full["truncated"] is False
        assert "1\n" in full["content"]
        assert "5000\n" in full["content"]
    finally:
        acc.close()
        os.unlink(path)


def test_read_full_output_keeps_head_and_tail(tmp_path):
    acc = OutputAccumulator()
    # 超过 read_full_output 的 1 MiB 预算；完整文件仍然可回读。
    acc.append(("A" * (600_000) + "MIDDLE" + "Z" * (600_000)).encode())
    acc.finish()
    snap = acc.snapshot(persist_if_truncated=True)
    path = snap["full_output_path"]
    assert path and os.path.isfile(path)
    try:
        full = acc.read_full_output(max_bytes=1024 * 1024)
        assert full["truncated"] is True
        assert "bytes omitted" in full["content"]
        assert full["content"].startswith("A")
        assert full["content"].endswith("Z")
    finally:
        acc.close()
        os.unlink(path)


def test_head_keeps_first_lines_and_marks_line_limit():
    text = "".join(f"{i}\n" for i in range(1, 5001))
    r = truncate_head(text)
    assert r["total_lines"] == 5000
    assert r["output_lines"] == DEFAULT_MAX_LINES
    assert r["truncated"] is True
    assert r["truncated_by"] == "lines"
    assert r["content"].startswith("1\n")
    assert r["content"].endswith("2000")


def test_head_flags_first_line_over_byte_limit():
    r = truncate_head("x" * (DEFAULT_MAX_BYTES + 1))
    assert r["truncated"] is True
    assert r["first_line_exceeds_limit"] is True
    assert r["content"] == ""


def test_middle_truncation_keeps_head_and_tail():
    text = "H" * 20_000 + "M" * 20_000 + "T" * 20_000
    r = truncate_middle(text, max_bytes=20 * 1024)
    assert r["truncated"] is True
    assert r["content"].startswith("H")
    assert r["content"].endswith("T")
    assert "bytes truncated" in r["content"]
    assert len(r["content"].encode("utf-8")) <= 20 * 1024 + 80
