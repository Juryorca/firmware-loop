// nfq_probe.c — 最小 NETLINK_NETFILTER / NFQUEUE 能力探针(不依赖 libnetfilter_queue)
// 用途: 判定主机内核是否真的支持 NFQUEUE(WSL2 上 socket(PF_NETLINK,SOCK_RAW,NETLINK_NETFILTER)
//       会 ENOPROTOOPT;真内核应当成功)。仅申请队列 3 的 bind,不做任何转发/丢弃。
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <linux/netfilter/nfnetlink.h>
#include <linux/netfilter/nfnetlink_queue.h>

static int send_nl(int fd, struct sockaddr_nl *sa, int type, uint16_t q,
                   const void *attr, size_t attrlen, int seq)
{
    char buf[1024];
    struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
    struct nfgenmsg *nfg;
    memset(buf, 0, sizeof(buf));
    nlh->nlmsg_len = NLMSG_LENGTH(sizeof(struct nfgenmsg)) + attrlen;
    nlh->nlmsg_type = (NFNL_SUBSYS_QUEUE << 8) | type;
    nlh->nlmsg_flags = NLM_F_REQUEST;
    nlh->nlmsg_seq = seq;
    nfg = (struct nfgenmsg *)NLMSG_DATA(nlh);
    nfg->nfgen_family = AF_UNSPEC;
    nfg->version = NFNETLINK_V0;
    nfg->res_id = htons(q);
    if (attr && attrlen)
        memcpy((char *)NLMSG_DATA(nlh) + NLMSG_ALIGN(sizeof(*nfg)), attr, attrlen);
    return sendto(fd, buf, nlh->nlmsg_len, 0, (struct sockaddr *)sa, sizeof(*sa));
}

int main(void)
{
    int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER);
    if (fd < 0) {
        printf("FAIL socket(NETLINK_NETFILTER): %s\n", strerror(errno));
        return 1;
    }
    printf("OK   socket(NETLINK_NETFILTER) fd=%d\n", fd);

    struct sockaddr_nl sa;
    memset(&sa, 0, sizeof(sa));
    sa.nl_family = AF_NETLINK;
    if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
        printf("FAIL bind(netlink): %s\n", strerror(errno));
        return 1;
    }
    printf("OK   bind(netlink)\n");

    uint16_t q = 3;
    struct nfqnl_msg_config_cmd cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.command = NFQNL_CFG_CMD_BIND;
    cmd.pf = AF_INET;
    if (send_nl(fd, &sa, NFQNL_MSG_CONFIG, q, &cmd, sizeof(cmd), 1) < 0) {
        printf("FAIL send(BIND): %s\n", strerror(errno));
        return 1;
    }
    printf("OK   sent queue %u BIND request\n", q);

    struct nfqnl_msg_config_params params;
    memset(&params, 0, sizeof(params));
    params.copy_range = htonl(0xffff);
    params.copy_mode = NFQNL_COPY_PACKET;
    if (send_nl(fd, &sa, NFQNL_MSG_CONFIG, q, &params, sizeof(params), 2) < 0) {
        printf("FAIL send(PARAMS): %s\n", strerror(errno));
        return 1;
    }
    printf("OK   sent queue %u PARAMS (COPY_PACKET)\n", q);

    // 收一下内核的 ACK/错误
    char rbuf[4096];
    struct timeval tv = {2, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    ssize_t n = recv(fd, rbuf, sizeof(rbuf), 0);
    if (n < 0) {
        printf("note recv: %s (无 ACK 也常见)\n", strerror(errno));
    } else {
        struct nlmsghdr *rh = (struct nlmsghdr *)rbuf;
        if (rh->nlmsg_type == NLMSG_ERROR) {
            struct nlmsgerr *e = (struct nlmsgerr *)NLMSG_DATA(rh);
            printf("kernel reply: NLMSG_ERROR err=%d (%s)\n", e->error,
                   e->error ? strerror(-e->error) : "ack ok");
        } else {
            printf("kernel reply: type=%u len=%u\n", rh->nlmsg_type, rh->nlmsg_len);
        }
    }
    printf("RESULT: NETLINK_NETFILTER 可用,NFQUEUE 队列可绑定\n");
    return 0;
}
