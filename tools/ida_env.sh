#!/usr/bin/env bash
# 被 tools/ida_*.sh source 的公共前置:加载**唯一一份**配置(fwcore/winenv.py)。
# 环境变量(显式设置)优先;否则问 fw-core 要(它自己也按 环境>配置>默认 解析)。
# 这里不写死任何路径:配置缺失就**明确报错**,而不是默默用一个开发者的机器路径。
set -u
IDA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${FW_IDA_STAGE:-}" ]; then
  _cfg="$(cd "$IDA_ROOT" && PYTHONPATH="$IDA_ROOT" python3 -m fwcore ida-env --sh 2>/dev/null)" || _cfg=""
  if [ -z "$_cfg" ]; then
    echo "配置不可用: 无法运行 'fw-core ida-env --sh'(需要 python3 与 fwcore 仓库)" >&2
    echo "  也可以显式设置 FW_IDA_STAGE / FW_IDA_URL / FW_IDA_MCP_EXE 等环境变量" >&2
    exit 2
  fi
  eval "$_cfg"
fi

: "${FW_IDA_STAGE:?缺少 FW_IDA_STAGE(暂存目录,WSL 路径)}"
: "${FW_IDA_WIN_STAGE:?缺少 FW_IDA_WIN_STAGE(暂存目录,Windows 路径)}"
: "${FW_IDA_URL:?缺少 FW_IDA_URL(Supervisor 地址)}"

export IDA_MCP_URL="${IDA_MCP_URL:-$FW_IDA_URL}"
