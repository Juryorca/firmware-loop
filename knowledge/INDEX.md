# Research Knowledge 索引

> **权威**: 可查询的研究知识在 SQLite `knowledge_entries`（`knowledge_search` / `knowledge_get`）。
> 本文件只是**静态参考与来源索引**，不常驻 System Prompt，也不定义知识政策。
> 知识准入/生命周期见 `AGENTS.md`「Knowledge Base」与 `docs/AGENT_ARCHITECTURE.md`。

## 存储分层

| 位置 | 内容 | 运行时 |
|---|---|---|
| SQLite `knowledge_entries` | 跨目标可复用知识：形态、判据、排除条件、操作经验 | `knowledge_search` / `knowledge_get` |
| `knowledge/research-knowledge.md` | 8 条 curated 知识种子（从旧案例提炼） | 仅迁移源，不常驻 |
| `knowledge/vuln-patterns.md` | 旧正向案例原始来源（24 条） | 不常驻；可作历史核对 |
| `knowledge/misreports.md` | 旧误报案例原始来源（27 条） | 不常驻；可作历史核对 |
| `knowledge/methodology.md` | 方法论/强方法/建场等静态参考 | 按需 `fs_read` |
| `knowledge/tool-quirks.md` | 工具/环境怪癖静态参考 | 按需 `fs_read` |
| `knowledge/sdk-bloodlines.md` | SDK/格式识别静态参考 | 按需 `fs_read` |
| `knowledge/exploits-validated.md` | 已验证利用链静态参考 | 按需 `fs_read` |
| `knowledge/intel-log.md` | 情报时间线/增量静态参考 | 按需 `fs_read` |

## 知识原则

- 只收能缩短未来分析路径、避免重复踩坑、判断成立/排除条件的经验；
- 不收目标专属地址/一次性复现记录，也不收网上低成本可查的通用原理；
- 正文是完整自然知识；结构化字段只做 `kind/status/tags/source` 等最小检索；
- Agent 判断值得记录时，`knowledge_propose` 直接写入 `approved`；不设人工/中间审核态。

## 维护

```bash
python3 -m fwcore.knowledge stats
python3 -m fwcore.knowledge search "认证" --kind pattern
python3 -m fwcore.knowledge get KN-...
python3 -m fwcore.knowledge migrate
python3 -m fwcore.knowledge stats
```

旧案例正文仍保留在 `vuln-patterns.md` / `misreports.md`，但不再由 Runtime 常驻加载；迁移/提炼时不得丢掉其判据与反例边界。

## Legacy 源文件条数（供 derived_check 核对）

| 文件 | 收什么 | 条数 |
|---|---|---:|
| `vuln-patterns.md` | 旧正向案例原始来源 | 24 |
| `misreports.md` | 旧误报案例原始来源 | 27 |
