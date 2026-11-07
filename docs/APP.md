# fw-core 应用层(CLI / TUI)

> 参考实现:Pi `v1.5.0`(commit `f9a26365568127461c47a07a37dcb42c140a3ac5`)。
> 本文件只描述**应用层**;研究引擎的架构见 `ARCHITECTURE.md`,不重复。

## 1. 定位

`fwapp/` 是**薄应用层**:把已完成的 `fwcore` 研究引擎组合成可日常使用的终端应用。
它不实现 Agent Loop、不调用模型 API、不建第二套状态库,也不通过 subprocess
反复调用 `fw-core run/status/resume`。

调用关系(与 Pi 一一对应):

```text
fwcore/cli.py                     ← 统一入口(已有研究子命令不变)
  └─ fwapp/cli.py                 ← CLI Entry(对应 Pi cli.ts)
       └─ fwapp/main.py           ← 参数/模式分发(对应 Pi main.ts)
            ├─ fwapp/runtime.py   ← AppRuntime(对应 AgentSessionRuntime)
            │    ├─ fwapp/services.py   ← cwd 绑定服务(DB/Domain/Research/Limits/Registry)
            │    └─ fwapp/session.py    ← AgentSession(对应 AgentSession 的应用侧)
            ├─ fwapp/modes/interactive.py  ← Textual TUI(对应 InteractiveMode)
            └─ fwapp/modes/print.py        ← Print Mode(对应 runPrintMode)
```

`fwapp/widgets/` 只放展示组件:`transcript`(消息/工具/通知)、`editor`(多行编辑)、
`palette`(斜杠命令补全)、`status`(状态栏)、`screens`(选择器/研究面板/帮助/确认)。

## 2. 生命周期与线程边界

- **唯一 Agent Loop 仍在 Core**:`AgentSession` 每一轮调用 `Runner.turn /
  resume / start / cancel`,不复制循环逻辑。
- **worker 线程**独占自己的 SQLite 连接与 Runner;模型调用阻塞只发生在 worker,
  UI 永远可响应。
- **UI 线程**用 `services.con` 只读快照,并在取消时直接调用 `Runner.cancel`
  (Core 的 CAS/租约仍决定谁能写)。
- 取消 = `paused`(安全点),不是研究结论;预算触发同样是暂停 + checkpoint。
- 应用退出:Session → Services 依次关闭;信号 `SIGTERM/SIGHUP` 走 `app.exit()`
  让 Textual 恢复终端,再由 `main` 的 `finally` 关闭 Runtime。

## 3. 运行模式

| 命令 | 行为 |
| --- | --- |
| `fw-core` | TTY 下进入 TUI;非 TTY 只打印用法并以 2 退出(绝不意外进全屏) |
| `fw-core tui` | 显式进入 TUI;非 TTY 直接报错退出 2 |
| `fw-core -p "..."` | Print Mode:执行一条消息,最终回答写 stdout,工具/状态写 stderr |
| `fw-core <研究子命令>` | 原 CLI 不变(`init/status/run/resume/...`) |

应用参数:`--root/--db/--target/--run/--model/--script/--no-mcp/--no-watchdog`。

## 4. 交互能力

- 全屏布局、终端 resize、Transcript 滚动;退出/异常/SIGTERM 恢复终端。
- 多行输入:Enter 发送、Shift+Enter / Ctrl+J 换行、Alt+Enter 排队、↑ 历史。
- 工具调用按状态着色,可折叠/展开(Ctrl+O),展示参数、结果、stdout 摘录。
- Slash Commands 带补全:`/help /model /target /resume /status /budget
  /hypotheses /evidence /new /continue /clear /expand /quit`。
- 选择器:模型、目标、历史 run(搜索 + 列表)。
- 研究面板:进度、假设池、观察、证据、Gate 阻塞(每 2 秒从 SQLite 刷新)。
- 状态栏:模型、target、run、状态、调用/工具数、Token(含 cache)、
  上下文占用、运行时长、假设/证据/阻塞、最近预算触发。
- 快捷键:F1 帮助、F2/Ctrl+G 研究面板、Ctrl+L 模型、Ctrl+R 恢复、
  Ctrl+T 目标、Ctrl+O 展开工具、Ctrl+Q 退出、Esc 停止、Ctrl+C 取消/清空/退出。

## 5. 测试

- `tests/test_app_args.py`:入口路由 / 模式选择 / 非 TTY 降级。
- `tests/test_app_session.py`:提交、取消、暂停后 resume、新 run、print mode;
  全部用 scripted 适配器,**不产生外部模型调用**。
- `tests/test_app_tui.py`:Textual headless pilot(真实按键/布局/overlay/resize)。
- `tools/tui_smoke.py`:真 pty 冒烟(启动/输入/多行/resize/历史/Ctrl+Q/SIGTERM)。

## 6. 已知限制

- 模型适配器目前是**非流式**:TUI 展示"model call 完成"事件与 busy 状态,
  不逐 token 流式渲染;要流式需在 `fwcore/models.py` 增加流式接口(未做)。
- 连续多轮用户消息里,只有 run 的初始 goal 会持久化;后续每轮 goal 只存在于
  当次运行的历史(旧轮在重载后不显示为用户气泡)。这是 Core 的既有边界,
  应用层没有另建一份会话存储。
- 不复制 Pi 的 Extension / Plan Mode / Subagent;保持单 Agent。
- TUI 依赖 `textual>=8`(`pip install -e ".[tui]"`);缺失时交互模式给出安装提示,
  Print Mode 与研究子命令不受影响。
