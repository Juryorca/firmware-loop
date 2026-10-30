# 工具怪癖与解法
> 条目格式:`## <工具> | <坑> → <解法> | (日期/来源)`

> **来源标注**:条目里的 `<目标>/…` 仅为来源标注(该目标的证据随目标清理,**不要求长期存在**);复用依据是条目自身的判据/反例/现象。

## 本机环境 | http_proxy 劫持 localhost → curl 一律 --noproxy '*' | (2026-09/58025 项目)
## 本机环境 | 禁止 conda 命令;Python 一律 pwn env 绝对路径 | (2026-09/用户规则)
## ida-pro-mcp 无头 | **必须用 `ida.exe`(GUI),不能用 `idat.exe`**。插件所有 IDB 工具走 `@idasync`=`idaapi.execute_sync()`,需要 IDA 的 Qt 事件循环泵送:①idat + 阻塞式 -S 脚本(如 while-sleep 保活)→ execute_sync 永不执行,所有 IDB 工具挂死(HTTP 000/空);②idat -A + 让 -S 返回 → idat 立即退出,端点死。解法:`ida.exe -A -S start_mcp.py`,脚本内 `MCP_SERVER.serve()`(后台线程)后**必须 return**(保活/清理放 daemon 线程,超时用 `idaapi.qexit`)。判据:`server_health` 能返回即正常;`tools/list`/`int_convert` 不依赖 IDB 所以不能作为可用性判据 | (2026-09/DIR850L 实测)
## idat.exe | 无 GUI 时 is_idaq()=False,ida_mcp 插件不自动起服务 → -S 脚本手动 MCP_SERVER.serve() | (2026-09/85046 环境实测)
## idat.exe | release 构建 %DebugPrint 无对象细节(v8_enable_object_print 未开) | (2026-09/V8 线实测)
## qemu-user 直调 CGI | 绕过 httpd 直跑 `qemu-mips -L rootfs cgi` 手工注入 env 时,必须补齐 httpd 本会注入的 CGI 变量(`SCRIPT_FILENAME`/`SCRIPT_NAME`/`PATH_INFO`/`PATH_TRANSLATED`/`GATEWAY_INTERFACE` 等),否则目标里未检查的 `getenv()` 返回 NULL → strstr/strlen 等 libc 解引用崩溃,极易误判成"载荷触发内存破坏"。判据:去掉/补上该 env 变量做 A/B,若崩溃随 env 而非载荷变化 → 仿真产物 | (2026-09/DIR850L 实测)
## binwalk | 提取"成功"≠有效:产出<3 文件时换 unblob(独立 venv)重试,两目录都检查 | (2026-09/环境清单)
## qemu-user 跑固件 CGI 依赖绝对路径(/var/run 等)时 → 用 bwrap 造 chroot,别用 -L 硬扛 | (2026-09-12/DIR850L 实测)
`qemu-mips -L rootfs` 只改 ELF 解释器/库搜索路径,**不改绝对路径命名空间**:目标里 `open("/var/run/hnap.lock")` 落到宿主 `/run`(普通用户 EACCES),目标于是走 else 分支静默退出,看起来"载荷无效",极易误判。
解法(tier-3 合理改造,已用于 DIR850L hnap):
```bash
bwrap --bind "$ROOT" / --ro-bind /usr/bin/qemu-mips /qemu-mips \
      --dev /dev --proc /proc --tmpfs /var/run \
      --bind /tmp/opencode /out --chdir /out \
      --setenv REQUEST_METHOD GET --setenv HTTP_SOAPACTION "$SOAP" \
      /qemu-mips -0 hnap -L / /htdocs/cgibin
```
要点:①`--bind ROOT /` 让固件绝对路径生效;②静态 `qemu-mips` ro-bind 进新根即可,无需宿主 lib;③`--tmpfs /var/run` 解决锁文件写权限;④`-0 <name>` 强制 argv[0]=`hnap`,命中 cgibin 的 basename 分发;⑤别 `--tmpfs /tmp`——固件 `/tmp` 是软链到 `/var/tmp`,bwrap 会报 `Can't mount tmpfs ... No such file or directory`。
附:`bwrap --setenv` 必须写成**两个参数** `--setenv NAME VALUE`;写成 `--setenv "NAME=VALUE"` 会把 `=` 带进变量名,报 `setenv failed`。

## TP-Link 魔改 Lua 5.1 字节码 | unluac 直接报错 → 需先转换 | (2026-09-12/AX21 实测)
固件内 `/usr/lib/lua/**/*.lua` 是**魔改 Lua 5.1.4 字节码**(全 175 个文件),与标准不兼容,三处差异:
1. 头部 byte11(标准 integral flag=0)= `0x04`
2. 常量类型: nil=0/boolean=1/**number=3**/**string=4**(标准同),但**额外 int=9 存 4 字节小端整数**(标准是 8 字节 double)
3. **opcode 号被重排**;真实顺序可从固件自带 `/usr/bin/luac` 的 `luaP_opnames` 字符串表按序读出:
   `GETTABLE,GETGLOBAL,SETGLOBAL,SETUPVAL,SETTABLE,NEWTABLE,SELF,LOADNIL,LOADK,LOADBOOL,GETUPVAL,LT,LE,EQ,DIV,MUL,SUB,ADD,MOD,POW,UNM,NOT,LEN,CONCAT,JMP,TEST,TESTSET,MOVE,FORLOOP,FORPREP,TFORLOOP,SETLIST,CLOSE,CLOSURE,CALL,RETURN,TAILCALL,VARARG`
解法(已封装):`AX21/artifacts/tools/tplua_convert.py <in> <out>`(改 header byte11=0、int9→number3、opcode 重映射)
配合 **unluac 1.2.3**(`artifacts/tools/unluac.jar`)反编译;`decompile_all.sh` 批量。
判据:标准 Lua 5.1 常量类型是 number=3/string=4(不是 2/3);unluac 报 `unmapped type code N` 即类型映射错,报 `Function doesn't end with implicit return` 即 opcode 未重映射。
注意:固件 `/usr/bin/luac` 能识别原始格式,可用 `qemu-arm -L rootfs rootfs/usr/bin/luac -l -l <file>` 交叉验证 opcode/常量数。

## 本机代理 | 外部域名默认走 127.0.0.1:7892;`--noproxy '*'` 并非万能 | (2026-09-13/E8450 实测)
`env` 有 `http_proxy/https_proxy=http://127.0.0.1:7892`,NO_PROXY 只含内网/本机段。
- 直连(`--noproxy '*'`):linksys.com、github.com、raw.githubusercontent.com、api.github.com 可通;
  **web.archive.org/archive.org 直连不通(000)**,必须走代理(去掉 `--noproxy`)。
- archive.org 走代理也极易 **429 Too Many Requests**(CDX/availability API),重试要间隔;CDX 大 prefix 查询会挂起到超时。
- 判据:curl 000 先换代理/直连各试一次,别急着判定"站点不存在"。

## sasquatch | AGENTS.md 的 `pip install sasquatch` **不可行** → 源码构建 | (2026-09-13/E8450 实测)
PyPI 的 `sasquatch` 是 2018 年无关 Python 语法包(同名冲突),sdist 缺 requirements 无法构建。
真工具是 devttys0/sasquatch(C 源码,squashfs4.3 + patch0)。GCC14 下构建坑:
- `-Werror` → 去掉;`-Wincompatible-pointer-types`(GCC14 默认 error)→ `-Wno-incompatible-pointer-types`;
- `multiple definition of 'verbose'` → 加 `-fcommon`;
- 缺 `lzo/lzoconf.h` → `LZO_SUPPORT=0 LZ4_SUPPORT=0`(xz 镜像不需要)。
产物 `venvs/unblob/bin/sasquatch.bin` + wrapper `sasquatch`(剥离 unblob 传入的 `-no-exit-code`)。
**unblob 用 PATH 解析提取器**:直接调 `venvs/unblob/bin/unblob` 时 venv/bin 不在 PATH → 必须
`PATH=/home/juryorca/venvs/unblob/bin:$PATH`。装好后 AGENTS.md 提取器表 sasquatch 应为 ✓。

## openwrt.org | 有 Anubis PoW 反爬,webfetch/curl 拿不到正文 | (2026-09-13/E8450 实测)
返回 "Testing to determine if you are a bot!" 占位页。需要 OpenWrt 设备资料时改走
其他镜像(如 git.openwrt.org 首页/raw、第三方文档)或手动检索,不要在该域名上耗步数。

## Linksys 固件获取 | 站点改版 + EOL 产品下架 → 走第三方镜像 | (2026-09-13/E8450 实测)
- 老 `downloads.linksys.com/downloads/firmware/FW_<型号>_<版本>_prod.img` 对已 EOL 型号返回 404。
- 新支持站 support.linksys.com 为 HubSpot 知识库:`/kb/search/?q=<型号>` 可列文章,但 discontinued 产品
  不再挂固件下载;`/kb/article/504-en/` 会标 discontinued。
- 可行替代:GitHub 公开仓库(如 `Wood1314/Linksys_E8450_vul`)存官方 `*_prod_signed.img` + 预提取 portal.cgi;
  下载后务必记 URL+MD5/SHA256,并用 rootfs 内同名二进制做 hash 交叉核对。

## qemu-aarch64 直跑 ARM64 CGI | 无需 chroot,`-L rootfs` 即可 | (2026-09-13/E8450 实测)
`/usr/bin/qemu-aarch64` 本机为**静态链接**,可直接跑固件 aarch64 CGI:
`REQUEST_METHOD=POST CONTENT_LENGTH=<n> REMOTE_ADDR=<ip> SCRIPT_FILENAME=/www/cgi-bin/x.cgi \
  qemu-aarch64 -L <t>/rootfs <t>/rootfs/www/cgi-bin/x.cgi < body.json`
- 要点:`-L` 只改解释器/库搜索,**不改绝对路径命名空间**;host `/dev/console`/`/etc/config` 会被命中,
  但很多目标对 `fopen("/dev/console")` 失败有 guard,`/etc/config` 读到空值属正常(CGI 仍能进业务逻辑)。
- 判据:能拿到 stdout 的 JSON 响应即入口解析链路已通;要精确控制 `/etc/config`/`/tmp` 再上 bwrap chroot
  (见上方 DIR-850L 条目)。
- portal.cgi 形态:JSON 从 **stdin** 读,响应 JSON 从 **stdout** 写。

## CGI stdin 喂数据 | 尾随换行会被 `while(fgets(...))` 循环覆盖 → JSON 解析失败 | (2026-09-14/E8450 实测)
很多 CGI 用 `while (fgets(buf, len, stdin));` 反复读**同一个**缓冲(**最后一次读取留在缓冲里**)。
若 body 文件带尾随 `\n` 且 `CONTENT_LENGTH` = 不含换行的字节数,则第一次 fgets 读走 JSON、
第二次 fgets 读到 `\n` 覆盖之 → `cJSON_Parse` 拿到 `"\n"` → 报 `Failed to get root data`。
判据:目标打印"解析失败"但请求明明合法。
解法:body 文件**不要尾随换行**(`printf '%s' '<json>' > body`),`CONTENT_LENGTH`=`wc -c` 精确值;
此时最后一次 fgets 读到 JSON 且遇 EOF 结束,缓冲内容保持。
(同类:若目标用 `read(0,...,CONTENT_LENGTH)` 则无此坑;先反编译 `main` 确认读法再构造 harness。)

## qemu-user 溢出类证据的崩溃阈值随启动环境漂移 | 差分/截图/PoC 必须同环境产出 | (2026-09-14/E8450 实测)
现象: 同一个 unbounded strcpy(128B buffer),参照环境 130 字节即 SIGSEGV,换启动环境
(env 块大小/工作目录不同 → 初始栈布局不同)后 130 存活、132 崩,且非单调(160/260/320
exit=1 走错误路径,200/400 崩)——踩坏相邻帧哪个槽位决定后果。
规则: 差分表、截图、PoC 必须在同一环境产出,报告中声明环境;结论写**不变量**
(buffer 尺寸、无 canary、copy 无界)+ **区间事实**("阈值随栈布局漂移"),
禁止把单点边界(如 "129 正常/130 崩")写成固有属性;MITRE 描述同理。
截图管线已封装(声明式): 先在 finding 包目录写 `shots.json`,再
`tools/termshot.sh <shots.json> <shot-id>` 一条命令出 PNG + `.provenance`
(内部 = tmux 120x45 真会话 + 提交人默认 shell zsh 原生提示符(不匿名化/不换 bash/不改
LANG —— 实测反馈: 中性提示符反而"像假的") + 前台进程归还即拍 + chromium 渲染)。
manifest 与脚本、截图同目录;缺 why / 脚本越界 / sha 不符 / argv 含元字符一律拒收
(旧版 `<工作目录> <输出.png> <命令>` 自由命令签名已废弃 —— 它就是“往终端里塞任意指令”的入口)。

## Netgear downloads CDN | 直觉文件名 403,真实 key 常带 build 后缀 → Wayback CDX 反查 | (2026-09-14/R6700v3 实测)
`https://www.downloads.netgear.com/files/GDC/<model>/<model>-V<x.y.z>.zip` 常返回 S3
`403 AccessDenied`(对象名不含 build,如真实为 `R6700v3-V1.0.5.128_10.0.104.zip`)。判据:
同站对照文件(如 RAX35)返回 206 即站点可用,只是 key 错。解法(需走代理,archive.org 直连 000):
`curl "http://web.archive.org/cdx/search/cdx?url=downloads.netgear.com/files/GDC/<model>*&output=text&fl=original,timestamp,statuscode&collapse=urlkey&limit=60"`。
拿到确切 key 后直连下载;并记 URL+MD5 入库。同类:厂商用 S3/CloudFront 时 key 猜测不可行,一律先取目录快照。

## qemu-user 跑厂商守护进程(Netgear upnpd 类) | NVRAM 桩 + 阻止 daemonize + 补证书 | (2026-09-14/R6700v3 实测)
现象与阶梯:
1. 直接 `qemu-arm -L rootfs usr/sbin/upnpd` → 打开 `/dev/nvram` 失败 → 空指针 → SIGSEGV(非目标漏洞,是环境)。
   `libnvram.so` 走 `open("/dev/nvram")+mmap(0x20000)`,伪造镜像格式不确定 → 改 **LD_PRELOAD 桩**。
2. 无 ARM 交叉工具链且无 passwordless sudo 时,用 pip 装 `ziglang`,`zig cc -target arm-linux-gnueabi
   -nostdlib -shared -fPIC` 产出**无 libc 依赖**的 ARM EABI5 桩(可被固件 uClibc loader 加载)。
   桩需实现 upnpd 引用的全部 `acosNvramConfig_{get,match,invmatch,read,set,save,unset,exist,readAsInt}`
   与 `nvram_{get,set,unset,commit}`;**接口类键必须返回宿主真实存在网卡的 IP**(否则 SSDP
   `IP_ADD_MEMBERSHIP` 报 ENODEV → `exit(1)`)。
3. 绝对路径:`/var/run/upnpd.pid` 落到宿主 `/run` → EACCES;用 bwrap 把 rootfs bind 成 `/`。
4. `daemon()` 使进程 fork+setsid,父进程退出后 bwrap 拆命名空间杀掉子进程 → 把 `daemon` 的 **PLT 桩**
   在文件里 patch 成 `mov r0,#0; bx lr`(ARM:`00 00 a0 e3 1e ff 2f e1`),进程即留前台。
5. `sub_27D38`(SSL init)在 `/tmp/h.c`+`/tmp/h.k` 不存在时用未初始化 `certfile` 解引用 → 放一对自签
   cert/key 即过。
运行模板:
```bash
bwrap --bind "$ROOT" / --ro-bind /usr/bin/qemu-arm /qemu-arm --dev /dev --proc /proc \
  --setenv LD_PRELOAD /libnvstub.so /qemu-arm -strace -L / /usr/sbin/upnpd
```
原始提取产物不动;桩/patch 只写工作副本并记入 notes。

## qemu-user -strace 输出信号行 | 可被 parse_signal.py 直接判定 | (2026-09-14/R6700v3 实测)
`qemu-arm -strace` 会把 guest 信号打成 `--- SIGSEGV {si_signo=SIGSEGV, si_code=1, si_addr=...} ---`
(strace 同格式),`tools/parse_signal.py` 的 crash 判据命中。若只看 qemu 的
`qemu: uncaught target signal 11`,该正则不匹配 → 必须开 `-strace` 才能得到 HIT:crash。
注意 upnpd 自身 `popen("arp -n")` 会引入 `execve("/bin/sh")`,使 parse_signal 同时报 `shell_exec`
(噪声),判定时以 `crash` 为准。

## RouterOS 7.x 守护进程无法离线整机仿真 | nova 总线 + loader MBR → 改用 LD_PRELOAD 构造器 harness | (2026-09-15/RouterOS 实测)
现象与阶梯:
1. `qemu-arm -L rootfs rootfs/nova/bin/<daemon>` 一律 `socket(PF_UNIX)` → `connect("/ram/novasock")`
   失败 errno=2 → `write(2,"connect failed (errno=2)")` → SIGABRT。
2. 自建假总线(bind `/ram/novasock` + `logsocket` 等 unix socket,`bwrap --bind rootfs / --bind <fakeram> /ram`)
   → connect 成功,daemon 发出 137B 注册包(`850000004d320100...`),随后 `poll(...,20000)` 等确认;
   超时报 `could not receive confirmation to CTRL_READY from loader` 后 abort。**缺 READY_CONFIRM 报文
   (nv::message 私有二进制格式),不值得为它逆向。**
3. 直接跑 `/nova/bin/loader`:输出 `readMBR: could not open /dev/mtdblock48` → `Failed to read MBR` → exit(1)。
   整机仿真需要真实 MBR/分区表,放弃。
**可行替代(zig 交叉编译)**:
```bash
ZIG=/home/juryorca/ENTER/envs/pwn/lib/python3.14/site-packages/ziglang/zig
$ZIG cc -target arm-linux-gnueabi -nostdlib -shared -fPIC -fno-stack-protector -o poc.so poc.c
qemu-arm -L rootfs -E LD_PRELOAD=/abs/path/poc.so rootfs/nova/bin/<daemon>
```
- RouterOS 用户态是 musl + soft-float ARM EABI5;`-nostdlib` 保证 .so 无 libc 依赖(musl loader 可直接加载),
  需要 syscall 时用 inline asm `svc 0`(ARM 号: write=4、exit=1、socket=281)。
- `__attribute__((constructor))` 在 **main 之前**执行;可 `((void(*)(void*))0xNNNN)(arg)` 直调目标二进制内固定
  地址的函数(EXEC 非 PIE,base 0x10000)。
- daemon 依赖总线初始化时:把工作副本的入口 prologue 后几条指令 patch 成 `b <目标块>`,并补齐该块需要的寄存器
  (`add r7,r4,#0xc0` / `mov r5,#0` / `mov r6,#0`),即可让目标函数完整跑完并正常 `pop {pc}` 收尾。
- 判据:对照组(短输入)必须打印 “returned normally” 且 exit 0;实验组崩溃 `si_addr` 应等于注入字节模式
  (如 `0x41414140`)——否则不是栈溢出而是 harness 副作用。

## tmux | 截图前 `/tmp/tmux-$UID` 残留 socket 目录导致 `error connecting to ...` | (2026-09-15/RouterOS 实测)
`tools/termshot.sh` 报 `error connecting to /tmp/tmux-1000/default (No such file or directory)`。
解法:`tmux kill-server`(清掉陈旧 server)后 `mkdir -p /tmp/tmux-$UID && chmod 700`,`tmux new-session` 即可。

## subagent 不继承本提示词的本地端点禁令 | 派 scanner/triage 前必须显式写"禁止 webfetch/curl 本地端点" | (2026-09-17/RouterOS 实测)
现象:hypothesis-scanner 子代理对本地 IDA MCP(`http://127.0.0.1:13337/mcp`)用了 webfetch/fetch,
该工具无超时且被本机代理劫持 → 无限挂死,子代理被终止,连带 MCP HTTP 线程失效。
规则:
- 任何子代理 prompt 必须显式包含:"本地端点一律用 tools/ida_call.sh 或 `curl --noproxy '*'`;
  禁止 webfetch/fetch 访问 127.0.0.1/localhost"。
- **派 scanner 前先把反编译/反汇编导出为纯文本**(它只读文本,不给它 grep 活体二进制),
  这样即使它乱试途径也碰不到端点。

## IDA MCP `py_eval` 批量 `ida_hexrays.decompile()` 会卡死 HTTP 线程 | 导出改用纯反汇编 | (2026-09-17/RouterOS 实测)
现象:一次 `py_eval` 遍历全部函数做 Hex-Rays 反编译,单线程 MCP 服务器被长时间占用,
连 `server_health`/`list_funcs` 都无响应(http=000),进程仍在但端点不可用 → 只能 kill 重启。
可行导出法(不卡):
1. `py_eval` 里改用 `idc.GetDisasm(head)` 遍历 `idautils.Heads()`(纯反汇编,毫秒级);1169 函数/2.4 万行瞬时完成。
2. 多二进制批量导出走 **idat.exe 批处理**(纯 dump 不需要 execute_sync,不受 idat 限制):
   `idat.exe -A -S"D:\ida_test\dump_batch.py" "D:\ida_test\<bin>"`;
   脚本内**必须先 `ida_auto.auto_wait()`**(否则只看到 167 个 import thunk,漏掉全部真实函数)。

## ida-pro-mcp 2.0 = 真多库并行(每库独立 worker) | 旧版 1.x 是端口假隔离 | (2026-09-17 定稿)
- 旧版 1.x: 单 GUI 进程多库,所有 MCP 端口服务"当前活动库"→ 端口假隔离
  (干净环境交叉验证暴露: 不同二进制双开,B 端口 survey 返回 A 的 i64);
- 新版 2.0 headless: idalib-mcp supervisor + **每库独立 idalib worker 进程**,
  工具调用显式 database=<会话id>,干净环境交叉验证真隔离 ✓
  (portal-a 与 upnpd 双开,各自特有函数正确命中/正确报无);
- 前置: idalib 激活(每 python 环境一次):
  `python.exe D:\ida9.3\idalib\python\py-activate-idalib.py -d "D:\ida9.3"`;
  Windows python 跑 WSL 路径会被当 UNC 报权限错误 → 脚本/参数一律传 D:\ 路径;
- idb_open 参数名是 input_path(不是 path);JSON-RPC 请求必须带 id 字段;
- worker 空闲 TTL 1 小时自灭,--max-workers 默认 4;
- 上游: github.com/mrexodia/ida-pro-mcp(issue #211 多实例已在本版落地)。- 生命周期: supervisor 与 workers 由 fw.sh 退出时的 ida_stop 统一回收
  (注册表 PID 逐个 taskkill,只杀 backend=worker,不动手动 GUI 实例);
  旧版 BEAT 打更保活在 headless 架构下已废弃删除。
- **max-workers 默认 4**: 开满后 `idb_open` 返回 `Maximum idalib worker count reached (4)`。
  用 `tools/ida_call.sh idb_close '{"database":"<会话>","save":false}'` 释放不需要的库(无需重启 supervisor)。

## RouterOS 相邻版本二进制存在构建布局噪声 | md5/字节 diff 不能单独定位修复点 | (2026-09-18/RouterOS 实测)
现象: 7.24.3→7.24.4 官方仅修 LTE 回归,但 `nova/bin/www` 的 `.text` 仍由 `0x17494` 缩到 `0x172a0`,
`cmp -l` 报 98353/138092 字节不同。根因: 早期一处插入/删除(本例 .text +0x4C)使后续全部内容位移,
字节级比较把“位移”也当差异。
- 单靠 md5/尺寸/字节 diff 无法定位安全修复;必须落到**具体函数指令序列**核对(或按函数级工具 radiff2,
  但该库构建噪声下 radiff2 -C 亦无输出)。
- 判别“有无真实代码改动”的快速法: 看 `.text` 尺寸**跨版本是否稳定** ——
  RouterOS 里 mtget(0x7c78)、btest(0xb6ec)、discover(0x107ec) 在 7.24.2/3/4 三版尺寸相同(7.24.2 有真实修复后即定型);
  www/net/parser/snmp 每版都变(含布局噪声)。
- 结论: 差分只能作“线索”, 结论必须来自函数级阅读。

## RouterOS 动态验证: arm64 npk 不能整机仿真,但 **CHR(x86_64) 能在 qemu 整机跑** | (2026-09-18/RouterOS 实测)
需求背景: 逻辑/认证/协议层面的缺陷(如登录限流)无法用 LD_PRELOAD 函数级 harness 验证,需要**整机**。
可行路径(本机实测):
1. 下载官方 CHR 镜像: `https://download.mikrotik.com/routeros/<ver>/chr-<ver>.img.zip`
   (45MB,zip 内为 128MB raw:2 分区;本机 CDN 对 HTTP/2 会 `stream reset`,
   用 `curl --http1.1 -C - --retry 3 --retry-all-errors` 断点续传下载)。
2. 启动(**本机 /dev/kvm 不可用** → TCG,需 `-m 768`):
   ```bash
   qemu-system-x86_64 -m 768 -smp 2 \
     -drive file=chr-<ver>.img,format=raw,if=virtio,cache=unsafe \
     -netdev user,id=n0,net=10.0.2.0/24,hostfwd=tcp:127.0.0.1:18728-:8728,hostfwd=tcp:127.0.0.1:18080-:80 \
     -device e1000,netdev=n0 \
     -vga std -monitor tcp:127.0.0.1:4556,server=on,wait=off -display none &
   ```
   TCG 启动到服务可用约 40–60s。
3. **无需串口配网**: CHR 出厂默认已启用 DHCP 客户端,ether1 会从 qemu user-net 拿到 10.0.2.15,
   故 hostfwd 直接可达 www(80)/api(8728)/winbox(8291);默认 `admin` 无口令。
4. **多源(跨地址)测试**: 再挂一个 `-netdev user,net=10.0.3.0/24` + `-device e1000`,
   guest 侧两个来源地址不同(10.0.2.2 / 10.0.3.2),可区分“按来源限流”还是“全局限流”;
   新接口用已认证的 REST 配 IP: `PUT /rest/ip/address {"address":"10.0.3.15/24","interface":"ether2"}`。
5. API 登录报文: `[len]/login` + `[len]=name=<u>` + `[len]=password=<p>` + 空字串;失败回 `!trap =message=... (<code>)`。
6. 无 sudo 时的取屏幕/控制台: CHR 控制台在 **VGA(串口无任何输出)**,不能靠 `-serial` 驱动;
   用 QEMU monitor(`-monitor tcp:...`):`screendump /tmp/x.ppm` 取帧(再 `convert` 成 png),
   `sendkey` 注入按键。无 tesseract 时无法 OCR,优先改成网络侧驱动(本条目 3)。
7. 不能 `apt install`/`mount`(sudo 需密码)时,不要指望 loop-mount CHR 分区预置配置;走 REST 配置。
- 与 tool-quirks 既有条目的关系: 上一条「RouterOS 7.x 守护进程无法离线整机仿真」仍成立(针对 **arm64 npk 用户态**);
  CHR 是**另一条**整机路径(x86_64 官方 CHR 镜像),两者互补。


## RouterOS TFTP(tftpd)动态驱动 | 传输走**新建临时端口** → 探针必须跟随应答源地址 | (2026-09-19/RouterOS 实测)
现象: 对 `hostfwd=udp:127.0.0.1:16969-:69` 发 RRQ,能收到 OACK/DATA(源端口已不是 69),
但**再往 69 端口发 ACK 却拿不到任何数据**(表现为超时,像是服务端不理会)。
根因(静态实证, `nova/bin/tftpd` `aav.0x16504`): 监听套接字在 :69,收到请求后为本次传输**新建**
套接字 `bind(本机地址, 0)` + `IP_PKTINFO`,数据从该**临时端口**发出;而 `sendto` 的目标端口仍是
客户端原始端口 → 所以「ACK 发回 69」落到的是**监听套接字**,被当作无效请求静默丢弃
(日志无痕,极易误判为“服务端拒绝 ACK”)。
解法: 驱动程序 `recvfrom` 时记下应答地址,后续 ACK/DATA 一律发到该地址(slirp 的 UDP 会话表会
把这个 host 侧端口映射回 guest 的临时端口);另注意 RFC2347 语义 —— 收到 OACK 后客户端要先
**ACK block 0** 才会迎来第一个 DATA 块。
判据: 负对照(无选项 RRQ → DATA 1,再 ACK 到**应答源地址** → DATA 2)必须先跑通,否则后面的
“无响应”都不能算服务端行为。可复跑脚本:
`RouterOS-7.24.4-arm64/artifacts/poc/tftpd-{triage,transfer}-probe.py`。

## RouterOS REST(singleton 配置)| `PATCH /rest/<path>` 报 `missing or invalid resource identifier` → 用 `/set` 子命令 | (2026-09-19/CHR 7.24.4 实测)
改单例配置(如 `/interface l2tp-server/server`、`/interface pptp-server/server`)时:
```bash
# 失败:
curl -u admin: -H 'Content-Type: application/json' -X PATCH \
  http://127.0.0.1:18080/rest/interface/l2tp-server/server -d '{"enabled":"yes"}'
#   -> {"error":400,"message":"missing or invalid resource identifier"}

# 可行:
curl -u admin: -H 'Content-Type: application/json' -X POST \
  http://127.0.0.1:18080/rest/interface/l2tp-server/server/set -d '{"enabled":"yes"}'
```
要点:①**必须带 `-H 'Content-Type: application/json'`**,否则 `415 Unsupported Media Type`;
②列表项(如 `/ppp/secret`)用 `PUT` 新增、`POST /rest/<path>/enable|disable -d '{"numbers":"<name>"}'`;
③写完后用 `GET` 复核(本机出现过 `POST .../set` 返回 `[]` 但实际已生效)。

## QEMU user-net 动态加端口转发 | monitor 里 `hostfwd_add <netdev_id> <proto>:host-guest` | (2026-09-19/CHR 实测)
启动时忘了 `hostfwd` 时不必重启 qemu:连 `-monitor tcp:127.0.0.1:4556` 发
`hostfwd_add n0 udp:127.0.0.1:17010-:1701`(第二个参数是 `-netdev id=n0`;TCP 同理)。
反向(guest→host 的 `10.0.2.2`)由 slirp 直接送到宿主 loopback,**不要**为它加 hostfwd。
另注意:monitor 是交互式 TTY,`printf '...\n' | nc` 的配合可用,但回显里会夹 ANSI 噪声。

## 截图护栏: 截图脚本必须是**整条命令**且恰好 2 个参数 | (2026-09-20/pi harness 实测)
现象: 用 `timeout … bash tools/<截图脚本> <manifest> <shot-id> 2>&1 | tail` 调用会被
`.pi/extensions/screenshot-guard.ts` 以“只接受 <shots.json> <shot-id>(收到 N 个位置参数)”拦下 ——
护栏用朴素 token 切分,把 `2>&1 | tail -6` 也算入位置参数(`timeout`/`bash` 前缀不剔除)。
解法: 只发 `bash tools/<截图脚本> <manifest> <shot-id>`,**不加**管道/重定向/`timeout`/前缀;
命令里也不要出现 `tmux capture-pane`/`send-keys` 或 `convert … report/*.png` 之类字样(同样被拦);
`write` 工具直写 `report/**/*.png` 也会被拒。产物与 `.provenance` 由脚本自身输出。

## CHR(QEMU user-net)丢弃 >~1470 字节的 UDP 单包 → 动态「无响应」先查包大小 | (2026-09-20/RouterOS CHR 7.24.4 实测)
现象: 对 hostfwd 到 guest 的 UDP 服务发**单包 >~1470 字节**(含 IP/UDP 头即超 MTU)时,
服务端**完全无应答**;guest 回 >~1470 字节的 UDP 响应同样收不到。极易误判成「服务端拒绝/不处理」。
实测阈值(CHR 7.24.4,qemu user-net + `hostfwd=udp:...`,`snmp-ber-thresholds.txt`):
请求 1442B 有响应 / 1482B 无;响应 1460B 有 / 1502B 无 ⇒ 双向 ~1470B 封顶(slirp 路径)。
判据: 把同一请求按大小二分 —— 若「无响应」只与包大小相关而与内容无关,即为传输限制,**不是目标行为**;
收口的报文解析器若按大缓冲(如 SNMP 的 8192)设计,则该尺寸以上只能静态覆盖,须在 B4/报告里注明
「emulation limitation」而非「服务端拒绝」。
规避: ①把测试拆成 <1470B 的单包;②需要大输入时改用非 slirp 通道(tap/socket netdev,本机需 root)或
guest 内部自发自收;③提高响应阈值可减少响应侧超限(如 SNMP 少发 varbind)。

## CHR(qemu user-net)guest→宿主 只有 TCP 到 10.0.2.2;UDP 到 10.0.2.2 不被投递 | (2026-09-20/RouterOS CHR 7.24.4 实测)
背景: 想给 guest 的 DNS 解析器配一个**自控上游**(改 `/ip dns servers` 或 `use-doh-server`)做
黑盒/差分实验时,宿主上的服务从 guest 看是 `10.0.2.2`。
实测:
- **TCP 到 10.0.2.2:<port> 可达宿主**(slirp 把 guest→10.0.2.2 的 TCP 连到宿主 loopback):
  `use-doh-server=https://10.0.2.2:8443/...` 后宿主 `127.0.0.1:8443` 收到 TLS ClientHello ✓。
- **UDP 到 10.0.2.2:53 不到宿主**: 宿主 `bind 127.0.0.1:53` / `bind <宿主各 IP>:53` 均收不到
  guest 发往 `servers=10.0.2.2` 的查询(客户端侧表现为 resolver 超时)。
- 指向**真实网络**上的 IP(如宿主 LAN 的 DNS `192.168.100.1`)则正常解析 ⇒ NAT 出网可用,只是
  "到宿主自身"的 UDP 不通。
规避: 需要给 guest 一个自控**UDP** 上游时,①改用 TCP 承载的协议(如 DoH);②把假上游放到真实网络
上另一端(容器/另一台机,走 NAT);③只用 guest 内可达的服务。
附带事实: RouterOS 7.24.4 的 **DoH 客户端用 HTTP/2**(ALPN=h2,`PRI * HTTP/2.0` 前言)⇒ 假 DoH
服务端必须支持 h2(可用 Python `h2` 库);`verify-doh-cert=no` 时自签证书可被接受。

## CHR 上驱动 RouterOS UPnP(`nova/bin/upnp`)动态测试的三条事实 | (2026-09-20/RouterOS CHR 7.24.4 实测)
1. **Host 头必须写成设备自身地址:端口**(`Host: 10.0.2.15:2828`)。用 `Host: 127.0.0.1:12828`
   (hostfwd 宿主端口)或任意值,SOAP/SUBSCRIBE 一律 **400 Bad Request**;换成设备地址:2828 → 200。
   写探针时别用 hostfwd 端口当 Host。
2. **SSDP(UDP/1900)经 `hostfwd udp:<host>->1900` 收不到 M-SEARCH**:SSDP socket 加入组播组
   `239.255.255.250` 并 `SO_BINDTODEVICE`,单播到 guest 接口地址不被投递 ⇒ SSDP handler 只能静态审计。
   ⚠️ **例外(2026-09-26/DS223 DSM 7.3.2-86009 实测)**: Synology 的 SSDP 守护是 `/usr/bin/minissdpd`(minissdpd),
   在 unshare+qemu-user 沙箱里用 `minissdpd -d -i lo` 启动后,**单播到 `127.0.0.1:1900` 能被投递并解析**
   (日志出现 `SSDP request: '%.*s'`)。⇒ SSDP handler 并非总是只能静态审计;先试 `-i lo` 单播再下结论。
   证据: `Synology-DS223-7.3.2-86009/artifacts/handler-sink/evidence_minissdpd.txt`。
3. **事件订阅可用宿主 listener 观察 SSRF 面**:`SUBSCRIBE` 后设备向 CALLBACK 发 TCP `NOTIFY`;
   宿主 `bind 127.0.0.1:<port>` 即可收到(guest→10.0.2.2 的 TCP 到宿主 loopback 可达)。
   注意设备拼的 `HOST:` 头是畸形的(值以 0x0A 开头),解析时别被绊住。
附:UPnP HTTP 服务端口固定 **TCP/2828**(`bind` 里 `0x0C0B0002` = AF_INET + 网络序 0x0B0C);
设备描述 `GET /gateway.xml`、SCPD `/osinfo.xml` `/wanipconn-3.xml` `/wancommonifc-3.xml`;
control/event URL 带每设备随机后缀,需先从 `/gateway.xml` 提取。

## UniFi OS Server 容器 | 本地 IPC 应用在 `127.0.0.1:11081`,无鉴权中间件 → 当作"可信本地通道"用于枚举与状态搭建 | (2026-09-21)
- **坑**: 有一大批 API 路由(`/api/authToken`(=API key)、`/api/users`、`/api/runnables/install`、
  `/api/backup/restore`、`/api/support/generate`、`/api/cloud/*`,即 Koa 里的 `ie` 路由器)在 `443` 上**全是 404**,
  容易误判为"不存在"。它们其实挂在**另一个 Koa 实例**上:该实例在 IPC server 里 `.use(JEe())`,监听
  `127.0.0.1:11081`(`E.ports.ipc`),与 `443`(经 unix socket `uos-http.sock`)是两个不同的 app。
- **解法**:
  - 枚举/联调时用 `docker exec <c> curl -s http://127.0.0.1:11081/api/<path>`;
  - 该实例**没有 router 级鉴权中间件**(实测 `/api/info`→200、`/api/consoleGroups/self`→200、`/api/users/self`→204),
    因此它是**搭建测试状态/枚举真实路由**的可信本地通道(等价于"本地已有执行权"的视角);
  - 网络侧唯一入口是 nginx `:11083` 的 `location /proxy/ipc/`(rewrite 到 `localhost:11081`),
    默认被 `colocated-access.conf` = `deny all;` 挡住,且 `auth-internal-proxy` 要求 mTLS 客户端证书;
  - 顺带: `443` 的 `/api/...` 404 与"路由不存在"要区分开——先用 `11081` 对照确认该路由属于哪个实例。
    (注: 本条的 11081 是"本地信任面"观察,**不是**可报缺陷;评估时它属于"已有本地代码执行"之后的环节。)

## WebSocket 升级探测 | 三个坑会让"路径不存在"的假象出现;101 之后必须看帧 | (2026-09-21/UniFi OS Server)
- **坑 1(最隐蔽)**: 用 `curl` 不带 `--http1.1` 时,若 TLS 协商成 **HTTP/2**,`Connection: Upgrade` 是**非法头**,
  curl 发出去的不是真正的 WS 升级 → 服务端按普通 GET 处理 → 每条路径都 **404**。**症状**: 连"肯定存在的路径"也 404。
- **坑 2**: 反代常带 WS 同源检查(本项目 nginx: `if ($provided_ws_origin != $expected_ws_origin) { return 500; }`)。
  `Origin` 缺失或与实际服务主机不一致 → **任何路径都 500**。**解法**: 让 `Host` 与 `Origin` 的主机部分一致
  (本项目 `-H 'Host: 127.0.0.1' -H 'Origin: https://127.0.0.1'`)。
- **坑 3**: 容器/映射环境里,容器内探测要用**容器内端口**(本项目 nginx 在 `:443`),宿主映射端口(18443)
  在容器内是 connection refused。
- **判读核心**: **101 Switching Protocols 不是鉴权信号** —— 有的实现先接收升级、再在处理器里拒绝,
  未知路径也会 101。必须**在升级之后**发一个 ping/文本帧,并区分:
  ①立即 EOF(=处理器关闭/未注册) ②收到 `{"type":"ERROR",...}` ③收到 PONG(连接真的活着且在处理帧)。
  裸探针要**先把升级响应头读完**,再单独读帧(一次 `recv` 可能把 `HTTP/1.1 101` 头和第一帧一起带回)。
- 附带: 本项目 WS 路由是**条件注册**的(`hasSupervisor`/`coLocated`/feature flag),所以"跑得到但立即 EOF"
  与"路径不存在"在**行为上同形** —— 必须回代码看注册条件才能定性。

## UniFi OS Server 容器 | `--network=host` 会让 unifi-core 崩溃重启(`Invalid MAC address`) → 用 bridge + 单独发布端口 | (2026-09-21)
- **现象**: 用 `docker run --network=host` 起该固件容器时,`unifi-core.service` 停在 `activating` 并**反复重启**,
  journal 里是 `crash uncaughtException: Invalid MAC address:` → `Error: Invalid MAC address:` →
  `at file:///.../service.js:4:311 / at Array.forEach / at g2 (service.js:4:4)`。发现应答器因此永不启动
  (10001 上只有 uos-discovery-client 在听)。
- **原因/解法**: 宿主网络下接口/地址布局与安装器假设不一致,`g2` 解析某个 MAC 拿到空值即抛**未捕获**异常。
  改用默认 bridge 网络 + `-p 127.0.0.1:18443:443 -p 127.0.0.1:10001:10001/udp`(只发布实验需要的端口)即可正常起。
- **附带坑**: `--network=host` 下 `ss` 会把**宿主自身**的监听(本项目实测 53/323)混进来,
  端口清点必须区分(可用 `ss -lunp` 的进程名 + 与容器内 systemd 单元对照)。

## TLS 1.3 | "握手成功"不能证明没开 mTLS → 必须发应用数据看 `certificate_required` | (2026-09-21/UniFi OS Server RabbitMQ 5671)
- **坑**: 目标开着 `fail_if_no_peer_cert=true`(要求客户端证书),但 `openssl s_client -connect host:port` 会打印
  `CONNECTION ESTABLISHED`,看起来"没要客户端证书"。原因: TLS 1.3 把客户端证书缺失的报错**推迟到握手之后**,
  服务端要等客户端发**应用层数据**才回 alert。
- **解法/判据**: 握手后**必须主动发至少一个字节的应用层数据**(如本项目发 AMQP 协议头 `b"AMQP\x00\x00\x09\x01"`),
  再读结果 —— 若得到 `SSLError: TLSV13_ALERT_CERTIFICATE_REQUIRED (tlsv13 alert certificate required)`,
  说明客户端证书是**强制**的;此时后续的 SASL/应用层认证(如默认口令 `ui/ui`)**根本不可达**。
- **同类**: 其它"传输层前置条件"也一样 —— 判"默认凭据是否可用"的顺序是: ①哪条 listener 面向网络
  ②该 listener 的传输层前置(mTLS/白名单)是否挡住认证阶段 ③最后才看用户/权限。

## AFL++ 的 qemu 模式:apt 包里没有,需自建(meson 走 pip) | (2026-09-21/Kali rolling 实测)
现象: `apt install afl++`(Kali 的 5.02c)装出 `afl-fuzz/afl-showmap/afl-tmin/afl-cmin/afl-clang-fast`,
**但没有 `afl-qemu-trace`**(只有 `/usr/lib/afl/libAFLQemuDriver.a`)⇒ 无源码二进制无法用 `-Q`。
GitHub release **无预编译产物**(v5.03c assets=0)⇒ 只能源码构建。
配方(全部免 root,除最后一步 copy):
1. `mkdir -p ~/opt && cd ~/opt && curl -LO https://codeload.github.com/AFLplusplus/AFLplusplus/tar.gz/refs/heads/stable && tar xf aflpp.tar.gz`
2. 依赖: `sudo apt install libglib2.0-dev libpixman-1-dev ninja-build`;
   **缺 meson 时不要找 apt** —— `pip install meson` 装进 pwn 环境,然后把 `.../ENTER/envs/pwn/bin` 前置进 PATH;
3. `cd AFLplusplus-stable/qemu_mode && PATH=<pwn bin>:$PATH ./build_qemu_support.sh`(约 2 分钟,产出 `afl-qemu-trace` + `libqasan.so` 于上一层目录)
   —— **注意**: 直接 `make qemu_mode` 在残留的 `qemu_mode/qemuafl` 目录存在时会报"无需做任何事"而**静默跳过**,必须直接跑 `build_qemu_support.sh`;
4. `sudo cp afl-qemu-trace libqasan.so /usr/local/bin/`;冒烟: `afl-showmap -Q -o /tmp/m -- /bin/cat <文件>`(实测 109 tuples)。
   ⚠️ **产物必须在 PATH 里**:实测把 PATH 目录里的副本藏起来后 `-Q` **不报错但不产出 coverage**(只有 `Executing ...` 一行);
   而放 `/usr/lib/afl/`(想当然的"Debian 约定目录")**同样无效** ⇒ 要么放 `/usr/local/bin/`,要么显式设 `AFL_PATH`。
   构建完源码树可整棵删除(实测:删 1.1 GB 源码树后 `-Q` 仍正常)—— 只留这两个产物即可。

## 活体审计: 动作类端点**不要**用"对值"请求(会真的执行) | (2026-09-21/UniFi OS Server 容器实测)
现象: 为验证授权矩阵,对 `POST /api/system/reboot` 发了一枚**通过鉴权**的请求 ⇒ **容器真的重启了**(不是 403 就是执行)。
做法: ①先只用**必被拒的组合**(缺角色 / 低权限掩码 / 错 id)验证门是活的;②对"可能通过"的组合,改用
**只读动作**或**干跑等价物**(如把 `reboot` 换成同链路的 `GET` 处理入点、或在 audit 日志停处断),或先把容器快照/可弃副本;
③被判"可达 + 授权通过"即可作为判据,不需要真让它执行副作用。
同源: 交付原则「最小充分」—— 能证明缺陷的最小动作就够,不要做多余操作。

## Rust stripped 二进制 | `&'static str` 是无 NUL 的连续 blob ⇒ IDA `strings`/`search_text` 不建条目;用字节扫描定位引用 | (2026-09-21/UniFi OS Server 宿主组件实测)
- **现象**: `strings -n6 bin | grep 'Failed to verify host file metadata'` 有输出,但把同一个二进制丢给 IDA 后
  `search_text`/`find_regex` 对该串 0 命中、`xrefs_to` 也 0(看似"无人引用",实为 IDA 未把它建成 string item,且 Rust 的引用形态不总是标准 `lea`)。
- **定位法(按顺序)**:
  1. `grep -abo <串> bin` 或 Python `data.find()` 拿 **rodata 偏移**;ELF 的 rodata `LOAD` 段通常是 `p_offset==p_vaddr`
     (用 `readelf -l` 确认),故偏移即虚拟地址;注意 Rust 串有**单字节长度前缀**(`(`=0x28=40 恰好是串长),真正起点在 `+1`。
  2. IDA 里用 `find_regex`(pattern=子串)拿地址(会带上前缀字节);`find_bytes` 需要 `patterns` 参数。
  3. **找引用不要只靠 `xrefs_to`** —— 直接在**原始字节**上扫描 RIP-relative `lea`:
     `8d` 且 `modrm>>6==0 && (modrm&7)==5`,无 REX 时长 6(`tgt=i+6+disp32`),有 REX(0x40–0x4f)时长 7(`tgt=i+7+disp32`);
     命中后把该地址喂给 `analyze_function` / `insn_query`(`op_any=<addr>`)。
- **代价/判据**: 本轮 `Failed to verify host file metadata for ` 在 IDA 里 0 引用,用第 3 步扫到 `0x7fb42`(`lea rax, unk_2A1617`);
  同理定位了 `next-uos-installer`、`chown/chmod/770` 等宿主侧执行点。
- **附**: `decompile`/`analyze_function` 的正文在字段 **`code`**(不是 `decompiled`),且**分页** —— 要跟 `cursor.next` 逐页取;
  大 Rust 函数(数千行)会把 `stat64` 结构误识别成通用 struct,判读要小心。

## GoReSym 的 Start/End 存在恒定偏移 bug ⇒ 反汇编 Go 二进制必须自解析 pclntab 取真值地址 | (2026-09-21/UniFi OS Server ulp-go-app Go 1.20.5 实测)
- **现象**: 对 stripped Go 二进制 `ulp-go-app`(ELF64 ET_EXEC,Go 1.20.5)跑 `GoReSym -p` 得到的 `Start/End`
  **不是有效 vaddr**:`AttachActor` GoReSym=0x3C0FDE0,但该二进制 text 段只到 ~0x1B79xxxx(0x3C0FDE0 远超映射范围),
  `objdump --start-address=0x3C0FDE0` 直接空输出。偏移为**恒定 0x222F7E0**(GoReSym 输出 − 真值)。
- **定位真值法(按顺序,自写 ~40 行 Python)**:
  1. 在二进制里找 pcHeader magic `0xFFFFFFF1`(`data.find(b'\xf1\xff\xff\xff')`),本目标在 file offset **0x2230e00**;
  2. 按 Go 1.20 pcHeader 布局解 `nfunc(+8)`、`textStart(+24)`、`funcnameOffset(+32)`、`pclnOffset(+64)`(全 uint64);
  3. functab 在 `pcHeader+pclnOffset`,每条 8B = `entryoff(u32)+funcoff(u32)`;`绝对地址 = textStart + entryoff`;
  4. 函数名 = `funcnametab[ _func[funcoff].nameoff ]`(`_func` 在 `functab_base+funcoff`,nameoff 是 `+4` 的 int32;
     funcnametab 在 `pcHeader+funcnameOffset`);
  5. objdump 用真值 vaddr(`--start-address/--stop-address`);file offset = vaddr − 0x400000(该二进制各 LOAD 段 `p_vaddr−p_offset` 恒 0x400000)。
- **判据/交叉**: 自解析出的地址落在 `.text` 段内、且函数名与 GoReSym `FullName` 一一对应 ⇒ 可信;再 `objdump -d` 用字符串
  字面量(header 名/错误串)与符号名(`net/textproto.MIMEHeader.Get`、`runtime.memequal`、`runtime.makemap_small`)交叉即可判读语义。
- **代价**: 本轮先按 GoReSym 地址 objdump 空输出、回退怀疑地址段、最后自解析 pclntab 一次性拿全 60225 个函数真值地址
  (脚本见 `<目标>/artifacts/recon/round17-ulp-go-trusthost/`)。**Go 二进制反汇编定位一律先自解析 pclntab,别信 GoReSym 的 Start/End**。

## UniFi OS Server 容器: `UOS_UUID` 必须是 UUID **version 5**,且就绪判据不能只看 `is-system-running` | (2026-09-22/UOS Server 5.1.42 容器实测)
- **现象**: 用 `docker run ... -e UOS_UUID=<uuid>` 起 `uosserver` 容器时,`unifi.service`(内置的 UniFi Network
  Application,`/usr/lib/unifi/lib/ace.jar`)会**无限 restart**,日志 `Caused by: java.lang.IllegalStateException:
  Invalid uuid version - N`,N 正好是所传 UUID 的 version nibble:
  `11111111-2222-3333-4444-…` → `- 3`;`44444444-5555-4666-8777-…` → `- 4`;
  `33333333-4444-5555-6666-…` → **通过**,`systemctl is-active unifi` = `active`。
- **后果(会带歪结论)**: app 崩着 ⇒ 它的监听端口(8080/8443/8843/8880/6789/28082、3478/5514/10003udp、
  127.0.0.1:8081/27117)**一个都看不到** ⇒ 很容易被判成"该 runnable 未安装 / 面不存在"。本项目历史上正是如此。
- **就绪判据陷阱**: `systemctl is-system-running` 会在 `unifi` 仍 `activating`(Type=notify,启动 15s+)时就返回
  `running`。若在此时 `ss -lntup`,会**漏掉**整个 Network app 的攻击面(40+ listener 里少十几个)。
  **正确就绪判据** = 你要数的那些 listener 所属的 unit 都 `is-active`(本项目至少 `unifi`)后,再 `ss`。
- **代价**: 本目标曾据此把内置 Network app 误判为"未安装"(见 notes「误判回溯」);修正后再测即复现全部端口。

## Spring-Boot fat jar:应用类在 `BOOT-INF/lib/*.jar` 里,不在顶层 | (2026-09-22/UniFi Network `ace.jar` 10.5.67)
- **现象**: `unzip -l ace.jar` 只有 **391 条目**、`BOOT-INF/classes/com/` 下仅 37 个类(启动器类);
  `web.xml` 里 `com.ubnt.ace.view.AuthFilter` 等**在顶层根本找不到** ⇒ 误以为"类被裁掉/混淆"。
  实际应用代码在 `BOOT-INF/lib/internal-dependencies.jar`(31MB)里。
- **做法**:
  1. `unzip -o ace.jar 'BOOT-INF/lib/*.jar' 'BOOT-INF/classes/**'`;
  2. 对每个候选 lib jar:`unzip -l <lib>.jar | grep -i <classname>` 定位,再 `unzip -o <lib>.jar -d app`;
  3. 无 decompiler 时 `javap -p -c <class>`(带 `-c` 才有字节码)完全够用:找 `ldc // String` 常量即可还原
     路径/端口/正则/错误串;`javap -p` 先看方法签名,`javap -c` 再读判定分支;
  4. 内嵌 OpenAPI(`webapps/ROOT/api-docs/*.json`)与 `WEB-INF/{web.xml,urlrewrite.xml}` 先读 —— 它们给出
     servlet→URL 映射、filter 覆盖范围、重写规则,比逐个 class 扫字符串快得多。
- **代价**: 本轮定位鉴权判定点 + 逐端口允许表,约 20 分钟(纯 javap)。

## Synology DSM `.pat` 是加密容器(非 zip) → 用 `synacktiv/synodecrypt` + patology 内置的 **type0 密钥**解;内部 `hda1.tgz` 实为 **XZ**,`rd.bin` 是 **LZMA+cpio** | (2026-09-24/DS223 DSM 7.2.2-72806 实测)
- **判性**: 头 4 字节 `1a ad be ef`(`u32b & 0xffffff == 0xADBEEF`);整包熵 7.999;`binwalk`/`unblob` 只会给假阳性(本项目实测全是 JBOOT/SWF 假命中)。
- **解法(可复跑)**:
  ```bash
  pip install pysodium msgpack     # 装进 venvs/unblob(提取链,勿污染 pwn)
  curl -sO https://raw.githubusercontent.com/synacktiv/synodecrypt/main/synodecrypt.py
  cat > keys.json <<'K'
  {"keypairs":[{"keytypes":[0,10,11],
    "signing":"64faba48feec6c8a2484d2489a11418a0e980317a9cc6b392f1041925b293fe0",
    "master" :"078a7529a07a998cffadb87d7378993b7d9ccfa7171f5c47f150838a6a7caf61"}]}
  K
  python synodecrypt.py -f keys.json -o fw.tar <DSM_xxx.pat>     # → 明文 tar
  ```
  密钥来源 = `sud0woodo/patology` 源码内置(`PUBLIC_KEY`/`SUBKEY`,注释 "Master key used for patch files with type 0 or type 10");
  官方结构 = msgpack header + Ed25519 签名 + secretstream(XChaCha20-Poly1305),密钥 = `crypto_kdf_derive_from_key(0x20, id, ctx, master)`。
  **pysodium 0.7.18 已含 `crypto_kdf_derive_from_key`**(patology 的 README 说"没有"是过时的;实装 `hasattr` 为 True)。
- **内部布局(DSM 7.2.2)**: `VERSION`(纯文本,含 `productversion/buildnumber/unique`)+ `hda1.tgz`(**XZ**,`tar xJf`)+
  `rd.bin`(**`lzcat | cpio -i`**,initrd,其 `usr/lib/libsynocodesign-ng-virtual-junior-wins.so.7` 藏 SPK 密钥)+
  `packages/*.spk`(**type3 加密**,需先从上面那个 .so 提 type3 键)+ `zImage`/`uboot_*.bin`/`updater`/`model.dtb`。
- **代价**: 从认识到解出 rootfs 约 15 分钟(此前该格式在项目内无先例,`binwalk` 完全无效)。

## DSM webapi 仿真:独立跑 `entry.cgi` **恒失败(102)**,注册表由 `synoscgi` 经 `WebAPIDaemonData` 注入 | (2026-09-24/DS223 DSM 7.2.2-72806 实测)
- **现象**: `qemu-aarch64 -L rootfs` 直跑 `webapi/entry.cgi`(SCGI/CGI env 齐备)会正常输出
  `Content-type: application/json` + `{"error":{"code":102}}`(API 不存在);给畸形 API 名则是 `101` ⇒ **能进解析、拿不到注册表**,
  极易误判成"该 API 不存在"。
- **根因(静态)**: `synoscgi` 内含 `entry.cgi` / `libwebapi-DSM5.so` / `/usr/syno/synoman/webapi` 与
  `Failed to reload webapi desc cache`;`entry.cgi` 只导入 `SYNO::WebEntry::{cTor,Run}` 与 `SYNO::APIRunner::SetArgs`;
  `synoscgi` 提供 `APIRunner::SetDaemonData(WebAPIDaemonData)`。⇒ 注册表不靠磁盘 `.lib` 扫描自建,**必须经 synoscgi**。
- **做法**: 起 `synoscgi --mode=scgi --task-per-worker=32`(env `SOCKET=/run/synoscgi.sock`,`LD_PRELOAD` 在**目标内**是
  `/usr/lib/openhook.so`,不是 `/usr/syno/lib/`),再按 SCGI 协议连 unix socket;或用 **proot**(纯用户态路径翻译)起 chroot。
- ⚠️ **不要** 用裸 `unshare -r -m` + `mount --bind /dev` + `chroot` + `qemu-aarch64` 跑目标 daemon ——
  本项目在 **WSL(Kali)** 上触发了**内核级重启**(uptime 归零);安全工作法是上文已有的 **bwrap** 条目,或 `tools/emu_run.sh`(qemu `-L`,无 chroot)。
- **实测可用的安全跑法(bwrap,替代上面被禁的组合)**: `bwrap --unshare-all --die-with-parent --new-session --ro-bind <rootfs> / --dev /dev --proc /proc --tmpfs /mnt --bind /usr/bin/qemu-aarch64 /mnt/qemu --tmpfs /tmp --tmpfs /run <…> /mnt/qemu <目标>`。rootfs 作 `/` ⇒ 绝对路径可解析;静态 `qemu-aarch64` 挂到 `/mnt/qemu`(rootfs 只读时须先 `--tmpfs /mnt` 才能在其下建文件);**不 bind 宿主 `/dev`**。实测 `nginx -v`/`entry.cgi`/`python3` 正常,WSL 无异常、无挂载残留。
- **`synoscgi` 起得来但不应答(部分仿真)**: 它能建 `/run/synoscgi{,restfulapi,-fastapi}.sock`,但标准 SCGI 请求(参数照 `etc.defaults/nginx/scgi_params` 补全)**读超时无响应**。`qemu-aarch64 -strace` 显示它反复 `socket(PF_UNIX,SOCK_DGRAM); connect = ENOENT`(连不存在的协作 socket)且 `setresgid(...,1,...) = EINVAL`(user namespace 里降权失败)⇒ **必须把 `synotaskmgr`/`synotokenmgr`/`synocached`/`synocgid` 一并起进同一共享 `/run`**,否则 webapi 无法动态验证。
- **✅ 能使 `synoscgi` 应答的完整配方(实测打通)**: 用 `unshare --user --map-users 0:<me>:1 --map-users 1:100001:65534 --map-groups 0:<me>:1 --map-groups 1:100001:65534 --mount --pid --fork`,rootfs 作 `/`,宿主 `/dev/{null,zero,random,urandom,tty}` 做**单文件 bind**(userns 里 **不能 mknod** 设备节点,会静默失败),`/dev/shm`+`/dev/pts` 用 tmpfs,挂 `/proc`,最后 `chroot`。**必须映射 uid/gid 1(=`system`)与用户组 100/101**,否则 worker 的 `setresuid/-gid(...,1,...)` 与 `setgroups` 报 `EINVAL` 而自毁(现象: synoscgi 只 listen、永不 accept)。
- **登录/加密前置(DSM 7.2.2)**: `SYNO::Auth::Login` 要求 `SYNOSystemGetBootStep()==4`,该值在 **`/run/boot_seq.cfg`**(内容 `STEP=4`,或跑 `/usr/syno/bin/synobootseq --set-boot-done`),否则 login 恒返回 **499**;`SYNO.API.Encryption getinfo` 需要 `/usr/syno/etc/ssl/cgi.key/{private,public,modulus}`(用 guest `openssl-3` 生成即可,`cgi-keygen` 是 bash 脚本、在本仿真里 bash 会挂),会话密钥用 `/usr/syno/sbin/synocgitool --check_session_key` 生 `/usr/syno/etc/private/session/pair.key`;daemon 日志走 `/dev/log`——**要自己在 guest 里绑一个 SOCK_DGRAM 收日志**,否则看不到失败原因。
- **本仿真里 guest `bash` 不可用**(`xmalloc: hashlib.c/...` 崩),脚本编排请用 guest `python3`(存在)。
- **Synology SPK 解密(应用层入口)**: SPK 与外层 PAT 同为 Synology 加密容器。type3 signing/master 键在 **`rd.bin` 解出的 `libsynocodesign-ng-virtual-junior-wins.so.7` 的 `get_keys`**(内部 `sub_B750`,keytype=3 分支 → 32B 常量 `0x23AE0`/`0x23B08`);用 `synodecrypt.py -k 3 -f keys3.json` 解(header 签名通过即键正确)。SPK 外层是 tar(`INFO`+`package.tgz`),⚠️ **`package.tgz` 实为 XZ**(`tar xJf`)。
- **让应用 webapi 在 DSM 仿真里可见**: synoscgi 的 desc cache 只扫 `/usr/syno/synoman/webapi/*.lib`(depth1),不自动扫 `/var/packages/*/target/webapi/`;把应用的 `.lib` **复制进核心 webapi 目录** + 应用放到 `/var/packages/<Pkg>/target/` 后,`SYNO.API.Info` 即列出该应用 API(实测 FileStation 48 个)。
- **抓"CGI→本地守护"的域套接字请求**: 目标把指令发到某个 unix socket(如 `/tmp/<daemon>.sock`)时,可在仿真里**用自己写的监听占据该路径**并 `chmod 777`(否则以非 root uid 运行的 worker 连接会 `EACCES`,现象是 API 返 "failed to access domain socket"),即可**原样捕获**它要发的 JSON/帧(注意可能有长度前缀,如 `00 00 01 2b`)。这是把"闭源守护之间到底传了什么"变成**可读原文**的低成本手法(2026-09-24/Synology QuickConnect `webrtcproxy` 实测)。

## DSM 仿真:认证后 webapi 调用的两道隐藏门 —— **SynoToken 二次校验** 与 **per-app 特权(111)** | (2026-09-25/DS223 DSM 7.3.2-86009 实测)
- **现象**: 用 `SYNO.API.Auth` 登录拿到合法 `sid` 后,把 `sid` 放进 `Cookie: id=<sid>` 调任何 authLevel≥1 的 API,**一律 `{"error":{"code":119}}`**;
  `synocgid` 日志还会对 `abn="1"` 的 admin 会话打一条 `session/session.cpp:581 AbnormalAccess:[admin] …` —— **极易误判成"会话异常/仿真没打通"**。
- **根因**: `SynoCgiCheckSessionFileEx`(libsynocgi 0x4a8d0)返回 positive `auth`(1=普通用户 / 2=管理员)后,
  **synoscgi 侧 `SynoCgiIsAuthorizedEx2`(libsynocgi 0x292e4)仍会调 `SynoCgiIsTokenValid` 校验 SynoToken**,失败返 `-7` → 119。
  `AbnormalAccess` 日志只是告警(`abn` 字段=24 用于打印),**不是拒绝原因**(同带 token 时 abn=1 也成功)。
- **做法**: 登录时带 `enable_syno_token=yes`,后续请求**同时**带 `SynoToken=<synotoken>`(query param)或头 `X-SYNO-TOKEN`,与 `Cookie: id=<sid>` 并用。
  ⚠️ 登录密文 `__cIpHeRtExT` 里的 `__cIpHeRtOkEn` 是**时间戳**,窗口约 **360s** ⇒ 长扫面要么缩短,要么后台定期重生成 ck 文件(本目标用 `refresh_ck.sh`),要么中途重登。
- **第二个门(应用库加载失败伪装成"per-app 特权 111")**: 随包应用的 API(FileStation 等)即使 admin 也回 **`111`**。
  **真正原因 = `.so` 没加载成**(日志 `APIRunner.cpp:942 cannot open library: /var/packages/FileStation/target/webapi/SYNO.FileStation.Info.so. error = libsynorelayd.so.7: cannot open shared object file`)⇒ **111 = "cannot open library"**。
  `libsynorelayd.so.7` 只在 **QuickConnect 包**(`/var/packages/QuickConnect/target/usr/syno/lib/`)、该目录不在 loader 路径 ⇒ 连同其它缺失的 `libsynoanalytics.so`/`libsynoc2fs-*.so`/`libopentelemetry_*.so`/`libreplace-samba4.so` 拷进 `/usr/lib` 后 **FileStation 立即可达**(`SYNO.FileStation.Info.get` 成功)。
  ⚠️ 别按"per-app 特权"去改 `/etc/synoappprivilege.db`(Type/ID 多组合+`synoapppriv_updater` 空跑均无效)—— **先看 `APIRunner.cpp:942 cannot open library`**。
  反例: **`appPriv` 为空的应用 API 装包后即可达**(`SYNO.Core.ISCSI.{LUN,Target}.list`、`SYNO.OAUTH.Common.get`、`SYNO.SupportService.Setting.get`、`SYNO.Storage.CGI.Storage.load_info`:admin 成功、testuser `105`)。
- **应用 API 装包+补库后的数据前置**: FileStation `list_share` 回 `401`(`/usr/syno/etc/synoshare.db` 缺 → `share_db_open.c:40 SLIBCBdbOpen` 失败);`synoshare --add` 需**已注册卷**(光建 `/volume1` 目录不认,"Share path is invalid")⇒ 审 FileStation 路径类 sink 前须先建存储/卷子系统。

- **代价**: "登录成功但认证调用全 119" 这条曾卡住整整一轮(前目标 §18 遗留);根因靠"把 `SynoCgiCheckSessionFileEx` 的**最终返回值 patch 成常量**(0/1/2/-1/-2)再看响应码"定位。

## DSM 仿真:让 `synoshare --add` 通过卷检查 —— `/run/synostorage/volumetab` 的格式 | (2026-09-25/DS223 DSM 7.3.2-86009 实测)
- 共享夹创建链: `synoshare --add` → `SYNOShareCreate`(libsynoshare) → `ShareCheckVolumeAlive`(`SYNOMountVolInfoGet` libsynosdk → `SLIBVolumeTableEnum` @0x2b160)。
- **`/run/synostorage/volumetab` 是逐行文本**,每行用 `SLIBCStrTok(line," \t\n")` 切出**恰好 6 个 token**,否则整行被丢弃(`Wrong volume table row format`):
  `devpath  mountpoint  status  task  location  cnr-type`
  - status: `crashed`→2 / `not_support`→3 / 其它→1
  - task: `building`→2 / 其它→1
  - location: 只接受 `loc-internal` / `loc-usb` / `loc-esata` / `loc-eunit`(其它报 `Invalid location`)
  - cnr-type: `cnr-cross`→1 / `cnr-internal`→2 / `cnr-eunit`→4 / 其它→8
- 要越过 `ShareCheckVolumeAlive`,还需 `/volumeN` 是**真实挂载**(`mount -t tmpfs tmpfs /volume1` 即可,光建目录会 `Error: Share path is invalid.`)。
- **仍差一步**: `/usr/syno/etc/synoshare.db` 是 Berkeley DB,`SLIBCBdbOpen` **只开不建**(`bdb_open.c:51`/`0x0700`),`synoshare --build` 亦先开库 ⇒ 须先有合法 BDB(schema 未知,未收口)。

## DSM 仿真:`synoshare.db` 是 BSD `dbopen()` 库,`dbopen` 在 **libsynocore.so** 里 | (2026-09-25/DS223 DSM 7.3.2-86009 实测)
- `synoshare`(以及 FileStation 权限)读 `/usr/syno/etc/synoshare.db`(rootfs 原始不含,DSM boot 生成)。`SLIBCBdbOpen`(bdb_open.c)调用的是 **BSD `dbopen(path,flags,mode,DB_HASH,info)`**;
  该符号 **只在 `libsynocore.so` 定义**(`nm -D --defined-only /usr/lib/libsynocore.so | grep dbopen`),`libdb-5.3.so` 里没有。
- **造空库**(guest python3,无需编译):
  ```python
  import ctypes
  l = ctypes.CDLL('/usr/lib/libsynocore.so')
  l.dbopen.restype = ctypes.c_void_p
  l.dbopen.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
  db = l.dbopen(b'/usr/syno/etc/synoshare.db', 0x42, 0o600, 0, None)   # O_CREAT|O_RDWR, DB_HASH
  ```
  (不必调 close;进程退出后文件已在磁盘上,8192B 空哈希库。)
- 之后 `synoshare --build` / `--add <name> <desc> <path> NA '' '' 1 0` / `--setuser <name> RW = user1,user2` 正常;**FileStation 随之可达**(`List.list folder_path=/<share>` 对 admin 返回真实列表)。
- 完整 FileStation 前置三件套: ①`mount -t tmpfs tmpfs /volume1` ②写 `/run/synostorage/volumetab`(见上一条)③本条的 BDB。

## DSM 仿真:FileStation 的 DSL ACL 依赖内核 `system.synoacl` xattr → 用户态仿真不可用 | (2026-09-25/DS223 DSM 7.3.2-86009 实测)
- 现象: `SYNO.FileStation.Extract.start`/`Compress.start` 恒 `401`/`105`,syslog:
  `synoacl_perm_get.c:31 failed to get perm on [<path>], errno=Function not implemented` → `webfmmisc.cpp:725 Failed to get the ACL Privilege ... reason=[8100]` → `Extract.cpp:1695 Can't access <path>`。
- 原因: `getxattr(path,"system.synoacl")` 在本环境返回 **ENOTSUP(95)**(guest 实测:`user.*`/`security.*` 正常,`system.posix_acl_access` 与 `system.synoacl` 均 ENOTSUP)。Synology 的 `system.synoacl` 是其**内核补丁**提供的 xattr 命名空间,用户态(qemu-user)无法伪造。
- 附带的第二个门槛: `Extract.start` 还需 `/tmp/DSMTaskMgr/<user>` 可写(`taskutil.cpp:93 mkdir ... failed(Permission denied)`),可手工 `mkdir -p /tmp/DSMTaskMgr && chmod 777` 缓解,但 ACL 那条过不去。
- ⇒ **结论: FileStation 里依赖 DSM ACL 取权限的写/操作类 handler 无法动态验证**,只能静态复核。FileStation 只读类(`List.list`、`Extract.list`)不受影响,可正常用。

## DSM FileStation 解压:`ExtractArchiveUsing7z` 走**固件自带 `/usr/bin/7z`**;该 7z 会把 `..`/前导 `/` 剥离(无 Zip-Slip) | (2026-09-25/DS223 DSM 7.3.2-86009 实测)
- 静态: `SYNO.FileStation.Extract.so::ExtractArchiveUsing7z` 用 `SLIBCPopenv("/usr/bin/7z","rB",argv)`,argv = `7z` +(`x`|`e`)+ `-o<dest>` + `-p<pass>` + `-aos`/`-aoa` + 可选 `@filelist` + archive。
- **可绕开 ACL 门槛直接验证**: guest 里直接跑同一个 `/usr/bin/7z x -o<out> <archive>`,塞 `../../../../tmp/X`(tar/zip)与绝对路径条目 → 全部落到 `<out>/tmp/X`(`..`/前导 `/` 被剥),`/tmp/X` 不存在。
- ⇒ 用该 7z 的解压路径**不构成 Zip-Slip**;另一方面 FileStation 依赖 DSM ACL(`system.synoacl`)的 handler 在本仿真不可动态验证(见上一条)。

## DSM ftpd(SmbFTPD 2.7 Synology fork)仿真起场 + 判"fork 是否受上游 CVE 影响"须先看 primitive | (2026-09-26/DS223 DSM 7.3.2-86009 实测)
- **起场**(纯 state,guest 内):`/bin/ftpd -D -s /tmp/smbftpd.conf`(用一个最小启动器在 guest 里起 ftpd 并 `sleep` 保活;可选:进程内先 bind 一个 `/dev/log` DGRAM 桩以收 syslog)。配置项名与类型见二进制表 `off_34908`(`ChrootSet/SecurityPolicy/Port/...`)。
- **必须设 `SecurityPolicy nosecure`**,否则 `USER` 恒 `504 TLS/SSL protection required.`(门 = `!(security_policy & 2 | ssl_active)`,在 `cmd_user.c`/`cmd_pass.c` 同款);认证后命令才可达。
- **沙箱判读**: 该 fork **不 import `chroot(2)`**(仅 `chdir/seteuid/setegid`)⇒ 沙箱是**用户态虚拟根(`PATH_SMB_FTPD_ROOT="/tmp"`)+ 共享夹包含性**(`share.c::sub_B5A4`),不是内核 chroot;`ftpUserChroot=yes` + `/usr/syno/etc/ftpchrootrule.conf`(`user:<uid>:<share>`,格式见 `SYNOFTPChrootRuleFormat`=`"%s:%lu:%s"`)切到"以共享夹为根"。
- **判"是否受某 FreeBSD ftpd CVE 影响"**: 先核 **该 CVE 的 primitive 在 fork 里是否成立**,不能凭版本号推。实例: CVE-2020-7468(FreeBSD-SA-20:30,chroot 后 `chdir(home)` 失败仍存活 ⇒ 逃逸)的根因是**内核 chroot 逃逸**;本 fork 无 `chroot` 调用 + `chdir` 失败即 `end_login()` 复位 `dword_385F0` ⇒ **primitive 不成立**。
- **上游源可 diff**: SmbFTPD 源码 `https://www.twbsd.org/download/smbftpd/smbftpd-<ver>.tar.gz`(2.10 可得;`download.php` 返 500,走 `/download/` 直链)。`2.9` 移植了 CVE-2020-7468。

## avahi/mDNS(及任何忽略 loopback 的组播守护)的本地投递: 隔离 netns + veth 对 | (2026-09-27/DS223 DSM 7.3.2-86009 实测)
- **现象**: 直接向 `127.0.0.1:5353` 单播/组播投 mDNS,`avahi-daemon` **不处理** —— avahi 不把 `lo` 当作 mDNS 接口(默认 `allow-point-to-point=no`;`use-iff-running` 也不会选 lo)。
- **解法**: 让 guest 跑在**独立 netns**(`unshare --net`)里,内部建一条 **veth 对**(`veth0=10.99.0.1` / `veth1=10.99.0.2`,两端 up)→ avahi 在 `veth0` 上 join 组播组;探针从 `veth1` `bind(10.99.0.2:5353, SO_REUSEADDR|SO_REUSEPORT)` 发 mDNS 到 `224.0.0.251:5353`(设 `IP_MULTICAST_IF=10.99.0.2`)⇒ avahi 收到并解析(实测: 伪造 `_raop._tcp` 公告被处理,写出 `/tmp/avahi.raop.list`,内容与所发 TXT 逐字一致;1800 包 fuzz 存活)。
- **复用件**: 在既有 DSM `run_syno_ns.sh`(user-ns + `qemu-aarch64`)基础上加 `--net` + veth 建链即可;探针 = 发 mDNS 公告/畸形的 guest 脚本(来源: DS223 DSM 7.3.2-86009 2026-09-27)。
- **关键点**: 仅 `--net` + lo(哪怕把 lo up)仍不行 —— 必须有一条**非 loopback 且 up** 的接口承载组播;`avahi-daemon` 起场用 `--no-drop-root --no-rlimits -f <conf>`,conf 里 `enable-dbus=no`,并先 `rm -f /run/avahi-daemon/pid`(rootfs 工作副本里的 `/run` 是真实目录,pid 文件会残留导致 "Daemon already running" 误判)。

## DSM 的 `SLIBCExec`/`SLIBCExecl`/`SLIBCPopen` 是 **execv(argv) 封装,非 shell** ⇒ 命令注入面显著小 | (2026-09-27/DS223 DSM 7.3.2-86009 实测)
- **判据(反编译 `libsynocore.so.7`)**: `SLIBCExec(cmd,arg1,...)` → `SLIBCExecl(cmd,0xB9,args...,0)`;`SLIBCExecl` 把 varargs **组装成 argv 数组**;最终 `SLIBCExecvc(cmd,argv,wait)` 里是 `fork + execv(cmd,argv)`(不是 `sh -c`)。`SLIBCPopen(cmd,mode,arg1,...)`/`SLIBCPopenv(cmd,mode,argv)` 同样是 argv + `execv`(管道只接 stdout/stderr)。
- **含义**: DSM 里形如 `SLIBCExec("/usr/local/bin/smbclient","--query-smb1_support",<uservalue>,"-N",0)` 的调用**不会**因 `<uservalue>` 含 `;`/反引号/`$()` 而命令注入 —— 用户值整体作为**单个 argv** 传入。命令注入要成立,必须**另有**直接 `system()`/`popen()`(shell)调用且拼进用户数据。
- **方法**: 判一个"exec 类 sink"是否为 shell 前,先在 `libsynocore.so.7` 反编译 `SLIBCExec`(0x16c88)/`SLIBCExecl`(0x16ca4)/`SLIBCExecvc`(0x16ae0)/`SLIBCPopen`(0x18adc)/`SLIBCPopenvc`(0x18650)确认;再对目标 .so 扫描 `nm -D | grep ' U system@\\| U popen@'` 找**真 shell** 调用(DSM 自研库中很少:实测仅 `libesynoscheduler.so`(任务调度,admin-only 任务的 system())等极少数)。
- **注意**: 这**不**等于"DSM 无命令注入" —— 只是把 `SLIBC*` 这一大类从嫌疑里剔除;`system()/popen()` 的调用点仍要单独审其命令串来源。

## DSM FileStation Share-Link 动态研究前置(sharing.db + create 门槛)| (2026-09-27/DS223 DSM 7.3.2-86009 实测)
- **DB**: `/usr/syno/etc/private/session/sharing/sharing.db`(全局单例;schema `/usr/syno/share/synosharing/db_schema/{1,2}.sql`,表 `entry(...,owner_uid,project_name,hash,data)`/`session`/`token`;`PRAGMA user_version=2`)。目录须对**被冒充用户**可写(否则 `base_db.cpp:251 init failed` / `sharing_db.cpp:49 Execute(PRAGMA user_version)` 失败)。
- **请求**: 记带 `HTTP_HOST`(否则 `synosharingurl.cpp strHost.empty()` → `json_value.cpp JSON_ASSERT abort`,响应为空)。
- **已知墙**: 即便补 `HTTP_HOST` + `synoinfo.conf external_host_ip`,`SYNO.FileStation.Sharing.create` 仍回 **407** —— 根因是 `AddNewLinks→SharingLinkUtils::HasSharingPriv` / `CheckSharingPrivilege→WfmLibGetPrivilege` 需要**独立的 per-share FileStation 'share' 特权**(非普通 RW);只 `synoshare --add` 不授予它;**admin 在 HasSharingPriv 里被直接放行**(`SLIBGroupIsAdminGroupMem==1⇒1`),故 admin 的 407 来自其后的 `WfmCheckPath`/`SLIBShareAdvancePrivGet`(共享夹 advance/sharing 特权未建);**不能**用"直接向 `entry` 表插入行"的捷径 —— 手造行 `data='{}'`/手拼 LinkInfo JSON 均被 `GetByHash`/`Delete` 拒(**连 owner 自删也 401**),故无法用插行法测对象级授权。
- **登录**: `SYNO.API.Auth` + `dsm_encrypt_params.py` 生成 ck;口令需按 `/etc/shadow` 实际值起 ck(本例两账号口令均为 `admin`,不是文档默认的 Adminpw123/Testpw123)。
- 复用件: 探针 `rootfs/.emu/probe_sharing_idor.py`(建场 + admin/testuser 登录 + 插行 + delete/getinfo 对照)。

## radare2(发行版包) | `r2pm -ci r2ghidra` 编译失败 = **r2/sdb/r2ghidra 三方 ABI 错位** ⇒ 反编译别指望 r2,走 IDA | (2026-09-26/SRM-RT6600ax 立项实测)
- **现象**: 在 `src/R2TypeCompat.h:19` 报 `error: 'sdb_const_getf' was not declared in this scope`。
- **根因(本机逐条实证)**: 该处是版本分支 —— `#if R2_ABIVERSION >= 143` 走新 API `r_type_func_args_count(tdb,name,argc)`,`#else` 走旧 `sdb_const_getf(tdb,nullptr,"func.%s.args",name)`。本机三个事实**同时**落在缝里:
  - `R2_ABIVERSION 24`(`/usr/include/libr/r_lib.h:24`)⇒ `24>=143` 为假 ⇒ 取 `#else`;
  - 该分支要的 `sdb_const_getf` 在 `/usr/include/libr/sdb/sdb.h` **不存在**(只有 `sdb_const_get`/`sdb_const_get_len`;`SDB_VERSION "2.2.0"`);
  - 新分支要的 `r_type_func_args_count` 本机是 **2 参**(`r_util/r_type.h:46`),r2ghidra 按 **3 参**调 ⇒ 即便把 ABI 判断改成真也会挂。
- **包装层根因**: 本机是**发行版包**(`dpkg -l` → `radare2 6.0.4+dfsg-1`,而 `r2 -v` 自称 `6.0.5`;`R2_PREFIX=/usr`、`R2_INCDIR=/usr/include/libr`),`r2pm` 拉的是 **r2ghidra git HEAD**(2026-09-22);且 r2pm 的 r2ghidra 仓库 **无任何 tag**(`git tag` 空)⇒ 没有可 pin 的匹配版本。
- **解法(按代价)**: ①**不修,反编译走 IDA**(`tools/ida_start.sh`,项目钦定)—— 实际采用;②自建 git 主线 radare2 让头文件对齐(重);③本地 patch 该分支并迭代(编译在首个错即停)。
- **半成品残留的清理**: `r2pm -ci r2ghidra` 会**先装成功**依赖 `r2ghidra-sleigh`(预编译 zip ~10M,解到 `~/.local/share/radare2/plugins/`),再让主包失败。卸载 `r2pm -u r2ghidra-sleigh` + 删 `~/.local/share/radare2/r2pm`。
- **判据(别误判成遗留)**: 判断一个 r2pm 包是不是本次装的,看 `~/.local/share/radare2/r2pm/pkg/<name>` 里的 **`InstallationDate`**;zip/目录里的旧 mtime 来自发布包的**远端 Last-Modified**(`wget -c`/`unzip` 会保留),不能当残留证据。
- **复用场景**: 本机要用 `radare2` 做 **C 反编译**时直接跳过;`r2` 仍可做反汇编/分析,反编译走 IDA / `objdump`(`methodology.md` 静态导引行)。

## busybox applet 软链接 | `cat >`/重定向写到 `/bin/<applet>` 会**顺着软链把 busybox 本体覆盖** | (2026-09-27/SRM-RT6600ax emu 实测)
- **现象**: 为给缺 `sed` applet 的 busybox 打补丁,执行 `cat > <rootfs>/bin/sed <<...` —— 而 `/bin/sed` 是 **指向 `busybox` 的软链接** ⇒ **busybox 二进制被覆写成文本脚本**,随后 `/bin/sh`、`/bin/cat` 等**全部 applet 一起报 `Exec format error`**。
- **解法/预防**: 先 `[ -L path ] && rm -f path`,再创建独立文件;或写到新路径后 `mv`。判据: 事故后 `file <rootfs>/bin/busybox` 不再是 ELF 即中招,从固件重新解出该文件即可复原。
- **附带(同次实测)**: 某 SRM rootfs 里 **`hda1.tgz` 的 `/bin/busybox` 行为是 wget**(文件中无 busybox 字符串,applet 名全部落到 wget),而 **initrd(`rd.bin`)里的 busybox 才是真的**;
  用 initrd 那份覆盖后 `sh/echo/awk` 可用,但仍**缺 `sed`/`id`**。⇒ 在 user-ns + qemu 仿真里跑依赖 `popen("...| sed ...")` 的目标时,必须先把 shell 工具链补齐,否则目标会静默产出**空结果**(本例: 口令重置票面落库为空),极易误判为"目标无此行为"。

## 批量跑仿真时:① `pkill -f "<串>"` 会**自匹配杀死自己**;② 挂死的 qemu 需 `timeout -k`;③ 判"挂起"vs"崩溃"看 strace 尾巴 | (2026-09-27/SRM-RT6600ax emu 实测)
- **① 自匹配**: `pkill -f "unshare --user"`(或 `-f "qemu-aarch64"`)会把**执行该命令的 shell 自己**也匹配上(它的 cmdline 含该字符串)⇒ 命令被自己 SIGTERM 杀(退出码 143),清理没做完。**做法**: 用字符类让正则不自匹配 —— `ps -eo pid,cmd | grep "[q]emu-aarch64" | awk '{print $1}' | xargs -r kill -9`。
- **② 挂死进程**: `timeout 20 <runner>` 对**卡在系统调用里**的 qemu 常常**杀不掉**(SIGTERM 被忽略时 `timeout` 会一直等)⇒ 必须 `timeout -k 5 20 ...`,否则工具级超时先到、留下**孤儿 qemu 进程**(实测有挂了 36 分钟仍在的)。
- **③ 判"挂起"还是"崩溃"**: 别只看"没输出"。`QEMU_OPTS="-strace"` 看尾巴 —— 本例 `tgkill(1,1,SIGIOT)` + `qemu: uncaught target signal 6 (Aborted)` 说明是 **abort 崩溃**而非阻塞;两者后续处置完全不同(崩溃要先排除 `JSON_ASSERT` 类缺状态产物)。
- 实例(2026-10-04 二次踩): 同一串也会匹配到 `pgrep`/`bash -c` 的**自身**命令行 —— 排查时先看 `pgrep -af` 输出里有没有自己那条命令。

## Synology `synowebapi --exec` 是**特权本地调用**(绕过 authLevel),**不能当"预认证"oracle** | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测)
- **现象**: `synowebapi --exec api=SYNO.Core.User method=list version=1`(该 API `authLevel=1`,仅 admin)
  **无任何凭据**即回 `{"success":true}`;`SYNO.Core.System.info` 同。
- **判据**: `synowebapi` 的 `--exec` 是本地 runner(root)身份,派发时**不施加** webapi 的 `authLevel`/`allowUser` 门。
  ⇒ 它**只能**用来验"handler 逻辑/数据流",**不能**用来判"预认证能不能到达""鉴权门是否生效"。
- **用途对照**:
  - ✅ 适合: 拿 handler 的真实行为当 oracle(例: 让 handler 走到 sink,配 sink 记录器抓命令原文)——**前提是该 API 本身 authLevel=0**,
    或者你只关心**授权通过之后**的处理逻辑。
  - ❌ 不适合: 任何"未认证/低权能不能做 X"的结论(会得到**假阳性**)。
  - 想测鉴权语义: 用真实 HTTP 入口(webman CGI 直调 / per-API CGI,如 `FileStation/info.cgi` 无会话回 `105` = 门真的在跑),
    或 `--send cred_id` 带会话。
- **同类风险(通用)**: 任何"本地/特权 CLI 派发器"当 oracle 时,先做**越权对照**(拿一个已知需要权限的动作试一次)——
  通了就说明它是特权通道,不能用它下"预认证可达"的结论。

## Synology `webapi/entry.cgi` 在仿真里恒回 `102` 的真因: **CGI 的 cwd 必须是 `/usr/syno/synoman/webapi/`** | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测)
- **现象**: 独立跑 `entry.cgi`(全规范 CGI 环境)对**任何** API 都回 `{"error":{"code":102}}`;而 `synowebapi --exec` 能派发同样的 API。
- **根因(host/qemu 两侧 strace 对照)**: 两者都先读 `/usr/syno/synoman/webapi/lib.def`,随后
  `newfstatat("SYNO.Core.User.lib")` —— **用的是相对路径**。
  `synowebapi` 先 `chdir("/usr/syno/synoman/webapi/")` ⇒ 命中;`entry.cgi` **不 chdir** ⇒ cwd 不在该目录 ⇒ `ENOENT` ⇒ 注册表空 ⇒ **102**。
  真机上 Apache 会以该目录为 CGI 的 cwd。
- **解法**: 跑 `entry.cgi` 时给它正确的 cwd —— `sh -c 'cd /usr/syno/synoman/webapi && exec ./entry.cgi'`(或让运行器把 cwd 设到该目录)。
- **解锁后的收益(实测)**: `.lib` 风格 API 全部可用,**且带真实鉴权语义** ——
  `SYNO.Core.User.list`(authLevel1)→ **119**;`CaptivePortal.NoAuth.set_ip`(authLevel0)→ **4302**(参数校验,已进 handler);
  `SYNO.Entry.Request` → **110**(缺参数)。⇒ 这是能同时验"预认证可达"与"鉴权门生效"的**真实 HTTP oracle**。
- **推论(通用)**: 仿真里"某 CGI 恒回 API 不存在/未知错误"时,**别先怀疑缺守护进程**;先查它是否用了**相对路径**读配置
  (对照一个能正常工作的同族二进制 + 看它的 chdir)。本例若误判成"缺守护 socket",会白追很久。

## 找本机工具/文件:先走"便宜阶梯",禁止全盘扫 | 来源: 本工作区(WSL) | (2026-09-27)
**现象**: 找 AFL++ 时用了 `find / -name "afl-compiler-rt.o.c"` 与 `find / -type d -iname "AFLplusplus*"` —— WSL 上可能几分钟到几十分钟,
还会翻到别的项目目录;而工具其实在 `AGENTS.md` 明写的 `/usr/local/bin/`(`afl-fuzz`/`afl-qemu-trace`/`afl-showmap`)与源码 `~/opt/AFLplusplus-stable/`。
**做法(便宜→贵,命中即停)**:
```bash
command -v afl-fuzz afl-clang-fast afl-qemu-trace
dpkg -L afl++ 2>/dev/null | head
dpkg -L afl  2>/dev/null | head
ls /usr/bin/afl-* /usr/local/bin/afl-* 2>/dev/null
find ~/opt ~/src /opt /usr/local -maxdepth 4 -iname 'AFLplusplus*' 2>/dev/null
```
**确需从根搜**(先问用户): `find / -xdev \( -path /mnt -o -path /proc -o -path /sys -o -path /dev -o -path /run \) -prune -o -iname '<pat>' -print`
**不碰 Windows 盘** `/mnt/c`、`/mnt/d`(9p/drvfs 递归 stat 极慢)。


## 本机交叉编译 aarch64 并与 guest(固件)共享库动态链接 —— fuzz / harness 的通用底座 | (2026-09-27/SRM-RT6600ax emu 实测)
- **背景**: 无 `binutils-aarch64-linux-gnu`(apt install 需 sudo,本机无免密 sudo),但 **llvm 的 `ld.lld` 存在**:
  `/home/juryorca/.cache/solana/v1.51/platform-tools/llvm/bin/ld.lld`(LLD 19;Rust toolchain 下也有一份)。
- **可行命令**(把 guest 的 `libtins.so.4.1` 链进自家 harness):
```
clang --target=aarch64-linux-gnu --ld-path=<ld.lld> -nostdlib -nostartfiles -fno-stack-protector \
  -fexceptions -O2 -Wl,--dynamic-linker=/lib/ld-linux-aarch64.so.1 -Wl,-e,_start \
  -Wl,--unresolved-symbols=ignore-all -Wl,--no-as-needed h.cc -o h \
  -L$R/lib -l:libtins.so.4.1 -L$R/usr/lib -l:libstdc++.so.6 -l:libgcc_s.so.1 -l:libc.so.6
```
- **两个必踩的坑**:
  1. **guest 的 `.so` 被 strip 了 section header**(Synology 固件常见)⇒ `ld.lld` 读不到它们的 `.dynsym`,拿库解符号会报 `undefined symbol`。
     解法: **`--unresolved-symbols=ignore-all`** 跳过;运行时由 linker 按 NEEDED 解析(实测可跑)。
  2. **没有 CRT** ⇒ 自写 `_start` + 裸 syscall(aarch64: `read=63 write=64 lseek=62 openat=56 exit=93`,svc #0)。
     ⚠️ `_start` 里 `return` **不会退出**(会掉进后续代码),必须显式调 `exit`。
- **运行**: `qemu-aarch64 -L <emu_root> <h>`(host 文件系统可见 ⇒ `/tmp` 就是 host 的,便于读写进度/崩溃归因文件)。
- **用途**: 给固件里的**解析库**(如 libtins)写 fuzz harness、或拦截/复刻 daemon 内部逻辑;`afl-qemu-trace` 只装了 x86_64 风味的,qemu-user 跑 aarch64 harness 做"无反馈高吞吐变种 fuzz"是现成可用的。

## WSL/userns 里 `mount --bind /dev` 失败(devtmpfs 不能进 userns)⇒ 用 **privileged Docker 容器**跑固件 daemon | (2026-09-27/SRM-RT6600ax 实测)
- **现象**: 在 `unshare --user --map-root-user --mount` 里 `mount --bind /dev <dir>` 报
  `wrong fs type, bad option, bad superblock on /dev`(宿主 `/dev` = devtmpfs);而 `mount --bind /bin <dir>` **可以**。
  ⇒ userns 里 **devtmpfs 绑定被内核拒**;于是 chroot 后 `/dev` 退化为空 tmpfs,`cupsd` 等 daemon 拿不到 `/dev/null` 起不来。
- **解法(实测可用)**: 用本地已有的 **`docker:27-dind`(Alpine)** 起 **`--privileged`** 容器,
  宿主 `emu_root` 挂 `/rootfs`、宿主 `qemu-aarch64` 挂 `/qemu`:
```bash
docker run --rm --privileged --entrypoint /bin/sh \
  -v <abs emu_root>:/rootfs -v /usr/bin/qemu-aarch64:/qemu docker:27-dind -c '
    mount --bind /dev /rootfs/dev; mount -t proc proc /rootfs/proc
    chroot /rootfs /.emu/qemu-aarch64 /usr/bin/cupsd -f &'
```
  容器有真 `/dev` + `CAP_SYS_ADMIN` ⇒ `bind /dev` OK、`cupsd` OK(`lpstat -r` = "scheduler is running")、`lpadmin` 建打印机 OK。
- **注意**: ①`docker:27-dind` 是 **Alpine**,shell 是 `busybox sh`(无 bash;`printf` 等 applet 行为略异)。
  ②危险面: `--privileged` **只挂 `emu_root`,绝不挂宿主 `/`**;容器 `--rm` 即焚。
  ③本机 `docker` 在 `docker` 组,daemon 29.1.3 直接可用。
- **适用**: 任何"要先把 daemon/服务造起来才能到 sink"的候选(dnsmasq/printers/mesh…)。

## qemu-user 对 netlink 协议是**硬编码白名单** —— NETLINK_NETFILTER 面在 user-mode 仿真下跑不了 | (2026-09-28/Synology SRM RT6600ax)
- **现成能力(2026-09-28)**: 机器上已装 `qemu-aarch64-patched`(`~/.local/bin/`,49 MB 静态)→ **直接用,不涉及构建**;
  判读探针 `tools/qemu-nl-probe/`(`nltest2.aarch64` 2.8 KB 静态:同一条调用,原版 qemu `-93` / 补丁版 正数 fd);
  `tools/check.sh` 有 INFO 行。**别在目标目录里养副本** —— 工具属机器能力,权威 = `~/.local/bin/` 那份 + `AGENTS.md` 环境清单。
- **现象**: aarch64 guest 在 qemu-user 下 `socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER)` 恒得 **EPROTONOSUPPORT(-93)**;
  **同机原生 x86_64** 同一调用**成功** ⇒ 与内核/权限/netns 无关(两个不同内核上均复现)。
- **判据(逐条实测, 纯 raw syscall 探针 `tools/qemu-nl-probe/nltest2.c`)**: `AF_NETLINK/RAW/0(ROUTE)`→OK;`/15(KOBJECT_UEVENT)`→OK;
  `/4(SOCK_DIAG)` `/6(XFRM)` `/12(NETFILTER)` `/16(GENERIC)` → **-93**;改 `SOCK_DGRAM` 或加 `SOCK_CLOEXEC` 仍 -93。
- **根因(源码级)**: `qemu/linux-user/syscall.c::do_socket()` 的**硬编码白名单**(只放行 `NETLINK_ROUTE`/`NETLINK_KOBJECT_UEVENT`/`NETLINK_AUDIT`),
  紧随的 `switch(protocol)` 里还有 `default: g_assert_not_reached();`。发行版 8.2.2 与自建 10.1.50 行为完全一致 ⇒ 设计如此, **无开关**。
- **影响**: 走 NFQUEUE 的 guest daemon(`resolvsrmd`、`synosafeaccessfilterd`)**在 user-mode 仿真下起不来**(日志 `Failed on nfq_open()`);
  **LD_PRELOAD shim 也绕不过**(shim 自身仍要调 `socket()`);**任何 user-mode 重宿主框架(如 HouseEmu)同理**。
- **解法(①已端到端验证)**: ①**给 qemu 源码打补丁重编** —— 删白名单判定 + 把 `g_assert_not_reached()` 改 `break;`
  (无需 fd 翻译: `sockaddr_nl` 布局 guest/host 相同);`--target-list=aarch64-linux-user --static`,32 核实测 **40–52 秒**。
  **验证链**(`tools/qemu-nl-probe/`): 补丁版对 `NETLINK_NETFILTER` 返回 fd=3(原版 -93)→ 真 daemon `resolvsrmd`
  `socket/bind/CMD_BIND/PARAMS` 全部 **ACK error=0** 后进入 `recvfrom` 收包循环(队列 658);**无需 root**
  (`unshare -Urn` 内即有 CAP_NET_ADMIN + CAP_SYS_CHROOT);
  ②**原生 helper 建 netlink fd,经 `SCM_RIGHTS` 递给 guest**(qemu 不校验 fd 来源)。(③整机仿真,成本高。)
- **⚠️ 边界(跨架构用之前必读)**: 补丁把 `switch` 的 `default:` 改成 `break;`,即**对新增放行的协议不做 netlink 消息/fd 翻译**。
  对 **aarch64 guest / x86_64 host**(同为 64 位小端,`sockaddr_nl` 与属性对齐一致)是对的 —— 已端到端验过(NFQUEUE ACK error=0)。
  但 **32 位(如 MIPS/arm)与大端 guest** 上,上游 qemu 写 ROUTE/AUDIT 的翻译器**正是因为那些布局会差**;此时
  「socket 探针过」**不等于**「协议可用」(建 socket 与架构无关)。
  ⇒ 32 位/大端目标上**必须用真 daemon 跑一次 `CMD_BIND`/`PARAMS`,以 ACK=0 为准**,不得只凭探针结论。
- **普适教训**: 判"某内核不支持某系统调用"**必须用原生程序测同一 syscall**;凡在 **qemu 翻译过的进程**里拿到的 errno
  (ENOPROTOOPT/EPROTONOSUPPORT/EINVAL),**不得直接归因给内核** —— 先怀疑 qemu 的 syscall 层。
- **附带坑(跑 Synology daemon 通用)**: 目标 daemon 若取不到 syslog(chroot 内无 `/dev/log`)会**立刻 exit(1) 且零输出**;
  只有 `strace` 才看得到 `connect("/dev/log") = ENOENT`。造场前先在 chroot 的 `/dev/log` 放一个 AF_UNIX DGRAM sink
  (`tools/qemu-nl-probe/syslog_sink.py`)。

## qemu-user 下 guest `daemon()` 恒失败 → LD_PRELOAD 垫片跑前台;同法可做 mangled-symbol 插桩器定位库内错误 | (2026-09-28/Synology SRM RT6600ax)
- **现象**: guest 二进制在 `qemu-aarch64` 下调 glibc `daemon(0,0)` 返回 **-1**(其内部 `setsid`/`chdir("/")`/`open("/dev/null")` 都成功),目标日志 `Failed to daemonize` ⇒ 守护进程跑不完初始化。
- **解法**: 交叉编一个 guest aarch64 `.so`: `int daemon(int,int){return 0;}`,`LD_PRELOAD=/path/x.so` 让目标**前台运行**。
  自建配方见"llvm ld.lld 交叉编译"条(`clang --target=aarch64-linux-gnu --ld-path=<ld.lld> -shared -fPIC -nostdlib -nostartfiles --unresolved-symbols=ignore-all`,**不 include 宿主头文件**)。
- **同法做插桩器(高价值)**: 用**导出的 C++ mangled 名**在 .so 里定义同名函数 → 记 `__builtin_return_address(0)` + `dladdr`(模块+偏移),
  再 `dlsym(RTLD_NEXT, "<mangled>")` 调真身。实测一次把"某 handler 的 408 从哪来"一步定源:`WfmLibSetErr(408) off=0x28208 mod=libwebfm.so`。
  **比 gdb+qemu 断点稳** —— 无 section header 的主程序会让 gdb 内部崩。
- **适用**: 任何"目标 daemon 要 daemonize / 需知道库内错误码由谁设 / 需抓库函数的入参(如哈希原像)"的仿真场景。

## QNAP QTS 固件(`.img` + `icpnas` footer):只加密首 1MB 的 PC1 流密码 + QTS CGI 在 qemu-user 下直跑 | (2026-09-28 QNAP TS-233 QTS 5.2.10)
- **格式**: `TS-X33_*.img` = `gzip(tar)` + 尾部 `QNAP_FW_UPDATE_IMAGE_DIGITAL_SIGNATURE`(S/MIME 签名 2541B)
  + `0x4a` 字节 footer(`icpnas` + blobsize(u32 LE) + model[0x10] + ver[0x10] + date[0x10] + build)。
  **`blobsize` 就是加密长度,默认/实测 `0x100000` —— 只有首个 1MB 被加密,其余是明文**(反直觉,先按它试)。
- **解密**: QNAP 自研流密码 PC1,出厂固定 key **`QNAPNASVERSION4`**(14B ASCII)。参考实现 `Bonstra/qnap_fwdec`(`crypto.rs`;
  其 `--selftest` 测试向量可直接验移植);本工作区 C 移植 `tools/qnapcipher.c`(`d <key> <in> <out> <len>`,`--selftest` 对齐向量)。
  配方: `qnapcipher d QNAPNASVERSION4 <img> head 0x100000` → `head || img[0x100000:签名起始]` → `gzip -dc` → **260MB tar**。
  ⚠️ 直接 `gzip -dc` 整个原图会在 1MB 处报 `invalid block type` —— 那是**没解密首块**,不是文件损坏。
- **tar 内**: `initrd.boot`(gzip→cpio = 根 `/`)、`rootfs2.bz`(**LZMA**,`xz -dc` 可解;内为 `opt/rfs_usr.img` + `opt/rfs_home.img` 两个 ext3)、
  `rootfs_ext.tgz`(内 `rootfs_ext.img` ext3 = `/opt`)、`qpkg.tar`(内置 QPKG,独立代码基,须单独入面)、`Image`、`*.uboot/*.preloader/*.dtb`。
- **ext3 镜像无 root 提取**: 7z 能读 ext3(`7z x -o<out> x.img`)但**拒收 symlink**(报 `Dangerous link path was ignored`,
  留 0 字节普通文件)⇒ 那些 soname 链接要按日志行 `名字 : 目标` 手工 `ln -s` 补回,否则跑二进制会 `file too short`。
- **在 qemu-user 下直跑 QTS CGI**(解锁动态验证): `qemu-aarch64 -L <merged_rootfs> <cgi>` + `REQUEST_METHOD/QUERY_STRING/CONTENT_*` 环境变量。
  两个坑: ①**CGI 模式位非 0 才可 exec** —— `quick.cgi` 出厂是 `0444`(QTS 用它当"未初始化"状态旗标,`init_nas.sh` chmod 444 禁用、
  `init_check.sh` chmod 755 启用),qemu 对 0444 报 `Exec format error`(不是格式问题);②合并 rootfs 缺的 soname 链接要先补齐。
- **Web 服务器**: `/usr/local/sbin/_thttpd_`(Qthttpd/wfm_thttpd 同名硬链);`-u` 需 `rootfs/etc/passwd` 里存在的用户
  (QTS 只有 `admin`/`guest`),否则 `unknown user` 直接退出;跑起来后能直接验 CGI env。
- **unblob 自带 QNAP 解密**: `unblob -e <out> <img>` 的 QNAP handler 会直接产出 `<img>.decrypted` 并继续解 gzip→tar
  (实测解出 17710 文件),**与手工 PC1 链结果一致** ⇒ 有 unblob 时不必手写解密;binwalk 无此能力(只认 footer)。
- 来源: `QNAP-TS-233-QTS-5.2.x` `2026-09-28`。

## 在宿主侧观测 qemu-user 里 daemon 的真实行为:SysV IPC 权限 + strace(而非自己轮询抢消息)| (2026-09-28 QNAP qLogEngined/qShield)
- **背景**: qemu-user 的 guest 与宿主**共用**一个 SysV IPC 命名空间 ⇒ 宿主能直接 `msgget/msgrcv` guest 的队列(便于判读),
  但**别用它去"轮询等消息"**: 那会与 guest 里真正在 `msgrcv` 阻塞的 daemon **抢**消息 —— 抢到就以为"对方没处理"(本次据此误判了一轮)。
  正确做法: **`strace -f -e trace=msgget,msgsnd,msgrcv` 直接盯 guest 进程的系统调用**(qemu 透传 key,看到的 key 就是 guest 的 key),
  能让"到底谁发了/谁收了"一步定源。
- **两个必踩的坑**:
  1. **`msgget(key, IPC_CREAT|0777)` 对已存在的 `0666` 队列会 `EACCES`**(请求位含 x,属主类只有 rw)⇒
     要么先删队列再用 `umask(0)` + `0777` 建,要么按 `0666` 请求;QTS 里 `qLogEngined` 用 `0666` 建、`qShield` 用 `0777` 要 ⇒
     启动顺序不同就会有一方拿不到队列(仿真里表现为 `msgget ... = -1 EACCES`)。
  2. **`ipcrm -Q <key>` 不一定删得掉**;可靠写法 = `q=msgget(key,0)`(access 位 0 ⇒ 不做权限检查)+ `msgctl(q,IPC_RMID,NULL)`。
- **daemon 的"存活"判据**: QTS 守护普遍 `fork` + 父进程退出 ⇒ 用 `Popen.poll()` 判 `alive` 会把**活着的 daemon 判成死了**
  (`fork/dup2/umask/sigprocmask` 都在导入表里)。判据要用 `/proc` 扫 cmdline 或看它自己的 pidfile。
- **要看 fork 型 daemon 的内部判断,用它的 `-f -d <n>`** —— QTS 守护普遍 `fork`+`dup2` 把 stdout 丢掉,
  只有前台+debug 才看得到它的配置 dump 与判读日志(实测 `qShield -f -d 9` 直接打出
  `Service: [Http] Enabled/Max N times between M minutes/Block for K minutes`,以及 `Ban it => IP[...]!`);
  据此才知道"阈值是 5",而不是靠猜。同理 QTS 的 `_thttpd_` 也有 `-nor/-nos/-u/-d/-c` 等自描述开关。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-28。

## `afl-qemu-trace` 可能只编译了单一架构:先验 arch,再谈 fuzz | (2026-09-28 本工作区实测)
- **现象**: 对 aarch64 目标跑 `afl-showmap -Q` ⇒ fork server 起不来(`afl_fsrv_start(), src/afl-forkserver.c:2205`)、tuples=0、map 文件不生成;
  直接跑 trace 才看到真因: `afl-qemu-trace: <bin>: Invalid ELF image for this architecture`。
- **一眼验证(必做)**: `afl-qemu-trace -h | head -2` ⇒ 本机输出 `usage: qemu-x86_64 …  compiled for x86_64 emulation`
  ⇒ **该 trace 只能跑 x86_64 guest**;换句话说 AGENTS 里"`afl-showmap -Q -- /bin/cat` 抓到 109 tuples"的冒烟**用的就是 x86_64 二进制**,
  从未验证过跨架构 ⇒ 对 ARM 固件(本项目绝大多数)该格默认不可用。
- **修正(已实测通过,2026-09-29)**: `mkdir -p ~/opt && cd ~/opt && curl -sL codeload.github.com/AFLplusplus/AFLplusplus/tar.gz/refs/heads/stable | tar xz`
  → **先编本体**(qemu 脚本要 `../afl-showmap`):`cd ~/opt/AFLplusplus-stable && make -j$(nproc) source-only`
  → 再 `cd qemu_mode && PATH=$HOME/ENTER/envs/pwn/bin:$PATH CPU_TARGET=aarch64 ./build_qemu_support.sh`(依赖 glib/pixman/ninja 已装);
  **装法**:产物**按架构分目录 + `AFL_PATH` 选**(别动 `/usr/local/bin` 那份 x86_64,两者并存):
  `cp qemu_mode/afl-qemu-trace ~/.local/bin/afl-aarch64/` ⇒ 用时 `AFL_PATH=$HOME/.local/bin/afl-aarch64 afl-showmap -Q …`,
  它自己打印 `Using QEMU binary: <dir>/afl-qemu-trace`。
  **冒烟判据(A/B)**: aarch64 `busybox cat` ⇒ **168 tuples**;不设 AFL_PATH(旧 x86_64 那份)⇒ **0 tuples**(静默失败)。
  **吞吐实测**: aarch64 guest + QEMU 模式 ≈ **28 exec/s**(`afl-fuzz -Q` 70 s → 1979 execs,edges 4954/65536,0 crash)
  ⇒ 只够 smoke 与"按字符串表切字段后定向变异"的窄面搜索,**不够盲跑长时程**;给 fuzz 预算时按此折算。
  ⚠️ aarch64 构建**不产出 `libqasan.so`**(缺 `aarch64-pc-linux-gnu-gcc`)⇒ ASan 模式在 ARM guest 上不可用。
  未修好前,aarch64 面的"机械化一格"只能用**等价 oracle(直接跑厂商二进制 + 差分/strace)**,并在结论里注明无覆盖率证据。
- **附带**: 本机 `~/opt/AFLplusplus-stable/` 已不存在(环境清单该条过期)。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-28。

## 找"谁调了这个导入符号":别手工按 `.rela.plt` 下标推 PLT 地址 —— 有些二进制根本没有 PLT | (2026-09-29 本工作区踩坑)
- **错法(我踩了)**: 用 `plt.sh_addr + i*plt.sh_entsize`(i = 该符号在 `.rela.plt` 里的序号)算 PLT stub 地址,再用 capstone 扫 `bl`/`b` 到该地址。
  `.rela.plt` 序号与 PLT 槽**不是一一对齐**(首个条目常对应 `PLT+16`,还有 lazy-binding 头),算出来会落在**别的符号槽**上 ——
  实测把 `memcpy` 认成了日志调用,得出三个假的"调用点"。
- **对法**: ① `r2 -c "aa; axt @ sym.imp.<name>"`(r2 认得 PLT/GOT);② 若 `axt` 也为空而 `readelf --dyn-syms` 明明有该符号 ⇒
  该二进制**走直接 GOT 间接调用**(`adrp xN, page` + `ldr xN, [xN, #lo12]` + 后续 `blr xN`,**没有 PLT stub**):
  这时要按**符号的 GOT 槽地址**(`relocation.r_offset`)去搜 **adrp 的页 + ldr 的低 12 位**(不是搜整地址),再回溯最近的 `blr`;
  ③ 或者干脆改用行为/字符串侧判据(它读哪个字段/哪段字符串簇),别在调用图里死磕。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-29。

## QNAP QPKG 的 `.tgz` 可能是 LZMA(假后缀):按设备自己的判据解档 | (2026-09-29 实测)
- **现象**: 同一 `qpkg.tar` 里有的 `*.tgz` 用 `tar -xzf` 解出 **0 个成员**(静默失败),有的正常。
- **真因**: 设备自带安装器 `etc/init.d/installtgz.sh::unzip_pkg()` 用**首 4 字节**判压缩:
  `octal 135 000 000 000`(= `5d 00 00 00`)⇒ `tar Jxf`(**LZMA**),否则 `tar zxf`(gzip)。
  即 `.tgz` 后缀**不可信**:`ldap_server.tgz` 头是 `5d 00 00 00 04 ff ff ff ...` = LZMA(xz --format=lzma 可解)。
- **复现/修法**: `mag=$(head -c4 f | xxd -p)`;`[ "$mag" = 5d000000 ] && xz --format=lzma -dc f | tar -x || tar -xzf f`。
  实测:24 个包里 **8 个 LZMA + 12 个 gzip**,重解后文件数 2921 → **4348**,并首次取到 `slapd`。
- **教训**: 解厂商包**先读设备自己的解包函数**(`unzip_pkg`/`patch-firmware` 之类),别按后缀猜;静默空解要当成"格式不对"来查,别当成"包是空的"。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-29。

## qemu-user 下 `system()`/`popen()` 走**宿主** shell:凡目标守护要调"设备内助手"的面都会降级 | (2026-09-29 实测)
- **现象(两次,同一根因)**: `qShield` 落规则时 `sh: 1: /sbin/ip_filter: not found`;QNAP 改过的 `rsync --daemon` 启动时
  `sh: 1: /sbin/getcfg: not found`、`sh: 1: /sbin/rsync_util: not found` ⇒ **配置生成/鉴权判定被降级**,观测到的行为不是设备行为。
- **真因**: `qemu-aarch64 -L <rootfs>` 只影响**加载器/绝对路径 open**,`system()`/`popen()` 里 fork+exec 的 `/bin/sh` 是**宿主**的;
  宿主的 `/bin/sh` 当然没有设备的 `/sbin/getcfg` 之类。
- **修法(本工作区实测,免 root、免容器、免 mount)**: 宿主 binfmt_misc 已注册 `qemu-aarch64`(`/proc/sys/fs/binfmt_misc/qemu-aarch64`,interpreter `/usr/bin/qemu-aarch64`)
  ⇒ 直接用 **`unshare -r --map-root-user chroot <rootfs> /bin/busybox sh -c '<cmd>'`**:用户命名空间里当 root 做 chroot,**不做 mount**;
  实测 chroot 内 `/sbin/getcfg` 可用(输出 `0`)、`/sbin/ip_filter`/`/sbin/rsync_util` 都在、错命令报 `sh: <cmd>: not found`(无 `1:` = 设备 ash)。
  ⚠️ 历史坑是 **`unshare` + `mount` + chroot** 触发 WSL 重启 ⇒ **只用 chroot,别加 mount**;chroot 内 applet 不全(`head` 可能没有)⇒ 要输出就**写 rootfs 内文件再从宿主读**。
- **修法(需要 root 时的手段,按"缺哪层补哪层")**: 把提取的 rootfs 当**文件系统根**跑 —— 容器里 `chroot <rootfs> /bin/sh -c ...`(或 `--rootfs`),
  使 `/bin/sh` 与全部 QTS 助手都取自设备;再叠加 binfmt/qemu-user 跑 aarch64。**别再用 `-L` 去判"依赖 shell 助手的守护"**。
- **判别特征(可复用)**: `host dash` 打 `sh: 1: <cmd>: not found`(带 `1:`);`guest busybox ash` 打 `sh: <cmd>: not found`(无 `1:`)⇒ 看日志就能判"当时跑的是哪个 shell"(实测 `qShield` 的 `sh: 1: /sbin/ip_filter: not found` = 宿主 shell,证据闭合)。
- **本 build 无开关可改**: `strings /usr/bin/qemu-aarch64 | grep -iE "QEMU_.*EXEC"` 无输出 ⇒ **没有** `QEMU_EXECVE` 之类变量(实测设了也无行为差异)⇒ 只能换载体。
- **推论(先验判据)**: 反汇编时看到 `system()/popen()/qnap_exec(…,"|",…)` + 参数是**设备路径助手** ⇒ 该面**必须**用 chroot/容器载体,
  否则结论只能标"未判定(载体重)",不许写成"打不了/没洞"。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-29。

## 调用点判定必须**双工具**(IDA `xrefs_to` 会假阴性)| (2026-09-29 本工作区实测)
- **现象**: 同一二进制 `disk_lab.cgi`,IDA `xrefs_to` 对 `auth_get_session`/`CGI_Get_Input` 报 **0 调用点**(连 PLT 自引用都没有),
  而 **r2 `aa; axt @ sym.imp.<name>`** 正确报出 `main 0xb370 / main 0xb32c 的 bl sym.imp.*` ⇒ **IDA 假阴性**。
- **影响**: 用"导入符号存在但无调用点 ⇒ 死导入"这一判据时,**单用 IDA 会整片误判**(本次连下 5+ 条)。
- **纪律**: 凡结论依赖"**该符号是否被调用**" ⇒ **必须 IDA + r2 双工具**;两者不一致时**以 r2 的 `axt` 为准并留痕**,
  或改用第三判据(读 `.plt`/GOT 的引用计数、capstone 搜 PLT 段内 `bl` 次数)。
- 来源: QNAP-TS-233-QTS-5.2.x 2026-09-29。


## QNAP CGI「动作→参数表」的机械取法(2026-09-29,本工作区实证 6/7 个分派器)

结论:**不要**自己按 `.rela.plt`/low-12/`adrp`+`ldr` 手搓 PLT 映射(同一会话内 4 次静默失配),也**不要**用 pyelftools 的 section 反汇编
(QNAP CGI 的 `.text` section header 不可信 ⇒ 只解出 ~1.2k 条指令而真实代码上万条)。可靠三步,全程只依赖 r2:

1. 调用点: `r2 -q -c "aa; axt @ sym.imp.CGI_Find_Parameter" <cgi>`(行形如 `main 0x1ed7c [CALL:--x] bl sym.imp.CGI_Find_Parameter`);
2. 每个调用点: `r2 -q -c "e asm.bytes=false; s <addr>; pd -12" <cgi>` —— 解析 `adrp xN, …` **与** 紧随的 `add xN, xN, #lo12`;
   ⚠️ **`adrp` 的操作数可能是符号名**(如 `adrp x1, str.foo ; 0x3e000`),数值在 **`;` 之后的注释**里 ⇒ 正则必须两种形态都吃;
3. 字符串: `r2 -q -c "ps @ <page+lo>" <cgi>`。

产出实例(每行 `调用点 / 地址 / 参数名`): `userConfig.cgi` 93 行(`hash/app_id/cloud_url/id`、`sid/username/pwd/reset_pw_en/email_passwd/refresh_token/subUser`…)、
`sysRequest.cgi` 13 行(`subfunc/sid/apply/conncsv/eventcsv/backup_config/get_cert/get_key/get_issuer_cert/get_tou_html/lang`)、`appRequest.cgi` 13 行、
`manaRequest.cgi` 7 行、`devRequest.cgi` 2 行、`networkRequest.cgi` 1 行;`privRequest.cgi` 0(r2 亦未解出其导入,与"该 CGI 仿真即 SIGILL"一致)。

## QNAP 5.2.x 固件提取配方 + `_thttpd_` env 映射 + qemu multi-process CGI 限制(2026-09-29 TS-233 实测)
- **容器解密(PC1 只加密首 1MB)**: `tools/qnapcipher d QNAPNASVERSION4 <img> head.bin 0x100000` → `tail -c +1048577 <img> > rest` → `cat head rest > fw.gz`(尾部签名致 `gzip` 报 `trailing garbage ignored`,正常)→ `gzip -dc | tar -x`。
- **目录映射坑**: 固件 tar 里 `rootfs2.bz`(LZMA)→tar→`opt/rfs_usr.img`(ext3)= 挂载后的 `/usr`;用 `debugfs dump` 时**镜像内路径是 `/local/...`(不是 `/usr/local/...`)**。`_thttpd_` 真正的 dump 路径 = `/local/sbin/_thttpd_`。
- **`_thttpd_` 的 CGI env = 固定列表**(`REMOTE_ADDR/SERVER_NAME/SERVER_PORT/HTTP_HOST/HTTP_COOKIE/HTTP_USER_AGENT/…`),**不含 `HTTP_X_FORWARDED_*`**;但:
  `X-Forwarded-For:`(客户端)→`REMOTE_ADDR`、`X-Forwarded-Host:`(客户端)→`HTTP_HOST` **都无白名单**,而 `SERVER_NAME` = 设备配置主机名(**不受控**)。
  判证法(可复用):跑厂商 `_thttpd_` 指向含"回显 env 的 sh CGI"的 docroot,发五种头组合对照。来源: QNAP-TS-233-QTS-5.2.x 2026-09-29。
- **qemu-user 限制**: **单进程直调 CGI** 可(`qemu-aarch64-patched -L <rootfs> <cgi>`,env 用 `env -i REQUEST_METHOD=… QUERY_STRING=…`);
  但 **`_thttpd_` exec aarch64 CGI** 报 `qemu-aarch64: Could not open '/lib/ld-linux-aarch64.so.1'`(multi-process 不适用 `-L`);
  且 `authLogin.cgi`/`privRequest.cgi` **直调即 `qemu: uncaught target signal 4 (Illegal instruction)`**(间接跳转错译)→ 这些面动态需 chroot/binfmt 运行时。

## QNAP 保护 CGI 的 `.text` 加密:qemu 跑不起来**不是 qemu 的问题**(2026-09-29 TS-233 5.2.10 实测)
- **症状**:某些 QNAP CGI 直接 `qemu-aarch64 -L <rootfs> <cgi>`(或 chroot+binfmt)**入口即 SIGILL**;换 qemu 版本(11.0.3/11.1.0/10.1.0-patched/AFL)**全一样**。
- **判据(3 秒可验)**:`readelf -h <cgi> | grep 入口` 的 entry 落点的 4 字节是不是标准 `1d 00 80 d2`(`mov x29,#0`)。
  不是 ⇒ 该 CGI 的 **`.text` 被加密**(算 4KB 块熵 ≈ 8.0;`.init`/`.plt` 仍是明文合法指令)。TS-233 5.2.10 上受影响的是 **8/67**:authLogin/authLogout/azureLogin/device_info/diskRequest/disk_manage/privRequest/privWizard。
- **机制**:QTS 用 **`qelf`**(`/lib/modules/misc/qelf`,Go 程序,由 `etc/init.d/qlsmd.sh` → `qelf start` 拉起;它把内嵌 `.ko` 写到 `/tmp/kmod*` 并 `insmod`)+ **内核内建 `qlsm`**(`qlsm_register_qelf`)+ `qelf.ko`(导入内核 `aes_decrypt`)在**内核 ELF 加载时解密 `.text`**。用户态 qelf 有 `aesExpan/Derive/envKey/mediumckey` ⇒ 密钥调度在用户态,键疑按设备/环境派生。
- **结论**:这些 CGI 在**任何用户态模拟器**下都**不能运行**;要分析/运行它们,得先拿到**解密后的副本**(真机内存 dump / 真机上的明文文件)或逆向 `qelf`+`qelf.ko` 拿到密钥。**不要**为此换 qemu。
- **可复用**:判"qemu 能不能跑某 ARM 固件二进制"先看**入口指令是否可解码 + `.text` 熵**;加密段会伪装成普通 ELF(头部/reloc/.init 都正常)。

## QNAP QELF(加密 CGI)解密法:先用公开算法,再用 many-time-pad 绕开密钥(2026-09-29 TS-233 5.2.10 实测)
- **识别**:QNAP 加密的 ELF 其 **`e_flags`(ELF header 0x30)= 0x40000000**、`.text` 的 **sh_flags 含 0x0000010000000000**;正常文件 `e_flags=0`。`readelf -h` 的 entry 落点 4 字节不是 `1d 00 80 d2`(`mov x29,#0`) ⇒ `.text` 被加密(4KB 块熵≈8.0,`.init/.plt` 仍明文)。
- **算法(公开:TeamT5 2025-12)**:`.text` 逐页;每页从该页处理起点起 **counter 从 0 计**,每 64B 一块,用 **ChaCha20 变体**生成 keystream 与块 XOR。counter **逐页归零** ⇒ **同一个 4KB keystream 在所有页/所有文件重复**。
- **不用 key 的解密(本工作区自研,推荐)**:对 `C[X]=P[X]⊕K[X mod 4096]` 做 **many-time-pad**——
  ① 用**未加密的同族 ELF**(此处 59 个 CGI)的 `.text` 建**位置 mod 4** 的字节频率模型;
  ② 对全部加密文件的**整页**(跳过首半页)逐 offset argmax 恢复 4KB `K`;
  ③ 解密:首半页用 `K[X-text_off]`,整页用 `K[X&0xfff]`;再置 `e_flags=0`、`.text` sh_flags=0x6。
  工具 `tools/qelf_decrypt.py`。**判据**:解密后 `.text` 每页 capstone 100% 解码 + qemu 下可运行。
- **注意**:keystream **随固件版本变**(需按目标固件重跑恢复);`qelf`/`qelf.ko`(内核 `qlsm`)负责真实解密,离内核跑不了加密文件——**不要再为此换 qemu**。

## QNAP QTS(qemu-user)拿**真 admin 会话**的建场配方(2026-09-29 TS-233 5.2.10 实测,H-15 解除)
- **卡点**:出厂/未配置态 `shadow` 的 admin 是默认口令(`$1$$CoERg7ynjYLsj2j4glJ34.` = `admin`);QTS 在登录时用 `Is_Admin_Default_Pwd` 判出默认口令 ⇒ 返回 `authPassed=0`(逼你改口令),**拿不到会话**。
- **配方**:①把提取 rootfs 的 `etc/config/shadow` 里 admin 换成一个**非默认**口令的 MD5-crypt(`openssl passwd -1 -salt <s> <pw>`);②跑**解密后**的 `authLogin.cgi`:`POST user=admin&pwd=<base64(pw)>` ⇒ 响应含 `authSid`(8 字符)且 `authPassed=1`;③该 `authSid` **跨 qemu 进程**被 `auth_get_session` 接受(会话表在共享内存/文件,非进程内)。
- **验证**:`userConfig.cgi?func=get_all&sid=<sid>` → `{"success":"true"}`;无 sid → `"false"`。
- **用途**:解锁**认证后 CGI 的动态验证**(差分/IDOR/授权),此前 qemu-user 下不可做。

## QNAP QELF 解密件:qemu 直接 exec 报 `Exec format error`,但经 ld.so 可跑

- **症状**:QELF 解密还原后的某些 CGI(实测 `priv/privRequest.cgi`、`priv/privWizard.cgi`)用
  `qemu-aarch64 -L <rootfs> <cgi>` 直接执行 ⇒ `Error while loading <path>: Exec format error`,rc=1,零 stderr;
  而同批解密件 `authLogin.cgi` 同命令可跑。
- **排除**:两文件的 `e_ident`/`e_type`/`e_machine`(0xb7)/`e_entry`/PHDR 表**逐字节同构**(仅尺寸/偏移不同);
  `readelf -h/-l/-S` 全部正常;入口指令字节同构 ⇒ **不是解密损坏**。
- **解法**:把目标 CGI 交给动态加载器执行 ——
  `qemu-aarch64 -L <rootfs> <rootfs>/lib/ld-linux-aarch64.so.1 <cgi>`
  (实测该方式下 `privRequest`/`privWizard` 正常输出 XML)。
- **用途**:凡"解密还原正确但 qemu 拒 exec"的 QNAP CGI,一律换 ld.so 通道,别去重解 QELF。

## QNAP rootfs 里 `.so` 常是 440(root-only)⇒ 非 root 跑 qemu 静默加载失败

- **症状**:CGI 无任何输出、rc=1、连 `qemu -strace` 都无 syscall;`authLogin` 等同架构 CGI 却正常。
- **真因**:`libuLinux_Storage.so.0.0` 等是 `-r--r-----`(440);非 root 用户跑 qemu 时
  `openat(...,O_RDONLY) = -1 errno=13 (Permission denied)` ⇒ 动态加载失败。
  `-strace` 可见 `openat(AT_FDCWD,"<...>/libuLinux_Storage.so",O_RDONLY|O_CLOEXEC) = -1 errno=13`。
- **解法**:`chmod -R a+rX <rootfs_merged>`(一次性;解包器常保留原始 440)。
- **判读**:CGI "跑不出东西"且 `-strace` 显示 `errno=13` ⇒ 先 `chmod -R a+rX`,不要先怀疑解密/参数。

## qemu-user `-L <rootfs>` 会**先试宿主路径**:`/mnt`(等宿主已存在目录)不被翻译

- **症状**:`qemu-aarch64 -L $R <bin>` 里,guest 读 `/etc/config/...` 得到 **rootfs 的**文件,但读/写 `/mnt/...` 落到**宿主的** `/mnt`(WSL 下即 `c d e f k wsl wslg`)。
- **判据/机制**:qemu 的 ld-prefix 只在**宿主路径不存在**时回退到 `<prefix>/<path>`;`/mnt` 在宿主已存在 ⇒ 用宿主的。验证:`qemu -L $R $R/bin/cat /etc/config/shadow`(出 rootfs 内容)vs `qemu -L $R $R/bin/ls /mnt`(出宿主目录)。
- **后果**:凡是**只在 `/mnt` 下读写**的目标(如 QNAP `firm_srv` 的 `/mnt/update/firmware.img`)在 `-L` 模式下**永远不落盘**,并常表现为 `open: No such file or directory` 且无 syscall 可查。
- **解法(非 root 可用)**:用 **bubblewrap** 造真 root:
  `bwrap --unshare-user --uid 0 --gid 0 --bind $R / --bind <writable> /mnt --ro-bind /usr/bin/qemu-aarch64 /qemu --dev /dev --proc /proc --bind /tmp /tmp /qemu <bin>`
  ⇒ guest uid=0、`/mnt` 可写、`mount` 仍可能 EPERM(视 userns 而定)。`docker` 拉不到镜像时这是首选。
- **另**:`pkill -f <守护名>` 会**自匹配杀掉自己的测试壳**(cmdline 含该名字)⇒ 用 `setsid ... & echo $! > pid` 后按 PID `kill -9`。

## 缺目标 `.so` 时:按**导入符号**自造 aarch64 垫片,别去找现成的 | (2026-09-29/QNAP TS-233 QTS 5.2.x)
- **现象**: 目标 CGI/QPKG 依赖的某个 `.so` 在 rootfs 里缺失 ⇒ 直跑报 `error while loading shared libraries: libX.so.N` ⇒ 动态格(等价 oracle)整面跑不了 ✗
- **别做**: 去发行版归档"找现成的那个版本" —— 版本常对不上(例:ICU68 被 Ubuntu 跳过、Debian 68.2-1 无 arm64、ArchARM 归档停服)⇒ 白花轮次 ✗
- **做法(实测通过)**:
  1. 从 loader 报错 / `readelf -d` / `strings` 列出目标**实际导入的符号**(例:某 CGI 只用 ICU 的 4 个:`ucol_open_68`/`ucol_close_68`/`ucol_setAttribute_68`/`ucol_strcollUTF8_68`);
  2. 按这些符号写 stub 源(返回合理缺省值即可,`-nostdlib` 不需要真实现);
  3. 交叉编译:`sudo apt install lld` → `clang --target=aarch64-linux-gnu -shared -nostdlib -fuse-ld=lld -fPIC -o libicui18n.so.68 stub.c`;
  4. 运行:`LD_LIBRARY_PATH=<垫片目录> qemu-aarch64 -L <rootfs> <guest>`(本工作区也可用 `qemu-aarch64-patched` ✓)。
- **判据(必须能证伪)**: 垫片只是"让程序跑到业务逻辑"的手段,**不是结论** ⇒ 必须配**动态三态**(无会话 / 低权 / 管理员)证明它真在跑(例:`utilRequest.cgi` 无 `sid` ⇒ 拒绝;admin ⇒ 真出 JSON,且 `-strace` 证明确实读了 `/etc/config/*`)✓
- **注意**: 垫片只该覆盖**非安全相关**的外部依赖(ICU/编码/图形库);**门类 / 认证类符号绝不能垫** ✗(那会把结论做废)
- **适用**: 缺 `.so` 的 QPKG CGI、体积大的标准库(ICU / libxml / 图形库);与「qemu-user 下 guest `daemon()` 恒失败 → LD_PRELOAD 垫片」那条是**同一手法、不同用途**(那条拦 `daemon()`,这条补缺库)✓
- 来源: `QNAP-TS-233-QTS-5.2.x` `2026-09-29`

## GraalVM native-image 的"宿主识别"在 qemu-user 下必然失败(读 /proc/cpuinfo 且被 qemu 拦截) | (2026-10-02/Ubiquiti UDM Pro Network app 实测)
- **现象**: 直接跑厂商 native 应用 `chroot <rootfs> /usr/lib/unifi/lib/unifi ...`,过掉 Spring JSON 依赖后报
  `java.lang.IllegalStateException: Failed to identify UniFi-OS host`(bean `systemService` 实例化失败)。
- **根因(实测)**: 该识别读 `/proc/cpuinfo` 的 `CPU implementer`/`CPU variant`/`CPU part`(strings 有这三串);
  而 **qemu-user 自己合成 `/proc/cpuinfo`**(x86_64 宿主上跑 aarch64 guest 仍返回 ARM 内容),且在 chroot 内**自建真实 `/proc/cpuinfo` 文件也会被忽略**(实测 `cat` 到的是 qemu 合成的)。
- **无力点**: `qemu-aarch64-static -cpu cortex-a57/a72` 只改 `implementer`(0x41)/`part`(0xd07/0xd08),`variant`/`model name` 仍是通用值 ⇒ 仍失败。
- **可行替代(选一)**:
  1. **姊妹产品 Java 字节码**反推同版本组件(见 `methodology.md`「native AOT vs 姊妹产品字节码」)——本例首选;
  2. full-system aarch64 仿真(qemu-system + 厂商 kernel/DTB,非标 boot,通常难);
  3. 真机(本工作区没有;不构成停工理由,只降动态强度)。
- **别踩**: 不要反复换 `-cpu` 硬试(已试 a53/a57/a72/max 级别的部分组合),不是 CPU 型号问题而是 **qemu 拦截文件**这一层。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-02`

## qemu-user 下 nginx 起不来: `io_setup() failed (38: Function not implemented)`(Linux AIO 不可用) | (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测)
- **现象**: systemd 容器(aarch64 guest,binfmt=host qemu-user)内 `nginx`(1.30.4)config test 通过但起不了监听,error.log 反复
  `[emerg] io_setup() failed (38: Function not implemented)`。
- **根因**: nginx 启动时调用 Linux AIO `io_setup(2)`;qemu-user/seccomp 下该 syscall 未实现 ⇒ `38 ENOSYS`。**设备真内核无此限制**。
- **后果**: 依赖 nginx 的站点(如 unifi-core 控制台 443/80)**在 qemu-user 容器里拿不到"就绪态监听"** ⇒ 端口级依据只能用模板/配置(L3),不能用容器实测。
- **替代**: ①整机/真机;②若只需应用逻辑,用其**上游 unix socket**(如 `uos-http.sock`)直连,绕过 nginx。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-03`

## 把 rootfs 容器化系统 container(让 systemd 当 PID 1): 宿主侧 tar 不要用 `docker exec` 管道 | (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测)
- **场景**: 需要跑厂商 rootfs 自带的 systemd/服务(非标准 boot,不能整机仿真)。
- **做法**: `tar --numeric-owner --exclude=./proc --exclude=./sys --exclude=./dev -C <rootfs> -cf /tmp/r.tar .`(宿主侧,**~10s/3GB**)
  → `docker import /tmp/r.tar <img>:local`(~25s)→ `docker run -d --privileged --cgroupns=host --tmpfs /run --tmpfs /tmp -v /sys/fs/cgroup:/sys/fs/cgroup:rw <img> /lib/systemd/systemd --unit=multi-user.target`。
- **坑(踩过)**: ①`docker exec <cont> tar … | docker import` **管道极慢**(容器传输通道,15min 超时仍未完);改**宿主侧 tar** 即秒级。
  ②**tar 以非 root 跑会静默跳过不可读文件**: `/etc/sudoers`(mode 0440 root)会被漏掉 ⇒ import 后 `sudo` 报 `unable to stat /etc/sudoers`;
  须显式补回(`docker exec <root-cont> cat … | docker exec -i <new> sh -c 'cat > /etc/sudoers; chmod 440 …'`)。
- **判据**: `systemctl is-system-running` 会停在 `starting` 很久;以**目标 unit 的 `is-active`** 为就绪判据。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-03`

## QTS(x86_64)固件 | 直接跑 CGI 会因**绝对路径 `stat`** 落到宿主 → 用 `bwrap --bind <rootfs> /` 造命名空间 | (2026-10-03/TS-464)
厂商 CGI 用绝对路径(如 `stat("/home/httpd/cgi-bin/quick/quick.cgi")`)做状态判定;`ld.so --library-path` 只解库、**不改绝对路径**,
CGI 于是判成"文件不存在"走错分支(表现为与"造出的状态"不符)。x86_64 固件与宿主同架构时**不需要 qemu**:
`bwrap --bind <rootfs> / --dev /dev --proc /proc --tmpfs /tmp <绝对路径CGI>` 即可让绝对路径落进固件树(比 chroot 轻、无需 root)。
ARM 目标用 `qemu-<arch> -L <rootfs>` 自带同样前缀语义。

## QTS rootfs | 原生化进 Docker 可跑完整 web 栈(`apache_proxy` 才是默认前端) | (2026-10-03/TS-464)
x86_64 的 QTS rootfs 可 `tar | docker import`(排除 `dev/proc/sys`),容器内即真 `/etc/passwd`(root=admin)。
默认拓扑: `Qthttpd.sh start` 解 `/opt/source/apache_php5.tgz` 到 `/mnt/ext/opt/apache`(软链 `/usr/local/apache`)⇒
`thttpd.sh:start_httpd` 见 apache 可执行就 `PORT=InnerPORT`、`_thttpd_` 绑 127.0.0.1:58080,并起 `apache_proxy`(8080,`php-fpm-proxy` 以 user=admin 跑)。
坑 ①: 若 `/usr/local/apache` 先被建成**目录**,`ln -sf` 会在里面建软链而非替换 ⇒ `-x /usr/local/apache/bin/apache` 假,须先 `rm -rf` 再建软链。
坑 ②: 缺 `libpcre.so.1`(在 `apache/links/`)时 Apache 起不来 → `thttpd.sh` 静默回退成 `_thttpd_` 直绑 8080(**前端拓扑变了**,判读要注意)。

## QNAP QTS **x86_64** 版拿真会话(admin / 非管理员)的建场配方(本目标实测) | (2026-10-04/TS-464)
- **为什么**:认证后分派器的"action 级授权/委派位"只能靠**真会话**做三态差分(无会话 / 低权 / admin);QTS 的会话表在 **SysV 共享内存**(`ftok("/tmp/session",0x39)`,`shmget(key,0x578004,0x3ea)`,`libuLinux_nasauth.so`),不是进程内。
- **配方**:
  1. 解密 QELF 的 `authLogin.cgi`(本目标 `e_flags=0x40000000`):`tools/qelf_decrypt.py` 是按 ARM64 写的;复制一份把 capstone 改成 `Cs(CS_ARCH_X86, CS_MODE_64)`,以 `<rootfs>/home/httpd/cgi-bin` 为 base 跑 → 产出 `*.cgi.dec`(x86_64 这版 8 个 QELF 文件,decode 86–90%)。**坑**:首跑 `recover_keystream` 可能报 `IndexError: list index out of range`(加一行 `if idx>=len(lp): raise` 复跑即过,疑与 `pages` 某页短读有关)。
  2. `chmod 755 authLogin.cgi.dec`(非 QELF 的几支也顺带解出:`azureLogin/authLogout/privRequest/privWizard/disk_manage` 等)。
  3. 把 `<rootfs>/etc/config/shadow` **与** `<rootfs>/etc/shadow` 里 `admin` 的 MD5-crypt 换成已知口令(`openssl passwd -1 -salt <s> <pw>`,两文件都要改)。
  4. `POST`(无 `func=`,带 `func=login` 反而返回 `(null)`)`user=admin&pwd=<base64(明文口令)>` → 响应 `authPassed=1` + `authSid=<8字符>` + `isAdmin=1`。
  5. **非管理员**:在 `etc/{,config/}passwd` 加 `testu:x:1000:1000::/share/home/testu:/bin/sh`、`etc/{,config/}group` 加 `testu:x:1000:testu`、`etc/{,config/}shadow` 加同格式 hash(四个文件都要,`getpwnam` 读 `/etc/passwd`,但 `getcfg`/shadow 走 `/etc/config/`);再 `user=testu` 登录 → `authPassed=1`,但 admin 动作在同一入口被 `User_Belongs_To_Group(...,"administrators")` 拒(`authPassed 0`)。
- **坑(=必做)**:会话的 shm key 来自 `ftok("/tmp/session")` 的 **inode** ⇒ 若每次 bwrap 都 `--tmpfs /tmp`,两次进程的 key 不同、会话**读不到**。做法:**固定一个文件**(如 `artifacts/work/sesstmp/session`)并 `--bind <sesstmp> /tmp` 给**登录与后续每个 CGI 同一份** ⇒ 会话跨进程有效(实测:`qlicenseRequest.cgi` 带 sid 返回业务码 `-977` 而非 `auth get session fail`)。
- **判据**:会话有效 = 带 sid 的 CGI 返回**业务码**而非 `auth get session fail`(或无 sid 必失败)。

## `pkill -f "<串>"` 会匹配到**自己**的命令行 ⇒ 自杀(实测 2026-10-04)
- **现象**: `pkill -f "qemu-aarch64 -g"` 在自己的 `bash -c` 命令行里也含该串 ⇒ 把自己那条命令杀掉,后续输出全无、退出码 143。
- **解法**: 先用 `pgrep -af <串>` 看命中;或用字符类规避(`pkill -f "[q]emu-aarch64"`);或直接从 `ps` 取 PID 再 kill。

## 二进制载荷务必以 **bytes 模式**写文件(否则被 UTF-8 重新编码,地址就变了) | (2026-10-04)
- **现象**: `bytes([0x90,0x1f,0x40]).decode('latin1')` 用 `open(p,'w')`(默认 UTF-8)写出 ⇒ 文件里成 `c2 90 1f 40`;实测使 `PC` 从预期 `0x401f90` 变成 `0x00000041…`(多出 2 字节)。
- **解法**: 一律 `open(p,'wb')` 并拼 `bytes`;构造后**回验尾部字节**(`xxd`/`hex(' ')`)。

## 厂商签名镜像解不出(automated 提取器零命中)时:查镜像内**解密后的 raw/FIT 文件**里的 squashfs 偏移 | (2026-10-04)
- **现象**: Linksys 签名 `.img` 上 `binwalk`/`unblob` **零签名命中**;但同目录的解密后 `raw.bin`(FIT)内 `0x280000` 处直接是 **squashfs**。
- **解法**: 对 raw/FIT 做一次 magic 扫描(`hsqs`/`sqsh`/`gzip`/`xz`)⇒ `tail -c +$((off+1)) raw.bin > x.squashfs && unsquashfs x.squashfs`(退出码 2 = 仅非致命告警,文件齐)。
- **附带结论**: "重建配方"里写"`binwalk -Me` 即可"是**未经验证的想当然** ⇒ 配方必须实测过一次再入库。

## 断点下在 PLT 函数上会被**正常流程**先命中 ⇒ 证明"被劫持的调用"要断在自己的跳转目标 | (2026-10-04)
- **现象**: 为证明"劫持后执行了命令"而 `break system@plt`,第一次命中的却是程序正常流程里对 `uci ...` 的调用 ⇒ 误判。
- **解法**: 先在**自己选定的跳转地址**下断(证明落点),再往下继续到命令解释器入口取参数。

## qemu-user 的 `-L` 只翻译文件类系统调用;**`execve` 不翻译** ⇒ guest 的 `system()`/`popen()` 跑的是宿主二进制
- **现象(三条实测证据,2026-10-04)**: ①`qemu-aarch64 -L sysroot sysroot/bin/ls /` 列出的是 **sysroot** ⇒ `open/getdents` 等**文件类系统调用被翻译** ✓;②把 guest 的 `/bin/sh` 换成"只打印标记"的静态程序后,目标程序里的 `system()` **仍然**输出宿主 shell 的错误格式(`sh: 1: cmd: not found` = 宿主 dash;guest busybox ash 是 `sh: cmd: not found`)⇒ 标记从未被打印 ✓;③`qemu-aarch64 -L sysroot sysroot/bin/busybox sh -c '/bin/ls /'` 列出的是**宿主根目录** ⇒ **`execve` 未被翻译** ✓。
- **后果(为什么特别大)**: 凡"设备**执行了**某条命令 / 读到了设备上的文件"的结论,在 `qemu-user -L` 下**都不成立** ✗ —— 跑的那可能是**宿主**的程序与文件系统。可用的只有"确实发起了一次 system/exec"这一层。
- **正确做法(实测可用)**: 用**容器(chroot)**——镜像根 = 设备 rootfs,里面放一个**宿主架构的静态 qemu**,`ENV QEMU_LD_PREFIX=/`,入口 `qemu-aarch64 -L / <guest 程序>` ⇒ 容器内 `execve("/bin/sh")` 解析到**设备自己的** `/bin/sh` ✓。最小形态 `FROM scratch` + `COPY qemu-aarch64-static /usr/bin/qemu-aarch64` + `COPY rootfs/ /`(实测:设备自带的 `uci` 真跑起来,输出 `uci: Entry not found` ✓)。可复用配方见 E8450 挑战包里的 `Dockerfile`。
- **检测口诀(想知道跑的是谁)**: 看**错误消息格式**(busybox ash `sh: cmd:` vs dash `sh: 1: cmd:`)· 或塞一个**只打印标记的静态 `/bin/sh`** · 或 `ls /` 看根目录是设备还是宿主 ✓。
- **更省事的正确配方(2026-10-05 实测,优先用它)**: `chroot` 到目标 rootfs + 把**宿主架构的静态 qemu** 放进 rootfs(如 `rootfs/usr/bin/qemu-aarch64`):
  `rootfs/usr/bin/qemu-aarch64 -L /` 由 chroot 里的 qemu 跑目标程序 ⇒ `execve` 只能在 chroot 内解析 ⇒ 拿到的是**设备自己的** `/bin/sh` ✓(实测:`unshare -r chroot sysroot /usr/bin/qemu-aarch64 /bin/sh -c 'touch /tmp/x'` ⇒ 文件落在 `sysroot/tmp/` ✓,宿主 `/tmp` 无 ✓)。
  宿主不是 root 也能用:`unshare -r`(用户命名空间)提供 namespace 内的 root ✓;**不需要 docker** ✓;设备 busybox 一般**没有 `timeout`**,超时要用**宿主**的 `timeout` 包住(注意别包在 shell 函数名外面,否则 125/127 ✗)。
- **其它等价路线**: ①`binfmt_misc` + `qemu-user-static`(内核自动用 qemu 跑 aarch64 ⇒ 直接 `chroot rootfs /bin/sh` 即可,rootfs 里**不用**放 qemu;代价 = 要注册 binfmt,宿主要 root / 容器要 `--privileged`)②容器(docker/podman)= 包装好的 chroot ③`proot`(纯用户态,免 root/chroot 权限,但慢)。
- **踩坑记录(2026-10-05)**: 一度给挑战包同时写了 docker 与 chroot 两套 runner ⇒ 又挂又重复 ✗;正确做法 = **只留一条能通的**(chroot + 静态 qemu)✓。

## QNAP TS-X32 更新镜像:rootfs2 是 UBI/UBIFS 且不含 rootfs1;QPKG .bin 两类布局
- 目标:QNAP TS-X32(TS-932PX)QTS 5.2.10.3577 更新镜像(2026-10-19 实测)。
- **更新镜像不含 rootfs1**:`.img` 解密后 gzip→tar 顶层只有 `uImage`(kernel,无真实 initramfs;内嵌 cpio 仅 376 B)、`rootfs2.ubi`、`qpkg.tar`、`rootfs_ext.tgz`、u-boot。`/bin /sbin /etc /init.d` 在设备 mtd 的 rootfs1,**更新包不带** ⇒ 分析基座层需借**同版本同架构**镜像(本次用 TS-233/TS-X33 的 rootfs_merged),并在结论里标注 `/etc` 取值的来源风险。
- **rootfs2 是 UBI/UBIFS**(不是 TS-X33 的 initrd.boot):`ubireader_display_info` 得 PEB 128KB/LEB 126976/单卷 1205 块;`ubireader_extract_images` → `*.ubifs`,再 `ubireader_extract_files` 得 `boot/{rootfs2.tgz,qpkg.tar,rootfs_ext.tgz}`;`rootfs2.tgz` 实为 **LZMA**(`xz -dc`)→ tar(`lib/modules`,`opt/rfs_{usr,home}.img`);`debugfs rdump` 出 `/usr`、`/home`;`rootfs_ext.img` 挂 `/opt/source`(ext3,可能为空)。
- **QPKG `.bin` 两种布局**(全部 11 包装到内层):A 类 = shell 头 + 直接 gzip(FileStation5@975/NotificationCenter@973/QuLog@979/ResourceMonitor@776/Samba@1188/SSDProfilingTool@725);B 类 = shell 头 + control tar + 独立 `data.tar.gz`,data 起点 = `script_len + 20480`(或 71680),长度 = 脚本里 `f.seek(L);f.truncate()` 的 L(netmgr 1361/21841/7693394;LicenseCenter 1846/73526/1093434;MyCloudNas 1837/22317/3429357;QcloudSSLCertificate 1868/22348/391445;helpdesk 3490/44450/1720151)。A 类用 gzip magic 直接定位,B 类按脚本自身偏移公式切。
- **可跑**(qemu-user):宿主 `/usr/bin/qemu-aarch64` 为静态链接,可 `bwrap --bind <rootfs> /` 后直接执行 `/qemu -L /`;缺 ICU 的 CGI 用 4 个 `ucol_*_68` 桩 + 空 `libicuuc/libicudata` 自造垫片(见既有条目)。

## QNAP 多路复用 CGI:多个 CGI 名是同一二进制的软链,按 argv[0] 分派(按文件统计鉴权会漏分支)
- **现象(QNAP 5.2.x 实测)**: `/home/httpd/cgi-bin` 下多个 CGI 名是**同一二进制**的软链;真二进制 `main` 用 `basename(argv[0])` 分派到不同 handler。实测一组 5 个:`jc.cgi`/`mediaGet.cgi`/`sysinfoReq.cgi`/`language.cgi`/`qnapmsg.cgi` 全指向 `qtsReq.cgi`。
- **审计陷阱**: 按"文件"统计导入(`readelf -sW | grep auth_get_session`)会把整文件判为"有会话门",但实际**只有 sysinfoReq 分支**调 `auth_get_session`/`User_Belongs_To_Group`;其余 4 个分支是**预认证公开资源**。同理,`qnap_exec` 的调用者分布在无会话分支里,按文件看会以为"有门"。
- **判据(可复用)**: ①先查软链/`cmp` 多文件是否同一 inode 内容;②反编译 `main` 找 `basename(argv[0])`/`strcmp("<name>.cgi", …)` 分派;③按**分支**判门与汇,绝不按文件判;④别名清单可从部署脚本的 `ln -s` 得到。
- **本组各分支的安全要点**: jc=读 `/home/httpd/jc/<f>` 且 `strstr(f,"..")` 即拒;language=`lang_<cookie nas_lang,16B>.js`(前缀 `lang_` 阻断 `..` 段);qnapmsg=`lang` ≤4B + `Is_Valid_Language`;mediaGet=`f` 固定枚举 + `n` 走 `%d`;sysinfoReq 有会话,但**会话失败时仍以 guest 跑 `/sbin/user_cmd -5` 并回显**(设计内的登录页信息,未逐字段核)。
- 来源: QNAP-TS-932PX QTS 5.2.10.3577 / 2026-10-19(同源码 TS-233/464 可比对)。

## QNAP CGI 的路径/权限校验函数族:判'无校验'前必须全族 join(只 grep realpath 会误标)
- **场景**: 判 QNAP CGI(FileStation `utilRequest.cgi` 等)某 handler "有没有路径/权限校验"时,只 grep `realpath`/`sub_C7484`/`strstr("..")` 会把大量 handler 误标"无校验"。
- **实测(5.2.10 ARM64,119 handler 全量)**: 厂商把检查分散在多个函数族,至少包括:
  `sub_C70B0`(路径解析)、`sub_CEA1C`(link/权限)、`sub_C7798`(ACL)、`sub_C7484`/`sub_CAE38`/`sub_C8750`/`sub_C7B20`(realpath+ACL 变体)、
  `sub_CC3A4`(`..` 组件正则)、`sub_122FA8`/`sub_123F64`/`sub_123854`(参数入口校验)、`sub_124940`/`sub_124ABC`/`sub_125B7C`(动作前置校验)。
- **判据**: 先跑"handler → 上述函数族"的机械 join;只有**全族都未命中**且有文件/命令汇的 handler 才进人工复核。
  本次全量 119 handler:首轮(只认 5 个)得 66 个"无 ACL",补全后降到 54 个,其中有汇者 14 个;14 个逐个判 = 全为 admin-only/服务端生成路径/前缀粘连 ⇒ 无可控越界。
- **补充**: `sub_65D5C`(subtitle)的 `file_name` 在**预认证 share 分支**(`sub_122FA8`)与**认证后**(`sub_123F64`)都过 `sub_CC3A4` ⇒ "file_name 不查 `..`"的旧结论在 5.2.10 不成立。
- 来源: QNAP-TS-932PX QTS 5.2.10.3577 / 2026-10-19。
