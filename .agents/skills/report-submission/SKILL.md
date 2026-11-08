---
name: report-submission
description: 报告、CVE 备料与厂商提交的操作流程：报告定位、scope/policy 判收、浏览器探活、只填不提交、附件中转与待确认清单。需要输出 advisory 或用户要求提交时加载。
---

# 报告与提交

## 适用场景

- finding 需要写 advisory、INDEX、复现包或 CVE 备料；
- 用户明确要求向厂商提交报告。

## 报告

1. 复制 `tools/templates/report.md`，按材料完成判据填；
2. 对外文件（advisory/CVE/attachments README）禁内部路径、工具名、`HIT:/MISS:`；内部归档要带证据引用；
3. 复现包自含：脚本、依赖、benign/payload、预期结果、证据；大件通过公开获取/参数化路径准备，内部重建配方写目标 `notes.md`；
4. 跑 `tools/derived_check.sh`，覆盖度/包完整性以脚本输出为准。

## 提交（仅用户明确要求）

1. 定位报告；找不到或多份先问，不猜。
2. 读厂商 scope/policy 原文，摘句对照 finding；明确排除 ⇒ 不提交，走 CVE 路由；未写 ⇒ 记未知，照常提交。
3. `python3 tools/chrome-live-probe.py` 探活；OFF 提醒用户开 `chrome://inspect/#remote-debugging` 并停。
4. 用 `chrome-live` MCP 逐字段填；附件先 `tools/win_stage.sh` 转 Windows 路径。
5. 贴待确认清单（URL/收件人、字段值、附件、PGP）；**不点 Submit/Send**，留给用户。

## 边界

- 不代填密码、不保存登录态、不自行提交；
- 没有浏览器 MCP 时如实记录未接线，不伪造提交；
- 厂商是 CNA 时不产 MITRE 件。

## 验收

- 报告结构完整、证据可回查；
- 提交清单齐全且未外发。
