"""上下文状态(给模型看的动态块 + CLI 可观测)。

原则:
- 容量只使用 provider 返回的真实 usage;不再向模型暴露字符估算接口;
- 没有真实 usage 时写 unknown,不编造容量;
- 只放进消息**尾部**(state_prompt),不进 system、不写 history/checkpoint。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compact import threshold_tokens


@dataclass
class ContextStatus:
    model: str = ""
    window: int | None = None            # 模型上下文窗口(token);None = 未知
    used_input: int | None = None        # provider prompt_tokens; None = 未返回
    used_source: str = "unknown"         # provider | unknown
    output_reserve: int = 0
    threshold: int = 0                   # 压缩触发阈值(0 = 关闭)
    compaction_enabled: bool = False
    prepare: bool = False                # 是否已到"准备压缩"区间(>=90% 阈值)
    last_prompt_tokens: int | None = None
    last_cached_tokens: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def available(self) -> int | None:
        if self.window is None or self.used_input is None:
            return None
        return max(0, self.window - self.used_input - self.output_reserve)

    def render(self) -> str:
        """一行(必要时两行)给模型的状态;未知值明确写 unknown。"""
        model = self.model or "unknown"
        window = f"{self.window:,}" if self.window else "unknown"
        used = f"{self.used_input:,}" if self.used_input is not None else "unknown"
        used_src = "provider prompt" if self.used_source == "provider" else "provider usage unavailable"
        avail = f"{self.available:,}" if self.available is not None else "unknown"
        thr = f"{self.threshold:,}" if self.threshold else "disabled"
        if self.last_prompt_tokens:
            cached = (f", cached {self.last_cached_tokens:,}"
                      if self.last_cached_tokens is not None else ", cached unknown")
            last = f"last reply usage: prompt {self.last_prompt_tokens:,}{cached}"
        else:
            last = "last reply usage: unknown"
        lines = [
            (f"[Context status] model={model} window={window} context_used={used} "
            f"({used_src}) output_reserve={self.output_reserve:,} available={avail} "
            f"auto_compact={'on' if self.compaction_enabled else 'off'} threshold={thr}."),
            (f"{last}. Context capacity uses provider-reported prompt usage only; "
             "there is no local character-estimate fallback in this interface."),
        ]
        if self.compaction_enabled:
            if self.used_source != "provider":
                lines.append("Provider usage not reported yet: capacity is unknown; "
                             "do not claim the context is nearly full or nearly empty.")
            elif self.prepare:
                lines.append("Approaching the compaction threshold: save key progress now; "
                             "the framework will compact automatically at a safe boundary.")
            else:
                lines.append("Plenty of room. Keep working; do not claim the context is nearly full.")
        elif self.window is None:
            lines.append("Context window is not configured for this model: capacity is unknown; "
                         "do not assume it is nearly full, and do not claim otherwise.")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "model": self.model, "window": self.window,
            "used_input": self.used_input, "used_source": self.used_source,
            "output_reserve": self.output_reserve,
            "available": self.available, "threshold": self.threshold,
            "compaction_enabled": self.compaction_enabled, "prepare": self.prepare,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_cached_tokens": self.last_cached_tokens,
        }


def build_status(*, model: str, window: int | None,
                 output_reserve: int, context_tokens: int,
                 last_prompt_tokens: int | None = None,
                 last_cached_tokens: int | None = None) -> ContextStatus:
    """Build the model-facing capacity block from provider usage only.

    The chars/2.0 estimate remains in the compaction path, never in this
    interface: showing it here made the agent claim 972k used when the
    provider had actually reported 789k.
    """
    thr = threshold_tokens(context_tokens, output_reserve)
    enabled = context_tokens > 0
    real = last_prompt_tokens if last_prompt_tokens and last_prompt_tokens > 0 else None
    used = real
    source = "provider" if real is not None else "unknown"
    prepare = bool(thr) and real is not None and real >= int(thr * 0.9)
    return ContextStatus(model=model, window=window,
                         used_input=used, used_source=source,
                         output_reserve=output_reserve, threshold=thr,
                         compaction_enabled=enabled, prepare=prepare,
                         last_prompt_tokens=last_prompt_tokens,
                         last_cached_tokens=last_cached_tokens)
