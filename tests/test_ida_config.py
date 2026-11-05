"""Windows/IDA 接入配置:单一来源、优先级、路径转换、脚本不再写死、Adapter 报错准确。

用**模拟的 Windows 路径/盘符/含空格目录**验证,不启动真实 IDA。
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.policy import Authz, Policy
from fwcore.toolreg import register_feature, register_manifests
from fwcore.tools import Registry, ToolContext
from fwcore.winenv import DEFAULTS, load


def test_env_overrides_config_file_and_defaults(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    with open(os.path.join(root, "state", "ida.json"), "w", encoding="utf-8") as f:
        json.dump({"values": {"IDA_WIN_STAGE": r"E:\stage from file",
                              "IDA_STAGE": "/mnt/e/stage from file"}}, f)
    # 文件生效
    c = load(root, env={})
    assert c.win_stage == r"E:\stage from file" and c.stage == "/mnt/e/stage from file"
    # 环境变量压过文件
    c2 = load(root, env={"FW_IDA_WIN_STAGE": r"F:\stage from env",
                         "FW_IDA_STAGE": "/mnt/f/stage from env"})
    assert c2.win_stage == r"F:\stage from env" and c2.source == "env"
    # 都没给 ⇒ 兼容默认值(可被覆盖,不是唯一实现)
    c3 = load(None, env={})
    assert c3.stage == DEFAULTS["IDA_STAGE"]


def test_windows_wsl_path_conversion_handles_drive_and_spaces(tmp_path):
    c = load(str(tmp_path), env={"FW_IDA_MOUNT_E": "/mnt/winE"})
    assert c.windows_to_wsl(r"E:\ida stage\a b.exe") == "/mnt/winE/ida stage/a b.exe"
    assert c.wsl_to_windows("/mnt/winE/ida stage/a b.exe") == r"E:\ida stage\a b.exe"
    # 默认盘符挂载
    assert c.windows_to_wsl(r"D:\x\y") == "/mnt/d/x/y"
    assert c.wsl_to_windows("/mnt/d/x/y") == r"D:\x\y"
    # 无盘符/UNC:原样(交给上层报错),不瞎猜
    assert c.windows_to_wsl(r"\\server\share\x") == r"\\server\share\x"


def test_shell_exports_quote_spaces_and_roundtrip(tmp_path):
    c = load(str(tmp_path), env={"FW_IDA_STAGE": "/mnt/e/ida stage",
                                 "FW_IDA_WIN_STAGE": r"E:\ida stage",
                                 "FW_IDA_MCP_EXE": r"E:\tools dir\idalib-mcp.exe",
                                 "FW_IDA_URL": "http://127.0.0.1:9911/mcp"})
    exports = c.shell_exports()
    script = exports + '\nprintf "%s\\n" "$FW_IDA_STAGE|$FW_IDA_WIN_STAGE|$FW_IDA_URL|$FW_IDA_MCP_EXE"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    stage, win_stage, url, exe = out.stdout.strip().split("|")
    assert stage == "/mnt/e/ida stage"
    assert win_stage == r"E:\ida stage"
    assert url == "http://127.0.0.1:9911/mcp"
    # 真实断言:Windows 路径必须被转换成默认盘符挂载下的 WSL 路径(含空格不拆)
    assert exe == "/mnt/e/tools dir/idalib-mcp.exe", exe
    assert " " in stage and " " in win_stage


def test_problems_name_the_missing_thing(tmp_path):
    c = load(str(tmp_path), env={"FW_IDA_STAGE": "/nonexistent/stage",
                                 "FW_IDA_MCP_EXE": r"Z:\no\such\idalib-mcp.exe"})
    problems = c.problems()
    assert any("idalib-mcp" in p for p in problems), problems
    assert any("暂存目录" in p for p in problems), problems


def test_scripts_have_no_hardcoded_paths_or_ports():
    """脚本必须用配置;不允许再出现开发机的路径/端口字面量。"""
    files = [f for f in os.listdir("tools") if f.startswith("ida_") and f.endswith(".sh")]
    assert files, "没找到 ida_*.sh"
    bad = []
    for name in files:
        with open(os.path.join("tools", name), encoding="utf-8") as f:
            text = f.read()
        for needle in ("/mnt/d/", "Users/30104", "python313", ":8745"):
            if needle in text:
                bad.append(f"{name}: {needle}")
    assert not bad, bad


def test_stop_only_kills_own_supervisor_and_matching_workers(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    inst = tmp_path / "instances"
    inst.mkdir()
    sup = subprocess.Popen(["sleep", "30"])
    other = subprocess.Popen(["sleep", "30"])
    try:
        (stage / "supervisor.pid").write_text(str(sup.pid), encoding="utf-8")
        (inst / "ours.json").write_text(json.dumps(
            {"pid": other.pid, "backend": "worker", "idb_path": r"D:\ida_test\x.i64"}),
            encoding="utf-8")
        (inst / "theirs.json").write_text(json.dumps(
            {"pid": other.pid, "backend": "worker", "idb_path": r"E:\other\y.i64"}),
            encoding="utf-8")
        env = dict(os.environ, FW_IDA_STAGE=str(stage), FW_IDA_WIN_STAGE=r"D:\ida_test",
                   FW_IDA_URL="http://127.0.0.1:9/mcp", FW_IDA_INSTANCES=str(inst),
                   FW_IDA_WSL_STAGE=str(stage))
        out = subprocess.run(["bash", "tools/ida_stop.sh"], env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        # 轮询而不是固定 sleep:Windows 互操作(taskkill.exe)可能慢,固定等待会 flaky
        deadline = time.time() + 10
        while time.time() < deadline and sup.poll() is None:
            time.sleep(0.1)
        assert sup.returncode is not None, "本环境的 supervisor 应被停掉"
        # 其它环境的 worker 注册不能被动
        assert (inst / "theirs.json").exists(), "不允许动其它实例的注册"
    finally:
        for p in (sup, other):
            if p.poll() is None:
                p.kill()


def test_adapter_reports_config_problem_not_analysis_failure(tmp_path):
    from fwcore.adapters_ida import IdAdapter

    con = _db.connect(os.path.join(str(tmp_path), "state", "fwcore.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="1")
    ctx = ToolContext(domain=dom, root=str(tmp_path),
                      policy=Policy(authorization=Authz(roots=[str(tmp_path)]), allow_high_risk=True))
    a = IdAdapter(str(tmp_path))
    a.cfg = load(str(tmp_path), env={"FW_IDA_STAGE": "/nonexistent/stage",
                                      "FW_IDA_MCP_EXE": r"Z:\no\such\exe.exe"})
    a.cfg.problems()          # 触发一次
    out = a.ensure(ctx)
    assert out is not None and out["status"] == "error"
    assert "环境配置" in out["error"] and "idalib-mcp" in out["error"]
    assert out["meta"]["config_problems"]


def test_runner_close_invokes_feature_adapter_close(tmp_path):
    """收尾必须真的从 Runner 生命周期走通(而不是只在单测里单独调用)。"""
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    reg_dir = os.path.join(root, "tools", "registry")
    os.makedirs(reg_dir, exist_ok=True)
    with open(os.path.join(reg_dir, "feat.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "feat_tool", "description": "d",
                   "input_schema": {"type": "object"}, "kind": "feature",
                   "adapter": "closer"}, f)
    closed: list[int] = []

    from fwcore.toolreg import Adapter

    class Closer(Adapter):
        def ensure(self, ctx):
            return None

        def call(self, ctx, m, args):
            return {"status": "ok"}

        def close(self):
            closed.append(1)

    register_feature("closer", lambda r: Closer())
    reg = Registry()
    register_manifests(reg, root, dirs=[reg_dir])
    r = Runner(con, root, ScriptedAdapter(script=[]), registry=reg,
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))
    r.close()
    r.close()
    assert closed, "Runner.close 必须调用适配器 close"
    assert len(closed) == 1, "重复 close 必须幂等"
