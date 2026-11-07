"""能力/Provider 层:状态区分必须诚实(架构不符/依赖缺失不能报 ready)。"""

from __future__ import annotations

import os

from fwcore.providers import (
    ARCH_MISMATCH,
    READY,
    Capability,
    Probe,
    Provider,
    inventory,
    qemu_provider,
)


def _p(name, status, arch=None):
    return Provider(id=name, kind="test", resolve=dict, probe=lambda: Probe(status, arch=arch))


def test_best_prefers_ready_and_skips_arch_mismatch():
    cap = Capability("c", "d", [_p("bad-arch", ARCH_MISMATCH, arch="x86_64"), _p("good", READY, arch="aarch64")],
                     target_arch="aarch64")
    best, probe = cap.best()
    assert best is not None and probe is not None
    assert best.id == "good" and probe.status == READY


def test_best_when_only_mismatch_returns_that():
    cap = Capability("c", "d", [_p("x86", READY, arch="x86_64")], target_arch="aarch64")
    _best, probe = cap.best()
    assert probe is not None
    assert probe.status == ARCH_MISMATCH, "只有 x86_64 tracer 时不能宣告 aarch64 fuzz 可用"


def test_partial_beats_missing():
    cap = Capability("c", "d", [_p("m", "missing"), _p("p", "partial")])
    best, _probe = cap.best()
    assert best is not None and best.id == "p"


def test_qemu_netlink_semantics_are_not_interchangeable():
    """改版 QEMU 与上游 QEMU 必须是不同的能力状态(不能静默互换)。"""
    patched = qemu_provider("qemu-aarch64-patched", netlink=True)
    upstream = qemu_provider("qemu-aarch64", netlink=False)
    pr_p, pr_u = patched.probe(), upstream.probe()
    if pr_p.status == "missing" or pr_u.status == "missing":
        return  # 本机没装就不硬断言
    assert pr_p.status == READY
    assert pr_u.status == "partial" and "NETLINK" in pr_u.detail


def test_inventory_runs_and_reports_states(tmp_path):
    inv = inventory(str(tmp_path))
    assert inv, "至少要有能力清单"
    for e in inv:
        assert e["best_status"] in ("ready", "partial", "deps_missing", "not_started", "arch_mismatch", "missing")
        for p in e["providers"]:
            assert p["status"] in ("ready", "partial", "deps_missing", "not_started", "arch_mismatch", "missing")


def test_toolmeta_reads_explicit_runtime_only(tmp_path):
    from fwcore import toolmeta
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"))
    with open(os.path.join(root, "state", "tools.json"), "w", encoding="utf-8") as f:
        f.write('{"unblob": {"path": "/bin/true", "python": "/usr/bin/python3", "cwd": "."}}')
    assert toolmeta.path(root, "unblob") == "/bin/true"
    rt = toolmeta.runtime(root, "unblob")
    assert rt["python"] == "/usr/bin/python3"
    assert toolmeta.path(root, "angr") is None
    assert toolmeta.load(root)["unblob"]["path"] == "/bin/true"


def test_toolmeta_keeps_env_and_workdir(tmp_path):
    from fwcore import toolmeta
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"))
    with open(os.path.join(root, "state", "tools.json"), "w", encoding="utf-8") as f:
        f.write('{"unblob": {"path": "/bin/true", "cwd": "work", "env": {"A": "B"}}}')
    rt = toolmeta.runtime(root, "unblob")
    assert rt["cwd"] == os.path.join(root, "work")
    assert rt["env"] == {"A": "B"}


def test_toolmeta_missing_config_is_empty(tmp_path):
    from fwcore import toolmeta
    assert toolmeta.load(str(tmp_path)) == {}
    assert toolmeta.runtime(str(tmp_path), "unblob") == {}
    assert toolmeta.path(str(tmp_path), "unblob") is None
