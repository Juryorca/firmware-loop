---
name: tool-management
description: 配置已有工具、用 Bash 直接调用、选择 Agent 自己的 Python 环境、处理原生 MCP/Plugin。用户触发 /tool-setup，或研究过程中发现工具/依赖缺失时加载。
---

# 工具管理：配置、调用与安装

## 原则

- **新增工具 ≠ 新增框架 Tool**。普通 CLI 用 `shell_exec` 直接跑，不新建 Tool manifest/provider/adapter/wrapper。
- 已有 Plugin/MCP 保持原生接入；不转换成 Bash Wrapper。
- 工具路径、解释器、cwd、env 写 `<root>/state/tools.json`（`FW_TOOL_CONFIG` 可覆盖）。
- Python 环境队列（`state/pyenvs.json`）只服务 Agent 自己的 Python 代码和包安装；**不用于找工具**。
- 工具经验在 SQLite（`kind=guideline`, tags 含 `tool-quirk`），不写回 Markdown。
- 不重复安装；不扫描 Home/虚拟环境找工具；不重构 Runtime。

## 先检查

1. `python3 -m fwcore toolset --json`：只读默认资产/依赖/能力状态。
2. `python3 -m fwcore capabilities`：复杂 Provider 探针。
3. `python3 -m fwcore.toolmeta`：已有工具的元数据。

`toolset.py` **只负责就绪检查**，不负责工具执行；执行是 Bash 或已有特殊适配器。

## 已有工具：元数据 + Bash

`state/tools.json` 只放运行信息：

```json
{
  "unblob": {
    "path": "/opt/unblob/bin/unblob",
    "python": "/opt/unblob/bin/python",
    "cwd": ".",
    "env": {"PATH": "/opt/unblob/bin:/usr/bin:/bin"}
  },
  "angr": {"python": "/opt/angr/bin/python", "cwd": "."},
  "qemu-aarch64-patched": {"path": "/opt/qemu/bin/qemu-aarch64-patched"}
}
```

- `path` 实际可执行文件；`python` Python 工具解释器；`cwd` 工作目录；`env` 额外环境变量。
- 配置路径不存在 → 明确报错，不静默换路径。
- 系统命令未配置时才按 `PATH` 查找。

### Bash 调用示例（必须这样应用 cwd/env）

`toolmeta --shell` 只导出变量，**不会自动 cd**：

```bash
eval "$(python3 -m fwcore.toolmeta --shell unblob)" || exit $?
cd "${FW_TOOL_CWD:-$PWD}" || exit 1
"$FW_TOOL_PATH" --help
```

Python 工具：

```bash
eval "$(python3 -m fwcore.toolmeta --shell angr)" || exit $?
"$FW_TOOL_PYTHON" -c 'import angr; print(angr.__version__)'
```

如果元数据里没有 `cwd`，不要 cd；如果命令需要保存输出，由 Agent 自己重定向：

```bash
"$FW_TOOL_PATH" --help > state/_scratch/unblob-help.txt
```

### shell mode

`shell_exec` 的 `mode=auto` 只有真正 shell 语法（`; | && > $(...)` 等）才走 shell。
需要 glob/tilde 展开时显式：

```json
{"command": "ls *.c ~/src", "mode": "shell"}
```

`mode=argv` 不解释 `* ? ~`；`mode=auto` 不会因为 `* ? ~` 自动进入 shell。

## Python 环境队列（只给 Agent 代码/装包）

配置优先 `state/pyenvs.json`（兼容旧 `state/toolenv.json`）：

```json
{"python_envs": ["/path/to/python", "/another/venv/bin/python"]}
```

或 `FW_PYTHON_ENVS`。顺序即优先级；缺失/依赖不满足跳过；配置损坏必须报告，不静默回退当前解释器。

```bash
python3 -m fwcore.pyenvs --list
python3 -m fwcore.pyenvs --select json
```

## 原生 Plugin / MCP

- 已有 Plugin 用其原生生命周期/配置。
- MCP 保持 MCP transport：先查 `transport`；stdio 用现有客户端，HTTP 若客户端不支持就明确报 `unsupported_transport`，不要转成 shell。
- MCP 调用必须走授权门；不要用 Bash 重新包一层。

## 补齐已有默认工具

1. 先看 `state/tools.json` 是否只是路径没配；能配路径就不安装第二份。
2. 系统包用系统包管理器；Python 包用 Agent 环境队列决定安装位置；冲突则跳过/新建 venv。
3. 安装/配置后重跑 `toolset`、`capabilities`，再做一次真实无破坏性 smoke test。
4. “装上命令”不等于“能力可用”；不能只把存在的文件标 ready。

## 接入真正的新工具

只有**普通 CLI 无法表达**时才增加框架层：

- 普通 CLI：`state/tools.json` + `shell_exec`；**不加** manifest/provider/adapter/wrapper；
- 需要统一结构化能力探针/版本/依赖/架构：才加 Provider；
- 需要生命周期/特殊协议（IDA、Ghidra 脚本路径、MCP、资源/证据保护脚本）：才加 Adapter/Wrapper；
- 已有特殊工作流脚本（`fw_extract`、截图、IDA）保留，直接通过 Bash 调用。

## 安全边界

- Authz 只做**授权与路径检查**，不是文件系统/网络沙箱；进程仍可访问 root 外。不要宣传成沙箱。
- 不把工具执行状态当漏洞结论。
- 需要账号/付费/可能破坏环境时先请求用户。

## 禁止

- 普通 CLI 新建 `tools/registry/*.json`、Provider、Adapter 或 Wrapper；
- 把 MCP/Plugin 改成 Bash；
- 扫描 Home/虚拟环境找工具；
- 把 Python 环境队列当工具注册表；
- 工具经验写入 SQLite Knowledge（`knowledge_propose` / `knowledge_revise`，`kind=guideline`，tags 含 `tool-quirk`）；不再写任何知识 Markdown。
- 仅凭文件存在/配置存在报 ready。
