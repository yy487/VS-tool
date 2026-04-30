/*
 * lzss_fast.c - LZSS compress/decompress with hash chain acceleration
 * Standard: 4KB window, init pos 0xFEE, fill 0x00, 12-bit offset + 4-bit length, min match 3
 *
 * Build: gcc -O2 -shared -fPIC -o lzss_fast.so lzss_fast.c
 *        (Windows: cl /O2 /LD lzss_fast.c)
 */

#include <string.h>
#include <stdlib.h>

#define WINDOW_SIZE  4096
#define WINDOW_MASK  0xFFF
#define INIT_POS     0xFEE
#define MIN_MATCH    3
#define MAX_MATCH    18
#define HASH_BITS    12
#define HASH_SIZE    (1 << HASH_BITS)
#define MAX_CHAIN    256

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

/* ── Decompress ─────────────────────────────────────────────────────────── */

EXPORT int lzss_decompress(
    const unsigned char *src, int src_len,
    unsigned char *dst, int dst_cap)
{
    unsigned char window[WINDOW_SIZE];
    int wp = INIT_POS, sp = 0, dp = 0;
    unsigned int flags = 0, bits = 0;

    memset(window, 0, WINDOW_SIZE);

    while (dp < dst_cap && sp < src_len) {
        if (bits == 0) {
            flags = src[sp++];
            bits = 8;
        }
        if (flags & 1) {
            /* literal */
            if (sp >= src_len) break;
            unsigned char b = src[sp++];
            dst[dp++] = b;
            window[wp] = b;
            wp = (wp + 1) & WINDOW_MASK;
        } else {
            /* match */
            if (sp + 1 >= src_len) break;
            unsigned int lo = src[sp++];
            unsigned int hi = src[sp++];
            int offset = lo | ((hi & 0xF0) << 4);
            int length = (hi & 0x0F) + MIN_MATCH;
            for (int i = 0; i < length && dp < dst_cap; i++) {
                unsigned char b = window[(offset + i) & WINDOW_MASK];
                dst[dp++] = b;
                window[wp] = b;
                wp = (wp + 1) & WINDOW_MASK;
            }
        }
        flags >>= 1;
        bits--;
    }
    return dp;
}

/* ── Compress (greedy, hash chain) ──────────────────────────────────────── */

static inline unsigned int hash2(unsigned char a, unsigned char b) {
    return ((a << 4) ^ b) & (HASH_SIZE - 1);
}

EXPORT int lzss_compress(
    const unsigned char *src, int src_len,
    unsigned char *dst, int dst_cap)
{
    unsigned char window[WINDOW_SIZE];
    int head[HASH_SIZE];
    int prev[WINDOW_SIZE];
    int wp = INIT_POS, sp = 0, dp = 0;

    memset(window, 0, WINDOW_SIZE);
    memset(head, -1, sizeof(head));
    memset(prev, -1, sizeof(prev));

    /* pre-populate hash chain for initial zero-filled window region */
    /* The window[0..0xFED] is all zeros, so hash(0,0) chains them all.
       We only need to register the last few positions to avoid huge chains. */

    while (sp < src_len && dp < dst_cap - 2) {
        int flag_pos = dp++;
        unsigned char flag_byte = 0;

        for (int bit = 0; bit < 8; bit++) {
            if (sp >= src_len) {
                /* pad with literal zero */
                flag_byte |= (1 << bit);
                if (dp < dst_cap) dst[dp++] = 0;
                continue;
            }
            if (dp + 2 >= dst_cap) {
                /* no room, emit literal */
                flag_byte |= (1 << bit);
                if (dp < dst_cap) dst[dp++] = src[sp];
                window[wp] = src[sp];
                int h = hash2(src[sp], (sp + 1 < src_len) ? src[sp+1] : 0);
                prev[wp] = head[h];
                head[h] = wp;
                wp = (wp + 1) & WINDOW_MASK;
                sp++;
                continue;
            }

            int best_len = 0, best_off = 0;
            int max_match = MAX_MATCH;
            if (src_len - sp < max_match) max_match = src_len - sp;

            if (max_match >= MIN_MATCH) {
                unsigned int h = hash2(src[sp], (sp + 1 < src_len) ? src[sp+1] : 0);
                int chain_pos = head[h];
                int chain_count = 0;
                while (chain_pos >= 0 && chain_count < MAX_CHAIN) {
                    /* Distance from match source to write position in circular buffer.
                       If distance < MAX_MATCH, the match could overlap with writes. */
                    int dist = (wp - chain_pos) & WINDOW_MASK;
                    int ml = 0;

                    if (dist == 0 || dist >= max_match) {
                        /* No overlap possible - fast path, direct comparison */
                        while (ml < max_match &&
                               window[(chain_pos + ml) & WINDOW_MASK] == src[sp + ml])
                            ml++;
                    } else {
                        /* Overlap possible - simulate decompressor behavior:
                           when reading window[off+i] where i >= dist, the decompressor
                           sees what it wrote at window[off + (i % dist)] earlier. */
                        while (ml < max_match) {
                            int read_pos = (chain_pos + ml) & WINDOW_MASK;
                            unsigned char b;
                            if (ml < dist) {
                                b = window[read_pos];
                            } else {
                                /* decompressor already wrote here; it repeats
                                   the pattern from offset 0..dist-1 */
                                b = src[sp + (ml % dist)];
                            }
                            if (b != src[sp + ml]) break;
                            ml++;
                        }
                    }

                    if (ml > best_len) {
                        best_len = ml;
                        best_off = chain_pos;
                        if (best_len == max_match) break;
                    }
                    chain_pos = prev[chain_pos & WINDOW_MASK];
                    chain_count++;
                }
            }

            if (best_len >= MIN_MATCH) {
                /* emit match */
                dst[dp++] = best_off & 0xFF;
                dst[dp++] = ((best_off >> 4) & 0xF0) | ((best_len - MIN_MATCH) & 0x0F);
                for (int i = 0; i < best_len; i++) {
                    unsigned char b = src[sp];
                    int h = hash2(b, (sp + 1 < src_len) ? src[sp+1] : 0);
                    prev[wp] = head[h];
                    head[h] = wp;
                    window[wp] = b;
                    wp = (wp + 1) & WINDOW_MASK;
                    sp++;
                }
            } else {
                /* emit literal */
                flag_byte |= (1 << bit);
                unsigned char b = src[sp];
                dst[dp++] = b;
                int h = hash2(b, (sp + 1 < src_len) ? src[sp+1] : 0);
                prev[wp] = head[h];
                head[h] = wp;
                window[wp] = b;
                wp = (wp + 1) & WINDOW_MASK;
                sp++;
            }
        }
        dst[flag_pos] = flag_byte;
    }
    return dp;
}

/* ── Literal-only compress (flag=0xFF, ~12.5% overhead, zero risk) ──── */

EXPORT int lzss_compress_literal(
    const unsigned char *src, int src_len,
    unsigned char *dst, int dst_cap)
{
    int sp = 0, dp = 0;
    while (sp < src_len && dp < dst_cap) {
        int chunk = 8;
        if (src_len - sp < chunk) chunk = src_len - sp;
        dst[dp++] = (1 << chunk) - 1;  /* all literal bits */
        for (int i = 0; i < chunk && dp < dst_cap; i++)
            dst[dp++] = src[sp++];
    }
    return dp;
}
