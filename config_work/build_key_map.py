#!/usr/bin/env python3
"""Regenerates the remote's KEY MAP -- `keys.json`: the 44 clickable zones
plus the LCD rectangle -- and, only if you hand it one, a photographic
background to sit underneath them.

The key map is the part the rest of the project consumes:
`config_work/draw_remote_svg.py` reads it to draw `app/ui/remote/remote.svg`,
`config_work/keys_photo.py` reads it to resolve each zone's command, and
`app/ui/app.js` reads it to lay the clickable layer on top of the drawing.
**None of that needs an image.** The coordinates live in the `ZONES` table
below as measured constants; nothing here detects a button in pixels. So the
script runs, and regenerates the key map, on a bare clone with no image files
of any kind (verified: `build_model()` never opens an image).

Outputs:

    graphics/mando/keys.json       44 zones + LCD rectangle      always
    app/ui/remote/keys.json         the copy the app loads        always
    graphics/mando/verificacion.png  visual check: zones outlined  needs Pillow
    graphics/mando/mando-real.png    cropped, upscaled photo       only with --photo
    app/ui/remote/mando-real.png      the copy the app loads        only with --photo

Pillow is a soft dependency and is NOT in `app/pyproject.toml`: with a bare
`uv sync` the key map still regenerates, and only the two image outputs are
skipped, with a message instead of a traceback.

## The photo is an OPTIONAL input, and it is not part of this repository

The zones were originally measured over a catalog photograph of the remote
(`logi.png`, 296x920). That photograph is someone else's copyrighted work: it
is not distributed here, and this script does not require it. With no photo
the 44 zones, the LCD rectangle and every key code come out exactly the same,
and `verificacion.png` draws them over a plain background, so the map can
still be checked by eye.

The three fields that describe the PHOTO pipeline -- `source` as a filename,
`recorte` and `metodo_agrandado` -- are the only difference: with no photo
they are not written at all (nothing was cropped and nothing was upscaled),
`source` says where the coordinates came from instead, and `imagen` points at
`remote.svg`, the drawing the app ships. Running this script therefore never
puts a reference to an undistributed photograph back into `keys.json`.

If you want a photographic background, supply your own image:

    python3 build_key_map.py --photo my_remote.png --crop 25,0,271,907

`--crop` matters: the default crop frames the remote's body inside the
REFERENCE photo, and your image will not have the remote in the same pixels.
Pass the box that frames the body in YOUR image. The zone coordinates are
stored as a PERCENTAGE of the cropped image, so they survive any crop that
frames that same body, and any `--scale`.

## The reference crop

The reference photo comes with a white background. The remote's body (every
pixel that is not near-white) measures `(28,2)-(268,904)`; it is cropped to
`(25,0)-(271,907)` to leave 3 px of breathing room so the chrome edge doesn't
get cut off.

## The upscaling, no smoke and mirrors

**This script does no AI super-resolution, and imports nothing that could**:
the only image dependency is Pillow (see the imports -- no `cv2`, no `torch`,
no `realesrgan`/`waifu2x`/`upscayl`). What it does is honest and states
itself: **Lanczos x3 + unsharp mask**. That upscales and recovers edge
contrast; **it does not invent detail the photo doesn't have**. On the
reference photo the small silkscreen text ("Replay", "Skip", "more"/"clear")
stays illegible at any zoom, because in the original it measures 4 px tall.
An earlier round had delivered a x1.087 (296x920 -> 322x1000) calling it
"upscale"; this is a real x3 (246x907 -> 738x2721), measured, not claimed.

## Where each key's coordinates come from

Measured by hand on the photo with a 10 px grid overlaid (zoomed 4x-5x),
and the five most delicate pieces additionally with an objective
brightness detector (threshold + bbox), not eyeballed:

    LCD (content area)         (56,78)-(191,247)  136x170 -> aspect 0.8000
                                POSITIVE CHECK: the real panel is 176x220,
                                176/220 = 0.8000. They match to the 4th digit.
    left paging arrow           white triangle (40,177)-(48,200)
    right paging arrow          white triangle (201,174)-(208,198)
    bottom-left softkey bar     white bar (53,257)-(119,263)
    bottom-right softkey bar    white bar (129,256)-(195,263)

The two arrows fall OUTSIDE the LCD's content area (which starts at x=56
and ends at x=191): they are physical bezel keys, backlit -- exactly what
ESTADO.md says ("the physical paging keys light up"). The two white bars
fall BELOW the LCD (y=257 against the bottom edge y=247): they are the two
physical softkeys, and are NOT swallowed inside the screen rectangle.

## The `codigo` field

Each zone carries the remote's key code (`0x81`..`0xB7`) when it could be
TIED TO EVIDENCE, and `null` when it couldn't. The evidence is not
geometric: it comes from `teclas_foto.command_names()`, which
resolves each row's command from the keyboard contexts using the
firmware's arithmetic (`device.resolve_section5`) and cross-checks
it against `backups/command_table.json`, which carries the command's NAME
("VolumeUp", "Number7", "DirectionLeft"). Cross-check: the two configured
Activity contexts (TV HD, which drives the DVR, and PC, which
drives the Sony TV) declare 34 codes in common, and in 31 of those 34 the
command NAME is identical; the 3 that differ are synonyms for the same
spot (`Exit`/`Return`, `Info`/`Display`, `Menu`/`Home`). Two different
devices, two different activities, the same reading.

The `[ASSUMED]` items are marked one by one in `NOTE` and there are four
of them: `pag_arriba`/`pag_abajo` (codes 0xA4/0x9D repeat
DirectionUp/Down), `num_mas` (the command is called `Dot`), and
`activities` (0xB7 jumps to a page, it doesn't emit a command).

`power` (0xA5) is NOT in that list: it's measured, not assumed -- it's
the only code in the 14-row GLOBAL keymap (`[10][1]`) that carries class
`0x1F` ("enters an Activity"), and its target is context 9 ("All Off").

`num_e` ("E") stays completely unassigned (`codigo: None`) on purpose:
of the 9 leftover inventory codes, none resolves to a real command via
section [5] in either configured Activity, and the one with a real
distinguishing behavior (`0xA6`, "opens another screen" in both
Activities) fits the still-unmapped `help` button better than `num_e`.
Tying a leftover code to `num_e` would be exactly the invented
assignment this project avoids -- see `num_e`'s own `nota` for the
per-code breakdown.

Does not touch the device. Does not import `write.py`. Needs no config blob,
no backup and no image. Usage:

    python3 build_key_map.py             # key map (+ check), no photo
    python3 build_key_map.py --photo P   # ... and the photo background
    python3 build_key_map.py --no-copy   # doesn't touch app/ui/remote/

NOTE ON NAMING: the dict keys written to `keys.json` (`label`,
`forma`, `codigo`, `nota`, `box_px`, `panel_ancho`, `panel_alto`,
`metodo_agrandado`, `crop_width_px`, `crop_height_px`, `ancho_px`,
`alto_px`, `lcd`, `keys`, `recorte`, `escala`, `source`, `imagen`) are
a JS-crossing contract: `app/ui/app.js` (see `teclasPintarMando()`) reads
them by name, so renaming a KEY without updating `app.js` in lockstep
would silently break the "Keys" screen. The keys therefore stay as they
are.

The human-facing label/note VALUES (`label`, `nota`, and
`metodo_agrandado`) are the opposite case: they are UI copy shown to the
app's user and are in English. Nothing compares them -- `app.js` only
concatenates them into the SVG `<title>`/`aria-label` and the detail
panel -- so they are safe to reword. `metodo_agrandado` is the one
exception to watch: `app.js` splits it on `--` and displays only the left
half, so keep that separator.

Whoever edits these VALUES must rewrite all three copies together:
this file, `graphics/mando/keys.json`, and `app/ui/remote/keys.json`
(the last is the copy the app actually serves).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil

#: Pillow is a SOFT dependency, and it is not in `app/pyproject.toml`: the key
#: map is arithmetic and needs no image library at all, so a clone with a bare
#: `uv sync` has to be able to regenerate it. Only the two image outputs need
#: Pillow, and only those are skipped when it is missing -- with a message,
#: never with a traceback.
try:
    from PIL import Image, ImageDraw, ImageFilter
except ModuleNotFoundError:
    Image = ImageDraw = ImageFilter = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "graphics" / "remote"
DEST_APP = ROOT / "app" / "ui" / "remote"

#: Size of the catalog photograph the zones were measured on (`logi.png`),
#: kept so `--photo` can tell you when the default crop cannot possibly apply
#: to the image you passed. The photograph itself is somebody else's
#: copyrighted work: it is NOT distributed with this project and nothing here
#: opens it -- these are the two numbers that survive it.
REFERENCE_SIZE = (296, 920)

#: crop of the remote's body inside that reference photo (non-white bbox + 3 px).
#: Override with `--crop x0,y0,x1,y1` for a different image.
CROP = (25, 0, 271, 907)
SCALE = 3

#: zoom used only for `verificacion.png`, so the labels stay legible. It is
#: independent of `SCALE`: the check is a check, not a deliverable.
VERIFY_ZOOM = 3

#: LCD CONTENT area, measured by saturation (not the bezel).
#: 136x170 -> 0.8000, and the real panel is 176x220 -> 0.8000.
LCD = (56, 78, 192, 248)  # x0, y0, x1, y1 in crop coordinates
PANEL = (176, 220)

# ---------------------------------------------------------------------------
# The 44 zones. (x0, y0, x1, y1) in crop coordinates; `forma`/shape is how
# the highlight is drawn in the app; `codigo`/code is the remote's key byte.
# Tuple fields stay in Spanish below only where they become dict keys/values
# read by app.js -- see the naming note in the module docstring.
# ---------------------------------------------------------------------------
ZONES = [
    # id, etiqueta (label), forma (shape), caja (box), codigo (code), nota (note)
    (
        "power",
        "Power",
        "rrect",
        (34, 26, 58, 52),
        0xA5,
        "turns everything off: it enters the All Off activity, it does not send a command",
    ),
    (
        "pag_izq",
        "Previous page",
        "rrect",
        (34, 170, 54, 207),
        None,
        "physical paging key (it lights up); it pages the screen, it does not emit IR",
    ),
    (
        "pag_der",
        "Next page",
        "rrect",
        (195, 167, 215, 204),
        None,
        "physical paging key (it lights up); it pages the screen, it does not emit IR",
    ),
    ("softkey_izq", "Left softkey", "rrect", (51, 251, 121, 269), 0xAB, ""),
    ("softkey_der", "Right softkey", "rrect", (127, 250, 197, 268), 0xAC, ""),
    (
        "activities",
        "Activities",
        "rrect",
        (27, 299, 119, 327),
        0xB7,
        "[ASSUMED] opens the activities screen (page jump), it does not emit IR",
    ),
    ("help", "Help", "rrect", (130, 299, 222, 327), None, ""),
    ("menu", "Menu", "rrect", (26, 338, 92, 368), 0xA8, ""),
    (
        "pag_arriba",
        "Up (page)",
        "rrect",
        (106, 337, 142, 369),
        0xA4,
        "[ASSUMED] its command is DirectionUp, the same as the D-pad arrow",
    ),
    ("info", "Info", "rrect", (156, 338, 222, 368), 0x8A, ""),
    ("exit", "Exit", "rrect", (26, 372, 92, 402), 0x82, ""),
    (
        "pag_abajo",
        "Down (page)",
        "rrect",
        (106, 371, 142, 403),
        0x9D,
        "[ASSUMED] its command is DirectionDown, the same as the D-pad arrow",
    ),
    ("guide", "Guide", "rrect", (156, 372, 222, 402), 0x92, ""),
    ("vol_mas", "Volume +", "rrect", (37, 415, 74, 466), 0x83, ""),
    ("vol_menos", "Volume -", "rrect", (37, 466, 74, 519), 0x84, ""),
    ("ch_mas", "Channel +", "rrect", (174, 415, 209, 466), 0x93, ""),
    ("ch_menos", "Channel -", "rrect", (174, 466, 209, 519), 0x94, ""),
    ("dpad_arriba", "D-pad up", "rrect", (104, 418, 146, 451), 0x9B, ""),
    ("dpad_izq", "D-pad left", "rrect", (74, 451, 107, 486), 0x8B, ""),
    ("ok", "OK", "circle", (106, 450, 144, 488), 0x9C, ""),
    ("dpad_der", "D-pad right", "rrect", (144, 451, 175, 486), 0xA1, ""),
    ("dpad_abajo", "D-pad down", "rrect", (104, 486, 146, 518), 0x9A, ""),
    ("mute", "Mute", "circle", (78, 516, 106, 552), 0x89, ""),
    ("prev", "Previous channel", "circle", (140, 516, 168, 552), 0xA3, ""),
    ("rebobinar", "Rewind", "rrect", (33, 561, 88, 587), 0x85, ""),
    ("avanzar", "Fast forward", "rrect", (156, 561, 211, 587), 0x95, ""),
    ("play", "Play", "rrect", (101, 555, 143, 617), 0x9E, ""),
    ("replay", "Replay", "rrect", (33, 598, 88, 624), 0x86, ""),
    ("skip", "Skip", "rrect", (156, 598, 211, 624), 0x96, ""),
    ("grabar", "Record", "rrect", (31, 630, 94, 667), 0x87, ""),
    ("pausa", "Pause", "rrect", (108, 621, 144, 668), 0x9F, ""),
    ("stop", "Stop", "rrect", (162, 630, 214, 667), 0x97, ""),
    ("num_1", "1", "circle", (48, 671, 82, 706), 0x88, ""),
    ("num_2", "2", "circle", (107, 678, 141, 713), 0xA0, ""),
    ("num_3", "3", "circle", (166, 671, 200, 706), 0x98, ""),
    ("num_4", "4", "circle", (48, 717, 82, 752), 0x81, ""),
    ("num_5", "5", "circle", (107, 723, 141, 758), 0x99, ""),
    ("num_6", "6", "circle", (166, 717, 200, 752), 0x91, ""),
    ("num_7", "7", "circle", (48, 762, 82, 797), 0xA7, ""),
    ("num_8", "8", "circle", (107, 769, 141, 804), 0x90, ""),
    ("num_9", "9", "circle", (166, 762, 200, 797), 0x8F, ""),
    (
        "num_mas",
        "+",
        "circle",
        (61, 805, 93, 840),
        0x8C,
        "[ASSUMED] its command is called Dot (the keypad separator). "
        "Reinforced: once 0-9 are each tied to their own NumberN command, "
        "0x8C is the ONLY leftover inventory code that still resolves, via "
        "section [5] in a configured Activity (PC), to a real command -- "
        "every other leftover code resolves to nothing. The code->command "
        "link is measured; only the code->silkscreen link is assumed.",
    ),
    ("num_0", "0", "circle", (102, 810, 148, 848), 0x8E, ""),
    (
        "num_e",
        "E",
        "circle",
        (160, 805, 192, 840),
        None,
        "not editable: no code in the 55-entry inventory can be tied to "
        "this key. Of the 9 leftover physical-candidate codes: 0x06/0x07 "
        "are firmware screen hooks, not keys; 0xAD/0xAE/0xAF are Devices-"
        "menu strip placeholders (the same 'franjas' ESTADO.md ties to "
        "menu paging); 0x2D only toggles the remote's own backlight; 0xA2 "
        "only sets an internal property in the All-Off screen; 0xA6 opens "
        "another screen in both configured Activities but fits the still-"
        "unmapped Help button (same behavior, same on-screen neighbor) "
        "better than E; and 0x8D never gets bound to anything in either "
        "configured Activity -- it only shares the generic standby "
        "placeholder every other unbound key shares there. None resolves "
        "to a real command via section [5], so none is assigned here.",
    ),
]


def _strip_background(im: Image.Image) -> Image.Image:
    """Removes the catalog's white background, leaving the cropped remote
    body with transparency, so the photo sits well on the app's color in
    light AND dark mode (with the white background baked in, in dark mode
    you'd see a white rectangle around the remote).

    The cutout is a flood fill from the four edges over near-white pixels:
    **not** a global threshold. That way the two white softkey bars and the
    chrome highlight, which are SURROUNDED by black, are left untouched --
    exactly the mistake a global threshold would make.
    """
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()

    def is_white(x, y):
        r, g, b, _a = px[x, y]
        return r > 228 and g > 228 and b > 228

    background = bytearray(w * h)
    stack = [(x, y) for x in range(w) for y in (0, h - 1) if is_white(x, y)]
    stack += [(x, y) for y in range(h) for x in (0, w - 1) if is_white(x, y)]
    for x, y in stack:
        background[y * w + x] = 1
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (
                0 <= nx < w
                and 0 <= ny < h
                and not background[ny * w + nx]
                and is_white(nx, ny)
            ):
                background[ny * w + nx] = 1
                stack.append((nx, ny))

    alpha = Image.new("L", (w, h), 255)
    ap = alpha.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            if background[row + x]:
                ap[x, y] = 0
    # half a pixel of smoothing so the edge doesn't come out jagged on upscale
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    im.putalpha(alpha)
    return im


def crop_and_upscale(photo: pathlib.Path, crop=CROP, scale: int = SCALE) -> Image.Image:
    """Crops the remote's body out of `photo` and upscales it. Only reached
    when the user supplied a photo -- see the module docstring."""
    im = Image.open(photo).convert("RGB").crop(crop)
    w, h = im.size
    im = _strip_background(im)
    upscaled = im.resize((w * scale, h * scale), Image.LANCZOS)
    # gentle unsharp mask: recovers the edge that Lanczos interpolates away.
    # It does NOT invent detail -- it only makes legible what was already there.
    rgb = upscaled.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=2.0, percent=110, threshold=3)
    )
    rgb.putalpha(upscaled.getchannel("A"))
    return rgb


def build_model(crop=CROP, scale: int = SCALE, source_name: str | None = None) -> dict:
    """The key map. Pure arithmetic over the `ZONES` table: it opens no file,
    reads no image and touches no device, which is why a clone with no photo
    regenerates the whole map anyway.

    `source_name` is the name of the photograph the background was built from,
    or `None` when no photograph took part -- see the branch below."""
    w = crop[2] - crop[0]
    h = crop[3] - crop[1]

    def pct(box):
        x0, y0, x1, y1 = box
        return {
            "x": round(100.0 * x0 / w, 3),
            "y": round(100.0 * y0 / h, 3),
            "width": round(100.0 * (x1 - x0) / w, 3),
            "height": round(100.0 * (y1 - y0) / h, 3),
        }

    keys = []
    for ident, label, shape, box, code, note in ZONES:
        t = {"id": ident, "label": label, "forma": shape}
        t.update(pct(box))
        t["codigo"] = ("0x%02X" % code) if code is not None else None
        t["nota"] = note
        t["box_px"] = list(box)
        keys.append(t)

    lcd = pct(LCD)
    lcd.update(
        {
            "id": "screen",
            "label": "LCD screen",
            "panel_ancho": PANEL[0],
            "panel_alto": PANEL[1],
            "box_px": list(LCD),
        }
    )
    # The three fields that describe the PHOTO pipeline -- `source` as a
    # filename, `recorte`, `metodo_agrandado` -- are written only when a photo
    # actually went through it. With no photo they would be a lie, and worse:
    # they would point the reader of a clone at an image the clone does not
    # have. So the photo-free model says what it is and points `imagen` at the
    # drawing the app ships.
    if source_name is None:
        head = {
            "source": "coordinates measured on the remote; the background "
            "drawing is generated by config_work/draw_remote_svg.py -- "
            "no photograph is distributed",
            "escala": scale,
            "imagen": "remote.svg",
        }
    else:
        head = {
            "source": source_name,
            "recorte": list(crop),
            "escala": scale,
            "metodo_agrandado": "Lanczos x%d + UnsharpMask(2.0, 110%%, 3) -- "
            "interpolation, NOT super-resolution (no cv2/torch/realesrgan "
            "on this machine)" % scale,
            "imagen": "mando-real.png",
        }
    head.update(
        {
            "crop_width_px": w,
            "crop_height_px": h,
            "ancho_px": w * scale,
            "alto_px": h * scale,
            "lcd": lcd,
            "keys": keys,
        }
    )
    return head


def render_verification(
    m: dict, photo: pathlib.Path | None = None, crop=CROP, f: int = VERIFY_ZOOM
) -> Image.Image:
    """Visual check: each zone with an outline (NOT an opaque fill: a fill
    would cover up exactly what needs to be verified) and its label next to it.

    With `photo`, the outlines go over the cropped image, which is what lets
    you confirm that a box lands on its button. Without one -- the normal case
    in a clone, where no photograph is distributed -- they go over a plain
    background, which still shows the layout, the sizes and which keys have a
    code tied to them."""
    w = crop[2] - crop[0]
    h = crop[3] - crop[1]
    if photo is not None:
        im = Image.open(photo).convert("RGB").crop(crop)
        im = im.resize((im.width * f, im.height * f), Image.LANCZOS).convert("RGBA")
    else:
        im = Image.new("RGBA", (w * f, h * f), (24, 26, 30, 255))
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = (v * f for v in m["lcd"]["box_px"])
    d.rectangle([x0, y0, x1, y1], outline=(255, 80, 255, 255), width=3)
    d.text((x0 + 4, y0 + 4), "LCD 176x220", fill=(255, 80, 255, 255))
    for t in m["keys"]:
        cx0, cy0, cx1, cy1 = (v * f for v in t["box_px"])
        col = (0, 255, 120, 255) if t["codigo"] else (255, 170, 0, 255)
        if t["forma"] == "circle":
            d.ellipse([cx0, cy0, cx1, cy1], outline=col, width=3)
        else:
            d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=6, outline=col, width=3)
        label = t["label"] + (" " + t["codigo"] if t["codigo"] else " --")
        tx = cx1 + 4 if cx1 < w * f * 0.5 else cx0 - 4 - 6 * len(label)
        d.text((max(2, tx), (cy0 + cy1) / 2 - 6), label, fill=col)
    return Image.alpha_composite(im, layer).convert("RGB")


def _parse_crop(text: str):
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected x0,y0,x1,y1")
    try:
        x0, y0, x1, y1 = (int(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("expected four integers: x0,y0,x1,y1")
    if x1 <= x0 or y1 <= y0:
        raise argparse.ArgumentTypeError("empty box: needs x1>x0 and y1>y0")
    return (x0, y0, x1, y1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--photo",
        metavar="PATH",
        help="optional photograph of the remote to use as the background. "
        "None ships with this project; without it only the key map is "
        "regenerated.",
    )
    ap.add_argument(
        "--crop",
        metavar="X0,Y0,X1,Y1",
        type=_parse_crop,
        default=CROP,
        help="box that frames the remote's body inside --photo "
        "(default: %s, measured on the reference photo)" % (CROP,),
    )
    ap.add_argument("--scale", metavar="N", type=int, default=SCALE)
    ap.add_argument("--no-copy", action="store_true")
    a = ap.parse_args()

    if a.scale < 1:
        ap.error("--scale must be >= 1")

    photo = pathlib.Path(a.photo).expanduser() if a.photo else None
    if photo is not None and not photo.is_file():
        ap.error("--photo: no such file: %s" % photo)
    if photo is not None and Image is None:
        ap.error("--photo needs Pillow, which is not installed: pip install pillow")

    crop, scale = a.crop, a.scale
    if photo is not None:
        size = Image.open(photo).size
        if a.crop == CROP and size != REFERENCE_SIZE:
            print(
                "warning: --photo is %dx%d but the default crop %s was measured "
                "on a %dx%d photo. Pass --crop with the box that frames the "
                "remote in YOUR image, or the crop will land anywhere."
                % (size[0], size[1], CROP, REFERENCE_SIZE[0], REFERENCE_SIZE[1])
            )
        if crop[2] > size[0] or crop[3] > size[1]:
            ap.error(
                "--crop %s falls outside the image (%dx%d)" % (crop, size[0], size[1])
            )

    DEST.mkdir(parents=True, exist_ok=True)
    m = build_model(
        crop=crop,
        scale=scale,
        source_name=photo.name if photo is not None else None,
    )
    # With a trailing newline, and that is not cosmetic: the `keys.json` that
    # ships in the public repo is written by the export, with the newline, and
    # this script is the tool a cloner uses to redo it. Without the newline the
    # two outputs differed by one byte, and the `git diff` of a clone that
    # regenerated the map showed a changed line that changed nothing.
    (DEST / "keys.json").write_text(
        json.dumps(m, indent=1, ensure_ascii=False) + "\n"
    )
    if Image is not None:
        render_verification(m, photo=photo, crop=crop).save(DEST / "verificacion.png")
    if photo is not None:
        crop_and_upscale(photo, crop=crop, scale=scale).save(
            DEST / "mando-real.png", optimize=True
        )

    if not a.no_copy:
        DEST_APP.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEST / "keys.json", DEST_APP / "keys.json")
        if photo is not None:
            shutil.copy2(DEST / "mando-real.png", DEST_APP / "mando-real.png")

    if photo is None:
        print("photo      none given -- key map only (see --photo)")
    else:
        print("photo      %s  %dx%d" % (photo, *Image.open(photo).size))
    if Image is None:
        print(
            "Pillow     not installed -- verificacion.png skipped. The zones "
            "can still be seen in the drawing: config_work/draw_remote_svg.py"
        )
    print("crop       %dx%d  %s" % (m["crop_width_px"], m["crop_height_px"], crop))
    print("output     %dx%d  (x%d)" % (m["ancho_px"], m["alto_px"], scale))
    print(
        "keys       %d  (%d with a code tied, %d untied)"
        % (
            len(m["keys"]),
            sum(1 for t in m["keys"] if t["codigo"]),
            sum(1 for t in m["keys"] if not t["codigo"]),
        )
    )
    lx = m["lcd"]["box_px"]
    print(
        "LCD        %dx%d px in crop -> aspect %.4f (real panel %d/%d = %.4f)"
        % (
            lx[2] - lx[0],
            lx[3] - lx[1],
            (lx[2] - lx[0]) / (lx[3] - lx[1]),
            PANEL[0],
            PANEL[1],
            PANEL[0] / PANEL[1],
        )
    )
    print("-> %s" % (DEST / "keys.json"))
    if Image is not None:
        print("-> %s" % (DEST / "verificacion.png"))
    if photo is not None:
        print("-> %s" % (DEST / "mando-real.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
