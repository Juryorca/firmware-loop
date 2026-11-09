---
name: firmware-reversing
description: 固件静态逆向与入口建模：rootfs 枚举、IDA/Ghidra、CGI/守护进程、入口→参数→sink、组件卡与证据登记。需要在二进制里定位解析器/鉴权门/命令执行点时加载。
---

# 固件逆向与入口建模

## 适用场景

- 已拿到 rootfs，需要枚举服务/入口/二进制；
- 需要从 handler 追到 sink、鉴权门或状态机；
- 需要把静态事实登记为 observation/evidence。

## 前置

- 已确认 target/version/arch；
- 工具可用性已查（IDA/Ghidra/objdump/qemu）；不可用要如实记录；
- 已有 surface map 或候选入口。

## 步骤

1. **枚举**：init/rcS、监听 socket、CGI、代理 location、封装应用；记录入口属性。
2. **组件卡**：信任边界、输入可控点、判定点、sink、已知保护、推翻条件、作者视角。
3. **定位**：先用字符串/xref/导入符号缩小；IDA/Ghidra 反编译；保留函数地址与调用链。
4. **追链**：入口 → 参数解析 → 校验/鉴权 → 数据流 → sink；不要只看单个函数。
5. **登记**：确认的地址/字符串/调用关系用 `state_add_observation`；有产物用 `state_add_evidence`。
6. **构造预测**：写出最可能的缺陷形态和验证 oracle，再进入 validation Skill。

## IDA headless wrapper

IDA 的安装/配置/命令/参数/生命周期/故障处理是工具套件的一部分，单一手册在
[`tools/ida/README.md`](../../../tools/ida/README.md)。本 Skill 只规定研究顺序：
需要 IDA 时先读该 README，再按“入口 → 参数 → 鉴权/校验 → sink”分析，不复制工具命令。

## 判据

- “存在危险函数”不等于漏洞；必须证明输入可控、路径可达、保护可绕过。
- 反编译不确定时写“未确认”，不要补造地址/类型。
- 历史案例只提供形态，不作为当前证据。

## 验收

- 入口→参数→sink 链有静态证据；
- 关键地址/函数/字符串可回查；
- observation/evidence 带来源（tool_call_id/path/hash）。
