"""fw_extract.sh 的资源保护:真实产物轮询(磁盘/文件数/深度)、整组回收、相对路径。

用**假提取器**(放在临时 PATH 前)验证保护逻辑;不跑真实 binwalk(避免大固件与磁盘风险)。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest

SCRIPT = os.path.abspath("tools/fw_extract.sh")


def _stub(bindir, name: str, body: str) -> None:
    os.makedirs(bindir, exist_ok=True)
    p = os.path.join(bindir, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(p, 0o700)


def _env(bindir, **kw):
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    env.update({k: str(v) for k, v in kw.items()})
    return env


def test_relative_firmware_path_survives_cd_to_outdir(tmp_path):
    """旧实现 cd 到 OUT 后用相对固件路径 ⇒ 找不到文件。这里必须用绝对路径。"""
    work = tmp_path / "work"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    _stub(bindir, "binwalk", f'echo "$@" > "{args_file}"\necho "extract-ok"\n')
    out = tmp_path / "out"
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(out), "--no-unblob"],
                       cwd=work, env=_env(bindir), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    seen = args_file.read_text(encoding="utf-8")
    assert str(work / "fw.bin") in seen, f"传给 binwalk 的固件不是绝对路径: {seen}"
    assert "compute" not in r.stdout.lower()


def test_disk_cap_aborts_and_kills_child_group(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    pidfile = tmp_path / "child.pid"
    # 假提取器:写 2MB 文件,然后长睡(证明被整组杀掉)
    _stub(bindir, "binwalk",
          f'echo $$ > "{pidfile}"\nhead -c 2000000 /dev/zero > ./bomb.bin\nsleep 30\n')
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out"), "--no-unblob"],
                       cwd=work, env=_env(bindir, FW_EXTRACT_MAX_MB=1),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 9, (r.returncode, r.stdout[-500:])
    assert "ABORT=disk" in r.stdout, r.stdout[-500:]
    child = int(pidfile.read_text(encoding="utf-8").strip())
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("超限后假提取器仍存活(没有整组回收)")


def test_file_count_cap_aborts(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    _stub(bindir, "binwalk",
          'i=0; while [ $i -lt 200 ]; do echo x > "./f$i"; i=$((i+1)); done; sleep 30\n')
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out"), "--no-unblob"],
                       cwd=work, env=_env(bindir, FW_EXTRACT_MAX_MB=4096,
                                          FW_EXTRACT_MAX_FILES=10),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 9, (r.returncode, r.stdout[-400:])
    assert "ABORT=files" in r.stdout, r.stdout[-400:]


def test_depth_limit_is_passed_to_both_extractors(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    args = tmp_path / "args.txt"
    _stub(bindir, "binwalk", f'echo "bw $@" >> "{args}"\n')
    _stub(bindir, "unblob", f'echo "ub $@" >> "{args}"\n')
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out")],
                       cwd=work, env=_env(bindir, FW_EXTRACT_MAX_DEPTH=4,
                                          FW_UNBLOB=os.path.join(bindir, "unblob")),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    text = args.read_text(encoding="utf-8")
    assert "bw -Me -d 4" in text, text
    assert "ub " in text and "-d 4" in text, text


def test_interrupt_kills_child_group(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    pidfile = tmp_path / "child.pid"
    _stub(bindir, "binwalk", f'echo $$ > "{pidfile}"\nsleep 30\n')
    p = subprocess.Popen(["bash", SCRIPT, "fw.bin", str(tmp_path / "out"), "--no-unblob"],
                         cwd=work, env=_env(bindir), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 20
    while time.time() < deadline and not pidfile.exists():
        time.sleep(0.05)
    assert pidfile.exists(), "假提取器没起来"
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=30)
    child = int(pidfile.read_text(encoding="utf-8").strip())
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("中断后子进程仍存活")


def _run(script_args, tmp_path, bindir=None, **env):
    envs = dict(os.environ)
    if bindir:
        envs["PATH"] = f"{bindir}:/usr/bin:/bin"
    envs.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", SCRIPT, *script_args], env=envs,
                          capture_output=True, text=True, timeout=120)


def test_no_extractor_is_not_success(tmp_path):
    """binwalk 与 unblob 都缺失 ⇒ 不能报成功(未发生提取)。"""
    work = tmp_path / "w"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    only = tmp_path / "only"
    only.mkdir()
    # 只放脚本需要的核心工具,**不放** binwalk/unblob
    for tool in ("sh", "bash", "basename", "dirname", "mkdir", "pwd", "du", "df", "find",
                 "awk", "wc", "sort", "tail", "head", "sleep", "kill", "cat", "printf",
                 "setsid", "env"):
        src = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if src and not (only / tool).exists():
            os.symlink(src, only / tool)
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out")], cwd=str(work),
                       env={**os.environ, "PATH": str(only),
                            "FW_UNBLOB": str(tmp_path / "nope-unblob")},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 4, (r.returncode, r.stdout[-400:], r.stderr[-300:])
    assert "STATUS=no_extractor" in r.stdout


def test_partial_when_one_extractor_missing(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    _stub(bindir, "binwalk", 'echo ok\n')
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out")], cwd=str(work),
                       env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin",
                            "FW_UNBLOB": str(tmp_path / "missing-unblob")},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 6, (r.returncode, r.stdout[-400:])
    assert "STATUS=partial" in r.stdout and "missing_unblob=1" in r.stdout


def test_all_attempted_failed(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    _stub(bindir, "binwalk", 'echo boom >&2\nexit 7\n')
    stub_ub = tmp_path / "unblob"
    stub_ub.write_text("#!/bin/sh\nexit 8\n", encoding="utf-8")
    stub_ub.chmod(0o700)
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out")], cwd=str(work),
                       env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin",
                            "FW_UNBLOB": str(stub_ub)},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 5, (r.returncode, r.stdout[-400:])
    assert "STATUS=failed" in r.stdout


def test_fast_burst_is_caught_by_final_check(tmp_path):
    """突发写入发生在轮询间隙、进程已退出 ⇒ 收尾复核必须判失败(不能报成功)。"""
    work = tmp_path / "w"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    _stub(bindir, "binwalk", 'sleep 0.05\nhead -c 3000000 /dev/zero > ./bomb.bin\nexit 0\n')
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out"), "--no-unblob"],
                       cwd=str(work), env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin",
                                           "FW_EXTRACT_MAX_MB": "1"},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 9, (r.returncode, r.stdout[-500:])
    assert ("ABORT=disk" in r.stdout) or ("ABORT=disk_after" in r.stdout), r.stdout[-500:]


def test_unblob_metadata_env_cwd_are_used(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "fw.bin").write_bytes(b"FW")
    bindir = tmp_path / "bin"
    marker = tmp_path / "unblob-meta.txt"
    _stub(bindir, "binwalk", "echo ok\n")
    fake = tmp_path / "unblob"
    fake.write_text("#!/bin/sh\npwd > \"$MARKER\"\necho \"FOO=$FOO\" >> \"$MARKER\"\n", encoding="utf-8")
    fake.chmod(0o700)
    cfg = tmp_path / "tools.json"
    cfg.write_text(json.dumps({"unblob": {"path": str(fake), "cwd": str(work),
                                          "env": {"FOO": "bar", "MARKER": str(marker)}}}),
                   encoding="utf-8")
    r = subprocess.run(["bash", SCRIPT, "fw.bin", str(tmp_path / "out")],
                       cwd=str(work), env={**os.environ, "PATH": f"{bindir}:/usr/bin:/bin",
                                           "FW_TOOL_CONFIG": str(cfg)},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-300:])
    text = marker.read_text(encoding="utf-8")
    assert str(work) in text
    assert "FOO=bar" in text
