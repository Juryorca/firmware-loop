import os
import sys
import threading
import time

sys.path.insert(0, r"C:\Users\30104\AppData\Roaming\Hex-Rays\IDA Pro\plugins")
from ida_mcp import MCP_SERVER, IdaMcpHttpRequestHandler

# 由 ida_start.sh 生成: __PORT__=本实例端口, __TAG__=实例标记(READY/BEAT/STOP 后缀)
PORT = __PORT__
TAG = "__TAG__"
LOG = r"D:\ida_test\boot" + TAG + ".log"
def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
_log("boot start: port=%d tag=%s" % (PORT, TAG))

# Start the HTTP endpoint in a background daemon thread; this call returns.
MCP_SERVER.serve("127.0.0.1", PORT, request_handler=IdaMcpHttpRequestHandler)
_log("server thread started on port %d" % PORT)

# Warm up on the IDA main thread *before* returning from the -S script.
# IMPORTANT: the -S script MUST return so IDA's idle loop keeps running and
# services idaapi.execute_sync() calls made by MCP tool handlers.
# (A blocking while-loop here deadlocks every @idasync tool.)
try:
    _log("auto_wait begin")
    import ida_auto
    ida_auto.auto_wait()
    _log("auto_wait done")
except Exception:
    pass
try:
    from ida_mcp.api_core import init_caches
    init_caches()
except Exception:
    pass
try:
    import ida_hexrays
    ida_hexrays.init_hexrays_plugin()
except Exception:
    pass

open(r"D:\ida_test\READY" + TAG, "w").write("ok")
open(r"D:\ida_test\BEAT" + TAG, "w").write("start")

IDLE_LIMIT = 300     # 300s no heartbeat -> exit
HARD_LIMIT = 14400   # 4h runaway cap(保活由 BEAT 驱动,正常不会触发)

def _watchdog():
    start = time.time()
    while time.time() - start < HARD_LIMIT:
        if os.path.exists(r"D:\ida_test\STOP" + TAG):
            break
        beat = os.path.getmtime(r"D:\ida_test\BEAT" + TAG) if os.path.exists(r"D:\ida_test\BEAT" + TAG) else start
        if time.time() - beat > IDLE_LIMIT:
            break
        time.sleep(2)
    try:
        import idaapi
        idaapi.qexit(0)
    except Exception:
        os._exit(0)

threading.Thread(target=_watchdog, daemon=True).start()
# Return control to IDA's idle loop (do NOT block here).
