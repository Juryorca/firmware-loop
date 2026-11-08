"""策略层:工具权限、参数校验、执行边界。

原则
- **默认拒绝高风险**:没有 confirm 回调时,高风险工具返回 `cancelled`,而不是悄悄放行。
- 参数必须先过 JSON Schema 的**结构校验**(必填/类型/枚举),不合格直接 `error`,不进 handler。
- 文件类参数必须落在工作区内(防 `../../etc/passwd`);超时受上限约束(防借工具卡死).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

from .domain import DomainError


@dataclass
class Authz:
    """可撤销、带范围与时限的研究授权。

    设计意图:**在研究工作区内的分析操作默认允许**(否则每步都要人确认 ⇒ 谈不上自主研究);
    外部网络 / 真实登录态 / 公开提交这类越界动作仍然必须人类确认。
    """

    roots: list[str] = field(default_factory=list)
    allow_shell: bool = True
    allow_high_risk: bool = True
    # MCP 会把外部世界接进来 ⇒ 是否允许由授权显式控制(默认允许"授权内",但可关)
    allow_mcp: bool = True
    # 外部动作(MCP 服务器未声明 external=false 时)是否在**授权内**自主执行。
    # 默认 False ⇒ 需要 confirm;显式授权为 True ⇒ 授权窗口内不再逐次打断(仍留审计事件)
    allow_external_mcp: bool = False
    expires_at: float | None = None
    revoked: bool = False
    reason: str = ""
    # 诚实标注:**本层只做授权判断与路径检查,不提供文件系统/网络沙箱**
    isolation: str = "none"      # none | chroot | container | bwrap(当前实现只有 none)

    def active(self, now: float | None = None) -> bool:
        import time as _t

        if self.revoked:
            return False
        if self.expires_at is not None and (_t.time() > self.expires_at):
            return False
        return True

    def describe(self) -> dict:
        return {"roots": list(self.roots), "allow_shell": self.allow_shell,
                "allow_high_risk": self.allow_high_risk, "active": self.active(),
                "isolation": self.isolation,
                "note": ("仅授权与路径检查;进程仍可访问该 root 之外的文件/网络"
                         if self.isolation == "none" else "外部隔离层由调用方提供")}

    def covers(self, path: str) -> bool:
        p = os.path.realpath(path)
        return any(p == os.path.realpath(r) or p.startswith(os.path.realpath(r) + os.sep) for r in self.roots)


@dataclass
class Policy:
    allow_high_risk: bool = False
    authorization: Authz | None = None
    confirm: "Callable[[str], bool] | None" = None
    max_timeout: float = 120.0
    sandbox_roots: list[str] = field(default_factory=list)
    require_path_within: bool = True
    # 框架自己生成的完整工具输出在系统临时目录；这些文件必须能被 fs_read/fs_hash
    # 读回，否则 full_output_path 只是摆设。只放行带框架前缀的临时文件，不放开整个 /tmp。
    allow_framework_temp_outputs: bool = True

    @staticmethod
    def is_framework_temp_output(path: str) -> bool:
        full = os.path.realpath(path)
        tmp = os.path.realpath(tempfile.gettempdir())
        if not full.startswith(tmp + os.sep):
            return False
        name = os.path.basename(full)
        return name.startswith(("fwcore-output", "fwcore-stdout", "fwcore-stderr",
                                "fwcore-mcp", "fwcore-structured", "fwcore-observation"))

    def decide(self, tool: str, risk: str, args: dict, *, external: bool = False,
               requires: str | None = None) -> tuple[bool, str]:
        """返回 (allowed, reason)。

        优先级:授权失效 ⇒ 拒;外部动作 ⇒ 必须 confirm;高风险 ⇒ 授权内允许,否则拒。
        """
        az = self.authorization
        if az is not None and not az.active():
            return False, f"研究授权已失效/被撤销({az.reason or 'expired'})"
        if requires == "mcp":
            # MCP 是"把外部世界接进来":必须在授权里显式允许,不能靠工具名猜
            if az is None:
                return False, ("MCP 工具需要研究授权:"
                               "`fw-core authz grant <target> --roots <dir>`")
            if not getattr(az, "allow_mcp", False):
                return False, "研究授权未允许 MCP(allow_mcp=false)⇒ 拒绝调用 MCP 工具"
        if external:
            if requires == "mcp" and az is not None and az.allow_external_mcp:
                return True, ""              # 授权里显式允许"授权范围内的外部 MCP 动作"
            if self.confirm is None:
                return False, "外部动作(网络/登录态/公开提交)需要人类确认"
            try:
                if not self.confirm(f"{tool} {args}"):
                    return False, "用户未确认外部动作"
            except Exception as e:
                return False, f"confirm 异常: {e}"
            return True, ""
        if az is not None and az.allow_high_risk:
            if risk == "high" and az.allow_shell:
                return True, ""              # 授权范围内自主运行分析工具
        if risk == "high":
            if self.authorization is None and self.confirm is None and not self.allow_high_risk:
                # 可发现性:没有授权文件时要告诉用户**怎么授权**(而不是只说"被拒")
                return False, ("未授权:需要研究授权(例如 `fw-core authz grant <target> --roots <dir>`)"
                               "或显式确认(confirm) —— 默认拒绝高风险工具")
            if self.confirm is None and not self.allow_high_risk:
                return False, "高风险工具需要人类确认(未配置 confirm,默认拒绝)"
            if self.confirm is not None:
                try:
                    if not self.confirm(f"{tool} {args}"):
                        return False, "用户未确认"
                except Exception as e:  # confirm 自己坏了也不能放行
                    return False, f"confirm 异常: {e}"
        return True, ""

    def check_args(self, tool: str, schema: dict, args: dict) -> str | None:
        """最小 JSON Schema 校验:type/required/enum/pattern。返回错误描述或 None。"""
        if not isinstance(args, dict):
            return "参数必须是对象"
        req = schema.get("required") or []
        missing = [k for k in req if k not in args]
        if missing:
            return f"缺必填参数: {missing}"
        props = schema.get("properties") or {}
        for k, spec in props.items():
            if k not in args:
                continue
            v = args[k]
            t = spec.get("type")
            if t == "string" and not isinstance(v, str):
                return f"参数 {k} 应为 string"
            if t == "number" and not isinstance(v, (int, float)):
                return f"参数 {k} 应为 number"
            if t == "object" and not isinstance(v, dict):
                return f"参数 {k} 应为 object"
            if t == "array" and not isinstance(v, list):
                return f"参数 {k} 应为 array"
            if spec.get("enum") and v not in spec["enum"]:
                return f"参数 {k} 必须是 {spec['enum']} 之一,收到 {v!r}"
        extra = [k for k in args if k not in props] if props else []
        if extra:
            return f"未知参数: {extra}"
        return None

    def clamp_timeout(self, t: float | None) -> float:
        if t is None:
            return self.max_timeout
        try:
            t = float(t)
        except (TypeError, ValueError):
            return self.max_timeout
        return max(0.1, min(t, self.max_timeout))

    def resolve_path(self, root: str, p: str, roots: list[str] | None = None) -> str:
        """把参数路径解析为绝对路径,并确认在允许的根内。"""
        if not isinstance(p, str) or not p:
            raise DomainError("路径参数非法")
        full = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
        allowed = [os.path.realpath(r) for r in (roots or self.sandbox_roots or [root])]
        if (self.require_path_within
                and not any(full == a or full.startswith(a + os.sep) for a in allowed)
                and not (self.allow_framework_temp_outputs
                         and self.is_framework_temp_output(full))):
            raise DomainError(f"路径越界(不在允许的根内): {full}")
        return full


class DirectPolicy(Policy):
    """本地交互会话的固定策略：全部 approve（没有可切换的设置项）。

    用于 fwapp/TUI，避免把“是否审核”再做成一个配置开关；CLI/测试仍可使用
    默认 Policy 的授权门。
    """

    def decide(self, tool: str, risk: str, args: dict, *, external: bool = False,
               requires: str | None = None) -> tuple[bool, str]:
        return True, ""
