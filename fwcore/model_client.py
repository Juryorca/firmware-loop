"""模型客户端:严格遵守 OpenAI 兼容的 tool-call 协议。

为什么单独有这一层
- 旧实现把 assistant 的 `tool_calls` 丢掉,只保留 content ⇒ 真实 API 会在
  "tool 消息引用了不存在的 tool_call_id" 上报 400。脚本化模型不会暴露这个问题,
  所以这里把"请求体"作为可注入的传输层,便于测试断言线上格式。
- 传输层可注入(transport),测试用假传输捕获 payload;真实用 urllib。
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from .mcp_content import image_data_uri
from .streaming import StreamCancelled, StreamControl, StreamError, StreamEvent


def _emit_reasoning() -> bool:
    """thinking 模式下**每个** assistant 消息都必须回传 `reasoning_content`(空串也要)。

    默认就补(这是 DeepSeek thinking 的协议要求);只有显式 `FW_REASONING_CONTENT=0/off`
    才关掉(给不接受该字段的网关用)。之前是"默认不补",换个进程(env 没继承)就会 400。
    """
    return (os.environ.get("FW_REASONING_CONTENT") or "1").lower() not in ("0", "off", "false")


class Transport(Protocol):
    def __call__(self, url: str, headers: dict, payload: dict) -> dict:
        raise NotImplementedError


class ModelHTTPError(RuntimeError):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body[:800]}")
        self.payload: dict | None = None      # 由 chat() 填入,便于排查"我们发了什么"

    @property
    def status(self) -> int:
        return int(str(self).split()[1].rstrip(":"))


ALLOWED_SCHEMES = ("http://", "https://")


def _check_url(url: str) -> None:
    """只允许 http(s) 且必须有 host:防止配置被改成 file:// 读本地文件 / 空 host 触发 AttributeError。"""
    if not url.startswith(ALLOWED_SCHEMES):
        raise ModelHTTPError(0, f"拒绝非 http(s) 端点: {url[:80]}")
    if not urlsplit(url).hostname:
        raise ModelHTTPError(0, f"端点缺少 host: {url[:80]}")


def http_transport(url: str, headers: dict, payload: dict) -> dict:
    """POST JSON 到 http(s) 端点;只用 `http.client`(不接受 URL 被当成任意 scheme 读取)。

    `_check_url` 先做 scheme 白名单;连接/超时/HTTP 错误都转成 `ModelHTTPError`。
    """
    _check_url(url)
    u = urlsplit(url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    host = u.hostname
    if not host:
        raise ModelHTTPError(0, f"端点缺少 host: {url[:80]}")
    try:
        port = u.port
    except ValueError as e:
        raise ModelHTTPError(0, f"端点端口非法: {e}") from e
    conn = cls(host, port, timeout=180)
    try:
        conn.request("POST", path, body=json.dumps(payload).encode(), headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:                 # 服务端原文要带出来(排查协议问题必须有)
            raise ModelHTTPError(resp.status, body.decode("utf-8", errors="replace"))
        return json.loads(body or b"{}")
    except OSError as e:
        raise ModelHTTPError(0, f"连接失败: {str(e)[:200]}") from e
    finally:
        conn.close()


# 兼容旧名(脚本/测试可能还在 import)
urllib_transport = http_transport


def list_models(base_url: str, api_key: str = "", *, timeout: float = 10.0) -> list[dict]:
    """GET an OpenAI-compatible ``/models`` catalog.

    This is a read-only discovery call.  It deliberately does not reuse
    ``http_transport``: that POSTs a model request payload and would be the
    wrong contract for model discovery.
    """
    url = base_url.rstrip("/") + "/models"
    _check_url(url)
    u = urlsplit(url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    if not u.hostname:
        raise ModelHTTPError(0, f"端点缺少 host: {url[:80]}")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    conn = cls(u.hostname, u.port, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise ModelHTTPError(resp.status, body.decode("utf-8", errors="replace"))
        data = json.loads(body or b"{}")
    except OSError as e:
        raise ModelHTTPError(0, f"连接失败: {str(e)[:200]}") from e
    finally:
        conn.close()
    if isinstance(data, dict):
        rows = data.get("data")
        if rows is None:
            rows = data.get("models")
    else:
        rows = data
    if not isinstance(rows, list):
        raise ModelHTTPError(0, "模型列表响应不是数组")
    return [x for x in rows if isinstance(x, dict)]


def _tool_text_and_images(m: dict) -> tuple[str, list[dict]]:
    raw_parts = m.get("parts")
    parts: list[dict] = raw_parts if isinstance(raw_parts, list) else []
    text_parts = [str(p.get("text") or "") for p in parts
                  if isinstance(p, dict) and p.get("type") == "text"]
    text = "\n".join(text_parts).strip()
    if not text:
        content = m.get("content")
        text = content if isinstance(content, str) else json.dumps(content or {}, ensure_ascii=False)
    images = [p for p in parts if isinstance(p, dict) and p.get("type") == "image"]
    return text, images


def to_api_messages(messages: list[dict], *, supports_vision: bool = False,
                    image_mode: str = "none") -> list[dict]:
    """内部历史 → API 消息。

    内部历史一律用 API 形状(assistant 带 tool_calls;tool 带 tool_call_id),
    这里做一次**严格校验**:每个 tool 消息必须有前一条 assistant 声明的 id。
    这样可以尽早发现"history 拼错"的 bug,而不是等线上 400。
    """
    out: list[dict] = []
    pending: set[str] = set()
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            # thinking 模式要求**每个** assistant 消息都带 reasoning_content;
            # 老 checkpoint(开启该模式之前存的)没有这个字段 ⇒ 补空串回传,而不是整条丢弃
            if _emit_reasoning() and "reasoning_content" not in m:
                msg["reasoning_content"] = ""
            if "reasoning_content" in m:      # 空串也要保留:thinking 模式要求"每个" assistant 都带
                msg["reasoning_content"] = m["reasoning_content"] or ""
            calls = m.get("tool_calls") or []
            if calls:
                norm = []
                for c in calls:
                    fn = c.get("function") or {}
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args or {}, ensure_ascii=False)
                    norm.append({"id": c.get("id"), "type": "function",
                                 "function": {"name": fn.get("name"), "arguments": args}})
                    pending.add(c.get("id"))
                msg["tool_calls"] = norm
            out.append(msg)
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if tcid not in pending:
                raise ValueError(f"非法历史:tool 消息的 tool_call_id={tcid} 没有对应 assistant.tool_calls")
            pending.discard(tcid)
            text, images = _tool_text_and_images(m)
            images_sent = bool(images and supports_vision and image_mode == "tool_parts")
            if images and not images_sent:
                paths = ", ".join(str(p.get("path") or "?") for p in images)
                why = ("text-only model" if not supports_vision
                       else f"model image mode {image_mode!r} does not carry tool images")
                text = (text + f"\n\n[Images returned by this tool are not sent to the "
                               f"configured {why}; saved files: {paths}]").strip()
            if images_sent:
                blocks: list[dict] = []
                raw_parts = m.get("parts")
                parts_list: list[dict] = raw_parts if isinstance(raw_parts, list) else []
                for part in parts_list:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": str(part.get("text") or "")})
                    elif part.get("type") == "image":
                        uri = image_data_uri(part)
                        if uri:
                            blocks.append({"type": "image_url", "image_url": {"url": uri}})
                        else:
                            blocks.append({"type": "text",
                                           "text": f"[image unavailable: {part.get('path') or 'missing'}]"})
                if not any(b.get("type") == "text" for b in blocks):
                    blocks.insert(0, {"type": "text",
                                      "text": text or "(see attached image)"})
                out.append({"role": "tool", "tool_call_id": tcid, "content": blocks})
            else:
                out.append({"role": "tool", "tool_call_id": tcid, "content": text})
        elif role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        else:
            raise ValueError(f"非法消息 role: {role}")
    if pending:
        raise ValueError(f"非法历史:assistant 声明了 tool_calls 但没有对应 tool 结果: {sorted(pending)}")
    return out


def to_responses_input(messages: list[dict], *, supports_vision: bool = False) -> list[dict]:
    """内部历史 → OpenAI Responses input items。

    Responses 的 function_call_output.output 是合法的 text/image 数组；因此视觉模型
    不需要伪造 user 消息，也不需要把 image_url 塞进 Chat Completions 的 tool role。
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "user"):
            text = m.get("content")
            if isinstance(text, str) and text:
                out.append({"role": role, "content": text})
        elif role == "assistant":
            text = m.get("content")
            if isinstance(text, str) and text:
                out.append({"role": "assistant",
                            "content": [{"type": "output_text", "text": text}]})
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args or {}, ensure_ascii=False)
                out.append({"type": "function_call", "call_id": c.get("id"),
                            "name": fn.get("name"), "arguments": args})
        elif role == "tool":
            text, images = _tool_text_and_images(m)
            if images and supports_vision:
                output: object = []
                raw_parts = m.get("parts")
                parts_list: list[dict] = raw_parts if isinstance(raw_parts, list) else []
                for part in parts_list:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        output.append({"type": "input_text", "text": str(part.get("text") or "")})
                    elif part.get("type") == "image":
                        uri = image_data_uri(part)
                        if uri:
                            output.append({"type": "input_image", "detail": "auto",
                                           "image_url": uri})
                        else:
                            output.append({"type": "input_text",
                                           "text": f"[image unavailable: {part.get('path') or 'missing'}]"})
                if not output:
                    output = "(see attached image)"
                elif isinstance(output, list) and not any(
                        isinstance(x, dict) and x.get("type") == "input_text" for x in output):
                    output.insert(0, {"type": "input_text",
                                      "text": text or "(see attached image)"})
            else:
                if images:
                    paths = ", ".join(str(p.get("path") or "?") for p in images)
                    text = (text + f"\n\n[Images returned by this tool are not sent to the "
                                   f"configured text-only model; saved files: {paths}]").strip()
                output = text or "(no tool output)"
            out.append({"type": "function_call_output",
                        "call_id": m.get("tool_call_id"), "output": output})
        else:
            raise ValueError(f"非法消息 role: {role}")
    return out


@dataclass
class OpenAIResponsesModel:
    """OpenAI Responses API（非流式），用于合法的工具结果图片输入。"""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = "FW_API_KEY"
    temperature: float = 0.2
    transport: Transport = http_transport
    supports_vision: bool = False
    last_request: dict | None = field(default=None, repr=False)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"缺少 API key(环境变量 {self.api_key_env})")
        payload: dict = {"model": self.model,
                         "input": to_responses_input(messages, supports_vision=self.supports_vision),
                         "temperature": self.temperature}
        if tools:
            payload["tools"] = [{"type": "function", "name": t["name"],
                                 "description": t.get("description", ""),
                                 "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
                                for t in tools]
            payload["tool_choice"] = "auto"
        self.last_request = payload
        url = self.base_url.rstrip("/") + "/responses"
        _check_url(url)
        data = self.transport(url, {"Content-Type": "application/json",
                                    "Authorization": f"Bearer {key}"}, payload)
        text_parts: list[str] = []
        calls: list[dict] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text_parts.append(str(part.get("text") or ""))
            elif item.get("type") == "function_call":
                raw = item.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except json.JSONDecodeError:
                    args = {"__invalid_json__": raw}
                calls.append({"id": item.get("call_id"), "type": "function",
                              "function": {"name": item.get("name"),
                                           "arguments": raw if isinstance(raw, str)
                                           else json.dumps(raw, ensure_ascii=False)},
                              "_args": args})
        return {"content": "".join(text_parts), "tool_calls": calls,
                "finish_reason": data.get("status") or "",
                "usage": data.get("usage") or {}}


@dataclass
class OpenAICompatModel:
    """OpenAI 兼容 chat completions(默认指向可配置 base_url)。"""

    model: str
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    api_key_env: str = "FW_API_KEY"
    temperature: float = 0.2
    transport: Transport = http_transport
    # 流式传输可注入(测试用 Mock SSE);默认走下面的 http SSE 实现
    stream_transport: Callable | None = None
    last_request: dict | None = field(default=None, repr=False)
    supports_vision: bool = False
    image_mode: str = "none"

    def _payload(self, messages: list[dict], tools: list[dict], *, stream: bool) -> dict:
        payload: dict = {"model": self.model,
                         "messages": to_api_messages(
                             messages, supports_vision=self.supports_vision,
                             image_mode=self.image_mode),
                         "temperature": self.temperature}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                }} for t in tools
            ]
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def stream(self, messages: list[dict], tools: list[dict], *,
               control: StreamControl | None = None) -> Iterable[StreamEvent]:
        """真正的 SSE 增量流;`control.cancel()` 会关闭底层连接。"""
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"缺少 API key(环境变量 {self.api_key_env})")
        payload = self._payload(messages, tools, stream=True)
        self.last_request = payload
        url = self.base_url.rstrip("/") + "/chat/completions"
        _check_url(url)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if self.stream_transport is not None:
            lines = self.stream_transport(url, headers, payload)
            closer = getattr(lines, "close", None)
            if control is not None and callable(closer):
                control.bind(closer)
            yield from _parse_sse(lines, control)
            return
        u = urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        host = u.hostname
        if not host:
            raise ModelHTTPError(0, f"端点缺少 host: {url[:80]}")
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        conn = cls(host, u.port, timeout=120)
        resp = None
        sock = None
        try:
            conn.request("POST", path, body=json.dumps(payload).encode(), headers=headers)
            resp = conn.getresponse()
            if control is not None:
                # getresponse() 可能把 socket 从 conn 移到 resp.fp.raw._sock
                sock = conn.sock
                if sock is None:
                    raw = getattr(getattr(resp, "fp", None), "raw", None)
                    sock = getattr(raw, "_sock", None)
                # 不要给流式 socket 设 0.5s 读超时:HTTPResponse 在超时后
                # 会进入不可恢复状态,下一次 readline 会抛
                # OSError("cannot read from timed out object"),把正常的
                # 慢 SSE(例如 reasoning 间隔 >0.5s)误判成断网。
                # 取消由 shutdown 打断阻塞 read;正常读取沿用连接超时。

                def _abort() -> None:
                    # 只 shutdown,不在这里 close:readline 持有 file 锁时
                    # resp.close() 会阻塞取消线程(实测 9.5s)。shutdown 足够唤醒
                    # 阻塞的 recv,真正的 close 由读取线程的 finally 完成。
                    if sock is not None:
                        with contextlib.suppress(Exception):
                            sock.shutdown(socket.SHUT_RDWR)

                control.bind(_abort)
            if resp.status >= 400:
                body = resp.read()
                raise ModelHTTPError(resp.status, body.decode("utf-8", errors="replace"))
            yield from _parse_sse(resp, control)
        except OSError as e:
            if control is not None and control.cancelled:
                raise StreamCancelled("请求已取消") from e
            raise ModelHTTPError(0, f"连接失败: {str(e)[:200]}") from e
        except Exception as e:
            if control is not None and control.cancelled:
                raise StreamCancelled("请求已取消") from e
            if isinstance(e, StreamError):
                raise
            raise StreamError(f"{type(e).__name__}: {e}") from e
        finally:
            if resp is not None:
                with contextlib.suppress(Exception):
                    resp.close()
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close()
            with contextlib.suppress(Exception):
                conn.close()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"缺少 API key(环境变量 {self.api_key_env})")
        payload = self._payload(messages, tools, stream=False)
        self.last_request = payload
        _check_url(self.base_url.rstrip("/") + "/chat/completions")
        try:
            data = self.transport(self.base_url.rstrip("/") + "/chat/completions",
                              {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                                  payload)
        except ModelHTTPError as e:
            e.payload = payload      # 排查协议问题时需要看到"我们到底发了什么"
            raise
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {"__invalid_json__": raw}
            calls.append({"id": tc.get("id") or f"call_{len(calls)}", "type": "function",
                          "function": {"name": fn.get("name"), "arguments":
                                       raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)},
                          "_args": args})
        return {"content": msg.get("content") or "", "tool_calls": calls,
                "reasoning_content": msg.get("reasoning_content") or "",
                # 诊断用:截断/长度限制等异常结束必须能和"普通空转"区分开
                "finish_reason": ((data.get("choices") or [{}])[0].get("finish_reason") or ""),
                "usage": data.get("usage") or {}}


def assistant_message(content: str, calls: list[dict], reasoning: str = "") -> dict:
    """把模型返回的 tool_calls 规范成"可以回填进历史"的 assistant 消息。

    `reasoning_content` 是 DeepSeek thinking 模式的**必需回传字段**(不回传 ⇒ 400),
    这类 provider 私有字段必须原样往返,不能在我们的规范化里丢掉。
    """
    base: dict[str, object] = {"role": "assistant", "content": content}
    # thinking 模式(如 DeepSeek reasoner/flash)要求**每个** assistant 消息都回传该字段;
    # 非 thinking provider 多带未知字段会被拒 ⇒ 用 FW_REASONING_CONTENT=1 显式开启。
    if reasoning or _emit_reasoning():
        base["reasoning_content"] = reasoning or ""
    if not calls:
        return base
    norm = []
    for c in calls:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(c.get("_args") or args or {}, ensure_ascii=False)
        norm.append({"id": c.get("id"), "type": "function",
                     "function": {"name": fn.get("name"), "arguments": args}})
    base["tool_calls"] = norm
    return base


def _parse_sse(lines: Iterable, control: StreamControl | None) -> Iterable[StreamEvent]:
    """把 OpenAI 兼容 SSE 行解析成 StreamEvent。

    只做协议解析:不执行工具、不写 DB;`done` 之后由 Runner 组装并校验参数。
    """
    started = False
    finish = ""
    usage: dict = {}
    seen_calls: set[int] = set()
    saw_done = False
    iterator = iter(lines)
    while True:
        try:
            raw = next(iterator)
        except StopIteration:
            break
        except TimeoutError:
            if control is not None:
                control.raise_if_cancelled()
            continue
        if control is not None:
            control.raise_if_cancelled()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as e:
            raise StreamError(f"SSE JSON 解析失败: {e}: {data[:200]}") from e
        usage = obj.get("usage") or usage
        choices = obj.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        if not started:
            started = True
            yield StreamEvent("start")
        reasoning = delta.get("reasoning_content")
        if reasoning:
            yield StreamEvent("reasoning_delta", text=reasoning)
        content = delta.get("content")
        if content:
            yield StreamEvent("text_delta", text=content)
        for tc in delta.get("tool_calls") or []:
            try:
                idx = int(tc.get("index") or 0)
            except (TypeError, ValueError):
                idx = 0
            fn = tc.get("function") or {}
            if idx not in seen_calls and (tc.get("id") or fn.get("name")):
                seen_calls.add(idx)
                yield StreamEvent("toolcall_start", index=idx,
                                  call_id=tc.get("id") or "", name=fn.get("name") or "")
            args = fn.get("arguments")
            if args:
                yield StreamEvent("toolcall_delta", index=idx, arguments=args)
        if choice.get("finish_reason"):
            finish = choice["finish_reason"]
    if not started:
        if control is not None and control.cancelled:
            raise StreamCancelled("请求已取消")
        raise StreamError("SSE 响应为空(没有任何 assistant 事件)")
    # 合法终止:收到 [DONE],或服务端给出 finish_reason。
    # 两者都没有 ⇒ 连接被截断/异常 EOF,绝不能当成正常完成。
    if not (saw_done or finish):
        if control is not None and control.cancelled:
            raise StreamCancelled("请求已取消")
        raise StreamError("SSE 响应异常结束:缺少 [DONE] 或 finish_reason(响应可能被截断)")
    yield StreamEvent("done", finish_reason=finish, usage=usage)
