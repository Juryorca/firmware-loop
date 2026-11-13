# firmware-loop

**面向固件漏洞研究的单 Agent 框架。**

给它一份固件，或一个设备型号。Agent 围绕攻击面提出漏洞假设，调用逆向、仿真和验证工具寻找证据；研究中断后，可以从保存的状态继续，而不必重新分析整个目标。

它不是按固定步骤运行的固件扫描器。工具、研究方向和下一步实验由当前发现决定；SQLite 保存研究记录，运行时负责会话、工具执行与恢复。

[快速开始](#快速开始) · [配置与外部工具](#配置与外部工具) · [设计理念](#设计理念) · [文档](#文档)

## 快速开始

需要 **Python 3.10+**、Git 和一个支持 OpenAI-compatible API 的模型端点。固件提取、反汇编与仿真工具按需要安装，不属于 Python 包的基础依赖。

### 安装

```bash
git clone https://github.com/Juryorca/firmware-loop.git
cd firmware-loop
```

按自己的习惯选择一种隔离环境：

<details open>
<summary><b>uv</b></summary>

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[tui]"
```

</details>

<details>
<summary><b>mamba</b></summary>

```bash
mamba create -n firmware-loop python=3.12 -y
mamba activate firmware-loop
python -m pip install -e ".[tui]"
```

</details>

<details>
<summary><b>venv + pip</b></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[tui]"
```

</details>

以上激活命令面向 bash/zsh。Windows PowerShell 中，uv/venv 创建的环境可通过 `./.venv/Scripts/Activate.ps1` 激活。不需要终端界面时，可将 `".[tui]"` 换为 `"."`。

### 配置模型

在仓库根目录创建 `state/model.json`（首次使用时先创建 `state/`）：

```json
{
  "api_key": "<YOUR_API_KEY>",
  "base_url": "https://<YOUR_OPENAI_COMPATIBLE_ENDPOINT>/v1",
  "model": "<YOUR_MODEL_ID>"
}
```

也支持环境变量 `FW_MODEL_API_KEY`、`FW_MODEL_BASE_URL`、`FW_MODEL_NAME`，且环境变量优先。请使用你自己的服务地址与模型 ID；`state/` 已加入 Git 忽略规则。

### 开始研究

```bash
fw-core
```

在终端界面中输入：

```text
/fw <固件文件路径>
```

如果手头只有设备信息，也可以输入：

```text
/fw <厂商、设备型号及已知版本>
```

这是项目内置的研究入口：Agent 会尝试识别固件、建立目标、整理攻击面，并围绕漏洞假设开展研究。用户**无需提前执行** `init`、`hyp add` 等内部状态命令。通过 `/status` 查看进度、`/hypotheses` 查看假设、`/evidence` 查看证据；用 `/continue` 继续当前研究，`/resume` 选择历史会话。

实际执行效果取决于模型、固件材料与已接入的工具。没有配置模型时会使用 `scripted` 模式，它只用于检查应用启动及运行时流程，**不会开展真实的漏洞研究**。

<details>
<summary>非交互方式</summary>

如果已经有一个研究目标，可以使用 print mode：

```bash
fw-core init example-target
fw-core --target example-target -p "梳理该目标的攻击面并提出首轮待验证假设。"
```

`-p` 执行一条消息后退出。完整的固件研究更推荐使用上面的 TUI 和 `/fw` 入口。

</details>

## 配置与外部工具

Python 包与研究工作区是分开的。**目前完整研究推荐在克隆的仓库根目录启动**：项目的 `AGENTS.md`、`.agents/skills/` 和 `tools/registry/` 从工作区加载，而不是全部随 Python wheel 分发。可用 `--root` 或 `FW_ROOT` 指定另一个已准备好这些资源的工作区；不要通过修改系统 `site-packages` 来配置项目。

运行时状态默认保存在工作区的 `state/fwcore.sqlite3`，模型配置位于 `state/model.json`。如使用 Responses API，可在该文件中设置 `"api": "responses"`；如模型端点不提供上下文窗口信息，可设置 `context_tokens`。

固件解包、IDA/Ghidra、QEMU 以及其他分析程序由各自的安装方式提供，不会因安装本 Python 包而自动出现。可以先检查当前研究环境：

```bash
fw-core capabilities
fw-core toolset
```

缺少某项工具时，相应分析能力可能不可用；不必为了运行框架安装所有外部工具。更多 CLI/TUI 操作见 [使用文档](docs/APP.md)。

## 设计理念

固件漏洞研究不总是顺着一条工具链走下去。分析一个输入点时，可能发现新的入口；一次动态验证失败，也可能只是仿真环境不完整。我们希望 Agent 能追踪这些变化，知道**下一步要验证什么，以及哪些问题仍然没有答案**。

<details>
<summary><b>展开阅读：为什么采用单 Agent、假设驱动和外部研究状态</b></summary>

**保持同一条研究脉络。** 项目采用单 Agent，而不是将取件、逆向、验证和写报告分别交给不同角色。固件研究会反复在二进制、配置、协议与运行行为之间切换；让一个 Agent 持续掌握当前假设，可以减少角色间的上下文交接。这是针对该研究场景的设计取舍，而不是声称单 Agent 在所有任务上更好。

**围绕假设推进，而非固定阶段。** 框架记录目标的攻击面，并依据入口可达性、解析复杂度等因素安排假设优先级。Agent 可以进行静态分析或动态实验，记录一次研究尝试的结果；新观察可能支持原假设、推翻它，或引出新的调查方向。研究队列随事实变化，不要求每个固件都经过完全相同的步骤。

**把研究状态留在上下文之外。** SQLite 保存攻击面、漏洞假设、研究尝试、观察、证据和结论，也保存运行与会话历史。模型上下文可以压缩、重新组织；已经记录的研究事实不会因此被删除。会话恢复时，Agent 能重新取得当前进度与待研究问题。

**区分线索和结论。** 研究尝试并不等于单次命令执行，工具输出也不会自动成为观察或有效证据。框架保存材料来源与复核记录，并对漏洞结论设置可检查的前置条件；这些规则有助于追溯研究依据，但**不能自动证明漏洞真实性**。最终判断仍依赖实际实验与安全分析。

运行时的部分设计受 [Pi](https://github.com/badlogic/pi-mono) 启发；研究方向也参考了 [Big Sleep](https://googleprojectzero.blogspot.com/2024/11/from-naptime-to-big-sleep.html) 等工作。本项目没有嵌入这些系统，也不声称自动发现漏洞具有保证。

</details>

## 使用边界

仅在已获授权的设备、固件及网络上使用。Agent 可能调用本地命令及外部程序，应在适当隔离的研究环境中运行；运行时授权控制并不等于操作系统沙箱。模拟环境中的验证结果也不代表已经在真实硬件上验证。

## 文档

[架构与运行时](docs/ARCHITECTURE.md) · [Agent 指令、Skills 与工具](docs/AGENT_ARCHITECTURE.md) · [CLI / TUI](docs/APP.md) · [研究知识库](docs/KNOWLEDGE.md) · [研究循环 Skill](.agents/skills/research-loop/SKILL.md)
