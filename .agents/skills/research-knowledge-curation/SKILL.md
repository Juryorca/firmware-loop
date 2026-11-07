---
name: research-knowledge-curation
description: 研究知识的使用与沉淀：何时检索 pattern/misreport/guideline、如何读取完整正文/版本、如何直接记录可复用经验。需要复用历史经验或收尾沉淀时加载。
---

# 研究知识使用与沉淀

## 适用场景

- 遇到相似漏洞形态，想知道以前怎么识别；
- 判断一个现象是否误报；
- 收尾时把可复用经验沉淀为知识条目。

## 检索

1. 先 `knowledge_search(query=..., kind=...)`，同时看正向 `pattern` 与反向 `misreport`；
2. 需要完整正文/版本时 `knowledge_get(entry_id=...)`；
3. 知识只作参考，不把历史目标名/地址当当前证据。

## 直接记录（无中间审核态）

- 先判断：这条经验能否跨目标复用？能否缩短未来分析路径/避免重复踩坑？
- 不能复用、只是一次性地址/复现细节、或网上低成本可查 → **不记录**；
- 能复用 → 直接调用 `knowledge_propose(kind=..., title=..., body_md=...)`，记录即为 `approved`；
- 必须写清形态、成立条件、反例边界、证据来源；
- 目标专属事实留在 SQLite research state，不写入知识正文。

## 生命周期

- 记录后若不再适用：`knowledge_deprecate`；
- 修改正文用 `knowledge_revise`，保留版本历史；
- `knowledge_stats` 用来看规模与 kind/status 分布；
- 不设 `draft`/`needs_review`/人工批准门。

## 验收

- 检索命中同时覆盖中英文/函数名/API 名；
- 正文完整、来源可追溯；
- 知识没有被当前目标证据冒充。
