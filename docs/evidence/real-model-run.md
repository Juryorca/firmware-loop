# 真实模型端到端记录(自动生成)

- 模型: `(历史运行)` · 端点: `(历史运行)`
- 证据等级:**真实模型协议**(run/resume/advance 的多轮工具调用与状态更新)。
  这**不**等于真实逆向工具调用,也不等于真实固件漏洞研究质量。
- 数据来源:本次运行的 SQLite(工具调用/状态/事件),报告由它渲染。

## 阶段

| 阶段 | state | steps | rounds | ready | handback |
|---|---|---|---|---|---|
| run#1 | paused | 12 | 1 |  |  |

- history 通过 OpenAI 协议校验: 历史记录,未重验
- 事件序列(节选): run.started, surface.added, target.created, tool.called, tool.called, tool.called, tool.called, observation.added, tool.called, hypothesis.added, tool.called, attempt.started, run.finished, run.resumed, tool.called, tool.called, observation.added, tool.called, attempt.finished, attempt.observation_outcome, tool.called, round.ended, run.finished, tool.called

## 工具调用

- 总数 15,其中失败/非 ok 2

| tool | status |
|---|---|
| fs_hash | ok |
| fs_hash | ok |
| fs_read | ok |
| fs_read | ok |
| state_add_observation | ok |
| state_add_hypothesis | ok |
| state_start_attempt | ok |
| state_finish_attempt | error |
| state_add_observation | ok |
| state_finish_attempt | ok |
| fs_hash | ok |
| fs_hash | ok |
| state_add_observation | ok |
| state_start_attempt | ok |
| state_finish_attempt | error |

## 失败/异常调用

- state_finish_attempt: error — 
- state_finish_attempt: error — 

## 事件时间线(节选)

| ts | kind |
|---|---|
| 2026-11-05T05:33:25+00:00 | run.started |
| 2026-11-05T05:33:25+00:00 | surface.added |
| 2026-11-05T05:33:25+00:00 | target.created |
| 2026-11-05T05:33:26+00:00 | tool.called |
| 2026-11-05T05:33:26+00:00 | tool.called |
| 2026-11-05T05:33:26+00:00 | tool.called |
| 2026-11-05T05:33:26+00:00 | tool.called |
| 2026-11-05T05:33:29+00:00 | observation.added |
| 2026-11-05T05:33:29+00:00 | tool.called |
| 2026-11-05T05:33:31+00:00 | hypothesis.added |
| 2026-11-05T05:33:31+00:00 | tool.called |
| 2026-11-05T05:33:32+00:00 | attempt.started |
| 2026-11-05T05:33:32+00:00 | run.finished |
| 2026-11-05T05:33:32+00:00 | run.resumed |
| 2026-11-05T05:33:32+00:00 | tool.called |
| 2026-11-05T05:33:35+00:00 | tool.called |
| 2026-11-05T05:33:37+00:00 | observation.added |
| 2026-11-05T05:33:37+00:00 | tool.called |
| 2026-11-05T05:33:39+00:00 | attempt.finished |
| 2026-11-05T05:33:39+00:00 | attempt.observation_outcome |
| 2026-11-05T05:33:39+00:00 | tool.called |
| 2026-11-05T05:33:41+00:00 | round.ended |
| 2026-11-05T05:33:41+00:00 | run.finished |
| 2026-11-05T05:33:42+00:00 | tool.called |
| 2026-11-05T05:33:42+00:00 | tool.called |
| 2026-11-05T05:33:48+00:00 | observation.added |
| 2026-11-05T05:33:48+00:00 | tool.called |
| 2026-11-05T05:33:51+00:00 | attempt.started |
| 2026-11-05T05:33:51+00:00 | tool.called |
| 2026-11-05T05:33:53+00:00 | run.finished |
| 2026-11-05T05:33:53+00:00 | tool.called |

## 状态更新

- 假设 1 条
  - H-20261105-d0a89ba4 b.txt 与 a.txt 内容差异 [investigating]
- 尝试 2 条
  - A-20261105-2ae92d95 result=inconclusive note=已拿到哈希:a.txt sha256=6788f6f0e644487ec21aa76ace6c52938bcd2061393d331f050fc86f96e01
  - A-20261105-bd9cf5ff result=running note=
- 观察 3 条
  - fs_hash 计算结果(工作区根目录,version 1.0):a.txt sha256=6788f6f0e644487ec21aa76ace6c52938bcd2061393d331f050fc86f96e017ff (8 bytes)
  - 尝试 A-20261105-2ae92d95(fs_hash)输出:a.txt sha256=6788f6f0e644487ec21aa76ace6c52938bcd2061393d331f050fc86f96e017ff (8 bytes
  - 对目标 e2e@1.0 用 fs_hash 计算得到: a.txt sha256=6788f6f0e644487ec21aa76ace6c52938bcd2061393d331f050fc86f96e017ff (8 bytes); b.t
- 证据 0 条(已复核 0),复核记录 0 条

## 结局

- run: state=paused step=12 rounds=1 handback=-
