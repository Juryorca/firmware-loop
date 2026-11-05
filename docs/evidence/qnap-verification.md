# QNAP authLogin.cgi 研究成果独立核验(离线、确定性)

对象:`state/_scratch/realdrill-20261105-164147`(原始固件片段 `fwroot/authLogin.cgi`,AArch64 stripped)
工具:`tools/verify_recovered_text.py`(可重跑)+ `aarch64-linux-gnu-objdump`/`readelf`/`strings`

## 1. 4096 字节周期 XOR —— **独立复现,成立**

`key = 原文 .text XOR 恢复文本`,对 key 做周期自相关:

| 周期 | key 一致率 |
| --- | --- |
| 1024 | 0.0054 |
| 2048 | 0.0029 |
| **4096** | **1.0000** |
| 8192 | 1.0000 |

⇒ "4096 字节周期"可重复验证(8192 同样 1.0,因为 8192 是 4096 的倍数,不冲突)。

## 2. .text 恢复长度 —— **成立**

- `recovered_text.bin` = 121588 字节,与声明一致;
- sha256 = `7e2afa30ed65ea55bc5a2b4ba73aa2200480ffc94d4a2c36a6c582b844974897`;
- `.text` offset=0xed00(声明里给的是同一区段;ELF 头/节表由恢复脚本重建)。

## 3. AArch64 解码质量 —— **成立(静态层面)**

`objdump -d authLogin.recovered.elf`:

- 反汇编指令 32170 条;疑似非法/未定义 9 条(**0.03%**);
- 函数序言 `stp x29, x30` 190 处;BL 调用 3815 次,其中 **1304** 条目标落在 .text 内(其余为 PLT/外部,合理)。

⇒ 恢复后的字节流可被 AArch64 反汇编器正常解码,"恢复质量"这一项成立。

## 4. `qnap_exec` 族的"无命令注入"结论 —— **范围受限,不能扩大**

独立计数(PLT 调用点):

| 符号 | 调用数 |
| --- | --- |
| `qnap_exec` | 13 |
| `qnap_exec_pipe` | 6 |
| `qnap_popen` | 4 |
| 合计 | **23** |

⇒ 模型的"23 处调用点"与独立计数一致。

**限制(重要)**:

- 该二进制里存在 `HTTP_COOKIE`、`lang` 字符串,以及 `qnap_sort_get_locale_of_display_language` 导入;
  模型声称"唯二格式化实参是 SMTP 口令与本地化语言串",但**未独立追出语言串的来源**;
- 若语言/主题值来自请求(Cookie、`lang` 参数)并经 `%s` 进入 `qnap_exec`,则"无注入"不成立;
- 现阶段结论只能写成:**静态 argv/字面量调用点未见请求参数直接进入命令串**;不能扩大为"整个程序不存在命令注入"。

## 5. 认证状态机 —— 保持 inconclusive

模型未完成认证前 func 分发(2FA/passwordless/qr_code/approve/verification_code)的逐分支状态机审计;
没有绕过证据,也没有排除证据 ⇒ **inconclusive 保持**。

## 6. 汇总

- 成立:4096 周期 XOR、121588 字节恢复、AArch64 解码质量、23 个调用点计数;
- 受限:命令注入否证只覆盖静态可判的字面量/argv 路径,语言/Cookie 数据流未验证;
- 未完成:认证状态机(维持 inconclusive)。
