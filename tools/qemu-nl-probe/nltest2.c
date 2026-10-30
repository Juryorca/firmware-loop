// nltest2.c — 枚举 (domain,type,protocol) 组合,找出 qemu-user 下哪些 socket() 能成功。
// 纯 raw syscall,无 libc。编译同 nltest.c。
#define SYS_write 64
#define SYS_exit 93
#define SYS_socket 198

static long sc3(long n, long a, long b, long c)
{
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
static long sc1(long n, long a)
{
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}
static char *put(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}
static char *putnum(char *p, long v)
{
    char t[24];
    int i = 0;
    if (v < 0) {
        *p++ = '-';
        v = -v;
    }
    do {
        t[i++] = '0' + (v % 10);
        v /= 10;
    } while (v);
    while (i)
        *p++ = t[--i];
    return p;
}

struct tc { const char *name; long domain, type, proto; };
static struct tc cases[] = {
    {"AF_INET/SOCK_DGRAM/IPPROTO_ICMP(ctrl)", 2, 2, 1},
    {"AF_NETLINK/RAW/0      (ROUTE)     ", 16, 3, 0},
    {"AF_NETLINK/RAW/4      (SOCK_DIAG) ", 16, 3, 4},
    {"AF_NETLINK/RAW/6      (XFRM)      ", 16, 3, 6},
    {"AF_NETLINK/RAW/12     (NETFILTER) ", 16, 3, 12},
    {"AF_NETLINK/RAW/15     (KOBJ_UEVENT)", 16, 3, 15},
    {"AF_NETLINK/RAW/16     (GENERIC)   ", 16, 3, 16},
    {"AF_NETLINK/DGRAM/12   (NETFILTER) ", 16, 2, 12},
    {"AF_NETLINK/RAW|CLOEXEC/12         ", 16, 3 | 0x80000, 12},
    {"AF_NETLINK/RAW/12|CLOEXEC         ", 16, 3, 12 | 0x80000},
};

void _start(void)
{
    char buf[2048];
    char *p = buf;
    int i;
    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++) {
        long r = sc3(SYS_socket, cases[i].domain, cases[i].type, cases[i].proto);
        p = put(p, cases[i].name);
        p = put(p, " -> ");
        p = putnum(p, r);
        p = put(p, "\n");
    }
    sc3(SYS_write, 1, (long)buf, p - buf);
    sc1(SYS_exit, 0);
    for (;;)
        ;
}
