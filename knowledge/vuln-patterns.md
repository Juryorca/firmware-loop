# 固件漏洞模式库

> **来源标注**:条目里的 `<目标>/…` 仅为来源标注(该目标的证据随目标清理,**不要求长期存在**);复用依据是条目自身的判据/反例/现象。
> 条目格式: `## <形态名> | 目标: <厂商 型号> <函数(地址)> | 实证: <命令/输出要点> | 复用场景 | 产物 | (日期)`
> 收口/准入/维护(含“实例 vs 新条目”“盲点审计”)→ `AGENTS.md`「文件准入与归属」+ `INDEX.md`「维护规则」,**本文件只放条目,不重复规则**。
> 用法: 定向查询,非必读 —— 观察到具体事实后才 grep,禁止事前通读。

## 未检查 getenv() → strstr/strlen 空指针解引用(NULL-deref DoS)- 目标: D-Link DIR-850L FW1.10WW `htdocs/cgibin`(Alpha Networks CGI 框架)
- 实证: `sess_get_uid`(0x408678)中 `strstr(getenv("SCRIPT_FILENAME"), "/htdocs/mydlink")`;
  getenv 无 NULL 检查。gdb 现场 PC=uClibc `strstr+20 lbu v0,0(a0)`,A0=0,A1="/htdocs/mydlink",
  RA=0x4086fc,调用链 strstr←sess_get_uid←hedwigcgi_main(0x40ca80)。si_code=SEGV_MAPERR,si_addr=NULL。
- 触发: 请求上下文缺少该 env 变量时(本例是 qemu 直调 CGI 漏设 SCRIPT_FILENAME;真机 httpd/Mathopd 会设置 → 非远程可达)。
- 复用场景: 审 CGI/守护进程时,凡 `getenv()` 返回值直接喂给 strstr/strcmp/strlen/atoi 等
  未判空的 libc 调用即候选;但必须对照真实调用方是否会设置该 env(否则只是仿真假阳性)。
- 产物: `DIR850L/artifacts/sess_get_uid_null_deref.md` | (2026-09/DIR850L PoC 验证)

## 免认证 controller 中 `getenv()` 头值直入 `fork_call("ls "..x)`(命令注入)
- 目标: TP-Link Archer AX21 V5.6 1.0.2 `controller/locale.lua` `get_sysinfo`
- 实证: `lang = getenv("HTTP_ACCEPT_LANGUAGE")` → `sub(1, find(",")-1)` → 仅 `gsub("-","_")`
  → `loc = "/www/webpages/locale/"..lang.."/lan.js"` → `nixio.fs.stat(loc)` 失败 → `luci.sys.fork_call("ls "..loc)`
  触发串 `Accept-Language: en;id>/tmp/x;#,x` → strace 见 `/bin/sh -c "ls /www/webpages/locale/en;id>/tmp/x;#/lan.js"`,
  marker 落盘, `parse_signal.py` = **HIT:shell_exec,payload_exec**
- 前提: uci `locale.sysinfo.locale` 缺失/为空才会进 header 分支;出厂默认 `option locale 'en_US'` ⇒ 默认不可达,
  升级保留旧配置/配置损坏时可达(报告定性为"条件触发")。
- 复用场景: LuCI 派生框架(OpenWrt/TL)审 `fork_call/os.execute` 时,凡是 `getenv(HTTP_*)`/`formvalue`
  经**只替换部分字符**(gsub 单一字符)的白名单即候选;`^[%w_]+$` 才算安全校验。对照同文件 `set_sysinfo`
  有 `^[%w_]+$` ⇒ 应排除。
- 产物: `AX21/artifacts/poc/poc_locale_accept_language.lua` + `.strace` | (2026-09-12/AX21 PoC 验证)

## 不完整的"shell 转义"helper(漏反引号)复用于多个 handler → 命令注入
- 目标: Linksys E8450 1.2.00.360516 `portal.cgi`(aarch64) `sub_406BB8`(0x406BB8)
- 实证: helper 逐字符处理,仅对 `"`(0x22)、`$`(0x24)、`\`(0x5C) 前置 `\`(条件 `((c-0x22)&0xFD)==0 || c==0x5C`);
  **反引号 0x60 不在集合内**。调用点把结果放进**双引号**上下文:
  `sprintf(cmd,"echo -n \"%s\" | openssl ...")` → `sub_404A50`(popen)。双引号内反引号仍做命令替换。
  载荷 `` `id>/tmp/PWNED` `` → strace 见 `execve("/bin/sh",["sh","-c","echo -n \"\`id>/tmp/PWNED\`\" | openssl ..."])`,marker 落盘,
  `parse_signal` = **HIT:shell_exec,payload_exec**;普通字符对照无 marker(A/B)。
- 命中点: 登录路径 `sub_417968`(0x417cc0)/ guest 登录 `sub_4249A0`(0x424a98),字段均为 `http_passwd_hidden`(免认证)。
- 复用场景: 审代码时看到自定义"转义/过滤"函数,**先精确还原它的字符集**,再按**上下文引号类型**判断:
  双引号内 → 反引号、未转义 `$()`、`\n` 都可能致命;单引号内 → 只需 `'`。
  常见漏项: 反引号、换行、`!`(历史)、以及只转义一个引号类型。
- 产物: 两份请求体 + 对应 strace(免认证/带会话对照)| (来源标签: E8450 轮 2026-09-14;该目标已归档,产物不随库保留)

## 单体 CGI 的 action 分派表位于认证/CSRF 闸之前 → 整张表免认证
- 目标: Linksys E8450 `portal.cgi` `sub_42E1E0`(taskManager)
- 实证: 入口先 `sub_422FB8` 取 `page`/`action`,然后立即按 **action** 查静态表 `off_452D98` 并分派到
  `sub_42D8A0` 的 case(共 36 个 action);**CSRF 校验 `sub_42DD10` 与页面分派表 `off_453110` 都在其后**。
  即该 36 个 action 天然免认证(含 register_email/set_wizard_status/update_fw/handle_guest_login 等)。
- 复用场景: 审厂商单体 CGI 时,先定位"请求取参 → 分派表 → 业务函数"的顺序;
  凡分派/取参在 `session`/`csrf` 校验之前者即为免认证面,应优先审计该表内**所有** action(而非只看已披露字段)。
  注意区分两张表: 免认证 action 表 vs 认证后 (page,action) 表。
- 产物: 免认证 handler 反编译汇总 + 分派表 | (来源标签: E8450 轮 2026-09-14;该目标已归档)

## 由**请求内偏移之差**算出的 memcpy 长度无校验 → 整数下溢/越界(global 溢出)
- 目标: NETGEAR R6700v3 V1.0.5.128 `usr/sbin/upnpd` 的 `sa_method_check`(IDA `sub_44474`,0x44474)
- 实证: SOAP 处理里用请求缓冲 `a1` 内的三个指针构造 service-type 串:
  `v26 = stristr(stristr(a1,"urn:")+4,":")`,`v27 = stristr(a1, service_name)`,`v28 = strlen(service_name)`,
  然后 `memcpy(&sa_service_type_buf[18], v26, (v27+v28) - v26)`,`sa_service_type_buf` 是 .bss 全局 100B。
  长度**只依赖请求内 token 的相对位置**,无 `v27>=v26` 或上界校验。
  - 把 service 名放在第一个 `urn:` 之前 → `v27<v26` → 长度负,以 `size_t` 传入 → ~4GB memcpy → SIGSEGV;
  - 在 `urn:` 与真实 SOAPAction 之间塞 padding → 正长度越界,越过 100B 覆盖相邻全局 `uuid_str`。
  gdb 现场: `#0 memcpy(dest=0x4aa242 <sa_service_type_buf+18>)`,`#1 0x444888`;`parse_signal`=HIT:crash。
- 免认证面: `POST /soap/server_sa/...`(TCP 5000),仅 `...service:DeviceConfig:1#SOAPLogin` /
  `...ParentalControl:1#Authenticate` 两个 action 跳过 opendns 认证直达该路径(网络相邻即可)。
- 复用场景: 审 SOAP/XML/URL 解析时,凡 `memcpy/sprintf/strncpy` 的长度写成"两个请求内指针相减"
  (`end - start`、`p2 + len - p1`)而无 `if (start <= end)` 与目标缓冲上界检查者即候选;
  这类长度可由攻击者摆布 token 顺序/间距,既能正向溢出、也能**下溢成巨值**。对照: 同版本对**栈上**
  的 `strncpy(&v83,v9,v12)` 已加 `v12>126→127` clamp,但**全局 memcpy 漏了** → 不完整修复的典型。
- 产物: `R6700v3/report/upnpd-soap-servicetype-overflow/`(PoC+gdb 现场) | (2026-09-14/R6700v3 实证)

## 固定栈缓冲 + "长度取自输入长度"的无界 `memcpy`(URL/文件名入包构造器)
- 目标: MikroTik RouterOS 7.24.1 arm64 `nova/bin/mtget` TFTP RRQ builder(函数入口 0x16a28)
- 实证: 请求路径串 `{u32 len; char data[]}` 被 `<ldr r5,[r1],#4>` 取长度后直接 `<bl memcpy>`:
  `memcpy(sp+0x16, path, path_len)`,帧仅 0x21c、缓冲起于 sp+0x14 → path>~520 即覆盖 saved r4-r8/sb/lr。
  path=600 实测 SIGSEGV `si_addr=0x41414140`(=注入字节),对照 path=400/520 正常返回。
- 关键点: **这类"构包函数"往往在收到任何对端响应之前就把攻击者可控的长串写进固定栈缓冲**
  (RRQ/WRQ、SNMP get、DHCP option、SSDP NOTIFY 都常见),所以"没有可连的对端"不构成不可达性辩护。
- 复用场景: 审 fetch/客户端/构包类代码时,凡看到 `len = <obj>->len` 紧跟 `memcpy(stackbuf, obj->data, len)`
  而无 `len <= sizeof(stackbuf) - off` 判断者即候选。对照修复形态:同项目 7.24.2 改为
  `append(&s,&maxlen,src,len)` helper(`if (*maxlen < len) return 0;`)+ 显式余量预算。
- 识别修复是否完整的一招:比较**同一功能在相邻两个版本**里的调用序列 —— 7.24.1 `bl memcpy` vs
  7.24.2 `bl <bounds-checked helper>`;若只有部分调用点换掉(如全局 memcpy 换了、栈上 strncpy 没换),
  即"不完整修复",应继续追。
- 产物: `RouterOS-7.24.2-arm64/report/mtget-tftp-rrq-stack-buffer-overflow.md` + `report/poc/mtget-tftp-rrq-overflow/` | (2026-09-15/RouterOS 实测)

## 认证速率限制缺失(CWE-307):"每连接固定延迟" + 过宽的并发上限 = 可并发绕过
- 目标: MikroTik RouterOS 7.24.2 `nova/bin/user`(CVE-2026-16347)
- 实证: 7.24.2 的失败登录延迟是**每连接**的固定延迟;并发登录上限为
  `list_base::count_nodes(this+0x11C) > 0x63`(99,`sub_1CE9C`),且 `UserContainer::cmdLogin`
  (`sub_1E8C4`)路径**无并发上限检查**。攻击者开 N 个并发会话即可把有效猜测速率放大约 N 倍。
  CISA icsa-26-209-05 原文:"a fixed per-connection delay is present, but it can be bypassed through
  concurrent sessions";CVSS3.1 8.8 `AV:A/.../PR:N`。7.24.3 将上限降到 10 并累加排队数、新增全局失败计数。
- 复用场景: 审认证服务时,凡"失败延迟/锁定"状态挂在**连接对象/会话对象**上(而非按来源 IP/账号的全局表),
  且并发上限宽松或缺失,即候选 CWE-307。定位法:`grep` 反汇编中 `count_nodes`、`addTimer`、
  以及失败分支里的计数器 `LDR/ADD #1/STR`;对照厂商是否在**后续版本**收紧上限。
- **边界(2026-09-25/Synology DSM 7.3.2-86009 实测)**: 不要把“有并发丢失更新”直接当成“无限绕过”。
  若限额状态是**全局共享文件/内存计数器**、读改写无锁,但**同一代码路径另有一把锁把“验证”串行化**
  (例: `EncVolume::UnlockModeEnterPasswd` 的计数 RMW 无锁,但 `SYNOEncVolumeVaultVerifyPasswd` 持 `/run/encvol/vault.lock`),
  则放大因子被锁的临界区压成 **≈(锁外更新窗口)/(单次验证时长)**,通常只有 ~2×,不随并发数增长。
  判法: 先量“cap 触发前的验证次数”(可用 inotify 数锁文件 `IN_OPEN`,顺序基线应与 cap 阈值 1:1),再看它是否随 N 增长;
  有界放大按 `misreports.md` 反例 1 四问处置(本例全否 ⇒ 不报,只记加固)。
- 产物: `RouterOS-7.24.2-arm64/notes.md` §3/§6 | (2026-09-17/RouterOS 实证)


## 修法只加“主会话已认证”门,未给派生连接自身身份/来源绑定 —— 目标: MikroTik RouterOS 7.24.4 `nova/bin/btest`(`sub_1DC44` @0x1dc44,`sub_185A4` @0x185a4)
- 形态: 一个会话被拆成「一条已认证控制连接 + N 条派生数据连接」;派生连接只携带一个
  capability(=控制连接下发的小整数),服务端校验该整数对应的**会话对象**是否已认证,
  但**不认证派生连接本身、也不比对它与控制连接的来源地址**。
- 实证: related 帧 = `u32 flags = 0x00020000 | key` + 12 个被忽略的 0 字节;准入只读
  `test+5`(控制会话认证过)与 `test+6`(related 已消费)。key = `test id >> 8` = accept 时
  `nv::Allocator::allocate` 的索引(每接受一个 TCP/2000 连接 +1,可外部预测)。
  `authenticate=yes` 默认配置下未认证第三方从另一源地址接入并收到 61,256 B 会话数据流。
- 为什么容易漏: 上游 CVE(本例 CVE-2026-67277)修的是“派生连接早于主会话认证”,
  修完就把该面标成 No finding;真正缺的是**派生连接的身份/来源绑定**这条正交属性。
- 复用场景: 任何多连接会话协议把数据连接当 capability 传(带宽测试、FTP 数据连接、
  SIP/RTP、RTSP、多流 QUIC/MPQUIC、监控 NVR 拉流)。
- 审计要点: ①派生连接的分派键是不是可预测/可枚举(计数器、时间戳、会话 id 派生);
  ②准入判定里有没有 `getpeername`/地址比对;③把既有 CVE 的修法**逐条**当成“只覆盖了一半”复核。
- 产物: `RouterOS-7.24.4-arm64/report/btest-related-connection-unauthenticated-access.md`;
  证据 `RouterOS-7.24.4-arm64/artifacts/poc/btest-related-connection-evidence.md`。(2026-09-19)

## nginx `auth_request` 的目标 location 在某 server 块中缺失 → 被 catch-all 以 2xx 捕获 ⇒ 认证形同虚设
- 目标: Ubiquiti UniFi OS Server 5.1.42(容器镜像 `uosserver:c9603dec9010`)`unifi-core` 生成的 nginx 配置
- 实证: setup 态 443 server(`user/share/unifi-core/http/site-setup.uosserver.conf`)经 `shared-server-defaults.conf`
  include 了 `shared-runnable-*.conf`(其中 `/proxy/users/`、`/proxy/uid-agent/` include `auth.conf`
  = `auth_request /internal/auth/public-api`),但**未** include 定义该 `internal` location 的
  `shared-post-setup-server.conf`(只被 post-setup 站点 include)。无匹配的 `internal` location ⇒ 认证子请求落到
  setup UI 的 catch-all `location /`(try_files→静态)⇒ `GET /internal/auth/public-api` 返回 **200 text/html**
  ⇒ nginx 视 2xx = 已授权。宿主机经发布口**无凭据**可达 ulp-go:枚举用户(200)、`POST /api/v2/user` 建号、
  `PUT /api/v2/user/:id/role {"role_id":<super_administrator>}` 赋内置 Super Admin;`/proxy/uid-agent/*` 同机制。
  同一容器切到 post-setup 模板后同请求 = 401(`/internal/auth/public-api`→404)。
- 判据(可证伪/可复用): ①找出所有 `auth_request` 指令的 URI;②**在每个 server 块**外部请求该 URI —— 若返回该 server
  的 catch-all 内容(而非 404/401 等内部错误),则该 server 下所有 auth_request 皆被 2xx 放行;③做 setup vs post-setup
  差分配置,排除"设计内不鉴权"。⚠️ 不要只看"存在 auth_request 指令"就判受保护。
- 复用场景: 任何用 nginx `auth_request` 做子请求鉴权、且**同一批 location 片段被多个 server 块 include** 的固件/产品
  (TLS 站点 vs 设置/管理站点、明文 vs SNI 分站);`internal` location 只在其中一个 server 定义是常见漏配。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/report/setup-mode-auth-request-bypass.md` +
  `artifacts/recon/round16-setup-auth-bypass.txt` | (2026-09-21/UOSServer VR-1)

## 用户更新端点可无旧口令改写任意用户(含 owner)的口令 ⇒ 最短 ATO 链
- 目标: Ubiquiti UniFi OS Server 5.1.42 ulp-go `PUT /api/v2/user/<id>`(经 setup 态 `/proxy/users/*` 无凭据可达)
- 实证: 无 cookie / 无 API key / 无客户端证书请求 `PUT /proxy/users/api/v2/user/<owner_id>` body `{"password":"OnlyPass456!"}`
  → HTTP 200 `{"code":1,...,"data":{...,"username":"owner","local_account_exist":true}}`;从容器内用 trusthost-only 端点
  `GET /api/v2/user/<id>/credential` 读到口令已变(`OnlyPass456!`,原 `OwnerPass123!`),且 `POST /api/v2/login_v2`
  用新口令返回 `code:1`。同一 PUT 也用于给新建账号设 `username`+`password`(无需旧口令)。post-setup 站点同请求 401。
- 判据(可复用的审计动作): 一旦拿到任何"未授权可达 / 越权可达"的用户管理面,**先把 `PUT|PATCH|POST /user/<id>`
  带 `{"password":...}`(或 `local_credentials`/`credential`/`reset_password` 变体)打一遍** —— 很多实现把
  改口令当"普通字段更新",不要求调用者已认证、也不要求提供旧口令,于是一步就是账号接管(比"建号再赋权"更短更硬)。
  负对照: 若改口令需 old-password/已认证 actor(403/400),则该形态不成立。
- 复用场景: 任何转发到身份/用户后端的管理面(nginx/反代鉴权缺口、IDOR、内部信任面);判定"影响"时优先找这类
  **凭证写入**原语,而不是停在"能建号/能列用户"。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round18-owner-ato-preauth.txt` +
  advisory `report/setup-mode-auth-request-bypass.md`(Impact) | (2026-09-22/UOSServer VR-1 影响深化)

## 未配置态(setup/onboarding)的预认证面:设计必须开放少数初始化端点,缺陷在"暴露越界" | (2026-09-22 Ubiquiti UniFi OS Server 5.1.42;nginx `auth_request` 子请求被 catch-all 捕获)
**形态**: 设备/服务第一次部署、尚无任何凭据时,**必须**提供未认证的初始化入口(设 owner、选区域、配网络),所以
"未配置态有预认证端点"本身**不是**缺陷;缺陷出现在**该态下暴露的比初始化所需更多**。
**本次实例**: setup 站点的 `auth_request /internal/auth/public-api` 指向**本 server 块里不存在**的 `internal` location
⇒ 子请求落到 setup UI 的 catch-all(`location /`)⇒ 得 200 ⇒ nginx 判"已授权" ⇒ 该站点内 `/proxy/users/*`、
`/proxy/uid-agent/*`(身份目录全功能)**预认证可用**。设计意图 = 少数初始化端点;实际 = 整站身份 API。
**报前必查(两条 mirror)**:
1. **完成后是否仍可重跑 setup?** 若"已配置实例仍能重跑初始化/重建 owner"⇒ 严重度上一个台阶
   (先例 CVE-2007-6619 Jira *"does not properly restrict setup attempts after setup is complete"*)。本目标**有**护栏:
   setup handler 首行 `if (<isSetup>) throw ALREADY_SETUP`(httpCode 401)⇒ 只能写"已配置后受护栏保护"。
2. **未认证方能否把已配置实例推回未配置态?**(复位端点是否预认证、是否需权限位)本目标: `POST /system/reset`
   带权限位中间件(`Se(128)`)且不在预认证白名单 ⇒ **不能**。
**严重度先例(NVD)**: CVE-2021-44736 **9.8** Lexmark(初始 admin setup wizard 预认证);CVE-2019-13278/13279 **9.8**、
CVE-2019-13277 **7.5** TRENDnet(setup wizard 预认证)。
**表述口径**: "启用初始化入口是设计必需;缺陷是把**初始化态之外的接口**也置于未认证" —— 该口径同时解释
为什么不是"设计内"(超出设计意图)以及为什么前提是"目标处于未配置窗口"。


## 内部集成端口只靠"网络隔离"、不做认证 ⇒ 未认证可达的强操作端点 | (2026-09-22 Ubiquiti UniFi Network 10.5.67,随 UOS Server 5.1.42 捆绑)
- **形态**: 产品把给**宿主/同机组件**用的"内部 API"放在独立端口上,安全模型 = 只做**端口→URI 允许表**
  (`filter` 按 `getLocalPort()` 选正则/前缀,**不查任何身份**;TLS 也**不请求客户端证书**)。调用方(宿主侧)自己
  **不带凭据** ⇒ 设计假设是"外部到不了这个端口"。一旦**安装器/portmap 把它发布到 0.0.0.0**,假设即失效。
- **实例**: UniFi Network(捆绑于 UniFi OS Server 5.1.42)`POST /api/ucore/support-dump` @ **28082**
  (`UbiosHttpsFilter`: 28082 仅放行 `/v2/upload/` 与 `/api/ucore/support-dump`;`portmap.json` = `0.0.0.0:28082`)。
  未认证 `POST {"dir":"<path>"}` → 200:**先递归清空 `<dir>` 内容,再**以服务身份写支持包(设置/拓扑/日志/thread-dump);
  `dir` 指向应用数据目录时**清空本地数据/密钥/备份与实时日志**;边界 = 服务账号写权限(root 属主目录不受影响;不可写时仍回 200);认证路径同请求 401(负对照)。
- **认得出**: ①同一服务有多个监听端口,其中若干只在 `filter` 里做 URI/端口白名单(无 `auth_request`/无 token/无 mTLS);
  ②这些端点的调用方在本机(`127.0.0.1`/同容器)且请求里**没有** Authorization/Cookie/证书;
  ③安装器/清单(`portmap.json`、`EXPOSE`、`--publish`)把它们声明为对外发布。
- **验证(最小)**: 从**非本地源**直接打该端口的一个白名单 URI → 若返回非 401/403 且**副作用可观测**(落盘/状态改变),
  再拿同一 URI 走**认证路径**(反代/主端口)做负对照(应为 401) ⇒ 缺口成立。
- **别与"信任本地头/loopback"混为一谈**(那条是"已有本地代码执行后"的观察,不可报);本条只要端口**对外可达**就是网络缺陷。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round22-network-app-support-dump-unauth.txt`、notes §40。
- **可达性(2026-09-22 定)**: 该端口由**厂商文档**确认为对外入口 —— Ubiquiti《Required Ports Reference》UniFi Network 段:
  `TCP 28082 | Ingress | Device packet capture and support file downloads`。
  注: 先前"portmap 声明 + pasta 默认转发 ⇒ 发布"的**机制推断**已被 podman 实测证伪(`-t none` 覆盖默认,见 notes §40.9);
  结论改由厂商文档支撑(判据见 methodology「先查厂商端口参考文档」)。

## 同族"设备上传"端点里,某一个漏了兄弟们的校验 ⇒ 未认证内容注入 | (2026-09-22 Ubiquiti UniFi Network 10.5.67,随 UOS Server 5.1.42 捆绑)
- **形态**: 一个服务把**同一类操作**(设备上传/心跳/上报)拆成若干个 handler,挂在**同一个无鉴权的内部通道**上
  (端口→URI 白名单,见「内部集成端口只靠网络隔离」条)。其中多数 handler 至少有一道**业务前提校验**
  (设备已 adopt、存在进行中的任务、一次性 token);**只要有一个 handler 只校验参数格式**(如 MAC/ID 语法),
  攻击者即可用**该 handler** 做未认证写/改——它绕过了同族共有的前提。
- **实例**: UniFi Network `/v2/upload/*` dispatcher(28082)恰好 3 个 controller:
  `pcap-upload` → 查设备(`api.err.UnknownDevice`);`support/upload` → 查"该 MAC 生成中"缓存 + 查设备;
  `proav/snapshot/upload` → **只查 MAC 语法**,随后 `Files.copy(..., <dir>/<MAC>-snapshot.jpg, REPLACE_EXISTING)`。
  配置态未认证 POST → 200 + 以服务账号落盘任意内容(≤100MiB/请求,可覆写)。
- **认得出**: ①同一 dispatcher/路由前缀下有 ≥2 个功能相近的 handler;②其中一个的判定链比兄弟短
  (没有 `devmgr.find`/`getIfPresent`/token 比对);③该 handler 的入参里有**可控内容**(multipart body)。
- **验证(最小)**: 对**同族每个 handler** 各发一次未认证请求,记录"是否到达 sink + 是否落盘/改状态";
  兄弟里失败的那个(400 UnknownDevice / 无写)正好反证"前提校验是本设计的一部分",缺它的那个就是缺口。
  注意: **仅看 HTTP 200 不够**——本实例 `support/upload` 也回 200 但**不落盘**(guard 内 warn+return),必须做落盘差分。
- **边界(配证据一起写)**: 本实例路径由校验后的 MAC 派生(`<12HEX>-snapshot.jpg`),**无穿越**;
  影响=完整性(可覆写任意 MAC 的快照)+ 可用性(磁盘耗尽,厂商/赏金普遍不单列 DoS)⇒ 按此如实定级,不夸大。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round24-network-app-proav-snapshot-upload.txt`(§5/§7/§8),notes §42。

## 可信中继把**客户端可控头**再注入到 loopback 请求 → 客户端来源 IP 欺骗(ACL 绕过) | (2026-09-24/Synology DS223 DSM 7.2.2 实测)
- 目标: Synology DSM 7.2.2-72806 QuickConnect WebRTC 中继(`SYNO.WebRTC.Proxy.Sharing` + Go 守护 `webrtcproxy`)
- 实证: 预认证 API(`authLevel:0`、`skipSharingAppCheck:true`)的 handler 首步 `SYNO::SetCommonParam` 把
  **客户端 HTTP 头**写入待转发 JSON(`forwardedFor ← HTTP_X_FORWARDED_FOR`、`host`/`cookie`/`origin`/`referer` 同理);
  JSON 经 `/tmp/webrtcproxy.sock` 交给 `webrtcproxy`;后者 `main.SetHTTPRequestHeaders` 把它写成出站请求的
  **`X-Forwarded-For`**,而目标 URL 由 `main.(*HTTPProxyContext).GetTargetURL` 固定为 `http://127.0.0.1:<DSM 端口><path>`;
  DSM nginx `set_real_ip_from 127.0.0.1` ⇒ **信任该头**,`$remote_addr` 变为攻击者指定的 IP。
  (动态已证: API 预认证可达且 `sid` 不校验;转发 JSON 里 `forwardedFor` 即攻击者头值。)
- 复用场景: 审**中继/代理/网关**时,凡"下游把某来源标识(IP/Host/角色头)当作可信,而上游组件会把它从
  **客户端可置入的位置**取出再发一遍"者即候选。判据: ①客户端能否置值(可达性)②该值是否被再注入到
  **受信来源**(loopback/内网)发起的请求 ③下游是否信任那个受信来源 —— 三者分开证。
  与 `misreports.md`「上游信任身份头」的区别: 那条只要求"上游信任头≠可网络利用";本条额外要求**存在一条客户端置值路径**。
- 窗口: 需特性启用(QuickConnect)+ 分享链路 ⇒ 窗口级,排序降级但仍可报。
- 产物: `Synology-DS223-7.2.2-72806/report/quickconnect-webrtc-xff-spoof.md` + `report/attachments/quickconnect-webrtc-xff/`。

## 按**唯一 id/hash 取对象**的管理类 API 漏 owner 过滤(share-link 编辑/删除) —— 同一 API 内 list 有过滤、delete/edit 无 | (2026-09-26 Synology DSM 7.3.2-86009 动态确证)
**形态**: 文件/分享管理 API 用不可枚举/半可枚举的 id(如 share-link hash)定位对象;`X(id)` handler 直接
`GetByHash(id) → Delete/Update`,**不比较对象 owner 与调用者**,也不复用同组 `list` 用的 owner/scope 过滤。
`list` 只返回自己的对象 ⇒ 看似"安全",但 `getinfo/edit/delete` 仍接受任意 id。
**判据(报之前)**: ①目标对象是否有 owner 字段且 handler 是否比较;②id 攻击者能否从正常渠道获得
(分享链接的 URL 路径、`list_share_me`、二维码等)—— **能获得 ≠ 可管理**;管理操作应与"访问能力"分离;
③UI 是否把 edit/delete 只给 owner(recipient 面板只有 view)⇒ UI/API 语义不一致是强证据;
④受限主体真实存在(低权用户对该资源无访问权,实测 407)且管理动作确实改变状态(owner 侧 `getinfo` 可观察)。
**反例边界**: 仅"知道哈希就能查看/下载"是 capability 模型的正常语义,**不算**;必须落到"管理/破坏他人对象"。
**证据**: `Synology-DS223-7.3.2-86009/notes.md` §26;`artifacts/handler-sink/evidence_sharing_idor*.txt`。

## Synology webapi handler 的命令注入: `system()` 里 `'%s'` 单引号包裹 + **未**用 `SLIBCEncShParam` | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实证)
- **形态**: 厂商 webapi 的 `.lib` handler 常把请求参数拼进 shell 命令再用 `system()`/`popen()` 执行。
  安全写法是先把值过 **`SLIBCEncShParam`**(shell 参数转义,如 `webapi_file.so` 的 `php ... %s --from %s`);
  不安全写法是**直接 `snprintf("... '%s' ...", value)`** —— 单引号只挡空格不挡 `'`,值里一个 `'` 即可闭合引号后注入。
- **实证**: `lib/SYNO.Core.Certificate.so::SSLCAFormHandler::ProcessJsonReq` 把 `GetParam("cert_tmp"/"key_tmp"/"inter_cert_tmp")`
  填进 `"/bin/cp '%s' '%s'"` → `system()`;`cert_tmp=/tmp/x';touch PROOF;'y` 触发执行(strace 见 `execve("/bin/sh",{"sh","-c","/bin/cp '/tmp/x';touch PROOF;'y' ...})`)。
- **快速筛子(成本低、判据硬)**: `nm -D <lib> | grep -c SLIBCEncShParam` == 0 且 `grep -c system\|popen` > 0 ⇒ 候选;
  再反编译 system 前的 `snprintf` 格式串,看 `%s` 是 `GetParam`/`In` 值还是内部常量/设备结构。
- **前置常见**: 注入值往往必须是**存在且过校验的路径/对象**(如合法证书、已存在的打印机),所以要配合一个能落地的对象。
- 同类候选(未定论): `SYNO.Core.ExternalDevice.Printer`(`synoprint --npenable '%s'`,参数 `printer_id`)。

## Synology `libsynocgi::SynoCgiGetRemoteIP` **信任 `X-Forwarded-For`** ⇒ 预认证客户端 IP 欺骗 | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实证)
- **形态**: `lib/libsynocgi.so.5` 取源 IP 顺序 = `getenv("HTTP_X_FORWARDED_FOR")` → `getenv("REMOTE_ADDR")` → `getenv("HTTP_CLIENT_IP")`。
  `X-Forwarded-For` 是客户端可自设头,而 Synology httpd(`httpd.conf-{sys,user}`、Safe Access block-page vhost)**无 `RequestHeader`/`RemoteIP`,不 strip**。
  ⇒ 任意客户端可决定 CGI 眼中的源 IP。**这是 libsynocgi 全局行为**,凡调 `SynoCgiGetRemoteIP` 的 CGI 全中招。
- **实证**: `auth.cgi` + 插桩 `synoautoblock`:REMOTE_ADDR=127.0.0.1 + `X-Forwarded-For: 203.0.113.77` ⇒ `synoautoblock --deny 203.0.113.77`;每次换 XFF ⇒ 限流目标每次变。
- **影响面(按 CGI 分)**: ①autoblock 限流(`synoautoblock --deny/--in-white-list`)⇒ 登录/口令重置无限枚举(放大可预测重置票面类缺陷);②栽赃第三方 IP 入 deny;
  ③Safe Access 预认证拦截页(`accessanyway.cgi`/`request.cgi`)的 allow-bypass/bypass 次数/`libsynoipblock` ⇒ 绕家长控制。
- **判据/筛子**: 在 Synology 固件里搜 `HTTP_X_FORWARDED_FOR` / `SynoCgiGetRemoteIP`;确认 httpd 配置未 strip XFF;用"插桩本地 CLI(如 synoautoblock)+ 变 XFF"做 L1 oracle。

## 「前端把客户端可控头(如 `X-Forwarded-For`)写进 CGI 的 `REMOTE_ADDR`」—— 预认证源 IP 欺骗 | (2026-09-28 QNAP TS-233 QTS 5.2.10)
- **实例 2(上一条的 QNAP 分支;判据差在"入口")**: Synology 那条的入口是 **CGI 库读 `HTTP_X_FORWARDED_FOR`**(前端只是"不 strip");
  QNAP 这条的入口更靠前 —— **Web 服务器自己**在 CGI env 里把 `REMOTE_ADDR` 改成请求头 `X-Forwarded-For` 的值:
  `_thttpd_` `sub_12138` @`0x13058` 解析 `X-Forwarded-For:` 存入连接结构 `+0x274`;`sub_16908` @`0x171dc`
  `if (*(char*)(hc+0x274)) "REMOTE_ADDR=%s" ← hc+0x274; else ← socket peer`。
  ⇒ **凡从 `REMOTE_ADDR` 取源 IP 的 CGI/库全部中招**(本目标:`libuLinux_cgi::CGI_Get_Http_Info`、`libuLinux_nasauth::__get_remote_ip`
  → `auth_add_session(_ex)` 把该值写进会话记录;`authLogin.cgi` 同请求还喂 `SendConnToLogEngineEx4` 连接日志)。
- **判据(硬,跨厂商复用)**: 别只搜 `HTTP_X_FORWARDED_FOR` —— **前端自己重写 `REMOTE_ADDR`** 是另一条路且更彻底。
  查法:在 Web 服务器二进制里搜 `X-Forwarded-For`/`X-Forwarded-Host` 字符串 → 找到解析点与 env 构造点 →
  看 env 构造是取"请求头字段"还是"socket peer";**再用 qemu-user 直接跑该前端二进制**做 L1 oracle
  (自建一句话 CGI 回显 `$REMOTE_ADDR`,同请求发/不发头对照)。
- **影响面(按 QNAP 实测)**: 伪造审计源 IP(会话记录/登录历史/连接日志,含栽赃第三方),以及任何**用户态**按源 IP 做的判定;
  **边界**: 内核态过滤(qShield / IPSEC deny 规则)按真实报文源 IP 生效 ⇒ 不因此绕过包过滤(别夸大)。
- **"能伪造"不是结论,必须追到判定点(本次的可复用做法)**: 源 IP 在 QNAP 有**一条完整的消费链**,按 IPC 报文逐跳对齐全在代码层可证:
  `CGI_Get_Http_Info(+376)` → `SendConnToLogEngine(...,a3=IP,...)`(记录里 user@0xCE0 / **IP@0xD21**,队列 key 1002)
  → `qLogEngined`(结果码 9/10 时 `add x0,x0,0xd21; b SendMsgToBand`) → `libuLinux_ban::SendMsgToBand`(`msgget(1102)`)
  → **`qShield`**(`msgget(0x44e)`@0x405fe0)读同一队列 ⇒ 维护 `/run/ban.txt` + `/sbin/ip_filter`(IPSEC deny)。
  ⇒ 后果 = ①攻击者自己**永不进 ban**(每次换头 ⇒ 计数不累积)②可让设备**封任意第三方 IP**(栽赃/锁死管理员自己)
  ③审计源 IP 全伪造。**本条已端到端 L1 跑通到"决定"这一步**(7 条伪造来源的认证失败后 qShield 日志:
  `[Http  ] ip[198.51.100.9], account[admin]` → 计数累积 → **`Ban it => IP[198.51.100.9]!`**),
  只剩"装载规则"未观测(它通过 shell 调 `/sbin/ip_filter`,宿主没有该程序)。**判据(可复用)**: 在发送端反汇编找 `snprintf(记录+OFF, "%s", <IP>)`,`OFF` 就是接收端要读的偏移;
  然后在**宿主**用 `msgget(相同 key)` + `msgrcv` 把报文读出来,直接看该偏移是否等于自己发过去的伪造值 ——
  这是"链路两端都钉住"的 L1/L3 双证据,比只读一边代码强得多。
  ⚠️ qemu-user 下 SysV IPC 与宿主同一命名空间 ⇒ 宿主能直接读 guest 发出的报文(本目标实测有效)。
- **旁证(同目标、非利用)**: `libuLinux_cgi::CGI_Get_Http_Info` 的 `HTTP_X_FORWARDED_FOR` 分支**不做地址格式校验**,
  而 `REMOTE_ADDR` 分支做(`IS_IPv4_Address`/`IS_IPv6_Address`)—— 即"两种来源一严一松",是同类缺陷的第二个落点。
- 来源: `QNAP-TS-233-QTS-5.2.x` `2026-09-28`(advisory 与截图见该目标 `report/`)。

## 用“另一个文件的 mode 位”代理设备状态 → 该状态的命令面整体免鉴权 | 目标: QNAP TS-464 QTS 5.2.10.3577 `home/httpd/cgi-bin/qid/qlicenseRequest.cgi` | 实证: 见下 | 复用场景 | 产物: QNAP-TS-464 `report/qlicense-unauthenticated-license-management.md` | (2026-10-03)
- **机制**: CGI 的 `cmd` 分派门是 `qcloud_license_internal_is_nas_init_done()`(`libqlicense.so`),而它只
  `stat("/home/httpd/cgi-bin/quick/quick.cgi")` 并对 owner-x 位取反 ⇒ 设备处于 setup 态(该文件可执行)时**直接跳到命令分派**,`auth_get_session` 不执行。
- **判据/复用场景**: 审鉴权时先看"门的输入是什么"。若门的输入不是会话/凭据,而是**另一文件的存在/模式位**或另一个组件的状态,
  该门只能区分**状态**、不能区分**命令** ⇒ 必须把"放开"那一支到分派的**完整命令表**逐条判副作用(常见形态是整表免鉴权,不是单点)。
- **实证(L1)**: 无会话 `cmd=get_installed_license_list` / `get_public_key` 返回数据;无会话 `set_proxy` 写
  `/etc/config/qlicense/qlicense.conf`(`URL = ,attacker.example.com,8080`);负对照(`quick.cgi` 444)→ `auth get session fail`。
- **整表判定(2026-10-04 复核,TS-464 x86_64)**: 无会话把**全部分派命令逐条**发一遍(41 条)⇒ 门开时**无一条**返回鉴权错;
  分三类:①返回数据/生成物(`get_installed_license_list`、`get_public_key`、`get_proxy`、`generate_dif`(**设备签名 DIF**)/`generate_encoded_dif`、`get_web_host`、`check_network`);
  ②**动作执行**(`floating_refresh`、`online_recovery`、`online_check_license`、`get_log_files`(触发 debug 归档脚本并下载));
  ③**状态更改**——`set_proxy`(持久化代理配置);`delete_license`/`activate_trial`/`online_*`/`offline_activate_license`(含**无会话 256KB 文件上传**)等**handler 已执行**,
  只因缺参数/数据/网络而失败(不是因缺鉴权)。配置态(`quick.cgi` 444)同请求**全部**返回 `auth get session fail`。
  ⇒ 判据:**门是非凭据输入时,不能只抽查几个命令就收口——整表逐条判,否则低估影响面**。
- **取证提示**: 状态由文件模式位决定时直接 `chmod` 换态即可复现,但报告须说明真机上由哪个 boot 脚本设置该模式(否则像自造条件)。
- 实例(2026-10-20,QTS 5.2.11.3652):`libqlicense.so` 与 `qlicenseRequest.cgi` 与 5.2.10 **逐字节相同** ⇒ 漏洞在新版**仍在** ✓;而同一次更新里 `reset_password.cgi` 已重建、userinfo 形态由 `-1` 变 `-2` ⇒ 该条已修 ✗ ⇒ 同一次发布要**逐条**核,不能按整包推断。

## URL host 校验在首个 `:` 处截断、却把原始串回填进邮件/跳转 ⇒ `http://<allowed>:<port>@attacker/` 绕过 | 目标: QNAP TS-464 QTS 5.2.10.3577 `home/httpd/cgi-bin/reset_password.cgi` | 实证: 见下 | (2026-10-04)
- **机制**: 预认证 `func=send_mail` 接收客户端 `url`,提取 host 的方式是「从 `://` 后扫到首个 `:`/`#`/`/`」再与 `localhost`/server name/DDNS 允许表比对;
  但写邮件时用**原串** `snprintf("%s&u=%s&rp=%s", url, user, token)` 作为 `<a href>` ⇒ 校验器把 `localhost:80@evil` 读成 host=localhost(过),
  浏览器把 authority 解成 `evil`(userinfo)。同一字符串、两个解析器。
- **判据/复用场景**: 凡「校验一个 URL 的 host 后又回填/跳转到原始串」的实现,检查是否 (a) 用正规 URL parser 取**有效 authority**、
  (b) 拒绝 authority 含 `@`/`\`/末尾点、(c) 用**校验后的 host 重建**链接而非回填原文。自写「找首个冒号」的 host 提取 = 红旗。
- **实证(L1)**: 无会话三 URL 差分 —— `url=http://evil.example/...` ⇒ `result=-2`(host 拒);`url=http://localhost/...` ⇒ `-1`(过);
  `url=http://localhost:80@evil.example/...` ⇒ `-1`(**绕过**,回 `is_smtp_en=0`+`-1`,即已过 host 门);`python3 -c "urlsplit(...).hostname"` ⇒ `evil.example`。
- **取证提示**: 前置(设备已配 SMTP + 目标账户有邮箱)只影响"邮件是否真的发出";**绕过本身与前置无关**,差分即可证。

## 厂商固件内嵌 Python 服务用 `xml.dom.minidom.parseString` 解析**未认证**输入 ⇒ 实体膨胀 DoS | 目标: QNAP TS-464 QTS 5.2.10.3577 WS-Discovery(`wsd.py`,UDP/3702) | (2026-10-04)
- **机制**: 内嵌 CPython(此处 2.7.18,厂商从 QPKG 装)的 `minidom.parseString` 经 expat 递归展开内部实体、无上限;一个带 `<!DOCTYPE>` 嵌套实体的**小报文**即让解析函数长时间不返回并持续吃内存。
- **判据/复用场景**: 审固件里所有「内嵌脚本语言 + 解析外来数据」的服务(Python `minidom`/`lxml`/正则、Perl、Node、Lua),先看解析调用是否在**未认证入口的内联路径**上、以及解析器是否禁用了 DTD/外部实体。
  固件自带解释器 = 同时也带其默认行为(默认不安全)⇒ 不能假设"上游库会保护"。
- **实证(L1)**: QNAP TS-464 `WSDiscovery.py::parseEnvelope`(被 `_recvMessages` 单线程内联调):control 普通 Probe 443 B → 0.001 s;436 B 实体报文 → 15 s 未返回(bundled CPython 2.7.18)。可达性:`init_final.sh:310` 每 boot 装 `Python.tgz`;`rcS.d/S89wsd`→`wsd.sh` 在 `Samba Enable=TRUE`(出厂 TRUE)且 `System WSD Enable`(默认 1)时启动。
- **修复**: `defusedxml.minidom.parseString` 或先拒收含 `<!DOCTYPE`/`<!ENTITY` 的报文 + 限制解析时长/内存。

## 字符串型载荷(JSON/表单)限制"不能含 NUL"的栈溢出:链不可叠,但**低位部分覆盖即足以任意跳转** | 目标: Linksys E8450(2026-10-04)
- **机制**: 保存的返回地址在固定偏移处,且其高字节本就是 `0x00`;`strcpy` 类拷贝又受"载荷不能含 `\x00`"约束 ⇒ 写不出完整 8 字节指针,但能把**低 2–3 字节**改成任意非零值 ⇒ 落点 = 代码段内任意地址(非 PIE 固件上尤为完整)。
- **判据/复用场景**: 凡"字符串型溢出 + 非 PIE":先算固定偏移(帧布局),再"填充 + 目标地址低 3 字节"实测 `PC`;⇒ **不要**因为"不能写 0 字节 / 不能叠 ROP 链"就把结论降级成仅 DoS ✗(这是本工作区踩过的表述错误)。
- **实证**: `A`×136 + `90 1f 40` ⇒ 执行流精确落 `0x401f90`;换 `a8 48 41` ⇒ 落 `0x4148a8`;`A`×136 + `BBBBBBBB` ⇒ `PC`=`x30`=`0x4242424242424242`、`x29`=`0x4141414141414141`;更长载荷还能控制保存的 `x19`–`x23`(来源: E8450 `portal.cgi` 轮)。
- **配套**: 部分覆盖只解决"跳到哪";"能执行什么"取决于参数控制(见 methodology 的"无参执行点"条)。

## 预认证控制流劫持 ⇒ 直接触发**认证后**特权动作(借"无参命令执行调用点") | 目标: Linksys E8450(2026-10-04)
- **形态**: 认证检查在**分派器**里、处理函数在其之后 ⇒ 一次控制流重定向可**跳过全部鉴权**进入处理函数;而"不需要参数"的代码点(静态命令串直接交给命令解释器)即使 `x0=0`/栈帧无效也能执行 ⇒ 未认证 ⇒ 特权动作。
- **判据**: 有跳转原语时,影响评估**不要停在"可崩溃"**:枚举"一次跳转即可触发、不需要参数/栈帧"的执行点(如三段式 `adrp x0,<静态串>; add x0,x0,#imm; bl system`),其中**特权/破坏性**者决定后果档位。
- **实证(本目标)**: 117 个此类调用点,50 个特权/破坏性(`reboot`、`mtd write /tmp/raw.bin Kernel1|2`、`mtd -r erase rootfs_data`、`rm -rf /etc/config/…`、服务重启)⇒ 实测跳入其一,设备进程执行了 `service_lan_restart.sh restart`。

## 集成组件代理路由注册在主 API、但漏挂 Scope/身份中间件 ⇒ 低权/未认证调用内部强操作
- **形态**: 产品为同机组件(本例 Protect)提供"内部代理"路由,直接注册在**主 Web API** 上;兄弟路由都挂
  scope 闭包(如 `edit:access.settings`),这一条只挂全局 `appContext`/`catchPanic`。handler 按请求体 `fn`
  分派到 `GetAccessKeys`/`RequestProvision`/`ReExchangeKeys` 等内部强操作。
- **实例**: `POST /api/v2/protect/device/proxy`;未认证从非 loopback 源可达,`GetUserAvatar` 返回 success,
  `RequestProvision` 进入 `nfc_compose.RequestProvisionNfc`;scoped 兄弟 `POST /api/v2/access_group` 返回
  `CODE_UNAUTHORIZED`(负对照)。
- **认得出**: ①路由表里同一前缀下多数路由有 scope 闭包,个别没有;②该路由 handler 是 dispatcher,`fn` 字段
  直接选内部操作;③设计意图是"同机组件调用",但路由在主 API、反代前缀会转发它。
- **验证(最小)**: 导出应用自带 route dump(`/api/v2/routers`)→ 与 scope 闭包清单 join → 取无 scope 且
  handler 为 dispatcher 的路由,从**非 loopback 源**发一份最小 body;同前缀 scoped 路由作负对照。
  只看 200 不够:要记录 handler 是否执行(错误串/内部调用栈)与是否触达内部 service。
- **边界**: handler 需要已配对相机/已运行 user-assets 才产生最终副作用时,把"未取得 key/未落盘"写清,
  只声明"到达特权路径";不要把"未到达 sink"写成"无影响"。
- - **实例 2(2026-10-26 UniFi Access 4.3.12)**: `/api/v2/ucs/*` 整组无 scope;无凭据 `PUT /ucs/adopt` 使 `ucs/status.enable` false→true,二次 adopt 报 already adopted,`unadopt` 复原 ⇒ 认证后低权(nginx 仅 session auth_request)可改 UCS adoption 并触达同组凭据预置路由。
- - **实例 3(2026-10-27 UniFi Access 4.3.12)**: `prepareAvatarProxy` 注册 `GET /avatar/*path` 与 `GET /public/avatar/*path` 时只挂 `LoggerAPIRequest`/`middlewarePutAppContext`/`catchPanic`,**无 token/scope**;代理把原 path+query 转发到 loopback ULP HTTP(测试 harness 9081,生产 9080)。带 `X-Forwarded-For: 203.0.113.9`、不带任何 session/token 的 `GET /avatar/1` 与 `/public/avatar/1?x=1` 均 200,ULP mock 收到原路径 ⇒ 未认证客户端可经 Access 调 loopback ULP 的 GET 路径;真实 ULP 未随包提供,数据面影响需在 ULP 侧确认。
