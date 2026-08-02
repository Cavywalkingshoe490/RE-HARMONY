# RE-HARMONY

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#2-libconcord--the-one-real-install-step)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Hardware](https://img.shields.io/badge/hardware-Harmony%20One-orange.svg)](#re-harmony)

**Configure a Logitech Harmony One again.**

<img src="re-harmony.png" alt="RE-HARMONY" width="640">

---

Logitech shut the Harmony service down in May 2025. The remotes still work —
they just cannot be *reconfigured* any more, because every path to change what
a button does went through a server that no longer answers.

RE-HARMONY forges the remote's configuration directly: it reads the one your
remote has, builds a new one byte by byte, and writes it back over USB.

> Not affiliated with, endorsed by, or connected to Logitech. *Harmony* and
> *Logitech* are trademarks of Logitech International S.A. and/or its
> affiliates, used here only descriptively. See [Trademarks](#trademarks).

## What it does

- **Reads your remote** over USB and keeps it as your baseline, and as your
  way back.
- **Downloads devices from Logitech's catalog.** Their device database is
  still online. Search by manufacturer and model and the device is saved ready
  to add. Read-only against your account: nothing is created or removed there.
  ([one-time setup](#optional-the-logitech-catalog))
- **Adds IR from `.ir` files** — Flipper Zero / IRDB format — for anything the
  catalog does not have.
- **Learns IR codes** with the remote's own receiver: point the original
  remote at it and press the button.
- **Adds devices** to the remote's `Devices` list, with their own on-screen
  pages of commands.
- **Reassigns the rubber keys** (volume, channel, transport, number pad) per
  device.
- **Shows every change before writing** and refuses to write if anything moved
  that you did not ask to move.

## What it does not do

- **Only the Harmony One.** Not the Hub, not the 650, not the Touch.
- **Tested against one unit** (arch 12, skin 54, firmware 0.5.0). If yours
  differs it is built to refuse rather than write wrong. *Add device* in
  particular reuses factory bitmaps by absolute offset and will abort cleanly
  on a remote that is not that one.
- **Never touches firmware, bootloader or safemode.** Only the configuration
  region.
- **The catalog needs your Logitech account** and one identifier that is not
  ours: pull Logitech's `client_id` from your own copy of the Harmony APK with
  `config_work/extract_client_id.py`, or set `RE_HARMONY_CLIENT_ID`. Without
  it, `.ir` files and learning still work.
- **Developed on macOS.** Linux should work; Windows has not been exercised.

---

## 1. Install

You need a **Harmony One** with its USB cable, **Python 3.11+**, and a
**patched libconcord** (section 2 — the one real install step).

```bash
git clone https://github.com/Unkn0wn-dx/RE-HARMONY
cd RE-HARMONY

cd app && uv sync && cd ..          # creates app/.venv
# without uv:  python3 -m venv app/.venv && app/.venv/bin/pip install -e app

PY=app/.venv/bin/python             # the system python3 has no pywebview

$PY first_run.py                    # YOUR baseline. Plug the remote in. READ ONLY.
$PY check.py                        # is this machine sane?
./RE-HARMONY                        # open it
```

`first_run.py` is **not optional**: every screen compares against that
baseline, and the write gate has nothing to compare against without it.

### Optional: the Logitech catalog

Downloading devices from Logitech's catalog needs your Logitech account **and
one identifier that is theirs, not ours**: a `client_id` that lives inside
their Android app. It is their credential, so it is not redistributed here.
Pull it out of your own copy of the APK, once:

```bash
python3 config_work/extract_client_id.py /path/to/harmony.apk
#   or, if you already have the value:  export RE_HARMONY_CLIENT_ID=...
```

Skip this and everything else still works — you add devices from `.ir` files
or by learning the codes off your old remote. The Catalog screen says so
instead of failing halfway.

---

## 2. libconcord — the one real install step

**The released libconcord cannot read a Harmony One.** Its dump entry points
read relative to `firmware_base`, and for arch 12 that field is `0`, so every
call reads address 0 — while the configuration lives at `0x040000`.
RE-HARMONY refuses to write anything it has not read first, so without the fix
nothing works.

There is no package with it: `brew install concordance` does not exist, and
`apt`/`dnf` ship upstream libconcord, which lacks the two functions. **You
build it yourself, and it has to be our version** — an unpatched build loads
perfectly and then cannot read anything, which is exactly the trap.
`check.py` calls that case a failure, not a skip.

**The source is in this repository, already modified**, at
`tools/libconcord/src/` — Concordance's libconcord with our change applied.
No binary ships here: you compile it. `tools/libconcord/libconcord-re-harmony.patch`
is the exact diff against upstream, so you can see what changed and apply it
to a different Concordance revision if you want to.

What the change adds, both **read-only** — no write symbol is introduced:

| function | what it does |
|---|---|
| `read_flash_at(addr, len, out)` | read an arbitrary flash range |
| `read_misc_at(addr, len, kind, out)` | `READ_MISC` with a 16-bit address; subtype `PROGRAM` (4) is the only route to the CPU's own view of memory |

### macOS

```bash
brew install pkg-config autoconf automake libtool libzip hidapi

cd tools/libconcord/src
autoreconf -fi
# tell configure where Homebrew put headers and libraries, otherwise it
# stops at "libhidapi is missing!"
CPPFLAGS="-I$(brew --prefix)/include" LDFLAGS="-L$(brew --prefix)/lib" ./configure
make
```

Result: `tools/libconcord/src/.libs/libconcord.6.dylib`. Verified end to end on
macOS 15 / arm64: the tree in this repository configures, compiles and loads
with both symbols resolvable.

### Linux

```bash
sudo apt install build-essential autoconf automake libtool pkg-config \
                 libzip-dev libhidapi-dev libcurl4-openssl-dev

cd tools/libconcord/src
autoreconf -fi
./configure        # plain, no CPPFLAGS/LDFLAGS needed
make
```

Result: `tools/libconcord/src/.libs/libconcord.so.6`.

Reading over USB needs permission on the device node: run `first_run.py` as
root once, or add a udev rule. *Not exercised here — the USB layer is
libconcord's, so it is expected to work, but nobody has confirmed it.*

### Windows

*Not exercised here at all.* Upstream builds with **MinGW** and our change is
platform-neutral, so it compiles the same way. Build `zlib`, `libzip`,
`hidapi` and `libcurl` for the target first — on Fedora the `mingw32-*`
packages, elsewhere into a prefix such as `/tmp/buildroot`:

```bash
cd tools/libconcord/src

mingw32-configure && mingw32-make        # Fedora

./configure --host=i686-w64-mingw32 --prefix=/tmp/buildroot \
  CPPFLAGS="-I/tmp/buildroot/include" \
  LDFLAGS="-L/tmp/buildroot/libs -L/tmp/buildroot/libs/bin" \
  PKG_CONFIG_PATH=/tmp/buildroot/lib/pkgconfig && make    # elsewhere
```

`tools/libconcord/src/INSTALL.windows` is upstream's own page on the
dependencies.

### Point RE-HARMONY at what you built

```bash
export RE_HARMONY_LIBCONCORD=$PWD/tools/libconcord/src/.libs/libconcord.6.dylib
#   Linux: .../libconcord.so.6      (installing it system-wide also works)

python3 -c "import ctypes,os; l=ctypes.CDLL(os.environ['RE_HARMONY_LIBCONCORD']); \
            l.read_flash_at; l.read_misc_at; print('both symbols present')"
```

`check.py` reports the same thing, and `first_run.py` refuses to start without
it and says why.

---

## IR codes

No device catalogue and no vendor protocol database is redistributed here — a
device list is also a list of what is in somebody's living room. Six protocol
timing tables ship in `config_work/read_ir.py` (SIRC, NEC, LG 28-bit, Jerrold,
Magnavox); everything else arrives with the devices you download, the `.ir`
files you import, or the codes you learn, and is kept in `protocol_library/`
from the first time it is seen.

---

## Layout

```
first_run.py       reads your remote and leaves the baseline. Start here.
check.py           runs the checks: PASSED / FAILED / COULD NOT BE RUN
RE-HARMONY         the launcher
app/               the application: API, UI, library, history
config_work/       the format work: blob, screens, keys, IR, flash
protocol_library/  the seed protocols and the glyph word list
tools/libconcord/  libconcord with our read-only change, plus the diff
```

---

## Trademarks

**Logitech**, **Harmony** and **Harmony One** are trademarks of Logitech
International S.A. and/or its affiliates. This project is **not affiliated
with, endorsed by, sponsored by, or connected to Logitech** in any way.

Those names appear only to say what hardware this software works with, which
is descriptive, nominative use. No Logitech logo, icon, product photograph or
trade dress is distributed here; the drawing of the remote in the interface is
generated from measured button coordinates.

## License

GPL-3.0-or-later — full text in [LICENSE](LICENSE).

RE-HARMONY talks to the remote through **libconcord**, from
[Concordance](https://github.com/jaymzh/concordance) by Phil Dibowitz and
contributors, building on the earlier Harmony tools by Kevin Timmerman and
Phil Dibowitz. Concordance is GPLv3, and the C sources in `config_work/`
include its internal headers, which is why this repository is GPL and not
something permissive.

**The vendored sources.** `tools/libconcord/src/` is Concordance's libconcord
with our change applied, redistributed here under the same
GPL-3.0-or-later. Every file keeps its original copyright headers, and the
four we touched — `libconcord.h`, `libconcord.cpp`, `remote.h`,
`remote_mh.cpp` — carry a notice at the top saying so, as the licence
requires. `tools/libconcord/libconcord-re-harmony.patch` is the exact diff,
and it records the sha256 of each file it applies to. Unmodified upstream is
at <https://github.com/jaymzh/concordance>.

Those two read functions belong upstream, and sending them there is the right
outcome. Until that happens, this patch is how you get them.

---

## Support

If this brought a dead remote back to life, you can buy me a coffee:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/davexx)

Completely optional — starring the repo helps just as much.
