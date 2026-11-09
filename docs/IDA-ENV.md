# IDA / Windows 接入配置(`fw-core ida-env`)

单一来源:`fwcore/winenv.py`。优先级 **环境变量 `FW_IDA_*` > `state/ida.json` > 兼容默认值**。
`tools/ida_*.sh` 通过 `tools/ida_env.sh` 自动获取同一份配置,不写死路径/端口。

## 键

| 键(环境变量 `FW_` 前缀) | 含义 |
|---|---|
| `IDA_WIN_DIR` | Windows 侧 IDA 安装目录(激活 idalib 用) |
| `IDA_MCP_EXE` | Windows 侧 `idalib-mcp.exe`(会自动转成 WSL 路径调用) |
| `IDA_WIN_STAGE` | Windows 侧暂存目录(传给 `idb_open` 的路径前缀) |
| `IDA_STAGE` | 同一暂存目录的 WSL 路径 |
| `IDA_WIN_PYTHON` | Windows 侧 python(仅用于提示激活命令) |
| `IDA_URL` | Supervisor 地址(端口来自它,不再写死 8745) |
| `IDA_LOG` | Supervisor 日志(默认 `<root>/state/ida/supervisor.log`) |
| `IDA_WIN_INSTANCES` | worker 注册表目录(默认用 `%APPDATA%` 探测) |
| `IDA_START_TIMEOUT` / `IDA_PROBE_TIMEOUT` | 启动/探测超时(秒) |
| `IDA_STOP_ON_CLOSE` | `1` = `Runner.close()` 时停本环境 supervisor(默认 `0`,共享服务不停) |
| `FW_IDA_MOUNT_<盘符>` | 盘符挂载点覆盖(如 `FW_IDA_MOUNT_D=/mnt/winD`) |

## 用法

```bash
python3 -m fwcore ida-env            # 人读:来源、派生路径、problems
python3 -m fwcore ida-env --sh       # 给 shell:export 语句(路径含空格也安全)
python3 -m fwcore run <target> ...   # Adapter/探针/脚本共用同一份配置
```

各 wrapper 的用法可用 `bash tools/ida_stop.sh --help` 这类方式查看；`ida_env.sh` 是被
source 的配置前置，不是独立 CLI。

`state/ida.json` 示例:

```json
{"values": {"IDA_WIN_STAGE": "E:\\ida stage", "IDA_STAGE": "/mnt/e/ida stage"},
 "mounts": {"E": "/mnt/e"}}
```

配置错误(缺 exe / 暂存目录不存在 / 盘符未挂载)会在工具的 error 里直接点名,**不是** IDA 分析失败。
