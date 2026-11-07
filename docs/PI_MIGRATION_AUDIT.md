> **历史审计**:本文件记录 `c0fc820` 时的盘点与缺口;之后的内容架构重构见 `docs/AGENT_ARCHITECTURE.md`。
> 文中“当前/现在”状态可能已被后续实现更新,不要把本文件当最新运行时契约。

# Pi → firmware-loop 迁移完整性审计

> 审计基线: `feat/agent-framework` @ `35473d6`(2026-11-07)
> 审计范围: 旧 Pi 项目/用户级资产、旧工作区文档、Git 历史、Pi 会话、当前 `fwcore/fwapp`、`AGENTS.md/LOOP.md`、`knowledge/`、`prompts/`、`tools/`。
> 本文件只做审计与迁移建议;未修改 Runtime、工具注册或研究流程。API key/token 不记录、不展示;Pi 配置只记录结构与状态。

## 0. 结论摘要

**结论: 当前固件研究核心能力已基本迁移,但“旧 Pi 资产 → 当前 Agent 实际可见/可执行”的链路并不完整。**

最重要的三个事实:

1. **规则文件存在 ≠ 规则被注入**: `Research.rules_prompt()` 每个规则文件只注入 12,000 字符。
   `AGENTS.md`(25,535 字符)只保留 5/12 个二级章节;`LOOP.md` 没有 `##` 级标题,
   实际只注入前 12,006 字符,末尾的「三镜刷新」「结论依据层级」「反水坑」「扫面/提取」**没有进入提示词**。
   `misreports.md` 27 节只注入 11 节,`vuln-patterns.md` 24 节只注入 11 节。
2. **旧工作流文档没有进入当前仓库**: `AI辅助复现工作循环.md`、`固件研究线/循环定制.md`
   包含选靶/固件获取/环境决策树/FirmAE·EMBA·FACT·boofuzz·GhidraMCP 等实际工作法;
   当前 `AGENTS/LOOP/knowledge` 没有全文对应,部分原则被更强的现行方法替代,但选靶与获取链条明显缺失。
3. **Pi 侧浏览器 MCP、扫描/截图/防假收尾护栏没有迁入 fwcore 运行时**:
   旧 Pi 用户级 `mcp.json` 的两个 Chrome MCP、`.pi/guards/*` 仍只在 Pi harness 中有效;
   fw-core 没有 `state/mcp.json`,也没有对应的 tool-call 拦截层。当前 `AGENTS` 仍在引用
   `pi mcp list`、`/mcp`、`/reload` 等 Pi 命令,对 fw-core 用户是失效指引。

## 1. 旧资产来源清单

### 1.1 旧固件工作区(仓库外,仍可访问)

| 资产 | 路径 | 规模 | 性质 |
|---|---|---|---|
| 通用 AI 复现方法 | `~/整活/漏洞复现/AI辅助复现工作循环.md` | 165 行 | 旧方法论:情报→假设→环境→信号→PoC→执行→迭代→归档 |
| 固件线定制 | `~/整活/漏洞复现/固件研究线/循环定制.md` | 123 行 | 固件选靶/获取/提取/反编译/信号/Agent 集成 |
| 威胁情报原文 | `~/整活/漏洞复现/威胁情报/Anthropic-TI-2026-09.md` | 168 行 | 旧方法论文的原始情报来源 |
| 操作者笔记 | `~/整活/漏洞复现/运维笔记.md` | 23 行 | **明确“agent 不读取”** 的 harness/操作事故记录 |
| 旧 CVE 复现项目 | `CVE-2026-58025`, `CVE-2026-64638`, `CVE-2026-85046`, `cve-2026-85706` | 4 项目 / 341 文件 | 历史复现资料、PoC、环境脚本;不是框架本体 |
| 旧 harness 配置 | `opencode.json`, `.zcodeignore`, `.claude/` | 少量 | OpenCode/ZCode/Claude 遗留;非 Pi 产品资产 |
| 报告参考 | `~/整活/报告参考/*.docx` | 7 份 | 报告格式参考;非固件研究资产 |

### 1.2 Pi 用户级资产(`~/.pi/agent`,只读盘点)

| 资产 | 内容/状态 | 与固件研究的关系 |
|---|---|---|
| `settings.json` | 4 个包:`pi-lens`、`pi-code-review`、`pi-web-access`、`@rustywelch/pi-fuel-gauge` | 开发/浏览辅助,不是 fwcore 产品依赖 |
| `extensions/fw-loop-guards.ts` | 将项目 `.pi/guards` 转发到个人扩展目录 | 只在 Pi 中加载;fw-core 不加载 |
| `extensions-disabled/context-truth.ts` | 把真实上下文占用注入 system prompt | Pi-only;fwcore 用 `contextinfo`/状态消息替代 |
| `mcp.json` | `chrome-devtools`(headless)与 `chrome-live`(用户 Chrome) | 旧 JS 取件/提交能力;**未迁入** `state/mcp.json` |
| `models.json` | DeepSeek provider(值已脱敏) | 当前模型适配层的参考;实际配置由 `state/model.json`/env 提供 |
| `sessions/--home-juryorca-整活-漏洞复现-firmware-loop--` | 10 个 JSONL,约 63.9 MB | 旧 Pi 研究会话;工具使用以 `bash` 为主 |
| `npm/` | Pi 扩展依赖 | 开发环境,非交付物 |

Pi 会话工具调用统计(10 个 firmware-loop 会话):

| 工具 | 次数 |
|---|---:|
| `bash` | 8,888 |
| `codemode` | 338 |
| `edit` | 249 |
| `lens_diagnostics` | 226 |
| `write` | 172 |
| `read` | 131 |
| `ralph_start/ralph_done` | 5 |
| `compact_now` | 1 |

**判读**: 旧 Pi 的“研究工具”并不是一组 Pi 自定义 Tool,而是 **bash + 仓库脚本 + IDA/MCP**;
因此迁移重点应是脚本能力、调用约束与权限层,不是 Pi Extension API 本身。

### 1.3 Git 历史资产

- 初始版本 `b720438`(2026-10-30)有 **60** 个受版本控制文件;当前 `35473d6` 有 **217** 个。
- 初始版本包含 `.pi/{dev-notes,guards,prompts}`、`knowledge/`、`prompts/`、`tools/`、两份模板。
- 后续删除/迁出的主要资产:
  - `.pi/dev-notes.md`、`.pi/guards/*`、`.pi/prompts/*`(当前文件系统仍保留,但被 `.gitignore` 忽略);
  - `docs/OVERVIEW.md`、`docs/ARCHITECTURE-RUNTIME.md`、`docs/PI-CORE-ALIGNMENT.md`(内容合并/重写);
  - `fwcore/pi_bridge.py`、`fwcore/pi_hook.py`、`tests/test_pi_bridge.py`、`test_pi_hook_gate.py`(Pi 耦合移除);
  - `.ralph/*`(旧路线自动化状态);
  - 旧演练/评测记录(历史材料,不符合交付目录规范)。
- 旧 `prompts/*.txt` 仍存在,但文件头已标 **DEPRECATED**;当前 `Research.rules_prompt` 不加载它们。

## 2. 工具接入矩阵

判定口径: “完整”=能力+关键语义保留;“适配”=实现改变但能力保留;“部分”=能力或约束有缺口;
“未迁移”=找不到对应实现;“有意废弃”=明确不应进入 fwcore;“无法核实”=来源不足。

| 原始资产 | 原始位置 | 原有能力 | 新位置 | 实际调用路径 | 验证方式 | 状态 | 问题 |
|---|---|---|---|---|---|---|---|
| Pi `bash` | Pi harness | 任意 shell/脚本执行 | `shell_exec` | `fwcore/tools.py` ToolSpec;`Runner.turn` 执行 | `test_shell_exec_records_statuses` | 完整 | 高风险需授权;无 Pi 的 shell 细节 |
| Pi `read` | Pi harness | 读文件/摘要 | `fs_read` | 同上 | `test_runtime_core` 等 | 完整 | — |
| Pi `write`/`edit` | Pi harness | 低风险写文件/结构化编辑 | **无独立工具** | 只能 `shell_exec` 或外部编辑器 | 无专用测试 | **部分** | 旧 249+172 次调用;fwcore 缺 write/edit schema,模型只能高风险 shell |
| Pi `codemode` | Pi 扩展 | JS 批量编排工具 | 无 | fwcore 多 tool-call 循环 | 旧会话 338 次 | 有意废弃 | 不复制 Pi Extension 生态;需在提示词说明多工具替代 |
| `pi-lens`/`pi-code-review`/`pi-fuel-gauge` | Pi 用户包 | 开发诊断/审查/上下文显示 | 无(开发侧外部) | 不在 fwcore | — | 有意废弃 | 不属于研究产品;不影响 fwcore 研究 |
| binwalk / unblob | 旧 tools/环境 | 固件提取 | `fw_extract` | `tools/registry/fw_extract.json` → `tools/fw_extract.sh` | `tests/test_fw_extract_tool.py` | 完整 | — |
| qemu 运行 | 旧 `emu_run.sh` | 用户态/整机仿真 | `emu_run` | `tools/registry/emu_run.json` | 现有工具测试 | 完整 | — |
| CGI fuzz | `cgi_fuzz.sh` | 有界变异 fuzz | `fuzz_cgi` | `tools/registry/fuzz_cgi.json` | `tests/test_fuzz*` / 脚本 | 完整 | 高风险需授权 |
| IDA 无头 | `ida_*.sh` / Windows IDA | 反编译/查询 | `ida_query` + `fwcore/adapters_ida.py` | `tools/registry/ida_query.json` feature adapter | IDA e2e/适配器测试 | 适配 | Windows/端口配置迁移到 `fwenv`;运行依赖仍外部 |
| Ghidra 脚本 | `tools/ghidra_scripts/*.java` | 函数画像/反编译导出 | `ghidra_query` 声明 | `tools/registry/ghidra_query.json` | **注册不可用** | **部分** | 缺少 `tools/ghidra_run.sh`;注册报告如实列为 unavailable;旧 Pi 用 bash 跑 Ghidra |
| QNAP cipher / QELF decrypt | `qnapcipher`、`qelf_decrypt.py` | QNAP 加密/QELF 解密 | 文件保留 | 只能 `shell_exec`;无 registry | `tool-quirks` 有配方,无专项测试 | 部分 | 可发现性/schema 缺失,模型需自己知道路径 |
| Go Gin 路由枚举 | `go_gin_routes*.py` | 二进制路由候选提取 | 文件保留 | 只能 `shell_exec` | 旧会话 20 次 | 部分 | 未注册为研究工具 |
| `parse_signal.py` | tools | 信号判读 | `research_signal` | `tools/registry/research_signal.json` | `test_parse_signal` 等 | 完整 | `MISS` 退出码 1 已声明 |
| derived/queue/kb | `derived_check.sh`、`queue_state.sh`、`kb.py` | 派生量/队列/KB 编辑 | `research_derived`、`research_queue` | registry;`kb.py` 仍 CLI | 工具/CLI 测试 | 适配 | `kb.py` 未注册为模型工具;KB 编辑仍偏开发侧 |
| termshot / ansi2png | tools | 声明式终端截图 | `termshot.sh` | 报告模板/工具;无 registry | `tools/termshot.sh` + 模板 | 适配 | 截图 guard 未迁入 fwcore;只靠规范 |
| tmux-capture | tools | 旧手工截图 | 文件保留 | 无注册 | 无 | 有意废弃 | 与 termshot 规范冲突;仅历史保留 |
| Chrome DevTools / Chrome Live MCP | Pi `mcp.json` | JS 页面取件、提交表单 | `fwcore/mcp_client` 通用 MCP | 需要 `state/mcp.json`/`FW_MCP_CONFIG` | `check.sh` 报 MCP 配置缺失 | **部分/未配置** | 旧两个 server 未搬入;fw-core 当前无浏览器能力 |
| IDA MCP / `start_mcp.template.py` | tools/旧会话 | IDA MCP 端点 | `fwcore/mcp_runtime` + IDA adapter | `mcp_client`/`ida_query` | MCP 测试 | 适配 | 旧模板不是直接运行时;需配置化 |
| 扫描 guard | `.pi/guards/scan-guard.ts` | 阻断全盘扫描 | 无 fwcore 对应 | 仅 AGENTS/LOOP 规则 | 无 | **部分/未迁移** | fw-core 的 `shell_exec` 不做命令级扫描拦截 |
| 截图 guard | `.pi/guards/screenshot-guard.ts` | 阻断手绘/旁路截图 | 无 fwcore 对应 | 仅模板/规则 | 无 | **部分/未迁移** | termshot 仍可被 shell 绕过 |
| continue guard | `.pi/guards/continue-guard.ts` | settle 前查假收尾/借口降级 | 无 fwcore 对应 | 仅 Stop Rules 文档 | 无 | **部分/未迁移** | fw-core 没有 harness settle 钩子;靠提示词 |
| context-truth | Pi 扩展 | 真实上下文占用 | `fwcore/contextinfo.py` + state prompt | `Runner` 注入 | `test_context_*` | 适配 | 行为已迁移,不再是 Pi Extension |

## 3. 研究工作流矩阵

| 原始资产/环节 | 原始位置 | 原有能力 | 新位置 | 实际路径/注入 | 验证 | 状态 | 问题 |
|---|---|---|---|---|---|---|---|
| 选靶/立项 | `循环定制.md` 层 1 | CVE 密度 × 攻击面 × 补丁可及性、SDK 血统聚类 | `AGENTS.md` 立项/目标取舍;`knowledge/sdk-bloodlines` | 系统提示(AGENTS 部分注入)+ 人工 | 无专项测试 | **部分** | 现重点是“自研 vs 上游”;CVE 密度/补丁可及性/选靶决策树缺失 |
| 固件获取 | `循环定制.md` 层 2 | 官网/OTA/云存储/硬件拆解 | `AGENTS` 取件/`intel/firmware`;`knowledge/tool-quirks` | 文档 + Chrome MCP(当前无配置) | `check.sh` 只查 MCP 缺失 | **部分** | 四条获取路径未形成流程;浏览器取件链路当前不可用 |
| 提取/解包 | `循环定制.md` 层 3 | binwalk/unblob、加密/非标、EMBA/FACT | `LOOP` 扫面/提取;`fw_extract` | LOOP 注入(但被截断)+ registry | `test_fw_extract` | 适配/部分 | binwalk/unblob 完整;EMBA/FACT 未迁移;LOOP 末尾“扫面/提取”当前未被注入 |
| 反编译与假设 | `循环定制.md` 层 4 | IDA/Ghidra/模式库 | `AGENTS` 建模;`vuln-patterns`;IDA adapter | 提示词 + 工具 | 模式库/Ghidra 部分 | **部分** | Ghidra 工具不可用;模式库只有 11/24 节进入提示词 |
| 环境与信号 | `循环定制.md` 层 5 | qemu-user/FirmAE/全系统/真机;boofuzz | `knowledge/methodology` 模拟分层;`parse_signal` | 方法论文档 + signal tool | 现有 qemu/容器测试 | 适配/部分 | FirmAE/boofuzz 未迁移;真实设备被工作区明确排除(可接受) |
| 迭代/轮/停 | `AGENTS`/`LOOP` | 一轮一假设、停/不停、三镜 | 同文件 + Domain/Gates | `rules_prompt` 注入 | 测试 + Gate | 内容完整/运行时**部分** | `AGENTS` 循环守则、`LOOP` 三镜/依据层级未进入提示词 |
| 证据/复核/结论 | `AGENTS`/`LOOP`/Domain | 结构化事实、复核、范围 | `fwcore/domain.py`、`gates_db.py` | 工具/闸门/DB | `test_evidence*`、`test_conclude*` | 完整 | 当前比旧文档更强 |
| 三镜/误判回溯 | `LOOP` | 机制/作者/状态三镜、回溯 | `LOOP` + `Research.pool`/`record_mirror` | 但 `LOOP` 末尾未注入 | 无专项 runtime 测试 | **部分** | 文档完整,Agent 当前看不到该段 |
| 报告/提交 | `AGENTS`/模板 | advisory/索引/CVE/不提交 | `tools/templates`、`gates_db` | 模板 + 文档 | `derived_check` | 适配 | 浏览器 MCP 缺失;Pi 命令引用失效 |
| 长上下文/恢复 | Pi/Pi Core 参考 | 压缩、checkpoint、resume | `fwcore/compact.py`、`runtime`、DB | Runtime | 大量测试 | 完整 | Pi Extension 未复制(有意) |
| 日志/下一步 | 旧 notes | 逐轮/停止/决策 | DB `research_logs`、`state_add_log` | Runtime/工具 | 迁移/测试 | 完整 | 旧 Markdown notes 已迁移到 DB |
| 子代理分工 | `prompts/*.txt` | hypothesis-scanner/triage/extractor | `LOOP` 内联步骤 | 文档 | — | 有意废弃 | 旧 OpenCode 子代理不存在;已折叠 |
| Pi harness 护栏 | `.pi/guards/*` | 防假收尾/扫描/截图 | 无 fwcore runtime | 仅文档 | 无 | **部分/未迁移** | 需要产品侧 tool-call 拦截或 Gate |

## 4. 文档/知识资产矩阵

| 原始资产 | 原始位置 | 当前对应 | 实际加载 | 状态 | 问题 |
|---|---|---|---|---|---|
| `AGENTS.md` | 旧工作区/初始 commit | 同文件,已扩展 | `Research.rules_prompt` 读取 | **部分(注入)** | 25,535→11,881 字符;`循环守则`、KB 政策、报告、信号、环境、工具速查未进入 system prompt |
| `LOOP.md` | 同上 | 同文件,已扩展 | 同上 | **部分(注入)** | 无 `##` 标题,仅注入前 12,006 字符;`三镜刷新`/`依据层级`/`反水坑`/`扫面提权` 四节缺失 |
| `knowledge/methodology.md` | 旧 | 同文件(867 行,新增 1 节) | **未注入正文**;仅 `kb_titles` 标题 | 部分/按需 | 旧方法与新经验可 fs_read,但 system prompt 只看到标题 |
| `knowledge/tool-quirks.md` | 旧 | 同文件(1016 行) | 仅标题 | 部分/按需 | 同上;大量环境/仿真细节不自动进入 |
| `knowledge/misreports.md` | 旧 | 同文件(27 节) | `rules_prompt` 注入 | **部分(注入 11/27)** | 16 个反例未进入提示词 |
| `knowledge/vuln-patterns.md` | 旧 | 同文件(24 节) | `rules_prompt` 注入 | **部分(注入 11/24)** | 13 个模式未进入提示词 |
| `knowledge/intel-log.md` | 旧 | 同文件(71 行) | 仅标题 | 部分/按需 | 情报时间线不自动进入 |
| `knowledge/sdk-bloodlines.md` | 旧 | 同文件(15 行) | 仅标题 | 部分/按需 | 旧 SDK 血统细节需主动读 |
| `knowledge/exploits-validated.md` | 旧 | 同文件(38 行) | 仅标题 | 部分/按需 | 已验证链不自动进入 |
| `prompts/*.txt` | 旧 OpenCode | 文件仍在 | **不加载**(文件头 DEPRECATED) | 有意废弃 | 内容已声明折叠;但指向的 `AGENTS「专项任务分派」` 当前已不在 AGENTS,指针过期 |
| `.pi/prompts/fw.md` | 旧 Pi | 文件仍在(未入库) | fwcore 不加载 | Pi-only/部分 | 内容仍写 harness=pi、Pi MCP、`/reload`;fw-core 不适用 |
| `.pi/prompts/提交.md` | 旧 Pi | 文件仍在(未入库) | fwcore 不加载 | Pi-only/部分 | 提交流程依赖 `mcp__chrome-live__*`;当前产品无此入口 |
| `.pi/guards/*` | 旧 Pi | 文件仍在(未入库) | fwcore 不加载 | **未迁移到 runtime** | 旧 3 类护栏只有文档规则 |
| `.pi/dev-notes.md` | 旧开发 | 文件仍在(未入库) | 不存在产品入口 | 有意不迁移 | 开发笔记不属研究知识;不能当产品文档 |
| `docs/OVERVIEW.md` / `ARCHITECTURE-RUNTIME.md` / `PI-CORE-ALIGNMENT.md` | 旧 docs | 合并为 `docs/ARCHITECTURE.md` + `docs/evidence/token-audit.md` | 开发文档 | 适配/有意精简 | 历史 P9 细表有删减;关键工程结论保留在 ARCH §6/证据 |
| `docs/evidence/*drill*`、`.ralph/*` | 旧 | 已删除 | — | 有意废弃 | 历史演练/路线状态,不是产品契约 |
| `docs/ARCHITECTURE.md`/`APP.md`/`ROADMAP.md`/`MIGRATION.md`/`IDA-ENV.md` | 当前 | 当前 | 开发文档 | 完整 | — |
| `tools/templates/report.md`/`cve-request.md` | 旧 | 当前保留 | 报告产出 | 完整 | — |
| 外部 `AI辅助`/`循环定制` | 仓库外 | **未复制** | 无 | **部分/未迁移** | 选靶/获取/环境/信号/MCP 架构等关键内容不在仓库 |
| `威胁情报/Anthropic-TI-2026-09.md` | 仓库外 | 无 | 无 | 外部资料 | 作为旧方法论来源;非运行时资产 |
| `运维笔记.md` | 仓库外 | 无 | 无 | 有意不迁移 | 文件明确“agent 不读取”;属操作者记录 |
| 旧 CVE 目录(4 项目/341 文件) | 仓库外 | 未入框架 | — | 外部历史资料 | 不是框架本体;PoC/环境脚本未归档到 `intel/` |
| `opencode.json`/`.zcodeignore`/`.claude` | 旧 | 仍在 | 无 | 有意废弃/无关 | 旧 harness 配置,不属 Pi/fwcore 产品 |

## 5. 完整度统计(按矩阵行计数)

计数单位: **一个原始能力/文档资产 = 一行**,不把一个工作流拆成多个子步骤来抬迁移率。

| 类别 | 原始行数 | 完整迁移 | 适配迁移 | 部分迁移 | 未迁移 | 有意废弃 | 外部/范围外 | 无法核实 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 工具接入 | 22 | 6 | 5 | 8 | 0 | 3 | 0 | 0 |
| 研究工作流 | 13 | 3 | 1 | 8 | 0 | 1 | 0 | 0 |
| 文档/知识 | 23 | 2 | 1 | 12 | 1 | 5 | 2 | 1 |
| **合计** | **58** | **11** | **7** | **28** | **1** | **9** | **2** | **1** |

“无法核实 1”是横切项(2026-10-30 之前无 Git 历史),不重复计入 58 行。
“外部/范围外”指旧 CVE 现场与威胁情报原文;不是 fwcore 框架资产,不应为凑数复制。

- “部分”包括:内容存在但未进入实际提示词、工具文件存在但未注册/不可用、旧文档只有摘要或章节缺失。
- “有意废弃”不是遗漏:Pi Extension/OpenCode/子代理/`.ralph` 等按当前架构不复制。
- “无法核实”:仓库 2026-10-30 之前没有 Git 历史,早期框架编辑无法从 Git 还原;旧 Pi 会话只做了工具/结构盘点,未逐条审计 64 MB 对话内容。

## 6. 实际遗漏与影响(按优先级)

### P0 — 规则注入不完整(必须先修)

**证据**: `fwcore/research.py` 的 `RULES_LIMIT = 12000`;`rules_files` 只含
`AGENTS.md`、`LOOP.md`、`knowledge/misreports.md`、`knowledge/vuln-patterns.md`。
实测:
- AGENTS 25,535 → 11,881,12 个二级章节只保留 5 个;`循环守则`、`Knowledge Base`、
  `报告产出`、`信号约定`、`环境清单`、`工具速查` 未注入。
- LOOP 17,528 → 12,006;文件只有 `###` 标题,`select_rules` 不识别,按字符截断;
  末尾 `三镜刷新`、`结论依据层级`、`反水坑`、`扫面/提取` 未注入。
- misreports 27 节→11;vuln-patterns 24 节→11。

**影响**: 旧项目积累的“三镜/误判回溯/证据层级/环境清单/工具使用约束”在当前 Agent 面前
不是可执行规则,只是磁盘文件;这会直接改变研究行为(假收尾、跳过复核、扫描/工具误用)。
**建议**: 把规则选择改为按 `##`/`###` 标题分段(不能只按 `##`),为 AGENTS/LOOP 定义
“必载章节”清单;或把长篇规则拆成短的核心提示 + 按需工具检索。不要只提高字符上限。

### P1 — 旧固件工作流的关键环节没有进入仓库

**证据**: 旧 `循环定制.md` 的选靶、固件获取、环境决策树、FirmAE/EMBA/FACT/boofuzz、
GhidraMCP 架构等关键词在当前 `AGENTS/LOOP/knowledge/docs` 中零命中;
`AI辅助复现工作循环.md` 的 GTG 形态/环境决策树/信号设计也未迁移。
**影响**: 当前产品能“做研究”,但缺少旧项目已验证的**选靶与获取阶段**工作法;
容易直接从已有固件开始,而不是按 CVE 密度/SDK 血统选择目标;遇到 OTA/加密/硬件路径时
没有成体系的决策树。旧 `knowledge/tool-quirks` 有零散经验,但不是工作流。
**建议(优先级)**:
1. 把旧两份文档的“选靶/获取/环境决策/信号设计”整理进 `docs/` 或 `knowledge/methodology`,并接入提示词;
2. EMBA/FACT/FirmAE/boofuzz/GhidraMCP 作为**可选能力**登记到 `knowledge/tool-quirks`/capabilities,
   不强制安装;缺依赖时如实报 `missing`,不写成已验收。

### P1 — Ghidra 工具实际不可用

**证据**: `tools/registry/ghidra_query.json` `requires_file: tools/ghidra_run.sh`,
但当前文件不存在;`register_repo_tools` 报告:
`unavailable: [{"name":"ghidra_query","reason":"缺少入口 tools/ghidra_run.sh"}]`。
**影响**: 旧 Pi 会话中 Ghidra 命令 234 次;当前模型看到的是“无 Ghidra 工具”,
只能靠 shell 自己拼;脚本资产在,但不可发现/无 schema/无 observer。
**建议**: 补 `tools/ghidra_run.sh`(或改 manifest 指向实际包装),加 smoke test;
能力缺失时保持 `unavailable`,不要伪造注册。

### P1 — 浏览器 MCP 与 fw-core 配置断层

**证据**: 旧 Pi `~/.pi/agent/mcp.json` 有 `chrome-devtools`/`chrome-live`;
`fwcore/mcp_runtime` 只读 `FW_MCP_CONFIG` 或 `state/mcp.json`,当前 `state/mcp.json` 不存在;
`tools/check.sh` 输出“MCP 配置缺失/不可解析”。
**影响**: 旧 JS 壳下载页取件、厂商表单填写/提交能力在 fw-core 不可用;
而 `AGENTS` 仍写 `pi mcp list`、`/mcp`、`/reload`,对 fw-core 用户是错误指引。
**建议**: 增加 `state/mcp.json.example`/迁移说明,把两个 Chrome server 配置迁入 fwcore;
把 `AGENTS` 的 Pi 专属命令改为 fw-core 的 `/mcp` 等价或明确“仅 Pi 开发会话”。

### P1 — Pi 三类护栏未进入 fwcore 运行时

**证据**: `.pi/guards/{continue,scan,screenshot}-guard.ts` 仍存在,但 `.pi/` 不入库;
`fwcore`/`fwapp` 没有 scan/screenshot/continue 的 tool-call 拦截;`tests` 无对应当前测试。
**影响**: 旧 Pi 上“假收尾提醒、全盘扫描阻断、截图旁路阻断”在 fw-core 只剩文档规则;
`shell_exec` 可直接 `find /`、手工截图;`Stop Rules` 依赖模型自觉。
**建议**: 在 fwcore Policy/工具调用层实现等价的最小拦截(扫描根/命令、截图产物、settle 前检查),
并为判定函数加单测;不要复制 Pi Extension API。

### P2 — write/edit 工具缺失

**证据**: 当前 builtins 为 16 个,没有 `write`/`edit`;旧 Pi 会话 `write` 172 次、`edit` 249 次。
**影响**: 模型要修改 PoC/notes 只能走 `shell_exec`(高风险),没有结构化编辑与低风险路径;
研究流程中“写复现脚本/修配置”能力被弱化。
**建议**: 增加 `fs_write`/`fs_edit`(带路径 containment、diff/哈希、低风险),或明确产品只允许 shell
写并更新提示词;后者需要写清风险。

### P2 — 知识库正文未注入

**证据**: `kb_titles()` 只取 `knowledge/*.md` 的 `##` 标题;`methodology/tool-quirks/intel-log/
sdk-bloodlines/exploits-validated` 正文不进入 system prompt;`misreports/vuln-patterns` 只注入部分章节。
**影响**: 旧知识库的详细判据/工具怪癖/已验证链对 Agent 不是“即查即用”,除非模型主动 fs_read。
**建议**: 在 state prompt 加“KB 检索提示”(按当前面指向文件/条目),或提供 `kb_search` 工具;
不要把所有 KB 全文塞进 system。

### P2 — 旧提示词/文档的失效指针

**证据**: `prompts/*.txt` 已 DEPRECATED,但头部仍指向 `AGENTS.md「专项任务分派」`;
当前 AGENTS 已无该节(内容在 LOOP);`.pi/prompts/fw.md` 仍写 `harness=pi`、Pi MCP、`/reload`;
`AGENTS` 仍含 `opencode.json`、`fw.sh`、`pi mcp list`、`/mcp` 等旧 harness 说明。
**影响**: 新 agent 读 AGENTS/旧 prompt 可能按不存在的工具/命令操作;文档层自相矛盾。
**建议**: 把所有 Pi-only 段落标注作用域或移入 `.pi`;fwcore 产品文档只保留 fw-core 可执行路径。

### P3 — 外部历史资产与运维笔记

- 旧 CVE 目录 4 项目/341 文件、`报告参考/*.docx`、`威胁情报` 原文、`运维笔记` 未进入 repo。
  **判断**: 不是 fwcore 框架本体;`运维笔记` 明确 agent 不读;CVE 目录属于目标现场/历史资料,
  不应为了“文件数一致”复制。建议只在 `intel/` 或外部归档保留索引,不纳入产品代码。
- 旧 Pi 会话 64 MB 未导入;会话 JSONL 是旧 harness 的事实记录,不是产品数据;保留在 `~/.pi` 即可。
- 初始 commit 之前没有 Git 历史,无法还原更早的框架编辑;标记为无法核实,不推测。

## 7. 建议恢复顺序

1. **P0 规则注入**: 修 `select_rules` 的 `###` 分段 + 必载章节;验证 `Research.rules_prompt` 实际包含
   `循环守则/三镜/依据层级/扫面提取/Stop Rules`;加“提示词覆盖”测试。
2. **P1 旧固件工作流落库**: 把选靶/获取/环境决策/信号设计整理进 `knowledge/methodology` 或 `docs/`,
   明确“旧经验 vs 现行规则”的取舍;不直接复制过时内容。
3. **P1 Ghidra wrapper / browser MCP 配置**: 修 manifest 或补 `ghidra_run.sh`;把 Chrome MCP 配置迁到
   `state/mcp.json.example`,更新 AGENTS 的 Pi-only 指引。
4. **P1 护栏产品化**: scan/screenshot/continue 的最小拦截 + 测试。
5. **P2 write/edit 与 KB 检索**: 补低风险文件写/编辑工具或明确 shell-only 决策;加 KB 检索路径。
6. **P3 历史资产**: 只建索引/归档说明,不复制 CVE 现场、不导入 Pi 会话。

## 8. 验证状态与边界

- 审计前基线: `35473d6`;工作区 clean。
- 本次只新增审计文件,未修改 `fwcore/fwapp/tools/knowledge` 核心实现。
- 审计后执行 `bash tools/fwcore_verify.sh`;当前测试基线 470 passed(审计报告提交前)。
- 未执行: 旧 Pi 会话逐条语义审计、旧 CVE 项目逐个复跑、真实付费模型调用、Pi 运行时重装。
- 未展示任何 API key/token;Pi 配置仅列出结构和 provider 名称。

## 9. 需要用户确认的迁移取舍

以下不是本次擅自动手的事项,需要确认后再补迁移:

1. 是否把旧 `AI辅助复现工作循环.md` / `循环定制.md` 的核心内容正式纳入 `knowledge/` 或 `docs/`?
2. 是否把 Pi 的两个 Chrome MCP 配置迁入 `state/mcp.json`(需要维护浏览器/代理/Windows profile 配置)?
3. 是否把 Pi 三类护栏改为 fwcore 的产品级拦截(会改 tool-call 执行路径)?
4. 是否补 `write/edit` 低风险工具,还是明确 `shell_exec` 为唯一写路径?

## 附录 A: 尚未接入工具台账(暂不接线)

> 记录时间 2026-11-07,基线 `c0fc820`。本节只登记“还没接/怎么接”的事实,**不实施接线**。
> 判定标准:模型能在工具列表中看到、有 schema、能实际调用并留下 observer/证据,才算“已接”。

### A.1 应接但当前未接

| 工具/能力 | 现有资产 | 当前实际状态 | 缺口 | 优先级 |
|---|---|---|---|---|
| `ghidra_query` | `tools/ghidra_scripts/*.java`、`tools/registry/ghidra_query.json` | 注册为 **unavailable** | 缺 `tools/ghidra_run.sh`;无 schema 对应包装;旧 Pi 只能 bash 跑 Ghidra | P1 |
| `qnapcipher` | `tools/qnapcipher`、`tools/qnapcipher.c`、KB 配方 | 只能 `shell_exec` 手工调用 | 无 registry/schema;模型不知道参数与 selftest | P2 |
| `qelf_decrypt` | `tools/qelf_decrypt.py`、KB 配方 | 只能 `shell_exec` 手工调用 | 无 registry/schema;架构/密钥参数需靠人记 | P2 |
| `go_gin_routes` | `tools/go_gin_routes.py` | 只能 `shell_exec` 手工调用 | 无 registry/schema;输出未结构化 | P2 |
| Chrome DevTools MCP | Pi 用户级 `mcp.json` 的 headless 配置 | fw-core **无配置**;`state/mcp.json` 不存在 | 配置迁移、代理/Chromium 路径、工具白名单 | P1 |
| Chrome Live MCP | Pi 用户级 `mcp.json` 的 `--autoConnect` 配置 | fw-core **无配置**;仅提交场景需要 | Windows profile、`chrome://inspect` 探活、只填不提交边界 | P1 |
| `write` / `edit` | 无;旧 Pi 内置 `write 172` / `edit 249` 次 | 只能 `shell_exec` 或外部编辑器 | fwcore 无低风险文件写/编辑 ToolSpec;路径 containment 与授权需设计 | P2(需产品裁决) |
| KB 检索/读取 | `knowledge/*.md`、`kb_titles` | 只注入标题;正文靠模型 `fs_read` | 无 `kb_search`/`kb_read` 工具;大文件按需读路径未标准化 | P2 |
| 扫描/截图/continue 护栏 | `.pi/guards/*.ts` | 只在 Pi;fwcore 无运行时拦截 | 需在产品 tool-call 层实现等价判定(不是复制 Extension) | P1 |

### A.2 脚本可调用但未注册(是否接线待定)

| 资产 | 当前状态 | 说明 |
|---|---|---|
| `tools/termshot.sh` + `ansi2png.py` | 报告模板/手工 shell 可用;无 registry | 截图规范存在,但 fwcore 无 screenshot guard;是否注册为工具待定 |
| `tools/ida_start.sh` / `ida_call.sh` / `ida_stop.sh` | IDA 能力经 `ida_query` adapter 可用;生命周期脚本未注册 | 不建议把生命周期做成研究工具;保持环境脚本 |
| `tools/chrome-live-probe.py` | 提交探活脚本;未注册 | 只在用户要求提交时用,不是研究工具 |
| `tools/win_stage.sh` | 提交附件中转;未注册 | Windows 路径专用,按需手动 |

### A.3 有意不接(避免以后重复讨论)

| 资产 | 处置 |
|---|---|
| `tmux-capture.sh` | 有意废弃;`termshot.sh` 是声明式替代 |
| `fw.sh` / `start_mcp.template.py` | 旧会话壳/模板;fwcore 有 `fwapp`/`mcp_runtime`,不作为产品工具 |
| Pi `codemode` / `pi-lens` / `ralph` | Pi harness/开发生态;fwcore 不复制 Extension API |
| `db_rebaseline.py` / `tui_smoke.py` | 开发/维护工具,不是研究工具 |
| 旧 CVE 目录脚本 | 外部历史现场,不纳入框架 |
