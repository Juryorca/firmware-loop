# tool-quirks 迁移记录

> 迁移源：`knowledge/tool-quirks.md`（已从工作树移除，Git 历史保留）
> 目标：SQLite `knowledge_entries`；这是迁移审计记录，不是运行时知识权威。

- 源标题数：94
- 新增条目：88
- 合并到已有条目：6
- 退休条目：3
- 第二次迁移：inserted=0, merged=0, skipped=94（source_hash 幂等）

## 退休条目

| # | ID | 原因 |
|---:|---|---|
| 2 | `KN-f9723b62302735` | 旧 Pi/OpenCode 的 pwn 绝对路径规则已被 state/pyenvs.json + state/tools.json 取代 |
| 22 | `KN-49247a29ddae4c` | 旧 Pi subagent 不继承提示词的规则；fwcore 不再创建子代理 |
| 30 | `KN-f95328db024031` | 旧 Pi 截图护栏的自由命令签名已被 tools/termshot.sh + shots.json 取代 |

## 94 条逐条去向

| # | ID | 动作 | 状态 | 标题 |
|---:|---|---|---|---|
| 1 | `KN-f8614a27087947` | insert | approved | 本机环境 \| http_proxy 劫持 localhost → curl 一律 --noproxy '*' \| (2026-09/58025 项目) |
| 2 | `KN-f9723b62302735` | insert | deprecated | 本机环境 \| 禁止 conda 命令;Python 一律 pwn env 绝对路径 \| (2026-09/用户规则) |
| 3 | `KN-f55fa22347ceee` | merge | approved | ida-pro-mcp 无头 \| **必须用 `ida.exe`(GUI),不能用 `idat.exe`**。插件所有 IDB 工具走 `@idasync`=`idaapi.execute_sync()`,需要 IDA 的 Qt 事件循环泵送:①idat + 阻塞式 -S 脚本(如 while-sleep 保活)→ execute_sync 永不执行,所有 IDB 工具挂死(HTTP 000/空);②idat -A + 让 -S 返回 → idat 立即退出,端点死。解法:`ida.exe -A -S start_mcp.py`,脚本内 `MCP_SERVER.serve()`(后台线程)后**必须 return**(保活/清理放 daemon 线程,超时用 `idaapi.qexit`)。判据:`server_health` 能返回即正常;`tools/list`/`int_convert` 不依赖 IDB 所以不能作为可用性判据 \| (2026-09/DIR850L 实测) |
| 4 | `KN-12e8dc152925c3` | insert | approved | idat.exe \| 无 GUI 时 is_idaq()=False,ida_mcp 插件不自动起服务 → -S 脚本手动 MCP_SERVER.serve() \| (2026-09/85046 环境实测) |
| 5 | `KN-45cacd6b16c0bc` | insert | approved | idat.exe \| release 构建 %DebugPrint 无对象细节(v8_enable_object_print 未开) \| (2026-09/V8 线实测) |
| 6 | `KN-28cdb222691b97` | merge | approved | qemu-user 直调 CGI \| 绕过 httpd 直跑 `qemu-mips -L rootfs cgi` 手工注入 env 时,必须补齐 httpd 本会注入的 CGI 变量(`SCRIPT_FILENAME`/`SCRIPT_NAME`/`PATH_INFO`/`PATH_TRANSLATED`/`GATEWAY_INTERFACE` 等),否则目标里未检查的 `getenv()` 返回 NULL → strstr/strlen 等 libc 解引用崩溃,极易误判成"载荷触发内存破坏"。判据:去掉/补上该 env 变量做 A/B,若崩溃随 env 而非载荷变化 → 仿真产物 \| (2026-09/DIR850L 实测) |
| 7 | `KN-e9e4e570c43cb8` | merge | approved | binwalk \| 提取"成功"≠有效:产出<3 文件时换 unblob(独立 venv)重试,两目录都检查 \| (2026-09/环境清单) |
| 8 | `KN-fbc2b93129d074` | merge | approved | qemu-user 跑固件 CGI 依赖绝对路径(/var/run 等)时 → 用 bwrap 造 chroot,别用 -L 硬扛 \| (2026-09-12/DIR850L 实测) |
| 9 | `KN-3c023040788d84` | insert | approved | TP-Link 魔改 Lua 5.1 字节码 \| unluac 直接报错 → 需先转换 \| (2026-09-12/AX21 实测) |
| 10 | `KN-3d79de66da9335` | merge | approved | 本机代理 \| 外部域名默认走 127.0.0.1:7892;`--noproxy '*'` 并非万能 \| (2026-09-13/E8450 实测) |
| 11 | `KN-e31f233448b9f7` | insert | approved | sasquatch \| AGENTS.md 的 `pip install sasquatch` **不可行** → 源码构建 \| (2026-09-13/E8450 实测) |
| 12 | `KN-ad8839d6a437c6` | insert | approved | openwrt.org \| 有 Anubis PoW 反爬,webfetch/curl 拿不到正文 \| (2026-09-13/E8450 实测) |
| 13 | `KN-6e863f4faa7f73` | insert | approved | Linksys 固件获取 \| 站点改版 + EOL 产品下架 → 走第三方镜像 \| (2026-09-13/E8450 实测) |
| 14 | `KN-9e2c807e16fab0` | insert | approved | qemu-aarch64 直跑 ARM64 CGI \| 无需 chroot,`-L rootfs` 即可 \| (2026-09-13/E8450 实测) |
| 15 | `KN-47554aef9e53d4` | insert | approved | CGI stdin 喂数据 \| 尾随换行会被 `while(fgets(...))` 循环覆盖 → JSON 解析失败 \| (2026-09-14/E8450 实测) |
| 16 | `KN-9c2d042e27576a` | insert | approved | qemu-user 溢出类证据的崩溃阈值随启动环境漂移 \| 差分/截图/PoC 必须同环境产出 \| (2026-09-14/E8450 实测) |
| 17 | `KN-f1f039d6322dd4` | insert | approved | Netgear downloads CDN \| 直觉文件名 403,真实 key 常带 build 后缀 → Wayback CDX 反查 \| (2026-09-14/R6700v3 实测) |
| 18 | `KN-1a6da58b0e39f4` | insert | approved | qemu-user 跑厂商守护进程(Netgear upnpd 类) \| NVRAM 桩 + 阻止 daemonize + 补证书 \| (2026-09-14/R6700v3 实测) |
| 19 | `KN-253e503a7efc9c` | insert | approved | qemu-user -strace 输出信号行 \| 可被 parse_signal.py 直接判定 \| (2026-09-14/R6700v3 实测) |
| 20 | `KN-5ac7f0a90facdd` | insert | approved | RouterOS 7.x 守护进程无法离线整机仿真 \| nova 总线 + loader MBR → 改用 LD_PRELOAD 构造器 harness \| (2026-09-15/RouterOS 实测) |
| 21 | `KN-943fadbd277dd6` | insert | approved | tmux \| 截图前 `/tmp/tmux-$UID` 残留 socket 目录导致 `error connecting to ...` \| (2026-09-15/RouterOS 实测) |
| 22 | `KN-49247a29ddae4c` | insert | deprecated | subagent 不继承本提示词的本地端点禁令 \| 派 scanner/triage 前必须显式写"禁止 webfetch/curl 本地端点" \| (2026-09-17/RouterOS 实测) |
| 23 | `KN-21cf087d46112d` | insert | approved | IDA MCP `py_eval` 批量 `ida_hexrays.decompile()` 会卡死 HTTP 线程 \| 导出改用纯反汇编 \| (2026-09-17/RouterOS 实测) |
| 24 | `KN-a15a37758bd380` | insert | approved | ida-pro-mcp 2.0 = 真多库并行(每库独立 worker) \| 旧版 1.x 是端口假隔离 \| (2026-09-17 定稿) |
| 25 | `KN-9320dc040054b9` | insert | approved | RouterOS 相邻版本二进制存在构建布局噪声 \| md5/字节 diff 不能单独定位修复点 \| (2026-09-18/RouterOS 实测) |
| 26 | `KN-c732396d0d2cbc` | insert | approved | RouterOS 动态验证: arm64 npk 不能整机仿真,但 **CHR(x86_64) 能在 qemu 整机跑** \| (2026-09-18/RouterOS 实测) |
| 27 | `KN-4a5df10ff0e0ed` | insert | approved | RouterOS TFTP(tftpd)动态驱动 \| 传输走**新建临时端口** → 探针必须跟随应答源地址 \| (2026-09-19/RouterOS 实测) |
| 28 | `KN-2a5316278894db` | insert | approved | RouterOS REST(singleton 配置)\| `PATCH /rest/<path>` 报 `missing or invalid resource identifier` → 用 `/set` 子命令 \| (2026-09-19/CHR 7.24.4 实测) |
| 29 | `KN-2f13977656d7b1` | insert | approved | QEMU user-net 动态加端口转发 \| monitor 里 `hostfwd_add <netdev_id> <proto>:host-guest` \| (2026-09-19/CHR 实测) |
| 30 | `KN-f95328db024031` | insert | deprecated | 截图护栏: 截图脚本必须是**整条命令**且恰好 2 个参数 \| (2026-09-20/pi harness 实测) |
| 31 | `KN-0d57dba5096f68` | insert | approved | CHR(QEMU user-net)丢弃 >~1470 字节的 UDP 单包 → 动态「无响应」先查包大小 \| (2026-09-20/RouterOS CHR 7.24.4 实测) |
| 32 | `KN-be47be5d33c8a2` | insert | approved | CHR(qemu user-net)guest→宿主 只有 TCP 到 10.0.2.2;UDP 到 10.0.2.2 不被投递 \| (2026-09-20/RouterOS CHR 7.24.4 实测) |
| 33 | `KN-dbe9b0a7dc7c2e` | insert | approved | CHR 上驱动 RouterOS UPnP(`nova/bin/upnp`)动态测试的三条事实 \| (2026-09-20/RouterOS CHR 7.24.4 实测) |
| 34 | `KN-03c542c272d900` | insert | approved | UniFi OS Server 容器 \| 本地 IPC 应用在 `127.0.0.1:11081`,无鉴权中间件 → 当作"可信本地通道"用于枚举与状态搭建 \| (2026-09-21) |
| 35 | `KN-12adfd1bf3bbb4` | insert | approved | WebSocket 升级探测 \| 三个坑会让"路径不存在"的假象出现;101 之后必须看帧 \| (2026-09-21/UniFi OS Server) |
| 36 | `KN-60d6071c38e11c` | insert | approved | UniFi OS Server 容器 \| `--network=host` 会让 unifi-core 崩溃重启(`Invalid MAC address`) → 用 bridge + 单独发布端口 \| (2026-09-21) |
| 37 | `KN-cf6b5eebe3b23f` | insert | approved | TLS 1.3 \| "握手成功"不能证明没开 mTLS → 必须发应用数据看 `certificate_required` \| (2026-09-21/UniFi OS Server RabbitMQ 5671) |
| 38 | `KN-f7a7f721cbceae` | insert | approved | AFL++ 的 qemu 模式:apt 包里没有,需自建(meson 走 pip) \| (2026-09-21/Kali rolling 实测) |
| 39 | `KN-36e445cff8768d` | insert | approved | 活体审计: 动作类端点**不要**用"对值"请求(会真的执行) \| (2026-09-21/UniFi OS Server 容器实测) |
| 40 | `KN-dc74c8d935498f` | insert | approved | Rust stripped 二进制 \| `&'static str` 是无 NUL 的连续 blob ⇒ IDA `strings`/`search_text` 不建条目;用字节扫描定位引用 \| (2026-09-21/UniFi OS Server 宿主组件实测) |
| 41 | `KN-645ad2a981c511` | insert | approved | GoReSym 的 Start/End 存在恒定偏移 bug ⇒ 反汇编 Go 二进制必须自解析 pclntab 取真值地址 \| (2026-09-21/UniFi OS Server ulp-go-app Go 1.20.5 实测) |
| 42 | `KN-9a01fec63c283e` | insert | approved | UniFi OS Server 容器: `UOS_UUID` 必须是 UUID **version 5**,且就绪判据不能只看 `is-system-running` \| (2026-09-22/UOS Server 5.1.42 容器实测) |
| 43 | `KN-f2e16a7b4ec9e0` | insert | approved | Spring-Boot fat jar:应用类在 `BOOT-INF/lib/*.jar` 里,不在顶层 \| (2026-09-22/UniFi Network `ace.jar` 10.5.67) |
| 44 | `KN-4864bd05591e1e` | insert | approved | Synology DSM `.pat` 是加密容器(非 zip) → 用 `synacktiv/synodecrypt` + patology 内置的 **type0 密钥**解;内部 `hda1.tgz` 实为 **XZ**,`rd.bin` 是 **LZMA+cpio** \| (2026-09-24/DS223 DSM 7.2.2-72806 实测) |
| 45 | `KN-d440758038127c` | insert | approved | DSM webapi 仿真:独立跑 `entry.cgi` **恒失败(102)**,注册表由 `synoscgi` 经 `WebAPIDaemonData` 注入 \| (2026-09-24/DS223 DSM 7.2.2-72806 实测) |
| 46 | `KN-705eccd4affc52` | insert | approved | DSM 仿真:认证后 webapi 调用的两道隐藏门 —— **SynoToken 二次校验** 与 **per-app 特权(111)** \| (2026-09-25/DS223 DSM 7.3.2-86009 实测) |
| 47 | `KN-1d50c9e0375945` | insert | approved | DSM 仿真:让 `synoshare --add` 通过卷检查 —— `/run/synostorage/volumetab` 的格式 \| (2026-09-25/DS223 DSM 7.3.2-86009 实测) |
| 48 | `KN-cb7fb6c2a848b9` | insert | approved | DSM 仿真:`synoshare.db` 是 BSD `dbopen()` 库,`dbopen` 在 **libsynocore.so** 里 \| (2026-09-25/DS223 DSM 7.3.2-86009 实测) |
| 49 | `KN-9781d721bc3d7c` | insert | approved | DSM 仿真:FileStation 的 DSL ACL 依赖内核 `system.synoacl` xattr → 用户态仿真不可用 \| (2026-09-25/DS223 DSM 7.3.2-86009 实测) |
| 50 | `KN-1316fe141775db` | insert | approved | DSM FileStation 解压:`ExtractArchiveUsing7z` 走**固件自带 `/usr/bin/7z`**;该 7z 会把 `..`/前导 `/` 剥离(无 Zip-Slip) \| (2026-09-25/DS223 DSM 7.3.2-86009 实测) |
| 51 | `KN-25d6c34f627a3f` | insert | approved | DSM ftpd(SmbFTPD 2.7 Synology fork)仿真起场 + 判"fork 是否受上游 CVE 影响"须先看 primitive \| (2026-09-26/DS223 DSM 7.3.2-86009 实测) |
| 52 | `KN-63fbebd1351321` | insert | approved | avahi/mDNS(及任何忽略 loopback 的组播守护)的本地投递: 隔离 netns + veth 对 \| (2026-09-27/DS223 DSM 7.3.2-86009 实测) |
| 53 | `KN-27386292da6d3f` | insert | approved | DSM 的 `SLIBCExec`/`SLIBCExecl`/`SLIBCPopen` 是 **execv(argv) 封装,非 shell** ⇒ 命令注入面显著小 \| (2026-09-27/DS223 DSM 7.3.2-86009 实测) |
| 54 | `KN-2be8dbd238185d` | insert | approved | DSM FileStation Share-Link 动态研究前置(sharing.db + create 门槛)\| (2026-09-27/DS223 DSM 7.3.2-86009 实测) |
| 55 | `KN-8980f6742995ce` | insert | approved | radare2(发行版包) \| `r2pm -ci r2ghidra` 编译失败 = **r2/sdb/r2ghidra 三方 ABI 错位** ⇒ 反编译别指望 r2,走 IDA \| (2026-09-26/SRM-RT6600ax 立项实测) |
| 56 | `KN-5a07712746e934` | insert | approved | busybox applet 软链接 \| `cat >`/重定向写到 `/bin/<applet>` 会**顺着软链把 busybox 本体覆盖** \| (2026-09-27/SRM-RT6600ax emu 实测) |
| 57 | `KN-8f297371a99484` | insert | approved | 批量跑仿真时:① `pkill -f "<串>"` 会**自匹配杀死自己**;② 挂死的 qemu 需 `timeout -k`;③ 判"挂起"vs"崩溃"看 strace 尾巴 \| (2026-09-27/SRM-RT6600ax emu 实测) |
| 58 | `KN-b1aca86a53ec4b` | insert | approved | Synology `synowebapi --exec` 是**特权本地调用**(绕过 authLevel),**不能当"预认证"oracle** \| (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测) |
| 59 | `KN-ecf86ab82971ea` | insert | approved | Synology `webapi/entry.cgi` 在仿真里恒回 `102` 的真因: **CGI 的 cwd 必须是 `/usr/syno/synoman/webapi/`** \| (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测) |
| 60 | `KN-1a126ab481f575` | insert | approved | 找本机工具/文件:先走"便宜阶梯",禁止全盘扫 \| 来源: 本工作区(WSL) \| (2026-09-27) |
| 61 | `KN-3c58210d989ee4` | insert | approved | 本机交叉编译 aarch64 并与 guest(固件)共享库动态链接 —— fuzz / harness 的通用底座 \| (2026-09-27/SRM-RT6600ax emu 实测) |
| 62 | `KN-37f80aa49eb1a6` | insert | approved | WSL/userns 里 `mount --bind /dev` 失败(devtmpfs 不能进 userns)⇒ 用 **privileged Docker 容器**跑固件 daemon \| (2026-09-27/SRM-RT6600ax 实测) |
| 63 | `KN-7876b97eef7fed` | insert | approved | qemu-user 对 netlink 协议是**硬编码白名单** —— NETLINK_NETFILTER 面在 user-mode 仿真下跑不了 \| (2026-09-28/Synology SRM RT6600ax) |
| 64 | `KN-96c1f976fdaf12` | insert | approved | qemu-user 下 guest `daemon()` 恒失败 → LD_PRELOAD 垫片跑前台;同法可做 mangled-symbol 插桩器定位库内错误 \| (2026-09-28/Synology SRM RT6600ax) |
| 65 | `KN-af00671813fdcf` | insert | approved | QNAP QTS 固件(`.img` + `icpnas` footer):只加密首 1MB 的 PC1 流密码 + QTS CGI 在 qemu-user 下直跑 \| (2026-09-28 QNAP TS-233 QTS 5.2.10) |
| 66 | `KN-f5aaa98d8c8367` | insert | approved | 在宿主侧观测 qemu-user 里 daemon 的真实行为:SysV IPC 权限 + strace(而非自己轮询抢消息)\| (2026-09-28 QNAP qLogEngined/qShield) |
| 67 | `KN-cc229dc60e9cec` | merge | approved | `afl-qemu-trace` 可能只编译了单一架构:先验 arch,再谈 fuzz \| (2026-09-28 本工作区实测) |
| 68 | `KN-14f9fe680b205d` | insert | approved | 找"谁调了这个导入符号":别手工按 `.rela.plt` 下标推 PLT 地址 —— 有些二进制根本没有 PLT \| (2026-09-29 本工作区踩坑) |
| 69 | `KN-c40c81a8c9e3d8` | insert | approved | QNAP QPKG 的 `.tgz` 可能是 LZMA(假后缀):按设备自己的判据解档 \| (2026-09-29 实测) |
| 70 | `KN-b5d6e6b5df3597` | insert | approved | qemu-user 下 `system()`/`popen()` 走**宿主** shell:凡目标守护要调"设备内助手"的面都会降级 \| (2026-09-29 实测) |
| 71 | `KN-7c82fbd719605f` | insert | approved | 调用点判定必须**双工具**(IDA `xrefs_to` 会假阴性)\| (2026-09-29 本工作区实测) |
| 72 | `KN-ba07594af94715` | insert | approved | QNAP CGI「动作→参数表」的机械取法(2026-09-29,本工作区实证 6/7 个分派器) |
| 73 | `KN-13d4705cdaed4a` | insert | approved | QNAP 5.2.x 固件提取配方 + `_thttpd_` env 映射 + qemu multi-process CGI 限制(2026-09-29 TS-233 实测) |
| 74 | `KN-80a1d9fef20f7d` | insert | approved | QNAP 保护 CGI 的 `.text` 加密:qemu 跑不起来**不是 qemu 的问题**(2026-09-29 TS-233 5.2.10 实测) |
| 75 | `KN-535aa5b3b7622f` | insert | approved | QNAP QELF(加密 CGI)解密法:先用公开算法,再用 many-time-pad 绕开密钥(2026-09-29 TS-233 5.2.10 实测) |
| 76 | `KN-4c3ccaa1204cb0` | insert | approved | QNAP QTS(qemu-user)拿**真 admin 会话**的建场配方(2026-09-29 TS-233 5.2.10 实测,H-15 解除) |
| 77 | `KN-9db9dd70bcc239` | insert | approved | QNAP QELF 解密件:qemu 直接 exec 报 `Exec format error`,但经 ld.so 可跑 |
| 78 | `KN-35a611b360bc3b` | insert | approved | QNAP rootfs 里 `.so` 常是 440(root-only)⇒ 非 root 跑 qemu 静默加载失败 |
| 79 | `KN-79afcb9017ba9b` | insert | approved | qemu-user `-L <rootfs>` 会**先试宿主路径**:`/mnt`(等宿主已存在目录)不被翻译 |
| 80 | `KN-621b5ddf80df63` | insert | approved | 缺目标 `.so` 时:按**导入符号**自造 aarch64 垫片,别去找现成的 \| (2026-09-29/QNAP TS-233 QTS 5.2.x) |
| 81 | `KN-659e5f381d7d75` | insert | approved | GraalVM native-image 的"宿主识别"在 qemu-user 下必然失败(读 /proc/cpuinfo 且被 qemu 拦截) \| (2026-10-02/Ubiquiti UDM Pro Network app 实测) |
| 82 | `KN-3af31892fc2c1d` | insert | approved | qemu-user 下 nginx 起不来: `io_setup() failed (38: Function not implemented)`(Linux AIO 不可用) \| (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测) |
| 83 | `KN-80cc91059cdbef` | insert | approved | 把 rootfs 容器化系统 container(让 systemd 当 PID 1): 宿主侧 tar 不要用 `docker exec` 管道 \| (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测) |
| 84 | `KN-f86d9c27370626` | insert | approved | QTS(x86_64)固件 \| 直接跑 CGI 会因**绝对路径 `stat`** 落到宿主 → 用 `bwrap --bind <rootfs> /` 造命名空间 \| (2026-10-03/TS-464) |
| 85 | `KN-cbf61f531421d6` | insert | approved | QTS rootfs \| 原生化进 Docker 可跑完整 web 栈(`apache_proxy` 才是默认前端) \| (2026-10-03/TS-464) |
| 86 | `KN-e39950002b51ca` | insert | approved | QNAP QTS **x86_64** 版拿真会话(admin / 非管理员)的建场配方(本目标实测) \| (2026-10-04/TS-464) |
| 87 | `KN-f04e77087c9bae` | insert | approved | `pkill -f "<串>"` 会匹配到**自己**的命令行 ⇒ 自杀(实测 2026-10-04) |
| 88 | `KN-07b6da6f081204` | insert | approved | 二进制载荷务必以 **bytes 模式**写文件(否则被 UTF-8 重新编码,地址就变了) \| (2026-10-04) |
| 89 | `KN-6532d466ce5ed5` | insert | approved | 厂商签名镜像解不出(automated 提取器零命中)时:查镜像内**解密后的 raw/FIT 文件**里的 squashfs 偏移 \| (2026-10-04) |
| 90 | `KN-cfd10f4f40be28` | insert | approved | 断点下在 PLT 函数上会被**正常流程**先命中 ⇒ 证明"被劫持的调用"要断在自己的跳转目标 \| (2026-10-04) |
| 91 | `KN-7b3a9f7ed66cb4` | insert | approved | qemu-user 的 `-L` 只翻译文件类系统调用;**`execve` 不翻译** ⇒ guest 的 `system()`/`popen()` 跑的是宿主二进制 |
| 92 | `KN-2bff366e0f12f8` | insert | approved | QNAP TS-X32 更新镜像:rootfs2 是 UBI/UBIFS 且不含 rootfs1;QPKG .bin 两类布局 |
| 93 | `KN-0a8e2eded25aa0` | insert | approved | QNAP 多路复用 CGI:多个 CGI 名是同一二进制的软链,按 argv[0] 分派(按文件统计鉴权会漏分支) |
| 94 | `KN-0856afa66d734f` | insert | approved | QNAP CGI 的路径/权限校验函数族:判'无校验'前必须全族 join(只 grep realpath 会误标) |

说明：`merge` 表示与既有 6 条工具判据合并；`insert` 表示新建；`status=deprecated` 的 3 条保留完整原文与版本历史，仅因旧 harness/环境前提失效而退休。
