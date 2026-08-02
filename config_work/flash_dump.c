/*
 * flash_dump -- READ-ONLY dump of an arbitrary flash range on a Harmony remote.
 *
 * Companion to flash_sweep. The sweep showed that arch 12's firmware_base = 0
 * in concordance is wrong: address 0x000000 holds data, and 0x020000 begins
 * with a header carrying the 0x48 0x47 firmware magic that libconcord's
 * _fix_magic_bytes() looks for. This pulls those regions out whole so they can
 * be examined offline.
 *
 * Only calls read_flash_at(); never erases or writes.
 *
 *   run: ./flash_dump <start_hex> <len_hex> <outfile>
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "libconcord.h"

/* libconcord's _report_stages() calls the callback without a NULL check. */
static void noop_cb(uint32_t stage, uint32_t count, uint32_t curr,
                    uint32_t total, uint32_t type, void *arg,
                    const uint32_t *stages)
{
    (void)stage; (void)count; (void)curr;
    (void)total; (void)type; (void)arg; (void)stages;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc < 4) {
        fprintf(stderr, "usage: %s <start_hex> <len_hex> <outfile>\n", argv[0]);
        return 2;
    }
    uint32_t start = (uint32_t)strtoul(argv[1], NULL, 16);
    uint32_t len = (uint32_t)strtoul(argv[2], NULL, 16);
    const char *out = argv[3];

    int err = init_concord();
    if (err) { fprintf(stderr, "init: %s\n", lc_strerror(err)); return 1; }
    err = get_identity(noop_cb, NULL);
    if (err) { fprintf(stderr, "identity: %s\n", lc_strerror(err)); return 1; }

    printf("%04X:%04X arch %d proto %d -- dumping %#08x..%#08x (%u KiB)\n",
           get_usb_vid(), get_usb_pid(), get_arch(), get_proto(),
           start, start + len, len / 1024);

    uint8_t *buf = malloc(len);
    if (!buf) { fprintf(stderr, "oom\n"); return 1; }

    /* Chunked so a failure localises to a range instead of losing everything. */
    const uint32_t chunk = 0x4000; /* 16 KiB */
    uint32_t done = 0, failed = 0;
    for (uint32_t off = 0; off < len; off += chunk) {
        uint32_t n = (len - off < chunk) ? (len - off) : chunk;
        int e = read_flash_at(start + off, n, buf + off);
        if (e) {
            fprintf(stderr, "  read error at %#08x: %s\n",
                    start + off, lc_strerror(e));
            memset(buf + off, 0xA5, n);
            failed++;
        } else {
            done += n;
        }
        printf("\r  %u/%u KiB", (off + n) / 1024, len / 1024);
    }
    printf("\n");

    FILE *f = fopen(out, "wb");
    if (!f) { fprintf(stderr, "cannot write %s\n", out); return 1; }
    fwrite(buf, 1, len, f);
    fclose(f);
    printf("wrote %s (%u bytes read OK, %u chunk failures)\n",
           out, done, failed);

    deinit_concord();
    return 0;
}
