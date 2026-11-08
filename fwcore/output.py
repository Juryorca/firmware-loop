"""Pi 风格的工具输出累积器。

语义与 Pi 的 `OutputAccumulator`/`truncateTail` 保持一致:

- 模型可见输出是**尾部**，同时受两个独立上限约束，先到者生效：
  `max_lines=2000` 行、`max_bytes=51200`（50 KiB）。
- 被截断时把完整原始输出写入临时文件，结果里返回 `full_output_path`；
  不写 workspace artifact。
- `read_full_output(max_bytes)` 对超长完整输出保留头/尾各一半，中间用
  `[... N bytes omitted ...]` 标记，只在 UTF-8 字符边界切。
- `total_lines`/`total_bytes` 是原始输出的总量；`output_lines`/`output_bytes`
  是实际给模型看到的量。

这是"上下文边界"，不是安全边界；完整输出应通过路径检查，而不是无限塞给模型。
"""

from __future__ import annotations

import codecs
import contextlib
import os
import tempfile
from typing import Any

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
# Pi codemode 的 bash structured result 用 1 MiB 读取完整输出。
DEFAULT_FULL_READ_BYTES = 1024 * 1024


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_lines_for_counting(content: str) -> list[str]:
    """按 Pi 的 splitLinesForCounting：末尾换行不额外产生空行。"""
    if content == "":
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    buf = text.encode("utf-8")
    if len(buf) <= max_bytes:
        return text
    start = len(buf) - max_bytes
    while start < len(buf) and (buf[start] & 0xC0) == 0x80:
        start += 1
    return buf[start:].decode("utf-8", errors="replace")


def _truncate_by_bytes_from_end(text: str, max_bytes: int) -> str:
    """保留尾部 max_bytes，只做 UTF-8 字符边界修正。"""
    return _truncate_string_to_bytes_from_end(text, max_bytes)


def truncate_head(content: str, *, max_lines: int = DEFAULT_MAX_LINES,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """返回 Pi `truncateHead` 的结果字典（用于文件读取，保留开头）。"""
    total_bytes = _byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return {
            "content": content,
            "truncated": False,
            "truncated_by": None,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": total_lines,
            "output_bytes": total_bytes,
            "last_line_partial": False,
            "first_line_exceeds_limit": False,
            "max_lines": max_lines,
            "max_bytes": max_bytes,
        }

    if lines and _byte_length(lines[0]) > max_bytes:
        return {
            "content": "",
            "truncated": True,
            "truncated_by": "bytes",
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": 0,
            "output_bytes": 0,
            "last_line_partial": False,
            "first_line_exceeds_limit": True,
            "max_lines": max_lines,
            "max_bytes": max_bytes,
        }

    out: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    for i, line in enumerate(lines):
        if i >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if i else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        out.append(line)
        output_bytes += line_bytes
    if len(out) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(out)
    return {
        "content": output_content,
        "truncated": True,
        "truncated_by": truncated_by,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "output_lines": len(out),
        "output_bytes": _byte_length(output_content),
        "last_line_partial": False,
        "first_line_exceeds_limit": False,
        "max_lines": max_lines,
        "max_bytes": max_bytes,
    }


def truncate_tail(content: str, *, max_lines: int = DEFAULT_MAX_LINES,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """返回 Pi `truncateTail` 的结果字典。

    与 Pi 一样，普通情况下不返回半行；只有"唯一的最后一行本身超过 byte
    上限"时，才返回该行的尾部（`lastLinePartial=True`）。
    """
    total_bytes = _byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return {
            "content": content,
            "truncated": False,
            "truncated_by": None,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": total_lines,
            "output_bytes": total_bytes,
            "last_line_partial": False,
            "first_line_exceeds_limit": False,
            "max_lines": max_lines,
            "max_bytes": max_bytes,
        }

    out: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    last_line_partial = False

    for line in reversed(lines):
        if len(out) >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if out else 0)  # 行间换行
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not out:
                # 唯一一行就超限：只保留它的尾部，和 Pi 的 edge case 一致。
                partial = _truncate_by_bytes_from_end(line, max_bytes)
                out.insert(0, partial)
                output_bytes = _byte_length(partial)
                last_line_partial = True
            break
        out.insert(0, line)
        output_bytes += line_bytes

    if len(out) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(out)
    return {
        "content": output_content,
        "truncated": True,
        "truncated_by": truncated_by,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "output_lines": len(out),
        "output_bytes": _byte_length(output_content),
        "last_line_partial": last_line_partial,
        "first_line_exceeds_limit": False,
        "max_lines": max_lines,
        "max_bytes": max_bytes,
    }


def combined_output_text(out: dict[str, Any]) -> str:
    """按 Pi 的“单一输出”语义拼接 stdout/stderr，避免只取第一个字段。

    有 tail 时优先 tail；没有 tail 时回退原始字段；最后追加显式的 text 字段。
    """
    parts: list[str] = []
    for stream in ("stdout", "stderr"):
        tail = out.get(f"{stream}_tail")
        raw = out.get(stream)
        if isinstance(tail, str) and tail:
            parts.append(tail)
        elif isinstance(raw, str) and raw:
            parts.append(raw)
    text = out.get("text")
    if isinstance(text, str) and text:
        parts.append(text)
    return "\n".join(parts)


class OutputAccumulator:
    """流式累积工具输出，内存只保留尾部；完整输出按需写临时文件。"""

    def __init__(self, *, max_lines: int = DEFAULT_MAX_LINES,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 temp_file_prefix: str = "fwcore-output") -> None:
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.max_rolling_bytes = max(max_bytes * 2, 1)
        self.temp_file_prefix = temp_file_prefix
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._raw_chunks: list[bytes] = []
        self._tail_text = ""
        self._tail_bytes = 0
        self._tail_starts_at_line_boundary = True
        self._total_raw_bytes = 0
        self._total_decoded_bytes = 0
        self._completed_lines = 0
        self._total_lines = 0
        self._current_line_bytes = 0
        self._has_open_line = False
        self._finished = False
        self._temp_path: str | None = None
        self._temp_stream: Any = None
        self._binary = False

    @property
    def full_output_path(self) -> str | None:
        self._flush_temp()
        return self._temp_path

    @property
    def binary(self) -> bool:
        return self._binary

    def append(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")
        if not data:
            return
        self._total_raw_bytes += len(data)
        if b"\x00" in data:
            self._binary = True
        self._append_decoded_text(self._decoder.decode(data, final=False))
        if self._temp_stream is not None or self._should_use_temp_file():
            self._ensure_temp_file()
            if self._temp_stream is not None:
                self._temp_stream.write(data)
        else:
            self._raw_chunks.append(data)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._append_decoded_text(self._decoder.decode(b"", final=True))
        if self._should_use_temp_file():
            self._ensure_temp_file()

    def snapshot(self, *, persist_if_truncated: bool = False) -> dict[str, Any]:
        """返回模型可见的尾部、截断元数据和完整输出路径。"""
        tail = truncate_tail(self._snapshot_text(),
                             max_lines=self.max_lines, max_bytes=self.max_bytes)
        truncated = (self._total_lines > self.max_lines
                     or self._total_decoded_bytes > self.max_bytes)
        truncated_by = None
        if truncated:
            truncated_by = tail["truncated_by"]
            if truncated_by is None:
                truncated_by = ("bytes" if self._total_decoded_bytes > self.max_bytes
                                else "lines")
        truncation = dict(tail)
        truncation.update({
            "truncated": truncated,
            "truncated_by": truncated_by,
            "total_lines": self._total_lines,
            "total_bytes": self._total_decoded_bytes,
            "max_lines": self.max_lines,
            "max_bytes": self.max_bytes,
        })
        if persist_if_truncated and truncated:
            self._ensure_temp_file()
            self._flush_temp()
        return {
            "content": truncation["content"],
            "truncation": truncation,
            "full_output_path": self._temp_path,
            "binary": self._binary,
        }

    def close(self) -> None:
        self._flush_temp()
        if self._temp_stream is not None:
            try:
                self._temp_stream.close()
            finally:
                self._temp_stream = None

    def read_full_output(self, *, max_bytes: int = DEFAULT_FULL_READ_BYTES) -> dict[str, Any]:
        """读取完整输出；超长时保留头/尾并用省略标记。"""
        self._flush_temp()
        if not self._temp_path:
            return {
                "content": b"".join(self._raw_chunks).decode("utf-8", errors="replace"),
                "truncated": False,
            }
        try:
            size = os.path.getsize(self._temp_path)
            if size <= max_bytes:
                with open(self._temp_path, "rb") as f:
                    return {"content": f.read().decode("utf-8", errors="replace"),
                            "truncated": False}
            head_bytes = max_bytes // 2
            tail_bytes = max_bytes - head_bytes
            with open(self._temp_path, "rb") as f:
                head = f.read(head_bytes)
                f.seek(max(0, size - tail_bytes))
                tail = f.read(tail_bytes)
        except OSError as e:
            return {"content": "", "truncated": False,
                    "error": f"读取完整输出失败: {e}"}
        # head 末尾可能截断一个 UTF-8 字符；tail 开头跳过 continuation byte。
        head_text = head.decode("utf-8", errors="replace")
        tail_start = 0
        while tail_start < len(tail) and (tail[tail_start] & 0xC0) == 0x80:
            tail_start += 1
        tail_text = tail[tail_start:].decode("utf-8", errors="replace")
        omitted = size - head_bytes - tail_bytes
        return {
            "content": f"{head_text}\n\n[... {omitted} bytes omitted ...]\n\n{tail_text}",
            "truncated": True,
        }

    # ---------------- 内部 ----------------

    def _append_decoded_text(self, text: str) -> None:
        if not text:
            return
        size = _byte_length(text)
        self._total_decoded_bytes += size
        self._tail_text += text
        self._tail_bytes += size
        if self._tail_bytes > self.max_rolling_bytes * 2:
            self._trim_tail()

        newlines = text.count("\n")
        if newlines == 0:
            self._current_line_bytes += size
            self._has_open_line = True
        else:
            self._completed_lines += newlines
            last = text.rfind("\n")
            tail = text[last + 1:]
            self._current_line_bytes = _byte_length(tail)
            self._has_open_line = bool(tail)
        self._total_lines = self._completed_lines + (1 if self._has_open_line else 0)

    def _trim_tail(self) -> None:
        buf = self._tail_text.encode("utf-8")
        if len(buf) <= self.max_rolling_bytes:
            self._tail_bytes = len(buf)
            return
        start = len(buf) - self.max_rolling_bytes
        while start < len(buf) and (buf[start] & 0xC0) == 0x80:
            start += 1
        if start == 0:
            pass  # 保留原来是否处于行首的信息
        else:
            self._tail_starts_at_line_boundary = buf[start - 1] == 0x0A
        self._tail_text = buf[start:].decode("utf-8", errors="replace")
        self._tail_bytes = _byte_length(self._tail_text)

    def _snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail_text
        first_newline = self._tail_text.find("\n")
        return self._tail_text if first_newline < 0 else self._tail_text[first_newline + 1:]

    def _should_use_temp_file(self) -> bool:
        return (self._total_raw_bytes > self.max_bytes
                or self._total_decoded_bytes > self.max_bytes
                or self._total_lines > self.max_lines)

    def _ensure_temp_file(self) -> None:
        if self._temp_path:
            return
        fd, path = tempfile.mkstemp(prefix=f"{self.temp_file_prefix}-", suffix=".log")
        self._temp_path = path
        self._temp_stream = os.fdopen(fd, "wb")
        for chunk in self._raw_chunks:
            self._temp_stream.write(chunk)
        self._raw_chunks.clear()
        self._flush_temp()

    def _flush_temp(self) -> None:
        if self._temp_stream is not None:
            with contextlib.suppress(OSError):
                self._temp_stream.flush()

    def __del__(self) -> None:  # pragma: no cover - 仅兜底
        with contextlib.suppress(Exception):
            self.close()
