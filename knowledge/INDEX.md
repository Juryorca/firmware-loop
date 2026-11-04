# 知识地图(常驻上下文的只有本文件;按关键词定位后加载具体文件)

> **权威**: 知识库**政策**在 `AGENTS.md`「Knowledge Base」节;本文件只是**登记册**(文件/条数/状态/关键词)。
> 两处不一致时以 AGENTS.md 为准。新增文件必须先在两处登记。
> 状态列: `有效` / `降级(仅核对)` / `退休(待删)`。

| 文件 | 收什么 | 条数 | 状态 | 最后更新 | 关键词(检索用;长尾在文内小标题) |
|---|---|---|---|---|---|
| `vuln-patterns.md` | 本工作区**实证成立**的漏洞形态 | 24 | 有效 | 2026-11-04 | getenv 空指针,命令注入,转义漏反引号,免认证分派表,memcpy 长度下溢,构包函数溢出,CWE-307,派生连接无身份/无来源绑定,nginx auth_request 目标 location 漏配 2xx 放行,无旧口令改写用户/owner 口令→ATO,内部集成端口只靠网络隔离/无认证强操作端点,同族 handler 检查项差分,设备上传端点漏校验,未认证内容注入,按唯一 id/hash 取对象的管理 API 漏 owner 过滤(share-link edit/delete),前端改写 REMOTE_ADDR=XFF(QNAP),文件模式位代理鉴权状态,整表免鉴权,URL host 校验冒号截断+原串回填,XML 实体膨胀无认证 DoS(内嵌 Python) |
| `misreports.md` | **勿报判据**(看着像漏洞、判据上不成立;只收跨固件可复用的) | 27 | 有效 | 2026-11-04 | 上游信任身份头≠可网络利用, 限流器饱和,锁定/退避,勿报判据,跨来源,负对照,nv 报文类型混淆,复合键,配置来源,值域可达性,可预测 capability,时间窗,瞬时越界,部分块还原,CBC,净变化,SET 类型不校验,定长 rdata 越界读被 cursor==end 抵消,UPnP CALLBACK 设计内,预认证白名单≠无鉴权,特性开关门控(404),null-vs-undefined 比较,直连后端 socket 复现,请求体身份字段,先 grep 官方前端,HTTP 200 非鉴权信号,业务码在体内,recover 的 panic,预认证限流器饱和,per-source vs global,跨来源单测,SAML AudienceRestriction,Recipient 绑定,断言 ID 重放存储,SAML ACS 加固缺口,bearer token,randomUUID,共享秘密,Koa ctx.get 空串,koa-router .use 跨 router 不生效,角色门vs权限掩码门,受限主体存在性,权限降低无效,CWE-272,role.system_key 唯一,exec/ProcessBuilder 非注入(追插值数据源),argv 数组 vs shell 字符串,硬编码默认密钥≠漏洞(追验收门),中继复制 Host≠中继独有能力,中继独有能力判据,默认前端拓扑,代理中和客户端头信任,loopback 后端 |
| `methodology.md` | 能跨固件复用的**做法/判据**与陷阱 | 55 | 有效 | 2026-11-04 | 假设锚定,建场 preset,双实例差分,没对照就停,强方法两轴,覆盖率 fuzz 缺口,托管语言 harness,托管运行时补格法,内嵌 Swagger 路由表,SAML oracle 矩阵,内存纪律,可证伪判据,验证双输出,函数级差分,差分定位,修复点审计,动态验证环境,CHR,qemu,配置值域,门控分支可达性,可预测索引,预置连接池,micro-race,闭源协议服务端,存活哨兵,结构化变异,Unicorn,ET_REL,重定位,指令流差分,净变化,guard page,SSTP,OCI 容器固件,docker 直跑,反代 auth_request,身份头信任,头伪造负对照,认证缓存键,Go 后端路由经验枚举,404 判据,直连后端,运行态监听为准,portmap 非依据,UDP 发现应答判读,默认凭据×listener 判读顺序,koa-router .use 范围,跨 router 不生效,可达性用产品特性表判,ssl_verify_client缺失,头值来源变量,就绪判据含 app runnable,filter 名≠鉴权点,逐端口 URI 允许表,输出目录参数 canonical 校验,src==dst 自毁,子工具默认值不能外推到父工具,先查厂商端口参考文档判可达,dispatcher @Import 收敛 handler 集合,检查项差分,handler 到达 vs 路由不存在,鉴权边界解析差分矩阵,content-type 区分静态兜底,unifi链路thread dump点名监听类,portmap≠发布面全集,DSM webapi 两层鉴权,compound 子请求鉴权,CheckPermission stub,grantable grantByUser,认证后 API 可达性差分,前置 scope 门伪非 105,对角验证,有效 allowUser 对账,同 API 内逐 method 授权语义不同,前置失败 vs 拒绝执行,补丁无关性全文件调用图,方法级 allowUser 覆盖 api 级,Go gopclntab debug/gosym 解析,转发字段可控性=赋值点+动态捕获,Synology synowebapi --exec 派发,sink 记录器,api/lib 两套注册表,先问正常流程有无凭据(防夸大/防漏报),native AOT vs 姊妹产品 Java 字节码反推,CFR 反编译,ace.jar internal-dependencies,201/@ResponseStatus 非创建,port×URI allow-list 逐条审,状态旗标代理鉴权,整表枚举 |
| `tool-quirks.md` | 工具/环境怪癖与解法 | 94 | 有效 | 2026-11-04 | 代理,CRLF,idat,execute_sync,bwrap,qemu,zig,sasquatch,RouterOS nova 总线,LD_PRELOAD,CHR,TFTP 临时端口,RouterOS REST,hostfwd_add,截图护栏,qemu user-net UDP 包上限,qemu 10.0.2.2 UDP 不通,DoH h2,UPnP Host 头/SSDP 不可达,UniFi OS 本地 IPC 11081,ie 路由面,可信本地通道,WS 升级探测坑,HTTP/2 禁 Upgrade,WS 同源检查 500,101 非鉴权信号,--network=host 致 unifi-core 崩溃,Invalid MAC,TLS1.3 mTLS 判定要发应用数据,Rust stripped 字符串blob,IDA 不建条目,字节扫 RIP-relative lea,find_regex,decompile 分页字段 code,GoReSym Start/End 恒定偏移 bug,Go 二进制反汇编须自解析 pclntab,UOS_UUID 必须 v5,unifi crash loop,Invalid uuid version,就绪判据 is-system-running 不够,Spring-Boot fat jar 类在 BOOT-INF/lib,无 decompiler 用 javap,Synology PAT 加密容器,synodecrypt,patology,type0 密钥,hda1.tgz 实为 XZ,rd.bin LZMA cpio,DSM webapi 仿真须 synoscgi,entry.cgi 102,unshare mount chroot 致 WSL 重启,DSM 认证后 API 需 SynoToken,119 非会话异常,AbnormalAccess 仅告警,per-app 特权 111,appPriv 空则可达,APIRunner 942 cannot open library,volumetab 6 列格式,synoshare 卷检查,synoshare.db BSD dbopen,libsynocore dbopen,FileStation 前置,system.synoacl ENOTSUP,FileStation 写路径不可动态验证,固件自带 7z 无 Zip-Slip,直接跑目标二进制当 oracle,DSM ftpd 起场(SecurityPolicy nosecure),ftpd 无 chroot 依赖,fork 是否受上游 CVE 影响先验 primitive,smbftpd 上游源 diff,avahi/mDNS 投递须隔离 netns+veth,DSM SLIBCExec/Popen 是 execv 非 shell,Share-Link 动态前置(sharing.db/HTTP_HOST/create 407 墙),r2ghidra 装不上(Debian 包 ABI/sdb 错位),busybox applet 软链被覆写,hda1 busybox 实为 wget,pkill 自匹配,timeout -k 杀挂死 qemu,synowebapi --exec 是特权通道(不能当预认证 oracle),entry.cgi 102 真因=cwd 必须为 webapi 目录,llvm ld.lld 交叉编译 aarch64 链 guest 库,section header 缺失用 unresolved-symbols=ignore-all,authLevel=2=预认证,privileged docker 绕 userns devtmpfs bind,QNAP CGI 动作→参数表机械取法(r2 axt+pd,adrp 数值可能在注释里),QELF 解密件 qemu Exec format error 走 ld.so,rootfs .so 440 致 qemu 静默加载失败(chmod a+rX),GraalVM 宿主识别 qemu-user 受阻,/proc/cpuinfo 被 qemu 拦截无法覆盖,qemu-user nginx io_setup ENOSYS,rootfs 容器化 systemd(宿主侧 tar),tar 漏 0440 文件,bwrap bind rootfs,QTS docker 全栈,apache_proxy 默认前端 |
| `exploits-validated.md` | 已验证利用链 | 9 | 有效 | 2026-11-04 | 已验证,pre-auth,栈溢出,RouterOS mtget,QNAP QFinder 未认证上传⇒root |
| `sdk-bloodlines.md` | SDK/格式识别特征与谱系 | 7 | 有效 | 2026-11-04 | SDK 识别,固件指纹,RouterOS npk,nova,libumsg,QNAP WSD |
| `intel-log.md` | **情报与实证日志**(改变过计划的新信息;每条必带 `增量`) | 60 | 有效 | 2026-10-19 | 情报,NVD,公告,changelog,增量,RouterOS 7.24.x,函数级差分,L2TP,btest,related,graphing,acme,allocator key,ppp 内核模块,REST 默认服务,SSTP,CBC,内核加速器,VR-04 撤回,厂商报告渠道与受理范围,MITRE 请求表单与发布条件,赏金计划,Ubiquiti,厂商端口参考文档判 Ingress,UniFi Network 内置 runnable,UniFiOSServer-5.1.42,Network 连接器路由,ubios 连接口,XFF 信任,support-dump 未认证,podman 覆盖 pasta 默认,NETWORK_MODE 存宿主,Synology,QNAP,NETGEAR,Xiaomi,Network /v2/upload dispatcher,proav snapshot 未认证上传,TNBU inform 帧与默认键验收门,Network UDP/TCP 监听类点名,wifiman 5 映射与未认证读,DS223,7.3-81180,DSM 7.2.2-72806,PAT 解密,webapi 423 API 17 预认证,DSM SynoToken 二次校验,认证后三态矩阵,H-05 定论,per-app 111 门,smbftpd 上游源,CVE-2020-7468 primitive,fork 判据,avahi Synology 改写 |

维护规则:
1. 新增条目先过 `AGENTS.md` 的**文件准入与归属**表与**成本自问**(重获成本 > 检索成本 才收)。
2. **实例 vs 新条目**:同一条模式在新目标再次命中 → **追加“实例”行到既有条目,不新建条目**;
   仅当验证手法或前提有本质差异才新开(判断:新条目的“复用场景”能否被既有条目的复用场景包含)。
3. **改知识库一律走工具(硬)**: 禁止手写(read+write)改 `knowledge/`;用 `tools/kb.py`:
   `check`(体检)· `list` · `add --title T --body F`(追加条目;标题唯一、禁目标内路径)· `append-line --entry K --line L`(如实例行) · `downgrade --entry K --note N` · `index-sync` · `snapshot` / `restore` / `selftest`。
   工具内置:**文件锁**(并发会话串行)+ **原子替换** + **改后结构自检** + **写坏自动回滚原字节** + INDEX 计数自动同步 ⇒ 多会话同写也不会互相写坏。
   体检同时挂在 `tools/derived_check.sh` ①e(围栏 / 重复标题 / NUL / CRLF / 冲突标记 / 截断);快照点在 `knowledge/.snapshots/`。
4. 条目**变更**时同步关键词与条数;**降级/退休**必须在状态列体现,并在文内加 `> 降级说明(日期)`。
5. 归属不符就**搬家**,不允许“就近塞进 vuln-patterns”(历史教训见 `AGENTS.md` 的 Write Admission)。
6. 关键词只写**检索时真会用的词**;长尾描述留在文内小标题,不要堆到本表。
7. 各 `知识文件` 的**头部只放条目格式 + 一行收口指针**;归属/准入/维护规则**一律不在文件头重写**
   (政策在 `AGENTS.md`。历史教训:同一套规则曾在 政策 / 本表 / 7 个文件头 三层重复 = 漂移源)。
8. **重复只在指针层面允许**: 同一事实只允许**一个权威副本**;其他文件只能写指针(或作为举证提及),
   **不得重复正文**。判定法: 两个地方的同一事实如果两边都“完整陈述”= 副本(拆);一边陈述一边指路 = 交叉引用(留)。
9. **盲点审计**: 仅当某文件条目 ≥5 时,项目结束跑一次“本文件漏了什么形态”;连续 2 次零盲点 → 该文件合并进提示词并归档。

---

## 附:知识库政策的叙述与维护细节(自 `AGENTS.md` 迁出;政策本体仍在 `AGENTS.md`「Knowledge Base」)

> 知识库只收录会话中**实际产生验证成本的经验**(lessons learned);公开可检索的通用知识不收录 —— 需要时按检索义务表现查。
> **文件准入与归属、写入准入、降级与退休、检索义务、Provenance、假设三通道**的规则见 `AGENTS.md`「Knowledge Base」;本节只放原本写在那里的解释性文字:
>
> - **原理**: 知识库的价值在"少走重复的路"。一条经验值不值得收,看它**重新获得的代价**是否高于**检索到它的代价**;
> - **索引维护**: 每行必须带 `条数 / 状态 / 最后更新 / 关键词`;新增文件先登记再写内容;条数与文件内实际条目数由 `tools/derived_check.sh` 核对(漂了就报 FAIL);
> - **重复的允许范围**: 只允许**指针层面**的重复(一处权威 + 别处"见 X"),不允许正文复制 —— 正文复制必然漂。
