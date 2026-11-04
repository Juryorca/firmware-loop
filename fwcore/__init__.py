"""fwcore —— 固件研究 Agent Framework 的核心(宿主无关)。

分层(见 docs/ARCHITECTURE.md):
  db/domain   权威状态与不变量(程序唯一写入口)
  tools       开放工具注册 + 真实执行状态
  runtime     模型↔工具循环、上下文、检查点与恢复
  migrate     历史材料迁移(可验证、不丢信息)
  cli         独立入口(`fw-core …`,不需要 Pi)
"""

__version__ = "0.3.0"
