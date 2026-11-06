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

### P1 认证控制流:`op` 值 → 分发 → 权限检查 → 失败分支**(需真实模型续跑授权)

- 目标:判定认证前输入能否绕过权限/状态检查抵达受保护行为;
- 已有:入口地址(0x19908 等 5 个)、`Is_Guest_User` 门在 0xf7cc/0xffdc、`op` 解析 `strtol`、`all_calls.txt` 调用图、`_notes/s3/` 12+9 份标注;
- 缺:`op` 取值范围与异常处理、各 handler 内权限检查点、失败分支可达性;
- 验收:地址级链路 + ≥1 条带来源的 observation;无法判定时写清缺口(inconclusive);
- 预算:`R-20261105-3518ef1d` 累计 100 calls ⇒ 上限 **200 calls / 7200 s**(run 级累计),耗尽即暂停;
- 需你批准才启动。

### P2 工具结果→研究的转化率**(无需付费,可用 fake transport)
- 目标:验证"大输出 → 模型引用产物 → 记录 observation/evidence → 下一 run 复用"整链;
- 依赖:P1 的 observation 来源字段(v12)已就绪;
- 验收:fake transport 端到端用例 + 真实库离线测量。

### P3 证据强度分层**(无需付费)
- 目标:`command-write` 弱来源与 `direct-*` 在报告/结论里始终分开呈现;
- 依赖:`link_relation`、`assess` 的 info 级检查;
- 验收:报告生成路径的断言测试。

### P4 依赖库/样本边界(可选)**
- 目标:缺失库或动态入口导致无法判定时,给出明确边界说明而非猜测;
- 验收:边界写进结论 `scope_note`,并有对应证据。

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
