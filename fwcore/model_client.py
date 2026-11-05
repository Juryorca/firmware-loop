"""模型客户端:严格遵守 OpenAI 兼容的 tool-call 协议。

为什么单独有这一层
- 旧实现把 assistant 的 `tool_calls` 丢掉,只保留 content ⇒ 真实 API 会在
  "tool 消息引用了不存在的 tool_call_id" 上报 400。脚本化模型不会暴露这个问题,
  所以这里把"请求体"作为可注入的传输层,便于测试断言线上格式。
- 传输层可注入(transport),测试用假传输捕获 payload;真实用 urllib。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol


def _emit_reasoning() -> bool:
    """每次都读环境变量(测试/脚本会在 import 之后才设)。"""
    return os.environ.get("FW_REASONING_CONTENT") == "1"


class Transport(Protocol):
    def __call__(self, url: str, headers: dict, payload: dict) -> dict: ...


class ModelHTTPError(RuntimeError):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body[:800]}")
        self.payload: dict | None = None      # 由 chat() 填入,便于排查"我们发了什么"

    @property
    def status(self) -> int:
        return int(str(self).split()[1].rstrip(":"))


ALLOWED_SCHEMES = ("http://", "https://")


def _check_url(url: str) -> None:
    """只允许 http(s):防止配置被改成 file:// 之类把本地文件读进来(advisory 的真实面)。"""
    if not url.startswith(ALLOWED_SCHEMES):
        raise ModelHTTPError(0, f"拒绝非 http(s) 端点: {url[:80]}")


def urllib_transport(url: str, headers: dict, payload: dict) -> dict:
    _check_url(url)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:   # 把服务端原文带出来(排查协议问题必须有)
        raise ModelHTTPError(e.code, e.read().decode("utf-8", errors="replace")) from e


def to_api_messages(messages: list[dict]) -> list[dict]:
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
            out.append({"role": "tool", "tool_call_id": tcid,
                        "content": m.get("content") if isinstance(m.get("content"), str)
                        else json.dumps(m.get("content"), ensure_ascii=False)})
        elif role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        else:
            raise ValueError(f"非法消息 role: {role}")
    if pending:
        raise ValueError(f"非法历史:assistant 声明了 tool_calls 但没有对应 tool 结果: {sorted(pending)}")
    return out


@dataclass
class OpenAICompatModel:
    """OpenAI 兼容 chat completions(默认指向可配置 base_url)。"""

    model: str
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    api_key_env: str = "FW_API_KEY"
    temperature: float = 0.2
    transport: Transport = urllib_transport
    last_request: dict | None = field(default=None, repr=False)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"缺少 API key(环境变量 {self.api_key_env})")
        payload: dict = {"model": self.model, "messages": to_api_messages(messages),
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
                "reasoning_content": msg.get("reasoning_content") or ""}


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
