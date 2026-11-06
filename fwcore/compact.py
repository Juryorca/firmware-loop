"""上下文压缩(对齐 Pi 的 compaction:阈值触发 + 保留最近一段 + 结构化摘要)。

原则:
- 阈值由**配置的模型上下文窗口**(token)决定,不用字符数冒充 token 上限;
- 只从**安全边界**切(不能以 `tool` 消息开头,否则破坏 assistant.tool_calls ↔ tool 配对);
- 摘要由调用方注入(生产用模型,测试用 fake);摘要文本也保留原文指针(DB/磁盘)。
"""

from __future__ import annotations

import json

CHARS_PER_TOKEN = 3.5     # 只用于触发判断的估算;真实 usage 仍以 provider 返回为准


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


def find_cut(history: list[dict], keep_recent_tokens: int) -> int:
    """从尾部往前累计,保留最近约 keep_recent_tokens;切点不得落在 tool 消息上。"""
    if not history:
        return 0
    acc = 0
    cut = 0
    for i in range(len(history) - 1, -1, -1):
        acc += estimate_tokens([history[i]])
        if acc >= keep_recent_tokens:
            cut = i
            break
    # 切点若是 tool,说明它对应的 assistant(tool_calls) 会被压掉 ⇒ 往后挪到下一个非 tool
    while cut < len(history) and history[cut].get("role") == "tool":
        cut += 1
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


def should_compact(messages: list[dict], context_tokens: int, reserve: int = 4_000) -> bool:
    if context_tokens <= 0:
        return False
    return estimate_tokens(messages) > max(1_000, context_tokens - reserve)
