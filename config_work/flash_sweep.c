/*
 * flash_sweep -- READ-ONLY survey of the Harmony One's external flash.
 *
 * concordance cannot dump this remote's firmware: arch 12 has firmware_base = 0
 * (remote_info.h), so --dump-firmware and --dump-safemode both read address
 * 0x000000 and return erased flash. Where the firmware actually lives on this
 * model was never established -- jaymzh/concordance#30 is still open on it.
 *
 * This samples a window at regular intervals across the whole chip and reports
 * which regions hold data instead of erased 0xFF. It only ever reads, so it
 * cannot brick the remote.
 *
 * Uses read_flash_at(), added to libconcord for this purpose. Written in C
 * against the public API on purpose: instantiating CRemote from outside the
 * dylib collides with the library's own C++ symbols and segfaults.
 *
 *   run: ./flash_sweep [step_kib] [sample_bytes]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "libconcord.h"

/* libconcord's _report_stages() calls the callback without a NULL check, so a
 * no-op callback is required rather than passing NULL. */
static void noop_cb(uint32_t stage, uint32_t count, uint32_t curr,
                    uint32_t total, uint32_t type, void *arg,
                    const uint32_t *stages)
{
    (void)stage; (void)count; (void)curr;
    (void)total; (void)type; (void)arg; (void)stages;
}

static int all_same(const uint8_t *p, size_t n, uint8_t v)
{
    for (size_t i = 0; i < n; i++)
        if (p[i] != v) return 0;
    return 1;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    uint32_t step = (argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 128) * 1024;
    uint32_t sample = argc > 2 ? (uint32_t)strtoul(argv[2], NULL, 0) : 256;

    int err = init_concord();
    if (err) {
        fprintf(stderr, "init_concord: %s\n", lc_strerror(err));
        return 1;
    }
    err = get_identity(noop_cb, NULL);
    if (err) {
        fprintf(stderr, "get_identity: %s\n", lc_strerror(err));
        return 1;
    }

    int flash_kib = get_flash_size();
    printf("USB %04X:%04X  arch %d  proto %d  flash %d KiB (%02X:%02X %s)\n",
           get_usb_vid(), get_usb_pid(), get_arch(), get_proto(), flash_kib,
           get_flash_mfg(), get_flash_id(), get_flash_part_num());
    printf("config: %d of %d KiB used\n\n",
           get_config_bytes_used() / 1024, get_config_bytes_total() / 1024);

    uint32_t total = flash_kib > 0 ? (uint32_t)flash_kib * 1024
                                   : 4u * 1024 * 1024;
    printf("sweep 0x000000..%#08x  step %u KiB  sample %u B  (READ-ONLY)\n\n",
           total, step / 1024, sample);

    uint8_t *buf = malloc(sample);
    uint32_t data = 0, errors = 0;

    for (uint32_t addr = 0; addr < total; addr += step) {
        memset(buf, 0xA5, sample);
        int e = read_flash_at(addr, sample, buf);
        if (e) {
            printf("  %#08x  READ ERROR (%s)\n", addr, lc_strerror(e));
            errors++;
            continue;
        }
        const char *kind = "DATA";
        if (all_same(buf, sample, 0xFF))      kind = "erased (FF)";
        else if (all_same(buf, sample, 0x00)) kind = "zeros  (00)";
        else                                  data++;

        printf("  %#08x  %-12s", addr, kind);
        if (!strcmp(kind, "DATA")) {
            printf("  ");
            for (int i = 0; i < 12; i++) printf("%02x ", buf[i]);
        }
        printf("\n");
    }

    printf("\nregions with data: %u   read errors: %u\n", data, errors);
    deinit_concord();
    return 0;
}
