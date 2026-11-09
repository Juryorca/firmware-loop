"""Overlay / Dialog:选择器、研究面板、帮助、确认框(Pi 的 selector/overlay 对应物)。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from fwcore.toolset import check_toolset

from ..commands import COMMANDS
from ..services import state_store_rows
from ..session import AgentSession
from .status import (
    blocker_label,
    disposition_label,
    handback_label,
    hypothesis_state_label,
    log_kind_label,
    state_label,
    target_status_label,
    verdict_label,
)


@dataclass
class SelectItem:
    value: str
    label: str
    detail: str = ""


class SelectorScreen(ModalScreen[str | None]):
    """通用选择器:搜索 + 列表(目标 / run / 模型)。"""

    BINDINGS: ClassVar[list[Any]] = [("escape", "cancel", "取消")]

    def __init__(self, title: str, items: list[SelectItem], **kw):
        super().__init__(**kw)
        self.title_text = title
        self.items = items
        self._filtered = list(items)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.title_text, classes="dialog-title")
            yield Input(placeholder="搜索…", id="search")
            yield OptionList(id="options")
            yield Static("↑/↓ 选择 · Enter 确认 · Esc 取消", classes="dialog-hint")

    def on_mount(self) -> None:
        self._render_options()
        self.query_one("#search", Input).focus()

    def _render_options(self) -> None:
        ol = self.query_one("#options", OptionList)
        ol.clear_options()
        for it in self._filtered:
            t = Text()
            t.append(it.label, style="bold")
            if it.detail:
                t.append("  " + it.detail, style="dim")
            ol.add_option(Option(t, id=it.value))
        if self._filtered:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        q = (event.value or "").lower()
        self._filtered = [i for i in self.items
                          if q in i.label.lower() or q in i.detail.lower() or q in i.value.lower()]
        self._render_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#options", OptionList)
        if ol.option_count:
            ol.focus()
            ol.action_select()

    def on_key(self, event: events.Key) -> None:
        # 搜索框里按 ↓ 直接进入列表(与 Pi 的 selector 一致)
        if event.key == "down" and isinstance(self.focused, Input):
            ol = self.query_one("#options", OptionList)
            if ol.option_count:
                ol.focus()
                event.stop()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Any]] = [("escape", "close", "关闭")]

    def compose(self) -> ComposeResult:
        lines = ["# fw-core 命令与快捷键", "", "## 命令", ""]
        for c in COMMANDS:
            hint = f" `{c.arg_hint}`" if c.arg_hint else ""
            lines.append(f"- **/{c.name}**{hint} — {c.description}")
        lines += [
            "", "## 快捷键", "",
            "- `Enter` 发送 · `Shift+Enter` 换行 · `Alt+Enter` 排队",
            "- `Esc` 停止当前运行 · `Ctrl+C` 取消/清空/退出",
            "- `Ctrl+L` 模型 · `Ctrl+R` 恢复会话 · `Ctrl+T` 目标",
            "- `Ctrl+O` 展开/折叠工具 · `Ctrl+G` 研究面板",
            "- `F1` 帮助 · `F2` 研究面板 · `Ctrl+Q` 退出",
            "", "## 说明", "",
            "TUI 只是操作界面:模型调用、工具执行、预算、暂停/恢复全部由 fwcore Core 控制。",
        ]
        with VerticalScroll(classes="dialog help"):
            yield Static("帮助", classes="dialog-title")
            yield Static(RichMarkdown("\n".join(lines)))
            yield Static("Esc 关闭", classes="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS: ClassVar[list[Any]] = [("escape", "no", "取消")]

    def __init__(self, message: str, **kw):
        super().__init__(**kw)
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm"):
            yield Static("确认", classes="dialog-title")
            yield Static(Text(self.message))
            with Horizontal(classes="dialog-buttons"):
                yield Button("取消", id="no", variant="default")
                yield Button("确定", id="yes", variant="primary")
            yield Static("y / Enter 确定 · n / Esc 取消", classes="dialog-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("y", "enter"):
            self.dismiss(True)
            event.stop()
        elif event.key in ("n", "escape"):
            self.dismiss(False)
            event.stop()

    def action_no(self) -> None:
        self.dismiss(False)


def _fmt_duration(seconds) -> str:
    try:
        s = float(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    if s >= 60:
        return f"{int(s // 60)}分{int(s % 60)}秒"
    return f"{s:.1f}秒"


class ResearchScreen(ModalScreen[None]):
    """研究专用视图：进度、假设池、观察、证据、结论与收尾检查。"""

    BINDINGS: ClassVar[list[Any]] = [("escape", "close", "关闭"), ("g", "close", "关闭")]

    def __init__(self, session: AgentSession, tab: str = "progress", **kw):
        super().__init__(**kw)
        self.session = session
        self.initial_tab = tab
        self._last_refresh_sig: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog research"):
            yield Static("研究状态（只读）", classes="dialog-title")
            with TabbedContent(initial=self.initial_tab, id="tabs"):
                with TabPane("进度", id="progress"):
                    yield Static(id="progress-body", classes="pane")
                with TabPane("假设池", id="hypotheses"):
                    yield DataTable(id="hyp-table", cursor_type="row")
                with TabPane("观察", id="observations"):
                    yield DataTable(id="obs-table", cursor_type="row")
                with TabPane("证据", id="evidence"):
                    yield DataTable(id="ev-table", cursor_type="row")
                with TabPane("结论", id="conclusions"):
                    yield DataTable(id="con-table", cursor_type="row")
                with TabPane("收尾检查", id="gates"):
                    yield Static(id="gate-body", classes="pane")
            yield Static("Esc 关闭 · 数据每 2 秒刷新", classes="dialog-hint")

    def on_mount(self) -> None:
        for tid, cols in (
            ("#hyp-table", ("编号", "状态", "结论", "优先级", "标题")),
            ("#obs-table", ("时间", "观察")),
            ("#ev-table", ("编号", "类型", "复核", "版本", "位置")),
            ("#con-table", ("编号", "关联假设", "结论", "适用范围", "时间")),
        ):
            self.query_one(tid, DataTable).add_columns(*cols)
        self.refresh_data()
        self.set_interval(2.0, self.refresh_data)

    def refresh_data(self) -> None:
        sess = self.session
        if not sess.target:
            self.query_one("#progress-body", Static).update("没有目标")
            return
        rows = state_store_rows(sess.services.con, sess.target, sess.run_id)
        derived = {}
        try:
            derived = sess.services.domain.derived(sess.target)
        except Exception as e:  # noqa: BLE001
            derived = {"error": str(e)}
        sig = json.dumps({"rows": rows, "derived": derived}, sort_keys=True, default=str)
        if sig == self._last_refresh_sig:
            return
        self._last_refresh_sig = sig
        run = rows.get("run") or {}

        def counts(d, labels=None):
            labels = labels or {}
            return ", ".join(f"{labels.get(k, k)}={v}"
                             for k, v in sorted((d or {}).items()) if v) or "—"

        prog = Text()
        prog.append("目标 ", style="bold")
        prog.append(sess.target or "—")
        prog.append("   研究会话 ", style="bold")
        prog.append(run.get("id") or "—")
        prog.append("   状态 ", style="bold")
        prog.append(state_label(run.get("state")) if run else "尚无研究会话")
        target_status = (rows.get("target_row") or {}).get("status", "active")
        if target_status != "active":
            prog.append(f"   目标：{target_status_label(target_status)}", style="yellow")
        prog.append("\n")
        if run:
            prog.append(f"目标说明：{(run.get('goal') or '')[:300]}\n")
            prog.append(f"模型调用 {run.get('model_calls')} · 工具调用 {run.get('tool_calls')} "
                        f"· 步骤 {run.get('step')} · 轮次 {run.get('rounds')} "
                        f"· 用时 {_fmt_duration(run.get('elapsed_s'))}\n")
            if run.get("handback_reason"):
                prog.append(f"暂停原因：{handback_label(run['handback_reason'])}\n", style="yellow")
        prog.append(f"假设：{counts(derived.get('hypotheses'), {k: hypothesis_state_label(k) for k in ('open','investigating','concluded','dropped')})}\n")
        prog.append(f"攻击面覆盖：{derived.get('surface_coverage')}  "
                    f"面状态：{counts(derived.get('surfaces'), {k: disposition_label(k) for k in ('unjudged','vulnerable','no_finding','out_of_scope')})}\n")
        prog.append(f"尝试：{counts(derived.get('attempts'))}\n")
        prog.append(f"证据 {derived.get('evidence')} · 观察 {derived.get('observations')}"
                    f" · 结论 {len(rows.get('conclusions') or [])}\n")
        blockers = derived.get("closure_blockers") or []
        prog.append("\n收尾检查\n", style="bold")
        if blockers:
            for b in blockers:
                prog.append(f"- {blocker_label(b)}\n", style="red")
        else:
            prog.append("- 没有阻塞\n", style="green")
        logs = rows.get("logs") or []
        if logs:
            prog.append("\n最近记录\n", style="bold")
            for lg in logs[:10]:
                prog.append(f"- [{log_kind_label(lg['kind'])}] {lg['title'][:100]} ({lg['created_at'][:10]})\n")
        self.query_one("#progress-body", Static).update(prog)

        gate = Text()
        gate.append("收尾检查\n", style="bold")
        for b in blockers:
            gate.append(f"- {blocker_label(b)}\n", style="red")
        if not blockers:
            gate.append("- 没有阻塞\n", style="green")
        self.query_one("#gate-body", Static).update(gate)

        hyp = self.query_one("#hyp-table", DataTable)
        hyp.clear()
        for h in rows.get("hypotheses") or []:
            hyp.add_row(h["id"], hypothesis_state_label(h["state"]),
                        verdict_label(h.get("verdict") or ""), str(h["priority"]),
                        (h["title"] or "")[:120])
        obs = self.query_one("#obs-table", DataTable)
        obs.clear()
        for o in rows.get("observations") or []:
            obs.add_row((o.get("created_at") or "")[:19], (o.get("text") or "")[:200])
        ev = self.query_one("#ev-table", DataTable)
        ev.clear()
        for e in rows.get("evidence") or []:
            ev.add_row(e["id"], e.get("kind") or "", "已复核" if e.get("verified") else "未复核",
                       str(e.get("version") or ""), (e.get("path") or e.get("capture") or "")[:100])
        con_table = self.query_one("#con-table", DataTable)
        con_table.clear()
        for c in rows.get("conclusions") or []:
            con_table.add_row(c["id"], c.get("hypothesis_id") or "",
                              verdict_label(c.get("verdict") or ""),
                              (c.get("scope_note") or "")[:80], (c.get("created_at") or "")[:19])

    def action_close(self) -> None:
        self.dismiss(None)


def parse_run_label(row: dict) -> str:
    goal = (row.get("goal") or "").replace("\n", " ")
    return f"{row['id']} · {state_label(row.get('state'))} · {goal[:70]}"



TOOLSET_STATUS_LABELS = {
    "ready": "已就绪", "partial": "部分可用", "missing": "缺失",
    "deps_missing": "缺依赖", "not_started": "未启动",
    "arch_mismatch": "架构不匹配", "optional": "可选未就绪",
}


def _toolset_user_text(report: dict) -> str:
    lines = ["工具就绪检查", ""]
    req = report.get("required_total", 0)
    ready = report.get("required_ready", 0)
    lines.append(f"必需项：{ready}/{req} 就绪；未就绪 {report.get('required_incomplete', 0)} 项")
    if report.get("config_error"):
        lines.append(f"配置错误：{report['config_error']}")
        return "\n".join(lines)
    for item in report.get("items", []):
        if not item.get("required"):
            continue
        status = TOOLSET_STATUS_LABELS.get(item.get("status"), "状态未知")
        lines.append(f"- {item.get('label', '未命名工具')}：{status}")
        for reason in (item.get("reasons") or [])[:2]:
            lines.append(f"  · {reason}")
    return "\n".join(lines)


class ToolsetScreen(ModalScreen[None]):
    """默认工具集完整性(只读;不启动服务/浏览器)."""

    BINDINGS: ClassVar[list[Any]] = [("escape", "close", "关闭"), ("g", "close", "关闭")]

    def __init__(self, root: str, **kw):
        super().__init__(**kw)
        self.root = root

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog research"):
            yield Static("工具就绪检查", classes="dialog-title")
            yield Static("正在检查工具…", id="toolset-body", classes="pane")
            yield Static("Esc 关闭", classes="dialog-hint")

    def on_mount(self) -> None:
        # toolset 探针会跑外部命令；不能在 compose/UI 线程里同步阻塞。
        self.run_worker(self._load_report(), exclusive=True)

    async def _load_report(self) -> None:
        try:
            report = await asyncio.to_thread(check_toolset, self.root)
        except Exception as e:  # noqa: BLE001 - 状态页要显示失败原因
            with contextlib.suppress(Exception):
                self.query_one("#toolset-body", Static).update(f"检查失败: {e}")
            return
        with contextlib.suppress(Exception):
            self.query_one("#toolset-body", Static).update(_toolset_user_text(report))

    def action_close(self) -> None:
        self.dismiss(None)
