# QNAP authLogin.cgi 第二阶段审计摘要(离线核对版)

- 工作区:`state/_scratch/realdrill-20261105-164147`(同一工作区/同一目标,复用第一阶段产物)
- 真实研究 run:**`R-20261105-984803d9`**(2026-11-05T12:35:23Z,90 model calls / 134 tool calls / 623 s,`finished`)
- 第一阶段 run:`R-20261105-0b4df34e`(421 calls,`finished`;含 200 次 FINAL 识别空转,见 token 审计)
- 误触 scripted 冒烟:`R-20261105-f640454a`(2 calls,**不计入研究统计**)

## 关键对象哈希(可复核)

| 对象 | 来源 |
|---|---|
| `authLogin.recovered.elf` | `report/artifacts/SHA256SUMS`(恢复自 4096 周期 XOR,一阶段产物) |
| `deps/libuLinux_Util.so.0.0`、`deps/libuLinux_cgi.so.0.0` | 同目录 `SHA256SUMS`(第二阶段新增) |
| 审计产物 7 份 | `lang_cookie_qnap_dataflow.txt`、`preauth_op_dispatch.txt`、`verify_return_checks.txt`、`passwordless_handler.txt`、`deps_resolution.txt`、`stage2-findings.md`、`SHA256SUMS` |

## 三条结论与支持链(均 `no_finding`,范围见数据库 `conclusions.scope_note`)

1. **HTTP_COOKIE/lang → qnap_exec**:`Get_Cookie_Value_By_Tag`(`libuLinux_cgi.so` 0xc0b8,`getenv("HTTP_COOKIE")`)→ authLogin 0x29510 读 `nas_lang=`(16 B 栈缓冲,`memset`+`strncpy` 上限 0x10)→ `CGI_Get_QLANG_Idx`(strcmp 白名单,未命中 -1 直接跳过 sink)→ `qnap_popen(mode='r', '/sbin/user_cmd', '-5', x20, '-c', lang, '', '-n')`(0x295a4)→ `libuLinux_Util` 0x2f108→0x2ef30→0x2ead4,`fork`+`execvp`(单点 0x2dd90),**无 system/popen**。
   边界:库内部分词法/正则未逐行反编译;结论只覆盖"唯一送语言串的位点(0x294f0–0x295a4)与其 wrapper 语义"。
2. **认证前 func/op 分发**:`main@0xf470` 分发表 → 5 个 handler(qr_code 0x196d8 / approve 0x197d8 / verification_code 0x19908 / passwordless 0x19a68 / 0x19b08),`verify_return_checks.txt` 覆盖 **18 个原语调用点**,逐点记录返回值检查与成功/失败跳转,未发现失败路径进入受保护操作。
   边界:**op 子处理器函数体未穷尽**;`no_finding` 只覆盖已审计的 18 个调用点,不扩展为"整个认证系统安全"。
3. **依赖库**:31/31 `NEEDED` 在真实 rootfs 中定位(`deps_resolution.txt` 列出路径/架构/哈希/符号),wrapper 语义分析完成。
   分类更正:**依赖库可定位属逆向/环境准备成果,不计为独立的安全阴性发现**;对应结论保留但按"技术命题"归档。

## 证据强度

- 21 条证据全部 `verified=1`;18 条有来源链接(10 `direct-result`、8 `command-write`),3 条历史证据保持未关联(多个写类调用同名产物,不猜)。
- 复核方法分布:`recheck-sha256` 只证明**产物完整性**,不证明安全判断;三条结论的安全判断依据是反汇编/数据流交叉核对(`cross-check`)与调用点清点。
- 新增领域约束:`conclude(no_finding)` 现有 ≥1 条**经复核**证据的要求(`tests/test_conclude_strength.py`),避免"工具跑成功即阴性"。

## 运行状态 / 研究质量 / 目标关闭 —— 三者分开

| 维度 | 状态 |
|---|---|
| 执行 | 两个 run 均 `finished`;无 running 尝试 |
| 研究状态闭环 | `closure_blockers('drill') == []`(材料布局修复 + 面 `fwroot/` 如实 `out_of_scope`) |
| 质量验收 | `assess(verdict=completed`),但 info 级 `decisive_attempts_have_direct_evidence` 为 0/7 —— 结论建立在弱来源 + 复核记录上 |
| 目标关闭 | **未关闭**;需用户显式确认 |

## 未解决

1. `.text` 头部 768 B OTP 与约 123 个未解字;2. 各 op 子处理器函数体未穷尽;3. 库内 token 逻辑只到调用点粒度;4. 无动态运行(混淆 CGI 无可加载入口);5. 3 条历史证据来源不可判定。
