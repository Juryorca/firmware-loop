# ROADMAP(fwcore)

> 唯一工程进度清单。规则见 `.agents/skills/fwcore-development/SKILL.md`。
> 数字一律来自 SQLite/脚本输出,不手写估计;历史里程碑只做背景,不代表当前状态。

## 当前状态(产品事实)

- **架构边界**:fwcore 独立运行(模型调用/工具/研究状态/证据/Watchdog/checkpoint/resume);Pi/DS 是外部开发工具,`.pi/` 已不在版本库,`pi_bridge`/`pi_hook`/`pi-event` 已删除。
- **模型协议**:`finish_reason`/`usage` 贯通;`model.call` 每次调用记账;FINAL 任意行首识别;重复内容/无工具空转有看护;不设上下文裁剪旋钮(历史全量发送)。
- **工具结果**:`_bounded_tool_result()` 保证 ≤8k 且**始终合法 JSON**,保留状态/`db_tool_call_id`/`stdout_path`/`result_path`/哈希/错误;完整产物在磁盘;协议 `call_…` 与 DB `TC-…` 已分开命名。
- **追溯链**:`_artifact_of()` 同时匹配 `id` 与 `call_key`(真实库 461 条 call_key 可查);旧 checkpoint 的非法 JSON 在 resume 时被包成合法结构并补真实来源;`observation` 来源要求可证明(stdout/result 相等或出现在命令参数)。
- **研究事实继承**:系统提示注入最近假设/证据/观察,**按类别配额**(假设 40%/证据 28%/观察 32%),真实 QNAP 库(8/21/9)下 7/7/5 条可见(修复前观察 0 条)。
- **数据契约**:schema **v12**(`observations.tool_call_id/path/sha256`);两库已 rebaseline(备份 `.bak-20261106-113941`)。
- **研究质量门**:`conclude(no_finding)` 要求 ≥1 条经复核证据;`vulnerable` 需 supported + 因果复核;`assess` 区分 completed/partial 与证据强度。
- **QNAP 目标**:旧 run `R-20261105-0b4df34e`(421 calls)、第二阶段 `R-20261105-984803d9`(90/134)、第三阶段 `R-20261105-3518ef1d`(**累计 100 calls / 107 tool calls**),均为 `finished/paused`;结论 8 假设(7 no_finding + 1 inconclusive)、21 证据、9 观察。
- **验证强度**:`fwcore_verify.sh` = pytest 全量 + capabilities + gates + 评测隔离 + ROADMAP 一致性 + git clean。

## 最高价值待办(按优先)

### P1 认证控制流:`op` 值 → 分发 → 权限检查 → 失败分支 🟡 静态审计完成,动态未做

- **本轮结果(2026-11-06,`R-20261105-3518ef1d` 累计 125 calls/138 tools)**:11 个 op 子处理器逐一过门禁,
  结论 `no_finding`(H-20261106-3fdc40e5),范围写得很窄(main@0xf470 → 5 入口 → 11 handler + 门禁原语库内实现);
  门禁类型:口令(0x14c50 含 `Check_NAS_*`)、session 三重校验、`device_auth_get_session`、Qtoken DB、
  verify_code strcasecmp、vc op13 qtoken;库内原语实测为真实秘密比对(sha256 已记)。
- **记录**:4 条 observation(1 条 `command-write` 弱来源、3 条旧式无来源)、6 条 evidence(3 条 cross-check + 3 条库文件 sha256)。
- **模型自然落账**:25 次调用里 13 次 `state_*`(obs 4/ev 6/verify 6/attempt 2/conclude 2/hyp 1/mirror 1)⇒ 之前的
  "只分析不落账"在修好 schema/来源字段/事实注入后**未再出现**。
- **未判定(如实)**:唯一无门禁入口 `passwordless op=8`(0x12d78,实测只有 `getpwnam`/`device_auth_load_user_config`,
  无 session/qtoken/password 调用)只读 2FA 配置,模型判为"低危信息暴露/弱枚举面",不构成绕过;是否值得单独成案见 P5。
- **缺**:运行时/模拟验证(服务端 session/qtoken 状态文件不在样本内);其它 `device_auth_*/auth_*` 助手只按调用点返回值判断。

- **目标**:判定认证前输入能否绕过权限/状态检查抵达受保护行为。
- **已有**:5 个 handler 入口(0x196d8/0x197d8/0x19908/0x19a68/0x19b08)、`CGI_Find_Parameter("op")`+`strtol`(0x19908)、访客门 `Is_Guest_User`(0xf7cc/0xffdc)、`all_calls.txt` 调用图、`_notes/s3/` 标注。
- **缺**:`op` 取值范围与异常处理、各 handler 内权限检查点、失败分支可达性。
- **验收**:地址级链路 + ≥1 条带来源 observation;判不了就写清缺口(inconclusive)。
- **预算**:`R-20261105-3518ef1d` run 级累计上限 **200 calls / 7200 s**,耗尽即暂停(非完成)。
- **阻塞**:需用户批准付费模型续跑。离线部分已做完,没有其它可自主推进的分支。

### P2 工具结果 → 研究状态 → 下一 run 复用 ✅ 完成(2026-11-06)

- 验收:`tests/test_research_e2e_chain.py`(大文件 `fs_read` → `db_tool_call_id` → `state_add_observation(带来源)` → 下一 run 提示可见);`tests/test_cross_run_inheritance.py`(观察跨 run、来源可证/缺文件拒绝);`tests/test_tool_result_payload.py`(50 KB 仍合法 JSON、协议/DB id 分离);`tests/test_context_budget.py`(旧坏 JSON 修复、配对完整、advance 续跑恢复历史)。

### P3 证据强度分层 ✅ 完成(2026-11-06)

- `observations.relation`(v13):`direct-stdout` / `direct-result` / `command-write`;提示按强度标注;`assess` 的 `provenance_mix` 与 `direct` 检查不被弱来源满足。
- **权威 Gate 同步(本轮)**:`fw-core gates` 输出 `provenance{direct,weak,unlinked}`;只有完整性复核的 `no_finding` 进 `advisories`(提示,不阻断)。
- 验收:`tests/test_provenance_strength.py`、`tests/test_gate_provenance.py`;真实 QNAP 库 gates 实测 `direct 10 / weak 8 / unlinked 3`,4 条 no_finding 标为"仅完整性复核"。

### P9 Pi 真实基准 + fwcore 缓存对齐 + 压缩模拟 ✅ 完成(2026-11-06)

- **Pi 实测(只读)**:pi 1.5.0;usage 字段 `input/cacheRead/cacheWrite/output/reasoning`;命中率
  `cacheRead/(input+cacheRead)` = **98.36%**(5,386 次,分段 97.6–99.9%);deepseek-flash 窗口 1,000,000、
  maxTokens 384,000、cacheRead 单价为 input 的 1/50。**Pi 没有客户端缓存算法** —— DeepSeek 原生前缀缓存。
- **fwcore 实测(31 次调用,官方 endpoint,临时工作区)**:总体 **89.8%**(80.0/89.1/92.8% 分段),逐次 85→93 单调上升;
  请求结构 `[静态 system] + append-only history + [尾部 state ≈160 tokens]`,工具 schema 固定。
  修复前对照:system 稳定窗口 98.3% vs 变化窗口 1.8% ⇒ **根因是前缀稳定,不是缓存实现**。
- **压缩模拟(全离线,不烧 token)**:70/85/90/95% 占用触发正确;85/90/95% 压缩后配对完整且体积下降;
  连续三次压缩稳定;压缩不动 hypothesis/evidence(verified 仍为 1);压缩后 resume 正常。测试 5 passed。
- **未验证**:>500k 长历史稳态命中、多轮压缩的真实 API 重建、第三方网关缓存行为。
### P8 上下文生命周期生产化 + 上下文感知 ✅ 完成(2026-11-06)

- **生产配置**:`state/model.json` 增 `context_tokens: 1000000`(依据 Pi 模型目录 `deepseek-flash.contextWindow`,
  非按名字猜)+ `output_reserve_tokens: 8000`;`models.limits_config()` 读 env > `state/model.json`;
  未配置时状态块明确 `window=unknown`,不静默宣称自动压缩已生效。
- **估算校准**:224 次真实调用实测 **2.08 字符/token**;旧 3.5 低估 ~40%(est/actual≈0.59)⇒ 改 **2.0**(偏保守)。
- **切点正确性**:改为按**协议组**(assistant.tool_calls + 全部 tool 结果)切分,不再只看"不以 tool 开头";
  测试覆盖多 tool_calls 组整体保留。
- **压缩失败语义**:摘要异常 ⇒ 历史原样保留 + `run.compact_failed` + 冷却(默认 5 次调用)⇒ 不重复付费、不产生空摘要。
- **上下文感知**:状态块合入 `state_prompt()` 尾部(model/窗口/估算/可用/预留/阈值/准备压缩/上次真实 usage),
  **不进 system、不写 history/checkpoint**;空间充足时明确"继续工作,不得声称快满"。
- **CLI**:`fw-core context <target>`(模型/窗口/估算/阈值/是否启用/最近压缩/上次真实 usage/checkpoint 消息数)。
- **真实 10 次调用基线**(临时工作区):prompt 2,552→5,767、cached 0→3,968、命中 70.9%→81.3%、
  状态块 ≈160 tokens(约初始请求 6%)。
- **未验证(如实)**:>500k tokens 长历史压缩后的命中率、多轮压缩的真实 API 行为(本轮未跑付费长程)。
### P7 Agent Runtime 架构对照与上下文生命周期 ✅ 完成(2026-11-06)

- **对照 Pi**(只读):append-only entries + projection、system 静态 + 动态更新消息、阈值 compaction(`keepRecentTokens 20000` + 结构化摘要);
  fwcore 现状与差异见 `docs/ARCHITECTURE-RUNTIME.md`(含 7 问复核)。
- **本轮新增**:`fwcore/compact.py`(估算/安全切点/压缩)、`Limits.context_tokens`(显式窗口,0=不压缩)、
  `Runner(summarizer=…)`;到阈值时把较早历史压成摘要(默认调模型、**计入预算**),保留最近 `keep_recent_tokens`;
  事件 `run.compacted` 记录 before/after。测试 `tests/test_compaction.py` 4 passed。
- **缓存调整复核**:动态状态不进 history、不累积、只在请求尾部;加了"程序生成,不是用户新指令"标注;
  resume/advance 共用 `_load_history`。
- **已知限制**:checkpoint 每步全量快照(未改)、磁盘保护截断旧输出(设计选择)、`model_calls` 与事件差 1(失败调用)。

### P6 Token 用量审计 ✅ 完成(2026-11-06)

- **数据**:`R-…3518ef1d` prompt 10.75M / 命中 4.91M(45.7%) / 未命中 5.84M / completion 71.6k(推理 43.7k);
  `R-…0d7f8196` 6.40M / 0.29M(4.6%) / 6.11M / 57.3k(35.5k)。逐条核对 `total=prompt+completion`、`cached⊆prompt`、`reasoning⊆completion`。
- **根因(已修)**:动态内容(derived 计数/已有事实/进度/目标)原本在**第一条 system 消息**里,研究一推进就变
  ⇒ provider 前缀缓存整体失效(实测稳定窗口 98.3% vs 变化窗口 1.8%;离线复现:加 1 条 observation 使 prompt 在偏移 795 处变化)。
  现拆 `rules_prompt()`(静态)+ `state_prompt()`(尾部 user 消息),`[system]+history+[user state]`;
  测试 `tests/test_prompt_cache_layout.py`(system 逐字节一致 / 历史只追加 / 动态在尾部)。
- **分类**:A 完整历史 + 15 个工具 schema(5,430 字符/轮)+ reasoning 回传(协议必需);
  B 工具结果已有 8k 上限(实测最大 3.3k);C 旧 run 的 200 次重复 FINAL(已修)、1 次 400(已修)、模型重复读同一库(4 次,非框架);
  D `model_calls` 比事件多 1(失败调用计入预算但无 usage)。
- **估算**:未命中输入合计 11.95M;按 98% 命中量级可降到 ~0.26M(标注为估算,非精确账单)。
- **不删历史 / 不摘要 / 不减工具定义**。
- 详见 `docs/evidence/token-audit.md`。

### P5 `passwordless op=8` 未认证信息暴露 ✅ 已成立(vulnerable,2026-11-06)

- **结论** `H-20261106-33991ba5` = **vulnerable**,范围写死:样本 authLogin.cgi(sha256 `29eb3bda…`,QTS 5.2.11.3661)的
  `func=passwordless → 0xffa8 → op==8 @0x19a68 → 0x12d78`,且 `user` 非 guest。
- **数据流**(模型查 + 我独立 objdump 复核):`CGI_Find_Parameter("func")` → `Is_Guest_User`(0xffdc,**只拦 guest**)
  → `IsLocalUser`(0xffe8)→ 0x19a68 `cmp w0,#8` `b.eq 0x19ae0` → `b 0x12d78`;handler 内
  `CGI_Find_Parameter("user")` → `Get_Exact_NAS_User_Name` → `device_auth_load_user_config` → `json_pack`
  返回 `passwordless_en/qrcode_en/approve_login_en`(+`error_code=0`)。**全程无 session/cookie/凭据判定**。
- **影响(如实)**:认证前可读任意本地非 guest 账号的 2FA/passwordless 配置 → 二次认证态势泄露 + 账号存在性判据
  (仅非 0 值可作判据;不存在用户与未写盘配置都返回全 0);不泄露口令/OTP/密钥。
- **证据**:4 条 verified(`reproduce-attempt`×3 + `cross-check`×1);来源全部 `command-write`(弱),
  `decisive_attempts_have_direct_evidence` 为 0/10 —— 报告里必须标注"弱来源 + 静态复核"。
- **未覆盖**:func 其余 op 分支、guest(被拦)、httpd 路由/鉴权配置、运行期 auth.conf 权限位;无真机/仿真动态验证。

### P4 依赖库/样本边界(可选,需新研究材料)

- **目标**:缺失库或动态入口导致无法判定时,给出明确边界说明而非猜测。
- **依赖**:P1 的实际运行结果(哪些库/符号确实缺)。
- **验收**:边界写进结论 `scope_note`,并有对应证据。

## 本轮修复(2026-11-06,里程碑)

- **advance 续跑丢历史**:`advance(run_id=…)` 把空 history 交给 `turn()` ⇒ 模型只看到新 goal;现与 `resume` 共用 `Runner._load_history()`。
- **`state_add_observation` schema 缺来源参数**:handler 收了 `tool_call_id/path`,schema 没声明 ⇒ 调用被"未知参数"拒绝(端到端测试抓到)。
- **时间戳只有秒精度**:`round.ended`/`mirror_checks`/`evidence` 同秒时 `ts > since` 排序失效
  ⇒ 轮次可能漏计、`mirror_due` 偶发为 False(全量测试里表现为顺序相关的 flaky)。改微秒精度。
- **`domain._count` 动态 SQL**:改字面量分支,静态扫描不再报注入(误报也一并消除)。

## 已取消(不属于产品)

- Pi Hook 接线、Pi 桥(`pi_bridge`/`pi_hook`/`pi-event`)、Pi continue-guard 开关 —— 已删除;
- Pi 侧调用 Gate、Pi 配置兼容回退 —— 不做;
- Ralph、工具适配 Agent、额外编排平台 —— 不做。

## 历史里程碑(背景,非当前状态)

- 研究循环 / 域不变量 / 证据复核(recheck-sha256、reproduce-attempt、cross-check)与 `vulnerable` 因果门;
- 权威 Gate(`fwcore/gates_db.py`)、评测隔离(`task.json` + 私有 `answers/`)、`derived_check.sh`;
- 统一工具注册(`tools/registry/*.json` + `toolreg`)、IDA 适配器与 Windows 配置单一来源(`winenv`);
- `fw_extract` 资源保护(卷/文件数/深度/空间,状态分级)、AFL/qemu Provider 真跑;
- MCP→Runtime 全链路(Bridge/超时/断连)与 `state/mcp.json` 权威配置;
- 研究授权(`authz.json`:roots/expiry/revoke)与看护进程(PGID + starttime);
- 跨 run 继承、observation 来源、schema v12、旧 checkpoint 修复(本轮)。

## 已知限制(如实)

1. 观察只有单来源字段,多来源需多次登记;
2. checkpoint 截断只保留路径不保留全文(磁盘保护,设计选择);
3. `domain._count` 的 SQL 白名单静态扫描仍报 S608(误报,未消除);
4. 第三阶段 `_notes/s3/` 产物未复制进 `report/artifacts/`,登记证据前需保留原路径与哈希;
5. 真实模型是否会自然落账 observation/evidence —— 待 P1 运行验证。
