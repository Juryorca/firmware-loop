#!/bin/bash
# cgi_fuzz.sh — 对提取出的厂商 CGI 做有界变异 fuzz(等价 oracle:qemu 直跑 + 崩溃判定)
# 用法: cgi_fuzz.sh <cgi二进制> <rootfs> [shim_lib_dir] [轮数]
# 判据: 退出码 >128(shell 信号)或 qemu 报 uncaught signal/SIGSEGV/SIGILL/SIGBUS ⇒ HIT:crash
set -u
CGI="${1:?usage: cgi_fuzz.sh <cgi> <rootfs> [shimdir] [rounds]}"; ROOTFS="${2:?}"; SHIM="${3:-}"; N="${4:-200}"
[ -f "$CGI" ] || { echo "no such cgi"; exit 2; }
fns=(list get_tree download stat trash_recovery share_file remote_folder qrpac upload search_ext)
payloads=('' '../../../../etc/passwd' '%2e%2e%2f' "$(python3 -c 'print("A"*5000)')" '%s%s%s%n' "'" '"' ';id;' '`id`' '$(id)' '..%00' '-x' '0x7fffffff' '2147483648' '/dev/zero')
crash=0
for i in $(seq 1 "$N"); do
  fn=${fns[$((RANDOM % ${#fns[@]}))]}
  p=${payloads[$((RANDOM % ${#payloads[@]}))]}
  key=(path file file_name name dir sharename ssids id uuid folder)
  k=${key[$((RANDOM % ${#key[@]}))]}
  qs="func=$fn&$k=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$p")"
  env LD_LIBRARY_PATH="$SHIM" REQUEST_METHOD=GET QUERY_STRING="$qs" SERVER_PORT=8080 HTTP_HOST=nas CONTENT_TYPE= CONTENT_LENGTH=0 \
      timeout 15 qemu-aarch64 -L "$ROOTFS" "$CGI" >/dev/null 2>/tmp/cgi_fuzz.err
  rc=$?
  if { [ $rc -ge 129 ] && [ $rc -le 165 ]; } || grep -qiE "uncaught target signal|Segmentation|Illegal instruction|Bus error" /tmp/cgi_fuzz.err; then
    echo "HIT:crash rc=$rc qs=$qs"; sed -n '1,2p' /tmp/cgi_fuzz.err; crash=$((crash+1))
  fi
done
echo "done: $N rounds, $crash crash(es)"
