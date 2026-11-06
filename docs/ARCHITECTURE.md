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

判据(方法论)在 `knowledge/`、`AGENTS.md`、`LOOP.md`;机器可核部分才进代码。
代码**不得自带新判据**:闸门红 ≠ 结论变,而是"补事实"或"先改判据"。

## 2. ① 模块(全部有测试)

```
fwcore/  db.py(SCHEMA v13/WAL/BEGIN IMMEDIATE)  domain.py(唯一写入口+不变量)
         tools.py(内置 ToolSpec)                toolreg.py(manifest→Registry,4 种注册路径)
         runtime.py(循环/检查点/恢复/预算)       research.py(提示/池/三镜/进度)
         compact.py(压缩)                        contextinfo.py(上下文状态)
         mcp_runtime.py(MCP 桥)                  adapters_ida.py + winenv(IDA/Windows)
         gates_db.py(权威 Gate)                  models/model_client(适配器/协议)
         policy.py(Authz)  limits.py(边界)  watchdog.py  migrate.py  cli.py
fwloop/  兼容入口        scripts/ 演练与 e2e        eval/ 答案隔离评测
tools/registry/*.json  仓库工具 manifest(8 个)
```

## 3. 存储:DB 是权威,文件是叙述/材料(不是双写)

| 谁写 | 写什么 | 参与判定 |
|---|---|---|
| 模型的 `state_*` 工具 | 假设/尝试/观察/证据/复核/结论/面处置 | ✅ |
| runtime | `runs`(预算)、`checkpoints`(恢复)、`tool_calls`(产物/来源)、`events`(审计) | ✅ |
| 模型/人 | `notes.md`(叙述、理由、下一步指针) | ❌ |
| finding/收尾时 | `report/`(advisory/INDEX/attack-surface/CLOSURE/复现包) | 材料门读 |

表:`targets surfaces hypotheses attempts observations evidence evidence_checks
conclusions runs checkpoints tool_calls events mirror_checks documents meta`。

保留机制:`checkpoints_keep=120`/run(写时裁剪)→ `_shrink_history`(老工具输出 200 字符 +
保留路径/`tool_call_id`)→ 库超 `max_db_bytes=256MB` 删最老 checkpoint 仍超则暂停。
清理:两档(**使用中/收尾未归档=提交中** 只清垃圾;**归档/不要了** 才释放载体/派生物/原件,需用户明说)。

## 4. 工具接入(统一注册契约,不统一内部实现)

四条路径:内置 `ToolSpec`(state_*/shell_exec/fs_*)、`tools/registry/*.json`(script/python)、
feature 适配器(ida)、MCP(`mcp_<server>_<tool>`)。

```json
{"name":"x","description":"…","input_schema":{…},"kind":"script",
 "argv":["bash","tools/x.sh","{a}"],"timeout":900,"timeout_arg":"timeout",
 "risk":"high","capability":"extract.binwalk","provider":"repo:x.sh",
 "requires_file":"tools/x.sh","observer":"x"}
```

执行链:`Registry.call`(call_key 幂等 + risk/Authz + capability)→ 适配器 → `run_process`
(独立 PGID、取消杀组、输出上限、ok_exit_codes)→ 产物落 `state/artifacts/<run>/` →
`tool_calls` 行(status/exit/sha/provider/arch/observation)→ `db_tool_call_id`(TC-…) →
`_bounded_tool_result`(≤8k、始终合法 JSON、保留路径/哈希/错误)→ 模型。
能力层:`fw-core capabilities` 探针(`ready/partial/deps_missing/arch_mismatch/missing`);
manifest 的 `capability` 不满足 ⇒ 进 `unavailable` 报告,不静默消失。

## 5. 上下文生命周期(静态前缀 + 尾部状态 + 阈值压缩)

- 请求 = `[system 静态规则] + history(append-only) + [user 尾部状态]`;状态块含派生量/事实/进度/目标
  + `[Context status]`(model/window/估算/可用/预留/阈值/上次真实 usage)。**不进 system、不写 history/checkpoint。**
- 阈值 = `window − max(output_reserve, 10% window)`;生产(1M 窗口/8k 预留)= 90%。
- 压缩:按**协议组**切(assistant.tool_calls + 全部 tool 结果),保留最近 `keep_recent_tokens`;
  被丢弃的历史走 `summarize_covered`:分块(默认 60k/块)→ 超大消息切片 → 归并;超 `summarize_max_calls`
  或任一块为空 ⇒ `SummarizationIncomplete` ⇒ **历史原样保留** + `run.compact_failed` + 冷却。
- 摘要调用与普通调用同一条**调用前**预算检查,计入 `run_calls` 与 `model.call(kind=summarize)`。

## 6. 与 Pi 的关系:参考架构,不是依赖

Pi 是外部开发工具(`.pi/` 不入库);fwcore 独立运行、不调用 Pi。
Pi 侧只读结论:会话是 append-only entries + projection;system 静态、动态上下文作为消息注入;
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
5. 无 CI;验证靠 `tools/fwcore_verify.sh`(pytest 57 文件 + capabilities + gates + 隔离 + ROADMAP 一致性 + git clean)。
