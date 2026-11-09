"""QuickJS Codemode tool (Pi v1.5-compatible subset).

The JavaScript VM runs in :mod:`fwcore.codemode_worker`; this module is the
parent adapter.  Tool calls are sent back here and executed through the normal
``Registry.call`` path, so authorization, recording, persistence, cancellation,
and MCP behavior are not duplicated.

The worker process exists only to make CPU timeout/cancellation reliable despite
the Python QuickJS binding's callback/time-limit interaction.  It is not a
second Agent, tool executor, session, or state store.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, TextIO, cast

from . import db as _db
from .domain import Domain
from .mcp_content import MAX_IMAGE_BYTES
from .mcp_content import image_part as mcp_image_part
from .output import save_temp_text, truncate_middle
from .research import Research

CODEMODE_TOOL_NAME = "codemode"
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_MAX_OUTPUT_TOKENS = 10_000
MAX_TOOL_CALLS = 100_000
MAX_IMAGES = 8


def codemode_identifier(name: str) -> str:
    """Pi's ``tools.<id>`` identifier, with deterministic collision handling."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _parse_options(code: str) -> tuple[dict, str | None]:
    """Read Pi's optional first-line ``// @options: {...}``."""
    lines = code.splitlines()
    options: dict = {}
    if lines:
        m = re.match(r"^\s*//\s*@options:\s*(\{.*\})\s*$", lines[0])
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError as e:
                return {}, f"invalid @options JSON: {e}"
            if not isinstance(parsed, dict):
                return {}, "@options must be a JSON object"
            options = parsed
    timeout = options.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        return {}, "timeout_ms must be a positive number"
    max_tokens = options.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        return {}, "max_output_tokens must be a positive integer"
    return {"timeout_ms": int(min(timeout, 10 * 60_000)),
            "max_output_tokens": int(min(max_tokens, 100_000))}, None


def _cancelled(ctx) -> bool:
    """Use the existing run cancellation state; no Codemode-specific ACL."""
    if not getattr(ctx, "run_id", None):
        return False
    try:
        row = ctx.domain.con.execute(
            "SELECT handback_reason FROM runs WHERE id=?", (ctx.run_id,)
        ).fetchone()
    except Exception:  # noqa: BLE001 - DB failures must not deadlock a script
        return False
    return bool(row and (row["handback_reason"] or "").startswith("user_stop"))


def _tool_meta(registry) -> list[dict]:
    metadata = registry.callable_metadata()
    used: set[str] = set()
    out: list[dict] = []
    for item in metadata:
        original = str(item.get("name") or "")
        ident = codemode_identifier(original)
        if ident in used:
            suffix = hashlib.sha256(original.encode()).hexdigest()[:8]
            ident = f"{ident}_{suffix}"
        used.add(ident)
        out.append({
            "name": original,
            "id": ident,
            "description": item.get("description") or "",
            "input_schema": item.get("input_schema") or {"type": "object", "properties": {}},
            "namespace": item.get("namespace"),
        })
    return out


def _image_part(item: dict, root: str) -> dict:
    """Convert a worker image block without turning it into base64 text."""
    mime = str(item.get("mimeType") or "").split(";", 1)[0].strip().lower()
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return {"type": "text", "text": f"[image omitted: unsupported MIME type {mime or 'unknown'}]"}
    path = item.get("path")
    if isinstance(path, str) and path:
        try:
            size = os.path.getsize(path)
        except OSError as e:
            return {"type": "text", "text": f"[image {path} omitted: {type(e).__name__}: {e}]"}
        if size > MAX_IMAGE_BYTES:
            return {"type": "text",
                    "text": f"[image {path} omitted: {size} bytes exceeds {MAX_IMAGE_BYTES} byte limit]"}
        real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        if real != root_real and not real.startswith(root_real + os.sep):
            return {"type": "text",
                    "text": f"[image {path} omitted: outside the research workspace]"}
        return {"type": "image", "mimeType": mime, "path": path,
                "bytes": size, "sha256": _file_sha(path)}
    data = item.get("data")
    if isinstance(data, str):
        part = mcp_image_part(data, mime, store_dir=None)
        return part
    return {"type": "text", "text": "[image omitted: no path or data]"}


def _file_sha(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _bounded_text(text: str, max_tokens: int) -> tuple[str, str | None, bool]:
    max_chars = max(1024, int(max_tokens) * 4)
    if len(text) <= max_chars:
        return text, None, False
    full_path = None
    with contextlib.suppress(OSError):
        full_path = save_temp_text(text, prefix="fwcore-codemode", suffix=".txt")
    truncated = truncate_middle(text, max_bytes=max_chars)["content"]
    where = f"\n[Full output: {full_path}]" if full_path else ""
    return truncated + where, full_path, True


def _format_result(worker: dict, elapsed_ms: int, max_tokens: int, root: str) -> dict:
    outputs = worker.get("outputs") or []
    console = worker.get("console") or []
    error = worker.get("error")
    text_items: list[str] = []
    image_parts: list[dict] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "image":
            part = _image_part(item, root)
            image_parts.append(part)
            if part.get("type") == "image":
                text_items.append(f"[image: {part.get('path')}]")
            else:
                # An omitted image must stay visible in text; silently dropping
                # the explanation makes the script look like it produced nothing.
                text_items.append(str(part.get("text") or ""))
        else:
            text_items.append(str(item.get("text") or ""))
    header = "Script failed" if error else "Script completed"
    text_lines = [f"{header} in {elapsed_ms / 1000:.2f}s"]
    if len(text_items) == 1:
        text_lines.append(text_items[0])
    elif len(text_items) > 1:
        for i, item in enumerate(text_items, 1):
            text_lines.append(f"==> text {i}/{len(text_items)} <==")
            text_lines.append(item)
    if console:
        text_lines.append("<console_output>")
        text_lines.extend(str(line) for line in console)
    if error:
        text_lines.append("Script error:")
        text_lines.append(str(error)[:5000])
    text = "\n".join(text_lines)
    text, full_path, truncated = _bounded_text(text, max_tokens)
    parts: list[dict] = [{"type": "text", "text": text}]
    parts.extend(image_parts)
    result = {
        "status": "error" if error else "ok",
        "text": text,
        "parts": parts,
        "images": [p for p in image_parts if p.get("type") == "image"],
        "console": console,
        "elapsed_ms": elapsed_ms,
        "truncated": truncated,
    }
    if full_path:
        result["full_output_path"] = full_path
    if error:
        result["error"] = str(error)[:5000]
    return result


class CodemodeExecutor:
    """Parent-side bridge; one worker process per script."""

    def __init__(self, registry, ctx):
        self.registry = registry
        self.ctx = ctx
        self._lock = threading.Lock()
        self._mcp_lock = threading.Lock()
        self._seq = 0

    def _call_key(self) -> str:
        self._seq += 1
        return f"codemode:{self.ctx.run_id or 'local'}:{uuid.uuid4().hex}:{self._seq}"

    def _isolated_ctx(self):
        """A per-thread DB connection for concurrent nested calls.

        The registry and tool handler are reused; only the SQLite connection is
        isolated so parallel `tools.*()` calls do not interleave transactions on
        one sqlite3 connection.  This is not a second state store.
        """
        try:
            row = self.ctx.domain.con.execute("PRAGMA database_list").fetchone()
            path = row["file"] if row is not None else ""
        except Exception:  # noqa: BLE001 - fall back to the existing context
            path = ""
        if not path:
            return self.ctx, None
        con = _db.connect(path)
        domain = Domain(con, self.ctx.root)
        research = Research(self.ctx.root, domain) if self.ctx.research is not None else None
        return replace(self.ctx, domain=domain, research=research), con

    def _execute_tool(self, name: str, args: dict) -> dict:
        # Reuse the existing Registry path: policy, SQLite recording, idempotency,
        # evidence/provenance, and MCP handling all stay in one place.
        ctx, con = self._isolated_ctx()
        try:
            # One MCP stdio client is a single request/response stream.  Keep
            # nested MCP calls ordered; non-MCP tools still run in parallel.
            if name.startswith("mcp__"):
                with self._mcp_lock:
                    return self.registry.call(ctx, name, args, call_key=self._call_key())
            return self.registry.call(ctx, name, args, call_key=self._call_key())
        finally:
            if con is not None:
                with contextlib.suppress(Exception):
                    con.close()

    def run(self, code: str) -> dict:
        options, option_error = _parse_options(code)
        if option_error:
            return {"status": "error", "error": option_error, "text": f"Script failed\n{option_error}"}
        timeout_ms = int(options["timeout_ms"])
        max_tokens = int(options["max_output_tokens"])
        metadata = _tool_meta(self.registry)
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "fwcore.codemode_worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, start_new_session=True,
                cwd=self.ctx.root,
                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            )
        except OSError as e:
            return {"status": "error", "error": f"cannot start codemode worker: {e}",
                    "text": "Script failed\nworker unavailable"}
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        stdin_stream = cast(TextIO, proc.stdin)
        stderr_stream = cast(TextIO, proc.stderr)
        stderr_tail: deque[str] = deque(maxlen=40)

        def drain_stderr() -> None:
            try:
                for line in stderr_stream:
                    stderr_tail.append(line.rstrip())
            except (OSError, ValueError):
                pass

        threading.Thread(target=drain_stderr, daemon=True, name="codemode-stderr").start()
        incoming_worker: queue.Queue = queue.Queue()
        stdout_stream = proc.stdout
        assert stdout_stream is not None

        def read_worker_stdout() -> None:
            try:
                for line in stdout_stream:
                    incoming_worker.put(line)
            except (OSError, ValueError):
                return

        threading.Thread(target=read_worker_stdout, daemon=True,
                         name="codemode-stdout").start()
        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="codemode-tool")
        done_queue: deque[tuple[Any, dict | None]] = deque()
        calls = 0
        status = "ok"

        def submit(msg: dict) -> None:
            nonlocal calls
            calls += 1
            name = str(msg.get("name") or "")
            args = msg.get("args")
            if not isinstance(args, dict):
                args = {}
            fut = executor.submit(self._execute_tool, name, args)
            fut.add_done_callback(lambda f: done_queue.append((f, msg)))

        def send_result(msg: dict, out: dict | None, fail: str | None = None) -> None:
            payload = {"type": "result", "id": msg.get("id"), "ok": fail is None}
            if fail is None:
                payload["value"] = out
            else:
                payload["error"] = fail
            with self._lock, contextlib.suppress(OSError, ValueError):
                stdin_stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                stdin_stream.flush()

        worker_result: dict | None = None
        deadline = started + timeout_ms / 1000.0
        try:
            with self._lock:
                stdin_stream.write(json.dumps({
                    "type": "init", "tools": metadata, "max_output_tokens": max_tokens,
                }, ensure_ascii=False) + "\n")
                stdin_stream.write(json.dumps({"type": "run", "code": code},
                                              ensure_ascii=False) + "\n")
                stdin_stream.flush()
            while True:
                now = time.monotonic()
                if _cancelled(self.ctx):
                    status = "cancelled"
                    break
                if now >= deadline:
                    status = "timeout"
                    break
                while done_queue:
                    fut, msg = done_queue.popleft()
                    if msg is None:
                        continue
                    try:
                        out = fut.result()
                    except Exception as e:  # noqa: BLE001 - tool failure is script-visible
                        send_result(msg, None, f"{type(e).__name__}: {e}")
                        continue
                    if str(out.get("status") or "ok") == "ok":
                        send_result(msg, out)
                    else:
                        why = out.get("error") or out.get("note") or out.get("status")
                        send_result(msg, None, f"{out.get('status')}: {why}")
                while True:
                    try:
                        line = incoming_worker.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("type") == "call":
                        submit(msg)
                    elif msg.get("type") == "done":
                        worker_result = msg
                        break
                if worker_result is not None:
                    break
                if proc.poll() is not None and incoming_worker.empty():
                    break
                time.sleep(0.005)
        finally:
            with contextlib.suppress(OSError):
                proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=0.5)
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=0.5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                with contextlib.suppress(OSError):
                    stream.close()
            executor.shutdown(wait=False, cancel_futures=True)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if worker_result is None:
            err = stderr_tail[-1] if stderr_tail else ""
            if status == "timeout":
                text = f"Script failed in {elapsed_ms / 1000:.2f}s\nScript error:\ntimeout"
                return {"status": "timeout", "error": "codemode timeout", "text": text,
                        "tool_calls": calls, "elapsed_ms": elapsed_ms}
            if status == "cancelled":
                return {"status": "cancelled", "error": "codemode cancelled",
                        "text": "Script cancelled", "tool_calls": calls,
                        "elapsed_ms": elapsed_ms}
            return {"status": "error", "error": f"codemode worker exited: {err}"[:5000],
                    "text": f"Script failed in {elapsed_ms / 1000:.2f}s\nScript error:\n{err}",
                    "tool_calls": calls, "elapsed_ms": elapsed_ms}
        result = _format_result(worker_result, elapsed_ms, max_tokens, self.ctx.root)
        result["tool_calls"] = calls
        result["script_exit"] = bool(worker_result.get("exit"))
        return result


def register_codemode(registry) -> None:
    from .tools import (  # local import avoids tools <-> codemode cycle
        ToolContext,
        ToolSpec,
    )

    def _handler(ctx: ToolContext, args: dict) -> dict:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"status": "error", "error": "code must be non-empty JavaScript"}
        return CodemodeExecutor(registry, ctx).run(code)

    registry.register(ToolSpec(
        CODEMODE_TOOL_NAME,
        "Run JavaScript that calls tools and returns only explicit output. "
        "Use tools.<name>(args), searchTools(), describeTool(), ALL_TOOLS, text(), "
        "image(), console, and top-level await/return. The script is sandboxed "
        "with no filesystem, network, timers, or Node APIs.",
        {"type": "object", "properties": {
            "code": {"type": "string", "description": "Raw JavaScript source; no code fence."},
         }, "required": ["code"]},
        _handler,
        risk="low",
        exposure="direct",
    ))
