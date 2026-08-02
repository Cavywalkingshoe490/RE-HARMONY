// flash_sweep -- READ-ONLY survey of the Harmony One's external flash.
//
// concordance cannot dump this remote's firmware: arch 12 has firmware_base = 0
// (remote_info.h), so --dump-firmware and --dump-safemode both read address
// 0x000000 and return erased flash. Nobody ever established where the firmware
// actually lives on this model -- jaymzh/concordance#30 is still open on it.
//
// This tool answers that empirically: it samples a small window at regular
// intervals across the whole chip and reports which regions hold data rather
// than erased 0xFF. It only calls ReadFlash, never erase or write, so it cannot
// brick the remote.
//
// Note on initialisation: the library must be brought up with init_concord()
// before any CRemote method is used. CRemote::GetIdentity -> make_serial ->
// make_guid calls is_z_remote()/is_mh_remote(), which dereference libconcord's
// *global* `rmt`. Instantiating a bare CRemote and calling GetIdentity on it
// leaves that global NULL and segfaults.
//
//   run: ./flash_sweep [step_kib] [sample_bytes]

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <string>
using namespace std;

#include "remote.h"
#include "hid.h"
#include "libconcord.h"
#include "protocol.h"

static bool all_same(const uint8_t *p, size_t n, uint8_t v)
{
    for (size_t i = 0; i < n; i++)
        if (p[i] != v) return false;
    return true;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    const uint32_t step = (argc > 1 ? strtoul(argv[1], NULL, 0) : 64) * 1024;
    const uint32_t sample = argc > 2 ? strtoul(argv[2], NULL, 0) : 256;

    // Full library init: sets the global remote object the identity path needs.
    if (init_concord()) {
        fprintf(stderr, "init_concord failed\n");
        return 1;
    }
    if (get_identity(NULL, NULL)) {
        fprintf(stderr, "get_identity failed\n");
        return 1;
    }

    const int proto = get_proto();
    const int arch = get_arch();
    const int flash_kib = get_flash_size();
    printf("USB %04X:%04X  arch %d  protocol %d  flash %d KiB (%02X:%02X)\n",
           get_usb_vid(), get_usb_pid(), arch, proto, flash_kib,
           get_flash_mfg(), get_flash_id());
    printf("config used %d of %d KiB\n\n",
           get_config_bytes_used() / 1024, get_config_bytes_total() / 1024);

    const uint32_t total = flash_kib > 0 ? (uint32_t)flash_kib * 1024
                                         : 4u * 1024 * 1024;
    printf("sweeping 0x000000..%#08x  step %u KiB  sample %u B  (READ-ONLY)\n\n",
           total, step / 1024, sample);

    // A local CRemote is fine for ReadFlash: the HID handle is global, and this
    // path does not touch the library's `rmt` pointer.
    CRemote remote;
    uint8_t *buf = (uint8_t *)malloc(sample);
    uint32_t data_regions = 0, errors = 0;

    for (uint32_t addr = 0; addr < total; addr += step) {
        memset(buf, 0xA5, sample);
        int err = remote.ReadFlash(addr, sample, buf, proto);
        if (err) {
            printf("  %#08x  READ ERROR %d\n", addr, err);
            errors++;
            continue;
        }
        const char *kind = "DATA";
        if (all_same(buf, sample, 0xFF))      kind = "erased (FF)";
        else if (all_same(buf, sample, 0x00)) kind = "zeros  (00)";
        else                                  data_regions++;

        printf("  %#08x  %-12s", addr, kind);
        if (strcmp(kind, "DATA") == 0) {
            printf("  ");
            for (int i = 0; i < 12; i++) printf("%02x ", buf[i]);
        }
        printf("\n");
    }

    printf("\nregions with data: %u   read errors: %u\n", data_regions, errors);
    deinit_concord();
    return 0;
}
