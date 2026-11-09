"""模型适配层:唯一的模型接口(CLI、测试、E2E 全部走这里)。

为什么要统一
- 之前 `runtime.OpenAIModel`(手写 urllib)与 `model_client.OpenAICompatModel` 并存 ⇒ 两套协议实现会漂,
  正式入口与真实 E2E 走的不是同一条路。
- 现在:`Adapter.chat()` 返回统一的 `Reply`;工具调用形状、provider 私有字段、JSON 解析都在这一层做。

Provider 语义(真实差异,不能"统一掉")
- OpenAI 兼容端点:工具名需 `^[A-Za-z0-9_-]+$`;`tool_calls[].function.arguments` 是 **JSON 字符串**。
- thinking 模式(DeepSeek reasoner/flash):**每个** assistant 消息都要回传 `reasoning_content`(空串也要)
  ⇒ 由 adapter 的 `echo_reasoning` 决定,历史序列化时按需补齐。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

from .model_client import (
    ModelHTTPError,
    OpenAICompatModel,
    OpenAIResponsesModel,
    assistant_message,
    to_api_messages,
)

__all__ = [
    "Adapter",
    "ModelHTTPError",
    "OpenAICompatAdapter",
    "OpenAIResponsesAdapter",
    "Reply",
    "ScriptedAdapter",
    "assistant_message",
    "build_adapter",
    "to_api_messages",
]


@dataclass
class Reply:
    content: str = ""
    finish_reason: str = ""      # stop / length / tool_calls / content_filter …
    usage: dict = field(default_factory=dict)     # token 统计(服务端给就给)
    tool_calls: list[dict] = field(default_factory=list)   # [{id, name, args(dict), args_raw(str)}]
    reasoning: str = ""

    def assistant_message(self) -> dict:
        calls = [{"id": c["id"], "type": "function",
                  "function": {"name": c["name"], "arguments": c.get("args_raw") or json.dumps(c["args"] or {},
                                                                                              ensure_ascii=False)}}
                 for c in self.tool_calls]
        return assistant_message(self.content, calls, self.reasoning)


class Adapter(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply: ...


@dataclass
class ScriptedAdapter:
    """测试/离线用:按脚本回复;脚本用尽返回 FINAL。"""

    script: list[dict] = field(default_factory=list)
    name: str = "scripted"
    i: int = 0
    calls: list[dict] = field(default_factory=list)
    _seq: int = 0
    # 流式测试参数:分片大小 / 每片延迟 / 在第 N 个文本片后抛错(验证部分回复)
    chunk_size: int = 4
    stream_delay: float = 0.0
    fail_after_chunks: int | None = None

    def stream(self, messages: list[dict], tools: list[dict], *, control=None):
        """scripted 的流式实现:先走 `chat`(保留子类/测试钩子),再拆成增量事件。

        真正的网络级 SSE 由 OpenAICompatModel.stream 实现;这里保证
        ScriptedAdapter 也满足新接口,便于 TUI/取消/分片组装的自动化验证。
        """
        from .streaming import StreamError, StreamEvent

        reply = self.chat(messages, tools)
        yield StreamEvent("start")
        for emitted, part in enumerate(
                _chunks(reply.content or "", max(1, int(self.chunk_size))), start=1):
            if control is not None:
                control.raise_if_cancelled()
            if self.stream_delay:
                time.sleep(self.stream_delay)
            if self.fail_after_chunks is not None and emitted > self.fail_after_chunks:
                raise StreamError("模拟流中断")
            yield StreamEvent("text_delta", text=part)
        for part in _chunks(reply.reasoning or "", max(1, int(self.chunk_size))):
            if control is not None:
                control.raise_if_cancelled()
            if self.stream_delay:
                time.sleep(self.stream_delay)
            yield StreamEvent("reasoning_delta", text=part)
        for idx, call in enumerate(reply.tool_calls):
            raw = call.get("args_raw")
            if not isinstance(raw, str):
                raw = json.dumps(call.get("args") or {}, ensure_ascii=False)
            yield StreamEvent("toolcall_start", index=idx,
                              call_id=call.get("id") or f"call_{idx}",
                              name=call.get("name") or "",
                              invalid=bool(call.get("args_invalid")))
            for part in _chunks(raw, max(1, int(self.chunk_size))):
                if control is not None:
                    control.raise_if_cancelled()
                if self.stream_delay:
                    time.sleep(self.stream_delay)
                yield StreamEvent("toolcall_delta", index=idx, arguments=part)
        usage = reply.usage if isinstance(reply.usage, dict) else {}
        yield StreamEvent("done", finish_reason=reply.finish_reason or
                          ("tool_calls" if reply.tool_calls else "stop"), usage=usage)

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        self.calls.append({"messages": len(messages), "tools": [t["name"] for t in tools]})
        if self.i >= len(self.script):
            return Reply(content="FINAL: script exhausted")
        raw = self.script[self.i]
        self.i += 1
        out = []
        for c in raw.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {"__invalid_json__": args}   # 保留原文,下面打标记
            invalid = isinstance(args, dict) and "__invalid_json__" in args
            self._seq += 1
            out.append({"id": c.get("id") or f"call_{self._seq}",
                        "name": c.get("name") or fn.get("name") or "",
                        "args": c.get("args") or args or {},
                        "args_raw": None,
                        "args_invalid": invalid})
        usage = raw.get("usage")
        return Reply(content=raw.get("content") or "", tool_calls=out,
                     reasoning=raw.get("reasoning_content") or "",
                     finish_reason=raw.get("finish_reason") or "",
                     usage=usage if isinstance(usage, dict) else {})


@dataclass
class OpenAICompatAdapter:
    """真实的 OpenAI 兼容端点(DeepSeek/OpenAI/兼容网关)。"""

    model: str
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    api_key_env: str = "FW_API_KEY"
    echo_reasoning: bool = field(default_factory=lambda: os.environ.get("FW_REASONING_CONTENT") == "1")
    temperature: float = 0.2
    transport: object = None
    stream_transport: object = None
    name: str = "openai-compat"
    supports_vision: bool = False
    image_mode: str = "none"

    def __post_init__(self) -> None:
        mode = self.image_mode
        kw = {"model": self.model, "base_url": self.base_url, "api_key": self.api_key,
              "api_key_env": self.api_key_env, "temperature": self.temperature,
              "supports_vision": self.supports_vision, "image_mode": mode}
        if self.transport is not None:
            kw["transport"] = self.transport
        if self.stream_transport is not None:
            kw["stream_transport"] = self.stream_transport
        self._client = OpenAICompatModel(**kw)

    @property
    def supports_stream(self) -> bool:
        # 注入自定义非流式 transport 时,沿用 chat()(测试/网关兼容);
        # 真实端点(默认 transport)或显式 stream_transport 才走 SSE。
        return self.stream_transport is not None or self.transport is None

    def stream(self, messages: list[dict], tools: list[dict], *, control=None):
        yield from self._client.stream(messages, tools, control=control)

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        r = self._client.chat(messages, tools)
        calls = []
        for c in r.get("tool_calls") or []:
            fn = c.get("function") or {}
            calls.append({"id": c.get("id"), "name": fn.get("name"),
                          "args": c.get("_args") or {}, "args_raw": fn.get("arguments")})
        return Reply(content=r.get("content") or "", tool_calls=calls,
                     reasoning=r.get("reasoning_content") or "",
                     # 诊断字段必须贯通:截断/长度限制不能和普通空转混为一谈
                     finish_reason=r.get("finish_reason") or "",
                     usage=r.get("usage") or {})


@dataclass
class OpenAIResponsesAdapter:
    """OpenAI Responses API 适配器（非流式）：合法支持 tool result 图片。"""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = "FW_API_KEY"
    temperature: float = 0.2
    transport: object = None
    supports_vision: bool = True
    name: str = "openai-responses"

    def __post_init__(self) -> None:
        kw = {"model": self.model, "base_url": self.base_url, "api_key": self.api_key,
              "api_key_env": self.api_key_env, "temperature": self.temperature,
              "supports_vision": self.supports_vision}
        if self.transport is not None:
            kw["transport"] = self.transport
        self._client = OpenAIResponsesModel(**kw)

    @property
    def supports_stream(self) -> bool:
        return False

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        r = self._client.chat(messages, tools)
        calls = []
        for c in r.get("tool_calls") or []:
            fn = c.get("function") or {}
            calls.append({"id": c.get("id"), "name": fn.get("name"),
                          "args": c.get("_args") or {}, "args_raw": fn.get("arguments")})
        return Reply(content=r.get("content") or "", tool_calls=calls,
                     finish_reason=r.get("finish_reason") or "",
                     usage=r.get("usage") or {})


def limits_config(root: str | None = None) -> dict:
    """上下文相关的**执行配置**(生产来源: env > state/model.json)。

    只认显式配置:没有 `context_tokens` 就是"窗口未知",不做按模型名的猜测。
    返回的键可直接传给 `Limits(**)`:context_tokens / output_reserve_tokens /
    keep_recent_tokens / compact_retry_cooldown_calls。
    """
    import json as _json
    import os as _os

    keys = ("context_tokens", "output_reserve_tokens", "keep_recent_tokens",
            "compact_retry_cooldown_calls")
    out: dict = {}
    path = _os.environ.get("FW_MODEL_CONFIG") or (
        _os.path.join(root, "state", "model.json") if root else "")
    if path and _os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = _json.load(f)
        except OSError as e:
            raise ValueError(f"读不到模型配置 {path}: {e}") from e
        except _json.JSONDecodeError as e:
            raise ValueError(f"模型配置 JSON 损坏 {path}: {e}") from e
        if not isinstance(d, dict):
            raise ValueError(f"模型配置顶层必须是 object: {path}")
        for k in keys:
            if isinstance(d.get(k), int):
                out[k] = d[k]
    for k in keys:                       # env 覆盖(FW_CONTEXT_TOKENS 等)
        v = _os.environ.get("FW_" + k.upper())
        if v and v.isdigit():
            out[k] = int(v)
    return out


def load_model_options(root: str | None = None) -> dict:
    """读取模型的多模态/API 选项（env 覆盖 state/model.json）。

    任何错误都抛 ValueError，由调用方显示诊断；不猜模型能力。
    """
    path = os.environ.get("FW_MODEL_CONFIG") or (
        os.path.join(root, "state", "model.json") if root else "")
    out = {"api": "", "supports_vision": False, "image_mode": "none"}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"读不到模型配置 {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"模型配置顶层必须是 object: {path}")
        out["api"] = str(data.get("api") or "")
        out["supports_vision"] = bool(data.get("supports_vision", False))
        out["image_mode"] = str(data.get("image_mode") or "none")
    out["api"] = os.environ.get("FW_MODEL_API") or out["api"]
    if os.environ.get("FW_MODEL_VISION") is not None:
        out["supports_vision"] = (os.environ.get("FW_MODEL_VISION") or "").lower() in (
            "1", "true", "yes")
    out["image_mode"] = os.environ.get("FW_MODEL_IMAGE_MODE") or out["image_mode"]
    return out


def build_adapter(kind: str = "scripted", **kw) -> Adapter:
    if kind in ("scripted", "fake"):
        return ScriptedAdapter(script=kw.get("script") or [])
    api = str(kw.get("api") or os.environ.get("FW_MODEL_API") or "").lower()
    if kind == "openai-responses" or (kind in ("openai", "deepseek", "openai-compat")
                                      and api == "responses"):
        return OpenAIResponsesAdapter(
            model=kw.get("model") or os.environ.get("FW_MODEL", "gpt-4o"),
            base_url=kw.get("base_url") or os.environ.get("FW_BASE_URL", "https://api.openai.com/v1"),
            api_key=kw.get("api_key", ""),
            supports_vision=bool(kw.get("supports_vision", True)))
    if kind in ("openai", "deepseek", "openai-compat"):
        vision = kw.get("supports_vision")
        if vision is None:
            vision = (os.environ.get("FW_MODEL_VISION") or "").lower() in ("1", "true", "yes")
        mode = kw.get("image_mode") or os.environ.get("FW_MODEL_IMAGE_MODE") or "none"
        return OpenAICompatAdapter(
            model=kw.get("model") or os.environ.get("FW_MODEL", "deepseek-chat"),
            base_url=kw.get("base_url") or os.environ.get("FW_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=kw.get("api_key", ""),
            supports_vision=bool(vision),
            image_mode=str(mode))
    raise ValueError(f"未知 adapter: {kind}")


def load_model_config(root: str | None = None) -> tuple[str, str, str]:
    """模型配置(fwcore 自己的契约)。

    优先级:环境变量 `FW_MODEL_API_KEY/FW_MODEL_BASE_URL/FW_MODEL_NAME`
            > `state/model.json`(或 `FW_MODEL_CONFIG`)
    返回 (api_key, base_url, model);缺项返回空串,由调用方决定是否报错。
    """
    key = os.environ.get("FW_MODEL_API_KEY") or os.environ.get("FW_API_KEY", "")
    base = os.environ.get("FW_MODEL_BASE_URL") or os.environ.get("FW_BASE_URL", "")
    model = os.environ.get("FW_MODEL_NAME", "")
    path = os.environ.get("FW_MODEL_CONFIG") or (
        os.path.join(root, "state", "model.json") if root else "")
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"读不到模型配置 {path}: {e}") from e
        if not isinstance(cfg, dict):
            raise ValueError(f"模型配置顶层必须是 object: {path}")
        key = key or cfg.get("api_key", "")
        base = base or cfg.get("base_url", "")
        model = model or cfg.get("model", "")
    return key, base, model


def _chunks(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i + size]
