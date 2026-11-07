#!/usr/bin/env bash
# Ghidra headless wrapper:只做入口解析/脚本路径补齐/exec,不解释业务参数。
# 用法: ghidra_run.sh [analyzeHeadless 参数...]
#   ghidra_run.sh -help
#   ghidra_run.sh -import <binary> -postScript ProfileFuncs.java <outdir>
set -u
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
FIND=""
for cand in "${GHIDRA_HOME:-}" "${GHIDRA_INSTALL_DIR:-}" \
            "$HOME"/tools/ghidra_*/support/analyzeHeadless \
            /opt/ghidra*/support/analyzeHeadless; do
  [ -n "$cand" ] || continue
  if [ -x "$cand" ]; then FIND="$cand"; break; fi
done
if [ -z "$FIND" ]; then
  echo "GHIDRA_MISSING: 未找到 analyzeHeadless(设 GHIDRA_HOME 或安装 Ghidra)" >&2
  exit 127
fi
args=("$@")
# -postScript 使用脚本名时,补仓库脚本目录;调用者显式给 -scriptPath 时不覆盖
has_script=0
has_path=0
for a in "${args[@]}"; do
  [ "$a" = "-postScript" ] && has_script=1
  [ "$a" = "-scriptPath" ] && has_path=1
done
if [ "$has_script" = "1" ] && [ "$has_path" = "0" ]; then
  args+=("-scriptPath" "$ROOT/tools/ghidra_scripts")
fi
exec "$FIND" "${args[@]}"
