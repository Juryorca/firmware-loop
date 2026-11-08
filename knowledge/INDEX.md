# Research Knowledge 索引

> **权威**: 可查询的研究知识在 SQLite `knowledge_entries`（`knowledge_search` / `knowledge_get`）。
> 本文件只是**静态参考与来源说明**，不常驻 System Prompt，也不定义知识政策。
> 知识准入/生命周期见 `AGENTS.md`「Knowledge Base」。

## 存储分层

| 位置 | 内容 | 运行时 |
|---|---|---|
| SQLite `knowledge_entries` | 研究知识 + 工具经验（`kind`/`status`/`tags`/`source`） | `knowledge_search` / `knowledge_get` |
| `methodology.md` | 方法论/强方法/建场等静态参考 | 按需 `fs_read` |
| `sdk-bloodlines.md` | SDK/格式识别静态参考 | 按需 `fs_read` |
| `exploits-validated.md` | 已验证利用链静态参考 | 按需 `fs_read` |
| `intel-log.md` | 情报时间线/增量静态参考 | 按需 `fs_read` |

旧 `vuln-patterns.md`、`misreports.md`、`tool-quirks.md` 已从当前工作树移除，只保留在 Git 历史中；
当前 Target 的分析不再读取它们。可复用的结论已经进入 SQLite；迁移记录见
`docs/tool-quirks-migration.md` 与 `docs/AGENT_ARCHITECTURE.md`。

## 维护

```bash
python3 -m fwcore knowledge stats
python3 -m fwcore knowledge search "认证" --kind pattern
python3 -m fwcore knowledge get KN-...
```

不再通过 Markdown 行数/INDEX 计数校验知识；权威统计用 `python3 -m fwcore knowledge stats`。
