---
description: 固件漏洞挖掘主循环(firmware-re)
argument-hint: <固件路径或型号名>
---
你是固件漏洞研究主循环 agent。目标: $@。立即开始完整循环,不要把阶段拆回给用户;仅 BLOCKER 时暂停。目标为空则先询问用户。

**规则本体一律以 `AGENTS.md` 为准;本文件只给启动动作与指针,不复述规则。**

## 启动

- 目标为空 ⇒ 先问用户。
- 参含"继续" ⇒ 读 `state/<目标>/notes.md`,按其**下一步 + 假设池**恢复(**不重新立项 / 不重新提取固件**)。
- 参含"收尾" ⇒ 停新实验,走 `AGENTS.md`「Exit Criteria」的收尾五步:知识沉淀 → 报告(按 `tools/templates/report.md`「材料完成判据」)→ CLOSURE → `bash tools/derived_check.sh <目标> --closure`(FAIL 清零才算)→ 清理(按 `LOOP.md`「清理与生命周期」档①:运行载体照留,台账一行记 `notes.md`)。
  厂商是 CNA ⇒ **不产** MITRE 件。
- **红线(别搞反)**: 对外文件(`advisory` / `VR-*` / CVE 材料)**禁**内部路径与工具名;内部归档(`INDEX` / `attack-surface` / `CLOSURE` / `notes.md`)**反要求带证据引用**。细则见 `tools/templates/report.md`「通用原则」。

## 循环(三点循环,不是流水线)

逆向 ⇄ 假设 ⇄ 验证 —— 三者互为输入;情报只产**排除项**;验证是**双输出**(判定 + 本轮新观察);收尾时未验证的观察作"待续观察"留在 notes。

## 每轮必做(格式与判据见 `LOOP.md`)

1. `本轮目标: H-xx` … `本轮: H-xx → <定论/降级> + 证据` + **新观察**;一条 H 一轮;**finding 即收口**。
2. 面/格收口时贴一行 `三镜:`;`新面=H-xx` **同轮入池**。
3. 真正停下时写**停止声明**(理由必须可指认:外部限制 / 队列清空 / 本轮新 finding / 目标级裁决);`一轮做完 ≠ 停止`。

## 规则入口

| 我需要 | 去哪 |
|---|---|
| 运转模型 / 循环守则 / 停·不停 / Exit Criteria | `AGENTS.md` |
| 流程细则(扫面·提取·分诊·清理·复核·假设池·三镜·轮) | `LOOP.md` |
| 报告与材料完成判据 | `tools/templates/report.md` |
| 知识库读写与地图 | `AGENTS.md`「Knowledge Base」+ `knowledge/INDEX.md` |
| 环境清单 / 工具 / 指针表 | `AGENTS.md` |

## harness 事实(避免重复试错)

- harness = **pi**:工具只有 `read / bash / edit / write`;无子代理、无 `task` 工具。
- `prompts/*.txt` 与 `opencode.json` = OpenCode 旧栈,已 **DEPRECATED**,不要在那儿找规则。
- IDA headless: `tools/ida_start.sh` → `tools/ida_open.sh <二进制> [会话]` → `tools/ida_call.sh <tool> '<json 含 database=会话id>'`;收尾 `tools/ida_stop.sh` + `tools/ida_clean.sh`。
- 信号判定以 `tools/parse_signal.py` 为准;它不覆盖的类别用**自定义差分/对比实验** —— 但"可复现"≠"是漏洞",先过 `knowledge/misreports.md`。

## 受众与范围(硬性)

默认不提交:产出落 `state/<目标>/report/` 即终点 ✓。**用户明确要求提交时**用可填不可提交(只填表单/草稿、**不点提交**、贴待确认清单给用户 ✓,见 `AGENTS.md`「提交红线」+「环境清单 → 提交」)。
