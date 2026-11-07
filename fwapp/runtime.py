"""Application Runtime(对应 Pi 的 AgentSessionRuntime)。

职责:**只做组合与生命周期**,不实现研究逻辑:
- 打开 cwd 绑定的 Services(DB/Domain/Research/Limits/Registry);
- 创建并启动一个 AgentSession(后台 worker);
- 模式切换(target/run/model)委托给 Session;
- 退出时按顺序关闭 Session → Services。
"""

from __future__ import annotations

from .config import AppConfig
from .services import Services, build_services
from .session import AgentSession


class AppRuntime:
    def __init__(self, cfg: AppConfig, *, target: str | None = None,
                 run_id: str | None = None, model: str | None = None,
                 mcp: bool = True, watchdog: bool = True,
                 adapter_factory=None):
        self.cfg = cfg
        self.services: Services = build_services(cfg)
        self.session = AgentSession(
            self.services, target=target, run_id=run_id, model=model,
            adapter_factory=adapter_factory, mcp=mcp, watchdog=watchdog,
        )
        self.session.start()

    def replace_session(self, *, target: str | None, run_id: str | None = None) -> None:
        """切换目标/run(不重建 Services;同一状态库、同一租约)。"""
        if target:
            self.session.set_target(target)
        if run_id:
            self.session.resume_run(run_id)

    def dispose(self) -> None:
        self.session.close()
