#!/usr/bin/env bash
# Ghidra headless wrapper:只做入口解析/脚本路径补齐/exec,不解释业务参数。
# 用法: ghidra_run.sh [analyzeHeadless 参数...]
#   ghidra_run.sh -help
#   ghidra_run.sh -import <binary> -postScript ProfileFuncs.java <outdir>
set -u
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
FIND=""
# 优先读本地工具元数据;未配置才允许环境变量/PATH 兜底。
# 配置损坏或配置了不存在的路径时明确失败,不静默换一个 Ghidra。
if [ -f "$ROOT/state/tools.json" ] || [ -n "${FW_TOOL_CONFIG:-}" ]; then
  _meta="$(cd "$ROOT" && python3 -m fwcore.toolmeta --path ghidra 2>&1)"
  _mrc=$?
  if [ "$_mrc" = "0" ]; then
    FIND="$_meta"
  elif [ "$_mrc" = "2" ]; then
    echo "GHIDRA_CONFIG_ERROR: $_meta" >&2
    exit 127
  elif [ "$_mrc" = "3" ]; then
    echo "GHIDRA_PATH_INVALID: $_meta" >&2
    exit 127
  fi
fi
for cand in "$FIND" "${GHIDRA_HOME:-}" "${GHIDRA_INSTALL_DIR:-}" \
            "$(command -v analyzeHeadless 2>/dev/null || true)"; do
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
