#!/usr/bin/env python3
"""syslog_sink.py <socket_path> — 在指定路径建一个 AF_UNIX DGRAM 接收器并打印收到的内容。
用途: 让 chroot 里的 guest daemon 能 connect("/dev/log")(很多 Synology 守护在拿不到
syslog 时会直接 exit(1))。纯用户态,不改系统、不装东西。
"""
import os
import socket
import sys

path = sys.argv[1]
try:
    os.unlink(path)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(path)
os.chmod(path, 0o666)
sys.stdout.write(f"[sink] listening on {path}\n")
sys.stdout.flush()
while True:
    data = s.recv(8192)
    sys.stdout.write(data.decode('utf-8', 'replace'))
    sys.stdout.flush()
