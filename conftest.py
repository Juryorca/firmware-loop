"""全局测试保护:任何用例超时即失败(防止再出现"跑不完"的失控用例)。

实现:autouse fixture 用 signal.alarm 给每个测试套 20s 硬超时(POSIX)。
这样即使某个用例逻辑写错(比如模型永远不 FINAL),也会秒级失败而不是跑 800 秒。
"""

from __future__ import annotations

import signal

import pytest

TIMEOUT_S = 20


class TestTimedOut(Exception):
    pass


def _on_alarm(signum, frame):  # noqa: ARG001
    raise TestTimedOut(f"用例超过 {TIMEOUT_S}s 未结束(执行边界保护)")


@pytest.fixture(autouse=True)
def _hard_timeout():
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(TIMEOUT_S)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
