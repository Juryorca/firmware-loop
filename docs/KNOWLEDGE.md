# Research Knowledge System

## 唯一存储

研究知识、工具经验和误报判据只存 SQLite：

```text
state/fwcore.sqlite3
  knowledge_entries          正文 / kind / status / source / tags
  knowledge_entry_versions   追加式版本历史
  knowledge_entry_tags       标签
  knowledge_entry_fts        FTS5 索引
```

运行时知识入口只有：

- `knowledge_search(query, kind=None, status=None, limit=...)`
- `knowledge_get(entry_id)`

写入与修订入口只有 Existing Knowledge API：

- `knowledge_propose`
- `knowledge_revise`
- `knowledge_deprecate`

不再有：

- `knowledge/*.md` 作为运行时或 fallback 来源
- Markdown → SQLite 双写
- SQLite → Markdown 自动导出
- 第二套知识索引

原 `knowledge/` 五份 Markdown 的内容已完成迁移并删除；原文只在 Git 历史中。

## 迁移与审计

逐文件、逐单元的映射、数量、跳过的历史项与理由见：

```text
docs/KNOWLEDGE_MIGRATION.md
```

迁移结果：

```text
原始 Markdown 知识单元: 132
新增 SQLite 条目:        61
合并/修订现有条目:       25
仅保留 Git 历史:         46
迁移后条目:              168
版本记录:                184
FTS 行:                  168
```

## 备份与恢复

不要直接复制正在写入的 WAL 数据库；使用 SQLite backup API。

备份：

```bash
python3 - <<'PY'
import sqlite3, time
src = sqlite3.connect("file:state/fwcore.sqlite3?mode=ro", uri=True)
dst = sqlite3.connect(f"state/fwcore-knowledge-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
```

恢复前先停止 TUI/所有写入进程，再把已验证备份恢复为 `state/fwcore.sqlite3`：

```bash
python3 - <<'PY'
import sqlite3
src = sqlite3.connect("state/fwcore-knowledge-YYYYMMDD-HHMMSS.sqlite3")
dst = sqlite3.connect("state/fwcore.sqlite3")
with dst:
    src.backup(dst)
dst.close(); src.close()
PY
```

恢复后至少检查：

```bash
python3 -m fwcore knowledge stats
python3 -m fwcore knowledge search guard
python3 -m fwcore knowledge get KN-c825317e690967
```

本次迁移的可用备份：

```text
state/knowledge-migration-backup-20261109-174400.sqlite3   # 迁移前，107 条
state/knowledge-migration-backup-20261109-174747.sqlite3   # 迁移前，107 条
state/knowledge-migration-final-20261109-175038.sqlite3    # 迁移后，168 条
```

恢复测试已验证：迁移前备份恢复为 107 条，迁移后备份恢复为 168 条；FTS、版本历史、`integrity_check=ok` 与外键检查均通过。

## 全新环境

`state/` 不进 Git，因此“本机 SQLite 有数据”不等于“重新部署后自动有数据”。全新部署必须：

1. 恢复一个经过验证的 SQLite 备份；或
2. 通过 `knowledge_propose` 从研究证据重新积累。

不存在 Markdown seed、运行时 Markdown fallback 或双写路径。
