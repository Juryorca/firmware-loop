# firmware-loop

面向固件安全研究的单 Agent 长程研究运行时。

[快速开始](#快速开始) · [配置](#配置) · [设计](#设计) · [文档](#文档)

## 它是什么

`firmware-loop` 把固件漏洞研究组织成一段可以持续、恢复和复核的工作，而不是一次性执行固定工具链。给定固件目标和授权范围，Agent 会维护攻击面、提出可证伪的假设、选择分析或验证方法，并把每一步的结果留在结构化状态中。

研究单位不是“跑完哪些工具”，而是**一个目标上的一组假设和证据**。Agent 可以在静态分析、仿真、抓包、反汇编和报告之间切换；研究中断后可以从 SQLite 恢复，而不是重新把整个目标讲一遍。

- **单 Agent**：一个模型围绕当前目标持续工作，不通过多个角色传递上下文。
- **假设驱动**：攻击面先登记，未判定的面进入假设池，再用尝试和观察推进或否定假设。
- **外部状态**：研究事实保存在 SQLite，模型上下文只是工作视图；上下文压缩不删除原始研究记录。

它不是一个固件扫描器，也不是多 Agent 编排框架。外部提取、反汇编、仿真和调试工具由使用者按需接入；运行时不声称能替代安全研究人员的判断。

## 快速开始

### 环境要求

| 项目 | 要求 |
|---|---|
| Python | 3.10 或更高；CI 的安装冒烟测试使用 3.12 |
| Git | 用于获取仓库 |
| TUI | 可选，安装 `.[tui]` 后可用 |
| 模型 | 真实研究需要 OpenAI-compatible 端点；未配置时可使用 `scripted` 做启动检查 |
| 固件工具 | 可选；`binwalk`、`unblob`、QEMU、IDA、Ghidra 等不随仓库捆绑 |

### 获取代码

```bash
git clone https://github.com/Juryorca/firmware-loop.git
cd firmware-loop
```

三种安装方式任选一种。它们都从当前源码目录做 editable 安装，不依赖预装的 `site-packages`。

<details>
<summary><b>uv</b></summary>

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[tui]"
```

Windows 激活命令为 `.venv\Scripts\activate`。

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

Windows 激活命令为 `.venv\Scripts\activate`。

</details>

只使用 CLI、不需要 TUI 时，可以把 `".[tui]"` 换成 `"."`。安装完成后先确认入口可用：

```bash
fw-core --help
```

### 第一个研究目标

下面的命令在仓库根目录创建本地工作区。运行状态写入 `state/`，该目录不会进入 Git。

```bash
fw-core init demo --version 1.0
fw-core hyp add demo "待验证的攻击面假设"
fw-core derive demo
```

启动交互式研究：

```bash
fw-core --target demo
```

也可以使用非交互的 print mode：

```bash
fw-core --target demo -p "先阅读目标材料，建立攻击面清单，再选择第一个未判定面做静态分析。"
```

没有配置真实模型时，可以用 `scripted` 检查启动和状态流程：

```bash
fw-core --target demo --model scripted
```

`scripted` 只验证运行时和工具协议，不会产生真实研究结果。真实研究先在[配置](#配置)中填写模型端点。

## 配置

### 模型

模型配置读取优先级为环境变量，然后是工作区内的 `state/model.json`。`state/` 已被 `.gitignore` 忽略，不要把密钥提交到仓库。

```json
{
  "api_key": "<your-api-key>",
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "model": "<model-id>"
}
```

等价的环境变量：

```bash
export FW_MODEL_API_KEY="<your-api-key>"
export FW_MODEL_BASE_URL="https://your-openai-compatible-endpoint/v1"
export FW_MODEL_NAME="<model-id>"
```

TUI 的 `/model` 会从端点的 `/models` 读取可用模型，不维护硬编码模型白名单。使用 Responses API 时，在 `state/model.json` 或环境变量中设置 `FW_MODEL_API=responses`。如果端点没有返回上下文窗口，可在 `state/model.json` 中显式设置 `context_tokens`。

### 工作区与数据库

- 默认工作区是当前目录，也可以用 `--root` 或 `FW_ROOT` 指定。
- 数据库默认位于 `<root>/state/fwcore.sqlite3`。
- `AGENTS.md`、`.agents/skills/` 和工具清单从工作区读取。正常的完整研究请在仓库根目录运行，这样这些源码资产可见。
- `state/` 只保存本机运行数据；不要把研究目标、固件或数据库提交到 Git。

### 外部工具

仓库提供研究循环、状态和工具接入层，但不包含完整的固件分析工具链。先查看当前环境实际具备的能力：

```bash
fw-core capabilities
fw-core toolset
```

缺少 `binwalk`、`unblob`、QEMU、IDA 或 Ghidra 时，对应能力会显示为不可用或以部分能力运行。安装这些工具、配置路径和验证能力属于使用者的环境准备，不在基础安装中自动完成。

## 设计

这一节解释为什么采用现在的结构，不要求按顺序阅读；也可以直接跳到[文档](#文档)。

**单 Agent，而不是角色分工。** 固件研究常常围绕同一组文件、配置和二进制反复切换视角。把分析、验证和报告拆成多个角色，会把这些上下文切碎，并增加交接成本。`firmware-loop` 让一个 Agent 在一次会话里持续追问：当前证据支持什么、下一个最值得排除的可能性是什么。单 Agent 不是对所有任务都更优，而是一个明确的选择。

**研究队列，而不是固定阶段。** 目标先建立攻击面表，每个入口有可达性、输入可控性和判定状态。未判定的面进入假设池；每次尝试产生结果，观察和证据再决定假设是继续、收窄还是否定。这样，Agent 不需要假设“所有目标都适合同一条扫描流程”，而是根据当前事实选择下一步。

**研究事实必须在模型上下文之外。** 复杂目标的材料、失败尝试和证据链会超过任何上下文窗口。运行时把目标、攻击面、假设、尝试、观察、证据和结论写入 SQLite；上下文压缩只改变下一次模型请求的投影，不删除原始记录。会话恢复时，Agent 重新拿到的是研究状态，而不是一份被截断的聊天记录。

**尝试、观察和证据是不同层级。** 一次命令执行是尝试，工具有输出是观察，只有经过复核并能定位到来源的材料才是证据。结论还必须带适用范围。运行时可以检查状态是否完整、证据是否登记、收尾是否有阻塞项，但不能自动证明一个漏洞成立；最终的严重性、可复现性和影响范围仍需要研究人员判断。

<details>
<summary>与现有 Agent 研究工作的关系</summary>

运行时采用了与 Pi 等编码 Agent 类似的 append-only 历史和压缩思路：静态 system prompt、尾部状态、可恢复会话和结构化摘要。`firmware-loop` 不嵌入 Pi，也不声称与其兼容；研究状态模型、攻击面/假设队列和证据约束是本项目自己的实现。

在问题方向上，它和 Big Sleep 等 Agent 驱动的漏洞研究工作共享一个前提：模型可以参与真实的分析和验证过程。但本项目不是这些系统的复刻，也不把“发现漏洞”当作自动保证。

</details>

## 能力边界

- **运行时与工具链分离。** 基础安装提供研究循环、SQLite 状态、CLI/TUI、文件/命令工具、MCP 和工具注册层；固件提取、全系统仿真、IDA/Ghidra 分析需要外部程序。
- **仿真不是真机。** QEMU、容器和提取镜像可以验证很多行为，但设备特有的驱动、启动流程和硬件状态可能不在其中。报告应明确说明验证环境和未覆盖的部分。
- **Agent 输出不是结论。** 工具失败、崩溃和模型描述都需要分诊；研究结论需要可追溯的证据和适用范围。
- **要求授权目标。** 只在自己拥有或获得明确许可的系统、固件和网络上使用。运行时提供授权边界和 PoC 级工具，不提供武器化或规避检测的能力。

## 文档

- [架构总览](docs/ARCHITECTURE.md)：状态层、运行时、工具和上下文生命周期。
- [Agent 内容架构](docs/AGENT_ARCHITECTURE.md)：System Prompt、Skills、命令和知识库的边界。
- [CLI / TUI](docs/APP.md)：交互模式、print mode、会话恢复和快捷键。
- [知识库](docs/KNOWLEDGE.md)：SQLite Knowledge 的存储和备份约定。
- [研究循环 Skill](.agents/skills/research-loop/SKILL.md)：假设池、尝试、证据和收尾规则。

## 开发与验证

```bash
python -m pytest
bash tools/fwcore_verify.sh
```

CI 只做可移植性冒烟测试：在 `uv` 和 `mamba` 创建的隔离环境中安装本体，检查模块导入、`fw-core --help`、内置模板资源，并从仓库外的工作目录运行 `init`/`derive`；它不调用真实模型，也不执行完整研究流程。安装说明和 CI 结果应作为“环境可用”的证据，而不是研究质量的证据。
