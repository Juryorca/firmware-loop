#!/usr/bin/env bash
# 用法: ida_start.sh [初始二进制的Linux路径]
# 启动 idalib-mcp supervisor(headless,端口 8745);幂等: 已在则直接返回
# 前置: idalib 已激活(python py-activate-idalib.py -d "D:\ida9.3",每环境一次)
set -u
STAGE=/mnt/d/ida_test
INIT="${1:-}"
code=$(curl -s --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8745/mcp" 2>/dev/null)
[ "$code" != "000" ] && { echo "supervisor already up ($code)"; exit 0; }
EXE=/mnt/d/python313/Scripts/idalib-mcp.exe

# 暂存: 把二进制拷进 D:\ida_test 可见的工作目录(同路径则跳过)
ARGS=()
if [ -n "$INIT" ]; then
  BASE="$(basename "$INIT")"
  [ "$(dirname "$INIT")" = "$STAGE" ] || cp -f "$INIT" "$STAGE/$BASE"
  ARGS=("D:\\ida_test\\$BASE")
fi

# 清孤儿 IDB(此刻 supervisor 未运行 = 无存活 worker,全部为上次会话残留;被锁的自动跳过)
rm -f "$STAGE"/*.id0 "$STAGE"/*.id1 "$STAGE"/*.id2 \
      "$STAGE"/*.nam "$STAGE"/*.til "$STAGE"/*.i64 "$STAGE"/*.idb \
      "$STAGE"/boot_*.py "$STAGE"/.port-* 2>/dev/null

setsid "$EXE" --host 127.0.0.1 --port 8745 "${ARGS[@]}" >/tmp/idalib-mcp.log 2>&1 < /dev/null &
for i in $(seq 1 60); do
  sleep 2
  code=$(curl -s --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8745/mcp" 2>/dev/null)
  [ "$code" != "000" ] && { echo "supervisor ready (8745)"; exit 0; }
done
echo "TIMEOUT: worker 可能未激活或二进制路径无效"; tail -8 /tmp/idalib-mcp.log 2>/dev/null
echo "若报 'worker exited early': 先激活 idalib —"
echo "  /mnt/d/python313/python.exe D:\\ida9.3\\idalib\\python\\py-activate-idalib.py -d D:\\ida9.3"
exit 1
