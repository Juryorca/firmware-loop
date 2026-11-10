"""全局测试保护:任何用例超时即失败(防止再出现"跑不完"的失控用例)。

实现:autouse fixture 用 signal.alarm 给每个测试套 20s 硬超时(POSIX)。
这样即使某个用例逻辑写错(比如模型永远不 FINAL),也会秒级失败而不是跑 800 秒。
"""

from __future__ import annotations

import signal

import pytest

TIMEOUT_S = 20
# 跨进程/MCP 用例要启子进程、发现 MCP、等锁；并行 verify 下 20s 会偶发误杀。
SLOW_MODULES = {"test_cross_process_resume.py": 60}


class TestTimedOut(Exception):
    pass


def _on_alarm(signum, frame):
    raise TestTimedOut(f"用例超过 {TIMEOUT_S}s 未结束(执行边界保护)")


@pytest.fixture(autouse=True)
def _hard_timeout(request):
    limit = SLOW_MODULES.get(request.node.path.name, TIMEOUT_S)
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(limit)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
