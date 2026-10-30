#!/usr/bin/env bash
# 环境预检:每项一行 OK/FAIL;有 FAIL 时 agent 须停手报告
PY=/home/juryorca/ENTER/envs/pwn/bin/python
pass=0; fail=0
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."   # 自定位到项目根,脚本与 cwd 无关
ok(){ echo "OK   $1"; pass=$((pass+1)); }
bad(){ echo "FAIL $1"; fail=$((fail+1)); }
info(){ echo "---- $1"; }   # 非必需能力:只报状态,不计入 FAIL/停手判定
[ -x /mnt/d/ida9.3/idat.exe ] && ok "idat.exe(Windows IDA 无头)" || bad "idat.exe"
[ -f "/mnt/c/Users/30104/AppData/Roaming/Hex-Rays/IDA Pro/plugins/ida_mcp.py" ] && ok "ida_mcp 插件" || bad "ida_mcp 插件"
command -v binwalk >/dev/null && ok "binwalk" || bad "binwalk"
[ -x /usr/bin/qemu-arm ] && ok "qemu-arm" || bad "qemu-arm"
[ -x /usr/bin/qemu-mipsel ] && ok "qemu-mipsel" || bad "qemu-mipsel"
[ -x /usr/bin/strace ] && ok "strace" || bad "strace"
# qemu 模式 fuzz:可能按架构装了多份(afl 只认写死文件名 ⇒ 用 AFL_PATH 选)⇒ 逐份报它能跑的 guest
_pat=""
for _t in /usr/local/bin/afl-qemu-trace "$HOME"/.local/bin/afl-*/afl-qemu-trace; do
  [ -x "$_t" ] || continue
  _a=$("$_t" -h 2>&1 | head -1 | grep -oE 'qemu-[a-z0-9_]+' | head -1)
  info "afl-qemu-trace: $_t ⇒ 只能跑 ${_a:-?} guest"
  case "$_a" in *aarch64*) _pat="$_t";; esac
done
[ -z "$_pat" ] && info "无 aarch64 版 trace ⇒ ARM 目标 fuzz 不可用(补装见 AGENTS 环境清单)"
[ -n "$_pat" ] && info "aarch64 fuzz 用法: AFL_PATH=$(dirname "$_pat") afl-fuzz -Q …（不带 AFL_PATH 会静默 0 tuples）"
if command -v qemu-aarch64-patched >/dev/null; then
  info "qemu-aarch64-patched 已就绪(解 netlink 白名单,NFQUEUE/SOCK_DIAG 类 daemon 用;直接用,不需构建) @ $(command -v qemu-aarch64-patched)"
else
  info "qemu-aarch64-patched 未装(该能力当前不可用;详见 knowledge/tool-quirks.md)"
fi
$PY -c "import mcp,httpx" 2>/dev/null && ok "pwn: mcp+httpx" || bad "pwn: mcp+httpx"
$PY -c "import angr" 2>/dev/null && ok "pwn: angr" || bad "pwn: angr"
# 当前 harness = pi(fw.sh 调用它);opencode 仅遗留
command -v pi >/dev/null && ok "pi(当前 harness)" || bad "pi"
# DeepSeek 凭据源: pi 的 models.json 优先(当前 harness),回退 opencode.json(遗留)/ 环境变量
KEY=$($PY -c "
import json,os
def get(p,path):
    try:
        d=json.load(open(p))
        for k in path: d=d[k]
        return d
    except Exception:
        return None
print(get(os.path.expanduser('~/.pi/agent/models.json'),['providers','deepseek','apiKey'])
      or get('opencode.json',['provider','deepseek','options','apiKey'])
      or os.environ.get('DEEPSEEK_API_KEY',''))
" 2>/dev/null)
code=$(curl -s --noproxy '*' --max-time 8 -o /dev/null -w '%{http_code}' https://api.deepseek.com/models -H "Authorization: Bearer $KEY")
[ "$code" = "200" ] && ok "DeepSeek API" || bad "DeepSeek API(http $code)"
# 文档口径防回归:已废弃的措辞不许回来(2026-10-30 提交功能上线时清掉的)
_stale=$(grep -l "只备料不提交\|禁登任何账号" AGENTS.md LOOP.md .pi/prompts/fw.md tools/templates/report.md 2>/dev/null | tr '\n' ' ')
[ -z "$_stale" ] && ok "文档口径: 无废弃措辞" || bad "文档口径: 出现废弃措辞($_stale)"
# 提交面:探针可执行 + MCP 配置可解析(配置在用户级,读不到就跳过)
[ -x tools/chrome-live-probe.py ] && ok "提交探针可执行" || bad "提交探针缺失/不可执行 tools/chrome-live-probe.py"
$PY - <<'EOF' >/dev/null 2>&1 && ok "MCP 配置可解析" || info "MCP 配置缺失/不可解析(仅提示)"
import json,os
d=json.load(open(os.path.expanduser("~/.pi/agent/mcp.json"),encoding="utf-8"))
assert all("command" in v or "url" in v for v in d["mcpServers"].values())
EOF

# 报告不放 Timeline 节(2026-10-30:一行"发现并验证"= 零信息,用户明确否掉)
_tl=$(grep -rlE "^[[:space:]]*##[[:space:]]*(Timeline|时间线)[[:space:]]*$" state/*/report/*.md 2>/dev/null | tr '\n' ' ')
[ -z "$_tl" ] && ok "advisory 无 Timeline 节" || bad "advisory 仍有 Timeline: $_tl"

# 框架文件版本控制:有未提交改动就提示(不是错误,只是提醒 commit)
if [ -d .git ]; then
  _dirty=$(git status --porcelain -- AGENTS.md LOOP.md .pi tools knowledge prompts 2>/dev/null | wc -l | tr -d ' ')
  if [ "$_dirty" = "0" ]; then ok "框架改动已全部提交"; else
    info "框架有 $_dirty 处未提交改动(建议 commit;git diff 可核对)"; fi
else info "非 git 仓库(框架改动无法回滚)"; fi

echo "=== pass=$pass fail=$fail ==="
[ $fail -eq 0 ]
