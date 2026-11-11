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
- 取消 = `paused`(安全点),不是研究结论;预算触发同样是暂停 + step 指针。
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
- **真流式**:模型 SSE → Adapter → Runner → Session → TUI 全链路增量;
  assistant 消息在首个 token 到达时即出现并持续追加,reasoning 只进隐藏的
  thinking 块,不混进正文。
- **终止判定严格**:合法结束 = 收到 `[DONE]` 或 `finish_reason`;两者都没有
  ⇒ `StreamError`(EEOF 截断),部分文本保留为 error,绝不触发 FINAL。
  `finish_reason=length/content_filter` 且含 FINAL/工具调用 ⇒ 暂停为 partial,
  不执行工具、不收口(工具参数未收全也绝不执行)。
- **取消**:`StreamControl` 对真实 HTTP 连接执行 socket shutdown + close,
  阻塞的 SSE read 会立即退出;Run 暂停、部分 assistant 保留、预算只记一次。
- 工具调用参数分片接收、完整组装并校验后才执行;状态实时更新,可折叠/展开
  (Ctrl+O),展示参数、结果、stdout 摘录。
- Slash Commands 带补全:`/help /model /fw <目标> /target /resume /status /budget
  /hypotheses /evidence /skills /knowledge /tools /mcp /tool-setup /new /continue /compact /clear /expand /quit`。
- 目标生命周期：`targets.status` 为 `active` / `closed_confirmed` / `archived`。
  `archived` 只表示生命周期，不删除 SQLite 研究事实、`report/` 或 `SOURCE_URL.txt`；
  已归档/已收尾目标在 TUI 中只读显示，不能开始新研究或继续 run。
  CLI：`fw-core archive <目标>`、`fw-core unarchive <目标>`；归档前必须没有运行中的 run，
  文件释放仍按 `research-loop` 的清理生命周期由用户明确触发。
- 选择器：模型、目标、历史研究会话（搜索 + 列表）。
  `/model` / `Ctrl+L` 从配置端点的 `/models` 自动读取目录；模型条目带
  `context_window` 时同步上下文预算，`[1m]`/`[256k]` 等显示标注会解析，
  但发送给 API 的是去掉标注的 model id。
- 研究面板：进度、假设池、观察、证据、结论、收尾检查；打开时每 2 秒检查一次，
  内容未变化时复用上次渲染（不重建表格）。
- 状态栏：目标、模型/演示模式、中文运行状态、模型调用数、工具调用数、缓存命中率（有数据时）、
  上下文、假设/证据、收尾阻塞数；长内容自动换行，不靠固定像素裁切。
  状态/标题只在会话事件、工具状态或排队变化时更新，空闲不轮询（对齐 Pi 的
  `requestRender` / 组件失效模型）；Token、运行时长和最近预算触发用 `/budget` 查看。
- 快捷键:F1 帮助、F2/Ctrl+G 研究面板、Ctrl+L 模型、Ctrl+R 恢复、
  Ctrl+T 目标、Ctrl+O 展开工具、Ctrl+Q 退出、Esc 停止、Ctrl+C 取消/清空/退出。

## 5. 测试

- `tests/test_app_args.py`:入口路由 / 模式选择 / 非 TTY 降级。
- `tests/test_app_session.py`:提交、取消、暂停后 resume、新 run、print mode;
  全部用 scripted 适配器,**不产生外部模型调用**。
- `tests/test_app_tui.py`:Textual headless pilot(真实按键/布局/overlay/resize)。
- `tests/test_streaming_conversation.py`:Mock SSE/真实本地 HTTP SSE、EOF 截断、
  分片工具参数、取消关闭阻塞读取、断流预算、多轮持久化与重启恢复、对话写入失败、
  transcript 补齐、TUI 首个 token 立即可见。
- `tools/tui_smoke.py`:真 pty 冒烟(启动/输入/多行/resize/历史/Ctrl+Q/SIGTERM)。

## 6. 对话持久化

`conversation_entries`(schema v16 引入，当前全局 schema v17)是**面向用户**的 append-only 对话,
与 `checkpoints`(仅 run step 指针,不承载模型消息)分工明确:

| 表 | 内容 | 谁用 |
|---|---|---|
| `conversation_entries` | user / assistant / tool 的可见消息,稳定 ID、`run_id`、`parent_id`、`status`、时间戳 | TUI transcript、`/resume` 恢复、模型上下文中的用户多轮输入 |
| `checkpoints` | 仅 run step / 进度指针 | 崩溃恢复的步进参考;模型历史从 conversation_entries+compactions 重建 |

- 每次用户提交都持久化(带 `client_id`,同一提交重试幂等);
  `runs.goal` 只保留初始目标,后续输入不再覆盖它。
- 状态:`ok` / `queued`(排队中,执行时同一 `client_id` 推进为 `ok`)/
  `cancelled` / `partial` / `error`;排队、取消、断流都保留明确状态。
- assistant 回复与工具结果按执行顺序追加;取消/断流写 `cancelled`/`error`,
  部分内容保留,不产生虚假完成记录。
- 用户消息和动态状态都按 append-only 语义进入模型历史:用户消息来自 conversation,
  当前 state 在**发起模型请求前**作为隐藏 `_context_only` user 消息保存;assistant 响应随后追加。
  下一轮不重写旧尾部;恢复时按同一 append-only 顺序重建模型可见消息。
  注入的是数据,不是新的用户指令。
- 关闭 TUI / 进程崩溃后重新 `/resume` 同一 run:用户消息、assistant 回复、
  工具记录按原顺序恢复,不丢失、不重复;新 run 从空对话开始。

## 7. 已知限制

- 流式只覆盖 OpenAI 兼容 SSE;注入自定义非流式 transport 的适配器会走
  `chat()` fallback(整段作为一个 delta)。reasoning 默认折叠,未做展开快捷键。
- **持久化失败不吞**:assistant/tool 对话写入在锁竞争上短重试,最终失败抛
  `ConversationPersistError` 并暂停轮次(不报告正常完成);工具已产生副作用时
  tool 结果以 conversation/tool_calls 为准,恢复时按 call_key 幂等补齐,**不重跑工具**。
- **Transcript 补齐**:conversation 为准;缺失的 assistant 从 conversation 记录恢复、
  缺失的 tool 从 `tool_calls` 幂等合并,按 tool_call_id/parent 顺序恢复,不重复注入上下文。
- `conversation_entries` 是开发期 v16 引入的对话基线；当前全局 schema 为 v17，已有旧库需先运行
  `tools/db_rebaseline.py`(会做一致性备份)。
- 不复制 Pi 的 Extension / Plan Mode / Subagent;保持单 Agent。
- TUI 依赖 `textual>=8`(`pip install -e ".[tui]"`);缺失时交互模式给出安装提示,
  Print Mode 与研究子命令不受影响。
