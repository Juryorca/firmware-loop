"""IDA 脚本级验证:配置替换、含空格路径、跨盘符 worker 匹配、会话退出清理语义。

全部用**模拟环境**(假 taskkill.exe / 假 pid),不启动真实 IDA。
"""

from __future__ import annotations

import json
import os
import subprocess
import time


def _env(**kw):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in kw.items()})
    return env


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_script_reads_state_config_when_env_absent(tmp_path):
    """配置替换:没有 FW_IDA_* 时,脚本必须用 state/ida.json(而不是默认值)。"""
    cfg = tmp_path / "ida.json"
    stage = tmp_path / "stage from json"
    cfg.write_text(json.dumps({"values": {
        "IDA_STAGE": str(stage), "IDA_WIN_STAGE": r"E:\stage from json",
        "IDA_URL": "http://127.0.0.1:9999/mcp"}}), encoding="utf-8")
    out = subprocess.run(["bash", "-c",
                          ('source tools/ida_env.sh && printf "%s|%s|%s" '
                          '"$FW_IDA_STAGE" "$FW_IDA_WIN_STAGE" "$FW_IDA_URL"')],
                         cwd=".", env=_env(FW_IDA_CONFIG=str(cfg)),
                         capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("|") == [str(stage), r"E:\stage from json",
                                     "http://127.0.0.1:9999/mcp"]


def test_stop_works_with_spaces_in_stage_path(tmp_path):
    stage = tmp_path / "ida stage with spaces"
    stage.mkdir()
    inst = tmp_path / "instances"
    inst.mkdir()
    sup = subprocess.Popen(["sleep", "30"])
    try:
        (stage / "supervisor.pid").write_text(str(sup.pid), encoding="utf-8")
        out = subprocess.run(["bash", "tools/ida_stop.sh"], env=_env(
            FW_IDA_STAGE=str(stage), FW_IDA_WIN_STAGE=r"E:\stage with spaces",
            FW_IDA_URL="http://127.0.0.1:9/mcp", FW_IDA_INSTANCES=str(inst)),
            capture_output=True, text=True, timeout=120, check=False)
        assert out.returncode == 0, out.stderr
        try:
            sup.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise AssertionError("含空格的暂存目录下 supervisor 未被停掉") from None
        assert not (stage / "supervisor.pid").exists()
    finally:
        if sup.poll() is None:
            sup.kill()


def test_stop_matches_workers_across_drive_with_mount_override(tmp_path):
    """跨盘符:仅 E: 挂载到 /mnt/winE;只应处理本暂存目录的 worker。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    killed = tmp_path / "killed.txt"
    stub = bindir / "taskkill.exe"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{killed}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o700)

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "supervisor.pid").write_text("999999", encoding="utf-8")   # 不存在的 pid
    inst = tmp_path / "instances"
    inst.mkdir()
    (inst / "ours.json").write_text(json.dumps(
        {"pid": 11111, "backend": "worker", "idb_path": r"E:\ida stage\x.i64"}), encoding="utf-8")
    (inst / "theirs.json").write_text(json.dumps(
        {"pid": 22222, "backend": "worker", "idb_path": r"D:\other\y.i64"}), encoding="utf-8")

    env = _env(FW_IDA_STAGE=str(stage), FW_IDA_WIN_STAGE=r"E:\ida stage",
               FW_IDA_URL="http://127.0.0.1:9/mcp", FW_IDA_INSTANCES=str(inst),
               FW_IDA_MOUNT_E="/mnt/winE", PATH=f"{bindir}:{os.environ['PATH']}")
    out = subprocess.run(["bash", "tools/ida_stop.sh"], env=env,
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr
    calls = killed.read_text(encoding="utf-8")
    assert "11111" in calls, f"本暂存目录的 worker 应被停: {calls}"
    assert "22222" not in calls, f"别的盘符/实例不能被动: {calls}"
    assert not (inst / "ours.json").exists()
    assert (inst / "theirs.json").exists()


def test_session_exit_clean_removes_staged_copies_but_keeps_whitelist(tmp_path):
    """既定语义:会话退出(ida_clean)清暂存副本(含 IDB),保留管线白名单。"""
    stage = tmp_path / "stage"
    (stage / "submit").mkdir(parents=True)
    for name in ("fw.bin", "fw.i64", "fw.id0", "keepalive.py", "idalog.txt"):
        (stage / name).write_text("x", encoding="utf-8")
    (stage / "submit" / "pkg").write_text("x", encoding="utf-8")
    out = subprocess.run(["bash", "tools/ida_clean.sh"], env=_env(
        FW_IDA_STAGE=str(stage), FW_IDA_WIN_STAGE=r"D:\stage",
        FW_IDA_URL="http://127.0.0.1:9/mcp"), capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr
    remaining = sorted(os.listdir(stage))
    assert remaining == ["idalog.txt", "keepalive.py"], remaining
    assert "removed: fw.i64" in out.stdout


def test_stop_preserves_idb_as_files(tmp_path):
    """stop 只停进程,不删 IDB(可能还有别的会话要用)。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "fw.i64").write_text("db", encoding="utf-8")
    (stage / "supervisor.pid").write_text("999999", encoding="utf-8")
    out = subprocess.run(["bash", "tools/ida_stop.sh"], env=_env(
        FW_IDA_STAGE=str(stage), FW_IDA_WIN_STAGE=r"D:\stage",
        FW_IDA_URL="http://127.0.0.1:9/mcp", FW_IDA_INSTANCES=str(tmp_path / "none")),
        capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr
    assert (stage / "fw.i64").exists(), "stop 不得删 IDB(与 ida_clean 分工不同)"
