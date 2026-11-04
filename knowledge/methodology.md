# 方法与判据
> 条目格式: `## <做什么> | 判据/步骤 | 证据/产物 | (日期)`
> 收口/准入 → `AGENTS.md`「文件准入与归属」;**本文件只放条目,不重复规则**。

> **来源标注**:条目里的 `<目标>/…` 仅为来源标注(该目标的证据随目标清理,**不要求长期存在**);复用依据是条目自身的判据/反例/现象。

## 情报锚定的假设轮次会整轮白做 —— 教训与判据 | (2026-09-18)
- **教训(代价实证)**: 规则已写“公开情报=排除清单,不是假设来源”,但实践中注意力会被 changelog 牵着
  (任务 §0 一句“7.24.2 是安全修复版”→ 去查 7.24.3 → 一池“修复不完整”类假设)。结果: 8 条假设,
  7 条否证/未完成,唯一“成立”的一条复核后判为非漏洞 → **净产出 0**。
- **可复用判据(跨固件)**: ①假设入池前必须能写出**偏差陈述**(相对设计意图/同类实现/相邻路径错位在哪)
  与**可证伪判据**(什么观察能杀掉它);写不出者不入池(否则会永生并伪装成进展);
  ②验证中产生的观察**优先于**情报派生条目;③验证是**双输出** —— 判定(HIT/MISS+证据) + **本轮新观察
  (强制,MISS 也要写)**,否则“否证 ≠ 面已穷尽”只是口号。
- **规则本体**(怎么分通道/怎么晋升): 见 `AGENTS.md`「假设机制:三通道」—— 本文件只留教训与判据,**不复制条文**。
- **另一处代价(观察会蒸发)**: 验证途中的事实若不落成“观察”,会话一结束就没了。本轮 3 例:
  构建布局噪声→被利用;`user` 的按来源控制→正是它翻了案;MNDP `s[6]`/ASN.1 结构/IKE 记录表→**全丢**。
- 项目证据: `RouterOS-7.24.4-arm64/notes.md` §5/§6、`RouterOS-7.24.4-arm64/artifacts/poc/obs01-*`。

## 任务给定版本号 = "已修复版"时的工作流(前提证伪 + 补丁差分) | ⚠️仅作核对,不作假设来源
> **降级说明(2026-09-18)**: 本条曾被当"漏洞模式"登记,是情报锚定的制度来源之一。现降级为
> **核对清单**:只有当你已经**独立**从攻击面得到"这个组件/路径可疑"时,才用它去确认"是否已被官方修掉"。
- 现象: 任务/情报把目标版本 N 与技术描述绑定,但情报原文同时写着"fixed in N"(或 "before N")——
  即 N 很可能是**修复版**,直接按描述找 PoC 会一无所获。
- 流程: ①先读全 NVD/公告/changelog 的 affected/fixed 边界;②落地补丁差分(见下条与 tool-quirks
  「构建布局噪声」:字节/md5 diff 只能当线索);③对差分命中的组件读**修复前后同一函数指令序列**判完整性;
  ④结论若为"已修",产出仍应包含根因、受影响版本、**在 N-1 上的可复现 PoC**、以及"N 上同输入被拒"的差分。
- 产物: `RouterOS-7.24.2-arm64/report/`(advisory + INDEX 的 Premise verification 节) | (2026-09-15/RouterOS 实证)

## 目标版本若为"安全修复版":它之后的版本要单独查 | ⚠️仅作核对,不作假设来源

> **降级说明(2026-09-18)**: 同上,降级为核对清单。
- 判据(可复用): ①目标带版本号 → 先列官方 changelog 目录(不必猜 URL,`<ver>/CHANGELOG`),确认有无**更新**版本;
  ②对"下一版"做逐二进制比对,变化文件里出现**同一 CVE 编号**或**新增校验错误串** = 上一版修复不完整的强信号;
  ③同一函数跨**三个连续版本**三次改动,基本可断定前一次是半成品。
- 实证: RouterOS 7.24.2 → 7.24.3:changelog 再次出现 CVE-2026-67278 → 差分 `lib/libucrypto.so`
  `RsaPublicKey::parseHashFromDerEncoded` 7.24.1=`0x140`/7.24.2=`0x1b8`/7.24.3=`0x234`,7.24.3 新增
  `empty digest info`、`digest info does not span whole block`(7.24.1/2 的 ELF 中 `grep -ao` 验证不存在)。
- 产物: `RouterOS-7.24.2-arm64/notes.md` §1/§3 | (2026-09-17/RouterOS 实证)

## 动态验证环境选型:整机 vs 函数级 | (2026-09-18)
| 需要验证的东西 | 环境 | 判据 |
|---|---|---|
| 内存破坏类解析器(buffer 进 → 崩/不崩) | arm64 npk + `qemu-arm` + **LD_PRELOAD 构造器 harness** | 见 `tool-quirks.md`「RouterOS 7.x 守护进程无法离线整机仿真」 |
| 逻辑/认证/协议/时序类(如限流、会话、可达性) | **官方 CHR(x86_64)整机 @ qemu** | 见 `tool-quirks.md`「RouterOS 动态验证: …CHR…」 |
**别再说"没有运行环境"**:整机仿真走不通的是 **arm64 npk 用户态**;CHR 是另一条整机路径。

## 构建噪声下用**函数级差分**定位“improve stability”式修复点 | (2026-09-19)
**何时用**: 厂商 changelog 只写 `X - improve stability`/`improve stability of...`(无 CVE、无细节),
但版本间该 ELF 确实变了 —— 想知道“到底改了什么、改得完不完整”。适用于任何被 strip、
且相邻版本存在整体位移的固件二进制(C/C++ 守护进程、路由/NAS 固件通用)。

**步骤**:
1. `readelf -SW <bin> | grep .text` 比两版 `.text` 尺寸 —— 稳定 ⇒ 基本无真实代码改动,可放弃;
   变了才继续。
2. **不要逐字节 diff**: 一次插入会让后续所有函数地址位移,`cmp`/md5 差异率可达 20%+,
   全是噪声(本工作区 RouterOS 实测 ratio 0.23)。机制与实测数据见 `tool-quirks.md`「构建布局噪声」。
3. 用 IDA `list_funcs` 导出 `(地址, 尺寸)`(注意需要 `queries:[{offset,count}]` 且**每页 100**,
   `next_offset` 翻页);取 `size >= 0x20` 的函数。
4. **按下标对齐会连锁误报**。两种可行对齐:
   - 简版: 按地址序扫描,找第一个尺寸不同的函数,把差值累进 `delta` 再继续(适合只有 1–2 处改动);
   - 稳版: capstone 逐函数**只取助记符序列**做 sha1(屏蔽操作数/字面量池地址),取两版哈希差集。
     差集里再按“助记符条数变化”排序,优先审变化最多的。
5. 对候选函数反编译核对根因;并做**同族逐点核对**——把该功能涉及的所有函数列出来,
   看“另一个有没有同样的检查”(修复往往只加在一处)。

**实证(2026-09-19, RouterOS 7.24.1→7.24.4, `nova/bin/btest`, CVE-2026-67277)**:
742 个函数命中 **6 个**(其余为布局位移 ⇒ 差分法在跨 3 个小版本后仍可用);反编译后把修复拆成三类,
其中**第三类最容易漏看**:
1. **准入判定**: related 分支新增 `if (obj[+5] && !obj[+6])` 才接受(7.24.1 无);
2. **数据边界**: 新增 `if (v18 > v20) reject`(无符号下溢相关);
3. **对象状态位初始化**: 持有 `+5`(已认证)与 `+6`(子连接已消费)的对象是 `malloc(0x1A8)` 分配的,
   7.24.1 **没有**清零这两位(未初始化堆字节),7.24.4 在对象创建处补 `*(u8*)(obj+5)=0; *(u8*)(obj+6)=0`。
→ **判据**: 核对“某处新增了权限/状态检查”时,必须回查**该状态位在哪里初始化**;
若对象由非零填充的分配器创建,只加检查 = 半边修复(检查读到的是垃圾值)。

**实证(2026-09-19, RouterOS 7.24.2→7.24.3)**:
- `bndl/ppp/nova/bin/ppp`(890 个函数)**只命中 1 个**: `sub_3647C` 0x2c0→0x2e4 →
  L2TP 控制报文摘要认证缺上界检查(OOB 写 16/20 字节),7.24.3 补 `end >= data+4*flag+27+len`;
  同族的 `sub_35604` 本就有该检查 ⇒ 修复完整。
- `nova/bin/www`(357/362 个函数)命中 15 个 → 定位到 jsproxy doGet/doPost 与 M2 会话处理
  (内联会话查找 → 带 NULL 检查的 helper)+3 个同族消费者全部判空。
- 反向价值: **`.`text` 每版都变的二进制不等于每版都有真实改动**(www/snmp/net/parser 的
  大量差异是布局噪声),但“函数级差集”能把噪声干净地剔掉。

**产物**: `RouterOS-7.24.4-arm64/artifacts/recon/{ppp-l2tp-auth-fix-7242-vs-7243.md,
www-7242-vs-7243-func-diff.md,btest-related-auth-7241-vs-7244.md}`;可复跑脚本
`artifacts/recon/func-diff-mnemonic.py`(capstone+difflib 对齐,屏蔽布局位移)。

## 差分的正确定位:它是“**技术**”,不是“**工作项**” | (2026-09-18)
- **错误用法(实测代价)**: 把“对 N→N+1 改动的 ELF 做函数级差分”当成一轮的工作项 —— 连做两轮,
  产出 5 条“N 版有缺陷、N+1 已修”的信息项,**按 B2 永远不能成为发现**(目标版已含 N+1 的修复);
  route/snmp 还被切成半成品(定位到 6 个 / 25 个差异函数就停,disposition 未动)。
- **正确定位**: 差分是**在已选定的面上加速定位**的技术;工作项只能来自 surface map 里 `未判定` 的面
  (选面理由 = 可达性 / 输入可控性 / 未理解度;**“changelog 说它变了”不是选面理由**)。
- **差分能得出什么(判据)**: 只能得出“**该面上的某个加固点**”(以及“同族是否漏修”)。
  **若一个面的结论全部来自差分,该面仍未判定** —— 因为厂商**没改过的地方差分一无所知**。
  ⇒ 差分**单独立不改变 disposition**;判“面干净”必须做与差分无关的审计。
- **反向也成立(选面后差分的价值)**: 已选定某面后,若该组件在相邻版本有改动,差分能把“值得先看的地方”
  从全组件缩到几个函数 → 这是它真正的用途。
- **复用场景**: 任何“有官方 changelog + 可获取多版本固件”的目标。
- 产物: `artifacts/recon/*-func-diff.md`(定位记录 —— **不是“该面已审”的证据**)。

## 配置门控分支:先算**值域**,再决定是否审计 | (2026-09-19)
**何时用**: 静态审计时发现某条可疑分支的进入条件是「某个配置项 == 某个值」/「读某个配置文件里的
字段」—— 不要立刻按“可被利用”往下推,**先去配置层确认该取值是否合法存在**。

**做法(成本约 5 分钟,能省掉整轮动态验证)**:
1. 从反汇编找出分支的判定字段,并追出它由哪个配置属性/规则字段灌入(本例:
   `conn+0x1c ← rule+0x38`,即 `/ip tftp` 访问规则的 `reading-window-size`);
2. 在真实设备/镜像上试写该属性的**触发值**(REST 最省事: `PATCH /rest/... -d '{"prop":"<触发值>"}'`);
   设备会把该属性值域里的**非法值直接回错**(RouterOS: `input does not match any value of <prop>`);
3. 逐个试:枚举值、边界值(0/1/255/256/65535/2^32-1)、字符串形态 —— 全部被拒 = 分支**配置不可达**,
   静态上的“越界/溢出可能”不构成发现(不作为发现,也不进假设池的验证队列);
4. 若某值被接受 → 该分支可达,再付动态验证成本。

**判据**: 分支不可达的**充分条件** = 配置模式拒绝一切触发值(含枚举/边界),且没有第二处写入该字段
(用 xrefs 确认 `strb/str` 到该偏移只有配置拷贝一处)。

**实证(2026-09-19, RouterOS 7.24.4 `nova/bin/tftpd`)**: `sub_15B64` 的 5 字节 ACK 窗口位图
(`bitmap[ack_block - obj+0x1a - 1]`)只在 `conn+0x1c == 0xff` 时进入;该字段来自访问规则的
`reading-window-size`,而 `/rest/ip/tftp` 对 `1/2/4/16/64/255/256/65535/4294967295` 全部回
`input does not match any value of window-size`,只有 `none` 合法 → 分支不可达,省掉了一整轮
乱序 ACK 动态验证。(静态记录见 `RouterOS-7.24.4-arm64/artifacts/poc/tftpd-triage-evidence.md` §3)

**反向提醒**: 该技巧**只**能判“不可达”。值域允许时仍需按正常流程审计 + 动态验证。

## 会话派生的可预测整数 key:外部如何预测 + micro-race 场景怎么把 connect 延迟移出关键路径 | (2026-09-19)
**问题**: 服务端用一个整数索引(每次 accept +1)作为会话/派生连接的授权 key,
既要“预测下一个窗口的值”,又要“在对方填满窗口前把帧送达”。

**两步做法(实证于 RouterOS 7.24.4 `nova/bin/btest` TCP/2000)**:
1. **读回 + 计数**:若服务端在某个配置下把该索引回给客户端(本例 `authenticate=no` 时 id 回显,
   `id = (index<<8)|1`),用一条短控制会话读回当前值;此后**每个被 accept 的连接使索引 +1**,
   所以“下一个窗口的 key”= 读回值 + 你之后建立的连接数 + 1。索引在大量连接后会回绕 → 先重启设备或重新读回。
2. **预置已 accept 连接池**:connect+greeting 的往返延迟会把帧送达推迟到窗口之后。
   先建立并保持一批**已 accept** 的连接(它们已拥有各自索引),之后再在这些连接上发帧:
   发帧不再产生新的 accept ⇒ **key 不随发帧漂移**,可对固定 key 反复重试。
3. **窗口宽度决定要不要报告**:先测合法对端填满窗口的耗时(本例客户端收到 id 后
   ~2 ms 内打开全部 related 连接)→ 命中率如实给出(本例 5 次 2 次),
   并把“已接受但未取到流”的中间态也记下来。

**坑**: ①把“新 connect + 发帧”当重试手段 → 每次重试都改变索引,预测失效;
②连接池超过服务端 `max-sessions` 会让后续连接(包括受害者的)被拒 —— 先算容量;
③索引回绕(本例经历 7145→1)会让单次预测落空。

**复用场景**: 多连接会话协议、按 accept 计数分配 id 的守护进程、需要“在极短窗口内注入”的 race 类验证。

**证据**: `RouterOS-7.24.4-arm64/artifacts/poc/btest-related-connection-evidence.md` §3/§4;
PoC `report/attachments/btest-related-connection-unauthenticated-access/btest-related-connection-poc.py`。

## 闭源协议服务端的“最小客户端 + 存活哨兵”变异法 | (2026-09-19/RouterOS ppp L2TP/PPTP)
**问题**: 面对自研/闭源协议服务端(只有二进制),如何在**不做完整协议实现**的前提下,对预认证
报文解析器做有效的边界/变异测试,并可靠判定“是否把服务端打崩”。

**做法**:
1. **先拿一句真实握手**(最省事的来源:用设备自带的客户端连你的伪装服务端抓包,如
   RouterOS `/interface l2tp-client` → 主机 UDP 服务器;或直接按 RFC 构造最小报文)。
   实测一句合法请求能得到合法应答 = **解析链已通**,这就是后续变异的基线。
2. **存活哨兵(liveness canary)**:每 N 个变异后,用**新建连接**发一次**合法请求**;
   有正确应答 = 实例存活。不要用“进程还在”判断 —— 守护进程被 watchdog 重启后 pid 会变,
   而“合法请求无应答”才是崩溃/重启的可靠信号。
3. **变异要有结构**:纯随机字节在成熟解析器上命中率极低。按协议字段分三类:
   ① 长度字段重写(含 0/1/头长/声明长/0xffff);② 变长字段的长度参数矩阵(如 phone-len × subaddr-len);
   ③ 整包截断 + 长度字段同时伪造(触发“先读长度再读体”的错位)。
4. **异步握手要等状态机**:多步协议(如 L2TP SCCRQ→SCCCN→ICRQ)里,**上一步没被接受,下一步
   根本进不了深层代码**。判据 = 上一步的重传停止(或收到期望应答);否则先修握手再变异。

**判据/坑**:
- 变异“无崩溃”**只说明该解析器对随机输入健壮**,不等于该面已清 —— 深层状态机分支可能没被覆盖;
  结论要写明覆盖到哪一层。
- 带“最小长度过滤”的读取(如 L2TP 服务端 `read` 后 `v3<=61` 丢弃)会让**合法短报文(ZLB)**
  也被过滤 → 变异时先过长过滤,否则会误判“服务端不响应”。
- 服务端可能以**源地址**而非报文里的会话 ID 匹配会话 → 同一 socket 贯穿整个会话才能走到深层。

**复用场景**: 固件里自研的 VPN/隧道/管理协议守护进程;任何“只有二进制 + 有状态”的服务端。

**证据**: `RouterOS-7.24.4-arm64/artifacts/poc/ppp-family-l2tp-pptp-triage-evidence.md`;
脚本 `RouterOS-7.24.4-arm64/artifacts/poc/ppp-triage/`。

## 无源码/无仿真环境的内核模块:直接执行固件里的 `.ko` 指令流做差分验证 | (2026-09-20/RouterOS sstp.ko)
- **场景**: 缺陷在 aarch64 内核模块(ET_REL `.ko`)里,既不能整机仿真目标固件(arm64 npk 无设备树),
  也不该把结论停在“静态看起来会越界”。此时可**把模块自身的机器码喂给 Unicorn** 做差分,
  算“动态验证该函数”,而不是重写一份等价实现。
- **做法(可复用)**:
  1. `pyelftools` 读 `.text` 与 `.rela.text`;
  2. 自行应用重定位:`R_AARCH64_JUMP26(282)`/`CALL26(283)` 重算 26-bit 立即数
     (`imm=((S-P)>>2)&0x3FFFFFF`);`R_AARCH64_ABS64(257)` 填 8 字节;
     未定义的外部符号(只会有少量,如 `memcpy`)落 **RET stub**,需要语义的用 `UC_HOOK_CODE` 实现;
  3. **同 section 内的引用不需要重定位**:`adrp/add` 到 `.text`/`.rodata` 的局部地址,
     只要整段 `.text` 一起搬到任一页对齐基址,相对关系不变(实测 `sstp.ko` 只有 257/282/283,
     无 276/278,即无跨 section ADRP);
  4. 调用点参数**照抄反汇编**(寄存器顺序极易看错,务必从 `BL` 前几条指令取);
  5. 缓冲区放“页末尾”+ 下一页设为 `UC_PROT_READ`(只读),越界写会以
     `UC_ERR_WRITE_PROT`/`UC_ERR_WRITE_UNMAPPED` 命中;另用 `UC_HOOK_MEM_WRITE` 记录
     `max_write_end - (buf+len)` 量化越界字节数。
- **判据**: 负对照(满足契约的输入,如 `len%16==0`)必须 0 越界;
  payload(违反契约)越界量应等于可预测公式(本例 `16-(len%16)`);两者同时成立才算 HIT。
- **坑**: ①忘设 `SP` → 第一次 `STP`/`BL` 就报 unmapped,极易误判为模块 bug;
  ②`mem_read` 返回 `bytearray`,喂 `mem_write` 前要 `bytes()`;
  ③hook 里 `raise` 会被 Unicorn 包成 `_hook_exception`,优先靠自然 fault 而不是自己抛。
- 与既有「动态验证环境选型」的关系:那是**整机/函数级**二选一;本条是第三条路 ——
  **对固件内的单函数/单模块指令流做受控执行**,适合内存破坏原语的确定性验证。
- 产物: `RouterOS-7.24.4-arm64/artifacts/poc/sstp_cbc_oob_emu.py`,
  报告包内 `sstp-cbc-record-oob-emu.py`。

## 内存破坏信号:先判“净变化”,再谈越界 —— guard page 只证访问不证破坏 | (2026-09-20/RouterOS sstp.ko 误报)
- **教训(代价实证)**: `sstp.ko` CBC 部分块在原地解密时读写越界 `16-(len%16)` 字节,guard page
  命中写保护 → 判为内核 OOB 写并产出 advisory;真机 12 次投递无崩溃,回头读实现才发现
  `memcpy(out+len, tmp+rem, 16-rem)` 把**解密前保存的原字节写回**,越界区最终逐字节不变。
- **可复用判据(跨固件)**: 断言“越界写 = 内存破坏”前,必须分别回答:
  1. **净变化**: 把目标区预填固定图案,调用后读回比对;不变 ⇒ 不是破坏(只是瞬时访问)。
     (`in==out` 且实现把 `tmp` 写回时常见。)
  2. **分支**: `in!=out` 分支是否用栈上临时缓冲、只写 `len%blocksize` 字节(多数 CBC 实现如此)。
  3. **落点可写性**: 内核里越界目标是同一分配的 tailroom/`skb_shared_info`,可写连续 ⇒ 连 fault 都没有。
- **guard page 的正确定位**: 只用来回答“有没有发生过越界**访问**”;**不得**单独作为“破坏”的证据。
  误报的根因正是把 “WRITE_PROT fault” 当成了破坏。
- **双输出要求**: 判为 HIT 前至少跑一个**负对照 + 净变化比对**;真机/整机以崩溃或日志独立确认。
- 关联:`misreports.md` 反例 4;实例证据
  `RouterOS-7.24.4-arm64/artifacts/poc/sstp-accelerator-reachability.md`。

## 容器/OCI 型固件:用宿主 docker 直跑得到整机动态环境(免仿真) | (2026-09-21/Ubiquiti UniFi OS Server)
- **场景**: "固件"其实是 x86-64 Linux 容器镜像(OCI/Docker),例如安装器尾部 ZIP 内嵌 `image.tar`。
  这类目标**不需要 qemu/仿真**:宿主机(同架构)直接 `docker load -i image.tar` 即可得到逐字节真实的运行态。
- **做法**:
  1. `tar -xf image.tar -C /tmp/oci` 后读 `manifest.json`/`blobs/sha256/<config>` 拿 `Config.Cmd`/`Env`
     —— **别猜启动方式**,镜像的 Cmd/Entrypoint/Env 决定要补哪些变量(本例需 `APP_MODEL`/`UOS_UUID`/
     `PRODUCT_NAME`/`FIRMWARE_PLATFORM`/`APP_VERSION`,由宿主侧安装器注入)。
  2. `docker load -i image.tar` → `docker run -d --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw ...`
     (镜像以 `/sbin/init` 为 PID1 时必须这三项;仅 web 面测试可不加 `--privileged`).
  3. `docker exec <c> ss -lntup` = **A1 入口点枚举最硬的一条证据**(监听地址 = 可达性判定的直接事实,
     比读 systemd 单元/配置可靠);对照宿主侧发布清单(`portmap.json`/`-p`)区分"容器内可达"与"网络可达"。
- **判据/收益**: ①预认证端点的真实响应(200/401/502)与响应体可直接取证,不再依赖推测;
  ②"出厂配置模板 vs 运行态生成配置"可对比 —— 本例把 `usr/share/unifi-core/http/site-*.conf` 模板
  复制为运行态站点并 `nginx -s reload`,即可在**未完成 setup**的情况下触发 post-setup 鉴权链。
- **坑**: ①容器内应用常按"未配置"状态分支(本例 setup 模式 `location /api/` 无鉴权),必须显式说明当前
  处于哪个状态,否则会把"设计内的 setup 模式"误判为鉴权缺失;②完成 setup 可能依赖镜像里**没装**的
  runnable(本例 network 应用按需下载)→ setup 卡住,不要据此判断"漏洞不可达";
  ③宿主代理劫持 localhost:容器内/端口映射的 curl 一律 `--noproxy '*'`。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/*`、notes §8.1。

## 反代鉴权链(auth_request + 身份头):判读顺序与"头伪造"负对照 | (2026-09-21/Ubiquiti UniFi OS Server)
- **形态**: nginx `auth_request` 子请求决定放行,再把身份**以请求头**注入上游(`X-UserId`/`X-UserRole`/
  `X-UserAccessMask` …);上游**只读头、不校验 token**。这类架构的漏洞几乎全在"某条 location 漏了
  `auth_request` / 漏了清头"。
- **判读顺序(缺一步都不能下结论)**:
  1. 找**鉴权判定的完整输入**: 本例子请求用 `X-Original-Method`(`$request_method`)+`X-Normalized-URI`
     (`$uri`),并看到 `proxy_pass_request_headers off`(注释直指历史 CVE)——判定输入是否可被客户端伪造;
  2. 枚举**所有** location,逐条分类:`include auth.conf`(需鉴权)/ `include strip-internal-headers.conf`
     (公开且清头)/ **两者都没有**(高危候选)。清头文件要逐行核对**同族头是否漏清**
     (本例 `X-Group-Token`/`X-UI-Local-VP-*`/`X-Internal-Proxy-Client-Cert` 未清,安全性全压在应用层自查);
  3. 把"身份头集合"当**密钥面**审计: 每个被信任的头都要问"谁能设置它"。
- **负对照(必须做,才敢说"头伪造不成立")**: 构造同请求两条,**一条带伪造身份头**、一条不带,
  比较响应(逐字节/字段);再确认非白名单路径确实 401。只测"伪造后仍 401"不算 —— 要测**已放行的路径**
  是否因伪造头而改变行为(本例: 白名单 `GET /api/apps` 加 `X-UserId=owner` 后响应逐字节不变 ⇒ 清头生效)。
- **认证缓存也算判定输入**: 若用了 `proxy_cache`,**缓存键必须覆盖判定输入**;键缺 URI/方法时,
  一条"放行"结果可能被另一条请求复用。检查顺序: 先看 `proxy_cache_key` 是否含判定输入,
  再看 `proxy_cache_valid` **实际会缓存哪些状态码**(本例只缓存 200,而 URI 相关的放宽分支返回 201
  → 暂不可利用;这一步决定"看起来缺键"是否真能兑现)。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/{nginx-locations,auth-probe,nginx-auth-whitelist}.txt`,
  notes §4/§8.2/O1。

## 预认证白名单端点逐点判读法:反代 → 应用层 middleware → 直连后端复现 | (2026-09-21/Ubiquiti UniFi OS Server)
- **场景**: 目标用"反代 `auth_request` + 身份头注入"作鉴权,反代侧有一份"无需鉴权"白名单(路径/方法)。
  白名单**只是第一步**,不能当结论。
- **三步法**:
  1. **反代侧**:把白名单原文抄出来(方法+**规范化后的** URI;注意反代用的是 `$uri` 而非原始 URI),
     再枚举**所有** location,标注 `auth_request` / 公开清头 / 两者皆无;
  2. **应用层**:对每条白名单端点,回代码里找它落在哪个 router、有没有 router 级 `.use()` 与 per-route middleware。
     ⚠️ **纠错(第 12 轮实测)**: `koa-router` 的 `router.use()` **只对注册在同一 router 的路径生效**
     (与这些路径的 per-route middleware 同链、先于后者运行),**跨 router 不生效**,对无匹配路由的路径也不运行
     (最小 harness: router a 的 `.use` 置 418,请求 router b 的 `/b` 得 200、请求 `/zzz` 得 404)。因此
     **不能假设某个"全局" `.use` 覆盖所有路径** —— 每条路由的防护 = **它自己所在 router 的 `.use`** + per-route
     middleware(本项目第 3 轮曾把 `gC` 路由的 401 误归给 `pe.use`)。并区分门控种类:权限掩码 / 凭据 /
     **特性开关(不满足给 404 而非 401)**;
     (注: 上述旧版写"`router` 级 `.use()` 对**该 router 下所有路径**生效,即使没有匹配路由也先跑"——该句已证伪。)
  3. **直连后端复现**:通过 unix socket 或内网端口直接向后端发请求(**自造身份头** = 模拟"反代已放行"),
     把"反代拒绝"与"应用拒绝"分离;这一步能把"像绕过的比较式门控"直接钉死或排除。
  4. **拒绝来源的机械判据**: 若反代有访问日志,优先看其中的 **upstream 字段**(nginx: `u_rt=`/`u_times`)
     —— `-` 表示**反代自己拒绝、未触上游**,有值表示**上游确实收到了请求**。比只看状态码/响应体可靠,
     且不必改任何配置(本项目据此一次分清 post-setup 的纯反代 401 与 setup 态真正到达上游的 401)。
- **必备对照(否则结论不可信)**:①一条**应放行**的端点(如 `GET /api/apps` → 200)证明链路可达、逻辑确实被执行;
  ②门控端点要测**四种输入**(缺头/空头/错值/对值),只看"错值被拒"不能证明"缺值被拒"。
- **坑**:①只测经反代的请求,会把"应用 401"误读成"反代拒绝"(或反之);②`404` 可能是特性开关;
  ③handler 在 IPC 上挂住 → 直连时**无 HTTP 响应**,那是实验事实而不是"拒绝"或"漏洞"。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round3-pre-auth-triage.txt`,notes §10.1/§10.2。

## Go 后端路由表的经验枚举法:字符串候选 + 404 判据 + 直连后端 | (2026-09-21/Ubiquiti UniFi OS Server)
- **场景**: 目标后端的路由表拿不到(无 symtab、无 swagger、非文本;gin/httprouter 注册在二进制里)。要用"否证"级别
  的证据说"某挂载点没有对应路由",不能只试几个猜的路径。
- **步骤**:
  1. **候选生成**: 取二进制里所有可打印串,按**每一个 `/` 边界**展开为"后缀 + 各级前缀"(只展开首个边界会漏掉
     嵌在长字符串里的路由字面量 —— 本项目第一版生成器即有此缺陷);丢弃含 `%`/`..`/`//` 的、含 `.`(域名/符号名)、
     `(`、Go 函数名形状(`pkg.Type.Method`、`.func1`)的条目;限制段长与总长。
  2. **判据探测(直连后端)**: 绕过反代直连 `127.0.0.1:<port><cand>`,只有 **404** 才算"路由不存在";
     `200/400/401/403/500` 都算"路由存在"(注意 `misreports.md` 反例 10:有的后端鉴权失败也回 200)。
  3. **有效性自证**: 先确认"这个方法在同一个二进制上确实能找到真实路由"(本轮 ulp-go 上找到 36 条),
     再拿它下"某些前缀无路由"的否证结论 —— 否则无法区分"没有路由"与"生成器漏了"。
  4. 最后回到**反代配置**判断网络可达性(挂载前缀改写、是否带鉴权指令),区分"上游有路由但被鉴权挡住"与"上游根本没有该路由"。
- **成本**: 数百~数千条候选 × 本地 5ms 级请求,可并行(本轮 3019+4667 条,P32 数十秒内完成)。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round5-public-mount-enumeration.txt`、notes §12。

## 可达性以**运行态监听**为准:portmap/端口清单只说明"打算发布什么" | (2026-09-21/UniFi OS Server)
- **教训(本轮实测)**: 安装器里的 `portmap.json`(主机发布哪些容器端口)列了 13 个端口,但逐端口在**运行态**核对
  (`ss -lntH/-lunH | grep -c ":$p "`)后:**只有 1 个(5671/RabbitMQ TLS)真的有监听**;其余
  (10003/udp、3478/udp、5514/udp、6789、8080、8444、8880-8882、28082、9543)全部无人监听 ——
  因为它们属于**按需下载的 runnable**(network/protect/access 等),基础镜像里根本没装。
- **可复用判据**: ①A1 的"可达性"列必须以**运行态监听**为唯一依据;声明文件/配置/安装脚本只能作为**候选来源**,
  不能作为事实。②"声明有、运行时无"要写成 **Out of scope(面不存在)** 并附逐端口实测输出,而不是"未判定"。
  ③反之也一样:运行态有、声明里没有的端口(本项目的 161/6812/6813/7812/7813)要单独记,并按是否发布判定可达性。
- **同族**: 容器型固件务必区分"容器内 0.0.0.0 监听"与"宿主是否转发"(portmap / `-p` / host 网络);三者判据不同。

## UDP 发现应答器的判读:先读实现拿格式,再逐字段解码应答,畸形输入只看"进程是否存活" | (2026-09-21/UniFi OS Server UDP 10001)
- **做法**: ①先从实现(本项目 `service.js` 的 `discovery.responder` 类)拿**精确**接受条件与打包格式,不要凭协议记忆猜;
  ②再构造最小请求打过去,把应答**按 TLV 逐字段解码**(本项目为 `[ver][cmd][len:2]` + `[type:1][len:2][value]`,
  v1 请求 `01 00 00 00`、v2 请求 `02 08 00 00`),把字段名/值列表存档;
  ③畸形输入扫一遍(0/1/3 字节、错 cmd、错 word、65507 字节)并**只判"进程是否活"**(这类应答器通常 try/catch 成 error 事件)。
- **判据**: 发现协议天生要向外泄露"设备指纹"(MAC/型号/固件/uptime/guid),**这本身不是漏洞**;要可报必须满足其一:
  ①响应含**凭据/令牌**(如默认口令、API key、会话材料);②响应含**超出发现所需**的敏感值(且能证明是意外字段);
  ③畸形输入导致**崩溃/状态破坏**(纯 DoS 按项目口径另记)。
- **注意**: 未配置态与已配置态的字段集不同(本项目 null 字段被省略,如 `ownerSsoId`)——**结论必须带状态**
  ("配置后是否带上 ownerSsoId 未实测"是待续观察,不是结论)。
- 产物: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round7-udp-discovery.txt`、notes §14。

## 默认凭据 × listener 的判读顺序(先传输层,后用户面) | (2026-09-21/UniFi OS Server RabbitMQ)
- **步骤(顺序不能颠倒)**: ①**哪条 listener 面向网络**(本项目 5671/TLS 发布、5672/明文仅 127.0.0.1);
  ②该 listener 的**传输层前置条件**是否已经挡住认证阶段(5671 要求客户端证书 → SASL 阶段不可达);
  ③最后才看用户/权限,而且要看**有效值**:`rabbitmqctl environment`(或等价的运行时查询),**不要只读单个 conf 文件**
  —— 本项目 `loopback_users.ui` 在 `10-unifi-common.conf` 是 `true`、被 `10-unifi.conf` 覆盖为 `none`,
  只看第一个文件会得出相反结论。
- **"默认凭据 + 配置漂移"的写法**: 若默认口令本可在网络侧使用、但被**另一层**(mTLS/白名单)挡住,不要把
  "漂移"直接写成漏洞;正确写法 = 记成 **"若版本变更优先复核点"**(写明:若客户端证书要求被去掉、或新增非 loopback
  listener,则该默认口令即刻可用),并附本轮实测证据。
- 关联坑: `tool-quirks.md`「TLS 1.3 下判是否要求客户端证书」。

## 强方法两轴:各格的落地工具与本机现状(含缺口) | (2026-09-21/工作区级)
> 规则(两轴封闭枚举、每面至少"一个 oracle × 一个导航"、弱方法不得单独把面判"干净")在 `AGENTS.md` 守则 11;
> 本条只记**落地**与**本机现状** —— 现状为 2026-09-21 用 `command -v` / `import` 实测。

**两轴(开局清单,不是穷举;判据可扩展)**
> **不变的只有判据**: "机械化 oracle(不依赖人先知道 bug 在哪)+ 有方向地探索输入空间"。
> 下表只是**开局会想到的格**;遇到表外机制,按同一判据**加格**即可(本项目的轮次里就自造过
> "最小客户端 + 存活哨兵变异""micro-race 时间预算""先判净变化再谈越界"这类格)。
> 反过来,**不得因为"不在表里"就不做**,也不得把表当穷举来申报覆盖。
- **oracle(开局)**: ①内存错误 ②状态·持久化净变化 ③差异 ④不变量与授权 ⑤资源·时间 ⑥解析与类型
  ⑦覆盖增量(弱,仅导航) ⑧语言运行时语义 ⑨数值·一致性 ⑩侧信道·测量
- **输入空间导航(开局)**: ①覆盖率反馈 ②语法·结构感知 ③符号·约束 ④数据流·污点 ⑤边界·组合 ⑥重放+变异
  ⑦并发·时序 ⑧差分导向 ⑨静态导引 ⑩规格·滥用 ⑪环境·桩注入

**判定器(oracle)侧**
| oracle | 本机可用 | 缺口 |
|---|---|---|
| ①内存错误 | `valgrind`(memcheck/drd/helgrind)、`gdb`;clang/gcc 可编 ASan/MSan/UBSan 版 | 无 lldb/rr |
| ②状态·持久化净变化 | 自写两次快照 diff(已有"先判净变化"判据) | — |
| ③差异 | 函数级差分法(已有)、`objdump`/`readelf` | — |
| ④不变量与授权 | curl/自写探针 + 负对照;容器内直连后端(已有反代鉴权判读法) | — |
| ⑤资源·时间 | `/usr/bin/time -v`、`ulimit`、容器 cgroup | — |
| ⑥解析与类型 | `angr`、`capstone`、`lief`、`unicorn` | — |
| ⑦覆盖增量 | **`afl-fuzz`/`afl-showmap`(apt 5.02c)+ 自建 `afl-qemu-trace`(QEMU 5.2.50,`/usr/local/bin`)** ⇒ 无源码二进制也能覆盖率反馈(2026-09-21 装) | 无 honggfuzz(源里没有;`perf_event_paranoid=2` ⇒ 其 perf 路线也不可用) |
| ⑧运行时语义 | `frida`(可 hook 托管运行时/原生库)、`gdb` | BEAM/Java 专用调试工具未装 |
| ⑨数值·一致性 | 自写 + `z3` | — |
| ⑩侧信道·测量 | 自写计时 | 未验证(`perf` 权限) |

**输入空间导航侧**
| 导航 | 本机可用 | 缺口 |
|---|---|---|
| ①覆盖率反馈 | **AFL++ 5.02c + qemu_mode 自建**(`afl-fuzz -Q`,已冒烟:109 tuples);插桩模式 `afl-clang-fast` ✓ | Frida 模式未建(需要时 `make frida_mode`) |
| ②语法·结构感知 | 自写生成器(已有"最小客户端 + 存活哨兵"变异法) | 无现成 grammar fuzzer(pwn 环境可试 `hypothesis`/`grammarinator`) |
| ③符号·约束执行 | `angr`、`z3` | triton 未装 |
| ④数据流·污点 | angr 符号执行可替代;源码级可 clang 静态分析 | 未验证 |
| ⑤边界·组合扫描 | 自写脚本 | — |
| ⑥重放+变异 | 自写(抓包/日志作种子) | — |
| ⑦并发·时序 | 已有 micro-race 时间预算法 | — |
| ⑧差分导向 | 已有函数级差分法 | — |
| ⑨静态导引 | **反编译主路径 = IDA**(`tools/ida_start.sh`,项目钦定);`objdump`/`readelf`/`ndisasm`/`lief` 可用;`radare2` **仅反汇编可用**(`r2ghidra` 在本机装不上,见 `tool-quirks.md`);`ghidra` 12.1.2 在位(headless 未验证) | — |
| ⑩规格·滥用 | curl/自写 + 认证矩阵判读法 | — |
| ⑪环境·桩注入 | LD_PRELOAD(已有做法)、`frida` | — |

**首要能力缺口(按收益排序)**
1. ~~覆盖率反馈 fuzz 基础设施~~ **已具备(2026-09-21)**: `apt install afl++` + 源码 `qemu_mode` 自建;
   用法:无源码二进制 `afl-fuzz -Q`(QEMU 模式)、有源码/可编译 `afl-clang-fast`;托管语言目标优先 `-Q` + 真实请求作种子,
   **不要**用 `-n`(盲变异)冒充强方法;
2. **托管语言反编译与 harness 化**: Go(`pclntab` 恢复函数名/路由、`go tool objdump`,必要时 `dlv`);
   BEAM(`.beam` 的 debug_info;**容器镜像内自带 `erl`** ⇒ 直接在容器里用,不必在宿主机装);
   Java(`usr/lib/unifi/webapps` 一套反编译);
3. 侧信道/测量的可用性验证(`perf` 权限、计时精度)。

**内存纪律的具体做法**(配套 `AGENTS.md` 守则 12;本机实测 7 GB RAM / 32 核)
- 大文件: `os.pread` / `mmap` / `dd skip=` / 流式分块;**永不** `open().read()` 整份固件(踩过:785 MB ×2);
- 工具日志: **先设级别**(debug 级能涨到数百 MB —— 实测某次 316 MB);
- 搜索/语料: 队列去重 + 上限 + 迭代化,必要时落磁盘;
- fuzz: 设内存上限(`afl-fuzz -m`)、语料限额、并行度按 RAM(7 GB ⇒ 建议 ≤8 实例 × 512 MB);
- 每次实验记录**峰值 RSS 与耗时**进 notes,使复跑可比。

## 托管运行时(Go / Node)面的"补格"法 —— ①内存错误 oracle 不适用时怎么办 | (2026-09-21/工作区级)
**触发**: 按守则 11 给某个面补"强方法"格子时,发现目标**不是 C/C++**。
**判性(先做)**: `file <bin>` + 找 `go1.x` / `node` / `.beam` / `RuntimeVersion` 痕迹。本项目的实测样本:
Ubiquiti UniFi OS Server 的 `ulp-go-app`/`uid-agent-app`/`unifi-directory-app`/`ucs-agent-app`/`unifi-identity-update-app`
**全是 Go 1.25.6**;`unifi-core` 是 Node.js(`service.js` + 少量 `.node` addon)。
**结论**: 这类目标上 **①内存错误(oracle)× 覆盖率(导航)在原理上无的放矢**(Go/JS 无原生内存破坏,除非进 cgo/addon)。
⇒ **换格**(守则 11 允许表外机制,只要满足"机械化 oracle × 有方向探索"),推荐组合:
- **oracle**: ⑧语言运行时语义(Go panic / Node uncaughtException / 框架 500)、⑥解析与类型(schema/类型/长度)、
  ④不变量与授权(签名、配对、audience、nonce、role)、②状态·持久化净变化;
- **导航**: ②语法·结构感知(用真实请求作种子) / ⑤边界·组合 / ⑥重放+变异 / ①覆盖率(`afl-fuzz -Q` 仍可对**单进程**
  命令行目标用,对长驻服务要么做 harness 要么改用网络侧 oracle)。
**纪律**: 别因为"进程没崩"就把面判干净(守则 11);也**别把被 recover 的 panic 报成漏洞**(`misreports.md` 反例 11)。

### Go 二进制的完整路由表:优先读**内嵌 Swagger**,字符串候选 + 404 判据作交叉验证
- go-swagger 生成的 Go 服务会把 `swagger.json` **原文内嵌**在二进制里(`.../restapi/embedded_spec.go`)。
  恢复法(本项目实证): 在 raw 字节里找 `"swagger"` 出现位置 → 向前找最近的 `{` → **按字符串感知的括号配平**截出整段 JSON → `json.loads`。
  比"字符串候选 + 按 `/` 边界展开 + 404 判据"更**准且全**(后者会把相邻字符串粘成一条、需要探测过滤)。
- 两者互补: 内嵌 spec 答"**声明了**哪些 path×method×operationId",404/401 判据答"**实际注册了**哪些"(注意 stub:
  本项目 `idp.AddIdp`/`scim.Patch`/`ldap.GetLdapImportScope`/`system.GetSystemMetrics`/`idp.GetUserCertificates`
  的 handler 直接返回 `operation X has not yet been implemented`)。
- 可达性还要叠加**反代映射**: spec 里的 path 未必是 nginx 暴露的 path(本项目 `^~ /api/idp/sso/` → `/api/v1/idp/sso/`)。
- strip 过的 Go 二进制仍留有 **pclntab 函数名**(`条 4`),可用 `strings` grep 出 `pkg.(*T).Method` 全限定名辅助定位。

### 预认证 SSO/SAML 的 oracle 矩阵(签名与断言不变量逐项;每例**新取**一个 request)
SAML ACS 是少见的"预认证 + 复杂解析器 + 认证语义"三合一面,补格时按下列**逐项单变量**测,oracle 取"是否继续登录":
1. 签名位置与覆盖: 签 Response / 签 Assertion / **只签 `<Status>` 或无关元素** / 无签名;签完再改内容(篡改);
2. **XSW**: 追加未签 Assertion(前/后)、嵌套 Response、同名多 Assertion —— 必须判"验签元素 == 消费元素";
3. 断言不变量: NotBefore/NotOnOrAfter(**过期与未来两端**)、**AudienceRestriction**、Recipient、Destination、Issuer、Subject 存在性、Status;
4. **重放/绑定**: 同一 Response+RelayState 再投一次、InResponseTo 与请求不符;记录"单次消费"发生在哪一层;
5. 解析器: XXE(`file:///etc/passwd`)、billion-laughs、超大字段/超长 base64。
**关键手法**: 每个用例**重新发起一次 IdP 流程**取新 request/RelayState —— 否则前一次的"单次消费"会把后面的用例全污染成 reject
(本项目首版矩阵就因此把 baseline 误判为 reject)。
**环境搭建(无 admin 凭据时)**: 若"加 IdP"是 stub 且直连后端要 scope 头,可**直接向 app 自己的 DB 插一行 IdP**
(自带 metadata/cert/key),把预认证 ACS 变成可动态验证——这是测试环境搭建,不是固件改动;把 SQL+密钥生成写成脚本存档(本项目 `artifacts/recon/round10-saml-harness/idp_setup.sh`)。

## 模拟方式按"缺哪层补哪层"选(别默认只用 qemu-user) | 来源: 本工作区(2026-09-27)
| 方式 | 还原到哪一层 | 什么时候用 |
|---|---|---|
| 静态(读二进制/配置) | — | 先定位入口与判定点 |
| `qemu-user` + `chroot`/`unshare` | **用户态指令**(快;无内核语义、无 init、无真实挂载) | 单进程 / 单 CGI / 单解析器面 |
| ↳ **我们那份 `qemu-aarch64-patched`**(`~/.local/bin/`,见 AGENTS 环境清单) | 同上,但**不做 netlink 协议白名单**(上游只放行 ROUTE/KOBJECT_UEVENT/AUDIT,其余 -> `-93`) | 面里有 **netlink** 的 daemon:`NETLINK_NETFILTER`(NFQUEUE)/`SOCK_DIAG`/`XFRM`/`GENERIC` —— 上游 qemu-user 下它们**起不来**(`nfq_open` 失败)。判读探针 `tools/qemu-nl-probe/`;32 位/大端边界见 `tool-quirks.md` |
| **容器(docker/podman)** | **补的是 qemu-user 缺的那层**: 真 `/dev`、真挂载与 `/proc`、多进程;跑发行版镜像时才有真 init/systemd | **缺这层时**才用(多守护要真 `/dev`、真挂载、按包/按用户态工作)。**固件 rootfs 一般不是可启动发行版** ⇒ 此时形态是「**容器当宿主 + qemu-user 跑 guest**」,不是"容器替换 qemu-user" |
| `qemu-system`(整机) | 含**内核** | 需要内核行为(内核特性/驱动/`xattr`)。**限定**: 只在目标**能整机启动**时可用 —— 本工作区实测过的是 **CHR x86_64**;厂商固件多为非标 boot/无 MBR ⇒ 通常启动不了,**别拿它硬启固件**(那时退回 qemu-user 或容器) |
**要点(缺哪一层补哪一层,不是"越真越好")**:
1. **默认工作马 = `qemu-user`(+ `chroot`/`unshare`)** —— 秒级起,面内定位最快(单进程 / 单 CGI / 单解析器);
2. **缺"真 `/dev`、真挂载、多进程/init、真包环境"才上容器**;固件 rootfs 不可启动时按「**容器当宿主 + qemu-user 跑 guest**」用
   (`--privileged` 只为拿 `CAP_SYS_ADMIN`)。**netlink 那一类面不用这步** —— 用**上面那行**的补丁版 `qemu-aarch64-patched` 就行;
3. **缺内核语义才上 `qemu-system`**(且目标须能整机启动)。
⇒ **三层可叠**(静态定位 → 容器里跑 → 需要内核再上整机)。用户层差异会**直接改变结论** ⇒ 结论含糊时,先把**缺的那层**补齐再判。
**危险点(硬)**: 容器里 `rm -rf` 只毁容器 —— 但**一旦 bind 挂载了宿主目录,容器内删除就是删宿主的** ⇒ 见 AGENTS/LOOP 的挂载隔离要求。

## 自托管 / 可本地运行的镜像:**先"建场"(preset)再生差分** | (2026-09-21/Ubiquiti UniFi OS Server)
**何时用**: 目标是**我们能跑起来**的服务端镜像/容器/VM,而"有意义的动作"要在**认证后 / 配置后**才可见。
此时**不要**把这些面标 `Blocked` / `Out of scope`(那是懒结论,见 `AGENTS.md` 守则 8/10):先在本地把**前置状态造出来**。

**建场清单(按需;手段不限,以下是本项目已实证可行的)**
1. **主体**: 直接写应用的 DB/配置 —— 实证: 往 PostgreSQL 插一行 IdP,预认证 SAML ACS 立刻从 `IDP_NOT_FOUND` 变成可处理;
2. **"已配置"状态**: 插入 setup 流程本该建的行,或置配置标记、补齐缺失 runnable ⇒ 让 post-setup 行为**本地可见**;
3. **证书**: 自建 CA + 客户端证书并塞进信任库 ⇒ **解锁 mTLS 门控的路径**(否则这些面只能永远记"No finding: 需 mTLS");
4. **会话 / 令牌**: 优先**读运行时材料**(容器在跑: 运行时生成的密钥 / DB 会话表 / setup token)自己铸;
   备选是**直连信任边界**: 若上游信任内部身份头(`x-userid`/`x-userrole`/… 不校验 token),直连后端 socket 带这些头即"成为已认证"
   —— 但**产出必须证明存在远端可达入口**(公开 location 不清这些头 / 某 handler 未自校验),否则如实降级为"加固缺口";
5. **对端 / IdP**: 自建(本项目已用 DB 注入做过 SAML IdP;SAML/OIDC 的 ACS 是预认证面里最肥的一块)。

**判定形态: 双实例差分(机械化 ③差异 oracle)**
A = pristine(setup 态) / B = 预设版(已配置 + 多主体 + 自签 CA);同一批请求打两边,比响应差
⇒ 直接产出"认证/配置带来了哪些差异",以及"哪个**预认证入口**多给了东西"。成本 = 两个 docker 容器。

**⚠️ 边界二(别把"造场"读成"改造出漏洞")**: 改本地配置 / 造主体 / 自签证书 **只提供可见性与对照**(看到"已配置/已认证"该有的行为),
**不提供可达性**。判定一个预认证入口是否真有缺陷,可达性必须来自**产品自身**: ①该状态是**厂商安装器/型号能力/默认值**会设的
(要给出**来源证据**: 谁写入这个配置、真实部署是否为该值);②端点自身**授权语义**不成立;③返回数据的性质(他人数据/凭据/配置)。
若可达性只由我们手改而来且无真实部署证据 ⇒ 只能算**加固观察**,不产 finding。
（反面教训: 2026-09-21 曾把"置 `config.stackable=true` 后两个端点变可达"当成"可能直接出 finding" —— 错在把**手改的配置**当成了真实暴露面。)

**边界(硬)**: 造场只是**尺子**(建立基线 + 负对照),**不是门票** —— 产出仍须落在**预认证入口**上(守则 10 排序 (a) 不变);
"需要管理员账户才能越权"这类**不报**。收尾时按守则 8 在 `CLOSURE.md` 列「本轮承认没做的深度动作」。

## 「没对照 / 看不进去」就停 —— 最贵的一类误判(经验) | (2026-09-21/Ubiquiti UniFi OS Server)
**现象(我们实际干的)**: 一批面被写成这类结论 ——
`1c` "No finding: **需 mTLS**";`1d` 的 `x-group-token` 比较"未配对态比较恒假 ⇒ 排除"(**真值从未跑过**);
`1b6` "`GET /api/system` 元数据缩减**未实测**(需完成 setup)";认证后 API → `Out of scope (Blocked: **需凭据**)`;
`1b4` "若**换目标**为 console 型号需重新判定"(而我们本来就能改本地配置)。
共同点: 句子里出现 **"需 X 才能…"** ⇒ 把它当成了结论,而不是**待造的条件**。

**为什么错**:
1. 判"是不是缺陷"靠的是**形态先验 + 构造利用形态**——不是靠对照。看到"预认证端点导出用户备份""上游信任内部身份头""比较可被置空"
   就该**直接去构造**,对照只用来把"很可能"钉成"确证";
2. "看不到正常行为"不是不可知,而是**没造**: 自建 IdP / 自签 CA / DB 注入主体 / 直连信任边界 —— 都能把"正常行为"**在本地造出来**;
3. 于是代价是**整面丢掉**: 六条反例连关六扇门、两轮 0 finding、把"最可能出真问题的认证后授权面"整块让掉。

**分两部分(覆盖这类偷懒的绝大部分)**

**(一)目标在本地 ⇒ 没有条件就自己造。** 我们是本机在跑它: 缺"已认证视角"就**建一个用户**(DB 注入主体/角色/组织),
缺 mTLS 就**自签 CA + 客户端证书**,缺"已配置"就置配置态,缺 IdP 就自建(都已实证可行)。
**判据: 只要"缺条件"这句话成立,它就是待办,不是结论。** 具体清单见「建场 + 双实例差分」。

**(二)连条件都造不出来 ⇒ 用经验直接判,不要等对照。** 很多形态**按定义**就成立,不需要基线:
"未认证者能拿到**他人**的数据/备份/会话" = 越权;"上游不校验身份头就采信" = 认证边界失效;
"派生连接不带自身身份" = 授权绑定缺失。此时对照**只是证据强度**(能写"有对照"更好,不能就如实写"无对照,判据是 …"),
**不是能不能判的前提** —— 判据是**语义**(谁能做什么),不是"和另一个实例比出来的差"。

**具体顺序(成本从低到高)**: ①不改环境: 逐点读**返回体**(哪些 public 端点带真实数据 —— 反例 10 的门);
②改配置/状态: 置特性开关 / "已配置" / paired 态 ⇒ 用**真值**跑一次;③造主体·证书: 自签 CA、DB 注入多主体、自建 IdP;
④双实例差分(pristine vs 预设)⇒ 机械化 ③差异 oracle。

**再遇到同类(新的偷懒形态)**: 按本条格式补进本库(现象/为什么错/做法/自查判据),**不要靠往提示词里加规则**。

**自查判据(写结论前问一句)**: 我是在**判**,还是在**看不进去**?
- 结论句里出现"需 X / 未实测 / 若换目标 / 等有凭据再说" ⇒ **那是待造条件,不是判定**;必须转成工作项或至少写清"没做";
- 缺对照只能**降结论强度**(如实写"无对照,判据是 …"),**不能**用来关面;
- 反例: `1c` 只要生成一个自签客户端证书就能跑 —— 成本 10 分钟,而我们把它写成了"No finding"。

## 可达性用**产品自身输出**判,不用本地改配置/型号反推 | (2026-09-21/Ubiquiti UniFi OS Server)
- **做什么**: 判"某面在真实部署是否可达"时,优先取**产品自己吐出来的能力/特性表**(本项目: 公开白名单
  `GET /api/system/features`、`GET /api/system`)或**运行态监听**,而不是"读型号 profile / 本地改配置后能跑"。
- **判据**: ①特性表里**没有**该键 ⇒ 相关**路由不注册**(本项目 `coLocated` 缺 ⇒ `/api/ws/consoleGroups` 不注册、
  `site-shadow.conf`/`site-colocated-*.conf` 不生成、11083 不监听);②改配置/造状态只给**可见性**,不给可达性
  (见「建场」边界二)。
- **"某头能否被攻击者置值"要看两层**: (a)**值来源变量** —— 本项目 `X-Internal-Proxy-Client-Cert $ssl_client_escaped_cert`,
  而 443 站点**无 `ssl_verify_client`** ⇒ nginx 不发 CertificateRequest ⇒ 变量恒空 ⇒ **自签客户端证书也没用**
  (带/不带证书响应逐字节同形);(b) 路径是否 `strip`/`override`(`strip-internal-headers.conf` vs `auth_request_set`)。
- **代价(本轮)**: 面 `1c` 曾被写成"No finding: 需 mTLS",外部复核又说"证书可自签 ⇒ 未判定";实测两条都不准 ——
  真实部署不可达(特性表证据),且即便造出 coLocated,443 也不请求客户端证书。**把 1c 收口为结构不可达**。
- 证据: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round13-mtls-authz-hosttrust.txt`、notes §26.1。

## 端口/攻击面清点:就绪判据必须覆盖**长启动的 app runnable**,不能只等 init 目标 | (2026-09-22/Ubiquiti UniFi OS Server)
- **现象**: 容器/整机固件里,主控服务先起、被托管的**应用**(Type=notify、java/go 大件)晚几十秒才 bind 端口。
  `systemctl is-system-running`(= `running`)只说明默认 target 达成,**不代表**这些 app 已 active — app 仍
  `activating` 时 `ss -lntup` 会漏掉它的全部 listener。反之,`starting` 阶段还会看到**临时**监听(如随机 UDP 端口)。
- **做法(枚举面之前)**: ①先列"**要数的面**由哪些 unit 承载"(本项目 = `unifi-core`、`unifi`、`nginx`、…);
  ②逐一 `systemctl is-active <unit>` 直到全 active(或确认 failed),**再** `ss -lntup`;③把"就绪判据 + 取值"
  和清单一起留证(否则"面不存在"无法复核);④同一份清点至少在**两个状态**(如 setup / configured)各跑一次。
- **判据**: 清单里出现"某产品组件未安装/未运行"的负结论时,必答一句"它对应哪个 unit、当前 `is-active` 是什么";
  答不出或该 unit 正 `activating` ⇒ 该负结论无效,必须重测。
- **代价(本轮)**: 一个 `UOS_UUID` 配错 + 提前 `ss`,让内置 UniFi Network app(10.5.67,十几个端口)在 A2 表里
  躲过了至少 9 轮,直到外部复核的快照才露出来(见 `notes` §38「误判回溯」)。

## 组件自带的 filter 名 ≠ 鉴权点:必须找到"决定放行/拦截"的代码路径 | (2026-09-22/Ubiquiti UniFi Network 10.5.67)
- **现象**: 目标是一个 Spring-Boot 应用,`web.xml` 里赫然有 `<filter-name>AuthFilter</filter-name>` 并映射了
  `/api/*`、`/v2/*`、`/manage/*` 等 —— 很容易据此认为"这些路径有鉴权"。反编译其 `doFilter` 后发现它
  **只做 setup 重定向**:路径前缀命中一个白名单就 `chain.doFilter` 放行,否则把请求 302 到 `/setup/`;并且当
  没有会话时它会**合成一个 `factory_default` 上下文**(`super_admin=true`)再放行。**真正的鉴权在 API 层**(另一处)。
- **后果(会带歪判据)**: 连接器/容器返回的 `400`/`404` 只是"该连接器不服务这个路径",**不是**"鉴权拒绝";
  把 400 当 401 用 ⇒ 高估了面已受保护,也高估了"差分能证明鉴权"。
- **做法**:
  1. 对每个"安全相关"的 filter/servlet,反编译其 `doFilter`/`service`,写出**判定表**(输入 → 分支 → 放行/拒绝/重定向);
  2. 把"名字像鉴权"与"实际是鉴权"分开列:找不到"拒绝路径 + 身份来源"的 filter 就不是鉴权点;
  3. 差分实验要用**同一连接器上确有鉴权的端点**做基线(不能拿"返回 400 的连接器"当基线);
  4. 逐端口 URI 允许表(本项目 `UbiosHttpsFilter`:按 `getLocalPort()` 选正则/前缀集)是**可达性**约束,
     不是**鉴权**;两者要分开写进 surface map。
- **代价**: 本轮一度把 8080 上 `/api/*` 的 400 读成"鉴权生效";读完 `AuthFilter` 字节码后才纠正 ⇒ 面判定重写。

## "输出目录/路径类参数"必须做 canonical 校验;收集器/复制器对 `src == dst` 不设防 | (2026-09-22/UniFi Network support-dump)
- **现象**: 一个"收集数据到目录"的端点把请求里的 `dir` **逐字**当输出目录用: 绝对路径、`mkdir -p`、`..` 归一化后写穿全接受;
  写不可写目录时**吞掉错误仍回 200**。更糟: 某一步把**实时数据目录**拷进 `new File(dir, "<name>")`,
  当 `<dir>/<name>` 恰好就是实时目录本身时,**实时文件被删/清空**(收集器先建/清目标再拷,未检查 src/dst 同一性)。
- **判据(遇到"路径/目录/文件名"参数时逐个问)**:
  1. 该参数是否能指向**产品自己的数据/日志/配置目录**?(能 ⇒ 有破坏性副作用,不只是"写到别人目录")
  2. 有无 canonical 校验(解析后再比较、拒绝 `..`、限定在调用者管理目录内)?没有 ⇒ 任意路径。
  3. 复制/移动类 helper 是否检查 `src` 与 `dst` 是同一路径?没有 ⇒ "收集/备份/导出"端点自带**自毁**面。
  4. 错误是否**被吞**(返回 200 而实际失败)?⇒ 调用方(和攻击者)无法从响应判断,但副作用仍发生。
- **验证法**: ①未认证/低权限发 `dir=<自身数据目录>` 与 `dir=<fresh>` 两格,前后比 `ls`/md5(差分);
  ②对 `dir` 试绝对路径、`..` 穿越、shell 元字符(分别区分"路径问题"与"命令注入");③用不可写目录确认错误处理。
- **代价**: 本轮即用"`dir=/data/unifi` 前后 `ls` 实时日志目录"一格,把"任意目录写"升级为"可删实时日志"。

## 子工具的"默认值"不能外推到包着它的工具(podman × pasta) | (2026-09-22/UniFi OS Server)
- **现象**: 用某二进制**自带/文档**的默认值,去推断**上层工具**拉起它时的行为 —— 例如由 `pasta --help`
  (`-t/--tcp-ports` 默认 `auto` = 转发 namespace 内所有绑定端口)+ 安装器只写 `--network pasta:<固定选项>`(没写 `-t`),
  推断"容器内绑定端口会被转发到宿主"。**被测伪**: podman 在未 `-p` 发布任何端口时,会给 pasta 传
  `-t none -u none -T none -U none`,**覆盖**了子工具的默认。
- **判据**: 判"某能力的默认是否生效"要落到**最终被执行的命令行**(在真环境 `ps -ef | grep <子工具>`,或读上层工具源码/`-v` 日志),
  不能停在子工具文档。推论链每多一跳,都必须有一格实测。
- **同族**: 「可达性以运行态为准,portmap/声明只是'打算发布什么'」;本条把"运行态"再收紧到"**实际被拉起的那条命令行**"。
- **代价**: 本项目据此把一条"产品自身会发布端口"的断言撤回(notes §40.9);若未撤回,会拿一条错的机制去支撑一条真缺陷。

## 判"某端口/接口是否对外可达":先查**厂商端口参考文档**,再谈机制 | (2026-09-22/Ubiquiti UniFi Network 28082)
- **做法**: 问"这个监听端口在真实部署是否入口可达"时,除了"运行态监听 + 安装器发布清单",还有一条更权威且便宜的证据 ——
  **厂商自己的端口参考页**(Ubiquiti: 《Required Ports Reference》;其他厂商同理: "port requirements"/"firewall ports"/"需要开放的端口")。
  它通常直接给 **方向(Ingress/Egress/Both)+ 用途**,足以判定"设计上是否对外"。
- **判据**: 文档标 `Ingress` ⇒ 该端口按设计就应可从外部连入(可达性成立);标 `Egress` ⇒ 只出站;
  标 `Both` ⇒ 双向。检索方式: 优先厂商 Help Center 的搜索 API(Zendesk: `/api/v2/help_center/.../articles/search.json?query=<端口号>`),
  比搜索引擎稳(搜索引擎常返 JS/反爬空页)。
- **与既有条目的关系**: 「可达性以运行态监听为准、声明只是'打算发布什么'」讲的是**枚举输入**;本条补的是**方向/意图**的权威来源。
  两者并用: 运行态证明"在听",厂商文档证明"设计上对外"。
- **代价**: 本项目在 VR-2 上先绕了"安装器发布机制"一大圈(含一次被 podman 实测证伪的推断),最后一条官方文档就定了方向。

## 枚举"某个 dispatcher/路由前缀到底挂了哪些 handler":先读它的 `@Import`,别对全 API 面数
- **问题**: 二进制里有上千个带 `@RequestMapping` 的类(整个 Network API),但某个连接器/端口只挂载**一个 servlet**
  的映射(如 `/v2/upload/*`)。逐个类读会淹没;而**只按 URI 猜**会漏掉没猜到的 handler。
- **做法**: 定位该 servlet 的注册点(`DispatcherServletRegistrationBean` / `servletRegistration`),再顺
  `WebMvcConfigurer`/`@Configuration` 的 `@Import` 链,把**真正注册进这个上下文**的 controller 收敛成小集合。
  收集手段: `strings`/`grep` 类常量找字符串 → `javap -p -v` 读 `RuntimeVisibleAnnotations`(注解里的
  `@Import(value=[class A,B,C])`、`@PostMapping(value=[...])`、`@RequestParam` 都在常量池可读)。
  **小集合一旦确定,就对集合内每个 handler 做"检查项差分"**(见 vuln-patterns「同族设备上传端点」条):
  同族里判定链最短的那个就是候选。
- **动态校验(必须)**: 对每个候选 URI 发一次请求区分"handler 已到达"与"路由不存在":
  Spring 未命中映射多为 Tomcat 404 页;命中但参数/前提不满足会走 controller advice(业务 JSON、405、500)。
  两者**混在一起**时,再用"同族兄弟的已知拒绝响应"做参照。
- **坑**: ①`@RequestMapping` 出现在常量池**不等于**注册进该上下文(可能属于另一个 servlet);②同名包下
  `oiFBAnX`/`iCfGofYCumTdnmc` 这类混淆名无法靠名字判断职责,只能读注解与方法签名;③端点标了权限注解
  (`@PreAuthorize` 样式的 marker)**不等于**执行时校验——本目标靠 `HandlerMapping` 的 `RequestCondition`,
  在**无身份**时也可能整体放行(必须先实测,见「filter 名≠鉴权点」)。
- **代价**: 本项目用一个 `@Import` 三元组把 `/v2/upload/*` 的 3 个 handler 锁定,省去对 2400+ 个 net 类的遍历;
  随后"落盘差分"一格就把三者区分开(一个写、两个不写)。

## 鉴权边界的"解析差分"矩阵(nginx↔后端):先取必拒基线,再用 content-type 区分"真到达后端"与"静态兜底"
- **场景**: 鉴权只在反代(nginx)做,后端(Tomcat/Node/Go)按自己的规则路由。想知道"改写法能不能让前端判公开、后端判受保护"。
- **做法**:
  1. 取一条**必被拒**的基线(如配置态 `GET /proxy/users/api/v2/users` = 401);
  2. 固定方法/凭据/来源,只改路径写法的**变体**:`//`、`/./`、trailing `.`/`/`、`%2f`、编码首段(`%70roxy`)、
     `;`/`..;/`、大小写(`/PROXY/`)、反斜杠、`%00`/`%20`/`%23`、`..%2f`;一律**关闭客户端 URL 规范化**(`curl --path-as-is`);
  3. **关键判读**: 只看状态码会误判 —— 用 `Content-Type`(与响应体)区分三种结果:
     401/403 = 鉴权生效;`text/html` 小体积 = 落到了 SPA/静态兜底(`try_files ... /index.html`,**不是**后端 API);
     `application/json` = 真到达后端(这才是有意义的分化)。
- **坑**: ①反代常开 `merge_slashes`,某些变体在匹配前已被改形(本例 `//proxy/...` → 400 来自 runnable-name map 不一致);
  ②路径穿越黑名单(`if ($request_uri ~ ...) return 400`)会吞掉多数 `..` 变体,**黑名单外的双重编码**(如 `%252f` 无 `..`)才是缺口候选;
  ③`auth_request` 的**响应缓存键**要单独看(缺 URI 时,能否用公开路径的 200 去命中受保护路径 —— 本例缓存只存不依赖 URI 的判定,不成立)。
- **收口条件**: 全部变体要么 401/400、要么落静态兜底 ⇒ 写"未发现规范化绕过",并**如实列出未做项**(如 CL.TE/TE.CL 走私的原始套接字测试)。

## 未文档化监听端口:先给**监听类**点名(thread dump),再读 handler —— 别靠逐个探包猜协议 | (2026-09-26/Ubiquiti UniFi Network)
- **场景**: 就绪态 `ss -lntup` 见某产品多开 UDP/TCP 端口(`10003/udp`、`6789`、`3478/udp`、`5514/udp`),无文档、探包无应答 ⇒ 不知道该读哪个类。
- **做法**: 起一个该产品的线程转储,按**线程名**和 `at com.xxx` 帧定位持有 `DatagramSocket.receive` / `ServerSocket.accept` 的类:
  - 若产品有**未认证可达的 support/诊断端点**(本例 `POST /api/ucore/support-dump` → 落盘 `thread-dump`),直接用它取 dump,**零额外权限**;
  - 否则用 `jstack`/`jcmd Thread.print`(纯 JRE 里可能没有 ⇒ 退 `kill -3` 到 stdout/journald)。
  - 判读: 线程名常直接点名角色(本例 `StunServer`/`StunServerListener` → `com.ubnt.net.SCVltTMvLLnQPC`;`discover` → `com.ubnt.service.B.oiFBAnX`;`RemoteLoggerServer` → `com.ubnt.service.aL.CPpReExkpHD`);Tomcat 端口则显示为 `http-nio-<port>-Acceptor`。
- **收益**: 4 个"未理解"面一次性收敛到具体类,`javap` 后即可判"是否有解析/输入面"(本例 6789 = 纯吞吐读写、无输入解析;3478 = jstun 反射服务)。
- **坑**: ①`docker cp` 对容器 **tmpfs 里的 `/tmp` 文件**可能报 "no such file"(overlay 视角看不到该挂载)⇒ 用 `docker exec cat > 文件` 取;②不要把"探包无应答"当作"端口无 handler"(可能是缺必填属性,如 jstun 的 `ChangeRequest`)。
- **证据**: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round26-inform-protocol.txt` §0。

## 判"某端口是否对外可达":`portmap.json` ≠ 发布面全集 —— 三源并列,且注意遗漏 | (2026-09-27/Ubiquiti UOS Server)
- **场景**: 安装器自带 `portmap.json`(声明要发布哪些宿主端口),容易被当成"发布面全集"。
- **反例(本例)**: `portmap.json` 列了 `28082/8080/8444/6789/3478udp/5514udp/10003udp…`,但**既没列 443(主 UI 监听)也没列 8443** —— 而 443 显然是产品对外入口。⇒ **不能由"portmap 未列"直接推出"端口不可达"**。
- **做法(三源并列 + 各自留证)**: ①容器**就绪态** `ss -lntup`(基线);②安装器 `portmap.json`(发布意图);③**厂商端口参考文档**(产品级 Ingress 声明,如 Ubiquiti《Required Ports Reference》)。三者不一致时,以"该 build 的实际发布机制可证"为准,机制不可证时**如实标注"未证可达"**,并区分"已发布路径"与"仅容器内监听"。
- **判读要点**: 同一个端口在"独立产品"与"被集成的 runnable"下语义可能不同 —— 本例厂商文档把 8443 记为 Network 控制台 "GUI/API",但 UOS Server 的 8443 实际是被 `UbiosHttpsFilter` 收窄的内部连接口(GUI 在 nginx 443)。**别把通用产品文档的端口用途直接套到被集成的 runnable 上**。
- **收益**: 避免把"未发布端口上的未认证端点"误报为 Pre-auth(本例 `wifiman`,`GET /api/site/<site>/wifiman` 在 8443 未认证可达但 8443 不在 portmap)。
- **证据**: `Ubiquiti-UniFiOSServer-5.1.42/artifacts/recon/round27-wifiman-face.txt` §2/§4。

## Synology DSM webapi 的**鉴权分两层**,审 batch/compound 子请求时必须分开核对 | (2026-09-24/DS223 DSM 7.2.2-72806 静态)
- **两层**(均在 `usr/lib/libwebapi-DSM5.so`):
  ① **顶层门** `SYNO::WebEntry::CheckPermission(APIRequest*, bool)` —— 由 `WebEntry::Run` 经 **vtable[4]** 调一次,施加 `authLevel`/`allowUser`/session/CSRF(`SynoCgiCheckSession`);**只对顶层请求执行**。
  ② **能力门** `SYNO::APIRunner::ProcessLib` 内的 `grantable`/`grantByUser` 校验(失败 `SetError(105)`,源码注释 `APIRunner.cpp:227/233`)—— 对**所有** lib 类 API 执行(含子请求)。
- **batch 机制** `api=SYNO.Entry.Request&method=request`(authLevel 0,须带 `compound` 数组):
  `ProcessEntry`→`HandleCompound`→`CompoundHandler::Process/CompoundProcess/CompoundProcessEntry`;每个子请求经 `InitParam`→`APIMiddleWare::Process("process_load_desc_middleware")`,然后**内联** `APIRunner::Process` 或 **fork** 后 `APIRunner::Process` —— **两条路径都不调用顶层门 ①**。
- **判据**: 所以"子请求是否被重新鉴权"**不能**用"它调没调 CheckPermission"一句话判定 ——
  必须分别确认: (a) 顶层 wrapper 的 `SynoCgiCheckSession` 对 authLevel 0 的 `SYNO.Entry.Request` 是否仍要求已认证(决定 pre-auth vs 认证后);
  (b) 子请求的 **admin 门**(`authLevel`/`allowUser`)由谁施加 —— 若仍只由 ① 施加,则 ② 的能力门不覆盖它。
- **别踩**: `APIRunner::CheckPermission` 在核心库里是 **stub(`return 1`)**,`NeedPermission→0`/`SkipPermission→1`,
  且全 rootfs `nm -D` 无第二个 .so 定义它们 ⇒ 看到这个名字**不等于**有鉴权;真逻辑在 `WebEntry::CheckPermission` 与 `ProcessLib` 的能力门里。
- **动态修正(2026-09-26/DS223 DSM 7.3.2-86009)**: 上面的静态担忧(子请求不经顶层门 ⇒ admin 门可能不施加)**被实测否证**:
  预认证 compound 调 `SYNO.Core.User.list`(authLevel1)⇒ **119**(`mode=sequential` 与 `mode=parallel` 同);
  testuser ⇒ **105**;预认证 compound 调 authLevel0 的 `SYNO.API.Info.query` ⇒ success。
  ⇒ 逐子请求的中央鉴权**确实生效**(`libwebapi-DSM5.so::CompoundHandler::CompoundProcessEntry` 0x96640 两分支后都走 `APIRunner::Process`;含 `allowUpload/allowDownload` 的方法另被直接回 **113**)。
  证据: `Synology-DS223-7.3.2-86009/artifacts/handler-sink/evidence_compound.txt`。
- **证据**: `Synology-DS223-7.2.2-72806/notes.md` §6.2(O7–O12);反编译脚本 `Synology-DS223-7.2.2-72806/artifacts/emu/` 同行同级。

## 认证后 API 的"可达性差分":**前置 scope 门会伪装成"非 105"**,判 reached 必须用业务码或对角验证 | (2026-09-25/DS223 DSM 7.3.2-86009 动态)
- **背景**: 对 ~1500 个认证后 API 做 {无会话, 低权用户, 管理员} × {版本,空参} 的分区差分时,若只用
  "**响应码 ∈ {105,119} = 被拒;其它 = handler 已执行**"来判"面是否可达",会**系统性误判**。
- **反例(实测)**: `SYNO.Core.TaskScheduler.Root.create` 对普通用户回 **152**(而非 105),看似"中央 allowUser 被绕过";
  实际 152 = `SynoCgiCheckScope("MustConfirmPasswordToken")` 的**缺前置 token**失败,发生在 allowUser 检查**之前**;
  给普通用户补上它自己就能拿到的 `SynoConfirmPWToken`(由 `SYNO.Core.User.PasswordConfirm.auth` 对普通用户开放)后,同一调用变 **105**(正确拒绝)。
  ⇒ 该"绕过"是**假阳性**。同理 **104**(版本不支持)、**117/119/120/121** 等也可能是前置门,不是 handler 码。
- **做法(判 reached 的两种可靠证据,至少其一)**:
  ① **对角验证**: 带齐所有前置(会话+token+版本)后,同一请求 **admin 得到"业务码/成功"**、而低权用户得到 **105** ⇒ 才能说"低权用户被 allowUser 拦住";
  ② **用业务码**: 只有能确定是 handler 内部产生的码(带 `errors.msg` 之类、且 admin 同样能得到该码)才算 executed。
- **配套**: 中央鉴权是否"完整"应另外做**注册表对账** —— 用"方法级 allowUser else api 级 allowUser"算**有效 allowUser**,
  与实测 reached/deny 逐条对齐;找"低权用户成功 且 有效 allowUser 不含该用户类"才是真缺口(本目标 1888 方法为 **0**,即无缺口)。
- **证据**: `Synology-DS223-7.3.2-86009/notes.md` §14.5–14.6 / `Synology-DS223-7.3.2-86009/artifacts/authz/{probe_pwconfirm.py,probe_root.py,sweep_core+app_1888.jsonl}`。

## 同一 API 内各 method 的授权语义可能不同 —— 必须**逐 method** 验证,不能由"组内某 method 正确"外推 | (2026-09-26/DS223 DSM 7.3.2-86009 动态确证)
- **形态**: 一个 Web API(同一 `.lib`/同一 `allowUser`/同一 authLevel)下,`list` 按对象所有者过滤,
  `getinfo` 对非 owner 返回空成功,但 `edit`/`delete` 按唯一 id 取对象后**不做 owner 校验**。
  API 描述符只能告诉你"这组 method 谁能调",**不能**告诉你"每个 method 是否做对象级授权"。
- **判据**: 审这类 API 时,对**每个** method 分别问:①取对象的键(自增 id? 哈希? 路径?)②该键是否绑定调用者
  ③handler 内是否有 owner/scope 比较(而不是只看全局 policy 如 `HasSharingPriv`/`grantable`)。
  **一个 method 正确 ≠ 同组其它 method 正确**;反过来,一个 method 的可疑也要逐个 method 排除。
- **配套(仿真前沿)**: 动态验证卡住时,先区分"**前置失败**"与"**拒绝执行**"——
  本目标 `delete` 一度对**所有**输入(含 owner 自删)回 401,看似"有门",实为 `create` 未产出真对象/
  手造行数据不合法导致取对象失败。判别法:让**合法前置下的 owner 动作**成功(负对照),再看低权用户是否也成功;
  若连 owner 都失败,结论是"不可分",而非"无洞"或"有洞"。
- **本目标**: `SYNO.FileStation.Sharing.{edit,delete}` 确证 IDOR(list 有过滤、edit/delete 无)。修 create 前置 =
  ①`path` 参数须是逗号分隔字符串(非 JSON 数组);②`WfmCheckPath` 读 DSM 内核 `system.synoacl xattr`(仿真 ENOTSUP)
  ⇒ 只 patch create 侧让它过,edit/delete 不调用它,故不影响判定。
- **证据**: `Synology-DS223-7.3.2-86009/notes.md` §26;`report/attachments/file-station-sharing-link-idor/`。

## 补丁/前置无关性的自证:用**全文件调用图**证明"补丁只影响 X,不影响被测 Y" | (2026-09-26/DS223 DSM 7.3.2-86009)
- **场景**: 为绕过仿真缺失的宿主特性(如 DSM 内核 `system.synoacl` xattr),复现脚本打了补丁;审查方会问
  "这个补丁会不会让漏洞结果失真?"。两阶段动态隔离常不可行(沙箱内 DB/服务不跨 run 持久)。
- **做法(不依赖运行持久性)**: 对被测模块与补丁符号所在库做**全文件调用图可达性**:
  ①`readelf -rW --use-dynamic` 取 `R_AARCH64_JUMP_SLOT` → 由 PLT 基址/GOT0 算出每个 `bl <plt>` 对应的导入符号名;
  ②`nm -D` 取已定义函数为节点;
  ③以 4 字节对齐**暴力扫** `bl`(调用)与尾 `b`/`b.cond`,源=最近前驱符号,目标=本地函数或 PLT 符号,跨 DSO 按**符号名**合并;
  ④从被测入口 BFS 到补丁符号,并**必带负控**(create 路径应可达;若负控也不可达 ⇒ 分析无效)。
  再核"补丁符号的 GOT 是否只被其 PLT 桩引用"(否 ⇒ 可能被取地址做间接调用,直接图不完备)。
- **本目标结论**: 强制 `WfmCheckPath` 返回 0 的补丁只被 create 路径经 `CheckSharingPrivilege` 调用;
  `edit`/`delete` 到它**无路径** ⇒ VR-02 与补丁无关(advisory 可据此撤回"补丁可能会影响"的保留)。
- **别踩**: 只扫"前 N 个文件/只扫本库内部 `bl`"会漏(本目标一度因只扫前 20 个 `.so` 误得"仅 4 个模块引用")。
- **证据**: `Synology-DS223-7.3.2-86009/artifacts/sharing_idor/{h14_callgraph.py,h14_patch_independence.md}`。

## 枚举"某方法是否对普通用户可达"必须**方法级 allowUser 覆盖 api 级** | (2026-09-26/DS223 DSM 7.3.2-86009)
- **形态**: DSM `.lib` 描述符的 `allowUser` 在 **api 级**可为 `normal.*`,但单个 method 可带**自己的** `allowUser`
  覆盖它(如 `SYNO.Core.QuickConnect`:`get`/`status` 对 normal 开放,而 `set`/`set_misc_config`/`Permission.set`/
  `Upnp.set`/`SNI.set` 在**方法级**是 admin-only)。按 api 级枚举会把这些 **admin-only 写方法误报为"normal 可达"**。
- **判据**: 有效 allowUser = **method 级 `allowUser` 若存在则用它,否则用 api 级**;写/配置类方法尤其要逐方法核。
  动态再验时:方法级 admin-only 的方法对普通用户应回 **105**(或 app 层 160),而不是业务成功。
- **本目标**: 按方法级重算后,非 FileStation 的 normal 可达 app 方法从"66(api 级)"降到"24",`set` 类全被正确排除。
- **证据**: `Synology-DS223-7.3.2-86009/notes.md` §28 / `artifacts/sharing_idor/h18_app_sink_triage.md`。

## 未剥离 Go 固件二进制:用标准库 `debug/gosym` 解 `.gopclntab` 恢复函数名/地址 | (2026-09-26/Synology DS223 DSM 7.3.2 `webrtcproxy`)
- **场景**: 固件里的 Go 二进制(如 QuickConnect `webrtcproxy`)常带 `.gopclntab`,但 ELF `.symtab`/`.strtab`/DWARF 可能
  被截断(段表指向文件尾之外 ⇒ `nm`/`go tool nm`/`pyelftools` 报 "unrecognized object file"/"section extending past end")。
- **做法(零依赖,Go 标准库)**: 读 `.data.rel.ro.gopclntab`(VA 与文件偏移的 delta 按段头算;本目标为 0x10000),
  取 `pcHeader` 得 `textStart`;用
  ```go
  lt := gosym.NewLineTable(pclntab, textStart)
  tab, _ := gosym.NewTable(nil, lt)
  for _, f := range tab.Funcs { fmt.Println(f.Entry, f.Name) }
  ```
  即得全部函数名+VA(本目标 11374 个)。随后按 "VA→文件偏移" 用 capstone 反汇编,`adrp+add/ldr` 解析 rodata 字符串
  (Go 字符串常量拼在一个大 blob 里,按 (ptr,len) 取;本目标 host=`127.0.0.1`、格式串=`http://%s%s` 一目了然)。
- **别踩**: `go tool nm`/`go tool objdump` 会因 ELF 尾部截断或 Go 版本不符而 "unrecognized object file";改走 `debug/gosym` 直接吃 pclntab 字节。
- **证据**: `Synology-DS223-7.3.2-86009/artifacts/emu/h25_webrtcproxy_ssrf.md`(解析器 `/tmp/gopcln.py` 思路:magic 0xfffffff1 / functab(uint32 entryoff,uint32 funcoff)/ _func nameOff 索引 funcname 表)。

## 判"转发/代理 JSON 的某字段是否客户端可控":必须核**赋值点 + 动态捕获** | (2026-09-26/Synology DSM 7.3.2,VR-01 的 `port`)
- **形态**: 预认证中继 API(`SYNO.WebRTC.Proxy.Sharing.create`)把一批请求值转发给本机守护。JSON 里同时有客户端可控项
  (header→`forwardedFor/cookie/host/origin/referer/agent/acceptLanguage`)与服务端填充项(`port/isIpv4/sid/signalAddr`)。
  只看"字段出现在 JSON 里"会误判 `port` 可控 → 误报"预认证 SSRF 任意端口"。
- **判据(两条独立)**: ①**赋值点**: 列出 `SetCommonParam` 复制的 header 白名单 —— 不在其中的字段不由客户端 header 决定;
  ②**动态捕获**: 探针**不发**该字段却仍出现在捕获的转发载荷里 ⇒ 服务端填充(本目标 `port` 未发,捕获值=该次仿真的 DSM 端口)。
- **本目标结论**: webrtcproxy `GetTargetURL` 的 host:port 为固定回环 DSM 地址(`127.0.0.1` + `GetHttpPort()`),
  攻击者仅可控 path/method/headers(= 既有 VR-01);"任意端口 SSRF"否证。
- **证据**: `Synology-DS223-7.3.2-86009/artifacts/emu/h25_webrtcproxy_ssrf.md`。

## Synology(SRM/DSM 家族)webapi 的动态派发:优先用固件自带 `synowebapi --exec`,别去凑 httpd/SCGI 注册表 | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测)
- **先试的错路(记录以免重复)**: 直接跑 `webapi/entry.cgi` 恒回 `{"error":{"code":102}}`(API 不存在)——
  给全规范 CGI 环境(`SCRIPT_FILENAME`/`PATH_INFO`/`GATEWAY_INTERFACE`/`REQUEST_URI`/`DOCUMENT_ROOT` …)也一样;
  连核心 API `SYNO.API.Info.query` 都是 102。根因: 注册表未装载,而 **SRM 固件里根本没有 `synoscgi`**
  (只有 `lib/httpd/modules/mod_proxy_scgi.so`)⇒ 不要把 DSM 那套"起 synoscgi + taskmgr/tokenmgr"照搬过来。
- **正解**: 固件自带命令行客户端 `/usr/syno/bin/synowebapi`:
  `synowebapi --exec api=<名> method=<法> version=<n> [key=json_value ...]`
  —— 实测**直接送到真 handler**,不需要 httpd、不需要注册表(官方用法见 `/etc/init/*.conf`,如 `autoupdate_schedule_set.conf`)。
  ⚠️ **字符串参数必须写成 `key="\"value\""`**,否则被当 JSON 数字解析而截断(`203.0.113.99` → `203.0`)。
- **并行事实(判"该走哪条")**: 该 webapi 有**两套并存**注册表 —— `webapi/*.api`(**45 个,带 `path`**,指向 per-API CGI)
  与 `webapi/**/*.lib`(**413 个,只带 `lib`**,指向 `.so`),**两者交集为 0**。
  `.api` 风格可直接按其 `path` 打(例: `FileStation/info.cgi?api=SYNO.FileStation.Info&method=getinfo&version=1`
  → 无会话得 `105`,说明**派发成功并到达鉴权门**;这与 102 的区别就是"面是否存在"的判据)。
- **配套手法(sink 记录器)**: 要把"目标究竟构造了什么命令"变成**可读原文**,把 sink 二进制(如 `/usr/bin/ipset`)
  临时替换成把 `"$*"` 追加到日志再 `exit 0` 的脚本,原件备份为 `<名>.real`(不改内核状态也能证明数据流)。
  ⚠️ 替换前先判 `[ -L ]` —— applet 常是指向 busybox 的软链,直接 `cat >` 会毁掉 busybox 本体。

## 判"预认证可写接口"的**实际收益**前,先问一句:正常流程本身要不要凭据? | (2026-09-27/SRM-RT6600ax 1.3.2-9366 实测)
- **场景**: 预认证 API 接受**客户端自报的身份/地址**(本例 `CaptivePortal.NoAuth.set_ip` 收任意 `client_ip` 并写入
  captive portal 放行集合)。静态看是"信任边界违例",很容易直接写成中危 authz 绕过。
- **判据(先做这三步再定严重度)**:
  1. **正常流程有没有秘密**: 去看合法调用者 + 门控页面 —— 若正常流程**只需点一次同意/无需凭据**,
     则"绕过"与"合法通过"能力等价 ⇒ **收益≈0**,只能算信息项。本例实测: portal 配置键里
     只有 `access_timeout_second`/`use_template`,**没有 password/voucher/RADIUS**;`welcome.html` 无口令字段。
  2. **边际能力是什么**: 逐条问"合法调用者做不到、攻击者做得到的事"。本例 = 只能为**第三方地址**放行
     (自己那台本来点一下就行)⇒ 差异极小。
  3. **下游语义**: 该放行集合**被什么规则消费**(本例 `iptables -t nat … ! --match-set ipset_captive_portal src … -j REDIRECT`)
     —— 若下游只影响"是否显示拦截页",则影响面就这么大;若下游是**计费/条款/审计**或后续会加**凭据模式**,
     同一缺陷会升级(要在报告里写成条件)。
- **反例价值**: 反过来也成立 —— 若正常流程**确实需要凭据/票据**,那么"客户端自报身份被采信"就是**实打实的认证绕过**,
  应当按高影响报。所以这一条判据的作用是**防两头误判**(既防夸大,也防把真 bypass 当成点一下)。


## 判"某入参是不是主体/对象"时,**参数被读取 ≠ 被采用**(2026-09-29 QNAP userConfig 实测)
- **形态**: `CGI_Find_Parameter(Input,"subUser")` 在 4 个 handler 里都被调用,看着像"以 subUser 为主体" ⇒ 第一直觉是 IDOR。
- **实测**: 存入的局部**是死栈槽**(写后无读)或**返回值直接丢弃**;真正喂进 FS sink 的是**会话用户名全局**(`auth_get_session` 写入的那个 buffer)。
- **纪律**: 否定"IDOR/越权"时,必须把**入参到 sink 的数据流走完**(谁把它读出来、传给谁);只看到"读了这个名字"就报/或就排除,都会错。
  同理:按**参数名**(`id`/`user`/`owner`)筛出的候选表天生会假阳 —— 名字筛只用于**排序**,定论必须落数据流。
- **附带**: 先取**身份来源**再做判断 —— QNAP CGI 的主体全局 = `auth_get_session(sid,0,&buf)` 写的 `buf+0x52`(可由 `.rela` 里指向该 `.bss` 的 `R_AARCH64_RELATIVE` 16 进制确认),不是任何请求头。

## 静态筛选:函数画像 × 动作表 join,先找"**有危险汇、无直接门**"的 handler | (2026-09-29/QNAP)
- **要解决的问题**: 一个 CGI 上百个 action ⇒ 逐个动态验太贵;先用静态把**候选**缩到个位
- **做法**:
  1. **动作表**: 从 `main` 的 if-else / `strcmp` 链机械抽出 `func 值 → handler 地址`(r2/Ghidra 批处理,别手搓);
  2. **函数画像**: 对二进制每个函数出 `size / n_calls / n_params / gates(直接调用的门类符号) / sinks(危险汇:execv、qnap_exec、system、strcpy…)`;
  3. **join**: 动作表 × 画像 ⇒ 三档排序:**有汇且无门** ⇒ 最高优先;**有门且有汇** ⇒ 次之(看门是否覆盖该动作);**无汇** ⇒ 先放。
- **常用门类符号名(厂商私有库,按目标替换)**: `Auth*`、`User_Belongs_To_Group`、`Is_User_Admins`、`Role_Is_User_Delegated`、`naspriv*`、`Is_Webfs_Enabled`
- **注意(踩过)**:
  - **地址基准差**: Ghidra base = IDA base + **0x100000** ⇒ join 两张表前必须换算 ✗(否则全错配);
  - **`gates` 只算"直接调用"** ⇒ 门可能在其调用的**前置函数**里 ⇒ "无门"只作**排序因子**,不是结论 ✓;
  - 抽取前先**验工具**(例:`r2` 的 `pdf` 在 section header 不可信的二进制上会给 0 条指令 ✗)。
- **判据**: 这套只产 **L2 候选榜**;每个候选仍要 L1(真会话差分:低权 vs 管理员)或 L3(handler 内门/汇实参)才能定论 ✓
- 来源: `QNAP-TS-233-QTS-5.2.x` `2026-09-29`

## 同版本组件的 native AOT vs 姊妹产品 Java 字节码:用后者反推前者 | (2026-10-02/Ubiquiti UDM Pro 5.1.33 实测)
- **场景**: 目标固件里的 Java 应用被 GraalVM **native-image AOT** 成单个大二进制(本例 `usr/lib/unifi/lib/unifi` 377 MB、stripped、无 `.symtab`/DWARF、方法名只在 `.svm_heap` 元数据里)。直接反汇编代价高、无符号。
- **做法**: 若**同一厂商在另一产品/镜像里附带同版本的 Java 字节码**,优先用字节码:
  1. 定位取件源: 本例上一目标的 `uosserver` **docker 镜像仍在本机**(`docker images`);`docker create <img>` → `docker cp <cid>:/usr/lib/unifi/lib/ace.jar /tmp/`;
  2. `ace.jar`(Spring Boot fat jar)的应用类在 **`BOOT-INF/lib/internal-dependencies.jar`**(顶层没有),先 `unzip ace.jar BOOT-INF/lib/internal-dependencies.jar` 再解内层;
  3. 反编译: **CFR**(`repo1.maven.org/maven2/org/benf/cfr/0.152/cfr-0.152.jar`,~2 MB)+ `java -jar cfr.jar <class> --outputdir out`;交叉校用 `javap -p -c`。
- **判据(必须成立才可用)**: ①**版本一致**(本例 UDM Pro `unifi-native 10.5.67-35187-1` == UOS Server 的 Network app)②差异只应是**平台接线**(device profile / 端口),核心 handler/鉴权/filter 逻辑同源。
- **边界(必须写明)**: 字节码来自姊妹产品 ⇒ 只能作**静态判据**;本目标设备上的**可达性/端口/部署**仍须分开证(本例 `UbiosHttpsFilter` 端口映射与 `web.xml` 在本目标 rootfs 独立取证)✓
- **收获示例**: 一轮内读到 `ServiceServlet`(预认证门 `getLocalAddr()` loopback + 无 `X-Forwarded*` 头)、`UbiosHttpsFilter`(8443/28082 的 URI allow-list)、`/v2/upload/*` 家族全部 `@*Mapping` ⇒ 把"未判定"面直接推到定论。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-02`

## 别把 HTTP 201 / `@ResponseStatus(CREATED)` 当作"真的建了资源" —— 先看注解 | (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测)
- **场景**: 一个未认证上传端点返回 `201`,看似"成功创建了资源(⇒ 有副作用 ⇒ 可立案)"。
- **判据**: 反编译 controller,若方法标 **`@ResponseStatus(HttpStatus.CREATED)`** ⇒ **201 是框架按声明返回的固定状态码**,与"是否真的落库/改状态"无关。
  必须追到 **service 的写路径**(`repo.save`/`device.set`)并**动态印证落库**(DB/文件),才能判影响。
- **本例**: `POST /v2/api/site/{s}/ssl-inspection/applications` 合法→201/非法→400;但 `save` 被 `deviceManager.findBySiteId(site).ifPresent(...)` 门控,
  无已 adopt 设备时 201 而**零落库**(mongo 90 collections 无痕)⇒ 判"未见/未证伪 + 显式降级"(不是 finding)。
- **反面**: 也别因"200/201"就跳过 —— 用它定位 handler 是否到达,再查副作用。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-03`

## port×URI allow-list 型连接器: 每个端口都可能暴露**多个**未认证 handler —— 逐条审,别只看被点名的那个 | (2026-10-03/Ubiquiti UDM Pro 5.1.33 实测)
- **场景**: 厂商内部连接器(本例 Network app 的 "ubios" 端口)用 `Filter` 做 **端口 → URI 允许表**(如 8443 放行 `wifiman/.*`、`ssl-inspection/applications`、`fingerprint/assets`;28082 放行 `/v2/upload/*`、`/api/ucore/support-dump`)。
- **坑**: 历史审计只覆盖被**点名**的那条(如 wifiman),把同端口其余白名单条目当"同族已覆盖" ⇒ 漏面。**白名单里每一条都要单列并逐个判**。
- **做法**: 反编译该 Filter 取**精确 allow-list**,对每个 `uri pattern × port` 建一个面,再逐条给 disposition。
- **本例收获**: 8443 上 `ssl-inspection/applications`(未认证 catalog 解析上传)与 `fingerprint/assets`(未认证按 filename 取资源)是上一目标未审的入口。
- 来源: `Ubiquiti-UDMPro-UniFiOS-5.1.33` `2026-10-03`

## 鉴权门用“别的文件模式 / 状态旗标”代理时,要枚举该状态下**全部**可达命令 | 判据/步骤 | 证据/产物: QNAP-TS-464 | (2026-10-03)
- **判据**: 门的输入若不是会话/凭据,而是 `stat()` 某个文件、某组件存在性或状态旗标 ⇒ 该门只能区分**状态**、不能区分**命令**;
  必须把"放开"那一支到分派的**完整命令表**逐条判副作用,禁止只抽查几个就下结论。
- **步骤**: ①反编译找门函数并追到其输入;②把状态判定为"放开"的那一支到分派的**完整**命令表拉出来;
  ③在**造出该状态**的环境里无凭据逐类跑命令(读数 / 改配置 / 改状态 / 上传),取另一状态做**负对照**差分。
- **取证提示**: 状态由**文件模式位**决定时直接 `chmod` 换态即可复现,但报告须写明真机上由哪个 boot 脚本设置该模式(否则像自造条件)。
- **证据/产物**: `qlicenseRequest.cgi` 门 = `stat(quick.cgi)` 的 owner-x;未配置态整表免鉴权;`set_proxy` 把攻击者代理**持久化**到
  `/etc/config/qlicense/qlicense.conf`。(来源标签: QNAP TS-464 QTS 5.2.10 轮;该目标已重置,'即查即用'不指向目标内文件)

## 控制流劫持类缺陷的**影响评估**清单(厂商表单口径;一次跳转即可触发的无参执行点) | 判据/步骤 | 证据/产物: Linksys E8450 轮
- **判据**: 拿到"任意跳转"后,影响档位由**可达的特权代码**决定,不由"能否叠链"决定 ⇒ 必须给出可达特权调用点清单,而不是只写"可劫持 PC"。
- **步骤**: ①算固定偏移并实测 `PC` 可控(部分覆盖即可);②扫全指令流找"**无需参数、不碰栈**"的现成执行点(模式:静态串加载 + `bl <命令解释器>` 相邻三条指令);③把清单按"特权/破坏性"分类(`reboot`/`mtd`/`rm -rf`/服务重启/GPIO…);④**动态实证一条无害的**(选真机上也安全的命令,观察命令确被执行);⑤如实写出"任意命令"是否成立(参数控制是否打通)。
- **取证提示**: 断点不要下在"命令解释器入口"——正常流程也会调用它 ⇒ 先断在**自己选定的跳转目标**再往下看,才能证明是被劫持的路径。
- **证据/产物**: 117 个无参执行点 / 50 个特权;实测 `service_lan_restart.sh restart` 与 `cp -rf /etc/config/network_init_backup /etc/config/network` 被执行。(来源标签: Linksys E8450 2026-10-04)

## 提交前核'最新版' + 逐 finding 组件哈希差分 + 动态复测(同一更新里常有 A 修了/B 没修)
- **判据**: 报告里写 "latest release" 前,必须**用厂商 API/发布页确认当前 latest**,并对**每条 finding 的核心组件做哈希差分 + 动态复测**——**不要按"整包变了/没变"推断**。
- **实测(2026-10-20,QTS 5.2.10.3577 → 5.2.11.3652)**: 同一次更新里:
  - `libqlicense.so` + `qlicenseRequest.cgi` **逐字节相同** ⇒ 该漏洞**在新版仍存在** ✓(动态:无会话 `code 0` + 持久化写 + 负对照均复现);
  - `reset_password.cgi` **已重建** ⇒ 该绕过**已被修** ✗(同一请求由返回 `-1` 变 `-2`);
  - `WSDiscovery.py` 变了但解析器仍是 `minidom.parseString`(无 defusedxml)⇒ 机制尚在,须动态复测。
- **做法(3 步,半小时量级)**: ①厂商 download API 取 latest(记录 URL/size/md5)②解包后对每个 finding 的组件 `md5sum` 对比旧版(相同 ⇒ 漏洞大概率仍在,须动态确认;不同 ⇒ 必须重测,可能已修)③对"仍相同"的跑一次原 PoC(正例 + 负对照),对"已变"的做差分 —— **变了的那些极可能是厂商在修上一版报的洞**。
- **意义**: 厂商一次发布常常**只修一部分**;把"latest + 组件差分 + 动态复测"当提交前的固定动作,可避免(a)把已修洞当新洞交(b)漏掉"同一根因在新版仍在"这一加分事实。来源标签: QNAP TS-464 5.2.11 复验(2026-10-20)。

## 用应用自带 route dump × scope 闭包清单做"缺鉴权路由"筛选
- **做法**: 先找应用自带的运行时路由表(本例 `GET /api/v2/routers` 返回 839 条 method/path);再从 Go 二进制
  的 `prepare*Router` 反汇编里解出每条路由挂的 scope 闭包(字符串捕获在 `scopes.go` 闭包构造处)。
  两者 join 后,**无 scope、handler 是 dispatcher、入参可控**的路由优先。
- **负对照**: 同前缀下一条 scoped 路由(本例 `POST /api/v2/access_group`)从同一非 loopback 源应 401;
  目标路由若进入 handler(业务错误/内部调用栈)即缺口成立。
- **注意**: 应用把内部组件代理注册在主 API 时,"同机调用"不是鉴权;反代会把前缀整体转发。
  判"无影响"前必须看 handler 是否触达内部 service;只看到 200/业务错误不等于无副作用。

## 厂商下载页是 JS 壳时:用无头浏览器 MCP 抓真实下载 API(拿到直链再回 curl + md5)
- **判据**: `curl` / webfetch 只拿到 HTML 骨架、**取不到版本号或直链** ⇒ 该页由前端渲染 ⇒ 换"能执行 JS"的手段(无头浏览器),而不是继续猜 URL 或放弃取件 ✗。
- **步骤**: ①`navigate_page` 打开产品下载页 ②`take_snapshot`(拿渲染后的 DOM 与链接)③`list_network_requests`(筛 `api|ajax|download|firmware|json` 找真实下载 API/直链)④拿到直链后**回到 `curl`** 下载并做 md5 校验,写 `SOURCE_URL.txt`(不要用浏览器下大文件:易中断、无校验)✓。
- **坑**: 必须 `--headless`(无显示宿主)与 `--proxyServer`(**页面与子资源都要过代理**,否则 JS 加载不全 ⇒ 快照是空的 ✗);`--isolated` 避免脏 profile;MCP 配置改完要 `/reload` 才进会话 ✓。
- **边界**: 跑的是**真浏览器** ⇒ 只碰公开下载页,**不代填密码** ✓。
- 证据/产物: 2026-10-27 装 `chrome-devtools`(`pi mcp list` ⇒ `connected, 32 tools` ✓),用它解决"厂商下载页 JS 壳"这类取件阻塞。

## 跨 WSL 用 --autoConnect 连 Windows 上的已登录 Chrome(必须带 --userDataDir,且要真调工具才知通不通)
- **判据**: 需要**用户已登录的浏览器**做自动化(填厂商表单/取登录后页面)而 agent 跑在 **WSL** 里 ⇒ 目标浏览器在 Windows ⇒ 只有两条语义不同的路:①`--autoConnect`(连**本机同 OS** 的 Chrome,靠 user-data-dir 里的 `DevToolsActivePort`)②`--browserUrl`(连一个**已开调试端口**的浏览器)✓。
- **步骤**:①MCP 参数加 `--autoConnect --userDataDir <Windows 的 .../Google/Chrome/User Data>`(**不带 `--userDataDir` 会去找 Linux 的 `~/.config/google-chrome` ✗**)②让用户在**自己的** Chrome 打开 `chrome://inspect/#remote-debugging` 并开启(这一步无法由 agent 代做 ✗,是 Chrome 136+ 的授权设计 ✓)③探活:先看 profile 下出现 `DevToolsActivePort`(首行=端口),再用 stdio 真调一次 `list_pages` 判定 ✓。
- **坑**: WSL2 默认 NAT ⇒ `127.0.0.1` 到不了 Windows;`networkingMode=Mirrored` 下才直通 ✓;`--autoConnect` 是 **stdio 服务**,进程起来不会报错 ✗ ⇒ 必须**真调一个工具**才知道通不通 ✓。
- 证据/产物: 2026-10-30 实测 `--autoConnect --userDataDir /mnt/c/Users/<u>/AppData/Local/Google/Chrome/User Data` 从 WSL 接上 Windows Chrome 156,`list_pages` 返回真实标签页 ✓;不带 `--userDataDir` 报 `Could not find DevToolsActivePort at /home/<u>/.config/google-chrome/…` ✗。

## 提交前先判「收不收」:读 scope 原文 + 上报渠道(security.txt→PSIRT→平台)+ CVE 走 CNA/CNA-LR 而非猜邮箱
- **原则:不凭印象判"收不收"** ✗ —— 每个项目在提交页/政策页都写明 scope 与排除项,**必须读原文并引用**;KB 不预存"某类一定不收"的结论 ✓。
- **做法**:①找到该项目 scope / policy 原文(记 URL + 摘句)②逐条核对我们的 finding 是否落在排除项里 ③把**项目原文**与**我们的对应事实**并列写进 notes(判据 = 原文)④**读完原文就分流,别一律去撞一次** ✗:原文**明确排除**(写明不收 DoS / 第三方组件 / 需非默认配置等)⇒ **不提交赏金**(只会拿拒信),把排除原文记进 notes,**直接走 CVE 路由**(按该 CNA 的指定联系方法请求 CVE ID,或走 CNA-LR)✓ —— 漏洞成立性不因项目不收而改变 ✓;原文**没写到 / 有歧义** ⇒ 记「未知」、**照常提交**(别用"我猜不收"当理由 ✗);**明确在 scope** ⇒ 照常提交 ✓。⚠️ 读原文时区分两件事:**「不受理」(report rejected)** 与 **「受理但不给奖励」(not eligible for reward)** —— 后者(常见于 DoS / 资源耗尽)厂商仍可能修、仍可请求 CVE ⇒ 先记清是哪一种,别一律当"不收"处理 ✗。
- **上报渠道:先查厂商"公开指定的"入口,不硬编码邮箱** ✗ —— ①`https://<厂商>/.well-known/security.txt`(RFC 9116:写明渠道/表单/邮件 + PGP 键,如 QNAP 实测为 `mailto:security@qnap.com` + PGP)②厂商安全响应 / PSIRT / 漏洞报告页 ③赏金平台项目页。**是平台就提平台、是表单就填表单、是邮箱才发邮件** ✓。
- **厂商联系不上 / 该产品没有 CNA 时的官方兜底 = CNA of Last Resort(不是猜 `security@`** ✗):cve.org「Report/Request for Non-CNAs」给出的三步 —— ①在 CNA 名单(List of Partners)里找 scope 覆盖该产品的 CNA ②**按其披露政策、用它指定的联系方法**报告并请求 CVE ID ③判断不出 ⇒ 找 CNA-LR:**CISA ICS CNA-LR**(`us-cert.cisa.gov/report`,ICS 与医疗设备)或 **MITRE CNA-LR**(`cveform.mitre.org`,其余全部、以及未被任何 CNA 覆盖的开源)✓。MITRE CNA-LR 自述适用场景 = 供应商**没有指定 CNA**,或分配**存在争议** ✓。
- **CVE 请求的最小字段**(MITRE 表单):problem types / vendor 或 project / product name / version / vulnerability description / references URL(+CVSS 可选)✓ —— 与赏金报告要的"在 scope 内 + 严重性 + 可复现"**不是一回事** ✓;表单可匿名、**不要求先有赏金** ✓。
- **赏金 ≠ CVE(两条独立的路)**:赏金 = 项目方按 scope 与 severity 决定**给不给奖励**;CVE = **标识符**,由 CNA 按 CNA Rules 分配,任何人可请求 ✓。CVE 的 CNA 定义里明确包含 bug bounty provider ⇒ 平台本身可能就是 CNA ✓;部分项目在被接受时**代申请**(以项目页为准,别假定 ✓)。
- **纪律**:90 天是行业惯例不是硬规定 ✓;未修复/未过通知期**不擅自公开** ✗,公开由用户决定 ✓;厂商拒收但技术成立 ⇒ 记录拒收理由、继续走 CVE 路由、**不改写结论措辞** ✓。
- **口径:不预设优先级** —— 赏金与 CVE 平行,按**项目原文**决定怎么走;**既不为了钱改结论措辞,也不为了 CVE 绕开项目** ✓(用户 2026-10-30:目前**不**以赏金优先)。**CVE 从哪来取决于"接收方是不是 CNA / 有没有开 CVE 功能",不是"提交时顺手请求"** ✗:①**厂商本身是 CNA**(如 QNAP,`CNA-2017-0030`)⇒ 通常随修复由其分配,报告里往往就有 `CVE ID` 字段(填"未分配"即可)✓;②**平台是 CNA 且该 program 开启该功能**(HackerOne 文档原文:组织在平台请求 CVE,advisory 公开后 HackerOne **自动分配**并发布到 MITRE ✓;但明确写明"not available to all editions" ✗)⇒ 研究者侧不需要额外动作 ✓;③**两者都不是** ⇒ **不会自动有** ✗,由研究者按 CNA / CNA-LR 流程自己申请 ✓。⇒ 所以先判"这次接收方属①②③哪一种",再决定要不要自己申请;**别假定赏金会给 CVE** ✗。
- **不存在"赏金 + 同时要 CVE"这种合并动作** ✗ —— 提交就是一次报告;"有没有 CVE"由**接收方身份**决定(厂商是 CNA / 平台开了功能 ⇒ 自动有 ✓;都不是 ⇒ 没有 ✗)。唯一需要研究者主动"申请"的情形只有一种:接收方不是 CNA 而你确实要编号 ⇒ **另走 CNA / CNA-LR 申请**,与赏金流程互不相干 ✓。
- **三条实际路由(2026-11-02 用户口径)**:①**厂商明确拒绝**(有沟通记录 / 拒信)⇒ **MITRE CNA-LR**(`cveform.mitre.org`)✓;②**没沟通厂商、但按其原文显然不会收**(明确排除或不符合)⇒ **投 VulDB** ✓(VulDB 本身是 CNA:实测 CVE 记录 `assigner=cna@vuldb.com` ✓;其投稿**不要求公开链接** [用户口径,其站点反爬未能实取核对 ✗])—— **不必先去撞一次拒绝** ✓;③其余 ⇒ 正常提交原厂商 / 其 CNA ✓。
- **厂商官方模板先照抄,别自创格式**:各家的报告页常带"示例模板"(如 QNAP 的 OS / App / Cloud 三个 `#...ExampleModal`,见 `intel/advisories/qnap-submission-templates.md` ✓)—— 字段、标题写法、必填项(如 Required privileges / 原始请求响应)按模板来 ✓。

