---
name: firmware-validation
description: 固件漏洞验证：建场、仿真/容器、差分、畸形输入、oracle、证据复核与结论强度。需要把静态假设变成可复现实验时加载。
---

# 漏洞验证与证据

## 适用场景

- 已定位候选 sink/鉴权门，需要构造利用/触发；
- 需要选择 fuzz、差分、协议矩阵或等价 oracle；
- 需要把结果变成 evidence 并判断能否收口。

## 建场

1. 明确要补哪一层：qemu-user、chroot/容器、整机仿真或真机（本工作区无真机）；
2. 建 `pristine` 与预设状态，做差分；
3. 记录启动命令、依赖、失败原因；环境失败不是“无漏洞”。

## 选格

- 解析外来数据（参数/JSON/XML/二进制/协议）→ 变异/畸形输入矩阵；
- 纯配置/授权位/启动门 → 差分；
- 密码学门 → 静态审计 + 已知测试向量；
- 等价 oracle 只有在覆盖该面输入空间时才算一格。

## 证据

- benign vs payload 对照；捕获 stdout/stderr/退出码/文件/网络响应；
- 用 `state_add_evidence` 绑定工具调用与哈希；需要因果结论时 `state_verify_evidence` 用 reproduce-attempt/cross-check；
- 崩溃/DoS/信息泄露/逻辑缺陷都是合法类别，但强度按证据写。

## 边界

- 可复现 ≠ 漏洞；先排除设计内行为（查 `knowledge_search` 的 misreport）。
- 历史案例不能替代当前 target 的证据。
- 跑完无异常也是证据；不要伪造成功。

## 验收

- 对照实验可重跑；
- 证据有来源/哈希/复核；
- 结论带适用范围与强度。
