# Agent 内容架构（开发者规范）

> 这是**开发者技术说明**，不是 Runtime Instructions，也不进入 System Prompt。
> 运行时行为以代码和 `AGENTS.md` 为准；本文解释四类内容 + 工具层的归属、加载和测试方式。

## 1. 四类内容 + 工具层

| 类别 | 位置 | 职责 | 加载方式 |
|---|---|---|---|
| **System Prompt** | `fwcore/prompts/system.md` + 根 `AGENTS.md` + 目录 | 身份、全局原则、项目硬约束、Skill/知识使用协议 | 每次模型请求的 system |
| **Skills** | `.agents/skills/<name>/SKILL.md` | 专项操作知识（提取/逆向/验证/报告/循环） | 仅目录常驻；正文按需 `skill_read` |
| **Commands** | `fwcore/commands.py`（元数据）+ `fwapp`（执行） | 用户主动输入的 `/xxx` 确定性操作 | 用户输入触发；不是模型工具 |
| **Knowledge Base** | SQLite `knowledge_entries` | 跨 Target 可复用的研究知识 | `knowledge_search` / `knowledge_get` / `knowledge_propose` / `knowledge_stats` / `knowledge_revise` / `knowledge_deprecate` |
| **Tools** | `fwcore/tools.py`、`tools/registry/*.json` | 真实执行能力 | 模型 tools 参数；独立于四类内容 |

**禁止**再新增与这五类并列的 Instructions/Policies/Rules 文件体系。`AGENTS.md` 是项目级指令的唯一根文件，不属于普通知识文章。

## 2. System Prompt 组装

唯一入口：`fwcore/prompting.py:build_system_prompt(root)`。

顺序固定：

```text
core system（fwcore/prompts/system.md）
  -> 根 AGENTS.md 全文（不裁剪）
  -> Available Skills（仅 name/description）
  -> User Slash Commands（仅说明，不是模型工具）
```

`fwcore/research.py:rules_prompt()` 只是调用该入口。运行时再拼接动态状态：

```text
system = rules_prompt()
history = 对话/工具消息
user tail = state_prompt() + context status + 轮前准备
tools = registry.describe()
```

约束：

- 案例正文、LOOP 正文、知识库正文**不进入 system**；
- `AGENTS.md` 不做字节裁剪；其指针指向的细节按需加载；
- 静态 system 与动态 state 分离，保持 prefix cache；
- 新增 prompt 内容前先判断它属于哪一类，不要直接塞进 system。

## 3. 根 `AGENTS.md` 的特殊地位

- 必须保留在仓库根；不改名、不移动到普通目录、不删除。
- 它是项目级 Agent 指令文件，具有通用 Agent 工具约定的特殊含义；属于 System Prompt 组装输入。
- 全局硬约束（停/不停、轮次、闸门、报告红线、权限/预算）留在 `AGENTS.md`。
- 具体操作流程迁到 Skills；历史案例/经验迁到 Knowledge；旧 `LOOP.md` 正文已迁到
  `.agents/skills/research-loop/references/LOOP.md`，根 `LOOP.md` 只留指针。
- 不允许 `AGENTS.md` 与 `LOOP.md` 成为两份重复加载的全局指令。

## 4. Skills

### 目录

```text
.agents/skills/<name>/
  SKILL.md            # 必需，YAML frontmatter: name, description
  references/         # 可选，按需加载的细节
  scripts/ assets/ templates/  # 可选
```

### Frontmatter

```yaml
---
name: research-loop
description: 一句话说明适用场景（Agent 据此判断是否加载）
---
```

### 正文最低内容

适用场景、前置条件、工具/依赖、步骤、输入/输出、关键判据、常见失败、验收条件、安全约束。

### Loader

`fwcore/skills.py`：

- `discover_skills(root)`：只扫描 `.agents/skills/*/SKILL.md`；跳过 symlink/非法 frontmatter；catalog 只返回 name/description。
- `read_skill(root, name, reference=None)`：读取完整 `SKILL.md` 或 `references/*`；realpath 必须在 Skill 目录内；拒绝绝对路径、`..`、symlink、非文本、>512KB。
- 工具：`skills_list`、`skill_read`。
- 不创建 Subagent、不引入新编排层；Skill 只是操作知识。

### 当前 Skills

| Skill | 用途 |
|---|---|
| `research-loop` | 一轮一假设、假设池、三镜、停止/收尾；完整 LOOP 参考 |
| `firmware-target-selection` | 选靶、固件获取、投入排序 |
| `firmware-reversing` | rootfs/IDA/Ghidra/入口→sink 建模 |
| `firmware-validation` | 建场、仿真/容器、差分/fuzz、证据复核 |
| `report-submission` | advisory/CVE/厂商提交（只填不提交） |
| `research-knowledge-curation` | 研究知识检索与直接沉淀（无中间审核态） |
| `fwcore-development` | 产品开发会话方法（开发用，不是研究流程） |

## 5. Slash Commands

- 定义：`fwcore/commands.py`（name/description/arg_hint）。
- 执行：`fwapp/modes/interactive.py` 的 `_cmd_*`；通过 `fwapp/services.py` 调用现有 Session/Service。
- 只有用户输入 `/xxx` 才触发；模型不能调用、不能伪造、不能把 Command 当 Skill。
- 新增命令必须同时：登记元数据、实现 handler、补参数/错误/补全测试。
- 当前命令：`/help /model /target /resume /status /budget /hypotheses /evidence /skills /knowledge /new /continue /clear /expand /quit`。

## 6. Knowledge Base：Research Knowledge，不是 Case Archive

### 定位

知识库只保存**能缩短未来分析路径、避免重复踩坑、判断成立/排除条件**的跨 Target 经验。

**值得收录**：

- 反复出现的厂商/SDK/组件缺陷模式；
- 容易误判的信任关系、代理拓扑、权限语义；
- 解析/长度/命令注入等可复用判据；
- 能帮助判断利用边界、复核方式、降权条件的经验。

**不收录**：

- 某个固件特有的函数地址、一次性调用过程、复现记录；
- 网上低成本可查的通用原理；
- 当前 Target 的假设/观察/证据/结论（留在 research state）；
- 没有跨目标复用价值的案例全文。

### 当前迁移策略

旧 `vuln-patterns.md`（24 条）+ `misreports.md`（27 条）是**原始来源**，不是 DB 行数目标。
本轮从中提炼 **14 条 curated 研究知识**（见 `knowledge/research-knowledge.md`），按主题合并
（认证授权、代理信任、命令注入、解析/内存、URL/Host、DoS、误报判据），去掉地址级复现细节。
旧两份 Markdown 保留，供回溯与再提炼；不常驻 System Prompt。
迁移报告必须区分“源条目 51”与“知识条目 8”，不能把源案例数冒充知识数。

### Schema（最小结构）

```sql
knowledge_entries(id, title, body_md, kind, status, source, source_hash, created_at, updated_at)
knowledge_entry_versions(id, entry_id, version, title, body_md, change_note, created_at)
knowledge_entry_tags(entry_id, tag)
knowledge_entry_fts(entry_id UNINDEXED, title, body_md, tags)  -- FTS5
```

- `body_md` 是完整自然知识；不要为了字段统一压缩正文。
- `kind`: `pattern`（正向）/ `misreport`（反向）/ `guideline`。
- `status`: Agent 判断值得记录即 `approved`；后续可 `deprecated` / `superseded`；不设中间审核态。
- 版本只追加，不覆盖历史。
- 检索：FTS5 优先，失败/无结果以 LIKE 兜底；支持中文、英文标识符、函数/API 名。
- `knowledge_propose` 直接写 `approved`；`knowledge_revise` 追加版本；`knowledge_deprecate` 退休；由 Agent 自己判断。
- 检索结果返回 ID/标题/类型/状态/摘要；需要全文再 `knowledge_get`。

## 7. 动态研究状态 vs 长期知识

| 内容 | 存储 | 生命周期 | 进 System Prompt |
|---|---|---|---|
| 当前 Target/面/假设/尝试/观察/证据/结论 | SQLite research tables | 随目标 | 否；走 `state_prompt` 尾部 |
| 运行/预算/checkpoint/工具调用/事件 | SQLite runtime tables | 随 run | 否；尾部状态/工具结果 |
| 长期研究知识 | `knowledge_entries` | 跨目标、长期 | 否；按需检索 |
| 静态规则/参考 | `AGENTS.md`、`knowledge/*.md`、Skills | Git 版本化 | 仅 AGENTS 常驻 |

## 8. 新增资产的流程

### 新增 Skill

1. 判断是否属于“专项操作知识”，不是全局规则、不是一次性案例；
2. 建 `.agents/skills/<name>/SKILL.md`，写 frontmatter + 场景/步骤/判据/失败/验收；
3. 需要细节放 `references/`；不要放二进制/密钥；
4. 运行 `python3 -m pytest tests/test_skills.py`；确认 catalog 只出现名称/描述。

### 新增 Command

1. 确认是用户主动 `/xxx`，不是模型工具；
2. 在 `fwcore/commands.py` 登记；在 `fwapp` 实现 `_cmd_<name>`；
3. 调用现有 Service，不复制研究循环；
4. 补参数、补全、错误反馈测试（TUI/单元）。

### 新增 Knowledge

1. 先问：它能否跨 Target 复用？重获成本是否高于检索成本？
2. 不能复用/通用可查 → 不进知识库；
3. 能复用 → 用 `knowledge_propose` 直接记录为 `approved`（完整正文、判据、边界、来源）；
4. 不设人工审核；不再适用时用管理接口标 `deprecated` / `superseded`；
5. 不把目标专属地址/当前证据写进知识正文。

### 新增 Tool

1. 先查现有 registry/capability，避免重复；
2. 真实执行能力 → `ToolSpec` 或 manifest；Skill 只写“怎么用”；
3. 补 schema、失败语义、evidence/observer、授权/风险；
4. 不能用占位实现冒充已接线；缺依赖如实标 `unavailable`。

## 9. 测试要求

- Prompt 组装：core + AGENTS + skills catalog + commands；案例正文不在 system；AGENTS 后段不裁剪。
- Skills：发现、frontmatter、catalog 不含正文、按需读取、路径穿越/symlink 拒绝。
- Commands：登记、分发、参数/错误/补全，不进入模型工具。
- Knowledge：迁移幂等、FTS 中英文、get 完整正文、propose 直接 approved、版本追加。
- Tool：真实能力、失败路径、授权；未接线状态如实。
- 全量：`bash tools/fwcore_verify.sh`。

## 10. 禁止事项

- 把所有内容无差别塞进 System Prompt；
- 把案例正文/LOOP 全文当常驻规则；
- 用 Skill 伪装 Subagent 或新编排层；
- 用 Command 复制研究循环；
- 把目标专属事实写进长期知识；
- 让 Agent 自行批准知识；
- 为了结构化而要求每条知识填大量字段；
- 文档声称已接入、Runtime 实际未加载。

## 11. 旧资产迁移映射

| 旧资产 | 原职责 | 新归属 | 加载方式 |
|---|---|---|---|
| 根 `AGENTS.md` | 项目硬规则 | System Prompt 项目指令 | 每次 system，全文 |
| `LOOP.md` | 流程细则 | `research-loop/references/LOOP.md` | Skill 按需 |
| `.pi/prompts/fw.md` | 启动/继续/收尾操作模板 | `research-loop` Skill + AGENTS | Skill 按需 |
| `.pi/prompts/提交.md` | 提交操作清单 | `report-submission` Skill | Skill 按需 |
| `prompts/*.txt` | 旧 OpenCode 子代理提示词 | 已删除（只保留职责映射） | 不再加载 |
| `opencode.json` | 旧 OpenCode 配置 | 已删除 | 不再加载 |
| `knowledge/vuln-patterns.md` | 正向案例来源 | 14 条 curated 知识 + 原始来源 | DB 检索 / 按需 fs_read |
| `knowledge/misreports.md` | 反向案例来源 | 同上 | DB 检索 / 按需 fs_read |
| `knowledge/methodology.md` 等 | 静态方法/工具参考 | 静态参考 | 按需 fs_read |
| `.pi/guards/*` | Pi 工具调用护栏 | 尚未迁入 fwcore runtime | 明确遗留 |
| `.pi/dev-notes.md` | 开发笔记 | 不进入产品 | 本地保留 |

## 12. 当前边界

- 旧案例的 51 条源条目未被逐条搬进 DB；按“Research Knowledge”原则提炼为 14 条知识（含 6 条工具判据），旧文件保留；
- 旧 Pi 的 scan/screenshot/continue guard 尚未产品化（工具台账附录 A 记录）；
- Chrome MCP、`write/edit`、Ghidra wrapper 等仍未接线，按台账处理，不在本轮伪装完成；
- 真实付费模型验证不在本轮范围。

## 13. 旧知识/流程的逐段去向（补充）

### 13.1 `LOOP.md` 主要段落

| 原段落 | 去向 |
|---|---|
| 失败分诊 / 打不了 / 需求式记录 | `research-loop` + `firmware-validation` Skills（硬规则仍在 AGENTS） |
| 清理与生命周期 | `research-loop` 参考；AGENTS 只留边界 |
| 停止声明 / 轮格式 | `research-loop`；AGENTS 的停止规则仍是权威 |
| 建模模板 / 入口→参数→sink | `firmware-reversing` |
| 跨版本 finding / 上游 vs 自研 | `firmware-target-selection` + `firmware-validation` |
| 文件搜索 / 扫描边界 | `research-loop` 参考；AGENTS 守则 12b 仍是硬约束 |
| 机械化 oracle 的选择 | `firmware-validation` |
| 假设池 / 三镜刷新 / 反水坑 | `research-loop` |
| 结论依据层级 | `firmware-validation` |
| 扫面 / 提取 | `firmware-target-selection` + `firmware-reversing` |

根 `LOOP.md` 只保留指向 `research-loop/references/LOOP.md` 的指针；原文完整保存在 Skill 参考中。

### 13.2 旧案例源 → 8 条研究知识

| 知识主题 | 主要旧来源 | DB 条目 |
|---|---|---|
| 对象级 owner/权限语义 | `vuln-patterns` 的 IDOR/owner 条目；`misreports` 的可预测 id/权限条目 | 认证/授权：对象级 owner 与权限语义 |
| 代理/转发头/默认拓扑 | `vuln-patterns` 的 XFF/Host/中继条目；`misreports` 的代理拓扑条目 | 认证/授权：代理、转发头与默认拓扑 |
| 门的位置/状态/管理员等价 | `vuln-patterns` 的 dispatch/setup/内部端口条目；`misreports` 的 admin 等价条目 | 认证/授权：门的位置、状态与管理员等价 |
| 命令/参数注入 | `vuln-patterns` 的命令注入/转义 helper 条目；`misreports` 的 exec/sink 条目 | 命令与参数注入：从输入到 shell 的边界 |
| 解析器/内存安全 | `vuln-patterns` 的 memcpy/整数/XML 条目；`misreports` 的块密码/RDLENGTH 条目 | 解析器与内存安全：长度、边界、部分覆盖 |
| URL/路径/Host | `vuln-patterns` 的 URL host 截断条目；`misreports` 的 Host/代理条目 | 路径、URL 与 Host 解析 |
| 速率限制/DoS | `vuln-patterns` 的认证限流条目；`misreports` 的限流/DoS 条目 | 速率限制、锁定与拒绝服务 |
| 误报判据/设计内行为 | `misreports` 的反例库整体 | 误报判据：能力、证据与设计内行为 |

**不是“51 条案例逐条入库”**：旧 51 条（24 + 27）保留为原始来源；DB 只存能跨目标复用的知识。
本轮提炼 8 条主题知识（另从 tool-quirks 提炼 6 条工具判据，共 14 条）；旧 51 条中其余 43 条未提升为 DB 条目的主要原因：目标专属地址/一次性复现细节、
已被主题条目覆盖、或属于可低成本公开检索的通用知识。`knowledge/research-knowledge.md`
是这 8 条的可审查种子，`knowledge_entries` 才是运行时查询源。

### 13.3 Pi / OpenCode 资产

| 旧资产 | 当前处理 |
|---|---|
| 根 `AGENTS.md` | 保留；system prompt 的唯一项目规则输入 |
| `.pi/prompts/fw.md` | 操作内容并入 `research-loop` / AGENTS；Pi 文件不再加载 |
| `.pi/prompts/提交.md` | 并入 `report-submission` |
| `.pi/guards/*` | 未迁入 fwcore runtime；工具台账遗留项 |
| `prompts/*.txt` | 删除（旧 OpenCode 子代理空壳） |
| `opencode.json` | 删除 |
| 旧 `AI辅助复现工作循环.md` / `循环定制.md` | 选靶/获取/环境/验证原则并入 `firmware-target-selection`、`firmware-validation`；按用户要求旧文件已删除 |
| 旧 CVE 目录 / 报告参考 | 外部历史资料，不纳入产品 |
