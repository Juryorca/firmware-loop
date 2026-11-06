"""上下文压缩(对齐 Pi 的 compaction:阈值触发 + 保留最近一段 + 结构化摘要)。

原则:
- 阈值由**配置的模型上下文窗口**(token)决定,不用字符数冒充 token 上限;
- 只从**安全边界**切(不能以 `tool` 消息开头,否则破坏 assistant.tool_calls ↔ tool 配对);
- 摘要由调用方注入(生产用模型,测试用 fake);摘要文本也保留原文指针(DB/磁盘)。
"""

from __future__ import annotations

import json

# 实测校准(QNAP 两个 run、224 次调用):实际 ~2.08 字符/token;
# 用 2.0 让估算**略偏保守**(宁可早触发,不可晚触发)。只用于触发,计费仍以 usage 为准。
CHARS_PER_TOKEN = 2.0


def estimate_tokens(messages: list[dict]) -> int:
    """粗估 token(字符/3.5)。仅用于"该不该压缩",不用于计费。"""
    n = 0
    for m in messages:
        c = m.get("content")
        n += len(c) if isinstance(c, str) else 0
        n += len(m.get("reasoning_content") or "")
        for tc in m.get("tool_calls") or []:
            n += len(json.dumps(tc, ensure_ascii=False))
    return int(n / CHARS_PER_TOKEN)


def message_groups(history: list[dict]) -> list[tuple[int, int]]:
    """把历史切成**协议组**:assistant(可带 tool_calls)+ 紧随其后的全部 tool 结果。

    返回 [(start, end)] 半开区间。压缩只能在这些边界处切:
    只保证"不以 tool 开头"不够 —— assistant(3 个 tool_calls) 的 3 条结果必须整体保留或整体丢弃。
    """
    groups: list[tuple[int, int]] = []
    i = 0
    n = len(history)
    while i < n:
        role = history[i].get("role")
        if role == "assistant" and history[i].get("tool_calls"):
            j = i + 1
            ids = {c.get("id") for c in history[i]["tool_calls"]}
            while j < n and history[j].get("role") == "tool" and history[j].get("tool_call_id") in ids:
                j += 1
            groups.append((i, j))
            i = j
        else:
            groups.append((i, i + 1))
            i += 1
    return groups


def find_cut(history: list[dict], keep_recent_tokens: int) -> int:
    """按**协议组**从尾部往前保留约 keep_recent_tokens,返回组边界处的切点。"""
    if not history:
        return 0
    groups = message_groups(history)
    acc = 0
    cut = 0
    for start, end in reversed(groups):
        acc += estimate_tokens(history[start:end])
        if acc >= keep_recent_tokens:
            cut = start
            break
    return cut


SUMMARY_HEADER = "## 历史摘要(程序压缩;原文仍可用 tool_calls/产物路径回查)"


def compact_history(history: list[dict], summarize, keep_recent_tokens: int = 20_000,
                    ) -> tuple[list[dict], dict]:
    """超阈值时把较早的消息压成一段摘要;返回 (新历史, 统计)。"""
    cut = find_cut(history, keep_recent_tokens)
    if cut <= 0:
        return history, {"compacted": 0}
    old, recent = history[:cut], history[cut:]
    summary = (summarize(old) or "").strip()
    head = {"role": "assistant", "content": f"{SUMMARY_HEADER}\n{summary}"}
    return [head] + recent, {"compacted": cut, "summary_chars": len(summary)}


def threshold_tokens(context_tokens: int, reserve: int) -> int:
    """压缩触发阈值 = 窗口 − 输出/协议预留(至少留 10% 窗口或 reserve,取较大者)。"""
    if context_tokens <= 0:
        return 0
    keep = max(reserve, int(context_tokens * 0.10))
    return max(1_000, context_tokens - keep)


def should_compact(messages: list[dict], context_tokens: int, reserve: int = 8_000) -> bool:
    if context_tokens <= 0:
        return False
    return estimate_tokens(messages) > threshold_tokens(context_tokens, reserve)
