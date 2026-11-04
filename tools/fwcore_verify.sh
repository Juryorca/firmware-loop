#!/usr/bin/env bash
# fwcore_verify.sh — 可外部重跑的最终验证(单命令,带硬超时,临时目录只在工作区内)
# 用法:  bash tools/fwcore_verify.sh              # 在仓库根执行
# 环境:  PYTHONPATH 由脚本设置;TMPDIR 不用(/tmp 不落数据)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2
export PYTHONPATH="$ROOT"
PY="${PY:-python3}"
WORK=".tmp/verify"
rm -rf "$WORK" && mkdir -p "$WORK/pytest"
fail=0
say() { printf '  %-56s %s\n' "$1" "$2"; }

# ① 受保护的相关测试(单次硬上限 300s)
if timeout 300 "$PY" -m pytest \
      tests/test_resource_guards.py tests/test_watchdog.py tests/test_mcp_client.py \
      tests/test_gates_db.py tests/test_pi_bridge.py tests/test_eval_scoring.py \
      tests/test_toolkit_structured.py tests/test_domain_core.py \
      tests/test_recovery_idempotency.py tests/test_providers.py \
      -q --basetemp="$WORK/pytest" > "$WORK/pytest.log" 2>&1; then
  say "pytest(8 个文件)" "PASS $(tail -1 "$WORK/pytest.log" | tr -d '\n')"
else
  say "pytest(8 个文件)" "FAIL"; tail -20 "$WORK/pytest.log" | sed 's/^/      /'; fail=1
fi

# ② 能力/Provider 探针(必须能跑,且输出含 arch_mismatch 之类状态)
if timeout 180 "$PY" -m fwcore capabilities > "$WORK/caps.json" 2>"$WORK/caps.err"; then
  n=$("$PY" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$WORK/caps.json" 2>/dev/null || echo "?")
  say "fw-core capabilities" "PASS(能力项 $n)"
else
  say "fw-core capabilities" "FAIL"; tail -5 "$WORK/caps.err" | sed 's/^/      /'; fail=1
fi

# ③ Gate(权威状态版)在合成目标上必须能算并如实报阻塞
timeout 60 "$PY" -m fwcore --root "$WORK/g" init verify --version t >/dev/null 2>&1
timeout 60 "$PY" -m fwcore --root "$WORK/g" hyp add verify "示例假设" >/dev/null 2>&1
if timeout 60 "$PY" -m fwcore --root "$WORK/g" gates verify > "$WORK/gates.json" 2>"$WORK/gates.err"; then
  say "fw-core gates(合成目标)" "PASS(无阻塞,可疑)"      # 有未收口假设时不该无阻塞
  fail=1
else
  if grep -q "blockers" "$WORK/gates.json" 2>/dev/null; then
    say "fw-core gates(合成目标)" "PASS(如实报阻塞)"
  else
    say "fw-core gates(合成目标)" "FAIL(没有 blockers 字段)"; fail=1
  fi
fi

# ④ 评测答案隔离:每个任务都要有 answers/,且 bundle/ 里不得出现答案文本
iso_fail=0
for d in eval/tasks/*/; do
  name=$(basename "$d")
  [ -f "$d/answers/expected.json" ] || { echo "      $name: 缺 answers/expected.json"; iso_fail=1; continue; }
  if grep -rqiE "control_verdict|expected_why|scope_must_mention" "$d/bundle" 2>/dev/null; then
    echo "      $name: bundle/ 里出现了答案字段(答案泄漏)"; iso_fail=1
  fi
done
# 权重:只看"答案字段"是否泄漏,不看内容相似
if [ "$iso_fail" -eq 0 ]; then say "评测答案隔离" "PASS(bundle 不含答案字段)"; else say "评测答案隔离" "FAIL"; fail=1; fi

# ⑤ 工作区必须干净(不留临时产物)
if [ -d "$WORK" ]; then rm -rf "$WORK"; fi
if git status --porcelain | grep -q .; then
  say "git status" "DIRTY(提交后再跑)"
else
  say "git status" "clean"
fi

[ "$fail" -eq 0 ] && echo "=== fwcore_verify: PASS ===" || echo "=== fwcore_verify: FAIL ==="
exit "$fail"
