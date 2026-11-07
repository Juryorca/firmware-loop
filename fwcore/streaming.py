"""流式事件协议与取消控制(对应 Pi AssistantMessageEventStream 的 Python 版)。

设计边界:
- **适配器**只产生事件(`StreamEvent`),不执行工具、不写数据库;
- **Runner** 消费事件并组装完整 Reply,工具调用只在 `done` 后执行;
- **控制**(`StreamControl`)只负责"取消时关闭网络请求",不碰研究状态。
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field


@dataclass
class StreamEvent:
    type: str                       # start / text_delta / reasoning_delta /
                                    # toolcall_start / toolcall_delta / toolcall_end /
                                    # done / error
    text: str = ""
    index: int = 0
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""
    status: str = ""
    invalid: bool = False


class StreamCancelled(RuntimeError):
    """用户/看护取消:不是模型错误,按暂停处理。"""


class StreamError(RuntimeError):
    """流式协议/连接/服务端错误(可能带部分内容)。"""


class StreamControl:
    """一次模型请求的取消令牌。

    `cancel()` 可以在**另一个线程**调用:
    1. 置位 event(让流循环在下一个事件处停止);
    2. 调用已注册的 closer(`response.close()`/`conn.close()`),把阻塞的 read 打断。
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closers: list = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            closers, self._closers = list(self._closers), []
        for closer in closers:
            with contextlib.suppress(Exception):
                closer()

    def bind(self, closer) -> None:
        """注册关闭函数;已经取消则立刻关闭(避免竞态窗口)。"""
        with self._lock:
            if not self._event.is_set():
                self._closers.append(closer)
                return
        with contextlib.suppress(Exception):
            closer()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise StreamCancelled("请求已取消")


def as_event(raw) -> StreamEvent:
    """允许适配器直接 yield dict(自定义适配器/测试),统一收敛。"""
    if isinstance(raw, StreamEvent):
        return raw
    if isinstance(raw, dict):
        return StreamEvent(**raw)
    raise TypeError(f"流式适配器返回了不支持的事件: {type(raw)}")
