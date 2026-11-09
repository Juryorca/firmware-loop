# IDA headless wrapper suite

这是 IDA headless 工具套件的**单一工具手册**。命令、参数、生命周期和故障处理都以本文件为准；
`.agents/skills/firmware-reversing` 只指向这里，不复制另一份命令清单。

## 文件

- `ida_env.sh` — 读取唯一配置来源（`fwcore/winenv.py`），其它脚本 sourced 它。
- `ida_start.sh` — 启动 `idalib-mcp` supervisor；幂等。
- `ida_open.sh` — 把 Linux 二进制复制到暂存区并调用 `idb_open`。
- `ida_list.sh` — 列出已打开的数据库会话。
- `ida_tools.sh` — 列出 supervisor 暴露的 MCP 工具。
- `ida_call.sh` — 调用一个 MCP 工具。
- `ida_stop.sh` — 停本环境的 supervisor/worker；默认不动其它实例和 IDB。
- `ida_clean.sh` — 会话退出时清暂存区（含 IDB 副本），保留管线白名单。
- `ida_mcp_lib.sh` — MCP initialize/session/call 的公共 shell 函数。

## 配置

配置优先级：**`FW_IDA_*` 环境变量 > `state/ida.json` > 兼容默认值**。

```bash
python3 -m fwcore ida-env        # 人读：来源、派生路径、problems
python3 -m fwcore ida-env --sh   # shell export
```

不要在命令里写死盘符、端口或 Windows 用户名。缺少 exe、暂存目录或挂载点时，
先修配置；这属于环境配置错误，不是二进制分析失败。完整键说明见
[`docs/IDA-ENV.md`](../../docs/IDA-ENV.md)。

## 快速流程

从仓库根目录执行：

```bash
bash tools/ida/ida_start.sh
bash tools/ida/ida_open.sh /path/to/linux-binary [session-name]
bash tools/ida/ida_tools.sh
bash tools/ida/ida_call.sh <tool> '{"database":"session-name",...}'
bash tools/ida/ida_list.sh
bash tools/ida/ida_stop.sh
bash tools/ida/ida_clean.sh
```

`ida_open.sh` 的会话名缺省是文件名；`ida_call.sh` 的 JSON **必须**带 `database`。
本机 MCP 端点要绕过代理：用 `curl --noproxy '*'` 或 `ida_call.sh`，不要用 webfetch。

## 生命周期

- `ida_start.sh` 幂等；已启动时直接返回。默认不删 IDB。
- `ida_stop.sh` 默认只停本环境的 supervisor（pidfile）和指向本暂存目录的 worker；
  不删 IDB，不动其它实例。`--all` 会波及同机其它实例，`--purge` 会删 IDB，显式需要时才用。
- `ida_clean.sh` 用于**会话退出**：清暂存副本，保留 `start_mcp.py`、`keepalive.py`、`idalog.txt`。
- 研究过程中不要为了“清干净”随意跑 `--purge`；IDB 是共享分析数据。

## 失败处理

- supervisor 不可达：先 `python3 -m fwcore ida-env` 看配置，再 `ida_start.sh` 看日志。
- `ida_open.sh` 复制失败：检查暂存目录挂载和空间；不要改脚本绕过配置。
- `ida_call.sh` 返回 5：supervisor 不可达；返回 2：JSON 参数不合法。
- 配置错误、IDA 分析失败、MCP 会话错误要分开报告，不能合并成“二进制分析失败”。

## 文档边界

本文件是工具套件的 README；研究流程（何时用 IDA、入口→参数→sink、证据登记）在
`.agents/skills/firmware-reversing/SKILL.md`。常驻 system prompt 只保留身份、协议和稳定规则，
不复制本文件。
