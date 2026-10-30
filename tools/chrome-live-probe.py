#!/usr/bin/env python3
"""探活:用户的日常 Chrome 是否已允许远程调试(--autoConnect 能否真的接上)。
用法: python3 tools/chrome-live-probe.py [--timeout 60]
退出码: 0 = 能接上(可以开始填表) / 1 = 接不上(打不开开关) / 2 = 环境问题(包跑不起来)
不改任何配置:直接跟官方包走 stdio JSON-RPC,调一次 list_pages。
"""
import json, subprocess, sys, threading, time

def main():
    tmo = 60
    if "--timeout" in sys.argv:
        tmo = int(sys.argv[sys.argv.index("--timeout") + 1])
    # WSL 里 --autoConnect 默认找本机(Linux)profile ⇒ 必须指到 Windows 的 profile 目录
    import glob, os
    ud = None
    for c in glob.glob("/mnt/c/Users/*/AppData/Local/Google/Chrome/User Data"):
        ud = c; break
    cmd = ["npx", "-y", "chrome-devtools-mcp@latest", "--autoConnect", "--no-usage-statistics"]
    if ud:
        cmd += ["--userDataDir", ud]
        portf = os.path.join(ud, "DevToolsActivePort")
        print(f"PROBE 用 Windows profile: {ud}")
        print(f"      DevToolsActivePort: {'存在' if os.path.exists(portf) else '不存在(开关未开?)'}")
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1)
    except Exception as e:
        print(f"ENV 起不来: {e}"); return 2
    err = []
    threading.Thread(target=lambda: [err.append(l) for l in p.stderr], daemon=True).start()
    def send(o):
        p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
    def read_id(want, deadline):
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line: return None
            try: m = json.loads(line)
            except Exception: continue
            if m.get("id") == want: return m
        return None
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "probe", "version": "1"}}})
        if not read_id(1, time.time() + tmo):
            print("ENV 初始化无响应"); return 2
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "list_pages", "arguments": {}}})
        m = read_id(2, time.time() + tmo)
        if not m:
            print("ENV list_pages 无响应(超时)"); return 2
        res = m.get("result") or {}
        txt = json.dumps(res, ensure_ascii=False)
        if res.get("isError") or '"error"' in txt and "pages" not in txt:
            print("OFF 接不上浏览器 ⇒ 让用户开 chrome://inspect/#remote-debugging")
            print("   原始返回: " + txt[:300]); return 1
        print("ON  已接上 ⇒ 可以开始填表")
        print("   " + txt[:300]); return 0
    finally:
        try: p.kill()
        except Exception: pass

if __name__ == "__main__":
    sys.exit(main())
