---
name: fwcore-development
description: fwcore(自主固件漏洞研究 Agent)的项目级开发方法:编码前做 Pi 参考实现审计、读现状、选工作项、查根因、维护 ROADMAP、按阶段提交。
audience: development
---

# fwcore 开发方法

`fwcore` 是独立产品:`fwcore/`(runtime/domain/tools/research) + `fwapp/` 应用层、`tools/registry/*.json`、
`eval/`、`tests/`。Pi/DS **不进入产品运行时依赖**(`.pi/` 已 gitignore),但 Pi 是**参考实现**:
“不是依赖”不等于“可以不看、另造一套”。凡 Pi 已解决的运行时问题,先对照 Pi 的文档与源码。

## 0. 参考实现审计(编码前硬门)

Pi 已经实现过 MCP、工具生命周期、TUI/会话、checkpoint、缓存、扩展等运行时能力。
遇到“检测 / 连接 / 注册 / 状态 / 生命周期 / 配置”需求,写代码前先留下三行审计:

1. **参考**:Pi 的文档路径 + 源码文件/函数(或明确写出“查过,Pi 无对应实现”的搜索证据);
2. **复用**:直接沿用的 schema、状态机、命令名、错误语义、关闭顺序;
3. **差异**:fwcore 为什么必须不同;没有实质差异就写 adapter,不要重写。

三行写进提交信息或 `docs/ROADMAP.md` 的工作记录。**没有审计,不许进实现。**

明确禁止:

- Pi 已有同义配置时,新造配置文件名、字段名或目录;能兼容 `mcpServers` 等既有 schema 就不另立一套;
- 配置 + 连接状态已经能表达时,再自建一套“检测/健康检查”轮询或第二状态机;
- 自建工具命名、注册表、错误分类、子进程关闭顺序,而没有对照 Pi 的实现;
- “先按自己的想法做出来,以后有空再对齐”;
- 用“fwcore 是 Python、Pi 是 Node”解释协议/生命周期差异——语言不同只能解释**代码不能直接复用**,不能解释**重新发明契约**。

### MCP 的具体口径

Pi 的 canonical 参考是 Pi 包内 `docs/mcp.md`、`dist/extensions/mcp/config.js`(`loadMcpConfig`)、
`dist/extensions/mcp/runtime.js`(连接/重连/关闭)和 `dist/extensions/mcp/ui.js`(`/mcp` 状态)。
改 MCP 前至少对照:全局/项目配置来源与 project trust、项目只覆盖 `enabled`/`exposure`/`toolExposure`、
stdio 与 HTTP、后台连接、`/mcp` 状态、`mcp__<server>__<tool>` 命名、
stdio 关闭顺序(stdin → SIGTERM → SIGKILL 进程组)、HTTP 瞬时错误重试。
当前 fwcore 的自定义 `state/mcp.json` 入口 + 自制 `probe()`/检测层是**历史实现/兼容债**,
不是新 MCP 工作的模板;先确认 `mcpServers` schema 与生命周期能否复用,能收敛就收敛,
保留 target override 或差异必须写明兼容与迁移理由。

## 1. 开始前(5 分钟)

**若涉及 Pi 已有能力(尤其 MCP/TUI/tools/checkpoint),先完成第 0 节审计,再往下做。**

1. `git log --oneline -5` + `git status`:确认实际 HEAD 与脏文件。
2. 读 `docs/ROADMAP.md` 的 **当前状态 / 最高价值待办** 两节(历史里程碑只读不跟)。
3. 读要动的模块的**调用者**,不只读函数本身:`grep -rn "函数名" fwcore/ tests/`。
4. `bash tools/check.sh` 看环境;`bash tools/fwcore_verify.sh` 只在阶段收尾跑。
5. 判断完成度时**看代码与真实数据**,不信 ROADMAP 的一句话、也不信测试名。

## 2. 选下一项工作(排序因子)

1. 是否卡住"自主研究主链":目标 → 历史事实 → 模型决策 → 工具 → 产物 → 观察/假设 → 证据 → 结论 → resume → 下一 run。
2. 是否影响**数据正确性/证据可信度/恢复能力**(追溯链问题优先)。
3. 是否是**多个症状的共同根因**(修一处、验证多处)。
4. 能否用现有资源与授权完成(不需付费模型、不需外部账号)。
5. 单位投入的工程/研究收益。

不要按 ROADMAP 行号从上往下做,也不要只修最新冒出来的小 bug。
同一类问题(如"来源不可追溯")要一次性查完整条链再动手。

## 3. 查根因(不是修补那一行)

- 先查参考实现/标准,再决定改哪一层;不要从症状直接长出一个自有协议;
- 先复现/度量(真实 SQLite、真实产物),再改代码;
- 追上下游契约:调用方、被调方、失败路径、历史数据、恢复路径;
- 找反例:构造能推翻"当前实现假设"的输入(大输出、坏 JSON、跨 run、缺字段、并发);
- 修完检查相邻模块是否有同型缺陷;
- 有边界的地方写清"这是设计选择"还是"未修",不要含糊。

## 4. 维护 ROADMAP(唯一权威清单)

阶段结束时更新,不是每个小修都加一行。规则:
- **当前状态** 只写产品事实(能跑什么、验证到什么强度);
- **最高价值待办** 每项带:目标 / 依赖 / 验收方式 / 是否需外部授权;
- 已取消的**运行时依赖/编排**(把 Pi 当依赖、Ralph、额外编排)单独一节,不混在待办里;
  但“取消 Pi 依赖”不等于“取消 Pi 参考”:MCP/工具/TUI 等行为对齐仍按第 0 节执行。
- 历史里程碑保留但要标"历史",不能冒充当前状态;
- 数字(调用数、覆盖率)必须来自 SQLite/脚本输出,不手写估计。

## 5. 阶段完成判据(不是"pytest 绿")

1. 要解决的真实问题是什么;2. 根因;3. 影响范围是否查清;4. 上下游是否覆盖;
5. 是否有代表性验证(真实规模数据/反例);6. 对自主研究能力有何实际改善;
7. ROADMAP 同步;8. 下一项最高价值任务是什么;9. 第 0 节参考实现审计已留痕,差异有测试或明确理由。

阶段内发现的关联问题,能一起解决就一起解决,不要留到下一轮。

## 6. Git

- 提交单位 = **可独立验证的工程成果**(根因修复 + 集成测试 + 文档同步);
- 不为每个小修/每次 ROADMAP 改动单独 commit;
- 不为了进度制造 SHA;不在未验证的情况下堆积大改动;
- 提交信息写"问题 → 根因 → 修法 → 验证",不写流水账。

## 7. 持续执行判据(硬)

一个子任务完成**不代表**本轮开发结束。按顺序自检:

1. 打开 ROADMAP 的"最高价值待办":还有**已授权、可实际执行**的项吗?
   有 ⇒ **在本次工作里继续做**,不要写成"下一轮再做"。
2. `pytest PASS` / `commit` / `push` / 更新 ROADMAP / 写完总结,**都不是**停止条件。
3. 某任务被外部授权阻塞(付费模型、账号、样本)⇒ 只阻塞那一项,其余继续。
4. 只有下面三种情况可以结束:①工作包达到可交付里程碑且 ROADMAP 无其它可自主推进项;
   ②所有剩余项都需要外部授权(逐条写清缺什么);③用户明确叫停。
5. 结束报告必须说明**整个工作包**为何完成/为何无法继续,不能只说"最新那个 bug 修好了"。
6. 不为了"持续"而空转:没有高价值项就停,不制造工作量。

## 8. 继续 vs 请示

可自主连续推进:代码审查、离线分析、测试、修复、文档、ROADMAP 整理、确定性重构。

必须请示:**付费模型续跑**、外部账号/样本、正式关闭目标、PGP 外发、改 `main`。

某项被阻塞(如等模型授权)不等于停工:转做其它已授权的离线工程或研究分析。

## 9. 与其它文档的关系

- `AGENTS.md` + `research-loop` Skill = 研究行为与流程(研究会话用),本 Skill 不复制其规则;
- Pi 的 `docs/` + `dist/` = 运行时能力的参考实现;Pi 不是运行时依赖,但必须先对照;
- `docs/ROADMAP.md` = 唯一工程进度清单(本 Skill 只规定怎么维护它);
- 跨目标研究经验统一写 SQLite Knowledge（`knowledge_propose` / `knowledge_revise`）；不再写知识 Markdown，开发教训写 `docs/`。

## 10. 常用命令

```bash
PI_ROOT="${PI_ROOT:-$(npm root -g)/@earendil-works/pi-coding-agent}"
rg -n "mcp|tui|checkpoint" "$PI_ROOT/docs" "$PI_ROOT/dist" | head -80   # 先找参考实现

git log --oneline -5; git status --short
bash tools/fwcore_verify.sh                  # 阶段收尾:全量
python3 -m pytest tests/ -q                  # 定向:先跑受影响的文件
python3 -m fwcore.cli capabilities           # 能力/Provider 事实
sqlite3 state/<目标>/.../fwcore.sqlite3 "SELECT ..."   # 真实数据核对
```
