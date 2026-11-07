#!/usr/bin/env bash
# termshot.sh — 真实终端截图(声明式;不接受自由命令)
#
# 用法: tools/termshot.sh <shots.json> <shot-id>
#   约定: shots.json、PoC 脚本、输出截图同目录(即 report/attachments/<描述性名称>/)
#   输出: <同目录>/<shot-id>.png  +  <同目录>/<shot-id>.png.provenance
#
# 为什么这样设计(与 AGENTS.md「报告产出 → 落点」一致):
#   * 规范要求「截图命令 = 直接运行提交包内的 PoC 脚本,禁止出现中间产物文件名」;
#   * 因此本工具**不再接受命令串** —— 只能跑 manifest 里已登记、且通过校验的脚本;
#   * 脚本必须与 manifest 同目录(路径 containment)、是常规可执行文件;
#     manifest 记了 sha256 则必须一致(截图绑定到确定版本);
#   * argv 逐项白名单、脚本名白名单 → 结构上不存在"往终端里塞命令"的面;
#   * 缺 why(这张图要证什么)直接拒收;
#   * 收尾写 .provenance(脚本路径+sha256、argv、png sha256、时间),截图从"信任"变"可审计"。
set -u

PY="${FW_PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MAN="${1:-}"; ID="${2:-}"
if [ -z "$MAN" ] || [ -z "$ID" ]; then
  echo "用法: tools/termshot.sh <shots.json> <shot-id>" >&2
  echo "  例: tools/termshot.sh report/attachments/my-finding/shots.json my-shot" >&2
  exit 2
fi
[ -f "$MAN" ] || { echo "no such manifest: $MAN" >&2; exit 2; }

MDIR="$(cd "$(dirname "$MAN")" && pwd)"
case "$MDIR" in
  */report/attachments/*) : ;;
  *) echo "REFUSED: manifest 必须在 .../report/attachments/<描述性名称>/ 下(当前 $MDIR)" >&2; exit 3 ;;
esac

# 校验 + 拼命令,全部交给登记解释器;bash 只消费结果(TAB 分隔: CMD / SHA / WHY)
RES="$("$PY" - "$MAN" "$ID" "$MDIR" <<'PYEOF'
import hashlib, json, os, re, shlex, sys
man, sid, mdir = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    doc = json.load(open(man, encoding="utf-8"))
except Exception as e:
    sys.exit("manifest 不是合法 JSON: %s" % e)
shots = {s.get("id"): s for s in doc.get("shots", [])}
s = shots.get(sid)
if not s:
    sys.exit("REFUSED: manifest 里没有 id=%r(现有: %s)" % (sid, ", ".join(map(str, shots)) or "无"))
why = (s.get("why") or "").strip()
if not why:
    sys.exit("REFUSED: id=%r 缺 why(这张图要证什么)" % sid)
script = (s.get("script") or "").strip()
rel = script[2:] if script.startswith("./") else script
if (not re.fullmatch(r"[A-Za-z0-9._-][A-Za-z0-9._/-]*", rel)
        or script.startswith("/") or ".." in rel.split("/") or "\\" in script):
    sys.exit("REFUSED: script 不是合法的包内相对路径: %r" % script)
path = os.path.realpath(os.path.join(mdir, rel))
if not path.startswith(mdir + os.sep):
    sys.exit("REFUSED: script 越出包目录: %s" % path)
if not os.path.isfile(path):
    sys.exit("REFUSED: script 不是常规文件: %s" % path)
if not os.access(path, os.X_OK):
    sys.exit("REFUSED: script 不可执行: %s" % path)
sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
want = (s.get("sha256") or "").lower()
if want and want != sha:
    sys.exit("REFUSED: script sha256 不匹配(manifest=%s 实际=%s)" % (want, sha))
argv = s.get("argv") or []
for a in argv:
    if not isinstance(a, str) or not re.fullmatch(r"[A-Za-z0-9._=:@/+,-]*", a):
        sys.exit("REFUSED: argv 含不允许的字符: %r" % (a,))
cmd = " ".join(["./" + rel] + [shlex.quote(a) for a in argv])
print("\t".join([cmd, sha, why]))
PYEOF
)" || { echo "$RES" >&2; exit 3; }
IFS=$'\t' read -r CMD SHA WHY <<<"$RES"

OUT="$MDIR/$ID.png"
PROV="$OUT.provenance"
SESSION="termshot"
W="${TERMSHOT_COLS:-120}"; H="${TERMSHOT_ROWS:-45}"

tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x "$W" -y "$H" -c "$MDIR" zsh
sleep 0.8
tmux send-keys -t "$SESSION" "clear" Enter
sleep 0.5
tmux send-keys -t "$SESSION" "$CMD" Enter
WAIT="${TERMSHOT_WAIT_SEC:-60}"
for _ in $(seq 1 $((WAIT * 2))); do
  sleep 0.5
  [ "$(tmux display-message -p -t "$SESSION" '#{pane_current_command}')" = "zsh" ] && break
done
sleep 0.6
ANSI="$(mktemp /tmp/termshot-XXXXXX.ansi)"
tmux capture-pane -t "$SESSION" -e -p > "$ANSI"
tmux kill-session -t "$SESSION" 2>/dev/null
"$PY" "$HERE/ansi2png.py" "$ANSI" "$OUT"
rm -f "$ANSI"

"$PY" - "$OUT" "$PROV" "$MAN" "$ID" "$WHY" "$SHA" "$CMD" <<'PYEOF'
import hashlib, os, sys, time
out, prov, man, sid, why, sha, cmd = sys.argv[1:8]
png_sha = hashlib.sha256(open(out, "rb").read()).hexdigest() if os.path.exists(out) else "(missing)"
open(prov, "w", encoding="utf-8").write(
    "shot-id: %s\nwhy: %s\ncommand: %s\nscript-sha256: %s\npng: %s\npng-sha256: %s\ntime: %s\nmanifest: %s\n"
    % (sid, why, cmd, sha, os.path.basename(out), png_sha,
       time.strftime("%Y-%m-%dT%H:%M:%S%z"), man))
PYEOF
echo "[+] $OUT"
echo "[+] $PROV"
