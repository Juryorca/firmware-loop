---
name: tool-management
description: 检查/补齐默认工具集、选择 Python 环境、安装或接入新的研究工具。用户触发 /tool-setup，或研究过程中发现工具/依赖缺失时加载。
---

# 工具管理：安装、配置与接入

## 适用场景

- `/tool-setup` 检查默认工具集并补齐缺失项；
- 研究过程中发现外部工具、Python 包、MCP 服务或 Wrapper 不可用；
- 需要接入新的研究工具（CLI/MCP/API）。

## 先检查，不先安装

1. `python3 -m fwcore toolset --json`：看仓库资产、外部依赖、能力、服务状态。
2. `python3 -m fwcore capabilities`：看 provider/架构/依赖的真实探针。
3. 只在确认“确实缺这项能力”后才安装；不要因为状态是 partial 就盲目升级。

## Python 环境（有序）

用户配置只放本地，不进入 Git：

```json
{"python_envs": ["/path/to/python", "/another/venv/bin/python"]}
```

文件：`<root>/state/toolenv.json`；也可用 `FW_PYTHON_ENVS`（路径分隔符分隔）。
`fwcore.pyenvs` 按顺序探测：

```bash
python3 -m fwcore.pyenvs --list
python3 -m fwcore.pyenvs --select angr
```

规则：

- 优先前面的环境；import/版本不满足就跳过，尝试下一个；
- 不为了装一个包而大规模升级/降级现有环境；
- 系统 Python、系统包管理器和 venv 分开判断；
- 都不适合时才建立独立 venv；安装前后记录版本与命令；
- 不静默修改用户已有环境。

## 补齐已有默认工具

1. 读取 toolset 的 `reasons`，区分：仓库资产缺失 / 外部命令缺失 / Python 依赖 / 服务未启动 / 架构不符。
2. 系统包用系统包管理器；Python 依赖优先用已配置环境，冲突则跳过或新建 venv。
3. 安装后重新跑 `fw-core toolset` 和 `capabilities`，再做一次真实无破坏性调用。
4. 不能把“装上命令”当成“能力可用”；需要真实的探针/调用证据。
5. 需要外部账号/付费/可能破坏环境时，先停下请求用户，不要擅自操作。

## 接入新工具

先检查是否已有相同 capability/provider/工具；优先复用原生 CLI/MCP/API。

标准流程：

1. 确认能力、输入、输出、依赖、架构、风险、超时、取消、清理方式；
2. 在 `tools/registry/*.json` 声明 Manifest（显式 argv/Schema）；
3. 在 `fwcore/providers.py` 增加/更新 Provider 探针（实际路径、版本、依赖、架构）；
4. 必要时写 Adapter（生命周期/特殊协议）；普通 CLI 不要额外包复杂 shell；
5. 使用统一 `run_process`，返回 `status`、产物路径、`db_tool_call_id`、来源；
6. 真实调用一次，失败路径也要验证；缺依赖如实报，不伪装 ready；
7. 更新 toolset 定义、测试、AGENTS/架构文档。

## Wrapper 边界

只有生命周期、复杂初始化、安全隔离或多步骤流程才保留独立 Wrapper/Adapter。
`IDA` 是合理特例；`fw_extract`/截图保留必要资源与证据约束。
不要为每个工具创建一层兼容所有历史版本的 shell wrapper。

## QEMU 特殊构建

`qemu-aarch64-patched` 是特殊可选能力：独立路径、独立 Provider、独立验证；
不替换系统 QEMU；调用时显式传 `qemu=` 路径，不能只因为“发现文件”就算接入。

## 禁止

- 不迁移/不改 94 条 tool-quirks；
- 不把仅配置存在当成服务 ready；
- 不把工具执行状态当漏洞结论；
- 不在未授权时安装外部服务/账号依赖；
- 不为“完整度”制造占位工具或伪造调用记录。
