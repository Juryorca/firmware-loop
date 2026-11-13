# firmware-loop

面向固件安全研究的单 Agent 长程研究运行时。

[快速开始](#快速开始) · [配置](#配置) · [设计](#设计) · [文档](#文档)

`firmware-loop` 接收一个固件文件或厂商型号作为研究起点，围绕它建立研究目标，维护攻击面、假设、尝试、观察和证据，并在需要时调用分析、仿真和调试工具。研究可以暂停、恢复和继续，不需要每次重新描述整个目标。

它不替代安全研究员，也不是固定扫描流水线。它提供的是一个研究运行时：模型负责判断下一步，程序负责保存事实、执行工具和恢复会话。

## 快速开始

### 环境要求

- Python 3.10+
- Git
- 一个 OpenAI-compatible 模型端点，或先用 `scripted` 检查界面和状态流程
- 固件提取、仿真、反汇编工具按需安装

### 安装

```bash
git clone https://github.com/Juryorca/firmware-loop.git
cd firmware-loop
```

选择一种隔离环境。三种方式都从当前源码目录安装，不要使用系统 `site-packages` 里的现有依赖。

<details>
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

Windows 下将激活命令换成 `.venv\Scripts\activate`。不需要 TUI 时可以把 `".[tui]"` 换成 `"."`。

`pip` 负责安装 Python 入口和内置模板；完整研究还会从当前 checkout 读取 `AGENTS.md`、`.agents/skills/` 和 `tools/registry/`。因此初次使用请在 checkout 根目录运行。如果你从其他目录启动，用 `--root` 指向 checkout：

```bash
fw-core --root /path/to/firmware-loop
```

### 配置模型

真实研究前先配置模型。配置可以写在 checkout 的 `state/model.json`：

```json
{
  "api_key": "<your-api-key>",
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "model": "<model-id>"
}
```

也可以用环境变量：

```bash
export FW_MODEL_API_KEY="<your-api-key>"
export FW_MODEL_BASE_URL="https://your-openai-compatible-endpoint/v1"
export FW_MODEL_NAME="<model-id>"
```

`state/` 已被 `.gitignore` 忽略，不要把密钥提交到仓库。没有模型配置时，TUI 会以 `scripted` 模式启动；它只演示界面和状态流程，不进行真实研究。

### 开始第一个目标

启动 TUI：

```bash
fw-core
```

在输入框中输入 `/fw`，后面跟固件路径或型号：

```text
/fw ./firmware/DSM_DS223_86009.pat
```

或：

```text
/fw Synology DS223 DSM 7.3.2
```

`/fw` 是内置的固件研究入口。没有目标时，它先建立共享的 bootstrap target；Agent 识别固件后创建真实 target，并把当前 run 绑定过去。你不需要先运行 `fw-core init`，也不需要手工创建假设或数据库记录。

如果只有厂商和型号，没有本地固件，Agent 会先确认产品、版本和可用来源；固件获取仍然需要网络、厂商渠道和你的授权。

常用操作：

```text
/status      查看研究进度
/continue    继续当前 run
/help        查看全部命令
```

### 无 TUI 或自动化

需要在脚本中启动一条研究消息时，可以先显式建立目标：

```bash
fw-core init my-target --version 1.0
fw-core --target my-target -p "先枚举网络入口，再选择第一个未判定面做分析。"
```

这条路径适合已有目标或自动化场景；日常使用优先用 `/fw`，不必先把目标状态拆成多条 CLI 命令。

## 配置

### 模型与端点

模型配置优先读环境变量，其次读 `<root>/state/model.json`。`base_url` 应指向支持 Chat Completions 的端点；如果使用 Responses API，在 `state/model.json` 或环境变量中设置：

```json
{
  "api": "responses"
}
```

TUI 的 `/model` 会从端点的 `/models` 读取模型目录，不维护硬编码模型白名单。端点没有返回上下文窗口时，可以在 `state/model.json` 中设置 `context_tokens`。

### 工作区

- `--root` / `FW_ROOT` 指定工作区，默认是当前目录。
- 研究数据库位于 `<root>/state/fwcore.sqlite3`。
- `state/` 保存运行数据和报告，不进入 Git。
- 研究规则和工具清单从工作区的 `AGENTS.md`、`.agents/skills/`、`tools/registry/` 读取；把这三个位置当作运行时的一部分，而不是可选的文档。

### 外部工具

仓库不捆绑完整的固件分析工具链。安装后先检查当前环境：

```bash
fw-core capabilities
fw-core toolset
```

`binwalk`、`unblob`、QEMU、IDA、Ghidra 等工具的路径、版本和可用性由使用环境决定。缺失时，对应能力会显示为不可用或部分可用；基础安装不会自动安装这些程序。

## 设计

这一节解释为什么采用现在的结构。只想开始使用可以跳到[文档](#文档)。

<details>
<summary><b>展开：单 Agent、假设队列和外部状态</b></summary>

研究流程不是“先跑完所有工具，再让模型写报告”。一个典型循环是：

```text
target
  → 攻击面 / surfaces
  → 假设队列
  → attempt
  → observation / evidence
  → conclusion
  → 下一轮
```

**为什么是单 Agent。** 固件研究经常在同一组文件、配置和二进制之间来回切换。把分析、验证和报告拆成多个角色，会产生额外交接，也容易让某个角色只看见局部上下文。`firmware-loop` 让一个模型在一次 run 中持续追踪假设；它可以使用很多工具，但决策上下文不依赖角色之间的消息传递。单 Agent 不是对所有任务都更优，而是一个明确的设计选择。

**为什么先登记攻击面，再形成假设。** 固件里可调查的入口很多，但可达性、输入可控性和理解程度不同。`surfaces` 记录入口和属性；未判定的入口进入假设池。Agent 根据当前事实选择一个假设，执行一次有明确方法的 attempt，再根据结果决定继续、收窄还是否定它。这样研究不会被一条预设流水线限制住。

**为什么把研究事实放在模型上下文之外。** 长时间研究会超过任何上下文窗口。目标、攻击面、假设、尝试、观察、证据和结论写入 SQLite；会话历史和压缩记录是模型看到的工作视图，不是研究事实的唯一副本。进程中断、上下文压缩或换一个 run 后，已经登记的事实仍然可以恢复。

**为什么把尝试、观察和证据分开。** `attempts` 是一次围绕假设的研究行动，记录方法和结果，可能包含多次工具调用；它不是“执行了一条命令”的同义词。`observations` 是明确登记的事实、异常或值得继续追的线索，工具输出本身不会自动变成 observation。`evidence` 关联到假设或尝试的路径、capture 或其他材料，可以先以未复核状态保存，再通过复核记录检查它是否可复现、可重算或可交叉验证。结论需要适用范围；运行时可以检查状态完整性和收尾阻塞，但不能替研究员判断漏洞是否真实成立。

运行时采用了与 Pi 等编码 Agent 类似的 append-only 历史、状态压缩和工具循环思路；研究状态模型和攻击面/假设队列是本仓库自己的实现。项目方向也受到 Big Sleep 等 Agent 漏洞研究工作的启发，但这不是它们的复刻，也不把自动发现当作保证。

</details>

## 能力边界

- **运行时和工具链分离。** 基础安装提供研究循环、SQLite 状态、CLI/TUI、文件/命令工具和 MCP 接入；固件提取、全系统仿真、IDA/Ghidra 分析需要外部程序。
- **仿真不是真机。** QEMU、容器和提取镜像能复现很多行为，但设备特有驱动、启动流程和硬件状态可能缺失。报告应说明验证环境和未覆盖的部分。
- **Agent 输出不是结论。** 工具失败、崩溃和模型描述都需要分诊；结论需要有来源、复核和适用范围。
- **只在授权范围内使用。** 仅对自己拥有或明确获准测试的系统、固件和网络使用。运行时面向 PoC 级验证，不提供武器化能力。

## 文档

- [架构总览](docs/ARCHITECTURE.md)：状态层、运行时、工具和上下文生命周期。
- [Agent 内容架构](docs/AGENT_ARCHITECTURE.md)：System Prompt、Skills、命令和知识库的边界。
- [CLI / TUI](docs/APP.md)：交互模式、print mode、会话恢复和快捷键。
- [知识库](docs/KNOWLEDGE.md)：SQLite Knowledge 的存储和备份约定。
- [研究循环 Skill](.agents/skills/research-loop/SKILL.md)：假设池、尝试、证据和收尾规则。

## 开发与验证

```bash
python -m pip install -e ".[dev,tui]"
python -m pytest
bash tools/fwcore_verify.sh
```

portability CI 在 `uv` 和 `mamba` 创建的隔离环境中安装本体，检查模块导入、`fw-core --help`、内置模板资源，并从仓库外目录运行 `init`/`derive`。它不调用真实模型，也不验证完整研究流程；安装成功不等于研究结果可靠。
