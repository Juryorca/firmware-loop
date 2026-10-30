# qemu-nl-probe —— 「user-mode 仿真能不能跑通 netlink 面」的判读探针

工作区级工具,**跨目标复用**。本目录只有**判读用的探针**(小体积),不含构建配方。

## 工具在哪(已装好,直接用)
`qemu-aarch64-patched` —— 静态 49 MB,位于 `~/.local/bin/`;登记在 `AGENTS.md`「环境清单」,状态见 `tools/check.sh` 的 INFO 行。
```bash
qemu-aarch64-patched -L <rootfs> <guest_bin>                    # 跑 guest 二进制 / 目标 daemon
qemu-aarch64-patched tools/qemu-nl-probe/bin/nltest2.aarch64    # 看本机 netlink 各协议放行情况
```
> 它是**机器上的一份现成能力**,不谈构建;`state/<目标>/` 里若出现它的副本,那是**副本、随时可删**。

## 为什么需要它(机制,判据)
上游 qemu-user 对 netlink 协议是**硬编码白名单**(`linux-user/syscall.c::do_socket()`):只放行
`NETLINK_ROUTE`/`KOBJECT_UEVENT`/`AUDIT`,其余(含 **`NETLINK_NETFILTER`**)直接 `-EPROTONOSUPPORT(-93)`;
紧随的 `switch(protocol)` 还有 `default: g_assert_not_reached();`。
⇒ 走 NFQUEUE 的厂商 daemon(`resolvsrmd`、`synosafeaccessfilterd`、autoblock …)在 user-mode 仿真下**起不来**
(日志形如 `Failed on nfq_open()`);`LD_PRELOAD` shim 也绕不过(它自身仍要 `socket()`)。
`qemu-aarch64-patched` = 去掉那两处守卫的版本(行为差异由下面探针给出对照)。

> 通用教训:**凡在 qemu 翻译过的进程里拿到的 errno(ENOPROTOOPT/EPROTONOSUPPORT/EINVAL),不得直接归因给内核** ——
> 先怀疑 qemu 的 syscall 层;判"内核不支持"必须用**原生**程序测同一 syscall。

## 探针清单
| 文件 | 是什么 | 怎么用 |
|---|---|---|
| `nltest2.c` / `bin/nltest2.aarch64`(2.8 KB 静态) | aarch64 纯 raw syscall,枚举 `(domain,type,protocol)` 返回码(正数=fd,负数=-errno) | 用 `qemu-aarch64-patched` 跑它 |
| `nltest.c` / `bin/nltest.aarch64` | 同上,精简单点探针 | 同 |
| `nfq_probe.c` / `bin/nfq_probe.x86_64` | **原生 x86_64** 能力探针:建 `NETLINK_NETFILTER` + bind 队列(不需 libnetfilter_queue) | 直接 `./nfq_probe.x86_64` |
| `syslog_sink.py <path>` | 在指定路径起 AF_UNIX DGRAM 接收器,让 chroot 里的 daemon 能 `connect("/dev/log")` | `python3 tools/qemu-nl-probe/syslog_sink.py <rootfs>/dev/log &` |

**判读**:同一条调用,原版 `/usr/bin/qemu-aarch64` = **-93**,`qemu-aarch64-patched` = **正数 fd** ⇒ 对照成立。
(实测:原版 `-93` / 补丁版 `fd=7`;真 daemon `resolvsrmd` 在补丁版下 `socket/bind/CMD_BIND/PARAMS` 全 ACK error=0 后进入收包循环。)

## 跑目标 daemon 的骨架(一次性 netns,不碰真实网络)
```bash
unshare -Urn sh -c '                      # 非 root 也能拿到 CAP_NET_ADMIN / CAP_SYS_CHROOT
  ip link set lo up
  # 例:Synology SRM 的 resolvsrmd 队列号 = 658(见 <rootfs>/etc/init/resolvsrmd.conf)
  iptables -t mangle -A OUTPUT -p udp --sport 5440 -j NFQUEUE --queue-num 658 --queue-bypass
  python3 tools/qemu-nl-probe/syslog_sink.py <rootfs>/dev/log &   # 否则 daemon 常 exit(1) 且零输出
  qemu-aarch64-patched -L <rootfs> <rootfs>/usr/syno/sbin/resolvsrmd
'
```
- 很多 Synology 守护取不到 syslog 会**立刻 exit(1) 零输出**,只有 `strace` 看得到 `connect("/dev/log") = ENOENT`。
- 队列号/链名去 `<rootfs>/etc/init/*.conf` 与安装脚本里查。

## ⚠️ 边界(跨架构用之前必读)
放行靠的是"删守卫 + 对新增协议不做 fd 翻译",这**只在 64 位小端配对(aarch64 guest / x86_64 host)上验证过** ——
`sockaddr_nl` 与属性对齐一致,已端到端验过(NFQUEUE ACK error=0)。
**32 位(arm/MIPS)与大端 guest**:上游 qemu 写 ROUTE/AUDIT 翻译器**正是因为那些布局会差**;此时"**socket 探针过 ≠ 协议可用**"
(建 socket 与架构无关)⇒ **必须用真 daemon 跑一次 `CMD_BIND`/`PARAMS`,以 ACK=0 为准**。
