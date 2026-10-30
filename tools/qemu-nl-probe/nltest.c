// nltest.c — 极小 aarch64 程序(纯 raw syscall,无 libc):试探 NETLINK_NETFILTER 套接字能否创建。
// 编译: clang --target=aarch64-linux-gnu -nostdlib -nostartfiles -static -Wl,-e,_start -o nltest nltest.c
// 用途: 在真内核 + qemu-aarch64 下区分 —— "是内核不支持" 还是 "qemu 翻译出问题" 还是 "guest 库的问题"。
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

void _start(void)
{
    char buf[128];
    char *p = buf;
    long fd = sc3(SYS_socket, 16 /*AF_NETLINK*/, 3 /*SOCK_RAW*/, 12 /*NETLINK_NETFILTER*/);
    p = put(p, "aarch64 socket(AF_NETLINK,SOCK_RAW,NETLINK_NETFILTER) = ");
    p = putnum(p, fd);
    p = put(p, "\n");
    sc3(SYS_write, 1, (long)buf, p - buf);
    sc1(SYS_exit, 0);
    for (;;)
        ;
}
