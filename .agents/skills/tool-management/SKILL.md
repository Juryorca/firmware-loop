---
name: tool-management
description: 检查/补齐默认工具集、配置已有工具的运行时元数据、选择 Agent 自己的 Python 环境、安装或接入新的研究工具。用户触发 /tool-setup，或研究过程中发现工具/依赖缺失时加载。
---

# 工具管理：配置、安装与接入

## 先检查，不先安装

1. `python3 -m fwcore toolset --json`：看默认资产、外部依赖、能力与服务状态。
2. `python3 -m fwcore capabilities`：看 provider、架构、依赖的真实探针。
3. `python3 -m fwcore.toolmeta`：看已有工具的本地元数据与实际路径。
4. 只在确认“确实缺这项能力”后才安装；不要因为状态是 `partial` 就盲目升级。

## 两类配置不要混用

### 1. 已有工具：运行时元数据

已有工具通过 `<root>/state/tools.json` 定位（本地，不进 Git；也可用 `FW_TOOL_CONFIG`）。
每条只放运行所需信息：

```json
{
  "unblob": {
    "path": "/opt/unblob/bin/unblob",
    "python": "/opt/unblob/bin/python",
    "cwd": ".",
    "env": {}
  },
  "angr": {"python": "/opt/angr/bin/python", "cwd": "."},
  "ghidra": {"path": "/opt/ghidra/support/analyzeHeadless"},
  "qemu-aarch64-patched": {"path": "/opt/qemu/bin/qemu-aarch64-patched"}
}
```

- `path`：实际可执行文件；`python`：Python 工具的解释器；`cwd`：工作目录；`env`：额外环境变量。
- 系统命令（binwalk、qemu、strace 等）没有显式元数据时，才允许按 `PATH` 查找。
- 配置了路径但不存在时，必须如实报“配置路径不存在”，不要偷偷换成别的路径。
- 已有工具**不通过** Python 环境队列搜索。

### 2. Python 环境队列：只给 Agent 自己跑代码

配置优先 `state/pyenvs.json`（也兼容旧的 `state/toolenv.json`）：

```json
{"python_envs": ["/path/to/python", "/another/venv/bin/python"]}
```

或 `FW_PYTHON_ENVS`（路径分隔符分隔）。它只解决两件事：

1. Agent 临时运行 Python 代码时选一个能 import 所需模块的解释器；
2. 决定 Python 包安装到哪个环境。

规则：

- 顺序即优先级；缺失/依赖不满足就跳过，尝试下一个；
- 不为了装一个包而大规模升级/降级已有环境；
- 系统 Python、venv、系统包管理器分开判断；
- 都不适合时才建立独立 venv；安装前后记录版本与命令；
- 不把环境队列当工具发现器，也不为每个工具扫描所有环境。

```bash
python3 -m fwcore.pyenvs --list
python3 -m fwcore.pyenvs --select json
```

## 补齐已有默认工具

1. 读 toolset 的 `reasons`，区分：仓库资产缺失 / 外部命令缺失 / Python 依赖 / 服务未启动 / 架构不符。
2. 先看 `state/tools.json` 是否只是路径没配；能配路径就不要安装第二份。
3. 系统包用系统包管理器；Python 包用 Agent 环境队列决定安装位置；冲突则跳过或新建 venv。
4. 安装/配置后重跑 `fw-core toolset`、`capabilities`，再做一次真实无破坏性调用。
5. “装上命令”不等于“能力可用”；要有真实探针/调用证据。
6. 需要外部账号/付费/可能破坏环境时，先停下请求用户。

## 接入新工具

先检查是否已有相同 capability/provider/工具；优先复用原生 CLI/MCP/API。
不要预设用户有复杂多环境、多架构或多执行节点。

标准流程：

1. 确认能力、输入、输出、依赖、架构、风险、超时、取消、清理方式；
2. 在 `tools/registry/*.json` 声明 Manifest（显式 Schema/argv）；
3. 在 `fwcore/providers.py` 增加 Provider 探针；路径优先读 `state/tools.json`；
4. 必要时写 Adapter（生命周期/特殊协议）；普通 CLI 不要额外包 shell；
5. 用统一 `run_process`，返回 `status`、产物路径、来源；
6. 真实调用一次，失败路径也要验证；缺依赖如实报，不伪装 ready；
7. 更新 toolset 定义、测试、AGENTS/架构文档。

## Wrapper 边界

只有**真实特殊问题**才保留 Wrapper/Adapter：Ghidra 的脚本路径补齐、IDA 生命周期、
`fw_extract` 的资源/证据约束、截图证据约束。不要为统一而给每个工具加一层 shell。
普通 CLI 用 Manifest 显式 argv + Provider 路径解析即可。

## 特殊 QEMU

`qemu-aarch64-patched` 是特殊可选能力：独立路径、独立 Provider、独立验证；
不替换系统 QEMU。调用时显式传 `qemu=` 路径，或将其写入 `state/tools.json`；
不能只因为“发现文件”就算接入。

## 禁止

- 不把仅配置存在当成服务 ready；
- 工具经验记录到 SQLite（`kind=guideline`, tags 含 `tool-quirk`），不再写回 `knowledge/tool-quirks.md`；
- 不把工具执行状态当漏洞结论；
- 不在未授权时安装外部服务/账号依赖；
- 不为“完整度”制造占位工具或伪造调用记录；
- 不把 Python 虚拟环境队列当作工具注册表。
