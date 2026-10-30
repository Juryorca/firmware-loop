/* QNAP PC1 cipher — C port matching Bonstra/qnap_fwdec crypto.rs (16-bit semantics).
 * Verified against the reference test vectors (see --selftest).
 *
 * usage: qnapcipher d|e <key> <infile> <outfile> [length]
 *        qnapcipher --selftest
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef struct {
    uint32_t y, z;
    uint8_t *key;
    uint8_t *workbuf;
    size_t klen;
} Ctx;

static inline uint16_t su8(uint8_t b) { return (uint16_t)b; }

static void ctx_init(Ctx *c, const uint8_t *key, size_t klen) {
    c->key = malloc(klen);
    c->workbuf = malloc(klen);
    memcpy(c->key, key, klen);
    memcpy(c->workbuf, key, klen);
    c->klen = klen;
    c->y = 0;
    c->z = 0;
}

static void ctx_reset(Ctx *c) {
    c->y = 0;
    c->z = 0;
    memcpy(c->workbuf, c->key, c->klen);
}

static inline void update_yz(Ctx *c, uint32_t index, uint32_t ks_val) {
    uint32_t new_y = ((ks_val & 0xffffu) * 0x015au) & 0xffffu;
    uint32_t new_z = (index + (c->z & 0xffffu)) & 0xffffu;
    if (new_z != 0)
        new_z = (new_z & 0xffffu) * 0x4e35u & 0xffffu;
    new_z += new_y & 0xffffu;
    new_z += c->y & 0xffffu;
    new_z &= 0xffffu;
    c->y = new_y;
    c->z = new_z;
}

static inline uint16_t assemble(Ctx *c) {
    size_t hwords = c->klen >> 1;
    uint32_t coded = 0;
    uint32_t prev = 0;
    for (size_t r = 0; r < hwords; r++) {
        int32_t k0 = (int8_t)c->workbuf[r << 1];
        int32_t k1 = (int8_t)c->workbuf[(r << 1) + 1];
        uint32_t keyhword = (uint32_t)((k0 << 8) + k1);
        uint32_t ks = (prev ^ keyhword) & 0xffffffffu;
        update_yz(c, (uint32_t)r, ks);
        ks = (((ks & 0xffffu) * 0x4e35u) + 1u) & 0xffffu;
        coded ^= (ks ^ c->z);
        prev = ks;
    }
    return (uint16_t)(coded & 0xffffu);
}

static inline uint8_t crypt_byte(Ctx *c, uint8_t in, int is_decrypt) {
    uint16_t a = assemble(c);
    uint8_t ab = (uint8_t)((a >> 8) ^ a);
    uint8_t scramble = is_decrypt ? (uint8_t)(in ^ ab) : in;
    uint8_t out = (uint8_t)(in ^ ab);
    for (size_t i = 0; i < c->klen; i++)
        c->workbuf[i] ^= scramble;
    return out;
}

/* ---- selftest ---- */
static const uint8_t S1_PT[0x20] = {
    0x5a,0x90,0x77,0x24,0xc7,0x14,0x37,0xeb,0x52,0x5f,0x72,0x60,0xbc,0x7d,0xb1,0x95,
    0x36,0x99,0x5e,0x09,0xe5,0x49,0x06,0x5b,0xdb,0x3a,0xa3,0xd4,0x17,0x9a,0xd4,0x4a};
static const uint8_t S1_CT[0x20] = {
    0x3f,0xb9,0xcf,0xf6,0xd1,0x80,0xc0,0x4c,0xf7,0x40,0xc0,0x74,0x98,0xbd,0x7e,0x3f,
    0x8f,0xc6,0x98,0x92,0x55,0xc0,0x09,0xd9,0xa7,0x8b,0x10,0x90,0xfe,0x66,0xc5,0xcf};

static int selftest(void) {
    Ctx c; ctx_init(&c, (const uint8_t *)"TestKey", 7);
    for (int i = 0; i < 0x20; i++) {
        uint8_t o = crypt_byte(&c, S1_PT[i], 0);
        if (o != S1_CT[i]) { fprintf(stderr, "enc mismatch at %d\n", i); return 1; }
    }
    ctx_reset(&c);
    for (int i = 0; i < 0x20; i++) {
        uint8_t o = crypt_byte(&c, S1_CT[i], 1);
        if (o != S1_PT[i]) { fprintf(stderr, "dec mismatch at %d\n", i); return 1; }
    }
    printf("selftest OK\n");
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--selftest")) return selftest();
    if (argc < 5) {
        fprintf(stderr, "usage: %s d|e <key> <infile> <outfile> [length]\n", argv[0]);
        return 2;
    }
    int dec = argv[1][0] == 'd';
    const uint8_t *key = (const uint8_t *)argv[2];
    size_t klen = strlen(argv[2]);
    if (klen & 1) klen--;              /* odd key: last byte ignored */
    if (klen == 0) { fprintf(stderr, "empty key\n"); return 2; }
    FILE *fi = fopen(argv[3], "rb");
    if (!fi) { perror("infile"); return 3; }
    FILE *fo = fopen(argv[4], "wb");
    if (!fo) { perror("outfile"); return 3; }
    unsigned long long limit = ~0ULL;
    if (argc >= 6) limit = strtoull(argv[5], NULL, 0);

    Ctx c; ctx_init(&c, key, klen);
    static uint8_t buf[1 << 20];
    unsigned long long done = 0;
    while (done < limit) {
        size_t want = sizeof(buf);
        if (limit - done < want) want = (size_t)(limit - done);
        size_t n = fread(buf, 1, want, fi);
        if (n == 0) break;
        for (size_t i = 0; i < n; i++)
            buf[i] = crypt_byte(&c, buf[i], dec);
        if (fwrite(buf, 1, n, fo) != n) { perror("write"); return 4; }
        done += n;
    }
    fclose(fi); fclose(fo);
    fprintf(stderr, "processed %llu bytes\n", done);
    return 0;
}
