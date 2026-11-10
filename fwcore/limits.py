"""执行资源边界(与"研究停止规则"彻底分开)。

原则(2026-11-04 62GB 事故后的定案)
- **研究停止规则**是领域语义:什么时候这条假设可以收口、什么时候可以交回控制权。
  ⇒ 由 domain/research 决定,永不因为"跑得久/占得多"而改变。
- **执行资源边界**是工程保护:模型调用次数、墙钟、工具时长。
  ⇒ 触发时**只允许暂停(run.state='paused')**,可恢复;绝不写结论、绝不放弃假设。

默认必须有边界;要"不限"必须显式 `Limits.unbounded()`。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Limits:
    # 软边界:0 表示"该维度不限"(需要显式选择 unbounded 才会出现 0)
    max_model_calls: int = 200
    max_seconds: float = 1800.0
    max_tool_seconds: float = 120.0
    # 模型上下文窗口(token,显式配置):0 = 不做阈值压缩(仍受 provider 上限约束)。
    # 到达 context_tokens-reserve 时压缩较早历史,保留最近 keep_recent_tokens。
    context_tokens: int = 0
    keep_recent_tokens: int = 20_000
    # 输出/协议预留:压缩阈值 = 窗口 − max(预留, 10% 窗口);不能把阈值顶到窗口上
    output_reserve_tokens: int = 8_000
    # 压缩失败后的冷却(模型调用次数):避免每轮重试烧预算
    compact_retry_cooldown_calls: int = 5
    # 分块摘要:每块 token 上限 / 单次压缩最多几次摘要调用(超限=失败,不丢历史)
    summarize_chunk_tokens: int = 60_000
    summarize_max_calls: int = 20
    # 单轮最多执行多少个工具调用(模型可能一次发很多;这是执行边界,不是研究规则)
    max_tool_calls_per_turn: int = 8

    @classmethod
    def unbounded(cls) -> Limits:
        """显式选择"研究循环不限步/不限时"。"""
        return cls(max_model_calls=0, max_seconds=0.0)

    def calls_exhausted(self, n: int) -> bool:
        return bool(self.max_model_calls) and n > self.max_model_calls

    def time_exhausted(self, elapsed: float) -> bool:
        return bool(self.max_seconds) and elapsed > self.max_seconds

    def describe(self) -> dict:
        return {
            "max_model_calls": self.max_model_calls or "unbounded",
            "max_seconds": self.max_seconds or "unbounded",
            "max_tool_seconds": self.max_tool_seconds,
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
        }
