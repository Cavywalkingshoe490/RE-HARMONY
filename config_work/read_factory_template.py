#!/usr/bin/env python3
"""READ ONLY. Reads the factory's DEVICE-PAGE key-register template out of a
config blob and writes the tables the builder needs.

Writes nothing to the device, imports no `grabar.cargar`, patches no bytes.
Everything it prints is MEASURED on the blob you give it. The command NAMES
are the one thing that is not: they come from a Hub `DeviceList.json`, which
is per-user account data and is not part of this repo -- so the path is
resolved by `glyphs.devicelist_path()` (env var, then `<repo>/hub/`, then an
account export left by the bridge), never hardcoded. Without it the layout
still comes out complete and the names come out `?`, and the run says so.

Where the blob comes from, in order:

    python3 -P read_factory_template.py               # backups/config_raw.bin
    python3 -P read_factory_template.py --blob b.bin  # any blob, raw or EZHex
    python3 -P read_factory_template.py --grabada 8   # a sync this app recorded

Outputs (JSON) go to `output/plantilla_fabrica/` (`--salida` to change it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import history
import sqlite3
import sys

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path[:0] = [str(AQUI)]

import commands as CMDS  # noqa: E402
import add_device as D  # noqa: E402
import ezhex  # noqa: E402
import glyphs  # noqa: E402
import irscan  # noqa: E402
import relocate  # noqa: E402
import keys_reach as TA  # noqa: E402
import keys_physical as TF  # noqa: E402
import keys_photo as TFOTO  # noqa: E402
import keys_map as TM  # noqa: E402

#: The blob this reads when nobody says otherwise: the config read off YOUR
#: OWN remote, which is where `read_config.py` and `flash_dump` leave it. `backups/`
#: is a working directory that is not published -- the blob is the user's
#: data, not the project's -- so this is a CONVENTION, and the message below
#: names it so that the error doubles as the instruction.
DEFAULT_BLOB = RAIZ / "backups" / "config_raw.bin"

#: The app's own registry of recorded syncs, for `--grabada`. Same path
#: `app/history.py` writes and `app/check_keys_auto.py` reads.
#: La MISMA carpeta que usa la app, resuelta por sistema operativo.
#: Estaba clavada a la ruta de macOS: en Windows apuntaba a una carpeta
#: que no existe, aunque `registro.data_directory()` ya sabia resolverlo.
DB = history.data_directory() / "registro.sqlite3"

SALIDA = RAIZ / "output" / "plantilla_fabrica"

NO_BLOB_TEXT = (
    "No config blob to read: %s does not exist.\n"
    "This tool measures a blob; it never talks to the remote. Give it one:\n"
    "  - read your own remote's config first (see `read_config.py`), which leaves\n"
    "    it at %s, or\n"
    "  - pass --blob <file> (a raw dump or an EZHex), or\n"
    "  - pass --grabada <n> to read a sync this app already recorded."
)


def load_file(path: pathlib.Path) -> bytes:
    """The blob inside `path`, whether it is an EZHex or a raw dump.

    `write.py` writes EZHex (an `<INFORMATION>` header and then the binary);
    `flash_dump` and `read_config.py` write the binary alone. Sniffing the header
    instead of the extension is what lets `--blob` take either without the
    caller having to know which one they have.
    """
    datos = path.read_bytes()
    try:
        return ezhex.split(datos)[1]
    except ValueError:
        return datos


def load_write_entry(n: int) -> bytes:
    """The blob of recorded sync `n`, READ ONLY. Never touches the device."""
    if not DB.exists():
        raise SystemExit(
            "there is no recorded-sync registry at %s, so --grabada has "
            "nothing to read. Use --blob <file> instead." % DB
        )
    try:
        row = (
            sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
            .execute("SELECT ezhex_path FROM grabadas WHERE id=?", (n,))
            .fetchone()
        )
    except sqlite3.Error as exc:
        raise SystemExit("%s could not be read: %s" % (DB, exc)) from exc
    if row is None:
        raise SystemExit("no such grabada: %d (in %s)" % (n, DB))
    path = pathlib.Path(row[0])
    if not path.is_file():
        raise SystemExit("grabada %d's file is gone: %s" % (n, path))
    return load_file(path)


def cargar(grabada: int | None, blob: str | None) -> tuple[bytes, str]:
    """`(bytes, where it came from)`. An explicit answer always wins."""
    if grabada is not None:
        return load_write_entry(grabada), "grabada %d" % grabada
    path = pathlib.Path(blob).expanduser() if blob else DEFAULT_BLOB
    if not path.is_file():
        raise SystemExit(NO_BLOB_TEXT % (path, DEFAULT_BLOB))
    return load_file(path), str(path)


def name_index(b: bytes, hubnames: dict) -> dict[int, dict[int, str]]:
    """`{k1: {k2: name}}` for every command section [5] resolves."""
    outside: dict[int, dict[int, str]] = {}
    for k1, d in enumerate(D.read_section5(b)):
        m: dict[int, str] = {}
        for k2 in range(d["n"]):
            reg, reason = D.resolve_section5(b, (k1 << 8) | k2)
            if reg is None:
                m[k2] = "!" + reason
                continue
            try:
                r = irscan.decode(irscan.read_waveform(b, D.u24(b, reg + 16) - D.BASE))
            except Exception:  # noqa: BLE001
                r = None
            nm = sorted(
                x
                for x in (hubnames.get((r[0], r[2])) if r else None) or []
                if x != "Unknown"
            )
            m[k2] = nm[0] if nm else "?"
        outside[k1] = m
    return outside


def has_hold(b: bytes, k1: int, k2: int) -> bool:
    reg, _ = D.resolve_section5(b, (k1 << 8) | k2)
    return reg is not None and D.u24(b, reg + 19) != 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--blob",
        help="a config blob (raw dump or EZHex); by omission %s"
        % DEFAULT_BLOB.relative_to(RAIZ),
    )
    ap.add_argument(
        "--grabada",
        type=int,
        help="read the blob of a sync this app recorded, instead of a file",
    )
    ap.add_argument(
        "--hub",
        help="a Hub DeviceList.json (per-user account data, not shipped with "
        "this repo); resolved by glifos.ruta_devicelist() when not given",
    )
    ap.add_argument("--salida", default=str(SALIDA), help="where the JSON goes")
    a = ap.parse_args()

    b, origin = cargar(a.grabada, a.blob)
    hub = pathlib.Path(a.hub).expanduser() if a.hub else glyphs.devicelist_path()
    salida = pathlib.Path(a.salida).expanduser()

    TM.set_t6(b)
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    if not hub.is_file():
        print(glyphs.TEXTO_SIN_DEVICELIST)
        print()
    hubnames = CMDS.hub_names(str(hub))
    n5 = name_index(b, hubnames)
    foto = json.loads(TFOTO.FOTO_JSON.read_text(encoding="utf-8"))
    etiq = {
        int(t["codigo"], 16): t["label"] for t in foto["keys"] if t.get("codigo")
    }

    print(
        "%s  len=%d  sha256=%s  md5=%s"
        % (
            origin,
            len(b),
            hashlib.sha256(b).hexdigest()[:24],
            hashlib.md5(b).hexdigest(),
        )
    )
    print("vocabulary: %s%s" % (hub, "" if hub.is_file() else "  (MISSING)"))

    # ------------------------------------------- census of header signatures
    n6 = D.u16(b, D.T6)
    firmas: dict[tuple, list[int]] = {}
    for o in range(n6):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * o) - D.BASE, max_n=200)
        if tr is None:
            continue
        h = D.read_header(b, tr["hdr"] - D.BASE)
        if h is None:
            continue
        firmas.setdefault(tuple(e[0] for e in h[0]), []).append(o)
    print("\n# header signatures in tabla[6] (%d screens)" % n6)
    for f, ords in sorted(firmas.items(), key=lambda x: -len(x[1])):
        print(
            "  n=%3d len=%2d  %s%s  ords=%s%s"
            % (
                len(ords),
                len(f),
                " ".join("%02X" % c for c in f[:8]),
                " ..." if len(f) > 8 else "",
                ords[:8],
                " ..." if len(ords) > 8 else "",
            )
        )

    plantilla49 = next((f for f in firmas if len(f) == 49), None)
    inv = TF.inventario(b)
    print(
        "\n# inventario 0x67 (%d) : %s" % (len(inv), " ".join("%02X" % c for c in inv))
    )
    if plantilla49 is None:
        # MEASURED, not assumed: the 49-row header is the factory's device-page
        # template. A blob that has none is not broken -- it just has no factory
        # device page to read the template off, and saying so beats a
        # `TypeError` three lines down.
        print("# template of 49      : NO 49-row header in this blob")
    else:
        print("# plantilla de 49     : %s" % " ".join("%02X" % c for c in plantilla49))
        print(
            "# NO declara          : %s"
            % " ".join("%02X" % c for c in inv if c not in plantilla49)
        )

    # --------------------------------------------------- the device pages
    pages = {
        i["screen"]: (k1, i["name"])
        for k1, i in TA.device_screen(b, str(hub)).items()
    }
    print("\n# paginas de dispositivo:", pages)

    tablas: dict[int, list[dict]] = {}
    for ordinal in sorted(pages) + [
        o for o in firmas.get(plantilla49, []) if o not in pages
    ]:
        tr, rows = TF._screen_header(b, ordinal)
        out = []
        for k, cod, campo, idv, cls in rows:
            f = {
                "k": k,
                "cod": cod,
                "cls": cls,
                "id": idv,
                "campo": campo,
                "label": etiq.get(cod),
            }
            if cls == TF.TAG_OBJ and 0 <= idv < len(dest11):
                forma, cmd, dev, pag, rs = TF._forma(b, dest11, idv)
                f["forma"] = forma
                f["slots"] = [(v, "0x%02X" % t) for v, t in rs]
                if cmd is not None:
                    f.update(
                        {
                            "cmd_id": cmd,
                            "k1": cmd >> 8,
                            "k2": cmd & 0xFF,
                            "name": n5.get(cmd >> 8, {}).get(cmd & 0xFF),
                            "dev_id": dev,
                            "hold": has_hold(b, cmd >> 8, cmd & 0xFF),
                        }
                    )
            elif cls == 0 and idv == 0:
                f["state"] = "apagada"
            tablas[ordinal] = tablas.get(ordinal, []) + [f]
            out.append(f)

    for ordinal, rows in tablas.items():
        k1, name = pages.get(ordinal, (None, "?"))
        print(
            "\n## pantalla %d  (%s, k1=%s)  filas=%d  con_comando=%d"
            % (ordinal, name, k1, len(rows), sum(1 for f in rows if "cmd_id" in f))
        )
        for f in rows:
            print(
                "   0x%02X %-16s %s"
                % (
                    f["cod"],
                    (f["label"] or "?")[:16],
                    (
                        "%-16s k1=%s k2=%-3s dev=0x%04X hold=%s"
                        % (f["name"], f["k1"], f["k2"], f["dev_id"], f["hold"])
                    )
                    if "cmd_id" in f
                    else (
                        "-- apagada (%02X 00 00 00) --" % f["cod"]
                        if f.get("state") == "apagada"
                        else "cls=0x%02X id=%d %s"
                        % (f["cls"], f["id"], f.get("forma") or "")
                    ),
                )
            )

    salida.mkdir(parents=True, exist_ok=True)
    (salida / "paginas.json").write_text(
        json.dumps({str(k): v for k, v in tablas.items()}, indent=1, ensure_ascii=False)
    )
    (salida / "seccion5_nombres.json").write_text(
        json.dumps(
            {str(k): {str(a2): b2 for a2, b2 in v.items()} for k, v in n5.items()},
            indent=1,
        )
    )
    # name -> [k2] index, per device
    idx = {}
    for k1, m in n5.items():
        d: dict[str, list[int]] = {}
        for k2, nm in sorted(m.items()):
            if nm == "?" or nm.startswith("!"):
                continue
            d.setdefault(nm, []).append(k2)
        idx[str(k1)] = d
    (salida / "indice_nombre_k2.json").write_text(json.dumps(idx, indent=1))
    print("\njson ->", salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
