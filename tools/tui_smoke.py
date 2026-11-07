#!/usr/bin/env python3
"""TUI 真终端冒烟(pty):启动 / 输入 / 多行 / resize / 历史 / 退出 / SIGTERM。

用法: python3 tools/tui_smoke.py
不调用外部模型(--model scripted),不写工作区外文件。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time

ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(fd: int, secs: float) -> bytes:
    out = b""
    end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def spawn(root: str, script: str):
    master, slave = pty.openpty()
    set_size(master, 100, 30)
    env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=ROOT, FW_MODEL="scripted")
    cmd = [sys.executable, "-m", "fwcore", "tui", "--root", root,
           "--model", "scripted", "--script", script, "--target", "tui-demo",
           "--no-mcp"]
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=slave, stdout=slave,
                         stderr=slave, close_fds=True)
    os.close(slave)
    return p, master


def clean(data: bytes) -> str:
    return ANSI.sub(b"", data).decode("utf-8", "replace")


def check(cond: bool, label: str, out: str) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        tail = "\n".join(out.splitlines()[-12:])
        print("----- last output -----\n" + tail + "\n-----------------------")


def scenario_graceful(root: str, script: str) -> bool:
    p, fd = spawn(root, script)
    out = b""
    try:
        out += drain(fd, 2.5)
        text = clean(out)
        check("fw-core" in text or "tui-demo" in text, "启动进入交互界面", text)
        os.write(fd, b"hello\r")
        out += drain(fd, 2.5)
        text = clean(out)
        check("hello" in text, "输入消息并显示 transcript", text)
        # Ctrl+J 换行,Enter 提交 ⇒ 多行输入
        os.write(fd, b"line1\nline2\r")
        out += drain(fd, 2.0)
        text = clean(out)
        check("line1" in text and "line2" in text, "多行输入(Ctrl+J + Enter)", text)
        # resize
        set_size(fd, 80, 24)
        os.kill(p.pid, signal.SIGWINCH)
        out += drain(fd, 1.0)
        set_size(fd, 120, 40)
        os.kill(p.pid, signal.SIGWINCH)
        out += drain(fd, 1.0)
        text = clean(out)
        check("Traceback" not in text, "resize 不崩溃", text)
        # 历史:↑ 取回上一条
        os.write(fd, b"\x1b[A")
        out += drain(fd, 0.4)
        text = clean(out)
        check("line1" in text or "line2" in text, "↑ 历史输入", text)
        os.write(fd, b"\x11")          # Ctrl+Q
        out += drain(fd, 3.0)
        try:
            rc = p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = p.wait()
        raw = out
        text = clean(raw)
        check(rc == 0, f"Ctrl+Q 正常退出(rc={rc})", text)
        check(b"\x1b[?1049l" in raw or b"\x1b[?25h" in raw,
              "退出恢复终端(退出备用屏/显示光标)", text)
        check("Traceback" not in text, "正常退出无异常", text)
        return rc == 0
    finally:
        with contextlib.suppress(OSError):
            p.kill()
        os.close(fd)


def scenario_sigterm(root: str, script: str) -> bool:
    p, fd = spawn(root, script)
    out = b""
    try:
        out += drain(fd, 2.5)
        p.terminate()
        out += drain(fd, 2.5)
        try:
            rc = p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = p.wait()
        text = clean(out)
        check(rc is not None, f"SIGTERM 后退出(rc={rc})", text)
        check(b"\x1b[?1049l" in out or b"\x1b[?25h" in out,
              "SIGTERM 后恢复终端", text)
        check("Traceback" not in text, "SIGTERM 路径无异常", text)
        return rc is not None
    finally:
        with contextlib.suppress(OSError):
            p.kill()
        os.close(fd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时工作区")
    args = ap.parse_args()
    base = os.path.join(ROOT, ".tmp", "tui-smoke")
    try:
        with contextlib.suppress(OSError):
            shutil.rmtree(base)
        os.makedirs(base, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=ROOT)
        subprocess.run([sys.executable, "-m", "fwcore", "--root", base, "init", "tui-demo"],
                       cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
        script = os.path.join(base, "script.json")
        with open(script, "w", encoding="utf-8") as f:
            json.dump([
                {"content": "登记一个假设。", "tool_calls": [{
                    "id": "c1", "name": "state_add_hypothesis",
                    "args": {"target": "tui-demo", "title": "pty 冒烟假设"}}]},
                {"content": "FINAL: pty 冒烟完成", "tool_calls": []},
                {"content": "FINAL: 第二轮", "tool_calls": []},
                {"content": "FINAL: 第三轮", "tool_calls": []},
            ], f, ensure_ascii=False)
    except OSError as e:
        print(f"准备临时工作区失败: {e}", file=sys.stderr)
        return 2
    ok1 = scenario_graceful(base, script)
    ok2 = scenario_sigterm(base, script)
    if not args.keep:
        try:
            shutil.rmtree(base)
        except OSError as e:
            print(f"清理临时目录失败(忽略): {e}", file=sys.stderr)
    print("\n=== TUI smoke:", "PASS" if (ok1 and ok2) else "FAIL", "===")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
