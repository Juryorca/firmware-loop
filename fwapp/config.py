"""应用配置:把 CLI 参数 / 环境 / `state/model.json` 收敛成一份可核的配置。

与 Pi 的 config.ts 对应。原则:
- 配置错误**报出来**,不静默换模型;
- 不读任何外部 agent 的配置文件;fwcore 自己的契约只有 env + state/model.json。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fwcore import db as _db
from fwcore.limits import Limits
from fwcore.models import (
    build_adapter,
    limits_config,
    load_model_config,
    load_model_options,
)

REAL_KINDS = ("openai", "deepseek", "openai-compat")


class ModelConfigError(RuntimeError):
    """模型配置不完整:应用层展示为启动诊断,不退出整个进程。"""


@dataclass
class Diagnostic:
    level: str          # info / warning / error
    message: str


@dataclass
class AppConfig:
    root: str
    db_path: str
    model_name: str
    limits: Limits
    limits_raw: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    script: str | None = None
    api: str = ""
    supports_vision: bool = False
    image_mode: str = "none"

    @property
    def db_exists(self) -> bool:
        return os.path.exists(self.db_path)


def resolve(opts) -> AppConfig:
    root = os.path.abspath(opts.root or os.getcwd())
    db_path = opts.db or _db.default_db_path(root)
    diags: list[Diagnostic] = []
    if not os.path.exists(db_path):
        diags.append(Diagnostic("info", f"新建状态库: {db_path}"))

    raw: dict = {}
    try:
        raw = limits_config(root)
    except Exception as e:                      # 配置坏 ⇒ 用默认值,但必须让人看见
        diags.append(Diagnostic("warning", f"limits 配置读取失败,使用默认值: {e}"))
    try:
        limits = Limits(**raw)
    except (TypeError, ValueError) as e:
        diags.append(Diagnostic("warning", f"limits 配置非法,使用默认值: {e}"))
        raw, limits = {}, Limits()

    key = base = model = ""
    try:
        key, base, model = load_model_config(root)
    except Exception as e:
        diags.append(Diagnostic("warning", f"模型配置读取失败: {e}"))
    model_opts = {"api": "", "supports_vision": False, "image_mode": "none"}
    try:
        model_opts = load_model_options(root)
    except Exception as e:
        diags.append(Diagnostic("warning", f"模型多模态配置读取失败,使用纯文本: {e}"))

    name = (opts.model or os.environ.get("FW_MODEL_NAME") or model or "scripted").strip()
    if name in REAL_KINDS:
        kind = name
        display = model or name
    elif model and name == model:
        kind, display = "openai", model
    else:
        kind, display = "scripted" if name in ("scripted", "fake", "") else "openai", name
    if kind != "scripted" and not (key and base and model):
        diags.append(Diagnostic(
            "warning",
            "未配置真实模型(设置 FW_MODEL_API_KEY/FW_MODEL_BASE_URL/FW_MODEL_NAME,"
            f"或写 {os.path.join(root, 'state', 'model.json')});当前可用 `--model scripted` 离线演示",
        ))
    cfg = AppConfig(root=root, db_path=db_path, model_name=display if kind != "scripted" else "scripted",
                    limits=limits, limits_raw=raw, diagnostics=diags,
                    api_key=key, base_url=base, model=model, script=opts.script,
                    api=str(model_opts.get("api") or ""),
                    supports_vision=bool(model_opts.get("supports_vision", False)),
                    image_mode=str(model_opts.get("image_mode") or "none"))
    cfg._kind = kind                              # type: ignore[attr-defined]
    return cfg


def adapter_for(cfg: AppConfig, name: str | None = None, script: str | None = None):
    """构造模型适配器。真实模型缺配置时抛 ModelConfigError(由 UI 显示)。"""
    name = (name or cfg.model_name or "scripted").strip()
    if name in ("scripted", "fake"):
        return build_adapter("scripted", script=load_script(script or cfg.script))
    if name in REAL_KINDS or name in (cfg.model, "openai", "deepseek"):
        if not (cfg.api_key and cfg.base_url and cfg.model):
            raise ModelConfigError(
                "模型配置不完整:需要 api_key/base_url/model(环境变量或 state/model.json)")
        return build_adapter("openai", api_key=cfg.api_key, base_url=cfg.base_url,
                             model=cfg.model, api=cfg.api,
                             supports_vision=cfg.supports_vision,
                             image_mode=cfg.image_mode)
    # 任意已知 provider 名但没配置 ⇒ 明确失败,不悄悄退回 scripted
    raise ModelConfigError(f"未知模型: {name}(可用: scripted / {cfg.model or 'openai'})")


def load_script(path: str | None) -> list[dict]:
    if not path:
        return [{"content": "FINAL: scripted 模式(离线演示)。配置 state/model.json 后可用真实模型。",
                 "tool_calls": []}]
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ModelConfigError(f"读脚本失败 {path}: {e}") from e
    if not isinstance(data, list):
        raise ModelConfigError(f"脚本必须是 JSON 数组: {path}")
    return data


def available_models(cfg: AppConfig) -> list[tuple[str, str]]:
    """给 /model 选择器:(名字, 说明)。"""
    out = [("scripted", "演示模式（本地脚本，不调用真实模型）")]
    if cfg.api_key and cfg.base_url and cfg.model:
        out.append((cfg.model, f"OpenAI 兼容端点 · {cfg.base_url}"))
    return out
