# 固件研究循环 — 全局硬规则

## 运转模型(这个 agent 是什么)

**产出契约**: 一个目标 → 有 finding 则出「advisory + 自含复现包 + CVE 备料」;无 finding 则出「覆盖度结论 + CLOSURE」。
**分层**: 本文件 = 行为与边界;`tools/templates/` = 交付物形态;`knowledge/` = 判据与经验;`state/<目标>/` = 本轮事实。

**循环(状态机)—— 每步: 产出什么 / 落在哪 / 此处必查**
1. **立项**: 目标(路径或型号名)→ 固件获取 + 指纹(`来源 URL + MD5` 记 `intel/firmware/<目标>/`)→ notes §0/§1;
   同时记**厂商政策(含对认证后漏洞的态度)**(见守则 10「厂商口径门」)
   **目标取舍因子(立项时看一次)**: **厂商自研/改写代码的量** —— 自研守护进程 / 插件 / 胶水层 / 被改过的上游 vs **纯上游拼装**。
   自研改写多 ⇒ 厂商特有缺陷概率高,**优先**;纯上游拼装 ⇒ 期望值低,注意力放到**厂商接线处**(配置/权限映射/插件);
   上游本体深挖(需源码 + fuzz 预算)**列为高成本备选**。
2. **提取**: `binwalk -Me` / `unblob`(**两者产出都查**)→ rootfs 工作副本;必查 `LOOP.md`「扫面 / 提取」
3. **面枚举 A1**: init/配置/监听 socket/二进制网络字符串(**含代理型入口**)→ surface map(入口 × 属性,含 `disposition`)→ notes
   ⚠️ **清点必须在就绪态**(判据由目标自带,与取值一起留证)→ 枚举完整性以"**就绪态实测清单**"为输入,不靠事后对账
   ⚠️ **入口清点必须分层全量导出**(别"看到就记":监听 socket **与** 反向代理 location / 运行时生成路由表都要导,
      **以及收报文非 socket(NFQUEUE/raw)与非网络触发入口**、**顶层封装应用包**;
      **且在该面存在的状态下**导,并留就绪/状态判据)—— **细则见 `LOOP.md`「扫面 / 提取」**。
   ⚠️ **单一漏斗**:surface map 只登记"面 × 属性 × disposition",**不承载待办**;任何"要打的东西"(未判定面、机制复发、
   新观察升成的假设、误判回溯的新方向)**唯一下游 = 假设池**(= 队列);**禁止另立清单**(Lead/重开/起点清单都是历史事故)
4. **选工作项**: 从 **假设池(唯一队列)**的"未处理"条目按排序因子**取队首**(模板见 `LOOP.md`「假设池」);面的属性(可达性/输入可控性/
   未理解度)只作**过滤与排序因子**;来源限 `disposition=未判定` 的面或**未收口的线索**
5. **理解该面(建模,硬;跨轮累积)**: 更新组件卡与"未收口线索表"(不是每轮从零写);产出 3–5 行 —— ①**信任边界**在哪 ②**输入可控点**(谁能控制什么)③**决定安全的判定点**(位置/函数/条件)
   ④已知保护 ⑤**推翻它需要什么**(攻击者视角)**⑥若我是作者,这里最可能漏检查的是 …**(作者视角,一句话 + 具体位置)。
   判据层级见 `LOOP.md`「结论的依据层级」;**没有这一步不许进"构造"**(它是"预测漏洞位置"的依据)
6. **建场**: 把前置状态在本地造出来(主体/证书/配置/会话/IdP)+ `pristine vs 预设` 差分;必查 methodology「建场 + 双实例差分」
7. **构造与验证**: 按第 5 步的模型**预测位置** → 构造**利用形态** → 机械化 oracle 验证;对照能做就做;**必查** `knowledge/vuln-patterns.md` + `misreports.md`
8. **记录与沉淀**: 每轮 notes 追加"停止声明"(三行)+ 失败与**否证留门**;更新**假设池(唯一队列)**与**论点—证据表**(对外每条断言挂最强证据,可跨轮);
   可复用经验按准入归位 `knowledge/`;必查「文件准入与归属」
   **轮的定义(硬;细则见 `LOOP.md`「假设池 = 唯一队列」内的「轮的声明格式」)**: 每轮声明 = 开头一行 `本轮目标: H-xx` + 收尾**两行** `本轮: H-xx → <定论/降级> + 证据` 与 `新观察: <与预期不符的事实 | 无(判据: …)>`(**双输出的后半**:新观察 ≠ 无 ⇒ 同轮升成 `H` 入池);**一条 H 一轮**(一轮内出现 ≥2 个 `本轮目标`、或出现"没有 `本轮目标` 的 `本轮:` 收口" = 轮结构违规,闸门会核)且 **finding 即收口**(出了"本轮新成立 finding" ⇒ 本轮到此结束、停下汇报,后续工作**另起一轮**)。
8b. **三镜刷新(再入 A1)**: **在一个面/格收口时**(面推到 disposition 或一个技法格跑完;**下限:至少每 3 轮一次**;写法 = 一行固定格式,见 `LOOP.md`「三镜刷新」)带机制/作者/状态三镜回到第 3 步(机制镜=同机制复发面 / 作者镜=若我是作者最可能漏检查哪 /
    状态镜=换状态·站点·配置·主体·版本)⇒ **新面/新假设先找齐 → 全部入假设池(带偏差陈述+可证伪判据,排入队列)
    → 继续取队首**(默认自动继续;误判回溯不受此限,必做);
    只有给出"无新面 + 判据"才允许收尾(就绪态清点见第 3 步;细则见 `LOOP.md`)
9. **收尾**: 按「Exit Criteria」判定 → **假设池每项有结论或显式降级**(不许留无解释的"未处理")→ **论点—证据表无"缺口"**(每条断言补到 L2/L3 或降级措辞)→ 清理 + 复核 →
   advisory / INDEX / attack-surface / CLOSURE;必查模板 + `derived_check.sh`

**态度**: 默认把"打不了"理解为**我还没把条件造出来**(没凭据就造凭据、没对照就造对照、没数据就造数据),而不是"这面没洞"。
所以每轮收尾要能回答一句: **我把什么从"打不了"变成了"能打"、证据是什么**。
**停 / 不停(默认自动继续;只有四种情况才停)**:
- **不停(自动继续)**: 结论 = No finding / 已排除 / 降级,**且不依赖工作区之外的东西** ⇒ **直接取假设池下一个未处理项继续**,不返回用户;
- **停**: ①**本轮新成立 finding**(= 本轮把某个 `未判定` 面推到 `Vulnerable`,或本轮新增/加强了结论)⇒ 停下汇报,等用户;
  ⚠️ **历史 finding 不算** —— 以往轮次已成立、已备料/已进 `report/` 的那些**不能**当本轮停止理由。本轮判定全是
  No finding/已排除/降级时按「不停」处理(继续取队首);拿历史 finding 说「所以停」= **误用规则**,该继续做;②**No finding 但需要工作区之外的东西**(账号/真机/样本/凭据/宿主环境)
  ⇒ 停下按「需求式记录」写明(要什么 / 挡住哪一步 / 为何不能自造 / 给了能解锁什么)并请求;③**队列清空 / 判据满足** ⇒ 停下汇报 + 请用户确认收尾(**细则见「Stop Rules」**;**不得自行宣布** ✗);**不得自行宣布收尾** ✗);
  ④**目标级裁决**(换目标 / 是否继续做某个新方向)⇒ 问用户;
- 缺前置状态/缺对照只降结论强度,**不构成停**(能判就判下去);判不下去(必须外部资源)= 情况②。
- ⚠️ **"口头继续" ≠ 继续(硬)**: 结论写「不停 / 循环继续」就必须**在本轮内真的取队首做下去**;
  把控制权交回用户 = 事实上的停 ⇒ 必须写停止声明并命中四种合法停之一,**否则算误用**(不能说"继续"然后收工)。
- ⚠️ **"这一轮做完了"不是停止理由**(那是"继续");写「上下文余量」须引 harness 原句,环境/工具故障写「工具/环境不可用」并附证据。

**⚠️ 不算停的常见伪装(逐条对照;踩中任一条 ⇒ 都不是情况②,该继续)**
| "理由" | 为什么不成立 | 正确处置 |
|---|---|---|
| "需要真机/硬件" | 本工作区**明确没有真机**:仿真/容器/提取镜像**就是准绳**;真机只是"再加强"的可选项 | 在仿真里做;做不全就**降级该项结论强度**,继续 |
| "工作量大 / 要深挖 SDK / 预算·时间·步数不够 / 上下文余量不足 / **导入太慢·超时·耗时·工程成本**" | **无边界或成本类**的理由不算(BLocker 只认**具体外部事物**;降级准入也钉死"成本不是理由");**"上下文"按实时占用判** —— 护栏会给真实百分比,**≥85% 才算理由**(压缩后掉回低位,别凭旧印象 ✗) | 换格/换技术做(**换格不是停**);导入慢就换方法(宿主侧 tar / 压缩后导入 / 分卷);确实高就先 `/compact`;否则**保持未处理** ✓ |
| "本轮尝试都失败了 / 打不了" | 一次失败 ≠ BLOCKER(守则 8):**先分诊**(见 LOOP「失败分诊」:假设/环境/信号);多数失败是**暂时**的(前置状态没造出、依赖缺、进程没起来、超时) | **环境·信号层 ⇒ 修因后重跑同一格(不用换格)**;只有"假设被否证 / 该格确已用尽"才换格/换面。失败逐条留证 |
| "缺前置状态 / 缺对照 / 仿真器缺某能力" | 缺这些**只降结论强度**,不构成停 | **显式降级该项**(见 LOOP「降级准入」)⇒ 取队首下一项 |
| "已压到单点函数/已插桩,再往下要写更多代码" | 那就是**工作项本身**,不是障碍 | 继续做(这通常是离定论最近的一步) |
| 把它**自己能做的工程步骤**包装成 A/B 让你裁决(例:把 rootfs 容器化让 systemd 当 PID 1、装依赖、换导入方式、等一个 15 分钟超时) | **工程选择不是"目标级裁决"** ✗;这类事的回答永远是"自己决定并做" | **自己拍板做掉**;写"可选、需用户裁决"= 误用 ✗ |

⇒ **该问的(才问你)**:①**收尾**(写 CLOSURE / 清理);②**释放运行载体/派生物/原件**(归档 / 不要了 / 腾空间);③**换目标 / 换方向**。
⇒ **不必问的(自己拍板做,别包装成 A/B)**:起容器(含 `--privileged` 做挂载/权限)、构建镜像、容器化 rootfs、装依赖、跑扫描/导入、重跑实验 —— 这些是**工程步骤** ✓(照容器与清理纪律做即可;成本/耗时不是理由,见上表)。
⇒ 合法停**只有四种**:本轮新成立 finding / 需工作区外**具体外部事物**(账号·样本·凭据·宿主环境 —— **不含真机**) / 队列清空 / 目标级裁决。

**停止声明**: 每次真正停下时写进 notes;**「有 finding」这条必须写明是本轮新成立的哪一条**(带本轮判定/证据);只引历史 finding ⇒ 视为**继续**,不得停。**理由必须附证据**(判据满足 ⇒ 引 `derived_check --closure` 输出;需用户输入 ⇒ 写四要素;工具/环境故障 ⇒ 附错误原文;「上下文余量」⇒ 引 harness 原句)。**格式与本条细则见 `LOOP.md`「停止声明」**。
- ⚠️ 本工作区没有真机 ⇒ 真机需求**不是**停的理由(见下条);只有"产品自身在容器/仿真里无法造出的外部事物"才算情况②。
⚠️ **本工作区没有真机/硬件**(用户明确声明): 硬件验证**不在可得资源内** ⇒ **容器/仿真/提取镜像的验证即为准绳**,
   不得因"需真机确认"而停工、降级 finding 或把它写成 Blocked;真机最多是"再加强"的可选项(报告里如实注明"physical-device
   validation was not performed" 即可),**模拟能做好就算做完**。

### 交付原则:最小充分(硬)
判据一条 —— **这一步产出的东西,是不是「缺陷本身」的直接事实?**
1. **事实 > 同原理补充**: 缺陷由能直接证明它的事实钉死(一次实测 / 一段反编译 / 一条重启后仍在的记录);同原理再次佐证 = 废话;
   判据 = **删掉它,读者对"缺陷成立"的把握会不会下降**。
2. **探测边界 = 缺陷本身 + 攻击链**: 不评估缺陷之外的后续影响链、不顺手审相邻组件;越界唯一理由 = **能串成攻击链**,且必须在 notes 写明这条链。
3. **同原理证据不重复给数据**: 一次运行 ⇄ 一张截图 ⇄ 一段文字,三者一致且不重复。

## 指针表(我需要 X → 去哪)

| 我需要 | 去哪 |
|---|---|
| 交付物形态(advisory 骨架 / 格式 / shots.json / CVE 线 / 材料完成判据) | `tools/templates/report.md`、`tools/templates/cve-request.md` |
| 流程细则(循环 / 清理 / 分诊 / 扫面提取 / 复核他人轮次) | `LOOP.md` |
| 研究判据与经验(建场 / 强方法两轴 / 形态判读 / 技术经验) | `knowledge/methodology.md` |
| 缺陷形态(认得出) / 已知假阳性(勿报) | `knowledge/vuln-patterns.md`、`knowledge/misreports.md` |
| 工具怪癖 / SDK 特征 / 已验证利用链 / 情报与实证 | `knowledge/{tool-quirks,sdk-bloodlines,exploits-validated,intel-log}.md` |
| 知识库登记册 | `knowledge/INDEX.md`;准入政策以本文件「Knowledge Base」为准 |
| 本轮事实(假设 / 已排除 / 下一步 / surface map) | `state/<目标>/notes.md` |
| 环境 / 工具入口 | 本文件「资源解析…」「环境清单」「工具速查」 |

## 资源解析与 Python 环境登记表(违反即返工)

### 系统包
- apt install <pkg>(本机为 **Kali rolling** 源;`sudo` 可用)
- 已装(2026-09-21): **`afl++` 5.02c**(含 `afl-fuzz`/`showmap`/`tmin`/`cmin`/`clang-fast`;**不含** qemu/frida 模式)、
  `libglib2.0-dev` `libpixman-1-dev` `ninja-build`(构建 afl++ qemu_mode 的依赖); **`lld`**(交叉编译垫片用:`clang --target=aarch64-linux-gnu -shared -nostdlib -fuse-ld=lld` ✓)系统自带 `qemu-system-x86_64`/`qemu-x86_64`
- 自建 fuzz 组件(无 apt 包 ⇒ 源码构建;产物**按架构分目录**,不都放 `/usr/local/bin/`):
  **`afl-qemu-trace`**(2,718,448 B;**只编了 x86_64**) + **`libqasan.so`**(81,984 B)。
  ⚠️ **跨架构未验证**:当时的冒烟(`afl-showmap -Q -- /bin/cat`,109 tuples)用的是 **x86_64** 二进制 ⇒ **aarch64 guest 不可用**
  (`Invalid ELF image for this architecture`)⇒ 本工作区绝大多数目标(ARM)的 fuzz 格**默认不可用**;先验:`afl-qemu-trace -h | head -2`。
  ✅ **已补(2026-09-29 实测)**:aarch64 版 trace 已构建并装到 **`~/.local/bin/afl-aarch64/afl-qemu-trace`**(4,661,184 B;
  `/usr/local/bin` 那份 x86_64 未动,两者并存)。冒烟:AFL_PATH 指向它 ⇒ `Using QEMU binary: <该路径>` + aarch64 `busybox cat` **168 tuples**、
  aarch64 `7z l` **2696 tuples**;不设 AFL_PATH(用 x86_64 那份)**0 tuples** ⇒ A/B 成立。
  ⚠️ **偏差**:aarch64 构建**未产出 `libqasan.so`**(脚本报 `Cross compiler aarch64-pc-linux-gnu-gcc could not be found`)
  ⇒ aarch64 上 **ASan 模式不可用**(不要设 `AFL_USE_QASAN`);上面 `{afl-qemu-trace,libqasan.so}` 的写法只对 x86_64 那份成立。
  **源码树已删**(`~/opt/AFLplusplus-stable/` 不存在 ✗)。**补装/换架构**(二选一):
  ① 自建(≈10 分钟;峰值 ~1.1 GB,编完删树只留 ~4.5 MB):重取 `codeload.github.com/AFLplusplus/AFLplusplus/tar.gz/refs/heads/stable`
     → **⚠️ 先编本体**(qemu 脚本依赖 `../afl-showmap`,否则报 `../afl-showmap not found`):`cd AFLplusplus-stable && make -j$(nproc) source-only`
     → `cd qemu_mode && PATH=$HOME/ENTER/envs/pwn/bin:$PATH CPU_TARGET=aarch64 ./build_qemu_support.sh`;
     **装法与多架构并存(实测过)**:AFL 找的是**写死文件名** `afl-qemu-trace` ⇒ **按架构分目录,用 `AFL_PATH` 选**
     (实测 `AFL_PATH=<dir>` 会顶替 PATH,它自己打印 `Using QEMU binary: <dir>/afl-qemu-trace`)——
     例:`~/.local/bin/afl-aarch64/afl-qemu-trace`(**aarch64 那份没有 `libqasan.so`** ✓)⇒ 用时 `AFL_PATH=$HOME/.local/bin/afl-aarch64 afl-fuzz -Q …`;
     `/usr/local/bin` 那份(x86_64)保持不动,两者并存 ✓(**别只放 `/usr/lib/afl/`**:实测**不生效** ⇒ 见 `tool-quirks.md`);
  ② 容器镜像 `aflplusplus/aflplusplus`(qemu 模式已构建 ✓ 零编译;用完删容器/镜像 ✓)。
  细节与判读见 `knowledge/tool-quirks.md`「`afl-qemu-trace` 可能只编译了单一架构」。
- **补丁版 qemu-user(解 netlink 白名单)**: `qemu-aarch64-patched`(49 MB 静态,装于 `~/.local/bin/`)—— 上游 qemu-user 对 netlink 协议是**硬编码白名单**
  (只放行 ROUTE/KOBJECT_UEVENT/AUDIT)⇒ `NETLINK_NETFILTER`(NFQUEUE 类 daemon)/`SOCK_DIAG`/`XFRM`/`GENERIC` 全被拒。
  **已装好,各轮直接用**:`qemu-aarch64-patched -L <rootfs> <guest_bin>`;判读探针在 `tools/qemu-nl-probe/`,
  `tools/check.sh` 有 INFO 行;**32 位/大端边界见 `knowledge/tool-quirks.md`**
- Windows 工具: 完整路径 /mnt/d/ida9.3/idat.exe、/mnt/c/Windows/System32/...(/mnt/c、/mnt/d 即 Windows 的 C:/D: 盘)
- 本机代理会劫持 localhost: 任何 curl 加 --noproxy '*';
  webfetch/fetch 工具同理且**无超时** —— 对 127.0.0.1 本地端点(如 IDA MCP)永远用
  curl --noproxy 或 ida_call.sh,用 webfetch 会无限挂死
- Windows 脚本经 WSL 执行注意 CRLF(必要时 dos2unix)
- 实验产物一律写 state/<target>/artifacts/(OpenCode 对工作区外路径默认**静默拒绝**;
  opencode.json 已设 external_directory=allow 放行 /mnt/d;新增工作区外路径访问时需在 permission 里登记)
- IDA 端点生命周期: 由会话壳 fw.sh 维护(打更循环保活,TUI 退出即回收) —— agent 无任何保活责任;
  裸 opencode(不经 fw.sh)时,端点在最后一次查询后 300s 自灭

### 登记维护义务(每次装包/建环境/卸载后执行)
- 新建环境 → 登记表加一行
- 新装/卸载重要工具 → 「环境清单」提取器表同步 ✓/✗ 状态
- 版本变更 → 修正对应行

## 循环守则

1. 信号判定: `tools/parse_signal.py` 覆盖的类别以其输出为准;不覆盖的类别(DoS/信息泄露/逻辑缺陷/权限等)用**自定义差分·对比实验**验证,同样构成发现 —— 禁止无证据宣称成功。
2. 每完成一个阶段 → 更新 `state/<目标>/notes.md`(当前假设 / 已排除项 / 下一步)。
3. 失败先分诊(**假设 / 环境 / 信号**三层),修正后重跑,禁止盲目重试(判据见 `LOOP.md`「失败分诊」)。
4. Windows IDA: `tools/ida_start.sh`(幂等)→ `tools/ida_call.sh` 查询 → `tools/ida_stop.sh` 停止。
5. 范围门控: 仅 PoC 级验证、仅授权目标;不做武器化、不做沙箱逃逸(探测/交付边界见下方「交付原则」)。
6. 前提验证: 任务/用户给的前提(如"已加密")同为待验证假设 —— 先指纹实证;证伪即改道,并把"前提修正"记入 `knowledge/tool-quirks.md`。
7. 工作区清理(收尾必做,**不等用户问**): **规则见 `LOOP.md`「清理与生命周期」(唯一所在)**;只有两档 —— **在使用中**基本不清理(仅清 `/tmp`、会话日志、运行残留),**用户宣布「归档/不要了」**后一次性释放 运行载体·派生物·原件(原件须满足"可重下"三条件);**只说"收尾"时运行载体照留**(复核/PoC 要用)⇒ 知识 / 对外材料(`report/`)/ 重下凭据永久保留(**`artifacts/` 是内部证据,不属永久保留** ⇒ 见 LOOP 两档表 ✓)。


8. **BLOCKER 纪律**: 一次失败、"打不了"都 ≠ BLOCKER;先穷尽获取与替代阶梯(**先在本地把前置状态造出来**),并逐级留证。
   BLOCKER 只阻塞对应子任务并**停下请求**(见「停 / 不停」②),**不终止整个循环**;收尾如实列出**没做的部分**。
9. **继续 vs 收尾**: 「继续 `<目标>`」= 按 notes 的待办(**假设池队列**)恢复,不重新立项 / 不重新提取;
   「收尾 `<目标>`」= 停止新实验 → 知识沉淀 → 报告 → CLOSURE;用户只说"继续"时按队列继续;**队列清空或全部显式降级**后才收尾。


10. **选面先于选技术(硬)**: 工作项只能来自 surface map 中 `disposition=未判定` 的面,且理由必须写在该面的**属性**里
    (可达性 / 输入可控性 / 未理解度);每轮**至少把一个面推到定论**(= 该面在论点—证据表里的条目**无缺口**)。
    **换面前先清零更接近定论的线索** —— 把"离定论最近"的那条留在身后去开新面,该轮**不算完成**,下一轮**必须先补它**。
    **排序**: 预认证 > 认证后/配置后;报文级输入 > 配置级;未理解 > 部分理解;复杂解析器/状态机 > 简单函数。
    **倾向(排序因子,不是禁令)**: 优先"**在常规(已配置/在用)状态下就能打完整链**"的面;做不到就把**窗口/影响当排序因子尽量扩大**。
    **上游 vs 自研(排序因子)**: 厂商自研/改写/胶水/权限映射层 ⇒ 优先;上游本体(vanilla/stripped/无源码)⇒ 只做版本-CVE 排除 + 审厂商接线,本体深挖=高成本备选。
    **厂商口径门(立项时查一次,结论记 notes §0 + `intel-log`)**: ①查厂商对**认证后**漏洞的态度(政策排除项 + 自家 CVE 先例);
    ②接受认证后 ⇒ 认证后面并入倾向但低一级;③明确排除 ⇒ 降为备查。
    **选面后先问"这个面最像哪一类缺陷"**(对照 `knowledge/vuln-patterns.md` / `misreports.md`),直接构造那一类形态去试;"changelog 说它变了"/"半成品"不是工作项。
11. **强方法 = 静态打底 + 按输入结构选格 × 有方向地探索输入空间**: **静态必做**(给位置与理解);**格由这一面解析的输入决定**——该面**解析外来数据**(参数/JSON/XML/二进制/协议)⇒ 默认格 = **变异/畸形输入**(`fuzz` 或等价的有界输入矩阵);无解析器(纯配置/授权位/启动门)⇒ 差分;密码学门 ⇒ 静态即可;**"等价 oracle"(直接跑目标自带工具)只在它真能覆盖该面的输入空间时才算一格** ✗;并在 notes 写明"静态做了什么 + 叠加了哪几格 + 主判据是谁";**"有界"的量级见 `LOOP.md`「机械化 oracle 的选择」** ✓ —— **跑完无异常也是证据**,不是白做 ✓;
    **读码 / 逐个手工试 = 弱方法**(可做,但不得作为唯一手段,也不得据此把面判"干净");失败记录写"用过哪些格、还没试过哪些";下一轮**换格**——但**若失败只是环境/信号层(格本身没问题)⇒ 修因重跑同一格,不算换格**。
    **选格要论证(硬)**: 每个面写明"选了哪格 + **依据**(面的哪些属性)+ **为什么不选别的格**";理由要**可反驳**(具体到卡在哪 / 代价从哪来),不能只写结论;**跳过任何一格都要留痕**(写理由或作为候选进池)—— 细则见 `LOOP.md`「机械化 oracle 的选择」。
    **判 No finding 前的三行(硬)**: 本面用的格 / 该格的最小证据 / 为什么这个组合够(含"没用其它格"的理由);缺任一行不许判 No finding。
    **对照能做就做,没有也继续挖**(缺对照只降结论强度);**否证要留门**: 被否证的形态写下"还能从哪条路到同一后果"并列下一工作项。两轴清单见 methodology「强方法两轴」。
12. **资源纪律**: 大规模/复杂算法(状态搜索、语料遍历、插桩与 fuzz、大文件)先定**内存/时间上限**,**流式优先**(禁整份读入),峰值 RSS 记进 notes。
12b. **扫描边界(硬)**: **禁**以 `/` 或 `/mnt/*` 为根的递归遍历(`find /`、`grep -R /`、`rg /`、`du /`、`tree /`、`ls -R /`)✗;
   搜索限定已知路径;工具/文件发现先走**低成本阶梯**(`command -v` → `dpkg -L` → `ls /usr/bin/<tool>*` → `find <已知目录> -maxdepth 4`);
   确需从根搜必须**先征得用户许可** + `-xdev` + 排除 `/mnt /proc /sys /dev /run`,**禁扫 Windows 盘**。**细则见 `LOOP.md`「文件搜索 / 扫描边界」**;harness 层由 `scan-guard` 扩展拦截。


13. **框架文件版本控制(硬)**: 本工作区是 git 仓库,但**只纳框架本体**(`AGENTS.md` / `LOOP.md` / `.pi/` / `tools/` / `knowledge/` / `prompts/`);
    `state/`、`intel/`、固件与工作副本**不进库**(可重下/可重建,非框架 ✓)。改框架文件**前后各 commit 一次**(改前 = 可回滚的基线 ✓),
    改完用 `git diff --stat` 确认只动了想动的地方 ✓;被证伪/误删的内容靠 `git checkout` 复原,**不要再靠人肉记**✗。
    (出处:2026-10-30 一次改写把「多会话纪律」整段吃掉、23 份 Timeline 删除无 diff 可回溯 —— 都是无版本控制的直接代价 ✓)

## 目录布局(各类产物的唯一合法落点)
- `intel/firmware/<目标>/` — **原始固件归档 = 可重下的缓存**(长期保留的是 `SOURCE_URL.txt`:URL+size+MD5);
  差分/旁证用的历史版本原始件也放这里。任何分析结论必须可从此处重新推导
- `intel/gpl_src/` · `intel/advisories/` · `intel/writeups/` — 参考资料库,长期保留
- `state/<目标>/` — 工作现场: rootfs 工作副本、artifacts/(**内部证据 = agent 自查用,不进对外材料**;**大体积派生物统一放 `artifacts/work/`** ⇒ 释放时整目录删 ✓)、notes.md、report/(advisory + INDEX + 复现包 + 截图);
  目标收尾按守则 7 清理
- `knowledge/` — 跨目标经验(按下方准入政策写入)
- `LOOP.md` — **流程细则**(循环 / 清理 / 分诊 / 步骤 / 复核);提示词只留边界与指针
- `tools/templates/` — **交付物模板**(报告/提交材料骨架):产出时**复制模板**再填值
- `/mnt/d/ida_test/` — Windows IDA 暂存区(会话退出自动清,白名单=管线工具)
- `/tmp` — 易失;禁止存放任何需要保留的文件

## Knowledge Base: 准入 / 归属 / 检索(政策本体,权威以此为准;登记册见 `knowledge/INDEX.md`)

**归属(新增文件或条目先过这张表)**
| 这条东西是什么 | 去哪 |
|---|---|
| 能跨固件复用的**缺陷形态** | `vuln-patterns.md` |
| 能跨固件复用的**勿报判据**(看着像漏洞但不成立) | `misreports.md` |
| 能跨固件复用的**做法/判据** | `methodology.md` |
| 工具/环境怪癖与解法 | `tool-quirks.md` |
| 已验证利用链 | `exploits-validated.md` |
| SDK/格式识别特征 | `sdk-bloodlines.md` |
| **改变过计划的新信息**(必带 `增量`) | `intel-log.md`(该事实已归位他处时,此表**只留时间线与 `增量` + 指针**) |
| **研究流程细则**(分诊/清理/步骤) | `LOOP.md` |
| **开发侧教训**(提示词工程/结构调整/复核方法) | **不写进任何研究 session 会读的文件**;留在开发会话内;**提示词常规不得自行改动**,只有**用户明确要求**时才改 |
| 某目标的实例/地址/证据 | `state/<目标>/artifacts/` |

**写入准入**: 只收**本次会话实际产生验证成本**的经验,且**重获成本 > 检索成本**;**KB 必须"即查即用"**(条目可用性不得依赖目标内文件 ⇒ 不写 `<目标>/artifacts/…` 之类路径,只写来源标签 `<目标名>` `<日期>`);禁止"顺便记录"、公开可检索的通识、项目细节;
**禁止把方法论/反例/工具坑就近塞进 `vuln-patterns.md`**。
**降级与退休**: 被证伪 → 标题加 `⚠️仅作核对,不作依据` + 正文写降级说明 + `INDEX.md` 状态列同步;归类不符 → **搬家**,不就地堆埋。
**检索义务**: 固件里出现可识别组件名 → 先 grep `knowledge/`(本地 0 命中且确有必要才外搜);检索返回 CVE/公告 → **只进排除清单**
(禁止据此生成"没修完整"类假设)。
**Provenance(假设必填)**: `[KB:<条目>]` / `[NET:<查询词>]` / `[RE]` —— 涉及可识别组件却标 `[RE]` 的假设**无效**。
**假设机制三通道(硬)**: **情报(只产排除项)/ 观察 / 假设**物理分开;观察→假设须写满**偏差陈述 + 可证伪判据**才入池;
验证是**双输出**(判定 + 本轮新观察,MISS 也必须写)。完整说明见 `methodology.md`。
**搜索命中率**: 连续 3 轮检索零增量 → 停止检索,回退实验层。

## 报告产出(硬性要求)

- **触发**: 有 finding / 到达范围门控 / 用户要求 —— 任一即产出。
- **产出时机**: `report/` 只在 ①有 finding ②收尾 时建立;跑循环期间的 A1/A2 活载体 = `state/<目标>/notes.md` 的 surface map
  (**结构化表**),收尾时**移动**(不是复制)为 `report/attack-surface.md`;留下旧副本 = 双源会漂。
- **落点**: `report/` = INDEX.md · attack-surface.md · CLOSURE.md · 每 finding 的 advisory(**顶层**)· `attachments/<描述性名称>/` · CVE 备料。
  **非发现不进 report/**;禁止临时自造板块(要新增类别,先在 `AGENTS.md` 定路径与归属)。
- **交付物规范一律在模板**(骨架 / 格式 / shots.json / CVE 编号线 / 通用原则 / 材料完成判据):
  产出时**复制** `tools/templates/report.md` 再填值 —— 不在提示词里重复。
- **自检**: 产出后跑 `bash tools/derived_check.sh`(图链 / 骨架 / 包完整 / 覆盖度),覆盖度与材料完成的**断言用它输出**(不手写)。

### 提交红线(硬性)

agent **可填不可提交** —— 仅当**用户明确要求**某份报告去提交时才走这条路;默认仍是"备料即终点":
- **可以**: 研究厂商口径(`intel/advisories/` + KB) → 用浏览器 MCP 打开厂商**提交表单 / 邮件草稿**并**逐字段填好**(标题/摘要/版本/复现/附件/PGP 加密 ✓);
- **不可以**: 点 "Submit"、点 "Send"、或任何让材料出门的动作 ✗ —— **最终提交一律留给用户**(唯一例外:该轮用户明确说"提交/发送/点提交" ✓);
- **登录**用**用户自己的浏览器会话**(见「环境清单 → 提交」的 `chrome-live` ✓);agent **不存、不代填密码** ✗;
- 填完必须贴**待确认清单**(表单 URL/收件人 · 每个字段最终值 · 附件清单与大小 · 是否已 PGP 加密 ✓),等用户核对后再收 ✓。
未要求提交时: 产出落在 report/ 即为终点 ✓。
## Exit Criteria(项目收尾判定标准)
循环的结束由本清单驱动,不由时间/步数/模型自行判断决定。
全部满足 → 输出 Closure Statement 后结束;无法满足 → 按 Stop Rules 处理。

### A. Attack Surface Coverage(攻击面覆盖)
- A1 Entry point enumeration: 列出 rootfs 内**全部**网络可达服务
  (依据: init 脚本、监听 socket 证据、二进制内网络字符串);
  对私有协议/自定义 IPC 须给出结构理解度(已理解/部分/未理解)并写入 surface map
  ⚠️ **间接/代理型入口也要枚举**: 形如 `www` 的 `remote` handler(proxy 到 graphs/apps/scep/…)
  与 `mproxy`/`wproxy`/`rproxy` 指向的后端守护进程 —— 它们同样是网络可达服务。
  ⚠️ **面必须带属性**(这是排序的唯一依据,也是“选面”的判据): `可达性`(预认证/认证后/LAN/L2/WAN)、
  `输入可控性`、`复杂度`、`理解度`、`disposition`。**`disposition=未判定` 的面 = 前沿(frontier) = 候选工作项**。
- A2 Entry disposition: 每个入口点必须给出三选一的 disposition,不允许未标注:
    Vulnerable(附 VR 报告) / No finding after triage(记录检测方法与范围) / Out of scope(注明原因)
  ⚠️ **什么不算“未覆盖面”**: “我还没审的”**补集**不算 —— 它回答的是“我还没看什么”,不是
  “攻击面里哪些没判定”。未覆盖面必须**逐条挂在 A1 枚举出的入口点上**;
  A2 表未给出的入口点不得声称“已覆盖”;“部分完成”必须写明缺哪一条入口点。
  **载体**: 跑循环期间 = `state/<目标>/notes.md` 的 surface map(**结构化表:入口点 × 属性(含 disposition)**);
  收尾时**移动**到 `report/attack-surface.md`。无论在哪,都不许是自由文本。
  ⚠️ **差分单独不改变 disposition**: 差分只覆盖厂商改过的点;一个面的结论若**全部来自差分**,该面**仍是未判定**
  —— 判“面干净”必须做与差分无关的审计(入口→sink 遍历 / 解析器边界 / 鉴权覆盖)。见 `knowledge/methodology.md`「差分的正确定位」。
  ⚠️ **派生量“算”不“写”**: 覆盖度 / knowledge 条数 / 材料完成 这类**能从权威处算出来**的值,
  一律**用 `bash tools/derived_check.sh` 的输出**(或只写指针),**散文里不写总结性断言**
  ("均已判定 / 全部覆盖 / 无一遗漏"之类)—— 手写派生量必漂(历史: §13 "C3 ✓"、§13 "修复完整"、§16 "均已判定")。
- A3 Same-family sampling: 同一框架/同一代码谱系的同类 handler,验证至少一个代表;其余标注 same-family,low priority。
    **"同类"必须给证据**(同一函数体 / 同一分派表 / 同一导入符号集合 / 同一模板生成 ✓),并写明"代表性覆盖了 N 个中的哪几个" ✓;
    **拿不出证据 ⇒ 逐条做** ✗ —— 不许用"同族"当停手理由 ✓;纵深要求见 `LOOP.md` 建模第 ⑦b(入口→参数→sink)

### B. Finding Validation(发现验证,逐条)
- B1 Static evidence: 函数/偏移/反编译片段 —— **负结论同样适用**: 意图性结论("设计内 / 不存在该边界 / 该功能没有")
  必须给到**代码路径级**依据(入口 handler → 关键判定点);只有行为观察时只能写"未见/未证伪"(依据层级见 `LOOP.md`)
- B2 Dynamic validation: 差分测试(benign vs payload)+ parse_signal.py 判定 HIT/MISS。
  验证必须在**目标版本(最新 stable)**上成立;仅存在于已被取代版本的缺陷
  (目标版已修复)不计入 B,按信息项记录
  ⚠️ **“修复完整 / 形态完整”这类结论必须带作用域**(哪个函数 / 哪个同族);
  **不得由单点差分推出组件级 disposition** —— 组件是否已清只能由 A1/A2 的逐点审计给出。
- B3 Exploitability assessment(与证据强度一致):
    arbitrary PC control / stack overflow with known offset / crash only (DoS) / info leak
- B4 Reachability: 网络可达路径确认,或注明 emulation limitation

### C. Reporting & Knowledge
- C1 每个发现一份 advisory(VR-<n>),结构完整自洽,不引用已删除的模板
- C2 report/INDEX.md 汇总
- C3 knowledge/ 同步 + 一键复现脚本验证通过

### D. Closure Statement(收尾声明,结束前必写)
- Closure reason: coverage complete / diminishing returns / user terminated / blocker
- Covered surface: <入口与 disposition 清单>
- Untested surface and reasons: <清单>
- Recommended next steps: <排序>

### Stop Rules
⚠️ 「有 finding ⇒ 停下汇报」里的「有 finding」一律指**本轮新成立**的;历史 finding(已备料/已进 `report/`)不构成停止理由 —— 见上面「停 / 不停」。
- A1+A2+B+C 全部满足 → **停并汇报 + 备好材料,等用户确认「收尾」** —— **Closure 必须由用户一句话确认**("收尾了"/"归档了" ✓);
  ⇒ **agent 不得自行宣布收尾** ✗(出处:2026-10-02 UDM 轮 —— 队列一清空它就自己宣布 `coverage complete`,用户并未要求收尾)
- 剩余入口均为 same-family(A3 代表已验证) → **停并汇报**(同上:等用户确认收尾 ✓)
- Blocker 两类(与「停 / 不停」②同一件事): **子任务阻塞** → 该子任务标 Out of scope(Blocked)并**停下请求**(一次列全需要的东西);
  **主路径阻塞** → **Hold 挂起**(非 Closure): 输出「BLOCKER 请求」节后结束本轮;
  用户补齐资源(或说"跳过")后,从挂起点继续 ⇒ 一律**不干等**
- 用户要求停止 → 立即以已收集证据生成报告
- ⚠️ **停止理由必须可指认**: 判据满足之外的停止,**必须点名具体的外部限制**(上下文余量 / 工具不可用 / 需用户输入),
  并写明「**未满足的 Exit Criteria** + **下一步第一条**」,不得含糊收场(正例: 2026-09-19 轮的 §10.7)。
  「预算 / 时间 / 步数」这类**无边界的理由不算** —— 写不出一条具体限制,就继续做。
- ⚠️ **引用任何 Closure reason 前,先逐条核对它的前置条件**(前置不满足就是“未完成”,不是“收尾”):
  `diminishing returns` 的前置是“**剩余入口均为 same-family(A3 代表已验证)**”;
  未做 A3 或剩余面非 same-family 时写它就是**误用** —— 正确处置是“该子任务标 Out of scope(Blocked),循环继续”。
  `coverage complete` 以 A1+A2+B+C 全满足为前提。

## 信号约定(常见类别举例,**非封闭枚举**)
- 命令注入: parse_signal.py 输出 HIT:shell_exec
- 内存破坏: HIT:crash(SIGSEGV/SIGILL/SIGBUS)
- Web 认证绕过: 未认证请求返回 200/管理内容
- V8 混淆: %HasSmiElements(bad)=true 且 bad[0] 仍为对象
**未覆盖类别(DoS/信息泄露/逻辑缺陷/权限问题等)同样是合法发现**,证据形态按性质
自行定义并如实记录(未认证可达性/差分行为/泄露对比);**信号未命中 ≠ 缺陷不存在**;
新类别首次验证后把判读方式补进本节


## 专项任务分派(harness 无关)

本 harness(pi)**无子代理、无 `task` 工具** ⇒ 三条内联步骤由主循环自行执行:**扫面 A / 失败分诊 B / 提取 C**。
步骤内容已并入 `LOOP.md`(「扫面 / 提取」与「失败分诊」)—— 不在提示词里重复。

## 环境清单(提取/分析资源,事实状态 2026-09-11)

### 加密/非标固件(提取产出贫瘠/整体高熵时)
目标: 拿到可分析的内容。技术面自行选择与组合,以下仅作覆盖参考(不限定、不排序):
- 判性: 熵分析、头部/尾部 hexdump、明文锚点定位(签名串/版本串/U-Boot 头)
- 公开解密知识: 以锚点串检索(主流厂商方案多已被公开破解,常有现成脚本)
- 设备侧: 真机 UART shell dd 解密分区;运行中系统内存/分区提取
- 更新器逆向: 固件内 updater/厂商升级工具中的解密例程(AES 常量、magic 比对、密钥编排)
- 弱加密: XOR 已知明文推 key / ECB / 固定或可预测 IV
- 非标封装: 魔数变体、自定义头部、分段/交错布局
纪律: 技术尝试与失败原因逐条记录;全部方向穷尽仍加密 → 挂起等待人工(请求真机或已解密固件)。

### 容器 / 模拟环境(事实状态 2026-09-27)
- **docker 29.1.3 可用**;容器补的是 **qemu-user 缺的那层**(真 `/dev`、真挂载、多进程;跑发行版镜像时才有真 init/systemd)⇒ **按面缺哪层补哪层**,不是"越真越好"(见 `knowledge/methodology.md`「模拟方式按"缺哪层补哪层"选」)。
- **用完必清**:容器 `docker rm -f <自己名字>` + 镜像 `docker rmi <自己构建的>`;**禁** `docker system prune -a`(本机有别的项目的镜像)。
- **挂载隔离**:只挂本目标目录;禁挂 `/`、`/home`、`/etc`、`/mnt/c`、`/mnt/d`;容器内脚本禁止对宿主路径 `rm -rf`。
- **user-mode 侧先用补丁版**: 解 netlink 白名单的 **`qemu-aarch64-patched`**(`~/.local/bin/`,环境清单已登记)⇒ NFQUEUE/`SOCK_DIAG`/`XFRM`/`GENERIC` 类 daemon 在 qemu-user 下也能跑,**别为这些面去起容器**;容器留给它真正独有的东西(真 `/dev`、真挂载、多进程 —— `--privileged` 只为 `CAP_SYS_ADMIN`)。判读探针 `tools/qemu-nl-probe/`。
- ⚠️ **`qemu-user` 的 `-L`/LD_PREFIX 只翻译"文件类系统调用";`execve` 不翻译** ⇒ guest 里 `system()`/`popen()`/`execve("/bin/sh")`
  跑的是**宿主**的程序与文件系统 ⇒ **"设备执行了某条命令 / 读到了设备上的文件"这类结论不能由 `qemu-user -L` 得出** ✗(只有"确实发起了一次 system/exec"可用 ✓)。
  要判**设备侧**行为 ⇒ 用能保证"**子进程 `exec` 也落在该 rootfs 内**"的方式即可 —— **chroot**(最省事:`unshare -r` 免 root,rootfs 内放宿主架构的**静态** qemu)、**容器**(docker/podman)、**`binfmt_misc`+`qemu-user-static`**(内核自动跑 aarch64,rootfs 里不必放 qemu)、**`proot`**(纯用户态、免 root 但慢)都是**等价路线**,按环境取用 ✓;判据/配方/检测口诀见 `knowledge/tool-quirks.md` ✓。
- **整机仿真(`qemu-system`)的适用范围**:只对**能整机启动**的目标(本工作区实测 = CHR x86_64);厂商固件多为非标 boot ⇒ 通常**启动不了**,别硬试,退回 qemu-user 或容器。具体配方见 `knowledge/tool-quirks.md`(docker/privileged 跑 daemon 那条)。

### 取件 / JS 页面(2026-10-27)
- **无头浏览器 MCP**:`chrome-devtools`(用户级 `~/.pi/agent/mcp.json`;`pi mcp list` ⇒ `connected, 32 tools`)。
  参数:`--headless --isolated --executablePath /usr/bin/chromium --proxyServer <本机代理:7892> --viewport 1280x720`。
- 边界:真 Chromium ⇒ 只碰公开页,不代填密码;流量走宿主代理(代理变了改 `mcp.json`);改配置后 `/reload`。

### 提交(仅当用户要求时;2026-10-30)
- 两条浏览器 MCP:①`chrome-devtools`(headless,自起 Chromium — 抓取)②`chrome-live`(`--autoConnect`,连**用户日常 Chrome**,带其登录态)。
- **先判收不收(读原文,不凭印象)**:到该项目的 scope / policy 原文核对,把**原文摘句**与"我们 finding 的对应事实"并列写进 notes;
  只有原文明确排除才判"不收"并给 CVE 路由;原文没写的项记「未知」⇒ **照常提交** ✓(禁凭类别臆断 ✗)。赏金与 CVE 独立 ✓ ——
  路由此见 KB「提交前先判「收不收」」(厂商是否 CNA → MITRE → 对口 CNA → 平台代申请)✓。
- **流程**:用户说"提交某份报告" ⇒ ①`python3 tools/chrome-live-probe.py` 探活 ②`ON` 才继续填 ③`OFF` **就地提醒用户**开
  `chrome://inspect/#remote-debugging`(唯一需要用户动手的一步),不要自己去点 ✗ ④填完贴待确认清单,**不点提交** ✗。
- `chrome-live` **默认常开(`enabled:true`)**:改 `enabled` 要 `/reload`,而 `/reload`/`/mcp` 只有**用户**能执行 ✗ ⇒ 不要让 agent 靠改配置开工 ✓。
  会话开始后才打开 Chrome 或服务连不上 ⇒ 让用户在 `/mcp` 里 **reconnect 一次** ✓。
- ⚠️ **WSL 必带 `--userDataDir`**:`--autoConnect` 默认找**本机(Linux)** profile(`~/.config/google-chrome/`)⇒ 指向 Windows 的
  `C:\Users\<u>\AppData\Local\Google\Chrome\User Data` 才认(实测:不带 ⇒ `Could not find DevToolsActivePort at /home/…` ✗)。
- **探活判据(已在 2026-10-30 验证走通 ✓)**:开关打开后 Chrome 在该 profile 写 `DevToolsActivePort`(首行=端口,本机 9222)⇒
  探针 `ON` 并列出真实标签页;开关未开 ⇒ 上述 `ENOENT`/`remote debugging is enabled` 报错 = `OFF`,提醒用户去开 ✓。
- **触发**:只认**明确指令** —— `/提交 <报告>` 或"提交 <报告名>" ✓;**没点名/匹配到多份 ⇒ 问一次** ✓;**禁止自行发起** ✗(会话里没有报告就直说没有 ✓)。
- **附件上传 = Windows 路径**:浏览器在 Windows 上跑 ⇒ `upload_file` / 表单附件只认 **Windows 路径**(Linux 路径无效 ✗)⇒ 用 `tools/win_stage.sh <文件>`
  把文件放到中转目录并打印 `D:\ida_test\submit\<名>`;该目录由 `tools/ida_clean.sh` 一并清理 ✓;邮件正文里的引用照旧写报告内路径 ✓。
- 边界:只用于用户要求提交的报告;只填不点提交;不存不代填密码;登录态不进对外材料、不复制出工作区。
- 多会话:①headless 天然隔离(每会话一进程 + `--isolated`),只费内存;②冲突只有一种 —— **两个会话连同一个可调试浏览器**(共用标签页)⇒ 同一时间只在一个会话 `enabled:true`;③`state/<目标>/` 单写者 ⇒ 一个目标只开一个会话。

### 提取工具
- binwalk: /usr/bin/binwalk( apt)
- unblob: /home/juryorca/venvs/unblob/bin/unblob(独立 venv 26.6.4)
  ★ 特殊说明:unblob 是 binwalk 的互补而非替代 —— 厂商魔改/非标打包固件 binwalk
    提不出时用它;用法 `unblob -e <输出目录> <固件>`;它依赖一批外部提取器,
    缺哪个对应格式就静默失败(见下表);两个工具的产出目录都要检查

### unblob 外部提取器可用性(缺=对应格式静默失败;按目标需要现装)
| 提取器 | 状态 | 覆盖格式 | 补装方式 |
|---|---|---|---|
| 7z/7za | ✓ | 多种压缩/归档 | - |
| debugfs | ✓(ext2/3/4) | ext 系列 | - |
| zstd | ✓ | zstd | - |
| sasquatch/sasquatch-v4be | ✓(2026-09-13 自建) | **厂商魔改 SquashFS(路由器最常见)** | ⚠️ `pip install sasquatch` 是 PyPI 同名无关包,**不可行**;改从 devttys0/sasquatch 源码构建(见 tool-quirks.md) |
| jefferson | ✗ | JFFS2(嵌入式常见) | unblob venv: pip install jefferson |
| ubireader_extract_files/images | ✗ | UBI/UBIFS(NAND 常见) | unblob venv: pip install ubi_reader |
| simg2img | ✗ | Android sparse | apt install simg2img |
| lz4 / lzop / lziprecover / unar | ✗ | 各压缩格式 | apt install lz4 lzop lziprecover unar |
| fsck.erofs | ✗ | EROFS | apt install erofs-utils |
| partclone.restore | ✗ | partclone 镜像 | apt install partclone |

规则:发现固件格式对应提取器缺失时,按"补装方式"安装后重跑;装完把状态改回 ✓。

## 工具速查
- 环境预检: bash tools/check.sh(开工必跑,有 FAIL 先报告)
- **知识库安全编辑**: `python3 tools/kb.py <check|list|add|append-line|downgrade|index-sync|snapshot|restore|selftest>` —— 禁止手写改 `knowledge/`;工具自带锁 / 原子写 / 改后自检 / 写坏自动回滚 ✓
- **批量静态/动态工具**: `tools/ghidra_scripts/{ProfileFuncs,DumpFuncsWithStr,DumpDecomp}.java`(函数画像/反编译批处理;Ghidra base = IDA base + 0x100000) + `tools/cgi_fuzz.sh`(对厂商 CGI 有界变异 fuzz,判据 = qemu `uncaught signal`/SIGSEGV;用法见文件头)→ 用法与坑见 `knowledge/methodology.md`「函数画像 × 动作表 join」
- **JS 页面取件**: `mcp__chrome-devtools__navigate_page` → `take_snapshot` / `list_network_requests` ⇒ 真实下载 URL(curl 拿不到时);见「环境清单 → 取件 / JS 页面」
- **提交(用户要求时)**: 先 `python3 tools/chrome-live-probe.py`(OFF ⇒ 提醒用户开 `chrome://inspect/#remote-debugging`)→ `mcp__chrome-live__*` 填表 → **不点提交** → 贴待确认清单;见「环境清单 → 提交」
- **netlink 面解锁**: `qemu-aarch64-patched`(已装,直接用)→ 判读探针 `tools/qemu-nl-probe/`
- **派生量重算**: `bash tools/derived_check.sh [目标]` —— 一次算出 knowledge 条数 / A2 覆盖度 /
  每个 finding 的包完整性;覆盖度与材料完成的**断言直接粘它的输出**(有 FAIL 非零退出)
- IDA(headless 多库并行): tools/ida_start.sh 启动 supervisor → 原生 MCP 工具
  idb_open 开库(二进制先 cp 到 /mnt/d/ida_test/<名>,idb_open 传 "D:\ida_test\<名>")
  → 查询显式带 database=<会话id>;工具清单用 ida_tools.sh;
  收尾 tools/ida_stop.sh 全停 + tools/ida_clean.sh 清暂存
- 终端截图(声明式,**不接受自由命令**): 先在 finding 包目录写好 `shots.json`,再
  终端截图走 `tools/termshot.sh`(**用法与约束见 `tools/templates/report.md`「截图」一节**),禁手绘、禁改用其他方式。
  脚本必须与 `shots.json` **同目录**(路径 containment)、可执行、argv 走白名单
  → 结构上无法被塞任意命令;`.pi/extensions/screenshot-guard.ts` 另在 `tool_call` 层拦旁路
  (直接驱动 tmux、用 convert/ffmpeg 造 png、write 直写 report/**.png)。禁手绘、禁改用其他方式。
