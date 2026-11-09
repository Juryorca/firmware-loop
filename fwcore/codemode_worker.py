"""QuickJS sandbox worker for the ``codemode`` tool.

This is intentionally a small process boundary, not a second Agent or tool
executor.  The worker owns only the JavaScript VM; every real tool call is sent
back to the parent, which executes it through the existing fwcore Registry and
ToolContext (including policy, recording, and persistence).

Protocol (one JSON object per line):
  parent -> worker: {"type":"init", "tools":[...], "max_output_tokens":N}
                    {"type":"run", "code":"..."}
                    {"type":"result", "id":N, "ok":bool, "value":..., "error":...}
  worker -> parent: {"type":"call", "id":N, "name":"...", "args":{...}}
                    {"type":"done", "outputs":[...], "console":[...],
                     "error":null|"...", "exit":bool}
"""

from __future__ import annotations

import contextlib
import importlib
import json
import queue
import sys
import threading
import time


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _read_line(stream) -> dict:
    line = stream.readline()
    if not line:
        raise EOFError("codemode parent closed stdin")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise EOFError(f"invalid codemode protocol line: {e}") from e
    if not isinstance(data, dict):
        raise TypeError("codemode protocol line must be an object")
    return data


def _text_value(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _run(code: str, tools: list[dict], max_output_tokens: int) -> dict:
    try:
        quickjs = importlib.import_module("quickjs")
    except ImportError as e:
        return {"outputs": [], "console": [], "error": f"quickjs unavailable: {e}", "exit": False}

    # The JavaScript-side result holder.  The parent supplies the code only
    # after this setup, so the sandbox has no file/network/timer globals.
    ctx = quickjs.Context()
    ctx.set_memory_limit(256 * 1024 * 1024)
    incoming: queue.Queue = queue.Queue()
    meta_json = json.dumps(tools, ensure_ascii=False, default=str)

    def enqueue(call_id, name, args_json):
        try:
            args = json.loads(args_json) if isinstance(args_json, str) else {}
        except json.JSONDecodeError:
            args = {}
        _send({"type": "call", "id": int(call_id), "name": str(name), "args": args})

    def search(query, limit, namespace=None):
        try:
            from .tool_search import search_metadata
            candidates = tools
            if namespace:
                candidates = [t for t in tools if t.get("namespace") == namespace]
            matches = search_metadata(candidates, str(query),
                                      max(1, min(100, int(limit))))
        except Exception:  # noqa: BLE001 - search failure must not kill the VM
            matches = []
        return json.dumps([
            {"name": m.get("name", ""), "description": m.get("description", "")}
            for m in matches
        ], ensure_ascii=False)

    ctx.add_callable("__enqueue", enqueue)
    ctx.add_callable("__search", search)

    bootstrap = r"""
const __meta = __META__;
globalThis.ALL_TOOLS = __meta.map(t => ({name: t.name, description: t.description}));
globalThis.__pending = new Map();
globalThis.__next = 0;
globalThis.__done = false;
globalThis.__error = null;
globalThis.__exit = false;
globalThis.__outputs = [];
globalThis.__console = [];
globalThis.__text = function(v) {
  __outputs.push({type: "text", text: (typeof v === "string" ? v : (() => {
    try { return JSON.stringify(v); } catch (_) { return String(v); }
  })())});
};
globalThis.text = __text;
globalThis.image = function(v) {
  let item = v;
  if (typeof item === "string" && item.startsWith("data:")) {
    const comma = item.indexOf(",");
    if (comma < 0) throw new Error("image() got an invalid data URL");
    const header = item.slice(5, comma).split(";")[0] || "image/png";
    item = {type: "image", data: item.slice(comma + 1), mimeType: header};
  } else if (item && item.image_url) {
    item = {type: "image", data: item.image_url.data || item.image_url, mimeType: item.image_url.mimeType || "image/png"};
  }
  if (!item || item.type !== "image") throw new Error("image() expects an image block or data URL");
  __outputs.push({...item, type: "image"});
};
globalThis.__console_push = function(args) {
  __console.push(Array.from(args).map(v => {
    if (typeof v === "string") return v;
    try { return JSON.stringify(v); } catch (_) { return String(v); }
  }).join(" "));
};
globalThis.console = {
  log: (...a) => __console_push(a), info: (...a) => __console_push(a),
  warn: (...a) => __console_push(a), error: (...a) => __console_push(a),
  debug: (...a) => __console_push(a)
};
globalThis.exit = function() { throw {__codemode_exit: true}; };
globalThis.tools = Object.create(null);
for (const t of __meta) {
  const fn = function(args) {
    return new Promise((resolve, reject) => {
      const id = ++__next;
      __pending.set(id, {resolve, reject});
      __enqueue(id, t.name, JSON.stringify(args === undefined ? {} : args));
    });
  };
  tools[t.id] = fn;
  if (!(t.name in tools)) tools[t.name] = fn;
}
globalThis.searchTools = function(query, opts) {
  const limit = opts && opts.limit ? opts.limit : 8;
  const namespace = opts && opts.namespace ? opts.namespace : null;
  return JSON.parse(__search(String(query || ""), limit, namespace));
};
globalThis.describeTool = function(name) {
  const t = __meta.find(x => x.name === name || x.id === name);
  if (!t) return undefined;
  return {name: t.name, description: t.description, inputSchema: t.input_schema,
          namespace: t.namespace === null ? undefined : t.namespace};
};
globalThis.describeNamespace = function(name) {
  const selected = __meta.filter(t => t.namespace === name);
  if (selected.length === 0) return undefined;
  return {name: name, tools: selected.map(t => ({name: t.name, description: t.description}))};
};
globalThis.__settle = function(id, ok, payload) {
  const p = __pending.get(id);
  if (!p) return;
  __pending.delete(id);
  if (ok) {
    try { p.resolve(JSON.parse(payload)); } catch (e) { p.reject(e); }
  } else {
    p.reject(new Error(String(payload)));
  }
};
globalThis.__run = function(code) {
  try {
    const AF = Object.getPrototypeOf(async function() {}).constructor;
    const fn = new AF(code);
    Promise.resolve(fn()).then(v => {
      if (v !== undefined) globalThis.text(v);
      __done = true;
    }, e => {
      if (e && e.__codemode_exit) { __exit = true; __done = true; }
      else { __error = String(e && (e.name + ": " + e.message + "\n" + e.stack) || e); __done = true; }
    });
  } catch (e) {
    __error = String(e && (e.name + ": " + e.message + "\n" + e.stack) || e);
    __done = true;
  }
};
""".replace("__META__", meta_json)
    ctx.eval(bootstrap)

    def drive() -> dict:
        try:
            ctx.eval("__run(" + json.dumps(code) + ")")
            while not ctx.eval("__done"):
                while True:
                    try:
                        msg = incoming.get_nowait()
                    except queue.Empty:
                        break
                    with contextlib.suppress(Exception):
                        ctx.eval("__settle(" + json.dumps(msg.get("id")) + ", " +
                                 json.dumps(bool(msg.get("ok"))) + ", " +
                                 json.dumps(json.dumps(msg.get("value"), ensure_ascii=False,
                                                       default=str)
                                            if msg.get("ok") else str(msg.get("error") or "")) + ")")
                for _ in range(20):
                    if not ctx.execute_pending_job():
                        break
                time.sleep(0.003)
            return {
                "outputs": json.loads(ctx.eval("JSON.stringify(__outputs)")),
                "console": json.loads(ctx.eval("JSON.stringify(__console)")),
                "error": ctx.eval("__error"),
                "exit": bool(ctx.eval("__exit")),
            }
        except Exception as e:  # noqa: BLE001 - protocol boundary reports VM failures
            return {"outputs": [], "console": [], "error": f"{type(e).__name__}: {e}", "exit": False}

    def reader() -> None:
        try:
            for line in sys.stdin:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "result":
                    incoming.put(msg)
        except (OSError, ValueError):
            return

    threading.Thread(target=reader, daemon=True, name="codemode-stdin").start()
    return drive()


def main() -> int:
    try:
        init = _read_line(sys.stdin)
        if init.get("type") != "init":
            _send({"type": "done", "outputs": [], "console": [],
                   "error": "expected init", "exit": False})
            return 2
        run_msg = _read_line(sys.stdin)
        if run_msg.get("type") != "run":
            _send({"type": "done", "outputs": [], "console": [],
                   "error": "expected run", "exit": False})
            return 2
        tools = init.get("tools") or []
        result = _run(run_msg.get("code") or "", tools, int(init.get("max_output_tokens") or 10000))
        _send({"type": "done", **result})
        return 0
    except (EOFError, OSError, ValueError) as e:
        with contextlib.suppress(OSError):
            _send({"type": "done", "outputs": [], "console": [],
                   "error": f"{type(e).__name__}: {e}", "exit": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
