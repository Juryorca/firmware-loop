#!/usr/bin/env bash
# 用法: fw.sh [pi 透传参数] —— 固件工作区会话壳(pi)
# 生命周期: 会话退出 → 停 supervisor/workers + 清暂存区(headless 无需 BEAT 监控)
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."
bash tools/check.sh || echo "[fw] 预检有 FAIL,确认后继续"
mkdir -p state

cleanup() {
  bash tools/ida/ida_stop.sh 2>/dev/null
  bash tools/ida/ida_clean.sh 2>/dev/null
  echo "[fw] 会话结束,supervisor/workers 已回收,暂存区已清空"
}
trap cleanup EXIT

pi "$@"
