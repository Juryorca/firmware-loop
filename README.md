# firmware-loop

**固件安全审计 Agent 框架。**

整合固件获取与解包、服务逆向、动态分析、仿真验证、报告生成、报告提交功能，全流程自动化。

[Quick Start](#quick-start) · [Commands](#commands) · [Configuration](#configuration) · [Design](#design) · [Docs](#docs)

![firmware-loop TUI demo](docs/assets/firmware-loop-demo.gif)

## Quick Start

**Requirements:** Python 3.10+、Git;兼容 OPENAI 格式的 API ;本项目不捆绑分析工具，但是提供了agent辅助接入功能，能够帮助准备基础的工具集。

### Install

```bash
git clone https://github.com/Juryorca/firmware-loop.git
cd firmware-loop
```

请按自身喜好选择安装方式：

<details open>
<summary><b>uv</b></summary>

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install ".[tui]"
```

</details>

<details>
<summary><b>mamba</b></summary>

```bash
mamba create -n firmware-loop python=3.12 pip -y
mamba activate firmware-loop
python -m pip install ".[tui]"
```

</details>

<details>
<summary><b>venv + pip</b></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ".[tui]"
```

</details>

以上为 bash 命令；Windows 请按所用 shell 激活环境。如果你觉得不需要 TUI ，可以将 `".[tui]"` 改为 `"."`。

### Configure

在仓库根目录创建 `state/model.json`：

```json
{
  "api_key": "<API_KEY>",
  "base_url": "https://api.example.com/v1",
  "model": "<MODEL_ID>"
}
```

也支持环境变量 `FW_MODEL_API_KEY`、`FW_MODEL_BASE_URL`、`FW_MODEL_NAME`（优先级高于 JSON）。使用 Responses API 时，增加 `"api": "responses"`。model 支持自动获取因此可以不填。

### Run

```bash
fw-core
```

在 TUI 中输入：

```text
/fw /path/to/firmware.bin
```

也可直接提供设备型号，例如 `/fw <厂商> <型号> <版本>`。Agent 会自行获取固件镜像。

在 TUI 中输入 `/help` 查看所有命令与快捷键。常用入口：`/status` 查看研究进度，`/continue` 继续当前研究，`/resume` 恢复历史会话。完整列表见 [Commands](#commands)。


<details>
<summary>Print mode / CLI</summary>

```bash
fw-core init target-1
fw-core --target target-1 -p "分析目标的网络入口并提出待验证的漏洞假设"
```

</details>

## Commands

在 TUI 输入 `/help` 可查看当前版本的命令与快捷键。

<details>
<summary>全部 21 条内置命令</summary>

| 命令 | 用途 |
| --- | --- |
| `/fw <固件路径或型号>` | 启动固件研究 |
| `/status` | 查看研究进度与收尾检查 |
| `/hypotheses` | 查看假设队列 |
| `/evidence` | 查看观察与证据 |
| `/continue` | 继续当前研究 |
| `/resume [会话 ID]` | 恢复历史会话 |
| `/new` | 在当前目标下新建会话 |
| `/target [name]` | 选择或切换目标 |
| `/model [name]` | 选择或切换模型 |
| `/budget` | 查看用量与上下文 |
| `/skills` | 查看可用 Skills |
| `/knowledge <query>` | 检索研究知识 |
| `/tools` | 检查工具状态 |
| `/tool-setup` | 检查并配置分析工具 |
| `/mcp` | 查看 MCP 连接 |
| `/compact [instructions]` | 压缩当前会话上下文 |
| `/expand` | 展开或折叠工具输出 |
| `/clear` | 清空当前对话视图（保留研究记录） |
| `/help` | 查看命令与快捷键 |
| `/quit` | 退出 TUI |
| `/提交 <报告>` | 填写厂商提交草稿（不发送） |

</details>

## Configuration

**Workspace.** 当前版本请在克隆的仓库根目录运行 `fw-core`。研究规则、Skills 和工具定义随仓库提供。知识库会随使用积累。

**State.** 研究数据保存在 `state/`，会随使用积累。

**Tools.** `binwalk`、`unblob`、QEMU、IDA、Ghidra 等由用户安装，本框架提供了统一的接入方案，因为我不喜欢一个项目捆绑及其庞大的依赖，像IDA、ghidra、AFL、qemu这些框架本身已经非常庞大了。

```bash
fw-core capabilities
fw-core toolset
```

这些命令可以检查目前的推荐工具集的完备情况

## Design

<details>

这个项目最初的灵感来自 Anthropic 的一份安全报告，其中介绍了一个固件漏洞挖掘工作流。它本身是 Claude 中的一个 Loop，于是我开始思考，能否基于这个 Loop 构建一套自己的固件安全审计工作流。

最开始，我只是在 Pi 中为一些工具编写 Wrapper，并配置了相应的 Skills。后来用这套配置扫描了不少固件，发现效果还不错，于是开始考虑，能不能把它发展成一个完整的 Agent 框架。

我很喜欢 Pi 的设计理念。它强调使用更简单的工具，社区研究也表明，DS 在这种情况下能够发挥更强的能力；同时，简单的工具设计也提供了更好的扩展性。因此，我的框架在 Runtime 设计上基本参考了 Pi。

市面上常见的渗透测试 Agent 通常采用多 Agent 编排：由一个规划 Agent 管理其他 Agent，各个 Agent 分别负责一部分任务，并通过结构化信息传递来组织工作。这类框架通常还会配上一个展示复杂任务图的前端。

我之前也做过这样的 Agent，但后来看到不少因过度设计而导致能力下降的案例。比如经典的固件模拟工作 Firmenstein，其中包含针对 5 种不同依赖的分析 Agent、负责规划干预的 Planner，以及运行 Agent。结果是，各个 Agent 之间存在大量重复工作，有时甚至因为信息共享不足，导致某些关键修复被跳过。不同 Agent 之间也可能出现互相推脱的情况：Planner 因为知道还有运行 Agent，就倾向于再等等运行后的 Trace，看看能否更好地修复依赖问题。

我认为，外部存储结构同样可以承担任务规划的作用。单 Agent 的优势在于能够更好地复用信息、减少重复工作，也更容易响应人类研究员临时提出的需求。我们可以尝试通过外部结构化存储来管理研究状态、规划任务，引导 Agent 深入研究、扩展攻击面，提高研究的深度与广度，同时减少任务漂移和缺乏全局视角的问题。

经典框架 Big Sleep 也强调加强单 Agent 的研究深度，不过 Big Sleep 会通过切换模型和研究路径进行采样，我并没有完全照搬它的设计。

我觉得固件审计中的一些特点也很适合单 Agent：

1. 研究员通常需要始终熟悉当前固件业务处理的整体架构。
2. 固件中的代码片段经常被复用，Handler 匹配的情况也很常见。过度拆分任务容易导致对相同内容的重复分析。
3. 一个缺陷往往能够揭示更多缺陷。

综合这些考虑，我最终决定让 firmware-loop 采用单 Agent 持续研究一个目标，并以漏洞假设组织工作。每轮研究都需要明确要验证什么、如何判断结果，并要求 Agent 在验证过程和得出结论后提出新的假设。

这样的设计既能帮助 Agent 维持当前研究目标、持续深入探索，也能让探索过程中出现的新发现得到保留，而不会打断当前研究。

Agent 经常会说“XXX 暂时没有证据”，框架则要求它将这类尚未解决的问题记录为新的假设，引导 Agent 进一步完善结论，减少后续返工。

除此之外，框架还有不少其他机制，但我不太想在这里逐一展开。它们大多是我在实际设计和测试过程中，针对遇到的问题逐步想到并加入的。

[研究循环](.agents/skills/research-loop/references/LOOP.md) · [运行时架构](docs/ARCHITECTURE.md) · [Pi](https://github.com/badlogic/pi-mono)（运行时设计参考） · [Big Sleep](https://googleprojectzero.blogspot.com/2024/11/from-naptime-to-big-sleep.html)（研究背景）

</details>

## Scope

固件通常允许研究者进行漏洞挖掘，本项目本身不会对实际部署环境测试。

## Docs

- [CLI / TUI](docs/APP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Agent / Skills / Tools](docs/AGENT_ARCHITECTURE.md)
- [Knowledge](docs/KNOWLEDGE.md)
- [Research Loop](.agents/skills/research-loop/references/LOOP.md)
