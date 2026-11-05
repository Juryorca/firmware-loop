"""fw_extract.sh 的资源保护:真实产物轮询(磁盘/文件数/深度)、整组回收、相对路径。

用**假提取器**(放在临时 PATH 前)验证保护逻辑;不跑真实 binwalk(避免大固件与磁盘风险)。
"""

from __future__ import annotations

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
