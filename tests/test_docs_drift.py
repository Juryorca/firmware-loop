"""文档里的"可核对事实"必须与代码一致 —— 防止 schema 版本/测试数再次漂移。

背景:2026-11 一次收尾后 ARCHITECTURE 还写着 SCHEMA v13 / pytest 57 文件,
而代码已是 v14 / 62 文件。这里只钉"能从代码算出来的数字",不评价文档内容。
"""

from __future__ import annotations

import glob
import os

from fwcore.db import SCHEMA_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_schema_version_in_docs():
    arch = _read("docs/ARCHITECTURE.md")
    road = _read("docs/ROADMAP.md")
    assert f"SCHEMA v{SCHEMA_VERSION}" in arch, "ARCHITECTURE 的 schema 版本过期"
    assert f"schema v{SCHEMA_VERSION}" in road, "ROADMAP 的 schema 版本过期"


def test_test_file_count_in_docs():
    n = len(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    arch = _read("docs/ARCHITECTURE.md")
    road = _read("docs/ROADMAP.md")
    assert f"pytest {n} 文件" in arch, f"ARCHITECTURE 的测试文件数不是 {n}"
    assert f"pytest({n} 文件)" in road, f"ROADMAP 的测试文件数不是 {n}"


def test_verify_step_list_matches_script():
    """verify 的关键步骤名必须出现在文档的验证清单里(步骤增删要同步文档)。"""
    script = _read("tools/fwcore_verify.sh")
    road = _read("docs/ROADMAP.md")
    steps = [s for s in ("运行产物边界", "迁移一致性/幂等") if s in script]
    for s in steps:
        assert s in road, f"verify 有步骤「{s}」但 ROADMAP 验证清单没写"


def test_command_registry_is_documented():
    from fwcore.commands import COMMANDS
    for rel in ("docs/APP.md", "docs/AGENT_ARCHITECTURE.md"):
        text = _read(rel)
        missing = [c.name for c in COMMANDS if f"/{c.name}" not in text]
        assert not missing, f"{rel} 缺少命令文档: {missing}"
