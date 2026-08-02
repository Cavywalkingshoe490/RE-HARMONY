#!/usr/bin/env python3
"""Draws the remote from the MEASURED COORDINATES, and writes `app/ui/remote/remote.svg`.

## What it draws, and why like this

`app/ui/remote/keys.json` has the 44 key boxes and the LCD one, in pixels of
a 246x907 crop. Those are **facts measured on the device**: positions, sizes
and shape (rectangle or circle) of every key. That is enough to draw the
remote, and the drawing that comes out is the SAME one the interface shows,
because the clickable-zone layer (`#capa-zones`, painted by `app/ui/app.js`)
comes out of that same file and shares the coordinate system.

**One single source of truth.** This file used to draw a remote BY HAND, with
37 keys placed from memory on a 280x1383 canvas: a drawing similar to the
product but that did NOT match the one the app uses (44 keys, 246x907). Two
different drawings of the same device is a contradiction waiting for someone
to believe it. Now there is only one, and it comes out of the measurements.

**No photograph is redistributed.** What comes out is a schematic: body,
screen and one shape per key in its real position. The body's silhouette is
derived from the extent of the boxes themselves plus a margin; it is not a
factory blueprint. The rounded corners, the colours and the border width are
aesthetic decisions of this file. None of that asserts a measurement that was
not measured.

**The text labels** are only drawn when they really fit (two characters or
less: the numeric keypad and little else). If it does not fit, it is not
drawn: clipped text would be worse than absent text. The full label goes into
each shape's `<title>` anyway, which is what a screen reader reads.

## No side effects on import

Importing this module writes NOTHING. Everything that touches disk lives
under `if __name__ == "__main__"`. (It used to not be like this:
`import generar_svg_mando` wrote the SVG and tried to rewrite
`app/ui/index.html`, which in a freshly made clone created files nobody
asked for.)

Usage:
    python3 config_work/draw_remote_svg.py            # writes app/ui/remote/remote.svg
    python3 config_work/draw_remote_svg.py --stdout   # prints it, writes nothing
    python3 config_work/draw_remote_svg.py --salida otro.svg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TECLAS_JSON = RAIZ / "app" / "ui" / "remote" / "keys.json"
SALIDA_SVG = RAIZ / "app" / "ui" / "remote" / "remote.svg"


CUERPO_RELLENO = "#24272c"
CUERPO_BORDE = "#4b515a"
TECLA_RELLENO = "#33383f"
TECLA_BORDE = "#5b626c"
LCD_RELLENO = "#171a1e"
LCD_BORDE = "#5b626c"
TEXT = "#9aa3ae"


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def construir(keys_json: dict) -> str:
    """The complete SVG, as text."""
    width = int(keys_json["crop_width_px"])
    height = int(keys_json["crop_height_px"])
    keys = keys_json["keys"]
    lcd = keys_json["lcd"]["box_px"]

    # The body: the extent of all the boxes, plus a margin. Derived, not
    # invented -- if some day a key gets re-measured, the body follows it.
    xs = [c for t in keys for c in (t["box_px"][0], t["box_px"][2])] + [
        lcd[0],
        lcd[2],
    ]
    ys = [c for t in keys for c in (t["box_px"][1], t["box_px"][3])] + [
        lcd[1],
        lcd[3],
    ]
    mx, my = 16.0, 22.0
    bx0 = max(0.0, min(xs) - mx)
    bx1 = min(float(width), max(xs) + mx)
    by0 = max(0.0, min(ys) - my)
    by1 = min(float(height), max(ys) + my)

    p: list[str] = []
    p.append(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d" role="img" '
        'aria-label="Schematic drawing of the remote, from the measured button coordinates">'
        % (width, height, width, height)
    )
    p.append(
        "<title>The remote, drawn from measured coordinates "
        "(app/ui/remote/keys.json). Not a photograph.</title>"
    )
    p.append(
        '<defs><linearGradient id="cuerpo" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#2b2f35"/>'
        '<stop offset="1" stop-color="#1d2025"/></linearGradient></defs>'
    )
    # cuerpo
    p.append(
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="26" '
        'fill="url(#cuerpo)" stroke="%s" stroke-width="1.4"/>'
        % (bx0, by0, bx1 - bx0, by1 - by0, CUERPO_BORDE)
    )
    # LCD
    p.append(
        '<rect x="%d" y="%d" width="%d" height="%d" rx="3" fill="%s" '
        'stroke="%s" stroke-width="1"/>'
        % (lcd[0], lcd[1], lcd[2] - lcd[0], lcd[3] - lcd[1], LCD_RELLENO, LCD_BORDE)
    )
    # teclas
    for t in keys:
        x0, y0, x1, y1 = t["box_px"]
        etq = _esc(str(t.get("label") or ""))
        if t.get("forma") == "circle":
            p.append(
                '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" '
                'stroke="%s" stroke-width="1"><title>%s</title></ellipse>'
                % (
                    (x0 + x1) / 2,
                    (y0 + y1) / 2,
                    (x1 - x0) / 2,
                    (y1 - y0) / 2,
                    TECLA_RELLENO,
                    TECLA_BORDE,
                    etq,
                )
            )
        else:
            p.append(
                '<rect x="%d" y="%d" width="%d" height="%d" rx="5" fill="%s" '
                'stroke="%s" stroke-width="1"><title>%s</title></rect>'
                % (x0, y0, x1 - x0, y1 - y0, TECLA_RELLENO, TECLA_BORDE, etq)
            )
        # Only the labels that really fit (the numeric keypad and
        # little else). No clipped text: if it does not fit, it is not drawn.
        crudo = str(t.get("label") or "")
        if len(crudo) <= 2:
            p.append(
                '<text x="%.1f" y="%.1f" text-anchor="middle" '
                'font-family="system-ui, -apple-system, sans-serif" '
                'font-size="%.1f" fill="%s">%s</text>'
                % (
                    (x0 + x1) / 2,
                    (y0 + y1) / 2 + (y1 - y0) * 0.17,
                    min(15.0, (y1 - y0) * 0.52),
                    TEXT,
                    _esc(crudo),
                )
            )
    p.append("</svg>")
    return "\n".join(p) + "\n"


def from_file(keys_json_path: Path) -> str:
    return construir(json.loads(Path(keys_json_path).read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--keys",
        type=Path,
        default=TECLAS_JSON,
        help="keys.json with the measured boxes (default: app/ui/remote/keys.json)",
    )
    ap.add_argument(
        "--salida",
        type=Path,
        default=SALIDA_SVG,
        help="where to write the SVG (default: app/ui/remote/remote.svg)",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="print the SVG instead of writing any file",
    )
    a = ap.parse_args(argv)

    if not a.keys.is_file():
        print("no encuentro %s" % a.keys, file=sys.stderr)
        return 2
    svg = from_file(a.keys)
    if a.stdout:
        sys.stdout.write(svg)
        return 0
    a.salida.parent.mkdir(parents=True, exist_ok=True)
    a.salida.write_text(svg, encoding="utf-8")
    datos = json.loads(a.keys.read_text(encoding="utf-8"))
    print(
        "escrito %s  (%d bytes, %d teclas, viewBox 0 0 %s %s)"
        % (
            a.salida,
            len(svg),
            len(datos["keys"]),
            datos["crop_width_px"],
            datos["crop_height_px"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
