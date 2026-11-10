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
    ModelInfo,
    build_adapter,
    fetch_model_catalog,
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
    model_catalog: list[ModelInfo] = field(default_factory=list)
    model_catalog_error: str = ""

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
    except Exception as e:                      # 配置坏 ⇒ 用默认值,但必须让人看见  # noqa: BLE001
        diags.append(Diagnostic("warning", f"limits 配置读取失败,使用默认值: {e}"))
    try:
        limits = Limits(**raw)
    except (TypeError, ValueError) as e:
        diags.append(Diagnostic("warning", f"limits 配置非法,使用默认值: {e}"))
        raw, limits = {}, Limits()

    key = base = model = ""
    try:
        key, base, model = load_model_config(root)
    except Exception as e:  # noqa: BLE001
        diags.append(Diagnostic("warning", f"模型配置读取失败: {e}"))
    model_opts = {"api": "", "supports_vision": False, "image_mode": "none"}
    try:
        model_opts = load_model_options(root)
    except Exception as e:  # noqa: BLE001
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
    if cfg.api_key and cfg.base_url and (cfg.model or name):
        # A provider catalog can return any id; don't maintain a hardcoded
        # allowlist.  ``openai``/``deepseek`` are config aliases, not model ids.
        model_id = cfg.model if name in REAL_KINDS else name
        return build_adapter("openai", api_key=cfg.api_key, base_url=cfg.base_url,
                             model=model_id, api=cfg.api,
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


def _format_context(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def model_detail(info: ModelInfo, base_url: str = "") -> str:
    parts = []
    if info.owned_by:
        parts.append(info.owned_by)
    if info.context_window:
        parts.append(f"context {_format_context(info.context_window)}")
    if info.max_output_tokens:
        parts.append(f"max out {_format_context(info.max_output_tokens)}")
    if not parts and base_url:
        parts.append(base_url)
    return " · ".join(parts)


def available_models(cfg: AppConfig, *, refresh: bool = False) -> list[ModelInfo]:
    """Fetch the provider catalog; never hardcode model ids.

    Only ``scripted`` is local.  If discovery fails, keep the configured model
    visible and expose the error through ``cfg.model_catalog_error`` so the
    selector can say why the live list is missing.
    """
    scripted = ModelInfo(id="scripted", label="scripted（演示）", api_id="scripted")
    if not (cfg.api_key and cfg.base_url):
        return [scripted]
    if refresh or not cfg.model_catalog:
        try:
            infos = fetch_model_catalog(cfg.base_url, cfg.api_key)
        except Exception as e:  # noqa: BLE001 - UI must show discovery failure, not crash
            cfg.model_catalog_error = f"{type(e).__name__}: {e}"
            infos = list(cfg.model_catalog)
        else:
            cfg.model_catalog = infos
            cfg.model_catalog_error = ""
    else:
        infos = list(cfg.model_catalog)
    selected = cfg.model or cfg.model_name
    if selected and selected not in {i.api_id for i in infos}:
        infos.append(ModelInfo(id=selected, label=selected, api_id=selected,
                               context_window=cfg.limits.context_tokens))
    return [scripted] + infos
