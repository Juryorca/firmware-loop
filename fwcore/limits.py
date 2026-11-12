"""执行资源边界(与"研究停止规则"彻底分开)。

原则(2026-11-04 62GB 事故后的定案)
- **研究停止规则**是领域语义:什么时候这条假设可以收口、什么时候可以交回控制权。
  ⇒ 由 domain/research 决定,永不因为"跑得久/占得多"而改变。
- **执行资源边界**是工程保护:模型调用次数、工具时长。
  ⇒ 触发时**只允许暂停(run.state='paused')**,可恢复;绝不写结论、绝不放弃假设。

默认**不设**模型调用次数、墙钟或单工具时长上限。旧的 `max_*` 字段保留为测试/兼容钩子，
不会被 `limits_config` 暴露；生产配置不设置这些值。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Limits:
    # 兼容/测试钩子:0 = 不限；生产配置不暴露这些字段。
    max_model_calls: int = 0
    max_seconds: float = 0.0
    max_tool_seconds: float = 0.0
    # 模型上下文窗口(token,显式配置):0 = 不做阈值压缩(仍受 provider 上限约束)。
    # 到达 context_tokens-reserve 时压缩较早历史,保留最近 keep_recent_tokens。
    context_tokens: int = 0
    keep_recent_tokens: int = 20_000
    # 输出/协议预留:压缩阈值 = 窗口 − reserve(Pi 语义)
    output_reserve_tokens: int = 8_000
    # 压缩失败后的冷却(模型调用次数):避免每轮重试烧预算
    compact_retry_cooldown_calls: int = 5
    # 分块摘要:每块 token 上限 / 单次压缩最多几次摘要调用(超限=失败,不丢历史)
    summarize_chunk_tokens: int = 60_000
    summarize_max_calls: int = 20

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
        }
