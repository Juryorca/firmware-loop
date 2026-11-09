#!/usr/bin/env bash
# 环境预检:每项一行 OK/FAIL;有 FAIL 时 agent 须停手报告
# 本脚本自己的检查用当前框架解释器;工具定位看 toolset/state/tools.json,不查 Python 环境队列
PY="${PYTHON:-python3}"
pass=0; fail=0
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."   # 自定位到项目根,脚本与 cwd 无关
ok(){ echo "OK   $1"; pass=$((pass+1)); }
bad(){ echo "FAIL $1"; fail=$((fail+1)); }
info(){ echo "---- $1"; }   # 非必需能力:只报状态,不计入 FAIL/停手判定
# 工具元数据:JSON 损坏必须显式失败,不能悄悄回退 PATH
if _meta_err=$($PY -m fwcore.toolmeta 2>&1 >/dev/null); then
  ok "工具元数据(state/tools.json)"
else
  bad "工具元数据: $_meta_err"
fi
# IDA 路径/暂存/挂载从 winenv 单一来源读取;不在检查脚本里写死盘符和用户名
if $PY - <<'EOF' >/dev/null 2>&1
from fwcore.winenv import load
raise SystemExit(1 if load(".").problems() else 0)
EOF
then ok "IDA 配置(winenv)"
else bad "IDA 配置(winenv;见 tools/ida_env.sh --help 或 state/ida.json)"; fi
command -v binwalk >/dev/null && ok "binwalk" || bad "binwalk"
command -v qemu-arm >/dev/null && ok "qemu-arm" || bad "qemu-arm"
command -v qemu-mipsel >/dev/null && ok "qemu-mipsel" || bad "qemu-mipsel"
command -v strace >/dev/null && ok "strace" || bad "strace"
# qemu 模式 fuzz:只认本地工具元数据;不再扫描 home 下的多套目录
_pat="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && python3 -m fwcore.toolmeta --path afl-aarch64 2>/dev/null || true)"
if [ -n "$_pat" ]; then
  _a=$("$_pat" -h 2>&1 | head -1 | grep -oE 'qemu-[a-z0-9_]+' | head -1)
  info "afl-qemu-trace: $_pat ⇒ 只能跑 ${_a:-?} guest"
  case "$_a" in *aarch64*) info "aarch64 fuzz 用法: AFL_PATH=$(dirname "$_pat") afl-fuzz -Q …（不带 AFL_PATH 会静默 0 tuples）";; *) info "该 tracer 不是 aarch64 ⇒ ARM 目标 fuzz 不可用";; esac
else
  info "未配置 afl-aarch64 元数据 ⇒ ARM 目标 fuzz 不可用(在 state/tools.json 配 path)"
fi
_patched="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && python3 -m fwcore.toolmeta --path qemu-aarch64-patched 2>/dev/null || true)"
if [ -n "$_patched" ]; then
  info "qemu-aarch64-patched 已就绪(解 netlink 白名单,NFQUEUE/SOCK_DIAG 类 daemon 用;直接用,不需构建) @ $_patched"
else
  info "qemu-aarch64-patched 未配置(该能力当前不可用;在 state/tools.json 配 path)"
fi
# 工具能力由 toolset/capabilities 统一报告;不在 check.sh 里用某个 Python 环境import 工具包
# 开发 harness 是外部工具(不在 fwcore 产品依赖里);这里只查 fwcore 自己的模型配置契约
# 凭据源: fwcore 的 state/model.json(api_key) → 环境变量(不再读其它 agent 运行时的配置)
KEY=$($PY -c "
import json,os
def get(p,path):
    try:
        d=json.load(open(p))
        for k in path: d=d[k]
        return d
    except Exception:
        return None
print(get('state/model.json',['api_key'])
      or os.environ.get('FW_MODEL_API_KEY','')
      or os.environ.get('DEEPSEEK_API_KEY',''))
" 2>/dev/null)
code=$(curl -s --noproxy '*' --max-time 8 -o /dev/null -w '%{http_code}' https://api.deepseek.com/models -H "Authorization: Bearer $KEY")
if [ "$code" = "200" ]; then ok "DeepSeek API"; else bad "DeepSeek API(http $code)"; fi
# 文档口径防回归:已废弃的措辞不许回来(2026-10-30 提交功能上线时清掉的)
_stale=$(grep -l "只备料不提交\|禁登任何账号" AGENTS.md LOOP.md tools/templates/report.md 2>/dev/null | tr '\n' ' ')
if [ -z "$_stale" ]; then ok "文档口径: 无废弃措辞"; else bad "文档口径: 出现废弃措辞($_stale)"; fi
# 提交面:探针可执行 + MCP 配置可解析(配置在用户级,读不到就跳过)
if [ -x tools/chrome-live-probe.py ]; then ok "提交探针可执行"; else bad "提交探针缺失/不可执行 tools/chrome-live-probe.py"; fi
if $PY - <<'EOF' >/dev/null 2>&1
from fwcore.mcp_config import discover_mcp_config, load_mcp_config
cfg = load_mcp_config(discover_mcp_config("."), base_dir=".")
assert cfg.servers and not cfg.errors
EOF
then ok "MCP 配置可解析"
else info "MCP 配置缺失/不可解析(仅提示)"; fi

# 报告不放 Timeline 节(2026-10-30:一行"发现并验证"= 零信息,用户明确否掉)
_tl=$(grep -rlE "^[[:space:]]*##[[:space:]]*(Timeline|时间线)[[:space:]]*$" state/*/report/*.md 2>/dev/null | tr '\n' ' ')
if [ -z "$_tl" ]; then ok "advisory 无 Timeline 节"; else bad "advisory 仍有 Timeline: $_tl"; fi

# 框架文件版本控制:有未提交改动就提示(不是错误,只是提醒 commit)
if [ -d .git ]; then
  _dirty=$(git status --porcelain -- AGENTS.md tools .agents fwcore/prompts 2>/dev/null | wc -l | tr -d ' ')
  if [ "$_dirty" = "0" ]; then ok "框架改动已全部提交"; else
    info "框架有 $_dirty 处未提交改动(建议 commit;git diff 可核对)"; fi
else info "非 git 仓库(框架改动无法回滚)"; fi

# 默认工具集摘要(复用 fwcore toolset,不替代上面的细项检查)
_toolset=$($PY -m fwcore toolset 2>/dev/null | head -1 || true)
if [ -n "$_toolset" ]; then info "默认工具集: $_toolset"; else info "默认工具集: 检查不可用"; fi

echo "=== pass=$pass fail=$fail ==="
[ $fail -eq 0 ]
