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
                    chunk_tokens: int = 60_000, max_calls: int = 20,
                    ) -> tuple[list[dict], dict]:
    """超阈值时把较早的消息压成一段摘要;返回 (新历史, 统计)。

    **覆盖保证**:被丢弃的每条消息都进入某一块摘要(summarize_covered);任何一块失败/为空
    ⇒ 抛 SummarizationIncomplete,调用方必须保留原历史。
    """
    cut = find_cut(history, keep_recent_tokens)
    if cut <= 0:
        return history, {"compacted": 0}
    old, recent = history[:cut], history[cut:]
    summary, cover = summarize_covered(old, summarize, chunk_tokens=chunk_tokens,
                                       max_calls=max_calls)
    if not summary.strip():
        raise SummarizationIncomplete("摘要为空")
    head = {"role": "assistant", "content": f"{SUMMARY_HEADER}\n{summary}"}
    return [head] + recent, {"compacted": cut, "summary_chars": len(summary),
                             "summarize_calls": cover["calls"],
                             "messages_covered": cover["messages"]}


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


class SummarizationIncomplete(RuntimeError):
    """压缩无法覆盖全部被丢弃的消息(或摘要为空):调用方必须保留原历史。"""


def _split_oversized(msg: dict, budget: int) -> list[dict]:
    """单条消息超过预算时按内容切片(每片都带原消息的 role/id),保证全文都被处理。"""
    content = msg.get("content")
    if not isinstance(content, str) or estimate_tokens([msg]) <= budget:
        return [msg]
    per = max(200, int(budget * CHARS_PER_TOKEN))
    parts = [content[i:i + per] for i in range(0, len(content), per)]
    out = []
    for k, part in enumerate(parts, 1):
        m = dict(msg)
        m["content"] = f"[part {k}/{len(parts)}] {part}"
        out.append(m)
    return out


def _pack(units: list[list[dict]], budget: int) -> list[list[dict]]:
    """把协议组装进 ≤budget 的块;组内单条过大时切片后再装。"""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_tok = 0
    for unit in units:
        flat: list[dict] = []
        for m in unit:
            flat += _split_oversized(m, budget)
        size = estimate_tokens(flat)
        if cur and cur_tok + size > budget:
            chunks.append(cur)
            cur, cur_tok = [], 0
        cur += flat
        cur_tok += size
    if cur:
        chunks.append(cur)
    return chunks


def summarize_covered(messages: list[dict], summarize, *, chunk_tokens: int = 60_000,
                      max_calls: int = 20) -> tuple[str, dict]:
    """把**全部** messages 分块摘要后再归并;任何一块失败/为空 ⇒ 抛 SummarizationIncomplete。

    覆盖保证:所有块的消息数之和 == len(messages)(超大消息按内容切片后仍计入其片数);
    无法在 max_calls 内完成 ⇒ 失败,绝不"只摘要最早一小段然后把其余删掉"。
    """
    if not messages:
        return "", {"chunks": 0, "messages": 0, "calls": 0}
    units = [messages[a:b] for a, b in message_groups(messages)]
    chunks = _pack(units, chunk_tokens)
    if len(chunks) > max_calls:
        raise SummarizationIncomplete(
            f"需要 {len(chunks)} 次分块摘要,超过上限 {max_calls};拒绝不完整压缩")
    summaries: list[str] = []
    covered = 0
    for ch in chunks:
        covered += len(ch)
        s = (summarize(ch) or "").strip()
        if not s:
            raise SummarizationIncomplete("分块摘要为空 ⇒ 视为失败(不产生半截摘要)")
        summaries.append(s)
    # 归并:多份摘要再分组压缩,直到只剩一份
    calls = len(chunks)
    merge_calls = 0
    while len(summaries) > 1:
        packed: list[str] = []
        cur: list[str] = []
        cur_tok = 0
        for s in summaries:
            tok = estimate_tokens([{"role": "user", "content": s}])
            if cur and cur_tok + tok > chunk_tokens:
                packed.append("\n\n".join(cur))
                cur, cur_tok = [], 0
            cur.append(s)
            cur_tok += tok
        if cur:
            packed.append("\n\n".join(cur))
        calls += len(packed)
        if calls > max_calls * 2:
            raise SummarizationIncomplete("归并摘要次数超限")
        merge_calls += len(packed)
        summaries = [(summarize([{"role": "user", "content": p}]) or "").strip() for p in packed]
        if any(not s for s in summaries):
            raise SummarizationIncomplete("归并摘要为空 ⇒ 失败")
    return summaries[0], {"chunks": len(chunks), "messages": len(messages),
                          "pieces": covered, "calls": calls, "merge_calls": merge_calls}


def summarize_chunks(messages: list[dict], chunk_tokens: int = 60_000) -> list[list[dict]]:
    """只做分组(测试/调试用),与实际摘要使用同一装包规则。"""
    return _pack([messages[a:b] for a, b in message_groups(messages)], chunk_tokens)
