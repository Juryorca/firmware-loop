# 报告交付物模板(advisory + 自含提交包)

> **用法**: 有 finding 时**复制本文件**为 `state/<目标>/report/<描述性名称>.md` 再填值;每 finding 一份 advisory
> + 一个自含的 `report/attachments/<描述性名称>/`(PoC + README + 截图 + `shots.json` + `.provenance`)。
> **本文件 = 对外交付物形态定义**(骨架/格式/通用原则/材料完成判据);规则的**判据**以 `AGENTS.md` 为准。
> `AGENTS.md` 只保留"何时产出 + 落点 + 提交红线"与指针 —— **不要在提示词里重复这些内容**。

触发: 发现得到结论(信号判定或其他证据形式)/ 到达范围门控 / 用户要求 —— 任一即必须产出。
输出: state/<target>/report/ 下**每个漏洞候选一份报告** + report/INDEX.md 索引。

**产出时机**: `report/` 只在 ①**有 finding**(advisory + attachments)或 ②**收尾**(INDEX/attack-surface/CLOSURE)时建立。
**跑循环期间不要预建 `report/`** —— A1/A2 维护在 `state/<目标>/notes.md` 的 surface map(**结构化表**),
> surface map 的收尾处理(移动而非复制)见 `AGENTS.md`「报告产出(硬性要求)」。

**落点(report/ 是*对外*目录,只放下表这几类;非发现不进 report/)**:

| 内容 | 落点 |
|---|---|
| 索引(目标元数据 + 发现表 + 复现材料 + 复现前提 + 关联公开漏洞) | `report/INDEX.md` |
| 入口点枚举与逐点处置(A1/A2/A3) | `report/attack-surface.md`(**收尾时从 notes 移入**) |
| 收尾声明 | `report/CLOSURE.md`(收尾时产出) |
| **每个 finding 的 advisory** | `report/<描述性名称>.md`(**顶层**,与 attachments/ 同级) |
| 该 finding 的自含提交包 | `report/attachments/<描述性名称>/`{PoC 脚本, README(怎么跑), 截图} |
| advisory 内引用的图片 | markdown 链接写 `attachments/<描述性名称>/xxx.png` |
| CVE 备料(仅厂商非 CNA 时) | `report/CVE-request-mitre.md` |
| **内部原样证据**(strace/差分中间产物/日志/运行记录) | `state/<目标>/artifacts/`,**不随包提交** |
| **非发现**(看过、判定不是漏洞的东西) | A2 disposition 占一行(写明检测方法/范围) + 可复用判据进 `knowledge/misreports.md` + 原始证据进 `artifacts/poc/` |

**材料完成判据(缺一即未完成;产出 finding 后必过一遍)**
1. 顶层 advisory 存在且骨架齐全(Summary/Tested Version/Vulnerability Type/Root Cause/Steps/Evidence/Impact/Remediation);
2. `attachments/<描述性名称>/` 自含:PoC 脚本 + README(怎么跑) + 截图;
3. **截图必须被 advisory 用 markdown 链接引用**(`attachments/<描述性名称>/<shot-id>.png`)
   —— **有动态验证却无图链 = 材料未完成**;无动态验证的 finding 要在 Evidence 里写明为什么没有“结果时刻”;
4. `shots.json` + `<shot-id>.png.provenance` 齐(截图可审计);
5. **本轮的经验已按 `AGENTS.md`「文件准入与归属」归位 `knowledge/`** —— **确无可沉淀要在 notes 里明说**,
   沉默不提 = 漏做(先沉淀再写报告)。
6. **「设备侧执行」类结论的证据要求(硬)**: 凡声称"设备**执行了**命令 / 改了设备文件 / 读到设备文件" ⇒ 证据必须来自
   **容器/chroot**(镜像根 = 该目标 rootfs),且是**设备文件系统内的可观测变化**(绝对路径落盘 / 配置改动 / 命令副作用);
   **不得**拿 `qemu-user -L` 跑出来的 marker(宿主 `/tmp`、相对路径、宿主 shell 的输出格式)当设备侧证据 ✗ ——
   `qemu-user` 的 `-L` 只翻译文件类系统调用、**`execve` 不翻译**,判据/配方见 `knowledge/tool-quirks.md`。
> Evidence 节的两个形态是**并列必填**:机制证据(文本原文)**且** 结果时刻(截图链接),
> **不是二选一**。(历史教训:2026-09-19 SCEP 轮把截图做了、带全 provenance,却**没在 advisory 里引用**:
> `report/**/*.md` 里 `![` 命中 0 —— 因为旧描述把两形态写成了“分类学”,读起来像可选。)

**禁止临时自造板块**:`report/` 下只允许出现上表列出的文件/目录。要新增一类产物,
**先在 AGENTS.md 里定路径与归属**,再落产物 —— 不许在产物里发明目录(历史教训:曾自建
`report/observations/`,把非发现塞进对外目录,且图片引用路径与规范不一致)。

要求: 专业、自然、结论带证据。不预设模板，按安全研究的正常结构组织内容。
> **写作前必查「论点—证据表」**(`LOOP.md`):每条事实/推断性断言(含**限定条件**)挂**最强既有证据**(可跨轮),
> 无证据则降级措辞;禁止"只在写作轮内找证据"。

受众纪律: 对外文件的写法红线**只认「### 报告格式与 CVE 编号线(细则)」的「通用原则」**(避免两处维护)。
过程性材料(notes/日志)留内部归档(INDEX/attack-surface/CLOSURE),不进对外文件;
内部归档反向要求必须带证据引用(命令/输出/文件路径/判定记号),保证可追溯可续跑。
证据形态: 机制证据用文本原文(命令+原样输出,自包含);结果时刻用真实终端截图
依赖一句带过)+ 自有证据截图),单份提交时材料自含 —— 一个 finding 的对照/证据**不得依赖
另一个 finding 的脚本或数据**。对外材料的命名与内容一律不出现内部 VR-n 编号 ——
advisory 标题/文件名、文件夹、PoC 脚本、截图全部用漏洞描述性命名;
VR 编号仅存在于内部 INDEX/notes;跨 finding 引用写 "documented in a separate advisory"。
截图须完整呈现终端内容(满幅,无黑边无裁切)。
红线: **默认不提交**;用户明确要求提交时「**可填不可提交**」(只填表单/草稿、不点提交、贴待确认清单)—— 细则见「### 提交红线(硬性)」。
报告格式与 CVE 编号线规则见下方「### 报告格式与 CVE 编号线(细则)」—— **该小节为唯一权威版本**,
与本节其他表述冲突时以细则为准。

### 报告格式与 CVE 编号线(细则)
> 报告规则已归入 `AGENTS.md` 与 `report-submission` Skill;旧 OpenCode 提示词已删除。

两条线分开: **报告格式跟厂商走,CVE 编号跟 CNA 资格走**。agent 只备料,提交动作永远归用户。

### 报告格式(按优先级)

1. 厂商有专属模板 → 用厂商的
   - 检索: "<vendor> security vulnerability report template" 或 "<vendor> PSIRT report format"
   - 常见位置: 厂商 PSIRT 页面、HackerOne 项目、安全公告附件
   - 例: TP-Link → security@tp-link.com + xlsx 模板(static.tp-link.com 下载)
   - 拿到模板后按模板字段填,不要自己发明节
2. 厂商无模板但有安全页面 → 用默认 advisory 骨架
   预置骨架(按需增删节,但必须有 Summary/Root Cause/Steps to Reproduce/Evidence):

  # <标题: 厂商 型号 组件 缺陷类型>
  ## Summary
  <一段话: 什么产品什么版本什么缺陷怎么触发后果>
  ## Tested Version
  <实测版本 + MD5;表后附一句 Other firmware versions were not tested.>
  ## Vulnerability Type
  <CWE 或文字>
  ## Root Cause
  <从入口到 sink 的数据流,附关键代码/反编译片段>
  ## Steps to Reproduce
  <按序号排列的具体操作,写动作不写分析;首段明示动态验证环境与真机未实测
  (如 Validated against the extracted firmware image under qemu;physical-device
  validation was not performed)>
  ## Evidence
  <机制证据 = 文本原文: 命令+原样输出,自包含可独立复核;**只放钉死缺陷的那一份**
   (同原理的重复佐证 = 废话,见「### 交付原则:最小充分」);
   结果时刻 = 真实终端截图(tools/termshot.sh 一键封装,已含本人 zsh 会话,禁止手工
   重绘),markdown 链接引用 report/attachments/ 图片,单份不重复;
   截图命令 = 直接运行提交包内的 PoC 脚本,禁止出现中间产物文件名);溢出/崩溃类注意: 崩溃阈值随启动环境栈布局漂移,
   差分/截图/PoC 必须同环境产出,报告写不变量+区间事实>

  **截图声明(`shots.json`,与 PoC 脚本、截图同目录)**:
  ```json
  {"shots":[{"id":"<shot-id>","why":"这张图要证什么","script":"./poc.sh",
             "argv":["arg1"],"sha256":"<可选: 绑定脚本版本>"}]}
  ```
  用法: `tools/termshot.sh <shots.json> <shot-id>` → 产出 `<shot-id>.png` + `<shot-id>.png.provenance`
  (含 command / script-sha256 / png-sha256 / 时间);advisory 里 markdown 引用 `attachments/<名称>/<shot-id>.png`。
  **拒收条件**: 缺 `why`、脚本越出包目录、不可执行、sha256 不符、argv 含元字符 —— 一律不拍。
  - **附件包必须自含**:**禁符号链接** ✗(指向包外/工作副本的链接,提交或转移后必坏);PoC 需要 rootfs 这类大件 ⇒
  参数化路径(默认 `./rootfs`)+ 在 README 写明"怎么准备"(从 `SOURCE_URL.txt` 重下并按其说明解包)✓。
- **README 必须能独立跑通**:依赖准备 / 一条命令 / 预期输出 / 清理 ✓;不确定就自己按 README 从零跑一遍再交 ✓。
## Impact
  <只写**已证实的后果**:攻击者实际能做到什么(1–2 句)+ 边界/未证实项。**不要**用 Confidentiality / Integrity / Availability 分类模板 ✗>
  （判据:找厂商要的是“后果”,不是 CIA 分类法;写不出已证实的后果就写“未证实”并降级措辞）
  ## Remediation
  <修复建议>


3. 什么都没有 → 自由文本
- 说清楚 什么产品什么版本什么缺陷怎么触发就够。

### CVE 编号线(独立于报告格式)
- 检索确认厂商是否为 CVE Numbering Authority: **以 cve.org 的 CNA 名单与厂商安全页的
  官方声明为准** —— 第三方(VulDB/ZDI/MITRE)代发过该厂商的 CVE 不能证明厂商不是 CNA
  (研究员走第三方渠道很常见,检索极易被带偏)。已知 CNA 例: TP-Link Systems、NETGEAR、ASUS
- 厂商是 CNA → 编号终点在厂商,**不得产出 MITRE 材料包**
- 厂商确认不是 CNA(例: D-Link、Linksys) → **有 finding 就与 advisory 同批产出机构提交材料**
  `CVE-request-mitre.md`: **复制本地模板** `tools/templates/cve-request.md` 后逐栏填值
  (各栏语义 / 短栏短值 / 成品文本 / 渠道事实**现查现填** / 唯一填写口径等要求都写在模板里);
  提交是用户自己的动作
- 厂商安全页的"可报 / 不要报"清单**只影响「提交」,不影响本地审计与产出**(本地照守则 5 的
  授权 + PoC 级做: 缺陷该记就记、报告该出就出)

### CLOSURE 的 `Recommended next steps` 必须含三块

1. **本目标剩余项**(若有):判据 + 需要什么资源;
2. **换目标建议**(候选 + 理由: 面属性(可达性/输入可控性/未理解度)、与我方能力匹配、赏金渠道)
   —— 目标切换是**用户动作**,但收敛/耗尽时必须给出建议;
3. 若判 `diminishing returns` / `coverage complete`,写出**支持该判定的证据层级**(L2/L3)。

### 待办与前提(需求式;**写进 INDEX**)

**不许**用"未有 X""仍需 Y""记为待办"这类**叙述式** —— 缺项必须可被下一轮直接接手(否则等于埋掉)。

### 提交红线(硬性)

### 通用原则
- 受众纪律: 对外文件(VR-*、CVE 申请材料)的读者是厂商/第三方,不是操作者。禁止出现
  工作区路径(state/…、artifacts/…)、内部工具名(tools/、parse_signal.py、IDA、tmux)、
  信号记号(HIT:/MISS:)与内部文件名(AGENTS.md、notes.md、knowledge/);
  文本证据不写操作者环境信息,截图用提交人本人真实终端(原生提示符,不匿名化)
- 内部归档(INDEX/attack-surface/CLOSURE/notes.md)反向要求: **必须带证据引用** ——
  每条结论附 命令/输出/文件路径/判定记号,保证可追溯、可续跑、可复核
- 证据自包含: 每条证据 = 可独立执行的命令 + 原样输出摘录;复现步骤不依赖本仓库文件,
  列附件只写附件名
- 定性与依据同段: 权限/可达性等关键声明紧随其推导依据(如"以 root 运行"附配置证据),
  仿真与真机的差异如实标注
- 验证边界: Steps 明示动态验证环境与"真机未实测";未实测环境禁止肯定式描述 ——
  写 "The equivalent request on the device is:",不写 "On a real device this is simply:"
- 版本表述: 节名用 Tested Version,不暗示验证过版本范围;表后附
  "Other firmware versions were not tested."
- CVSS: advisory 不主动打分;厂商表单要求时按实际可达性评(默认仅 LAN 可达时 AV 向量
  需说明),禁止惯性填 9.8/Critical
- 措辞风格: 像研究员写给同行 —— 直接说 输入在哪/怎么进 shell/什么后果;避免 monolithic、
  reach this sink、data context、break out of 这类静态分析论文腔;对照实验称
  Negative control;环境前提断言用 typically,不绝对化
- 修复建议与端点预期认证模型一致: login 等天然 pre-auth 端点不要求"加认证门",
  而是收敛 pre-auth action 面 + 禁止把请求输入传进 shell
- 代码块一律带语言标注(bash/json/http/c/text)
- 代码块可读性: 一行一句,行宽 ~80 列内(渲染不折行);长字符串在语法边界手工续行并缩进;
  反编译伪代码直接写代码体,地址锚点用行尾注释统一对齐(如 `// 0x417cd4`),
  禁止整块代码用 // 注释掉;日志/strace 摘录在语义边界续行或中段省略(…),
  保留判定关键段;JSON 请求体保持字节原样不换行
- 复现包按 finding 拆分: 每个漏洞候选一个文件夹(自有 PoC 脚本 + 简明使用说明(怎么跑即可)+
- **容器化复现**: 若复现依赖容器 ⇒ 包内必须有 `Dockerfile`(或 `docker-compose.yml`)+ 一行 build/run;README 写明"用完删除容器与镜像"(审阅者宿主机不留残留)。
  自有截图),单份提交自含 —— 本 finding 的对照/证据不得依赖其他 finding 的脚本;
  对外材料的命名与内容一律不出现内部 VR-n 编号 —— advisory 标题/文件名、文件夹、
  PoC 脚本、截图全用漏洞描述性命名,VR 编号只在内部 INDEX/notes 使用;
  跨 finding 引用写 "documented in a separate advisory";
  截图满幅呈现终端内容,无黑边无裁切;
- 报告语言: 英文(国际厂商) / 厂商官方语言(区域厂商)
- 内容来自 state/<target>/notes.md 与 artifacts/,不凭空编造

> **CLOSURE 必填:覆盖层声明** —— 写明本次覆盖判定到了哪几层:①监听端口 ②反向代理 location / 运行时生成路由表 ③应用内路由(含认证后)。
> 任何一层未做,必须写成"**已知遗漏**"并给出具体项(例:`/proxy/network/*` → 后端 REST 未枚举),**不得用 `coverage complete` 掩盖**。


> 需求式记录的**格式**见 `LOOP.md`「需求式记录(硬)」;`report/` 侧只需保证每条"没做"都写成需求式一行。
