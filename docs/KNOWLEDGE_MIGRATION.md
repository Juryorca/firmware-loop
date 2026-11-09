# Knowledge Markdown → SQLite 迁移审计

> 迁移完成日期: 2026-11-09 · 状态: 完成 · 运行时知识权威: SQLite `knowledge_entries`
>
> 本文的条目/版本/FTS 数字是**迁移时快照**；迁移后新增知识不回填本文。
> 当前数量以 `python3 -m fwcore knowledge stats` 为准。

## 结论与计数

| 项 | 数量 |
|---|---:|
| 原始 Markdown 知识单元 | 132 |
| 迁移前 SQLite 条目 | 107 |
| 新增 SQLite 条目 | 61 |
| 合并/修订的源单元 | 25 |
| 其中涉及现有条目 | 8（7 个 pattern + Chrome） |
| 已有覆盖（不新增） | 0 |
| 仅保留 Git 历史 | 46 |
| 迁移后 SQLite 条目 | 168 |
| 知识版本记录 | 184 |
| FTS 索引行 | 168 |

> “合并/修订的源单元”按原始 Markdown 单元计：methodology 2 个 Chrome 章节、exploits 7 个案例、
> intel-log 16 条时间线结论。它们落到 **8 个现有条目** 与 **2 个新方法条目**；没有为了凑数新建重复条目。

### 按文件统计

| 文件 | 原始单元 | 处理 |
|---|---:|---|
| `knowledge/methodology.md` | 54 | 52 新增；2 合并到现有 Chrome 条目 |
| `knowledge/sdk-bloodlines.md` | 7 | 7 新增 guideline |
| `knowledge/exploits-validated.md` | 9 | 7 合并到现有 pattern；2 新增 pattern |
| `knowledge/intel-log.md` | 60 | 16 合并到 2 个方法条目；44 仅 Git 历史 |
| `knowledge/INDEX.md` | 2 | 索引/维护说明，非知识；删除 |

### 迁移前/后检索验证

| 查询 | 迁移前命中 | 迁移后命中 | 迁移后首条 |
|---|---:|---:|---|
| `guard` | 1 | 1 | 内存破坏信号:先判“净变化”,再谈越界 —— guard page 只证访问不证破坏 \| (2026-09-20/RouterOS sst |
| `QFinder` | 0 | 1 | 未认证上传：可伪造包校验与 root 执行 |
| `赏金` | 0 | 1 | 提交前先判「收不收」:读 scope 原文 + 上报渠道(security.txt→PSIRT→平台)+ CVE 走 CNA/CNA-LR  |
| `DLOB` | 0 | 1 | D-Link DLOB / wrgac 固件封装 \| 头部 offset 0x00 起有 DLOB 记录:第 1 条含 ASCII `si |
| `execve` | 1 | 1 | qemu-user 的 `-L` 只翻译文件类系统调用;**`execve` 不翻译** ⇒ guest 的 `system()`/`pop |
| `7.24.4` | 1 | 1 | CHR(QEMU user-net)丢弃 >~1470 字节的 UDP 单包 → 动态「无响应」先查包大小 \| (2026-09-20/R |

## 逐单元处置


### methodology.md

| # | 原章节 | 处置 | 目标 |
|---:|---|---|---|
| 1 | 情报锚定的假设轮次会整轮白做 —— 教训与判据 \| (2026-09-18) | new | KN-518a8371f928a5 |
| 2 | 任务给定版本号 = "已修复版"时的工作流(前提证伪 + 补丁差分) \| ⚠️仅作核对,不作假设来源 | new | KN-926c25de634239 |
| 3 | 目标版本若为"安全修复版":它之后的版本要单独查 \| ⚠️仅作核对,不作假设来源 | new | KN-94cdf257cf2cbc |
| 4 | 动态验证环境选型:整机 vs 函数级 \| (2026-09-18) | new | KN-262a7d8572f019 |
| 5 | 构建噪声下用**函数级差分**定位“improve stability”式修复点 \| (2026-09-19) | new | KN-663148fd1b85b3 |
| 6 | 差分的正确定位:它是“**技术**”,不是“**工作项**” \| (2026-09-18) | new | KN-011240008e5e07 |
| 7 | 配置门控分支:先算**值域**,再决定是否审计 \| (2026-09-19) | new | KN-7d22de64ec643a |
| 8 | 会话派生的可预测整数 key:外部如何预测 + micro-race 场景怎么把 connect 延迟移出关键路径 \| (2026-09-19) | new | KN-c693be0ecfaa14 |
| 9 | 闭源协议服务端的“最小客户端 + 存活哨兵”变异法 \| (2026-09-19/RouterOS ppp L2TP/PPTP) | new | KN-0f451a87a74ec1 |
| 10 | 无源码/无仿真环境的内核模块:直接执行固件里的 `.ko` 指令流做差分验证 \| (2026-09-20/RouterOS sstp.ko) | new | KN-afa1d17c9b58b8 |
| 11 | 内存破坏信号:先判“净变化”,再谈越界 —— guard page 只证访问不证破坏 \| (2026-09-20/RouterOS sstp.ko 误报) | new | KN-c825317e690967 |
| 12 | 容器/OCI 型固件:用宿主 docker 直跑得到整机动态环境(免仿真) \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-649fc5715db75b |
| 13 | 反代鉴权链(auth_request + 身份头):判读顺序与"头伪造"负对照 \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-33ce3e2fa4aa02 |
| 14 | 预认证白名单端点逐点判读法:反代 → 应用层 middleware → 直连后端复现 \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-63c394729c66b9 |
| 15 | Go 后端路由表的经验枚举法:字符串候选 + 404 判据 + 直连后端 \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-3b9f4f480c54ff |
| 16 | 可达性以**运行态监听**为准:portmap/端口清单只说明"打算发布什么" \| (2026-09-21/UniFi OS Server) | new | KN-ba63818ed31622 |
| 17 | UDP 发现应答器的判读:先读实现拿格式,再逐字段解码应答,畸形输入只看"进程是否存活" \| (2026-09-21/UniFi OS Server UDP 10001) | new | KN-7e17e350f2e134 |
| 18 | 默认凭据 × listener 的判读顺序(先传输层,后用户面) \| (2026-09-21/UniFi OS Server RabbitMQ) | new | KN-34faf7bd8581d0 |
| 19 | 强方法两轴:各格的落地工具与本机现状(含缺口) \| (2026-09-21/工作区级) | new | KN-8726d7855e7ac8 |
| 20 | 托管运行时(Go / Node)面的"补格"法 —— ①内存错误 oracle 不适用时怎么办 \| (2026-09-21/工作区级) | new | KN-21cf59d2658bc6 |
| 21 | 模拟方式按"缺哪层补哪层"选(别默认只用 qemu-user) \| 来源: 本工作区(2026-09-27) | new | KN-f4db80d9f0f194 |
| 22 | 自托管 / 可本地运行的镜像:**先"建场"(preset)再生差分** \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-d168d027a1470b |
| 23 | 「没对照 / 看不进去」就停 —— 最贵的一类误判(经验) \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-2767f331698632 |
| 24 | 可达性用**产品自身输出**判,不用本地改配置/型号反推 \| (2026-09-21/Ubiquiti UniFi OS Server) | new | KN-dd438174a95fbe |
| 25 | 端口/攻击面清点:就绪判据必须覆盖**长启动的 app runnable**,不能只等 init 目标 \| (2026-09-22/Ubiquiti UniFi OS Server) | new | KN-3f0130dc22bdc1 |
| 26 | 组件自带的 filter 名 ≠ 鉴权点:必须找到"决定放行/拦截"的代码路径 \| (2026-09-22/Ubiquiti UniFi Network 10.5.67) | new | KN-cdfc40e04c3b9d |
| 27 | "输出目录/路径类参数"必须做 canonical 校验;收集器/复制器对 `src == dst` 不设防 \| (2026-09-22/UniFi Network support-dump) | new | KN-c40a9a657c9d30 |
| 28 | 子工具的"默认值"不能外推到包着它的工具(podman × pasta) \| (2026-09-22/UniFi OS Server) | new | KN-d03a0b6556e49f |
| 29 | 判"某端口/接口是否对外可达":先查**厂商端口参考文档**,再谈机制 \| (2026-09-22/Ubiquiti UniFi Network 28082) | new | KN-ce61bd8faf38f2 |
| 30 | 枚举"某个 dispatcher/路由前缀到底挂了哪些 handler":先读它的 `@Import`,别对全 API 面数 | new | KN-206d825ce1d8cf |
| 31 | 鉴权边界的"解析差分"矩阵(nginx↔后端):先取必拒基线,再用 content-type 区分"真到达后端"与"静态兜底" | new | KN-f2d3e274661298 |
| 32 | 未文档化监听端口:先给**监听类**点名(thread dump),再读 handler —— 别靠逐个探包猜协议 \| (2026-09-26/Ubiquiti UniFi Network) | new | KN-c44adc724cd905 |
| 33 | 判"某端口是否对外可达":`portmap.json` ≠ 发布面全集 —— 三源并列,且注意遗漏 \| (2026-09-27/Ubiquiti UOS Server) | new | KN-3b4eb4d614ab0d |
| 34 | Synology DSM webapi 的**鉴权分两层**,审 batch/compound 子请求时必须分开核对 \| (2026-09-24/DS223 DSM 7.2.2-72806 静态) | new | KN-be1e22962deae4 |
| 35 | 认证后 API 的"可达性差分":**前置 scope 门会伪装成"非 105"**,判 reached 必须用业务码或对角验证 \| (2026-09-25/DS223 DSM 7.3.2-86009 动态) | new | KN-0cdcbae5bfa965 |
| 36 | 同一 API 内各 method 的授权语义可能不同 —— 必须**逐 method** 验证,不能由"组内某 method 正确"外推 \| (2026-09-26/DS223 DSM 7.3.2-86009 动态确证) | new | KN-6881f0ea946f82 |
| 37 | 补丁/前置无关性的自证:用**全文件调用图**证明"补丁只影响 X,不影响被测 Y" \| (2026-09-26/DS223 DSM 7.3.2-86009) | new | KN-b175e9288d1f85 |
| 38 | 枚举"某方法是否对普通用户可达"必须**方法级 allowUser 覆盖 api 级** \| (2026-09-26/DS223 DSM 7.3.2-86009) | new | KN-5ffba514f6d51a |
| 39 | 未剥离 Go 固件二进制:用标准库 `debug/gosym` 解 `.gopclntab` 恢复函数名/地址 \| (2026-09-26/Synology DS223 DSM 7.3.2 `webrtcproxy`) | new | KN-4fce512f11cca1 |
| 40 | 判"转发/代理 JSON 的某字段是否客户端可控":必须核**赋值点 + 动态捕获** \| (2026-09-26/Synology DSM 7.3.2,VR-01 的 `port`) | new | KN-2c141df2ebc2ca |
| 41 | Synology(SRM/DSM 家族)webapi 的动态派发:优先用固件自带 `synowebapi --exec`,别去凑 httpd/SCGI 注册表 \| (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测) | new | KN-478f518842239f |
| 42 | 判"预认证可写接口"的**实际收益**前,先问一句:正常流程本身要不要凭据? \| (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测) | new | KN-da26597b98222b |
| 43 | 判"某入参是不是主体/对象"时,**参数被读取 ≠ 被采用**(2026-09-29 QNAP userConfig 实测) | new | KN-b98afca7ec915f |
| 44 | 静态筛选:函数画像 × 动作表 join,先找"**有危险汇、无直接门**"的 handler \| (2026-09-29/QNAP) | new | KN-94cb0f2468279f |
| 45 | 同版本组件的 native AOT vs 姊妹产品 Java 字节码:用后者反推前者 \| (2026-10-02/Ubiquiti UDM Pro 5.1.33 实测) | new | KN-f93808802cea7a |
| 46 | 别把 HTTP 201 / `@ResponseStatus(CREATED)` 当作"真的建了资源" —— 先看注解 \| (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测) | new | KN-60d35f8d54199b |
| 47 | port×URI allow-list 型连接器: 每个端口都可能暴露**多个**未认证 handler —— 逐条审,别只看被点名的那个 \| (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测) | new | KN-c72e8a06fefc3b |
| 48 | 鉴权门用“别的文件模式 / 状态旗标”代理时,要枚举该状态下**全部**可达命令 \| 判据/步骤 \| 证据/产物: QNAP-TS-464 \| (2026-10-03) | new | KN-3b9b49a0dccfcc |
| 49 | 控制流劫持类缺陷的**影响评估**清单(厂商表单口径;一次跳转即可触发的无参执行点) \| 判据/步骤 \| 证据/产物: Linksys E8450 轮 | new | KN-4a748fa0e21691 |
| 50 | 提交前核'最新版' + 逐 finding 组件哈希差分 + 动态复测(同一更新里常有 A 修了/B 没修) | new | KN-9b82dbcd922aaa |
| 51 | 用应用自带 route dump × scope 闭包清单做"缺鉴权路由"筛选 | new | KN-83bf51ad7b33c4 |
| 52 | 厂商下载页是 JS 壳时:用无头浏览器 MCP 抓真实下载 API(拿到直链再回 curl + md5) | merged | KN-21ee6373c5859c |
| 53 | 跨 WSL 用 --autoConnect 连 Windows 上的已登录 Chrome(必须带 --userDataDir,且要真调工具才知通不通) | merged | KN-21ee6373c5859c |
| 54 | 提交前先判「收不收」:读 scope 原文 + 上报渠道(security.txt→PSIRT→平台)+ CVE 走 CNA/CNA-LR 而非猜邮箱 | new | KN-126eb75415ee9a |

### sdk-bloodlines.md

| # | 原章节 | 处置 | 目标 |
|---:|---|---|---|
| 1 | D-Link DLOB / wrgac 固件封装 \| 头部 offset 0x00 起有 DLOB 记录:第 1 条含 ASCII `signature=wrgac??_..._<model>`(如 `wrgac05_dlob.hans_dir850l`)+ `dev=/dev/mtdblock/1` + `type=firmware`;随后 LZMA 内 | new | KN-984f853ab0f223 |
| 2 | Alpha Networks 路由器固件(CGI/服务框架) \| 目录含 `/etc/services/*.php`(PHP 生成 start/stop shell)+ `/usr/sbin/servd`(service 调度器)+ `xmldbc` XML 配置总线;CGI 统一分发器 `htdocs/cgibin`(软链为 `/usr/sbin/{ph | new | KN-6d8974653a903d |
| 3 | Linksys/Belkin 家用路由器(portal.cgi + lighttpd + LuCI/OpenWrt) \| 固件 = Linksys 签名容器(头 `85 01 0c 03`,raw 为 D00DFEED FIT + SquashFS4 xz);系统 = LEDE/OpenWrt `mediatek/mt7622`,aarch64,glibc | new | KN-f5fa45ec521593 |
| 4 | NETGEAR Nighthawk 家用路由器(R6700v3,upnpd NETGEAR-UPNP) \| Broadcom BCM4709 系;固件 CHK `*#$^`+TRX v1(LZMA 内核 + SquashFS4 xz);系统 ARMv7 EABI5/uClibc/busybox;Web = 自研 `usr/sbin/httpd`(内置 ~1 | new | KN-11eb8e45380752 |
| 5 | MikroTik RouterOS 7.x \| 固件 = `.npk`(magic `1e f1 d0 ba`,头部含包名/`stable`/架构/描述)+ offset 0x1000 SquashFS v4.0 xz;解包后 **arm64 包的用户态仍是 32-bit ARM EABI5**(仅内核为 aarch64,`lib/modules/5.6. | new | KN-0d6e631a341da2 |
| 6 | Synology SRM webapi / FileStation 路径语义 \| FileStation 分享/文件路径在 webfm 层是 **share-relative**(首段=share 名, 如 `/test/x.txt`);`SYNOShareGet()`/`WfmFullPathGet()` 取的是 **share 名**(传 `/volu | new | KN-a73fefa62fde2f |
| 7 | QNAP QTS 的 WS-Discovery(内建 WSD) \| 内置 QPKG `wsd.tgz`(成员 `wsd.py` / `WSDiscovery.py` / `wsd.cgi` / `listifs.py` / `struct.py`):`wsd.py` 用 Python2(来自 Python QPKG `/mnt/ext/opt/Python | new | KN-e59a939bdf42f6 |

### exploits-validated.md

| # | 原章节 | 处置 | 目标 |
|---:|---|---|---|
| 1 | DIR-850L 1.10WW / cgibin `hnap_main` \| 未认证 HNAP 命令注入 + 栈溢出 \| 前提: HTTP `SOAPAction` 含 `.../GetDeviceSettings`(免认证)+ 路径 action 名带 `;`/`` ` ``; `/HNAP1/` 由 httpd 映射到 `/usr/sbin/hnap | merged | KN-29d1cb581e0c42 |
| 2 | TP-Link Archer AX21 V5.6 1.0.2 / LuCI `locale.get_sysinfo` \| 免会话命令注入(Accept-Language) \| 前提: 根级 `/cgi-bin/luci/;stok=/locale?form=lang` 无 sysauth(仅 admin 节点设 sysauth)+ uci `locale | merged | KN-29d1cb581e0c42 |
| 3 | TP-Link Archer AX21 V5.6 1.0.2 / tdpServer UDP:20002 \| 未认证 TDP 协议访问 + 信息泄露 \| 前提: LAN 可达(wan input DROP) \| 影响: 自构造合法 TDP 包(16B 头+CRC32)可无认证调用 opcode;type0 op2 返回 252B 残留缓冲,泄露代码指针 | merged | KN-b2e0d6d307cb2b |
| 4 | Linksys E8450 1.2.00.360516 / portal.cgi `http_passwd_hidden` \| 免认证命令注入(反引号绕过转义) \| 前提: HTTP 可达 `/cgi-bin/portal.cgi`;入口二选一——①登录路径 `page=index&action=set`(无 session),②`action=hand | merged | KN-29d1cb581e0c42 |
| 5 | Linksys E8450 1.2.00.360516 / portal.cgi `hidden_timezone` \| 免认证栈缓冲溢出 \| 前提: HTTP 可达;`action=set_wizard_status`(需带合法 `wizard_status` 规避另一处 NULL deref) \| 影响: `strcpy(dest[128], hi | merged | KN-137c9f9473fe21 |
| 6 | NETGEAR R6700v3 V1.0.5.128 / upnpd SOAP `sa_method_check` \| 免认证全局缓冲区溢出(长度下溢→巨值 memcpy) \| 前提: LAN/网络相邻可达 upnpd TCP 5000;`POST /soap/server_sa/` 且 `SOAPAction` 为 `urn:NETGEAR-ROUTE | merged | KN-137c9f9473fe21 |
| 7 | RouterOS 7.24.1 arm64 / `nova/bin/mtget` TFTP RRQ builder \| 认证后栈溢出(URL path → 固定 520B 栈缓冲) \| 前提: 能执行 `/tool fetch`(read 组即可);`tftp://host/<长 path>`;**无需可达 TFTP 服务器**(RRQ 在收到任何响应前 | merged | KN-137c9f9473fe21 |
| 8 | Synology DSM QuickConnect WebRTC 中继:预认证客户端 IP 欺骗(部分验证) \| (2026-09-24/DS223 DSM 7.2.2-72806) | merged | KN-4fad2760d09afb |
| 9 | QNAP QTS:QFinder 上传助手未认证接收 ⇒ 伪造包以 root 执行(TS-233 / 5.2.10.3577) | merged | KN-445b445924e7d8 |

### intel-log.md

> 纯历史调查过程/目标临时情报只保留 Git 历史；可复用结论合并到方法条目并在正文保留日期与 `#L` 引用。

| 行 | 处置 | 目标/理由 |
|---:|---|---|
| 7 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 8 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 9 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 10 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 11 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 12 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 13 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 14 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 15 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 16 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 17 | merged | KN-126eb75415ee9a |
| 18 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 19 | merged | KN-126eb75415ee9a |
| 20 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 21 | merged | KN-9b82dbcd922aaa |
| 22 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 23 | merged | KN-126eb75415ee9a |
| 25 | merged | KN-9b82dbcd922aaa |
| 26 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 27 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 29 | merged | KN-9b82dbcd922aaa |
| 30 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 31 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 33 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 34 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 35 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 36 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 37 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 38 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 39 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 40 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 41 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 42 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 43 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 44 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 45 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 46 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 47 | merged | KN-126eb75415ee9a |
| 48 | merged | KN-126eb75415ee9a |
| 49 | merged | KN-126eb75415ee9a |
| 51 | merged | KN-126eb75415ee9a |
| 52 | merged | KN-126eb75415ee9a |
| 53 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 54 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 55 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 56 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 57 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 58 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 59 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 60 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 61 | merged | KN-9b82dbcd922aaa |
| 62 | merged | KN-9b82dbcd922aaa |
| 63 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 64 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 65 | merged | KN-126eb75415ee9a |
| 66 | merged | KN-126eb75415ee9a |
| 67 | merged | KN-126eb75415ee9a |
| 68 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 70 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |
| 71 | git_only | 历史目标/临时情报；结论已由方法论/SDK/exploit/既有工具条目覆盖 |

### INDEX.md

`存储分层`、`维护` 两节是索引/运维说明，不是研究知识；随文件删除，SQLite API 文档取代。


## 数据可靠性

- 迁移前备份: `state/knowledge-migration-backup-20261109-174400.sqlite3` 与 `state/knowledge-migration-backup-20261109-174747.sqlite3`
- 迁移后备份: `state/knowledge-migration-final-20261109-175038.sqlite3`
- 备份通过 SQLite backup API（含 WAL 一致性快照），恢复测试通过：迁移前 107 条、迁移后 168 条；FTS 168 行；版本 184 条；`integrity_check=ok`、外键 0 错误。
- 新环境不依赖 Markdown：`state/` 不在 Git；部署必须恢复验证过的 SQLite 备份，或通过 `knowledge_propose` 从研究证据重新积累。没有 Markdown seed/fallback/双写。

## 删除结果

已完成：删除当前工作树的五份 Markdown，`knowledge/` 目录不再存在；原文由 Git 历史保留。

```text
D knowledge/INDEX.md
D knowledge/methodology.md
D knowledge/sdk-bloodlines.md
D knowledge/exploits-validated.md
D knowledge/intel-log.md
```

## 恢复与检索验证

- SQLite backup API 备份/恢复通过；迁移前 107 条、迁移后 168 条；`integrity_check=ok`、外键 0 错误。
- `knowledge_search` 抽样：`guard`、`QFinder`、`赏金`、`DLOB`、`execve`、`7.24.4` 均命中。
- `knowledge_get`：返回完整正文、tags 与版本历史。
- 迁移脚本幂等：同标题同正文跳过；新条目/修订均走现有 Knowledge API。

## 证据

- `knowledge_search` / `knowledge_get` 抽样（guard、QFinder、赏金、DLOB、execve、7.24.4）均命中并返回完整正文。
- 迁移脚本为一次性、幂等：已存在同标题且正文相同即跳过；新条目/修订均走 SQLite Knowledge API 并生成版本记录。
