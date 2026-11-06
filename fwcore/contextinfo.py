"""上下文状态(给模型看的动态块 + CLI 可观测)。

原则:
- 数字分三类,不混用:本次请求的**估算**、上一次 API 返回的**真实 usage**、配置的**窗口/阈值**;
- 配置缺失/估算不可用时如实写 unknown,不编造容量;
- 只放进消息**尾部**(state_prompt),不进 system、不写 history/checkpoint。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compact import estimate_tokens, threshold_tokens


@dataclass
class ContextStatus:
    model: str = ""
    window: int | None = None            # 模型上下文窗口(token);None = 未知
    estimated_input: int | None = None   # 本次请求估算(字符/2.0)
    output_reserve: int = 0
    threshold: int = 0                   # 压缩触发阈值(0 = 关闭)
    compaction_enabled: bool = False
    prepare: bool = False                # 是否已到"准备压缩"区间(>=90% 阈值)
    last_prompt_tokens: int | None = None
    last_cached_tokens: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def available(self) -> int | None:
        if self.window is None or self.estimated_input is None:
            return None
        return max(0, self.window - self.estimated_input - self.output_reserve)

    def render(self) -> str:
        """一行(必要时两行)给模型的状态;未知值明确写 unknown。"""
        model = self.model or "unknown"
        window = f"{self.window:,}" if self.window else "unknown"
        est = f"{self.estimated_input:,}" if self.estimated_input is not None else "unknown"
        avail = f"{self.available:,}" if self.available is not None else "unknown"
        thr = f"{self.threshold:,}" if self.threshold else "disabled"
        if self.last_prompt_tokens:
            cached = (f", cached {self.last_cached_tokens:,}"
                      if self.last_cached_tokens is not None else ", cached unknown")
            last = f"last reply usage: prompt {self.last_prompt_tokens:,}{cached}"
        else:
            last = "last reply usage: unknown"
        lines = [
            f"[Context status] model={model} window={window} estimated_input={est} "
            f"output_reserve={self.output_reserve:,} available={avail} "
            f"auto_compact={'on' if self.compaction_enabled else 'off'} threshold={thr}.",
            f"{last}. estimated_input is an estimate (chars/2.0), not provider usage.",
        ]
        if self.compaction_enabled:
            if self.prepare:
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
            "estimated_input": self.estimated_input,
            "output_reserve": self.output_reserve,
            "available": self.available, "threshold": self.threshold,
            "compaction_enabled": self.compaction_enabled, "prepare": self.prepare,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_cached_tokens": self.last_cached_tokens,
        }


def build_status(*, model: str, window: int | None, messages: list[dict],
                 output_reserve: int, context_tokens: int,
                 last_prompt_tokens: int | None = None,
                 last_cached_tokens: int | None = None) -> ContextStatus:
    est = estimate_tokens(messages) if messages else 0
    thr = threshold_tokens(context_tokens, output_reserve)
    enabled = context_tokens > 0
    prepare = bool(thr) and est >= int(thr * 0.9)
    return ContextStatus(model=model, window=window, estimated_input=est,
                         output_reserve=output_reserve, threshold=thr,
                         compaction_enabled=enabled, prepare=prepare,
                         last_prompt_tokens=last_prompt_tokens,
                         last_cached_tokens=last_cached_tokens)
