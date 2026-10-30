#!/usr/bin/env bash
# 用法: ida_stop.sh —— 全停 idalib-mcp supervisor 与已注册 workers,清注册与标记
STAGE=/mnt/d/ida_test
INSTDIR="/mnt/c/Users/30104/AppData/Roaming/Hex-Rays/IDA Pro/mcp/instances"
PY=/home/juryorca/ENTER/envs/pwn/bin/python

taskkill.exe /IM idalib-mcp.exe /F >/dev/null 2>&1

# 注册表里登记的 worker(仅 backend=worker,不动手动 GUI 实例)逐个回收
for j in "$INSTDIR"/*.json; do
  [ -f "$j" ] || continue
  pid=$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('pid','') if d.get('backend')=='worker' else '')" "$j" 2>/dev/null)
  [ -n "$pid" ] && taskkill.exe /PID "$pid" /F >/dev/null 2>&1
  rm -f "$j"
done

rm -f "$STAGE/STOP"* "$STAGE/READY"* "$STAGE/BEAT"* \
      "$STAGE"/.port-* "$STAGE"/boot_*.py \
      "$STAGE"/*.i64 "$STAGE"/*.id0 "$STAGE"/*.id1 \
      "$STAGE"/*.id2 "$STAGE"/*.nam "$STAGE"/*.til \
      "$STAGE"/*.idb 2>/dev/null
echo "all stopped & cleaned"
