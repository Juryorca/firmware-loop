# fwcore 架构(唯一架构文档)

> 文档定位:这是**给开发 agent 看的**工程事实记录,不是给用户的产品说明。
> 用户可见的只有 `state/<目标>/report/`(advisory / CVE 材料)。
> 分四类,不得混写:①已实现(有代码+测试) ②已验证的工程结论(有实验/事故) ③未实现 ④已纠正的旧判断。

## 1. 定位与三层职责

目标:让 agent 在固件研究里**持续、可恢复、可验证**地工作。

| 角色 | 负责 | 不负责 |
|---|---|---|
| 模型 | 选方向/方法、解释结果、提出反例 | 维护机器事实、判定结论合法性 |
| 运行时 | 工具执行、超时/取消、上下文、持久化、恢复 | 判断"这个漏洞是否成立" |
| 领域层 | 事实与不变量(假设/尝试/观察/证据/结论) | 产生研究判断 |

判据(方法论)在 `.agents/skills/`、`knowledge/`、`AGENTS.md`;内容架构见 `docs/AGENT_ARCHITECTURE.md`;机器可核部分才进代码。
代码**不得自带新判据**:闸门红 ≠ 结论变,而是"补事实"或"先改判据"。

## 2. ① 模块(全部有测试)

```
fwcore/  db.py(SCHEMA v17/WAL/BEGIN IMMEDIATE)  domain.py(唯一写入口+不变量)
         tools.py(内置 ToolSpec)                toolreg.py(manifest→Registry,4 种注册路径)
         runtime.py(循环/检查点/恢复/预算/流式消费/对话持久化失败显式暂停)
         streaming.py(事件/取消令牌;真实 HTTP 取消用 socket shutdown)
         research.py(池/三镜/进度); prompting.py + prompts/system.md(System Prompt); skills.py(Skills Loader);
         knowledge.py(Research Knowledge); toolset.py(默认工具集完整性); toolmeta.py(已有工具元数据); pyenvs.py(Agent Python 环境队列); adapters_ghidra.py + adapters_qemu.py
         compact.py(压缩)                        contextinfo.py(上下文状态)
         mcp_runtime.py(MCP 桥)                  adapters_ida.py + winenv(IDA/Windows)
         gates_db.py(权威 Gate)                  models/model_client(适配器/协议)
         policy.py(Authz)  limits.py(边界)  watchdog.py  migrate_all.py  cli.py
fwapp/  应用层(TUI/print,薄组合;见 docs/APP.md)
scripts/ 演练与 e2e        eval/ 答案隔离评测
tools/registry/*.json  仓库工具 manifest(8 个); .agents/skills/*/SKILL.md
```

## 3. 存储:DB 是权威,文件是叙述/材料(不是双写)

**职责四分**(`state/<目标>/` 下的东西):

| 类别 | 存什么 | 谁读 | 进 Git |
|---|---|---|---|
| **SQLite**(`state/fwcore.sqlite3`) | 目标/面/假设/尝试/观察/证据/结论;runs/tool_calls/events/usage/checkpoints;**conversation_entries**(面向用户的 append-only 对话) | 程序判定(`gates`/`derive`/`context`/`derived_check`);TUI transcript | 否 |
| **Artifacts**(`artifacts/`) | 固件/反汇编/原始工具输出/截图/PoC 等原始材料;DB 用 `tool_calls.output_path` 关联回查 | 人复核 + 复现包引用 | 否 |
| **Markdown**(`notes.md`/`report/`) | `notes.md`=可选历史/人工摘要，不自动维护；`report/`=最终交付(advisory/INDEX/attack-surface/CLOSURE/复现包) | 人 | `report/` 是对外材料；`notes.md` 不承担研究状态 |
| **临时**(`state/_scratch/`) | 演练/E2E/评测输出、一次性报告 | 当次会话 | 否(有保留期,结束可删) |

| 谁写 | 写什么 | 参与判定 |
|---|---|---|
| 模型的 `state_*` 工具 | 假设/尝试/观察/证据/复核/结论/面处置 | ✅ |
| runtime | `runs`(预算)、`checkpoints`(恢复)、`tool_calls`(产物/来源)、`events`(审计) | ✅ |
| 模型/人 | `notes.md`(可选历史/人工摘要；不自动维护) | ❌ |
| finding/收尾时 | `report/`(advisory/INDEX/attack-surface/CLOSURE/复现包) | 材料门读 |

**唯一权威**:研究事实、决策、下一步只认 SQLite；`notes.md` 是可选历史，`attack-surface.md` 是收尾导出视图。
运行路径**不读 Markdown**:缺库/缺目标 ⇒ `derived_check`/`gates` 直接 FAIL(没有 legacy 回退);
旧目标由 `fw-core migrate --all` 一次性导入(`documents` 保原文,结构化内容进各表,模糊内容标 `needs_review`)。

研究表:`targets surfaces hypotheses attempts observations evidence evidence_checks
conclusions runs checkpoints tool_calls events mirror_checks research_logs documents meta`。
知识表:`knowledge_entries knowledge_entry_versions knowledge_entry_tags knowledge_entry_fts`(FTS5)。

保留机制:`checkpoints_keep=120`/run(写时裁剪)→ `_shrink_history`(老工具输出 2000 字符 +
保留路径/`tool_call_id`)→ 库超 `max_db_bytes=256MB` 删最老 checkpoint 仍超则暂停。
清理:两档(**使用中/收尾未归档=提交中** 只清垃圾;**归档/不要了** 才释放载体/派生物/原件,需用户明说)。

## 3b. 生命周期与并发不变量(程序强制,不是约定)

- **单 owner 租约**:`runs.pid + pid_starttime` 是所有者凭据。接管走 `_claim_run` 的 CAS
  (状态+旧 owner 全匹配);另有活主(同主机、pid 活、starttime 一致)时 **拒绝** 并发 resume。
  ＞ SQLite 事务只保证单条 UPDATE 原子,不保证"读状态→接管"这段逻辑。
- **心跳不抢所有权**:`_heartbeat` 是 CAS(pid/starttime 对不上就返回 False);turn 每个循环先
  `_owns` 校验,失去所有权立即退出。
- **所有权是所有生命周期写的原子条件**(不是"先 `_owns` 再写"):
  - `_checkpoint`:step 更新与 checkpoint 插入在同一事务,`UPDATE ... WHERE state IN ('running','paused')
    AND pid/starttime 全匹配` rowcount=0 ⇒ 不插入;
  - `_persist`/`_persist_counters`:计数用同一条条件 UPDATE + `MAX` 写,检查与写入不可分割;
  - `_reserve_call`:model_calls 预留要求 state='running' + owner 全匹配;预留失败调用方
    不得发起模型请求。预留成功后、请求前统一用 `is_running_owner`(state 必须是 running,
    且 pid/starttime 匹配)再核验一次;摘要调用与普通调用都走同一检查,取消/接管后
    不得再发出请求;工具批次也用同一检查,暂停后剩余工具不执行。
  - `except BaseException`:只有"仍 running 且仍是 owner"才写 paused;接管后旧进程
    不覆盖新 owner 状态,也不写 checkpoint;
  - `turn` 恢复 running:handback(user_stop)/state/owner 与更新是同一条 UPDATE,
    取消与恢复的竞争不会复活已取消的 run;
  - `_limit_hit`/`_finish_state`/rounds/取消写入同样使用 owner+state CAS。
  ＞ 仍然存在"预留成功后瞬间被接管"的极小窗口,但旧进程不会再预留/发起新请求;
  SQLite 事务不保证跨网络调用的租约,故以"预留+请求前复核+看护"兜底。
- **工具批次围栏**:模型调用期间被接管 ⇒ 同一回复里剩余工具(含 `state_*` 写操作)不执行,
  逐个补 cancelled 保证协议完整。
- **看护先赢竞争再动手**:hard / stale 两分支都是 `UPDATE ... WHERE state='running'`(rowcount=0 跳过),
  赢下后才 killpg + 写事件;`recover_stale` 同样 CAS,且只对"同主机 + 有 pid 记录 + 已死"执行 killpg。
- **pgid 安全三条件**:杀进程组必须①同主机 ②`.pgids` 带 starttime ③进程组仍属该 starttime
  (缺 starttime ⇒ 不杀,防 pgid 复用误伤);接管死亡 owner 时清孤儿,并把它遗留的
  `tool_calls.status='running'` 标 cancelled。
- **预算可核对**:`runs.budget_note` 记录每次 start/resume 生效的 limits 与最近 limit_hit;
  计数每步落库;预算只暂停(不写结论),resume 延续同一计数。

对应测试:`test_runtime_core`(租约/心跳/批次/handback;接管后预留/计数/checkpoint 拒绝写、
异常路径不覆盖新 owner、user_stop/异主 resume 拒绝)、`test_watchdog`(CAS 与 pgid 安全)、
`test_cross_process_resume`(跨进程 resume 幂等;预留后接管/取消不发起摘要或普通模型调用)。

## 4. 工具接入(统一注册契约,不统一内部实现)

四条路径:内置 `ToolSpec`(state_*/shell_exec/fs_*)、`tools/registry/*.json`(script/python)、
feature 适配器(ida)、MCP(`mcp__<server>__<tool>`)。

```json
{"name":"x","description":"…","input_schema":{…},"kind":"script",
 "argv":["bash","tools/x.sh","{a}"],"timeout":900,"timeout_arg":"timeout",
 "risk":"high","capability":"extract.binwalk","provider":"repo:x.sh",
 "requires_file":"tools/x.sh","observer":"x"}
```

执行链:`Registry.call`(call_key 幂等 + risk/Authz + capability)→ 适配器 → `run_process`
(独立 PGID、取消杀组、输出上限、ok_exit_codes)→ **输出留在 tool result/session**(Pi 2000 行/50 KiB 尾部;
截断时才写系统临时 `full_output_path`；只有工具显式产物才落 `state/artifacts/<run>/`)
→ `tool_calls` 行(status/exit/sha/provider/arch/observation)→ `db_tool_call_id`(TC-…) →
`_bounded_tool_result`(Pi 尾部、始终合法 JSON、保留路径/哈希/错误/`full_output_path`)→ 模型。
能力层:`fw-core capabilities` 探针(`ready/partial/deps_missing/arch_mismatch/missing`);
manifest 的 `capability` 不满足 ⇒ 进 `unavailable` 报告,不静默消失。

## 4b. 工具输出边界(单一来源: `fwcore/output.py`)

- **进程输出**（`shell_exec` / `run_process`）：模型看尾部 `2000` 行或 `50 KiB`，先到者生效；
  截断时临时完整输出由 `full_output_path` 给出，**不自动写 workspace artifact**。
- **文件读取**（`fs_read`）：保留头部 `2000` 行或 `50 KiB`，支持 `offset/limit`
  分段读；`full_output_path` 这类框架临时文件可被 `fs_read` 读回，否则路径没有意义。
- **manifest / toolkit**：`output_tail` / `text` 走 `truncate_tail`（2000 行/50 KiB）。
- **MCP 配置/发现**：复用 Pi 的 `mcpServers` 契约，不另造检测格式：用户级
  `~/.pi/agent/mcp.json` → 项目 `<root>/.pi/mcp.json` → 兼容目标级 `state/mcp.json`；
  显式 `FW_MCP_CONFIG` 覆盖以上来源。项目只覆盖 `enabled`/`exposure`/`toolExposure`，
  完整条目替换；单个坏条目跳过并保留其它服务器；`hidden` 必须真的不注册。
- **MCP**：单独按 Pi MCP 策略：模型可见文本 `20 KiB` **中间截断**（保留头/尾），
  完整文本写临时 `full_output_path`；`isError` 也走同一路径。完整
  `structuredContent` 写临时 `structured_full_output_path`，模型只保留小摘要。
  MCP `content` 的 text/image 转成有序 `parts`：图片按 MIME/base64/大小/数量校验后
  保存到 `state/artifacts/mcp-content/`（内容哈希，跨 checkpoint/进程可读；不是系统临时文件），历史只存路径/哈希，不把 base64 放进 JSON/DB；模型适配层再把
  image part 读成 data URI。Chat Completions 可在显式 `image_mode="tool_parts"`
  时走 image content part；标准 OpenAI Responses 走合法的 `function_call_output`
  text/image 数组；纯文本模型只收到路径和明确限制。HTTP SSE 按行增量读取，
  只接受与请求 id 匹配的响应。模型能力来自 `state/model.json` / 环境变量：
  `api=responses`（合法的 `function_call_output` 图片）、`supports_vision`、
  `image_mode=tool_parts`（仅在端点确实接受 tool content image parts 时启用）；
  纯文本模型只得到路径和限制。`notifications/tools/list_changed` 会标记客户端，
  Runtime 在每个 turn 开头主动 `tools/list` 刷新，移除旧工具，避免长期使用过期列表。
- **MCP Resources**：连接服务器声明 `capabilities.resources` 时才注册
  `list_mcp_resources` / `list_mcp_resource_templates` / `read_mcp_resource`；
  支持 server 过滤、分页、resource_link、嵌入 text/image/binary/audio；图片按图片处理，
  二进制落临时文件，音频明确标注无音频输入能力。资源工具本身跨 server，只要有任一
  server 未声明 `external=false`，三个资源工具也按外部工具走同一授权门。
- **conversation/checkpoint**：conversation 的 live tool payload 与 Pi 边界一致；
  checkpoint 的磁盘保护另有 `CHECKPOINT_TOOL_TAIL_CHARS=2000`，不混同 live 输出。
- **错误/探针摘要**（如 160/600 字符）只用于状态说明，不承载完整工具输出。
- 需要长期保存时由 Agent 自己用 bash 重定向；临时 `full_output_path` 不能当 evidence 路径。

### 常量来源（不要把本地经验当成 Pi 规范）

- **Pi 原值**（有来源可核）：bash live output `2000` 行 / `50 KiB` 尾部；
  read `2000` 行 / `50 KiB` 头部；MCP 模型可见文本 `20 KiB` 中间截断；
  structured/full read `1 MiB`；compaction 单条 tool result `2000` 字符。
- **Pi MCP 的 script/full 分层**：Pi 把完整 `CallToolResult` 留给 codemode，
  模型只看转换后的 content。fwcore 没有 codemode，因此模型可见 structured
  也只能留摘要，完整 JSON 落临时文件并在 `structured_full_output_path` 指回。
- **本地工程边界**（安全/显示/兼容，不是 Pi）：MCP transport 单条消息 `16 MiB`、
  Skill 文件 `512 KiB`、错误摘要 `160/600` 字符、legacy 修复 excerpt `1500`、
  MCP observation `2000`、图片单张 `4 MiB`/单结果 `8` 张（含 embedded resource）、
  二进制/音频单块 `8 MiB`、资源分页 `50` 页。
  这些应保持命名常量/局部理由，不能反过来冒充 Pi。

## 5. 上下文生命周期(静态前缀 + 尾部状态 + 阈值压缩)

- 请求 = `[system 静态规则] + history(append-only) + [user 本轮状态尾块]`；状态块含派生量/事实/进度
  + `[Context status]`(model/window/估算/可用/预留/阈值/上次真实 usage)。**不进 system。**
- 用户消息和本轮状态块在请求成功后按发生顺序追加到 history/checkpoint：
  用户消息不再只藏在临时 state 里，状态块以 `_context_only` 隐藏 user 消息保存。
  下一轮是在旧前缀后追加，而不是重写尾部；这是 Pi 的 append-only/custom-context 语义。
- 阈值 = `window − max(output_reserve, 10% window)`;生产(1M 窗口/8k 预留)= 90%。
- 压缩:按**协议组**切(assistant.tool_calls + 全部 tool 结果),保留最近 `keep_recent_tokens`;
  被丢弃的历史走 `summarize_covered`:分块(默认 60k/块)→ 超大消息切片 → 归并;超 `summarize_max_calls`
  或任一块为空 ⇒ `SummarizationIncomplete` ⇒ **历史原样保留** + `run.compact_failed` + 冷却。
- 摘要调用与普通调用同一条**调用前**预算检查,计入 `run_calls` 与 `model.call(kind=summarize)`。

## 6. 外部开发工具的历史对照(不是依赖,不再扩展)

fwcore 独立运行,不调用也不适配任何外部 agent 运行时(`.pi/` 不入库、无 Adapter/Hook/Bridge)。
下面只是当年读 Pi 源码得到的**工程对照**,保留作历史参考,不构成产品契约:
会话是 append-only entries + projection;system 静态、动态上下文作为消息注入;
阈值/溢出 compaction(keepRecentTokens 20k + 结构化摘要);usage 字段 `input/cacheRead/cacheWrite/output/reasoning`。
DeepSeek 自带**前缀缓存**(默认开启,客户端只需保持前缀稳定并读 usage)。
实测:Pi 命中 98.96%(n=11,561,均值 423k);fwcore 31 次(均值 12k)89.8%;
**同规模(<20k)**:Pi 88.47%(n=338) vs fwcore 89.8% —— 持平。此前“98.36% vs 89.8%”是规模差异。
可复核脚本:`scripts/pi_cache_stats.py`(只读/脱敏/`--by-size`)。

## 7. ② 已验证的工程结论(有实验/事故)

1. 成本类理由不是降级理由;停只有四种合法理由。
2. 旧停止声明不能当本轮理由;半轮必须被识别。
3. `qemu-user -L` 不翻译 `execve` ⇒ 设备侧执行需 chroot/容器证据。
4. 附件包必须自含(禁符号链接)+ README 可独立跑通。
5. 逐字节一致只证明迁移兼容,不证明语义正确。
6. 工具名 `^[A-Za-z0-9_-]+$`(点号名被端点 400 拒)。
7. thinking 模式要求每条 assistant 回传 `reasoning_content`(空串也要);新进程 env 未继承会 400
   ⇒ 默认补、显式 `FW_REASONING_CONTENT=0` 才关。
8. 步数预算是每次调用一份,不能当绝对上限(否则 resume 原地不动)。
9. 高风险工具默认拒绝;授权范围内分析工具自主运行(`Authz` 区分)。
10. 不要凭"命令存在"推断"能力可用"(aarch64 tracer 实为 x86-64 等),必须探针。
11. 动态内容放 system 首位会让前缀缓存从 98% 掉到 1%(实测);动态状态必须在尾部。
12. 请求长度分层后缓存才可比;不同均值规模不可直接比较。

## 8. ③ 未实现 / 未验证(不得写成已验收)

| 项 | 状态 |
|---|---|
| 真实 >500k 长历史的稳态命中率 | 未测(只有 31 次均值 12k 的受控实验) |
| 真实 API 多轮压缩后的缓存重建 | 未测(只有离线模拟) |
| 第三方网关缓存行为 | 未测(实测走官方 api.deepseek.com) |
| 模型切换 / 自动重试 | 未实现(失败上抛、预算暂停) |
| checkpoint append-only 日志 | 未做(每步全量快照,写入放大已记录) |
| 报告自动生成 | 未做(advisory 由 agent 写,`derived_check` 校验) |
| Web/图形界面 | 无(只有 CLI) |

## 9. 已知限制

1. 每步全量 checkpoint(427 条/114MB 的 drill 库即是例证);`checkpoints_keep` 只按 run 裁剪。
2. 摘要为"每次新生成",无增量更新;长历史压缩需要多次摘要调用(计入预算)。
3. 证据来源强度:弱来源(`command-write`)不计入 direct;但多数真实证据仍是弱来源。
4. 单模型适配;MCP 与 IDA 是适配器,Agent Loop 不感知其细节。
5. 无 CI;验证靠 `tools/fwcore_verify.sh`(pytest 75 文件 + capabilities + gates + 隔离 + ROADMAP 一致性 + 运行产物边界 + 迁移一致性/幂等 + git clean)。
