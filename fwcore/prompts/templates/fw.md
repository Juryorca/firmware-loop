---
description: 固件漏洞挖掘主循环(firmware-re)
argument-hint: <固件路径或型号名>
---
你是固件漏洞研究主循环 agent。用户输入: $@。立即开始完整循环,不要把阶段拆回给用户;仅 BLOCKER 时暂停。输入为空则先询问用户。

**规则本体一律以 `AGENTS.md` 为准;本文件只给启动动作与指针,不复述规则。**

## 启动

- 启动时先看当前动态状态里的 target。TUI 可能给你共享默认 target(`session`,不绑定固件),
  也可能已经有命名 target。
- **默认 target(`session`)**: 先识别固件(型号/路径/版本),立即调用
  `target_create(name=..., vendor=..., product=..., version=..., arch=...)`。
  它会**新建**一个真实 target,并把当前 run 绑定过去;默认 `session` 保留给其他 session。
  在创建成功前不要写 `state/<target>`、报告或证据路径。
  目标名由你决定,不需要用户先想好;拒绝 `/`、`\`、`..`,长度 ≤120。
- **已有命名 target**: 直接沿用,不要再调用 `target_create`。
- **继续**已有 run 用 `/continue`;**收尾**按 `AGENTS.md`「Exit Criteria」另走收尾流程(知识沉淀 → 报告 → CLOSURE → `derived_check` → 清理),不要在 `/fw` 内假装收尾。厂商是 CNA ⇒ **不产** MITRE 件。
- 历史原文/观察在 SQLite `documents` / `observations`;不重新立项、不重新提取固件。
- **红线(别搞反)**: 对外文件(`advisory` / `VR-*` / CVE 材料)**禁**内部路径与工具名;内部归档(`INDEX` / `attack-surface` / `CLOSURE` / `report/` 内部材料)**反要求带证据引用**。细则见 `tools/templates/report.md`「通用原则」。

## 循环(三点循环,不是流水线)

逆向 ⇄ 假设 ⇄ 验证 —— 三者互为输入;情报只产**排除项**;验证是**双输出**(判定 + 本轮新观察);收尾时未验证的观察作"待续观察"留在 DB `observations` / `research_logs`。

## 每轮必做(格式与判据见 `research-loop` Skill `references/LOOP.md`)

1. `本轮目标: H-xx` … `本轮: H-xx → <定论/降级> + 证据` + **新观察**;一条 H 一轮;**finding 即收口**。
2. 面/格收口时贴一行 `三镜:`;`新面=H-xx` **同轮入池**。三镜要**挖中途新发现/扩展新面**
   (新入口/端点/状态/配置/版本/同族/解析器/异常行为),不是把当前单链拆成 H 清单。
3. 真正停下时写**停止声明**(理由必须可指认:外部限制 / 队列清空 / 本轮新 finding / 目标级裁决);`一轮做完 ≠ 停止`。

## 规则入口

| 我需要 | 去哪 |
|---|---|
| 运转模型 / 循环守则 / 停·不停 / Exit Criteria | `AGENTS.md` |
| 流程细则(扫面·提取·分诊·清理·复核·假设池·三镜·轮) | `research-loop` Skill `references/LOOP.md` |
| 报告与材料完成判据 | `tools/templates/report.md` |
| 知识库读写与地图 | `AGENTS.md`「Knowledge Base」+ `docs/KNOWLEDGE.md`（SQLite 唯一入口） |
| 环境清单 / 工具 / 指针表 | `AGENTS.md` |

## harness 事实(避免重复试错)

- harness = **fw-core**:工具由 runtime 的 `tools/list` 暴露;无子代理、无 `task` 工具。
- `prompts/*.txt` 与 `opencode.json` = OpenCode 旧栈,已 **DEPRECATED**,不要在那儿找规则。
- IDA headless: `tools/ida_start.sh` → `tools/ida_open.sh <二进制> [会话]` → `tools/ida_call.sh <tool> '<json 含 database=会话id>'`;收尾 `tools/ida_stop.sh` + `tools/ida_clean.sh`。
- 信号判定以 `tools/parse_signal.py` 为准;它不覆盖的类别用**自定义差分/对比实验** —— 但"可复现"≠"是漏洞",先过 `knowledge_search(kind="misreport")`（SQLite；没有 Markdown fallback）。

## 取件(下载/版本核实)

- 厂商下载页是 **JS 壳**、`curl` 只拿到骨架/拿不到固件直链 ⇒ **用无头浏览器 MCP 取件**。
  MCP 默认是 `codemode` exposure:模型工具表里**不会**直接出现 `mcp__chrome_devtools__*`;
  用 `codemode` 脚本,通过 `tools.<name>` 调用,例如:
  ```js
  await tools.mcp__chrome_devtools__navigate_page({url: "..."});
  const snap = await tools.mcp__chrome_devtools__take_snapshot({});
  text(JSON.stringify(snap));
  ```
  先用 `searchTools("chrome")` / `ALL_TOOLS` / `describeNamespace("mcp__chrome_devtools")` 找工具;
  从 `list_network_requests` 找真实下载 API → 拿到直链后**回到 `curl` + md5**,写
  `intel/firmware/<目标>/SOURCE_URL.txt` ✓。边界:只碰公开页、不代填密码。

## 受众与范围(硬性)

默认不提交:产出落 `state/<目标>/report/` 即终点 ✓。**用户明确要求提交时**用可填不可提交(只填表单/草稿、**不点提交**、贴待确认清单给用户 ✓,见 `AGENTS.md`「提交红线」+「环境清单 → 提交」)。
