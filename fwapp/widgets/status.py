"""状态栏与快捷键提示(Pi 的 FooterComponent 对应物)。"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..session import Snapshot

STATE_STYLE = {"running": "bold #89B99D", "paused": "bold #D5B47A",
               "finished": "bold #83A9D5", "failed": "bold #CE8888", "idle": "#66717F"}


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


class StatusBar(Static):
    def update_snapshot(self, s: Snapshot) -> None:
        """单行状态：只留目标、状态、调用/上下文和研究摘要。"""
        t = Text()
        t.append(s.target or "—", style="bold #83A9D5")
        t.append(" · ", style="#66717F")
        t.append(s.model, style="#8995A5")
        t.append(" · ", style="#66717F")
        t.append(s.state, style=STATE_STYLE.get(s.state, ""))
        if s.busy:
            t.append(" · working…", style="#D5B47A")
        if s.handback:
            t.append(f" · {s.handback}", style="#D5B47A")
        lim = s.limits or {}
        calls_max = lim.get("max_model_calls", "?")
        t.append(f" · {s.calls}/{calls_max} calls", style="#66717F")
        t.append(f" · {s.tools} tools", style="#66717F")
        ctx = s.context or {}
        if ctx.get("window"):
            used = ctx.get("last_prompt") or ctx.get("estimated") or 0
            pct = ctx.get("percent")
            t.append(f" · ctx {_fmt_tokens(used)}/{_fmt_tokens(ctx['window'])}",
                     style="#8995A5")
            if pct is not None:
                t.append(f" {pct:.0f}%", style="#83A9D5")
        h = s.hypotheses or {}
        t.append(f" · H {h.get('open', 0) + h.get('investigating', 0)}/{h.get('vulnerable', 0)} vuln",
                 style="#66717F")
        t.append(f" · E {s.evidence}", style="#66717F")
        if s.blockers:
            t.append(f" · blockers {len(s.blockers)}", style="bold #CE8888")
        self.update(t)


HINTS = (
    "Enter 发送 · Shift+Enter 换行 · Esc 停止 · "
    "Ctrl+O 工具 · F2 研究 · / 命令"
)
