# 反例库 / 勿报判据(提假设前先扫)
> 条目格式: `## <形态> | 勿报判据 | 跨固件适用性 | 证据 | (日期)`
> 收口/准入见 `AGENTS.md`「文件准入与归属」;本文件只放条目。

> **来源标注**:条目里的 `<目标>/…` 仅为来源标注(该目标的证据随目标清理,**不要求长期存在**);复用依据是条目自身的判据/反例/现象。

## 反例 1:限流 / 锁定 / 退避类控制按其设计拒绝服务 ≠ 漏洞 | (2026-09-18)
**形态**: 某认证服务在失败/并发超限时拒绝后续登录,未认证方能把该状态填满,
使**其他来源**用**正确口令**也无法新建会话。

**勿报判据(先证"控制被错误规格化";四问任一为"是"才值得上报)**:
| # | 问题 | 判读 |
|---|---|---|
| a | 状态是否**本意按来源/账号隔离**,却做成了**进程全局**? | 是 → 可能是缺陷(全局缺省) |
| b | 阈值/衰减是否与设计意图不符 —— **永不自愈**才算? | 是 → 可能是缺陷 |
| c | 是否影响**已建立**会话,或造成**不可逆**影响? | 是 → 可能是缺陷 |
| d | 同产品是否**已有**按来源的控制? | 是 ⇒ 全局更像**刻意设计**(反而倾向不报) |

四问全"否" → 按**设计内行为**处理:只记观察,不产出 advisory、不备 CVE 材料。

**为什么这条难判(代价)**: "可复现" + "跨来源影响"都是**必要不充分**;这类现象极易被做成
一个看起来很完整的 advisory(本工作区就差点这么发出去)。

**检测法(可复用)**: 用**两个不同来源**同时打 —— 一个持续制造失败登录,另一个用**正确凭据**新建会话,
看是否被拒(`too many ...` 之类);再停掉 flood 看是否**自愈**。
负对照 = 无 flood 时正确凭据正常建立。整机环境选型见 `methodology.md`。

**跨固件适用性**: **高** —— 任何带失败锁定 / 退避 / 并发上限的固件(路由器、NAS、摄像头、打印机、交换机)。

**证据(本工作区实例,项目证据不随知识库走)**: RouterOS 7.24.4 已差分复现并判为设计内;
运行记录与 PoC 见 `RouterOS-7.24.4-arm64/artifacts/poc/obs01-*`。

## 反例 2:`nv::message` 报文“同 id 不同类型”的堆类型混淆 —— 看着像,实际键里带类型 | (2026-09-19)
**形态(乍看像漏洞)**: 二进制消息格式 `"M2"` + TLV,元素 tag 的高 5 位是类型、低 27 位是 id。
libumsg 里有 5 个“取或建值槽”的 helper(`sub_25960/259AC/259FC/25A4C/25A98`),
**共用同一个链表头**(impl+0x10),节点 `{id, next, value}`;但各类型**节点分配尺寸不同**
(字符串 12B、8 字节值 16B、16 字节 blob 24B)。若同一 id 能用不同类型出现两次,
`sub_25944` 只按 `*node == key` 命中 → 后一次写入可能越过后一次分配尺寸 ⇒ 堆溢出/类型混淆。

**勿报判据**: 先确认**键里是否包含类型位**。本例 `unflatten` 传的键是 `tag & 0xFCFFFFFF`
(只清 bit24/25 的“长度宽度”标志),**保留高 5 位类型**;节点析构 `sub_2A8F8`/`sub_2ACE0`
也用同一 5 位分派,且覆盖解析器接受的全部类型。→ 同 id 不同类型 = **不同节点**,不发生复用。
**检查顺序**: ①键的掩码里有没有把类型位清掉;②析构分派用的类型位是否与构造时同一套;
③各类型分配尺寸 vs 该类型写入宽度是否一致(用 `_ZN6stringC1Ev` 这类构造函数反推
`sizeof(string)` 等方法确认“看起来 12B 不够”其实是 4 字节句柄)。

**跨固件适用性**: 中 —— 任何“id+类型”复合键的 TLV/消息/属性容器(嵌入式 IPC、UPnP、TR-069)。

**证据**: RouterOS 7.24.4 `lib/libumsg.so`(`unflatten` 0x2C464、`sub_25944`、5 个 helper
与 2 个析构函数逐条反汇编核对);分析记录见
`RouterOS-7.24.4-arm64/artifacts/recon/`(§10.1 OBS-08)。

## 反例 3:输入来自**管理员配置**(或分支被配置值域锁死)→ 不是预认证缺陷 | (2026-09-19)
**形态**: 静态审计发现一处下标/长度未做双向校验(如 `bitmap[atoi(模式串里 \ 之后的数字)]`,
只查了上界没查负值),但该字符串**来自配置**(访问规则/策略/模板),不是报文输入。
**判据**: ①先看数据源 —— 配置(`rule+0xNN` / `nv::store` / `.x3` 文件)⇒ 不在未认证威胁模型内;
②再看分支进入条件是否在配置值域内可满足(做法见 `methodology.md`「配置门控分支:先算值域」)。
两者都不满足才值得继续投动态成本。
**处置**: 记加固建议(建议做双向校验),**不写 advisory、不进 B 类发现、不占验证轮次**。
**实例**: RouterOS 7.24.4 `nova/bin/tftpd` —— 访问规则正则的 `\N` 负下标(写 1 字节到 50 字节位图
之前)与 ACK 窗口位图分支(需 `reading-window-size == 0xff`,而该属性值域只有 `none`)。
证据: `RouterOS-7.24.4-arm64/artifacts/poc/tftpd-triage-evidence.md` §3/§4。

## 勿报判据补充:可预测的 capability ≠ 一定可报 —— 先量化时间窗 | (2026-09-19)
**形态**: 服务端用一个小整数 key 授权派生连接(本例 RouterOS btest related key,= accept 分配器索引);
key 可预测/可枚举,且不绑定来源地址 → 乍看是“未认证接入”。

**判据(报之前先答)**:
| # | 问题 | 判读 |
|---|---|---|
| a | 合法对端会在多短的时间内把窗口填满? | 若 <~10 ms(本例 ~2 ms),则**必须**报告为 race,并给出实测命中率 |
| b | 攻击者能否在不改变 allocator 的情况下反复重发? | 能(预置已 accept 连接池)→ 可复现;不能则只是理论 |
| c | 泄露/影响的数据是否真有价值? | 本例为合成测试流量(全 0/随机)→ 影响限定为完整性+可用性 |
| d | 上游是否已为同一机制发过 CVE? | 是(67277)→ 本条是**残留面**,advisory 必须写明机制差异,否则会被判重复 |

**做法**: 先写清“窗口宽度 + 命中率 + 影响数据性质”,再决定 advisory 的严重度措辞;
把“已接受但本轮未取到流”这类中间态如实列出。

**跨固件适用性**: 高 —— 任何用可预测 token 授权数据连接/辅助会话的协议。

**证据**: RouterOS 7.24.4 `nova/bin/btest`;窗口 ~2 ms(客户端收到 id 后 3 个 related 连接在 2 ms 内建立),
命中率 2/5;记录见 `RouterOS-7.24.4-arm64/artifacts/poc/btest-related-connection-evidence.md` §4。

## 反例 4:块密码“部分块越界写”被密文尾部 memcpy 原样还原 —— 瞬时越界 ≠ 内存破坏 | (2026-09-20)
**形态(乍看像漏洞)**: 自带/打包的 CBC 解密实现(OpenSSL `AES_cbc_encrypt` 风格)在**原地**解密最后
一个不足 16B 的块时,会 `tmp=*(block128*)in; AES_decrypt(in,out); …; memcpy(out+len,tmp+rem,16-rem)`,
读/写各越界 `16-rem` 字节。若调用方用网络可控长度且不校验 `%16`,静态看就是“堆越界写”。

**勿报判据(先问这三条,任一命中就不算内存破坏)**:
| # | 问题 | 判读 |
|---|---|---|
| a | 越界区最终是否被**还原/覆盖**? | 原地分支里 `memcpy` 把**解密前保存的**原字节写回 `out+len`,越界区最终与调用前**逐字节相同** ⇒ 无净破坏 |
| b | `in != out` 分支会不会越界? | 多数实现把部分块解密到**栈上临时缓冲**、只写 `rem` 字节 ⇒ 不越界 |
| c | 内核里越界目标是否可写且连续? | 目标通常是同一 `skb` 分配内的 tailroom/`skb_shared_info`,可写连续 ⇒ 连 fault 都不会有 |

**为什么这条难判(代价)**: guard page 实验会让**瞬时写**命中只读页并报 `UC_ERR_WRITE_PROT`,
看起来“越界写成立”;但那只证明“发生了一次访问”,不证明“内存被破坏”。本工作区就据此差点
发出完整 advisory(report 草稿已写、截图已做),真机 12 次投递无崩溃才回头复核代码发现还原。

**检测法(可复用)**: ①先读实现是否有 `memcpy(out+len, tmp+rem, 16-rem)` 这类**还原**;②判定实验 =
把 `buf+len` 之后预填图案(如 `0xaa`),调用后读回比对 —— 图案不变=无净破坏;③guard page 只用于
“确认访问发生过”,**不能**单独作为破坏证据;④真机以崩溃/日志做独立确认。

**跨固件适用性**: 高 —— 任何自研/打包的 CBC/ECB 解密、TLS/DTLS/IPsec/VPN 记录层、以及所有
“临时缓冲 + 写回”式密码代码。

**证据**: RouterOS 7.24.4 `misc/sstp.ko`(arm64 与 CHR x86_64 同源);调用点 `sub_1690`
(`skb->len - v5` → `AES_cbc_encrypt`,无 `%16`);还原实验与真机记录见
`RouterOS-7.24.4-arm64/artifacts/poc/sstp-accelerator-reachability.md`
(脚本 `sstp-cbc-restore-test.py`、`sstp-accelerator-poc.py`)。VR-04 撤回。

## 反例 5:SET 值不校验 ASN.1 类型,但写入目标与合法请求相同 ⇒ 不放大能力 | (2026-09-20)
**形态(乍看像漏洞)**: SNMP agent 的 SetRequest 对某变量(如 sysName)允许任意**声明类型**的 value
(IpAddress / Counter64 / OCTET STRING 均可),解析器只按声明 length 取内容、不比对 tag,
结果把该 value 的**原始字节**写进目标配置(实测 `/system identity set name="\FF…"`)⇒ 看着像类型混淆。

**勿报判据**: 问「**这一段输入还能被送到哪**」—— 若写入目标就是该变量本身,且合法类型也能写入同一处,
顺序/格式畸形**不越过任何既有边界**,则能力不放大(本例各类型解析器仍各自夹紧:INTEGER ≤4、
IpAddress ==4、OID tag==6、64 位 ≤8)⇒ 不报。
**与反例 2 的区别**: 反例 2 是**存在第二条内存路径**(同 id 不同类型命中同一槽位),本例只是**同一写入路径
接受了多余类型**。判据落点是「是否有第二条 sink」,不是「类型是否严格」。
**跨固件适用性**: 中 —— 任何 SNMP/配置写入协议;先查该变量是否有第二消费方(长度/指针语义)。

## 反例 6:定长 rdata 不校验 RDLENGTH 的越界读 —— 若"读宽 == 游标前进量"则必被收尾 `cursor==end` 抵消 | (2026-09-20)
**形态(乍看像信息泄露/DoS)**: DNS RR 解析器(如 RouterOS `nova/bin/resolver` 的
`sub_19E08`)对 A/AAAA/SRV 按**固定宽度**读 rdata(4/16/6 字节),只校验
`end >= cursor+10+rdlength`,不校验 `rdlength >= 宽度`。rdlength 偏小且该记录在报文尾部时,
读取越过报文尾 ⇒ 表面上是 CWE-125,疑似可泄露后续缓冲/可致崩溃。

**勿报判据(两步)**:
1. **写清"读发生了什么"还不够,要算"然后呢"**: 该记录读完后游标按**定长**前进
   (A +14 / AAAA +26 / SRV +6)。若"读宽 == 前进量",则越界读必然使 `cursor > end`;
   而调用方(`sub_1A344`)收尾**强制** `cursor == end`(否则丢弃整包)⇒ 字段值被丢弃、不进入应答。
   反过来,要让 `cursor == end`(解析成功),读的末端恰好落在 `end` ⇒ 不越界。**两者互斥**。
2. 再看**读落点是否仍在映射内存**: 服务端缓冲 `malloc(>=4096)`、全局复用缓冲、DoH body 字符串 ——
   `end` 之后仍有映射 ⇒ 不 fault。
3. **动态对照**: 合法 RDLENGTH(control)vs RDLENGTH=0(payload),看应答是否出现"报文里没有的数据"。
   本例 payload 得 SERVFAIL/ancount=0 ⇒ 越界读发生但不可观测(证据:`RouterOS-7.24.4-arm64/artifacts/poc/dns-rr-rdlength-oob-*`)。

**跨固件适用性**: 中高 —— 任何"定长字段 + 声明长度未校验"的解析器都需先做这一步(读宽 vs 前进量、
是否有 `cursor==end`/`consumed==len` 类收尾校验),再决定是否上报。
**与反例 4 的关系**: 反例 4 是**越界写被原样还原**(净变化 0);本例是**越界读被收尾校验抵消**
(值被丢弃)。两者的通用教训相同: 先判"净效果/后续校验",再判形态。

## 反例 7:UPnP 事件订阅的 CALLBACK 指向任意主机 —— 协议内机制,仅"连到指定主机"不足以成立 SSRF | (2026-09-20)
**形态(乍看像 SSRF)**: UPnP IGD 的 SUBSCRIBE 让客户端提供 `CALLBACK: <http://host:port/>`,
设备随后向该 URL 发 `NOTIFY`。实测 RouterOS 7.24.4 `nova/bin/upnp` **不校验 callback 主机是否等于订阅者**:
订阅者在 10.0.2.2、callback 填 `http://10.0.3.2:8888/`(另一网段),宿主 listener 收到了 NOTIFY
(含 `<OSBuildNumber>` 等设备事件)。看上去是"未认证客户端可让路由器任意连内网/元数据地址"的 SSRF。

**勿报判据(两步)**:
1. **机制是否协议内约定**: UPnP 事件架构里 CALLBACK 本就是**订阅者自选的通知地址**(规范允许指向云端
   通知端点)⇒ 跨主机/跨网段是协议语义的一部分,不是实现疏漏。对照:`/ip upnp` 还提供
   `allow-disable-external-interface` 等安全开关,说明厂商对暴露面有取舍。
2. **越界后果是否具体**: 要让"任意 callback"从设计内升级为可报 SSRF,须再证明**具体越界 consequence**
   —— 例如通知体携带攻击者本不可见的敏感数据、或请求可被做成对目标有副作用(POST/带凭据)、
   或能绕过目标自身的来源校验。本例请求体固定为设备事件 XML、方法为 NOTIFY、收包方=订阅者本人
   (或订阅者选择的端点)⇒ 仅构成"让路由器做一次 TCP 连接 + 固定 HTTP 请求"的侦察面,不达上报线;
   加固点 = 把通知目标限制为订阅者地址/白名单。
**跨固件适用性**: 中 —— 所有带事件/回调/通知机制的服务(WEBHOOK、SNMP trap sink、UPnP eventing、
firmware update callback)。**通用判据**: 先问"回调地址由谁选定、协议是否允许跨主机",再问"越界后果是什么"。

## 反例 8:「预认证白名单 ≠ 无鉴权」+ `===` 令牌比较的假值语义(`null` vs `undefined`) | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像 1(整类)**: 反代(`auth_request`)把一批端点列入"无需鉴权"白名单(如 `/api/ups/peer/pair`、
  `/api/blueprintSetup/complete`、`/api/stackable/users/backup`)—— 读白名单会以为这些端点是预认证可用的动作面。
- **实际**: 白名单只决定"反代不拦",**鉴权可能在应用层的三个不同层**:①权限掩码 `Se(mask)`;②凭据/身份门控
  (`pe.use`/`p8e`/`gC.use`);③**特性开关** `ve("x")`(不满足返回 **404**,不是 401)。本轮逐点核后:
  `ups/peer/pair` 需**已验证的 owner 用户名+口令**(`KP`→`EX` 打到 users 服务)、`unpair/shutdown` 需
  `x-ups-challenge`=**TOTP** over `${token}:${upsMac}`(token 仅在已认证 pair 时写入);
  `blueprintSetup/complete` 被 `Md()`(已应用 blueprint)门控;`stackable/*` 被型号/配置特性门控。
- **判据(可复用)**: ①拿到白名单后**必须**逐个回应用层找 router 级 `.use()` 与 per-route middleware
  (router 级 `.use()` 对该 router 下**所有**路径生效,即使没有匹配路由);②`404` 可能是**特性开关**门控而不是路由缺失;
  ③判"预认证动作面"前先问:**这个动作的凭据是什么、由谁写、什么时候写**。
- **看着像 2(单点)**: Node 侧门控 `if (req.get("x-group-token") === shadowToken())`;当状态里从未有 shadowToken 时,
  读 schema(`z.string().optional()`)会以为两端都是 `undefined` → `undefined === undefined` → 绕过。
- **实际**: 状态构造器把缺省值落成 **`null`**(`undefined === null` 为假)⇒ 恒拒;实测"缺头 / 空头 / 伪值"
  三种输入经反代与**直连后端 socket** 全部 401。
- **判据(可复用)**: ①`schema` 写 `.optional()` **不等于**运行态是 `undefined` —— 先读**初始值构造器/默认值**;
  ②这类比较的判读要实测**四种输入**(缺头/空头/错值/对值),不能只测错值;
  ③必须**直连后端**(unix socket / 内网端口)复现一次,以排除"其实是被反代拒绝";
  ④排除时顺便记录正向对照(`GET /api/apps` 直连=200,证明链路与比较逻辑确实被执行)。
- **代价(本轮)**: 先写出 2 条"像绕过"的假设(H1 shadow token 比较、1b 整面预认证),逐点核后**全 MISS**;
  若不写"判据①②③"就提交,会是一份自证不成立的报告。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round3-pre-auth-triage.txt`、notes §4(1b1–1b5/1d)、§10、§24.2。
- **⚠️ 修正(2026-09-21 第 12 轮)** —— 上面"看着像 2"的结论**不完整**,当时只跑了"缺头/空头/伪值"三格,**没跑对值**:
  1. **`null` vs `undefined` 不是真正原因**: Koa 的 `ctx.get(name)` 在请求头缺失时返回 **`''`**(空串),不是 `undefined`;
     未配对态 `GE()` 也拿不到 token ⇒ `'' === undefined` 为假 ⇒ 恒拒。结论(未配对态恒拒)本身成立,但机理解释要改。
  2. **配对态 + 真值 ⇒ 比较通过**: 造 paired 态(`consoleGroup.yaml` 置 role=SHADOW + 真 `shadowToken`)后,
     `x-group-token` = 真值 → **403**(到达 handler `rC`→`Koe`,role=SHADOW 拒),错值/缺值 → 401。
     ⇒ 该门是**共享 bearer token**(`shadowToken`=配对时 `randomUUID()`,122bit,经 11083 对等通道交付),
     不是"恒假的伪比较",也**不是可绕过的比较式门控**。判据: 比较式门控排除前,四格里"对值"必须跑;
     排除"猜"之前先看期望值的**生成函数**(这里是 `randomUUID()`,不可猜)。
  3. **router 级 `.use()` 归属纠错**: 本轮用该应用的 koa-router 做最小 harness 实测 —— `a.use`(置 418)
     对**注册在另一个 router 上**的 `/b` **不生效**(200),对无匹配路由的 `/zzz` 也不生效(404)。
     ⇒ 本条目旧判据①里"router 级 `.use()` 对该 router 下**所有**路径生效,即使没有匹配路由"**是错的**;
     正确表述: `.use()` 只对**注册在同一 router** 的路径生效(与它们的 per-route middleware 同链、先于后者运行),
     **跨 router 不生效**。第 3 轮把 1d 的 401 归给 `pe.use` 也就错了(实为与路由同 router 的 `gC.use`)。
  证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round12-shadow-token-1d.txt`。
- **交叉引用(第 13 轮)**: 「上游信任身份头 ⇒ 认证边界失效」**不等于可网络利用** —— ulp-go 实测仅凭
  `x-userid`+`x-userrole` 两枚头即回数据(不校 token),但所有经 nginx 到它的 location 要么 `auth.conf`
  (`auth_request_set` 覆盖)、要么 `strip-internal-headers.conf`(清空)⇒ **无到达路径 = 加固观察**,不产 advisory。
  判据: 上游是否信任头(机制) 与 攻击者能否置值(可达性) **分开证**;可达性看 location 的 include 组合。
  证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round13-mtls-authz-hosttrust.txt`(§D)。

## 反例 9:「请求体断言身份(`userId`)优先于会话身份」看着像 authz 绕过 —— **先 grep 官方前端** | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像**: `POST /api/backup/upload` 的服务端逻辑是
  `let n = z.object({userId: z.string()}).safeParse(req.body);
   if (n.success) o = n.data.userId;            // ← 客户端说了算
   else { /* 才去 session 里取用户并校验 isOwner, 否则 403 USER_NOT_VERIFIED */ }`
  —— 只要请求体带 `userId` 就**跳过**"必须是 owner"的校验。实测差分确认:
  不带 → `403 USER_NOT_VERIFIED`;带任意值(甚至 `1`、任意 uuid)→ 直接进入备份 extract 引擎。
  预认证(未配置态的 setup 流程)可达,restore 链会把备份内容(含 owner/SSO 身份、设置)导入 ⇒ 看似可提权。
- **实际**: 该字段是**无本地会话流程的正当输入**。官方 setup UI(`@ubnt/uhd-setup/dist`)的调用原文:
  `n = new FormData; n.append("backup", t, t.name); n.append("userId", de.uuid); POST /api/backup/upload`
  —— 未配置的机器上没有本地会话,客户端必须声明"这份加密备份属于哪个云用户",服务端据此选解密密钥。
  也就是说 `userId` 是**密钥选择提示**,不是授权声明;真正的授权在云端(SSO/ssoAuth)与备份自身加密上。
- **可复用判据(跨固件)**:
  1. 见到"请求体里的身份字段压过会话身份"的形态,**先 grep 官方 Web UI/客户端**该端点的调用(`FormData`/`append`/URL 常量表);
     官方客户端若在**无会话流程**里正是这么发,它是设计内输入,不能按绕过报。
  2. 判"是否放大能力"要问: 合法调用者**有没有**这条路径(反例 5 的同款判据)。此处合法调用者=setup UI,
     它本来就在无会话状态下上传 ⇒ 未放大能力。
  3. 若仍怀疑,把判据写成可证伪形式(本项目 O15): 必须证明"**post-setup 的非 owner 用户**借该字段在 restore 路径
     取得其既有权限之外的能力(装用户/API key/证书)",否则判设计内。
- **代价(本轮)**: 代码级疑点 + live 差分都指向"绕过",若就此产出报告即为误报;多花一步 grep 官方 dist 即翻案。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round4-auth-and-presetup.txt`、notes §11.1/§11.2(O15)。

## 反例 10:「HTTP 状态码 200 ⇒ 鉴权通过 / 接口可达」—— 这类后端把 `CODE_UNAUTHORIZED` 放在**响应体里** | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像**: 直连后端探测时,一批 API 返回 **HTTP 200**,于是判"这些端点无鉴权、预认证可达"。
  实测(ulp-go `:9080`、uid-agent `:6080`):
  `GET /api/v2/identity/subjects` / `/ucs/public` / `/api/ulp/users` → **200** 但体内
  `{"code":-17,"codeS":"CODE_UNAUTHORIZED","msg":"You do not have permission to perform this action.",…}`
  (ulp 甚至附 `"error":"CODE[-17];Mesg:ULP is not setuped"`)。
- **实际**: 这些服务用**业务码**(`code`)表达结果,HTTP 状态码只代表"HTTP 层成功";鉴权失败照样 200。
  与之对照,`/api/props` 的 200 里是 `{"code":1,"codeS":"CODE_SUCCESS",…,"data":{…}}` ——**同状态码,两种语义**。
- **可复用判据(跨固件)**:
  1. **状态码不是鉴权信号**;判"可达/已授权"必须读体(或头)里的业务码字段。先对**已知拒绝**的路径取样,
     确认该后端用什么表达拒绝(`code<0`/`CODE_UNAUTHORIZED`/`errCode`…),再把判据套到候选路径上。
  2. 经验枚举路由时(`→ methodology.md`「Go 后端路由表的经验枚举法」),**非 404 不等于找到路由**,
     要区分"路由存在但拒绝"与"路由不存在"(404 才是"不存在";200+UNAUTHORIZED 是"存在且拒绝")。
  3. 反面同样成立: 404 也可能被包装成 200 的情况要在取样阶段先排除。
- **代价(本轮)**: 我先把"localhost 后端无鉴权、200 即数据可达"写进了分析结论,抽样读 4 个响应体后自我纠正;
  若直接上报会是一条自证错误的结论。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round5-public-mount-enumeration.txt`、notes §12.2(O17)。
- **同族变体(WS)**: 「WebSocket 升级返回 **101 Switching Protocols** ⇒ 认证通过」同样不成立。实测未知路径
  `/api/ws/nonexistent-xyz` 也返回 **101**,应用是先接收升级、再在处理器里 `close()`/回 ERROR 文本。
  判据: 看**升级之后的帧**(ERROR 文本 / 关闭码 / 是否回 PONG),而不是握手状态码。详见 `tool-quirks.md`
  的「WebSocket 升级探测」条(探测方法权威副本在该条)。

## 反例 11:「Go 请求处理器 panic(`CODE_DIRECTORY_PANIC` 之类)⇒ 漏洞」不成立,除非能证明外泄/崩进程/坏状态 | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像**: 预认证的 `POST /api/idp/sso/saml` 发一个**缺 `<saml:Issuer>` 的 `samlp:Response`**,立即得到
  `HTTP 200 {"code":0,"codeS":"CODE_DIRECTORY_PANIC","msg":""}` —— Go 的 nil 解引用 panic,看起来是"预认证可打的崩溃/DoS"。
- **实际**: panic 被 **recover 中间件**接住并转成业务错误码:HTTP 仍 **200**、**响应体不含栈**,进程 pid 不变、`RSS` 不变、
  `service.crash.log` 字节数前后一致,连打 60 次无任何累积效应;应用日志里也**搜不到** `panic/goroutine` 栈。
  即"预认证 unhandled error"但没有可用影响 ⇒ **不构成 DoS/信息泄露**,不产出 advisory。
- **可复用判据(跨固件,托管运行时通用)**:
  1. **先分清"未捕获崩溃"与"被 recover 的 panic"**:证据 = 进程是否重启(pid/start-time)、崩溃日志是否增长、
     响应里有没有栈、重复打是否累积。任一项为"是"才升级。
  2. 把 oracle 写成**三选一的可证伪形式**: ①栈外泄到响应/日志可达位置;②未 recover 且进程退出/重启;
     ③panic 发生在持锁/半写事务中且能被观测到状态损坏。三样都取不到 ⇒ 判"无影响"。
  3. 别用"响应里有错误码"当影响 —— `CODE_*_PANIC` 只是一个业务错误码,HTTP 200 是这类后端的常规形态(参见反例 10)。
- **代价(本轮)**: 这条本来最容易写成"预认证 nil-deref DoS",多跑 60 次 + 查 pid/RSS/崩溃日志/日志搜索即翻案。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round10-saml-presuth-triage.txt`(§E)、notes §17.4(O36)。

## 反例 12:预认证"限流器被失败尝试打满"⇒ 漏洞 不成立 | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像**: 预认证的 SCIM API(`/api/idp/scim/v2/*`,自带 API key 校验)在约 20 次鉴权失败后,
  对**所有** key(含空/无 key)长期返回 `429 Too many failed authentication attempts`,约十几分钟不自愈 ⇒
  预认证攻击者可**锁死**合法 SCIM 同步(甚至阻断管理员登录路径) —— 看着像 DoS 漏洞。
- **实际**: 这是**按源 IP**的失败计数限流;bug bounty 口径亦明确**纯 DoS/资源耗尽不付**(`notes.md` §0)。⇒ **不报**,只记观察。
- **可复用判据**: 预认证可触发的资源耗尽/锁定,先问"②状态·持久化净变化是否真的发生、恢复期多长、是否有意为之";
  见既有「限流器饱和 / 锁定退避」条目;DoD:**不把 DoS 当 finding**(或仅作限制加固建议)。
- **⚠️ 复裁定论(2026-09-21 第 11 轮,由外部复核提出"可能是全局 key 空间"后实测)**:
  **是 per-source,不是 global**。判据 = **跨来源实验**:宿主来源把桶打满(429)后,**从另一个源 IP(容器内
  `127.0.0.1:443`)发同一请求仍返回 401**,不同来源互不影响 ⇒ 反例 1 四问的 (a) 为"否",属设计内。
  本次把"是不是全局"从"读码猜测"降到**两源单测**(读码结论: `ratelimit.ClientIP` 取 `X-Forwarded-For` 首段,
  nginx SCIM location 经 `proxy.conf` 以 `$remote_addr` 前置于 `strip-internal-headers.conf` 的空值 ⇒ 实际键 = 真实源 IP)。
  证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round11-scim-failure-guard.txt`、notes §19.2。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round10-saml-presuth-triage.txt`(§A)、notes §17.4(O37)。

## 反例 13:UniFi OS Server 5.1.42 SAML ACS 不校验 `<AudienceRestriction>` —— 但有 `Recipient` 绑定兜底 | (2026-09-21)
- **看着像**: 预认证的 `POST /api/idp/sso/saml`(nginx `/api/idp/sso/*`,无 `auth_request`)对签名的 SAML
  断言**完全不校验 AudienceRestriction**:缺该元素、`<Audience>` 指向他 SP(`https://other-sp.example/`)、
  任意 URN、空值 —— **全部 ACCEPTED**(实测矩阵见 `round11-saml-audience.txt` §A)。SAML 规范要求 SP 校验 Audience,
  直觉上 = "同 IdP 签发给他 SP 的断言可重放到本 SP ⇒ 账号接管",且 `InResponseTo` 也不校验(IdP-initiated 放行)。
- **实际**: 该 ACS 用 **`SubjectConfirmationData/@Recipient` 精确匹配**(`Destination` 存在时同样精确匹配)
  把断言绑定到本 SP 的 ACS URL;重放另由 **基于 Assertion ID 的 replay store** 挡住。实测:
  `Recipient` 指向他 SP → reject(缺 `Recipient` 也 reject);重放同一断言(换新 RelayState、或把**已签名的**
  `Assertion` 重新裹进一个**新的 Response ID** 里)→ 全部 reject。⇒ **无可本地证明的绕过**,`O35` 保持加固观察,
  **不产出 advisory**。(唯一残留前提是"攻击者已拿到一份 Recipient 为本 SP 的合法断言",那已等价于会话窃取。)
- **可复用判据(跨固件 SAML/SSO)**:
  1. 报 "AudienceRestriction 未校验" 前,**先测三类绑定是否也失效**: ①`SubjectConfirmationData/@Recipient`
     ②`Response/@Destination` ③重放存储的键(按 Assertion ID 还是按 RelayState/Response ID)。任一者有效,
     跨 SP 重放链就被它(而不是 Audience)挡住 —— 单看"Audience 被忽略"会得出错误的 ATO 结论。
  2. 测试方法: 每例**新取**一个 RelayState(避免单次消费污染),分别只改一个字段;
     "无 Audience"与"Audience=他 SP"两格都要有,才能区分"完全不校验"与"只查存在性"。
  3. 证明"不是打到别处"的正向对照: baseline(签名有效、字段齐)必须 ACCEPTED。
- **代价(本轮)**: 外部复核明确提出"若 ACCEPTED 即可本地证明 ATO";实测 ACCEPTED 后**再补 Recipient/重放两轴**才
  发现链被兜住 —— 只补"决定性一格"就上报会是一条把加固缺口写成 ATO 的误报。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round11-saml-audience.txt`
  (+ 可复跑 `round11-saml-audience/`),notes §19.1。

## 反例 14:同产品内"角色门 vs 权限掩码门"两套授权语义 ⇒ 越权 不成立,除非先证"受限主体"真实存在 | (2026-09-21/Ubiquiti UniFi OS Server)
- **看着像**: 同一个 Node 后端两套授权:一组路由只查 `role`(如 `role in {owner,admin}`),另一组用
  `Se(mask)=>(permissionMask & mask)===mask` 查**按 scope 计算的权限位**。直觉:只要能让一个"角色够、掩码不够"
  的主体存在,前者就成了后者的绕过(权限降低无效 / CWE-272)。
- **实际(本目标)**: 该主体**不可达** —— ①`role:"admin"` 只能来自内置 `system_key="super_administrator"` 角色
  (`role.system_key` 在有 `status=1` 时**唯一**,自定义角色 `system_role=false` 一律映射为 `role:"custom"`,被角色门直接拒);
  ②内置 admin 的 `permissionMask` 由产品默认即可,实测= **所有 os-settings 位去 {远程访问,重置,转移 owner}**;
  ③角色门那批路由**没有一条**对应这缺失的三位动作。⇒ **不存在"受限掩码 admin"**,两套语义只是同一管理角色在不同资源上的**不同表述**,不构成绕过。
- **可复用判据(跨固件:任何"role-gate"与"permission/mask-gate"混用)**:
  1. 先证"受限主体"**是产品可产生的状态**: 角色名→权限的映射是否唯一?自定义/私有角色是否会被复用成高角色名?
     高角色名的权限集是否可由低权主体(或本人)缩小?—— 三者任一为"否"就可否证。
  2. 再把"语义差"映射到**受保护动作**: 列出角色门批次路由的**实际后果**,与掩码位对应的动作取交集;
     交集为空 ⇒ 无可达绕过(只是风格不一致,非漏洞)。
  3. 判据必须**双轴都有证据**(主体存在性 + 动作重叠),只给后者会把设计不一致写成越权。
- **代价(本轮)**: 读码即可看出"两套语义",但要用**真实 setup 的内置管理员**量出 `permissionMask` 并跑授权矩阵,
  才能把"看着像"降为"设计内";若只停在第 13 轮的"admin+mask0 仍 200"(那是**注入的**掩码),
  会把一个不可产生的状态当成漏洞。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round14-t-role-mask.txt`(§B–§E)、notes §28。

## 反例 15:「目标处于未配置/setup 态 ⇒ 该状态下发现的面一律算设计内、不可报」不成立 | (2026-09-22 Ubiquiti UniFi OS Server 5.1.42)
**看着像**: 未配置态(无凭据)天然要开放初始化端点,于是"凡是未配置态可达的面"都被归为设计内 ⇒ 整片面包进"不可报"。
**判据(逐条)**: ①**设计意图边界**: 未配置态**只应**开放初始化所需的最小面;若该态还额外暴露出别的功能
(身份目录读写、口令改写、建号赋权),那是**越界**,不是设计内 —— 判"设计内"必须给出"该面是初始化所必需"的代码路径级依据;
②**镜像检查**: 完成初始化后是否仍可重跑 setup?(可 ⇒ 更严重,不是更轻);③**升级检查**: 未认证方能否把已配置实例
推回未配置态(复位端点是否预认证、是否需权限位)?
**本例结论**: 未配置态可达的**初始化端点**判设计内 ✓;但 `auth_request` 被 catch-all 200 应答导致
`/proxy/users/*`(身份 API)整片预认证可用 ⇒ **越界,成立**。⇒ "未配置态"不是免责标签,**要按面逐条判**。


## 反例 17:「看到 `exec`/`ProcessBuilder`/`child_process` 就算命令注入」不成立 —— 先追**插值数据源** | (2026-09-22 Ubiquiti UOS Server 5.1.42)
- **看着像**: 服务端存在真 shell 调用(`ProcessBuilder("/bin/bash","-c",cmd)`、Node `child_process.exec` 的模板字符串)。
- **实际**: 逐个追插值来源后,**没有一个攻击者可控值到达 shell**:
  - Java `com.ubnt.service.system.ALfCCPu` 是 `/bin/bash -c <cmd>` 的 builder,但调用者只有 `device/h/NFAXcLgiXA`
    (`tar -xzf <p> -C <d>`、`tcpdump -r <p>` 都是 **argv 数组**)与 `system/fSFkDPvCiJTebBnrTVn`(静态 `openssl` 命令)。
  - Node `gt = exec(...)` 的模板调用插值要么是固定路径/整数/挂载点,要么是 **multer 随机临时路径**(`e.file.path`);
    攻击者可控的上传文件名 `e.file.originalname` 只进 `mce/Cce/Sce` 的**正则解析**,不进 shell。
  - 其余进程调用全走 argv 数组(`spawn("sudo",[...])`、`spawn("md5sum",[...])`、`Q("systemctl",[...])`)。
- **判据(四问)**: ①该调用是 shell 字符串还是 argv 数组? ②shell 字符串里每个 `${...}`/拼接项来源是什么?
  ③来源是否经用户输入(文件名/参数/头/报文)? ④非 shell 的 argv 数组调用不算注入面。
  **四问里只要"无用户输入到达"⇒ 不是命令注入**,只记观察(不得写 advisory)。
- **同族**: 与「配置来源 ⇒ 不在未认证威胁模型」互补;那条判**可达性**,本条判**数据流是否触达 sink**。
- **代价**: 本项目 static 普查一次即可排除整类(Java 5 处 ProcessBuilder + 静态 openssl/tar/tcpdump;Node 8 处 `gt(\`…\`)`),
  避免把 `tar -tf ${path}` 这类**服务端路径**误报为注入。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round25-sink-sweep-and-nginx-differential.txt` §1/§2。

## 反例 18:「协议里有硬编码默认/工厂密钥」就叫漏洞 不成立 —— 必须追到**解密后的验收门** | (2026-09-26 Ubiquiti UOS Server 5.1.42)
- **现象**: 设备协议(本例 UniFi `/inform` 的 TNBU 帧)用一个**硬编码**默认 authkey(`ba86f2bbe107c7c57eb5f2690775c712`)解密未采用设备的报文;`X-Forwarded-For` 又被采信为设备地址。看起来"未认证即可投递任意设备数据"。
- **为什么不成立(判据)**: 解密成功 **≠** 被处理。追到字节码里的验收门:
  - `isDefaultKeyUsed = "_authkey".equals(authkeyForSourceMac)`;若为真且 `!allowed(device,req)` ⇒ **直接拒**(HTTP 404),设备状态置 `INFORM_ERROR`;
  - `allowed = (state==ADOPTING && is("adopt_manual")) || isInTwoPhaseAdoption() || (isUDM && !isAdoptionCompleted && local(req)) || (state==INFORM_ERROR && isUDM && local(req))`;
  - `local(req)`: 本地端口正确 **且无任何 `x-forwarded*`/`Forwarded` 头** **且** 源地址 = 本机地址或 loopback。
  ⇒ **远端未认证请求恒不满足**;默认键只对"采用中/两阶段采用/本机 UDM 恢复"有效,这些状态需要已认证管理员先开采用。
- **同族**: 与「预认证白名单≠无鉴权」互补 —— 前者判"能不能到达 handler",本条判"到达后有没有决定性门"。
- **可复用判据(三问)**: ①解密/解析用固定密钥时,后续有没有**基于设备状态/身份的验收门**? ②该门的输入是否攻击者可控(状态能否未认证改变)? ③"本机/来源可信"判定是否只看可伪造的报文头(本例用 `getLocalAddr/getRemoteAddr` + 禁 `x-forwarded`,不是头伪造)。
- **顺带否掉的另一形态**: inform 字段 `connect_request_ip/port` 虽会被拷入设备记录,但拷贝发生在**验收门之后的 accept 路径**,默认键不可达 ⇒ 无预认证 SSRF。
- **证据**: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round26-inform-protocol.txt` §1/§2/§6。

## 反例 19:「中继/代理把客户端可控 `Host` 复制进受信来源请求 ⇒ 可打 Host 头攻击」不成立 —— 先证"该头是**中继独有**能力" | (2026-09-25 Synology DSM QuickConnect WebRTC)
- **看着像**: 可信中继 `webrtcproxy` 把客户端 `Host`/`Origin`/`Referer`/`Cookie` 原样写进**发往 `127.0.0.1` 的 DSM 请求**(与已成立的 XFF 注入同一条 `SetCommonParam`→DTO 链)。既然 XFF 注入成立,看起来"Host 注入 ⇒ Host 头类攻击(重定向投毒/缓存投毒/域路由)"也成立。
- **为什么不成立(判据)**: 中继的**独有能力是来源身份**(loopback 源 IP 被 `set_real_ip_from 127.0.0.1` 采信),**不是 Host** —— `Host` 对任何直连客户端本来就可任意置入:
  - nginx `server_name _ default_server` + `scgi_param SERVER_NAME $host` ⇒ 任意 Host 直接进后端(非中继特有);
  - 逐个清点 DSM 侧 Host/Referer 消费者(`SynoCgiRefererMatch`(CSRF:Referer==Host)、`SynoCgiRedirect`、`IsQuickConnect`→`GetRedirectURL`、`SYNO.Auth::Login` 的 `GetEnv("HTTP_HOST","localhost")`…)后,**无一把特权挂在 loopback 源或某个 Host 值**上:
    CSRF 的 Referer==Host 对**浏览器**仍成立(攻击者不能替受害者浏览器置 Referer);重定向/HSTS 自指;`IsQuickConnect` 派生的 URL 只能落在 `*.quickconnect.to`(攻击者不控该域)。
- **可复用判据(三问)**: ①该头/标识是否**中继独有**(直连请求能否同样置入)?不能置入时才可能是中继新增能力。②下游是否把特权挂在**该值**上(而非仅来源 IP)?③把消费者按"取值来源→特权授予"逐条列全并给反例。
  ⇒ 中继/代理类的"复制客户端头"里,**只有"受信来源才能置的值"**才算新增能力;`Host`/`Referer`/`Origin`/`Cookie` 通常不属于此类。
- **与 `vuln-patterns.md`「可信中继把客户端可控头再注入」的区别**: 那条针对的是**来源标识**(`X-Forwarded-For` 被下游当客户端地址);本条划清"复制 Host ≠ 来源欺骗"。
- **代价**: 若不先证"中继独有"就去打 Host 头攻击,会把 VR-01 的机制错误外延到一整类不成立的面。
- 证据: `Synology-DS223-7.2.2-72806/notes.md` §17;`Synology-DS223-7.2.2-72806/artifacts/recon/nginx-routes-7.2.2.md`。

## 反例 20:「预认证 CGI 吐出一大坨配置(`_SYNOINFODEF` / defs)」看着像信息泄露 —— **先看取值是不是出厂静态默认** | (2026-09-27/SRM-RT6600ax 1.3.2-9366)
- **看着像**: 无会话直调 `webman/synodefs.cgi` 回 `_SYNOINFODEF={...}`,**6777 字节 / 246 个键**,含 `admin_port`/`httpport`/`ftpport`/`hostname`/`mailfrom`/`enable_syno_token`/`portforward_maximum_rules`/`ftp_pasv_def_*` 等 ⇒ 很像"未认证配置泄露"。
- **实际**: 取值**全部是出厂静态默认** —— `mailfrom` = `"Synology Router Manager"`(是发件人**显示名**,不是管理员邮箱)、`hostname` = `"SynologyRouter"`、`unique` = 机型串;其余为 `support_*` 特性位与端口号。这些正是**登录页渲染所必需**的 defs(`enable_syno_token` 决定前端要不要带 token、`admin_port` 决定跳转端口)⇒ 设计内,且不含任何站点/身份数据。
- **可复用判据(跨固件)**:
  1. 见到"预认证配置/defs 端点"先**取一次真实响应并逐键看值**,判据是**取值是否需要认证才能获得**:全为出厂默认/机型常量 ⇒ 设计内;
     出现**站点特有**数据(管理员邮箱、序列号、已配 SSID、DDNS 域名、用户列表)才是泄露。
  2. 反问"**登录页是否需要它**":需要(端口/特性位/文案)⇒ 预认证是功能要求,不是漏配。
  3. 键**数量多**≠ 严重:`246 个键`里 194 个非空,但身份类非空键仅 `hostname/unique/mailfrom(显示名)/maillist` 这类常量。
- **来源**: SRM-RT6600ax 1.3.2-9366 2026-09-27(H-04 批次 2-3)。

## 反例 21:「预认证 API 接受客户端自报的 IP/身份并写入放行名单」看着像 authz 绕过 —— **先问"攻击者能拿到什么合法流程拿不到的能力"** | (2026-09-27/SRM-RT6600ax 1.3.2-9366)
- **看着像**: `SYNO.Core.Network.NSM.CaptivePortal.NoAuth.set_ip`(authLevel=0)把请求里的 `client_ip`
  **原样**写进 captive portal 放行集合(实测捕获:`ipset add ipset_captive_portal 203.0.113.99 timeout 60 -exist`),
  且合法客户端 `captiveportal.cgi` 传的是 `getenv("REMOTE_ADDR")` ⇒ 服务端不与连接来源绑定。看似"预认证越权改访问控制"。
- **实际**: 该 portal **没有任何凭据**(配置键只有 `access_timeout_second`/`use_template`,无 password/voucher/RADIUS;
  `welcome.html` 无口令字段)⇒ **合法访客点一次同意就能给自己放行**。
  攻击者多出来的能力只有"**为第三方 IP 放行**"——而他控制不了那些地址;放行集合在 `gbr0` 上按 `src` 匹配
  (`iptables -t nat … ! --match-set ipset_captive_portal src … -j REDIRECT --to-ports 5121`),源欺骗对出站 TCP 也不通
  ⇒ **攻击者无任何收益**。撤回,不入 report/。
- **可复用判据(跨固件;与 `methodology.md`「先问正常流程有没有秘密」配套)**:
  1. 见到"客户端自报身份被采信",**先问正常流程需要什么**:若正常流程**无需凭据**(点同意/公开表单),
     则"绕过"≡"合法通过" ⇒ 收益≈0,**只能算硬化建议,不算 finding**。
  2. 再逐条列"**合法调用者做不到、攻击者做得到**"的能力;列不出**对攻击者有利**的项 ⇒ 撤回。
  3. 别把"我确实复现了这个调用"当成"这是漏洞" —— **可复现 ≠ 有安全影响**(这是本条最容易踩的坑:
     本轮就是先写了 advisory、被"攻击者能得到啥"一问才翻案)。
- **来源**: SRM-RT6600ax 1.3.2-9366 2026-09-27(VR-02 撤回)。


## 网络守护进程的“客户端头信任类”在判预认证可达前,必须先确认默认前端拓扑 | 勿报判据 | 跨固件适用性: 通用 | 证据: 见下 | (2026-10-03)
**形态**: 自研 httpd/守护进程直接把客户端 `X-Forwarded-For`/`X-Real-IP` 当作 `REMOTE_ADDR`,或信任 `X-Apache-Proxy` 之类"前端"头 ——
看着像预认证源 IP 欺骗 / 协议降级。

**勿报判据(先证"该守护进程在默认部署下能从网络直接收到请求";任一为"否"即降级)**:
| # | 问题 | 判读 |
|---|---|---|
| a | 默认启动后该守护进程绑 0.0.0.0/公网口,还是 loopback? | loopback ⇒ 前置代理会重写/追加该头,客户端值不生效 |
| b | 前置代理把真实客户端**追加在头尾**? 后端取**哪个** token? | 后端取最后且代理追加 ⇒ 客户端伪造值恒不成立 |
| c | 是否存在非默认态(代理未启动 / 特性开关 / 前端启动失败)**让后端直绑**? | 有 ⇒ 结论降级为"**非默认态成立**",不写预认证可达 |

**证据**: QNAP QTS 5.2.10.3577:`etc/rcS.d/S59Qthttpd`(解 `apache_php5.tgz`)先于 `S65thttpd` ⇒
`thttpd.sh:start_httpd` 见 `/usr/local/apache/bin/apache` 可执行即 `PORT=InnerPORT`、`_thttpd_` 绑 **127.0.0.1:58080**,
`apache_proxy` 占 8080。经 Apache 的 7 种 XFF 变体实测 CGI `REMOTE_ADDR` **恒为真实端**;直连 `_thttpd_` 才取伪造值(取**最后一个** token)。
⇒ 上一轮 `QNAP-TS-233-QTS-5.2.x` 的 VR-01 需按"非默认态/直连 `_thttpd_`"修正其预认证可达性表述。

## 诊断/维护类 CGI 的命令注入若鉴权门在 **admin 组** ⇒ 非漏洞(管理员本就 root 等价) | 目标: QNAP TS-464 QTS 5.2.10.3577 Helpdesk `QNAP_helpdesk.cgi`/`diag_QDT` | (2026-10-04)
- **场景**: `QNAP_helpdesk.cgi`(shell)把 POST 体清洗后交 `/sbin/diag_QDT`,后者把 `$enclosure_num` **原样写进** `/var/diag_soldier_command`
  (root 守护 `qdesk_soldier` 用 `sh` 执行它);清洗只黑名单 `$ { } \ ` / | ; #`,**漏了 newline** ⇒ `enc_num=X\nenc_num=<cmd>` 可注入命令。
- **为什么不报**: 门的 PHP 端 `WatchDog::isSidVaild` → `isValidUser && isAdmingroup`(sid 必须 `^[A-Za-z0-9]+$` 且 `authLogin.cgi` 判 `isAdmin=1`)⇒
  **需要 QTS 管理员会话**。QNAP 管理员可装 QPKG/有命令面,已 root 等价 ⇒ **无权限增益**。
- **判据(可复用)**: 报告命令注入前先问"门后面是谁"。若门要求 admin/root 组且该角色在本产品里已具备同等级能力,则不是提权/漏洞(记观察即可);
  真正的 finding 是"门要求**低权**用户/pre-auth 即可达"。
- **反例边界**: 若该注入还伴随 **CSRF**(管理员被诱导点击)或**跨权限数据**,则另说 —— 本例无。

## 反例 22:Content-Type 混淆(JSON 声明 text/html)+ 未转义 <>/<img> = 只证明 sink,不证明可利用
判据: 必须有"未认证/低权用户可控字段能进该 JSON"的注入面。核查方法: 字段来源 → 校验点 → 能否由未认证/低权请求写入。若字段全为校验后用户名/系统事件(如 QNAP NASLOG_NOTICE 的 user/desc/title 来自 `Is_Valid_Key_Change_PWD_User` 校验值、`nasutil` 固件串、`sdmd` 磁盘事件),则不构成 stored XSS,按硬化项记录。

出处: 2026-10-19 QNAP QTS 5.2.10.3577 `sys/syslogRequest.cgi`(H-29):sink 已实测(JSON 里 `<script>` 的 `<>` 原样 + `Content-type: text/html`),但全量核查 notice 写者后无攻击者可控字段 ⇒ 判 No finding(硬化项)。

## 反例 23:参数名写进 ini 的"键名/换行注入"——先看写入函数是否过滤 (QNAP Conf_Set_Field)
- **场景**: 认证后 API 把**参数名**当配置键写入 ini(QNAP netmgr `/api/cfg` POST:`for i in request.parameters: setcfg(NETMGR_CONF,'',i,value)`;中间件只校验值不校验键名),看着像"键名换行注入新 section"。
- **勿报判据**: 写入口 `Conf_Set_Field`(QNAP `libuLinux_Util.so.0.0`,实测@0x153c0)在落盘前做字符过滤:section 拒 `[]\n\r`、key 拒 `=\n\r`、value 拒 `\n\r`;键名里的换行/`=`/`[]` 一律被拒 ⇒ 节注入不成立。
- **仍成立的窄面**: 键名本身(不含上述字符)仍可写任意顶层键;是否成洞取决于**读该键的消费者**,不能只看写入口;判 finding 前须追到"键被谁读 + 读到后做什么"。
- 来源: QNAP-TS-932PX QTS 5.2.10.3577 / 2026-10-19;netmgr `views.py` + `libuLinux_Util` 反编译。

## 反例 24:"可预测 id + 无 owner 校验"≠可利用 IDOR——先算枚举窗口与泄露渠道
- **场景**: 管理 API 用"可预测 id"取对象且无 owner 校验(QNAP netmgr:`rid = repr(time.time()*1000000)`;`/api/asd?issue_no=` 读 rq job 结果、`/api/batch_request/<rid>` commit/abort),看着像 IDOR。
- **勿报判据**: 先算**可行枚举窗口**——结果 TTL 600 s = 6e8 个微秒候选、batch TTL 86400 s = 8.6e10;盲枚举不可行。仅当存在"已知时间窗口"(攻击者可诱导目标在已知时刻发请求)或 id 有**其他泄露渠道**(响应/列表/日志)时,才升级为可利用。
- **可报的形态**: id 为自增/短随机/可枚举列表接口,或同 id 被回显给他人;此时按"按唯一 id 取对象漏 owner 过滤"报。
- 来源: QNAP-TS-932PX QTS 5.2.10.3577 / 2026-10-19;仅作勿报判据,未报 finding。

## 反例 25:"FileStation subtitle 的 file_name 不查 .."——5.2.10 的两个入口都过 sub_CC3A4
- **旧判据(TS-233 5.2.10 早期)**: "FileStation `subtitle` 的 `file_name` 只查 `;`/反引号、不查 `..`(潜在路径穿越)"。
- **2026-10-19 复核(TS-932PX/QTS 5.2.10.3577 同源码 ARM64)**: **不成立**。`subtitle` 入口有两处:
  预认证 `share.cgi` 走 `sub_122FA8`(对 `path`/`file_name` 都做 `sub_CC3A4`),认证后走 `sub_65D5C` 开头 `sub_123F64`(同样对 `path`/`file_name` 做 `sub_CC3A4`)。
  动态: `file_name=../evil` 在公开分享(无口令)与 stub 会话下均返回 `status:20`(被拒),`file_name=a.txt` 正常执行(shim php 读到 `INPUT_OK .../a.txt`)。
- **勿报判据**: 看到 `file_name` 未显式 `strstr("..")` 还不够;必须确认**入口处**没有 `sub_CC3A4` 类组件校验(两处都要查)。
- 来源: QNAP-TS-932PX QTS 5.2.10.3577 / 2026-10-19(替代 TS-233 H-37 的旧表述)。
