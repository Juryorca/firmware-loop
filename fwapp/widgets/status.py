"""状态栏与快捷键提示(Pi 的 FooterComponent 对应物)。"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..session import Snapshot

STATE_STYLE = {"running": "bold green", "paused": "bold yellow",
               "finished": "bold cyan", "failed": "bold red", "idle": "dim"}


def _fmt_elapsed(seconds) -> str:
    try:
        s = int(float(seconds or 0))
    except (TypeError, ValueError):
        return "∞"
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


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
        t = Text()
        t.append("target ", style="dim")
        t.append(s.target or "—", style="bold")
        t.append("   run ", style="dim")
        t.append(s.run_id or "—", style="bold")
        t.append("   model ", style="dim")
        t.append(s.model, style="bold")
        t.append("   ", style="dim")
        t.append("● " + s.state, style=STATE_STYLE.get(s.state, ""))
        if s.busy:
            t.append("  working…", style="bold yellow")
        if s.handback:
            t.append(f"  handback={s.handback}", style="yellow")
        t.append("\n")

        lim = s.limits or {}
        calls_max = lim.get("max_model_calls", "?")
        sec_max = lim.get("max_seconds", "?")
        t.append("calls ", style="dim")
        t.append(f"{s.calls}/{calls_max}", style="bold")
        t.append("   tools ", style="dim")
        t.append(str(s.tools), style="bold")
        t.append("   elapsed ", style="dim")
        t.append(f"{_fmt_elapsed(s.elapsed)}/{_fmt_elapsed(sec_max) if sec_max != 'unbounded' else '∞'}",
                 style="bold")
        tok = s.tokens or {}
        t.append("   tokens ", style="dim")
        t.append(_fmt_tokens(tok.get("total", 0)), style="bold")
        if tok.get("cached"):
            pct = (tok["cached"] / tok["prompt"] * 100) if tok.get("prompt") else 0
            t.append(f" (cache {pct:.0f}%)", style="dim")
        ctx = s.context or {}
        if ctx.get("window"):
            used = ctx.get("last_prompt") or ctx.get("estimated") or 0
            t.append(f"   ctx {_fmt_tokens(used)}/{_fmt_tokens(ctx['window'])}", style="bold")
            if ctx.get("percent") is not None:
                t.append(f" {ctx['percent']:.0f}%", style="dim")
        t.append("   H ", style="dim")
        h = s.hypotheses or {}
        t.append(f"{h.get('open', 0) + h.get('investigating', 0)} open", style="bold")
        t.append(f"/{h.get('vulnerable', 0)} vuln", style="dim")
        t.append(f"   E {s.evidence}  O {s.observations}", style="dim")
        if s.blockers:
            t.append(f"   blockers {len(s.blockers)}", style="bold red")
        if s.budget and s.budget.get("last_limit"):
            last = s.budget["last_limit"]
            t.append(f"   limit={last.get('reason')}", style="yellow")
        self.update(t)


HINTS = (
    "Enter 发送 · Shift+Enter 换行 · Alt+Enter 排队 · Esc 停止 · "
    "Ctrl+C 取消/退出 · Ctrl+L 模型 · Ctrl+R 续跑 · Ctrl+T 目标 · "
    "Ctrl+O 展开工具 · Ctrl+G 研究面板 · F1 帮助 · / 命令"
)
