"""状态栏与快捷键提示(Pi 的 FooterComponent 对应物)。"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..session import Snapshot

STATE_STYLE = {"running": "bold #89B99D", "paused": "bold #D5B47A",
               "finished": "bold #83A9D5", "failed": "bold #CE8888", "idle": "#66717F"}
STATE_LABELS = {"running": "运行中", "paused": "已暂停", "finished": "已完成",
                "failed": "失败", "idle": "空闲"}
TARGET_STATUS_LABELS = {"active": "进行中", "closed_confirmed": "已收尾",
                        "archived": "已归档"}
HANDBACK_LABELS = {
    "awaiting_user_close": "等待你确认收尾",
    "closed": "收尾已确认",
    "needs_user_input": "需要你补充信息",
    "user_stop": "你已停止",
    "model_error": "模型调用失败",
    "watchdog_paused": "执行进程中断，已暂停",
    "recovered_stale": "恢复时发现旧任务，已暂停",
    "owner_dead": "执行进程失联",
    "run_not_running": "任务未在运行",
    "budget": "已达到执行预算",
    "paused": "已暂停",
}
HYPOTHESIS_LABELS = {"open": "待开始", "investigating": "调查中",
                     "concluded": "已结论", "dropped": "已排除"}
VERDICT_LABELS = {"vulnerable": "已成立", "no_finding": "未发现",
                  "inconclusive": "无法判定", "": "—"}
DISPOSITION_LABELS = {"unjudged": "未判定", "vulnerable": "已成立",
                      "no_finding": "未发现", "out_of_scope": "不纳入范围"}
LOG_KIND_LABELS = {"round": "轮次", "stop": "停止", "decision": "决策",
                   "mirror": "复盘", "migration": "迁移"}
KNOWLEDGE_KIND_LABELS = {"pattern": "缺陷模式", "misreport": "误报判据",
                         "guideline": "操作经验"}
KNOWLEDGE_STATUS_LABELS = {"approved": "已收录", "deprecated": "已退休",
                           "superseded": "已替代"}


def state_label(value) -> str:
    return STATE_LABELS.get(str(value or ""), "未知状态")


def target_status_label(value) -> str:
    return TARGET_STATUS_LABELS.get(str(value or "active"), "状态未知")


def handback_label(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = raw.split(":", 1)[0].strip()
    return HANDBACK_LABELS.get(key, "已暂停")


def response_status_label(value) -> str:
    return {"ok": "完成", "partial": "部分完成", "cancelled": "已取消",
            "error": "生成失败"}.get(str(value or ""), "未完成")


def hypothesis_state_label(value) -> str:
    return HYPOTHESIS_LABELS.get(str(value or ""), "未知状态")


def verdict_label(value) -> str:
    return VERDICT_LABELS.get(str(value or ""), "未知结论")


def disposition_label(value) -> str:
    return DISPOSITION_LABELS.get(str(value or ""), "未知状态")


def log_kind_label(value) -> str:
    return LOG_KIND_LABELS.get(str(value or ""), "记录")


def knowledge_kind_label(value) -> str:
    return KNOWLEDGE_KIND_LABELS.get(str(value or ""), "知识")


def knowledge_status_label(value) -> str:
    return KNOWLEDGE_STATUS_LABELS.get(str(value or ""), "未知状态")


def model_label(value) -> str:
    return "演示模式" if str(value or "") in ("scripted", "fake") else str(value or "—")


def blocker_label(value) -> str:
    """把可核的 Gate 原因换成用户能读懂的短句；细节仍保留在原文中。"""
    s = str(value or "").strip()
    replacements = (
        ("没有任何假设(空研究状态不算完成)", "还没有任何假设"),
        ("没有登记任何攻击面(A1 枚举缺失)", "还没有登记攻击面"),
        ("条假设未收口(open/investigating)", "条假设尚未完成"),
        ("个面未判定(disposition=unjudged)", "个攻击面尚未判定"),
        ("次决定性尝试没交代观察(new/none + 理由)——双输出缺失", "次尝试没有记录新观察"),
        ("三镜刷新超期", "研究复盘已到期"),
        ("材料不完整", "报告材料不完整"),
        ("条 vulnerable 结论", "条已成立结论"),
        ("条证据的版本与目标版本", "条证据的版本与目标版本"),
        ("个尝试仍在 running(先结束/取消)", "个尝试仍在运行"),
    )
    for old, new in replacements:
        s = s.replace(old, new)
    return s


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_tokens(n) -> str:
    try:
        n = int(float(n or 0))
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_snapshot(s: Snapshot) -> Text:
    """用普通中文显示目标和运行摘要；不依赖 Textual App，便于测试。"""
    t = Text()
    t.append(s.target or "未选择目标", style="bold #83A9D5")
    t.append(" · ", style="#66717F")
    t.append(model_label(s.model), style="#8995A5")
    t.append(" · ", style="#66717F")
    t.append(state_label(s.state), style=STATE_STYLE.get(s.state, ""))
    lifecycle = getattr(s, "target_status", "active")
    if lifecycle != "active":
        t.append(f" · {target_status_label(lifecycle)}", style="bold #D5B47A")
    if s.busy:
        t.append(" · 正在处理", style="#D5B47A")
    if s.handback:
        t.append(f" · {handback_label(s.handback)}", style="#D5B47A")
    lim = s.limits or {}
    calls_max = lim.get("max_model_calls", "?")
    if calls_max in (0, "unbounded"):
        calls_max = "∞"
    t.append(f" · 模型调用 {s.calls}/{calls_max}", style="#66717F")
    # tools 是 tool_calls 计数，不是“可用工具数”。
    t.append(f" · 工具调用 {s.tools}", style="#66717F")
    tok = s.tokens or {}
    # Pi 的 footer 口径是最近一次模型请求；没有 latest 字段时兼容旧快照。
    prompt = _as_int(tok.get("last_prompt")) or _as_int(tok.get("prompt"))
    cached = _as_int(tok.get("last_cached")) if "last_cached" in tok else _as_int(tok.get("cached"))
    if prompt:
        pct = min(100.0, cached / prompt * 100)
        t.append(f" · 缓存 {pct:.0f}%", style="#83A9D5")
    else:
        t.append(" · 缓存 —", style="#66717F")
    ctx = s.context or {}
    if ctx.get("window"):
        used = ctx.get("last_prompt") or ctx.get("estimated") or 0
        t.append(f" · 上下文 {_fmt_tokens(used)}/{_fmt_tokens(ctx['window'])}",
                 style="#8995A5")
    h = s.hypotheses or {}
    t.append(f" · 假设 {h.get('open', 0) + h.get('investigating', 0)}"
             f"/成立 {h.get('vulnerable', 0)}", style="#66717F")
    t.append(f" · 证据 {s.evidence}", style="#66717F")
    if s.blockers:
        t.append(f" · 收尾阻塞 {len(s.blockers)}", style="bold #CE8888")
    return t


class StatusBar(Static):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._last_plain: str | None = None

    def update_snapshot(self, s: Snapshot) -> None:
        text = format_snapshot(s)
        if text.plain == self._last_plain:
            return
        self._last_plain = text.plain
        self.update(text)


HINTS = (
    "Enter 发送 · Shift+Enter 换行 · Esc 停止 · "
    "Ctrl+O 工具 · F2 研究 · / 命令 · 拖选复制"
)
