#!/usr/bin/env python3
"""FULL ADDITION of a device on the Harmony One, TOUCHING the row opens
its commands (no paging).

## ROUND 3 -- ALL commands, spread across sub-screens

Round 2 (grabbed as `output/philips_ir_fix.bin`, **verified on the device
by the user**) left the commands screen at N=1 with SIX buttons, even
though the Philips's 32 IR records were already in the blob. This round
puts all of them on screen. What changes, and why:

  1. **The commands screen moves to N=6.** Same trailer
     `<flag=0><header ptr24><u16 N><N x slot ptr24>` the 14 factory
     multi-page objects use. Since N>1 now, its header **also** cannot
     declare `0xAE`/`0xAF` (census: 142/142 with N=1 declare them, 0/14
     with N>1); 74's header is copied WITHOUT them, same as already done
     for the menu's 3. The side strips -- with their physical keys already
     lit -- move on to paging.
  2. **The last page uses K=32, not an under-populated K=5.** 32 commands
     / 6 per screen = 5 pages of 6 + 1 of 2. Template K=32 is literally
     K=5 without the middle and bottom rows (its 5 zones have rectangles
     byte for byte identical to K=5's matching ones, verified in section
     [19]) and the factory uses it for exactly that in `table[6][142]`
     slot 2: 2 bitmaps on top + the 'Devices' softkey. A K=5 with 4 zones
     left undeclared has no precedent (0/156 at the factory for K in
     {4,5,25,29,32}) and would have needed a test grab just to see
     whether ghost buttons show up.
  3. **Labels get CENTERED.** `GRILLA` nailed the text's X at 28/111. The
     factory centers: `x = floor(C - width/2)` with C=46.5 (left column)
     and 129.5 (right), where `width` is the sum of each glyph's bitmap
     byte 0. The rule reproduces **14/14** of the ATTR 9 labels across
     `table[6][142]`'s three sub-screens -- the same anchor `GRILLA` came
     from -- and cross-checking gives 37 px for "Philips", the number
     that was already noted. With the X nailed down, a digit (6 px) came
     out shifted by 15 px.
  4. **The page indicator comes back, but cloned from the factory.** Round
     1's bug (A) correctly diagnosed the symptom (the sequence of 4 text
     pointers in the same program does NOT exist at the factory) and only
     half-diagnosed the cause: the real indicator is **split across two
     programs**, the total in the PROLOGUE (`ATTR <a> TXT(23,18)=<total>
     TXT(35,18)='pages'`) and the number in each SLOT (`ATTR <a>
     TXT(13,18)=<n> TXT(18,18)='/'`). Measured on the factory's 4
     multi-page objects: 103 (N=6, ATTR 16), 140 (N=10, ATTR 16), 69
     (N=10, ATTR 12) and 142 (N=3, ATTR 7). Since the new screen also has
     6 pages, ordinal **103**'s is cloned whole: same attributes, same
     X/Y, and **the same text pointers** -- not a single new string is
     written. (ATTR 7, the one 142 uses, only has digits 1-3: that's why
     it doesn't work here.)
  5. **The 64 KB gate now looks at the REAL length.** It used to check
     `(record+11, 4)`: 4 of the record's 25 bytes. Now it covers the
     record's full 25, the whole `press|00 00|hold|reg` unit, the
     trailers, the slots, the key registers, the headers, the sub-table,
     section [5] and tabla[6]; and it emits them ALIGNED (padding before
     if something were about to cross a boundary).
  6. `arrow_backlight.py` also gets the new ordinal, so the lighting stays
     DECLARED instead of depending on its header still being a copy of 74's.

What does NOT change (and the checks verify it against round 2's output):
the three menu objects 74/90/141, sheet 2, the jump object, page 1's key
register (byte for byte), and the 6 (cmd_id, dev_id) pairs already tested
emitting IR at the television.

## ROUND 2 (historical)

The round before that one. That round (grabbed as
`output/philips_franjas.bin`, tested on the device) had the commands
screen as the SAME Devices menu object's **sub-screen 3**: it was reached
by paging with the side strips, which is a DESIGN MISTAKE -- the user put
it this way: *"if I touch again to turn the page it's like I'm entering
some device and that's wrong because this is the devices page"*. That
round also had two visual bugs on sheet 2 (the name cut short to "Phili"
+ one extra line), diagnosed and fixed here (see "BUGS FOUND AND WHY",
below).

## THE NEW DESIGN

  1. The THREE Devices menu objects (`table[6][74|90|141]`) end up at
     **N=2**: sheet 1 = the same 3 devices as always (ORIGINAL slot and
     `keyreg`, not a single byte touched -- not even copied), sheet 2 =
     ONE new row (large icon + small + name) at the top position. The
     side strips keep paging between those two sheets (mechanism already
     verified on the device: removing `0xAE`/`0xAF` from the header).
  2. TOUCHING the Philips row on sheet 2 opens the commands screen --
     **copying the exact pattern the factory uses** for page 74's "TV"
     row to open page 103: the row's zone carries `{id, 0x7F}` in its key
     register, and `id` is a 10 B object `<03><{tipo,0x75}>
     <{ordinal,0x7E}><{1,0x9A}>` (verified byte for byte against the real
     object at `0x29d36`). `{ordinal,0x7E}` is exactly the "atom" that
     jumps screens, and the `ordinal` it uses is a **new** one: the
     commands screen becomes its own `table[6]` ordinal, extended from
     156 to 157 entries, repointing its master-index entry (`0x0c + 4*6`).
     The two alternatives the task asked to investigate were both looked
     at before choosing this one: the factory does NOT use the
     sub-screen mechanism (N>1) for this -- 74/90/141's rows jump to
     COMPLETELY DIFFERENT `table[6]` ordinals (78, 103, 140), each with
     its own internal N>1 chain that has nothing to do with the menu.
     The verified path is "new ordinal + `{ordinal,0x7E}`", exactly what
     the task asked for.
  3. Only ONE commands ordinal gets created (not one per each of the 3
     menu objects): the header/prologue reused is `objs[0]`'s (ordinal
     74), and all three sheet-2s (74/90/141) point at the SAME jump
     object and the SAME commands ordinal. Nothing stops a pointer from
     crossing from one `table[6]` object to another -- it's exactly what
     the factory already does on page 74 itself (its prologue draws the
     "Devices" title and that same prologue gets re-CALLed for sheet 2
     and the commands screen).

EVERYTHING gets appended at the end. **No byte of the original body
moves.** The only old-body bytes that change are:

    [0x04:0x07]        the closing pointer                 (PERMITIDOS)
    [0x30:0x3C]        master index for [9], [10] and [11]  (PERMITIDOS)
    [0x24:0x27]        master index for [6] (tabla[6])       (--repunta)

Only one repoint -- much less than the previous round's (3), because now
`table[6]` gets relocated WHOLE (like [9]/[10]/[11] already were) instead
of overwriting its 3 entries in their original spot.

## BUGS FOUND AND WHY (previous round, with evidence from the real blob)

**(A) The "page indicator" this tool drew was FALSE.**
`indicador(page, total)` emitted `10 0C` + 4 `TEXT_PTR` (digit,
separator, total, "pages"). The real pattern was searched for in the blob
(33 occurrences of `10 0c 04 0d 12`, and the full disassembly of a real
N=3 page, `table[6][142]` slot 0) and the real pattern is ONLY `10
<attr>` + 2 `TEXT_PTR` (digit, separator) -- **never** a third/fourth
pointer for "total" and "pages". Those two pointers (`TXT_DIG[3]`,
`TXT_PAGES`) pointed at real blob data ("3" and "pages", verified), but
the SEQUENCE of 4 pointers in the same program doesn't exist anywhere at
the factory: it's invented content. On top of that, the original page 1
was getting a "stub" that also drew it. Both are **removed**: none of the
pages this tool generates draws an indicator (sheet 1 isn't even
touched; sheet 2 and the commands screen are N=1 from their own point of
view -- nothing to page, nothing to indicate).

**(B) Sheet 2 jumped to `pie`, and `pie` is a SWITCH, not an inert tail.**
The previous version ended sheet 2's program with `JMP pie`, where `pie`
is the offset right after the ORIGINAL object's 3rd row. That spot was
disassembled by hand: it's a `0x12` opcode (SWITCH) over a runtime
selector (`sel=0x25`), with two cases that draw text at `Y≈196-207` (near
the screen's footer) before returning to the shared FIN. That SWITCH
belongs to **page 1's footer** (probably the "Activities" section/footer),
and jumping to it from sheet 2 draws that text there too -- this is, with
high confidence, the origin of "an extra line in the middle of sheet 2".
The new sheet 2 does NOT jump to `pie`: it calls the `prologo` (background
+ title, a generic subroutine) and ends in a direct FIN, without going
through the footer's SWITCH.
(The cause of "Phili" being cut off could not be isolated
independently -- it might have been a visual side effect of the SWITCH
running on foreign data, or the two causes overlapping. Removing (A) and
(B) leaves sheet 2's program reduced to exactly what the factory draws
for one more row plus a FIN: no unverified element is left that could
produce the cut.)

**(C) The commands screen also had text with no real basis.** The
previous version added `NOMBRE_ABAJO` (the device name repeated at the
bottom left, `X=0x0D`), on top of the softkey label at `X=0x6E`. The real
disassembly of `table[6][142]` slot 0 has only ONE text in that Y band
(the softkey's, at `ATTR=8`); there's no second string at `X=0x0D`.
`NOMBRE_ABAJO` is removed. While at it, the softkey's font ATTRIBUTE was
also fixed: the factory uses `ATTR 8` ONLY for the softkey's label,
before the grid, and `ATTR 9` (declared once) for the grid's 6 labels --
the previous version also used `ATTR 9` (the grid's) for the softkey,
because it drew it inside the same loop.

## WHAT STAYS UNCHANGED (already verified, not touched)

* **Entries `0xAE`/`0xAF` are REMOVED from the header** of the 3 menu
  objects -- this is the cause, already closed off last round and
  confirmed by the user watching the screen, of the strips paging
  instead of releasing the event to the global pager. Factory census:
  142/142 objects with N=1 declare AE/AF in the header, 0/14 with N>1 --
  perfect separation.
* The key order is the canonical one (`ab ac b2 b3 b0 b1 b4 b5`).
* The hold waveform doesn't carry the final closing word (197/197 at the
  factory).
* The reused resources (2 row icons + 6 81x50 button bitmaps) are the
  usual ones, referenced by pointer, not copied.

Usage:
    python3 add_device.py ../backups/config_raw.bin <hub-config.json> \
        --dispositivo "Philips TV" --indice 3 --nombre Philips \
        --salida ../salida/philips_todos.bin \
        --ezhex ../salida/philips_todos.EZHex \
        --plantilla ../backups/one_20260724_210614_a.EZHex

Without `--page`, spreads ALL of the Hub's commands using
`PAGINAS_POR_DEFECTO` (6 sub-screens: 5 of 6 buttons + 1 of 2) and ABORTS
if the split doesn't cover every command exactly once.

**Grabs nothing.** Doesn't import or run `write.py` or any libconcord
primitive. Prints the exact grab command for a human to run.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

import command_records
import arrow_backlight
import glyphs
import write  # only for `nothing_moved`; `main()` is NEVER called, nor libconcord
import irscan
import relocate
import synth_ir
from commands import records as _records

BASE = 0x040000

# ---------------------------------------------------------------- anchors ---
T6 = 0x01C699  # tabla[6]: <u16 count><pad 00><count x ptr24>, entries at +3
MAESTRO_T6 = 0x0C + 4 * 6  # 0x24: the master-index entry that points at T6
MAESTRO_S5 = 0x0C + 4 * 5  # 0x20: the entry that points at section [5]
# Section [5] is THE IR COMMAND INDEX, and it's TWO LEVELS deep. `cmd_setup_ir`
# (0x029768) walks it like this, without a single range check:
#     cfg_index_x4(5)             -> lands on entry 5 of the master index
#     getbyte + follow            -> section [5]
#     += k1*3 + 1 ; follow        -> the DEVICE k1 sub-table
#     += k2*3 + 3 ; follow        -> the record's type byte, i.e. reg+11
# with k1 = the HIGH byte of cmd_id ([0x2AE], which the caller loads from
# bank13:0xD13) and k2 = the LOW byte ([0x2AF] <- 0xD12). In other words: k1 is
# the DEVICE INDEX.
#     section [5]  : <u8 device_count><count x ptr24>
#     sub-table    : <u8 00><u16 count><count x ptr24 -> reg+11>
S5_ENC = 1  # header bytes of section [5] (the firmware's +1)
SUB_ENC = 3  # header bytes of a sub-table   (the firmware's +3)

#: PANTALLAS_FABRICA_BASELINE -- NOT a format constant, unlike T6/MAESTRO_*
#: above. It's how many `table[6]` ordinals THIS project's remote shipped
#: with from the factory, measured once against `backups/config_raw.bin`
#: (its raw flash dump) and never re-measured since. A different physical
#: Harmony One -- different region/bundle/firmware build -- could ship with
#: a different count; nothing here re-derives it from a live baseline yet
#: (see `config_work/read_flash_baseline.py`, `app/history.py::mandos`).
#: Kept as the DEFAULT of `--screens-fabrica` so today's behaviour (and
#: the regression anchor, `app/check_load_bearing.py`) is unchanged; a caller
#: that HAS a recorded per-remote baseline should pass its own count instead
#: of trusting this default.
PANTALLAS_FABRICA_BASELINE = 156
PAGE_SIZE = 0x10000  # cfg_index_x3 adds 24 bits but [0x19E] is the fixed TBLPTRU
#: a walk that crosses a 64 KB boundary falls off the mapped window
ROW_STEP = 54  # vertical step between device rows (Y, not "instance")
Y_ROW_0 = 0x26  # Y of the first row (icon); +19 gives the text's Y (0x39=57)
TAG_NAME = 0x3F  # X of the name text in a device row
#: THE BUG TO FIX: the Devices menu's K=4 template has 3 device rows
#: (Y=38/92/146, text Y=57/111/165 -- verified 3/3 against the factory's
#: sheet 1 in `menu_objects()`) plus the softkey below. It used to emit ONE
#: new sheet per device (1 row, always on top); now it packs `MAX_ROWS_PER_SHEET`
#: at a time, filling top/middle/bottom in that order, and only the LAST sheet
#: can end up with fewer than 3.
MAX_ROWS_PER_SHEET = 3
X_ICONO_GRANDE, X_ICONO_CHICO = 0x06, 0x0B
ICONO_GRANDE = 0x0A5F0E  # 164x50, the TV's (DeviceType 1 = Television)
ICONO_CHICO = 0x0E53D5  # 51x48,  same
# The font attributes. **They are NOT interchangeable**: each one indexes
# section [7], and each font in [7] carries ONLY a subset of the 71 glyphs --
# the rest are null pointers and the firmware **cuts the string** right there
# (see `fonts_by_attribute()` and control (h)).
#
#   ATTR 4 (the one the factory uses in 74/90/141's rows) has 33 glyphs and
#          does NOT have lowercase 'p' (slot 42 = 0x021687 = 00 00 00), which
#          is why "Philips" came out as "Phili". Its real repertoire is
#          ' 012ACDFHOPRSTVacdefhilmnorstuvy', and the 17 factory strings drawn
#          with it (TV / Home / DVR / Tutorial / 2020...) don't carry
#          a single 'p'.
#   ATTR 9 is the correct replacement for the row: 62 glyphs (writes "Philips"
#          in full, 37 px) and **the same palette as ATTR 4** -- both paint in
#          white 0xffff with the same antialiasing 0xa631 (measured by decoding
#          the RLE of both complete sets). Only difference: height 14 vs 15.
#   ATTR 0 does NOT work even though it has the glyphs and the same height 15:
#          its palette is white + BLACK 0x0000 (it's the font for the boot /
#          diagnostic screens, ordinals 0..15), not the one for lists.
#   ATTR 8 is the softkey's label and paints in LILAC 0xc3db, with only 9
#          glyphs: 'DHaceisvy'. That's exactly enough to write "Devices" and
#          nothing else -- which is why the factory uses it ONLY for that
#          label. Putting "Select" there draws nothing.
ATTR_FILA = 0x09
ATTR_ETIQUETA = 0x09  # the one for the grid's 6 labels (142), verified
ATTR_SOFTKEY = 0x08  # the one for the softkey's label (142), verified

# geometry of the commands screen, verified byte for byte against the real
# disassembly of tabla[6][142] slot 0 (K=5): drawing order top-left,
# top-right, mid-left, mid-right, bottom-left, bottom-right -- matches 6/6
# with the real coordinates (6,38)(89,38)(6,92)(89,92)(6,146)(89,146).
# (bitmap X, bitmap Y, label X [OBSOLETE], label Y)
#
# The label's X is NO LONGER taken from here: the factory **centers** each
# label in its column, and this table used to nail it at 28/111. See
# `centered_x()`.
GRILLA = (
    (0x06, 0x26, 0x1C, 0x3A),
    (0x59, 0x26, 0x6F, 0x3A),
    (0x06, 0x5C, 0x1C, 0x70),
    (0x59, 0x5C, 0x6F, 0x70),
    (0x06, 0x92, 0x1C, 0xA6),
    (0x59, 0x92, 0x6F, 0xA6),
)
BUTTON_BMP = (0x0B7B56, 0x07DA61, 0x112A64, 0x0E342C, 0x0A3F65, 0x09FE56)  # 81x50
SOFTKEY = (0x6E, 0xCA)  # label of the 7th zone; position (110,202) verified
K_MENU = 0x04
#: THE COMMANDS-SCREEN FAMILY FOR A DEVICE.
#:
#: They are the ONLY TWO templates, out of section [19]'s 33, that have BOTH
#: bottom touch softkeys (LEFT foot and RIGHT foot) AND the canonical grid as
#: a prefix of their reading order. They weren't picked by hand: they came
#: out of an exhaustive sweep of the 33 (control in `main()`), and they are
#: exactly the ones the factory uses in the THREE device commands screens
#: opened from the Devices menu -- ordinals 78, 103 and 140.
#:
#:     K=25 (0x19): 6 grid cells + LEFT foot 0xAB + RIGHT foot 0xAC
#:     K=29 (0x1D): 4 grid cells + LEFT foot 0xB4 + RIGHT foot 0xB5
#:
#: WHY IT WAS CHANGED (it was bug 2, "the two bottom softkeys crossed"): the
#: earlier templates (K=5 and K=32) **have no left-foot zone**, but the
#: object's header DID light that softkey's LED (channel 4 of the PCA9532).
#: In other words: light on a key that doesn't exist, and no label. Factory
#: census, both directions and without a single counterexample:
#:
#:     slots whose template has a LEFT foot and declare it in their key
#:         register .............................................. 120/120
#:     slots whose template has a RIGHT foot and declare it ........ 110/110
#:     ordinals with channel 4 or 5 ON <-> LEFT foot declared ....... 74
#:         yes-yes, 0 light-no-zone, 0 zone-no-light, 82 neither
#:     ordinals with channel 6 or 7 ON <-> RIGHT foot declared ...... 90
#:         yes-yes, 0 light-no-zone, 0 zone-no-light, 66 neither
#:
#: NOTHING THAT ALREADY WORKED MOVES: the touch rectangles of the 6 cells, of
#: the 2 side strips and of the RIGHT foot ('Devices') are byte for byte the
#: same as K=5/K=32's -- programmatic control in `main()`, comparing by
#: RECTANGLE (the tags get reassigned). The only thing that appears is the
#: LEFT foot, which K=5 and K=32 simply didn't have.
K_COMANDOS = 0x19  # 5 or 6 grid commands + the two bottom softkeys
K_COMANDOS_2 = 0x1D  # 1..4 grid commands + the two bottom softkeys

RETORNO_A = (SOFTKEY, ATTR_SOFTKEY)  # (110,202) ATTR 8 -- the usual one

#: how many grid buttons (not counting the two softkeys) -> which template.
#:
#: THE 3 IS NOT A GAP. It used to be believed that there was no template for
#: 3 commands ("none of the 33 has exactly 3 cells + the two softkeys") and
#: `page_sizes` carried a special case to avoid a remainder of 3. That's a
#: FALSE premise, by two independent measurements against `config_raw.bin`:
#:
#:   1. `main()`'s gate is `len(buttons_by_k[K]) < n_bot`, i.e. it requires
#:      **>= n_bot** cells, not == n_bot. K=29 has 4 cells and already hosts 1
#:      and 2 (under-populating at 3 and at 2); 3 is exactly the same case,
#:      under-populating at 1 -- the SMALLEST under-population of the three
#:      already in use.
#:   2. Under-populating has factory precedent: census of the 156 ordinals, 8
#:      slots declare fewer grid zones than their template has (K=17 has 5
#:      cells and declares 1; K=16 has 4 and declares 1; K=13/14/15/18/21/22
#:      have 2 and declare 1). A grid cell not declared in the key register
#:      draws nothing and isn't touchable.
#:
#: (The ONLY templates with exactly 3 cells are K=19 -- 3 + RIGHT foot -- and
#: K=4 -- 3 + LEFT foot --, and neither works: both are missing one of the two
#: bottom softkeys, which is exactly bug 2. That's why 3 goes to K=29.)
PLANTILLA_POR_CANTIDAD = {
    1: K_COMANDOS_2,
    2: K_COMANDOS_2,
    3: K_COMANDOS_2,
    4: K_COMANDOS_2,
    5: K_COMANDOS,
    6: K_COMANDOS,
}
#: where the return label comes from ((x,y), ATTR). The family's two
#: templates share the SAME right-foot rectangle, so there's only one.
RETORNO_POR_CANTIDAD = dict.fromkeys(PLANTILLA_POR_CANTIDAD, RETORNO_A)
#: the touch code (tag|0x80) of the return zone (RIGHT foot) and of the
#: ACTIVITIES softkey (LEFT foot) for each template, measured with
#: `template_feet()` over section [19]. `main()` re-derives them from the
#: geometry and aborts if they don't match: they're written here only so the
#: control has something to compare against.
CODIGO_RETORNO_POR_CANTIDAD = {1: 0xB5, 2: 0xB5, 3: 0xB5, 4: 0xB5, 5: 0xAC, 6: 0xAC}
CODIGO_PIE_IZQ_POR_CANTIDAD = {1: 0xB4, 2: 0xB4, 3: 0xB4, 4: 0xB4, 5: 0xAB, 6: 0xAB}
CANONICAL_ORDER = relocate.CANONICAL_ORDER  # (ab, ac, b2, b3, b0, b1, b4, b5)
CODIGOS_MENU = (0xB2, 0xB3, 0xB0, 0xB1)  # the K=4 template's 4 zones

# ------------------------------- the bottom/left softkey ("Activities") ---
#
# The label is NOT a static one: the factory draws it with a SWITCH on
# runtime selector 0x25, with two branches. Census of the exact block
# `10 05 04 10 CA CE F6 04` in `config_raw.bin`: 24 occurrences, and the byte
# that follows is `0x14` (JMP) in 23 of the 24 -- i.e. they're the BODY OF
# CASE 0 of that SWITCH, not a loose block. The only static one is
# `table[6][141]` slot 0.
#
#     case 0 (no activity running):   ATTR 5  TXT(16,202) -> "Activities"
#     case 1 (activity running):      ATTR 5  TXT(22,196) -> "Current"
#                                              TXT(21,207) -> "Activity"
#
# Case 1 appears 23/23 times, always alongside case 0. Both bodies go AFTER
# the program's `FIN` and end in `JMP <continuation>`, where the continuation
# is the byte right after the SWITCH (12 B). Verified byte for byte in
# `table[6][74]` (SWITCH 0x0117BD), `[90]` (0x012283) and in `[103]`'s 6
# slots (0x01296D...). The pointers are the FACTORY'S: not a single new
# string is written, and by construction no glyph is missing.
SEL_PIE = 0x25  # the SWITCH's selector, 23/23
ATTR_PIE_IZQ = 0x05
PTR_ACTIVITIES = 0x00F6CE  # "Activities"
PTR_CURRENT = 0x00F6E2  # "Current"
PTR_ACTIVITY = 0x00F6ED  # "Activity"
XY_ACTIVITIES = (0x10, 0xCA)  # (16,202)
XY_CURRENT = (0x16, 0xC4)  # (22,196)
XY_ACTIVITY2 = (0x15, 0xCF)  # (21,207)
LARGO_SWITCH = 12  # 0x12 <sel> <n=2> {00 ptr24} {01 ptr24} <default=0>
#: the (id, class) pair the factory puts in the key register for a commands
#: screen's left-foot zone: `{2085, 0x72}`. It's a FACTORY object referenced
#: by index -- the same one used by `table[6][103]` in its 6 slots (as 0xAB
#: with K=25 and as 0xB4 with K=29), `[78]`, `[140]`, and the Devices menu
#: `[74]`/`[90]` (as 0xB3 with K=4). `main()` reads it from the blob and
#: aborts if it doesn't match: it's not hardcoded as ground truth, it's
#: hardcoded as the expectation being checked against.
ACCION_PIE_IZQ = (2085, 0x72)

# The X center of each grid column, DERIVED from the factory, not assumed:
# the 14 ATTR 9 labels of tabla[6][142]'s 3 slots satisfy all 14/14 of
# `x = floor(C - width/2)` with C = 46.5 (left column) and 129.5 (right),
# where `width` is the sum of byte 0 (= width in px) of each glyph's bitmap.
# Independent cross-check: the rule gives 37 px for "Philips", the number
# already noted in ATTR 9's docstring.
CENTRO_COLUMNA = (46.5, 129.5)

# ---------------------------------------------------------- the 6 pages ---
#
# 32 commands and 6 grid buttons per screen -> 6 sub-screens (5 x 6 + 2). Less
# doesn't work: K=25 (the template the factory uses on page 103) has 8
# content zones but only SIX are 3x2-grid cells; the other two (0xAB, 0xAC)
# are the bottom bar, with different geometry -- measured in section [19] and
# confirmed by resolving 103's real key register.
ETIQUETAS = {
    "PowerToggle": "Power",
    "VolumeUp": "Vol Up",
    "VolumeDown": "Vol Dn",
    "ChannelUp": "Ch Up",
    "ChannelDown": "Ch Dn",
    "Mute": "Mute",
    "DirectionUp": "Up",
    "DirectionDown": "Down",
    "DirectionLeft": "Left",
    "DirectionRight": "Right",
    "Select": "Select",
    "Menu": "Menu",
    "Red": "Red",
    "Green": "Green",
    "Yellow": "Yellow",
    "Blue": "Blue",
    "Teletext": "Text",
    # abbreviation of the Hub's own name ("TeletextHiddenInfo"), not the
    # function's commercial name: there's no TV manual on hand to confirm it.
    "TeletextHiddenInfo": "Text Info",
    "0": "0",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "AV": "AV",
    "ChannelPrev": "Ch Prev",
    "SmartPicture": "Smart Pic",
    "SmartSound": "Smart Snd",
}
PAGINAS_POR_DEFECTO = (
    ("PowerToggle", "VolumeUp", "VolumeDown", "ChannelUp", "ChannelDown", "Mute"),
    (
        "DirectionUp",
        "DirectionDown",
        "DirectionLeft",
        "DirectionRight",
        "Select",
        "Menu",
    ),
    ("Red", "Green", "Yellow", "Blue", "Teletext", "TeletextHiddenInfo"),
    ("1", "2", "3", "4", "5", "6"),
    ("7", "8", "9", "0", "AV", "ChannelPrev"),
    ("SmartPicture", "SmartSound"),
)
# The return label. It's literally the factory's: tabla[6][142]'s 3 slots
# draw 'Devices' at (110,202) with ATTR 8 and send its 0xB0 zone to
# `{menu_ordinal, 0x7E}`. And "Devices" is, glyph by glyph, the only thing
# ATTR 8's font knows how to write.
ETIQUETA_VOLVER = "Devices"


# ============================================================= DISTRIBUTOR ===
#
# `PAGINAS_POR_DEFECTO` above is a THEMATIC split done by hand for the
# Philips's 32 commands -- it doesn't generalize (with the LG, 63 commands, it
# aborted: "38 names missing"). Below is the GENERIC split for arbitrary N:
# it fills sub-screens 6 at a time (K=5) and uses whichever template matches
# for the last partial one, taking ONLY templates with real factory precedent
# (see `PLANTILLA_POR_CANTIDAD`).
#
# The six possible remainders of 6 (1..6) all have their own template, so the
# split is the trivial `[6]*k + [r]` and it ALWAYS gives the minimum
# `ceil(n/6)` of sub-screens. (There used to be a special case for a
# remainder of 3 -- borrowing a full page and splitting into 4+5, or 2+1 if
# n==3 -- built on the false premise that there was no template for 3; see
# `PLANTILLA_POR_CANTIDAD`. It didn't change the page count for n=63 (11 in
# both cases) but for n==3 it gave TWO sub-screens where one is enough, and
# left `--page` with 3 buttons throwing a KeyError.)
TAMANOS_SEGUROS = frozenset(PLANTILLA_POR_CANTIDAD)  # {1, 2, 3, 4, 5, 6}


def page_sizes(n: int) -> list[int]:
    """[page_size, ...] adding up to `n`, each one in `TAMANOS_SEGUROS`.

    CONTROL (run in `main()`, not here): for `distribute_generic()`'s coverage
    to give exactly each command once, the partition also needs to have no
    gaps -- this is only each page's size, not its content.
    """
    if n <= 0:
        raise ValueError("page_sizes: n has to be >= 1, got %d" % n)
    k, r = divmod(n, 6)
    if r == 0:
        return [6] * k
    # the six possible remainders (1..6) are all in TAMANOS_SEGUROS; the
    # assert is the gate in case someone removes an entry from
    # PLANTILLA_POR_CANTIDAD
    if r not in TAMANOS_SEGUROS:
        raise SystemExit(
            "remainder %d has no template in PLANTILLA_POR_CANTIDAD (%s): the "
            "generic split needs all six" % (r, sorted(TAMANOS_SEGUROS))
        )
    return [6] * k + [r]


def distribute_generic(cmds: list[tuple]) -> list[list[str]]:
    """[[name, ...], ...] -- `cmds`'s names (in the Hub's order) cut according
    to `page_sizes(len(cmds))`. Exact coverage by construction (partitioning a
    list, no overlap); the caller still runs the generic coverage gate
    (shares code with `--page`) because that's the one that also covers the
    manual path."""
    tamanos = page_sizes(len(cmds))
    nombres = [c[0] for c in cmds]
    pages, cursor = [], 0
    for t in tamanos:
        pages.append(nombres[cursor : cursor + t])
        cursor += t
    return pages


_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LETRA_DIGITO = re.compile(r"(?<=[A-Za-z])(?=[0-9])")


def split_camel(name: str) -> str:
    """ "InputComponent1" -> "Input Component 1": splits camelCase,
    letter-digit and hyphens with spaces. It's the GENERIC fallback for a
    command the Hub doesn't bring with its own 'Label' (see `hub_labels`) --
    it never cuts anything, it only spaces out what's already there."""
    s = _CAMEL.sub(" ", name)
    s = _LETRA_DIGITO.sub(" ", s)
    return s.replace("-", " ")


def hub_labels(cfg_path: str, dev: dict, cmds: list[tuple]) -> dict[str, str]:
    """{command_name: label}, using the 'Label' field the Hub itself attaches
    to each command (joining `dev['Commands'][].FunctionId-` against
    `resources.FunctionList.FunctionMaps[].FunctionGroups[].Functions[]` for
    the SAME DeviceId-). It's the same text the official Logitech app already
    draws, NOT an abbreviation invented here -- verified against the LG's
    JSON: 'ChannelDown'->'Channel Down', 'FastForward'->'Fast Forward',
    'PowerToggle'->'Power Toggle'.

    Commands WITHOUT a FunctionId- (learned/custom: 8 of 63 on the LG, all
    'Input*'/'Caption'/'ServiceMenu'/'Signal'/'PrimeVideo') have no Label:
    they fall back to `split_camel(name)`.
    """
    d = json.loads(pathlib.Path(cfg_path).read_text())
    devid = dev["Device"].get("Id-")
    labelmap: dict[int, str] = {}
    for m in d.get("resources", {}).get("FunctionList", {}).get("FunctionMaps", []):
        if m.get("DeviceId-") != devid:
            continue
        for grp in m.get("FunctionGroups", []):
            for f in grp.get("Functions", []):
                fid = f.get("FunctionId-")
                if fid is not None and f.get("Label"):
                    labelmap[fid] = f["Label"]
    fidmap = {c.get("Name"): c.get("FunctionId-") for c in dev["Commands"]}
    out = {}
    for name, _proto, _val in cmds:
        fid = fidmap.get(name)
        lbl = labelmap.get(fid) if fid is not None else None
        out[name] = lbl or split_camel(name)
    return out


#: shortening rules, tried IN ORDER and only if the label doesn't fit as is.
#: The factory does NOT truncate words (tabla[6][142]: "Netflix" 37px,
#: "Options" 44px, "Display" 40px, all three unshortened in an 81px cell) --
#: which is why this also doesn't cut mid-word or drop vowels: it only
#: removes or abbreviates a context qualifier that's already redundant in a
#: button grid.
QUALIFIERS = (
    ("Direction ", ""),
    ("Input ", ""),
    ("Channel ", "Ch "),
    ("Volume ", "Vol "),
    # Added for soundbars, which is where the 81 px cell first ran out: a
    # Harmony Beta Bar 5.1 aborted the whole build on two of its 28 labels,
    # 'Audio Sync Down' (98 px) and 'Surround Down' (87 px). Same two kinds
    # of rule as the four above, no new idea: 'Audio ' is a context
    # qualifier that is redundant once you are inside the device's own
    # screen (like 'Input ' and 'Direction '), and 'Surr ' is the standard
    # AV abbreviation of the word (like 'Ch ' and 'Vol '). Neither cuts
    # mid-word in a way a reader has to decode: the results are 'Sync Down'
    # and 'Surr Down'.
    ("Audio ", ""),
    ("Surround ", "Surr "),
    # Soundbar again, and this pair is the reason the loop below now also
    # composes: a Klipsch BAR 40 brings 'Subwoofer Level Down' at 132 px, and
    # NEITHER rule alone is enough ('Sub Level Down' is 90, 'Subwoofer Down'
    # is 98). Applied one after the other they give 'Sub Down', 56 px.
    ("Subwoofer ", "Sub "),
    ("Level ", ""),
)
CELL_WIDTH = 81  # px, GRILLA


def undrawable_chars(label: str, fonts: dict, attr: int, glyph_table: dict) -> list:
    """The characters of `label` this remote has no glyph for, in order of
    appearance and without repeats. Empty means the alphabet is fine and any
    problem left is the WIDTH -- which is the whole point of separating them:
    a width problem is fixed by an abbreviation and this one never is.

    Spaces are not checked: they are not drawn, they are advanced over.
    """
    missing = []
    for ch in dict.fromkeys(label):
        if ch == " ":
            continue
        cod = glyphs.codificar(ch, glyph_table)
        if cod is None or missing_glyphs(fonts, attr, cod):
            missing.append(ch)
    return missing


def abbreviate_if_needed(
    label: str, b: bytes, fonts: dict, attr: int, glyph_table: dict
) -> str:
    """If `label` fits in an `CELL_WIDTH`-px cell with `attr` as is, it's
    returned untouched. If not, tries `QUALIFIERS` -- first one at a time,
    then composing them in order -- until it fits. If none is enough, ABORTS:
    it never cuts mid-word.

    TWO FAILURES LIVE HERE AND THEY ARE NOT THE SAME ONE. `width_of()`
    returns None both when the text is unmeasurable (a glyph the remote does
    not have) and never for "too wide" -- so the old single error message
    reported a missing glyph as `doesn't fit (measures None)`, which sends
    whoever reads it to shorten a label that would not be drawable at ANY
    length. Measured on a real LG OLED55C8: its 'Live Zoom' command dies on
    the letter 'Z', and no abbreviation can fix that. They are told apart
    now, and each one names its own way out.
    """

    def width_of(txt):
        cod = glyphs.codificar(txt, glyph_table)
        if cod is None or missing_glyphs(fonts, attr, cod):
            return None
        return text_width(b, fonts, attr, cod)

    a = width_of(label)
    if a is not None and a <= CELL_WIDTH:
        return label

    candidatas = []
    for find, replace in QUALIFIERS:
        if find in label:
            candidatas.append(label.replace(find, replace))
    # composing, in declaration order: each rule applied on top of the
    # previous result, checking after every step so the SHORTEST label that
    # already fits wins instead of the most abbreviated one.
    acumulada = label
    for find, replace in QUALIFIERS:
        if find in acumulada:
            acumulada = acumulada.replace(find, replace)
            candidatas.append(acumulada)
    for corta in candidatas:
        a2 = width_of(corta)
        if a2 is not None and a2 <= CELL_WIDTH:
            return corta

    missing = undrawable_chars(label, fonts, attr, glyph_table)
    if missing:
        # DO NOT send them to Repair for this one. MEASURED: `repair()` looks
        # at label AMBIGUITY (a word that breaks the glyph table by
        # colliding), not at characters the remote simply does not own -- it
        # answers "ready to add to your remote now" for a device that then
        # dies right here. Naming a remedy that does not remedy is the same
        # dead end as naming one that does not exist.
        raise SystemExit(
            "label %r can't be drawn on this remote at any length: it needs "
            "%s, and this remote's font has 71 glyphs that do not include "
            "%s (it is missing Q, X and Z -- the factory labels never used "
            "them). This is not a width problem: no abbreviation and no "
            "repair fixes it, because the glyph is not in the hardware. The "
            "command has to be renamed in the device file, or left out."
            % (
                label,
                ", ".join(repr(c) for c in missing),
                "them" if len(missing) > 1 else repr(missing[0]),
            )
        )
    raise SystemExit(
        "label %r doesn't fit in a %d-px cell (measures %s) and no "
        "abbreviation from QUALIFIERS is enough -- a new rule needs to be "
        "added, not a manual cut" % (label, CELL_WIDTH, a)
    )


def u24(b, o):
    return int.from_bytes(b[o : o + 3], "little")


def u16(b, o):
    return int.from_bytes(b[o : o + 2], "little")


def p(v):
    """file offset -> the blob's ptr24 (always `offset + BASE`, LE)."""
    return (v + BASE).to_bytes(3, "little")


# --------------------------------------------------------------- readers ---


def read_trailer(b, off: int, max_n: int = 20) -> dict | None:
    """`<flag u8><ptr24 header><u16 N><N x ptr24 slot>`. The canonical reader.

    It's the SAME one used to walk the 156 factory trailers and the one used
    to re-read the generated trailer: if the generated one doesn't parse with
    the factory reader, the program aborts.
    """
    if off < 0 or off + 6 > len(b):
        return None
    n = u16(b, off + 4)
    if not (1 <= n <= max_n) or off + 6 + 3 * n > len(b):
        return None
    return {
        "off": off,
        "flag": b[off],
        "hdr": u24(b, off + 1),
        "N": n,
        "slots": [u24(b, off + 6 + 3 * k) for k in range(n)],
    }


def disassemble(b, off: int, limite: int = 400) -> list[tuple[int, str, tuple]]:
    """The drawing bytecode -> [(offset, opcode, arguments)].

    It's the inverse reader of what `program_commands()` / `programa_hoja2()`
    emit, and also the only way to READ the factory's page indicator without
    hardcoding offsets. An unknown opcode cuts the walk short and returns
    what was read so far: it never invents anything.
    """
    out: list[tuple[int, str, tuple]] = []
    o = off
    for _ in range(limite):
        if not 0 <= o < len(b):
            break
        op = b[o]
        if op == 0x00:
            out.append((o, "FIN", ()))
            break
        if op == 0x17:
            out.append((o, "RET", ()))
            break
        if op == 0x16:
            out.append((o, "CALL", (u24(b, o + 1) - BASE,)))
            o += 4
        elif op == 0x14:
            out.append((o, "JMP", (u24(b, o + 1) - BASE,)))
            break
        elif op == 0x10:
            out.append((o, "ATTR", (b[o + 1],)))
            o += 2
        elif op == 0x02:
            out.append((o, "BMP", (b[o + 1], b[o + 2], u24(b, o + 3) - BASE)))
            o += 6
        elif op == 0x04:
            out.append((o, "TXT", (b[o + 1], b[o + 2], u24(b, o + 3) - BASE)))
            o += 6
        elif op == 0x05:
            e = b.index(b"\x00", o + 3)
            out.append((o, "TXTIN", (b[o + 1], b[o + 2], bytes(b[o + 3 : e]))))
            o = e + 1
        elif op == 0x11:
            out.append((o, "ATOMO", (u16(b, o + 1), b[o + 3])))
            o += 4
        elif op == 0x01:
            out.append((o, "RECT", (b[o + 1], b[o + 2], b[o + 3], b[o + 4])))
            o += 7
        elif op == 0x12:
            nc = b[o + 2]
            q = o + 3 + 4 * nc
            out.append((o, "SWITCH", (b[o + 1], nc, b[q])))
            o = q + 1 + 5 * b[q]
        else:
            out.append((o, "???", (op,)))
            break
    return out


# ------------------------------------- the factory page indicator ---
#
# The previous round's bug (A) was emitting FOUR text pointers in the slot's
# program ("<n>", "/", "<total>", "pages"), a sequence that doesn't exist at
# the factory. The real cause was only half-diagnosed: the factory indicator
# DOES have all four strings, but split across TWO different programs --
#
#     prologue (once per screen): ATTR <a>  TXT(23,18)=<total>  TXT(35,18)='pages'
#     slot     (once per page):   ATTR <a>  TXT(13,18)=<n>      TXT(18,18)='/'
#
# Verified by disassembling the 4 factory multi-page objects: 103 (N=6,
# ATTR 16), 140 (N=10, ATTR 16), 69 (N=10, ATTR 12) and 142 (N=3, ATTR 7).
# Since the new screen also has 6 pages, ordinal 103's indicator is cloned
# WHOLE -- same attributes, same X/Y and **the same text pointers**, i.e.
# without writing a single new string.
ORDINAL_INDICADOR = 103  # the factory commands screen that already has N=6
#: the factory ordinal with THE MOST pages and the SAME ATTR/pages/separator
#: as 103: that's where digits 7..10 come from, which 103 doesn't have.
#: Control (j) requires its first 6 digits to be IDENTICAL to 103's before
#: using the rest.
ORDINAL_INDICADOR_LARGO = 140  # N=10, ATTR 16
Y_INDICADOR = 18
#: the current-page digit is RIGHT-ALIGNED against this X, not nailed down.
#: Measured over the 20 digits of the two factory ordinals with N=10 (140 and
#: 69): `x + width == 18` in 20/20, including the two cases that give the
#: rule away -- the '4' (6 px, x=12) and the '10' (10 px, x=8). The "x nailed
#: at 13" hypothesis fails on exactly those 4 of 20.
X_FIN_DIGITO = 18
X_TOTAL = 23  # the TOTAL, on the other hand, is LEFT-aligned: x=23 with "6" and "10"


def _indicator_pair(ins) -> tuple[int, list[tuple[int, int, int]]] | None:
    """The `ATTR <a>` block + two `TXT` at Y=18 of a disassembled program."""
    for k in range(len(ins) - 2):
        if (
            ins[k][1] == "ATTR"
            and ins[k + 1][1] == "TXT"
            and ins[k + 2][1] == "TXT"
            and ins[k + 1][2][1] == Y_INDICADOR
            and ins[k + 2][2][1] == Y_INDICADOR
        ):
            return ins[k][2][0], [ins[k + 1][2], ins[k + 2][2]]
    return None


def read_indicator(b, ordinal: int = ORDINAL_INDICADOR) -> dict:
    """Extracts the factory `<n> / <total> pages` indicator from an N>1 screen.

    Returns {'attr', 'prologo', 'total', 'pages', 'digitos': [(x,y,ptr)...]}.
    `digitos` comes IN SUB-SCREEN ORDER, which is exactly what's needed.
    """
    tr = read_trailer(b, u24(b, T6 + 3 + 3 * ordinal) - BASE, max_n=200)
    if tr is None:
        raise SystemExit("indicator ordinal %d doesn't parse" % ordinal)
    digitos, attrs, prologos = [], set(), set()
    for sp in tr["slots"]:
        s = read_slot(b, sp - BASE)
        ins = disassemble(b, s["prog"] - BASE)
        prologos.update(a[0] for _, op, a in ins if op == "CALL")
        par = _indicator_pair(ins)
        if par is None:
            raise SystemExit(
                "slot %#08x of ordinal %d doesn't carry the indicator pair"
                % (s["prog"] - BASE, ordinal)
            )
        attrs.add(par[0])
        digitos.append(par[1])
    if len(prologos) != 1 or len(attrs) != 1:
        raise SystemExit(
            "ordinal %d doesn't have a single prologue/attribute: %s %s"
            % (ordinal, prologos, attrs)
        )
    pro = prologos.pop()
    par_pro = _indicator_pair(disassemble(b, pro))
    if par_pro is None or par_pro[0] != next(iter(attrs)):
        raise SystemExit("prologue %#08x doesn't carry the indicator's total" % pro)
    sep = {tuple(d[1]) for d in digitos}
    if len(sep) != 1:
        raise SystemExit("the pages' '/' separators don't match")
    return {
        "attr": par_pro[0],
        "prologo": pro,
        "total": par_pro[1][0],
        "pages": par_pro[1][1],
        "sep": sep.pop(),
        "digitos": [d[0] for d in digitos],
        "N": tr["N"],
    }


def indicator_for(b, fonts: dict, glyph_table: dict, n_pages: int) -> dict:
    """The indicator for an `n_pages`-page screen, for ARBITRARY N.

    The factory only has indicators for N=3, 6 and 10, so cloning a whole one
    (what used to be done) only worked if the new screen had exactly 6 pages.
    The LG needs 11. This COMPOSES it, without inventing anything about the
    format:

      * the FORM (attr, prologue, 'pages', separator) is still taken whole
        from ordinal `ORDINAL_INDICADOR` (103), i.e. the prologue's form
        controls compare against exactly the same object as before;
      * DIGITS 1..10 are the factory text pointers of ordinal
        `ORDINAL_INDICADOR_LARGO` (140). It wasn't picked by hand: it's the
        ordinal with the most pages that shares ATTR/'pages'/separator with
        103, and its first 6 digits are IDENTICAL to 103's -- that's required
        here (6/6) before using the four it adds;
      * digits 11 onward are NEW strings. The only novelty, and it's small:
        '11' is the SAME glyph twice, the one the factory already draws for
        digit '1' in that same ATTR, so no glyph the font lacks is needed
        (still checked anyway). They come out with `ptr=None` and `main()`
        resolves them when it emits the tail, in `sintetizados`;
      * each digit's X is DERIVED (`X_FIN_DIGITO - width`), not copied: the
        rule is validated beforehand against the 10 factory digits.

    The TOTAL is digit N with X=`X_TOTAL`: it's literally what the factory
    does (`total` and `digitos[N-1]` are the SAME pointer in the 4 ordinals
    with an indicator: 69, 103, 140 and 142).
    """
    ind = read_indicator(b, ORDINAL_INDICADOR)
    largo = read_indicator(b, ORDINAL_INDICADOR_LARGO)
    if (
        largo["attr"] != ind["attr"]
        or largo["pages"] != ind["pages"]
        or largo["sep"] != ind["sep"]
        or largo["digitos"][: ind["N"]] != ind["digitos"]
    ):
        raise SystemExit(
            "ordinal %d is not an extension of %d (attr/'pages'/separator/"
            "digits): can't compose a %d-page indicator"
            % (ORDINAL_INDICADOR_LARGO, ORDINAL_INDICADOR, n_pages)
        )
    # the X rule, validated against the factory digits before applying it
    malos = [
        (
            k + 1,
            x,
            text_width(
                b, fonts, largo["attr"], glyphs.codificar(str(k + 1), glyph_table)
            ),
        )
        for k, (x, _y, _p) in enumerate(largo["digitos"])
        if x
        + text_width(b, fonts, largo["attr"], glyphs.codificar(str(k + 1), glyph_table))
        != X_FIN_DIGITO
    ]
    if malos:
        raise SystemExit(
            "rule `x + width == %d` doesn't reproduce the factory digits of "
            "ordinal %d: %s" % (X_FIN_DIGITO, ORDINAL_INDICADOR_LARGO, malos)
        )
    digitos, sintetizados = list(largo["digitos"]), {}
    for k in range(len(digitos), n_pages):
        cod = glyphs.codificar(str(k + 1), glyph_table)
        if cod is None:
            raise SystemExit("digit %d can't be encoded in glyphs" % (k + 1))
        missing = missing_glyphs(fonts, largo["attr"], cod)
        if missing:
            raise SystemExit(
                "digit %d needs glyphs ATTR %d doesn't have: %s"
                % (k + 1, largo["attr"], missing)
            )
        x = X_FIN_DIGITO - text_width(b, fonts, largo["attr"], cod)
        if x < 0:
            raise SystemExit(
                "digit %d doesn't fit to the left of the separator (x=%d)" % (k + 1, x)
            )
        digitos.append((x, Y_INDICADOR, None))
        sintetizados[k] = (x, Y_INDICADOR, cod)
    digitos = digitos[:n_pages]
    ult = digitos[n_pages - 1]
    return {
        "attr": ind["attr"],
        "prologo": ind["prologo"],
        "total": (X_TOTAL, Y_INDICADOR, ult[2]),
        "pages": ind["pages"],
        "sep": ind["sep"],
        "digitos": digitos,
        "N": n_pages,
        "sintetizados": sintetizados,
        "de_fabrica": min(n_pages, largo["N"]),
    }


def resolve_indicator(ind: dict, emit) -> None:
    """Writes to the tail the digit strings the factory doesn't have and
    repoints `digitos`/`total`. `emit(bloque)` is `main()`'s emitter.

    `inline_text()` is reused as a string CONTAINER: it emits `05 x y <glyphs>
    00` and the text pointer points at `<glyphs>` (the `+3`), which is exactly
    the way the button labels and the row name are already emitted. In other
    words the new string is byte for byte the same type as the ones the tool
    already burns and the user has already seen on screen."""
    if not ind or not ind.get("sintetizados"):
        return
    for k, (x, y, cod) in sorted(ind["sintetizados"].items()):
        off = emit(inline_text(x, y, cod)) + 3
        ind["digitos"][k] = (x, y, off)
        if k == ind["N"] - 1:
            ind["total"] = (X_TOTAL, y, off)
    if any(d[2] is None for d in ind["digitos"]) or ind["total"][2] is None:
        raise SystemExit("some indicator digits were left unresolved")


def prologue_with_indicator(b, prologo_off: int, ind: dict) -> bytes:
    """The new screen's prologue: the factory one + the indicator's TOTAL.

    The original prologue isn't touched (the 3 menu screens share it, which
    DON'T carry an indicator): a COPY is emitted with the block
    `ATTR <a> TXT(23,18)=<total> TXT(35,18)='pages'` inserted right after the
    `ATOMO{0002,73}` and the title's attribute restored before the last
    string -- exactly where and how ordinal 103's prologue has it.
    """
    ins = disassemble(b, prologo_off)
    if not ins or ins[0][1] != "ATTR" or ins[-1][1] != "RET":
        raise SystemExit("prologue %#08x doesn't have the expected shape" % prologo_off)
    attr_titulo = ins[0][2][0]
    anclas = [
        k for k, (_o, op, ar) in enumerate(ins) if op == "ATOMO" and ar == (2, 0x73)
    ]
    if len(anclas) != 1:
        raise SystemExit(
            "prologue %#08x doesn't have a single ATOMO{0002,73}" % prologo_off
        )
    last_txt = len(ins) - 2
    if ins[last_txt][1] != "TXT":
        raise SystemExit("prologue %#08x doesn't end in TXT + RET" % prologo_off)
    fin = ins[-1][0] + 1
    out = bytearray()
    for k, (o, _op, _ar) in enumerate(ins):
        end = ins[k + 1][0] if k + 1 < len(ins) else fin
        if k == last_txt:
            out += bytes([0x10, attr_titulo])
        out += b[o:end]
        if k == anclas[0]:
            out += bytes([0x10, ind["attr"]])
            for x, y, ptr in (ind["total"], ind["pages"]):
                out += bytes([0x04, x, y]) + p(ptr)
    return bytes(out)


def read_slot(b, off: int) -> dict | None:
    """`<K u8><ptr24 key register><ptr24 program>` (7 B)."""
    if off < 0 or off + 7 > len(b):
        return None
    return {"off": off, "K": b[off], "keyreg": u24(b, off + 1), "prog": u24(b, off + 4)}


def scan_table6(b) -> list[tuple[int, dict | None]]:
    n = u16(b, T6)
    return [(i, read_trailer(b, u24(b, T6 + 3 + 3 * i) - BASE)) for i in range(n)]


def read_key_register(b, off: int) -> list[tuple[int, int, int]] | None:
    """`<count u8><count x {code u8, id u16, class u8}>`."""
    if off < 0 or off >= len(b):
        return None
    n = b[off]
    if not (0 < n <= 40) or off + 1 + 4 * n > len(b):
        return None
    return [
        (b[off + 1 + 4 * k], u16(b, off + 2 + 4 * k), b[off + 4 + 4 * k])
        for k in range(n)
    ]


def build_key_register(entradas: list[tuple[int, int, int]]) -> bytes:
    """Sorts by ORDEN_CANONICO and serializes. The order **is** the position."""
    ordenadas = sorted(entradas, key=lambda e: CANONICAL_ORDER.index(e[0]))
    out = bytearray([len(ordenadas)])
    for cod, ident, category in ordenadas:
        out += bytes([cod]) + ident.to_bytes(2, "little") + bytes([category])
    return bytes(out)


def build_raw_register(entradas: list[tuple[int, int, int]]) -> bytes:
    """Serializes **respecting the given order**, without touching it.

    An object's header uses the same construct as the key register but its
    vocabulary of codes (`06 07 b7 2d a6 ae af ...`) is **not** in
    `CANONICAL_ORDER`, so `build_key_register()` blows up with a ValueError.
    """
    out = bytearray([len(entradas)])
    for cod, ident, category in entradas:
        out += bytes([cod]) + ident.to_bytes(2, "little") + bytes([category])
    return bytes(out)


def read_header(b, off: int) -> tuple[list[tuple[int, int, int]], str] | None:
    """An object's header, in its TWO forms.

    Short: `<count u8><count x {cod u8, id u16, cls u8}>`
    Long:  `<00><count u8><count x {flag u8, cod u8, id u16, cls u8}>`

    The long one is dispatched by the same `0x02E2F2` (branch `0x02E338`),
    and its `flag` bit0=1 means "enqueue and KEEP walking the stack" instead
    of "handled". `read_key_register()` doesn't read it and also caps at 40
    entries, so it gave a false negative on ordinals 78/103/140 (49 entries).
    """
    if off < 0 or off + 2 > len(b):
        return None
    if b[off] == 0:
        n = b[off + 1]
        if off + 2 + 5 * n > len(b):
            return None
        base = off + 2
        return [
            (b[base + 5 * k + 1], u16(b, base + 5 * k + 2), b[base + 5 * k + 4])
            for k in range(n)
        ], "larga"
    n = b[off]
    if off + 1 + 4 * n > len(b):
        return None
    return [
        (b[off + 1 + 4 * k], u16(b, off + 2 + 4 * k), b[off + 4 + 4 * k])
        for k in range(n)
    ], "corta"


# The two touch codes for the side strips: `code = tag | 0x80`, and the tags
# are 0x2E (left strip, x0=765) and 0x2F (right, x0=3556). Declaring them in
# an object's header HIJACKS the event before it reaches the global register
# that pages. An object that wants the factory pager does NOT declare them.
CODIGOS_FRANJA = (0xAE, 0xAF)


def census_strips(b) -> dict:
    """How many `table[6]` objects declare the strips in their header, by N."""
    n = u16(b, T6)
    r = {"n1_con": 0, "n1_sin": 0, "nm_con": [], "nm_sin": 0, "no_parsea": []}
    for k in range(n):
        t = read_trailer(b, u24(b, T6 + 3 + 3 * k) - BASE, max_n=200)
        if t is None:
            r["no_parsea"].append(k)
            continue
        c = read_header(b, t["hdr"] - BASE)
        if c is None:
            r["no_parsea"].append(k)
            continue
        has = any(e[0] in CODIGOS_FRANJA for e in c[0])
        if t["N"] > 1:
            (
                r["nm_con"].append(k)
                if has
                else r.__setitem__("nm_sin", r["nm_sin"] + 1)
            )
        else:
            r["n1_con" if has else "n1_sin"] += 1
    return r


# ------------------------------------- the key LEDs, PCA9532 ---
#
# Backlighting is driven by a 16-output PCA9532 on I2C 0x60, and the config
# writes to it with class-0x3F atoms: `{0xC0nm, 0x3F}` = "LED channel n to
# state m" (m=0 off, m=2 on). They're fired from the object header's 0x06
# hook (ENTER) and turned off from its 0x07 hook (EXIT).
#
# THE READER HAS TO BE RECURSIVE. The tabla[11] object the hook points at
# does **not** carry the 0x3F atoms itself: it's a container of `{id,0x7F}`
# references and the atoms live one level below. It's the same structure
# `flechas.turn_on_paging_arrows` already exploits (adds `{037C,7F}` ->
# `[11][892]` = `{C022,3F}{C002,3F}`). With a FLAT reader, 1 of 156 ordinals
# lights anything up; with a RECURSIVE reader, 106 of 156 -- the flat one is
# a structural false negative.
CLASE_LED = 0x3F
CATEGORY_ACTION = 0x7F
CODE_ENTER, CODE_EXIT = 0x06, 0x07


def _obj11_atoms(b, t11: int, k: int):
    """`<u8 count><count x {u16 id, u8 class}>` of `table[11][k]`."""
    n11 = u16(b, t11)
    if not 0 <= k < n11:
        return None
    o = u24(b, t11 + 2 + 3 * k) - BASE
    if not 0 <= o < len(b):
        return None
    n = b[o]
    if o + 1 + 3 * n > len(b):
        return None
    return [(u16(b, o + 1 + 3 * j), b[o + 3 + 3 * j]) for j in range(n)]


def led_channels(b, t11: int, k: int, prof: int = 0, visto=None) -> set:
    """{(channel, state)} that `table[11][k]` fires, following class-0x7F
    references down to the leaf 0x3F atoms."""
    if visto is None:
        visto = set()
    if k in visto or prof > 6:
        return set()
    visto.add(k)
    at = _obj11_atoms(b, t11, k)
    if at is None:
        return set()
    out = set()
    for ident, category in at:
        if category == CLASE_LED and (ident >> 8) == 0xC0:
            out.add(((ident >> 4) & 0xF, ident & 0xF))
        elif category == CATEGORY_ACTION:
            out |= led_channels(b, t11, ident, prof + 1, visto)
    return out


#: which PCA9532 channel corresponds to each bottom touch softkey. The two
#: banks ({0,2,4,6} and {1,3,5,7}) are alternatives: one screen uses one or
#: the other, never mixed. 0 and 1 <-> left strip, 2 and 3 <-> right strip
#: (already verified on the device); 4 and 5 <-> LEFT foot, 6 and 7 <-> RIGHT
#: foot -- that's what `census_bottom_softkeys()` nails down.
CANALES_PIE = {"IZQ": (4, 5), "DER": (6, 7)}


def census_bottom_softkeys(b) -> dict:
    """The control that ties LIGHT and ZONE for the bottom softkeys, over the
    156 factory ordinals. Two independent contingency tables:

      (1) by SLOT: does the slot's template have a foot zone, does the
          slot's key register declare it?
      (2) by ORDINAL: does any of its templates declare the foot, does the
          ENTER hook light up any channel of that foot?

    Returns the counts. (2) is the one that gives the channel<->softkey mapping."""
    t6 = u24(b, MAESTRO_T6) - BASE
    t11 = u24(b, 0x0C + 4 * 11) - BASE
    plantillas = read_section19(b)
    porslot = {"IZQ": [0, 0], "DER": [0, 0]}  # [declared, not declared]
    inert = 0  # slots with an EMPTY key register (count=0): they declare
    #              NO zone at all, neither foot nor grid. These are the
    #              boot/diagnostic screens and the notice ones, and they're
    #              not a counterexample: it's not "the foot zone undeclared",
    #              it's the whole screen.
    porord = {lado: [0, 0, 0, 0] for lado in CANALES_PIE}  # yes-yes, zone-no-light,
    #                                                  light-no-zone, neither
    for i in range(u16(b, t6)):
        t = read_trailer(b, u24(b, t6 + 3 + 3 * i) - BASE, max_n=200)
        if t is None:
            continue
        lados = set()
        for sp in t["slots"]:
            s = read_slot(b, sp - BASE)
            if s is None or s["K"] not in plantillas:
                continue
            kr = read_key_register(b, s["keyreg"] - BASE)
            if kr is None:
                inert += 1
                continue
            cods = {e[0] for e in kr}
            for lado, cod in template_feet(plantillas[s["K"]]).items():
                porslot[lado][0 if cod in cods else 1] += 1
                if cod in cods:
                    lados.add(lado)
        c = read_header(b, t["hdr"] - BASE)
        gancho = (
            {cod: ident for cod, ident, cls in c[0] if cls == CATEGORY_ACTION} if c else {}
        )
        on = (
            {ch for ch, est in led_channels(b, t11, gancho[CODE_ENTER]) if est == 2}
            if CODE_ENTER in gancho
            else set()
        )
        for lado, canales in CANALES_PIE.items():
            has_light = bool(on & set(canales))
            has_zone = lado in lados
            porord[lado][
                0 if (has_zone and has_light) else 1 if has_zone else 2 if has_light else 3
            ] += 1
    return {"by_slot": porslot, "by_ordinal": porord, "inert": inert}


# ------------------------------------- touch geometry, section [19] ---
#
# `reubicar.sections()` does NOT see this section: it has `N_SECCIONES = 19`
# and section [19]'s master-index slot is at `0x0C + 4*19`, i.e. outside
# `range(19)`.
FRANJAS = (0x2E, 0x2F)  # the two side strips: they're the ones that page


def read_section19(b) -> dict[int, list[tuple[int, int, int, int, int]]]:
    """{K: [(tag, x0, width, y0, height), ...]} of the 33 zone templates.

    Shape control: each zone carries, at `+9`, a self-pointer back to itself.
    If it doesn't close, what's being read isn't a zone.
    """
    off = int.from_bytes(b[0x0C + 4 * 19 : 0x10 + 4 * 19], "little") - BASE
    n = b[off]
    out = {}
    for k in range(n):
        t = u24(b, off + 1 + 3 * k) - BASE
        zones = []
        for i in range(b[t]):
            z = u24(b, t + 1 + 3 * i) - BASE
            if u24(b, z + 9) - BASE != z:
                raise SystemExit(
                    "section [19]: self-pointer doesn't close at K=%d zone %d" % (k, i)
                )
            zones.append(
                (b[z + 8], u16(b, z), u16(b, z + 2), u16(b, z + 4), u16(b, z + 6))
            )
        out[k] = zones
    return out


def zones_in_reading_order(zones) -> list[int]:
    """A template's key codes, in SCREEN reading order.

    The code is `tag | 0x80` (measured 205/205). The touch panel's Y
    coordinate is INVERTED relative to the screen's. The calibration is
    linear and closes on its own -- `y_touch = 4436 - 16.14 * y_screen`
    reproduces the menu's three rows (38/92/146 px, 54 step) with error
    < 1.5 units.

    Independent POSITIVE CONTROL: with this order, K=4's first zone is
    `0xB0`, and `0xB0` is exactly the one page 74's key register sends
    (through the jump object) to page 103 -- the first device. In other
    words, the measured geometry and the navigation agree without either
    having been used to derive the other.
    """
    contenido = [z for z in zones if z[0] not in FRANJAS]
    return [z[0] | 0x80 for z in sorted(contenido, key=lambda z: (-z[3], z[1]))]


def _rects_by_code(zones) -> dict[int, tuple[int, int, int, int]]:
    """{code (=tag|0x80): (x0,width,y0,height)} of a template -- to compare
    geometry between TWO templates without depending on them sharing the
    same internal `tag` (family B reassigns the tags; the rectangle is what
    matters for touch)."""
    return {z[0] | 0x80: z[1:] for z in zones}


#: half the screen, in TOUCH units. The content area runs from 1257 to 3556
#: (the two side strips are at 765..1257 and 3556..4048), so 2000 separates
#: them without ambiguity: across the 33 templates, no foot zone falls
#: between 1900 and 2400.
X_MEDIO_TACTIL = 2000


def template_feet(zones) -> dict[str, int]:
    """{'IZQ': code, 'DER': code} of the bottom touch softkeys.

    Identified by GEOMETRY, not by name or by tag: they're the content zones
    (excluding strips) whose touch `y0` is the template's minimum -- and the
    touch Y is inverted relative to the screen's, so the minimum is the
    BOTTOM edge. The side comes from X, which is NOT inverted (the strips
    fix it: 0x2E starts at 765, 0x2F at 3556).

    Returns {} if the template has no foot. It's the reader used for the
    factory census that separates 120/120 and 110/110."""
    contenido = [z for z in zones if z[0] not in FRANJAS]
    if not contenido:
        return {}
    y_min = min(z[3] for z in contenido)
    return {
        ("IZQ" if z[1] < X_MEDIO_TACTIL else "DER"): z[0] | 0x80
        for z in contenido
        if z[3] == y_min
    }


def template_buttons(zones) -> list[int]:
    """The GRID zones (the ones carrying a command bitmap), in reading
    order: the full reading order minus the bottom softkeys."""
    pies = set(template_feet(zones).values())
    return [c for c in zones_in_reading_order(zones) if c not in pies]


# ---------------------------- the bottom/left softkey's label ---


def _txt(xy: tuple[int, int], ptr: int) -> bytes:
    return bytes([0x04, xy[0], xy[1]]) + p(ptr)


def left_foot_case0() -> bytes:
    """`ATTR 5` + `TXT(16,202) -> "Activities"`. The 8 bytes the factory
    repeats 24 times (23 as case 0's body in the SWITCH, 1 loose in
    `table[6][141]`)."""
    return bytes([0x10, ATTR_PIE_IZQ]) + _txt(XY_ACTIVITIES, PTR_ACTIVITIES)


def left_foot_case1() -> bytes:
    """`ATTR 5` + `TXT(22,196) -> "Current"` + `TXT(21,207) -> "Activity"`."""
    return (
        bytes([0x10, ATTR_PIE_IZQ])
        + _txt(XY_CURRENT, PTR_CURRENT)
        + _txt(XY_ACTIVITY2, PTR_ACTIVITY)
    )


def left_foot_switch(off_switch: int, off_cuerpos: int) -> tuple[bytes, bytes]:
    """(the 12 B of the inline SWITCH, the two bodies that go AFTER the `FIN`).

    `off_switch` is where the SWITCH is going to end up; `off_cuerpos`, where
    the first body starts (right after the program's `FIN`). The
    continuation both bodies `JMP` to is `off_switch + 12`, which is exactly
    what the factory does: in `table[6][90]` the SWITCH is at 0x012283, the
    bodies at 0x012290/0x01229C, and both jump to 0x01228F = 0x012283+12."""
    vuelta = off_switch + LARGO_SWITCH
    c0 = left_foot_case0() + bytes([0x14]) + p(vuelta)
    c1 = left_foot_case1() + bytes([0x14]) + p(vuelta)
    sw = (
        bytes([0x12, SEL_PIE, 0x02])
        + bytes([0x00])
        + p(off_cuerpos)
        + bytes([0x01])
        + p(off_cuerpos + len(c0))
        + bytes([0x00])
    )
    if len(sw) != LARGO_SWITCH:
        raise SystemExit(
            "the foot's SWITCH measures %d B, expected %d" % (len(sw), LARGO_SWITCH)
        )
    return sw, c0 + c1


# ------------------------------------------- the three menu objects ---


def menu_objects(b) -> list[dict]:
    """The `table[6]` objects that hold the device list.

    Same criterion as `fourth_device.py` (verified on the device): an object whose
    SHEET 1 draws >= 3 name fields `04 3f <Y>` with Y in the 54-step
    progression.

    LOCATED BY THE PROGRAM, NOT BY THE HEADER. The previous version scanned
    the `[header, trailer)` window and assumed the object's body lived
    between the two. That's only true for the factory blob: each addition
    emits a new header (without `0xAE/0xAF`) and a new trailer, both together
    at the tail, while the body with the rows stays untouched where it was.
    On an already-modified blob the window fell entirely in the tail and
    didn't find a single row -- `menu_objects()` returned [] and a second
    device was impossible.

    Sheet 1's program (`slots[0].prog`) DOES always keep pointing at the
    original body, because an addition reuses that slot without copying it.
    So it's disassembled from there: the initial `CALL` gives the prologue
    (previously derived from the header record's length) and the `TXT`s with
    tag `0x3F` give the rows. Control: on `config_raw.bin` this reader
    returns the SAME `ordinal`, `prologo`, `rows`, `pie`, `slot` and `prog`
    as the previous one, for all three menus -- it's not a new criterion,
    it's the same one anchored somewhere else.

    `slots` carries ALL the live sheets (1 at the factory, 2 after the first
    addition), so that the trailer assembled afterward preserves them
    instead of overwriting them.
    """
    import relocate as _reub  # noqa: PLC0415 -- cycle: reubicar imports this one

    _dest11 = _reub.table(b, _reub.sections(b)[11][0])
    n = u16(b, T6)
    out = []
    for k in range(n):
        t = u24(b, T6 + 3 + 3 * k) - BASE
        tr = read_trailer(b, t)
        if tr is None or b[t] != 0x00:
            continue
        slot = read_slot(b, tr["slots"][0] - BASE)
        if slot is None or not 0 <= slot["prog"] - BASE < len(b):
            continue
        prog = slot["prog"] - BASE
        ins = disassemble(b, prog)
        if not ins or ins[0][1] != "CALL":
            continue
        rows = [
            o
            for o, op, ar in ins
            if op == "TXT"
            and ar[0] == TAG_NAME
            and (ar[1] - Y_ROW_0 - 19) % ROW_STEP == 0
            and 0 <= (ar[1] - Y_ROW_0 - 19) // ROW_STEP < 8
        ]
        # >=1, not >=3, and with a POSITIVE filter instead of a threshold.
        #
        # `delete_device.py` can trim the factory's sheet 1 down to 2 and then to 1
        # row (TV/Home/DVR minus whichever get deleted), and the object
        # has to stay recognizable -- otherwise `list_devices.py` and the app's
        # Control tab go blind right after a deletion.
        #
        # Just lowering the threshold did NOT work: with >=1 a one-row
        # candidate showed up, ordinal 44, which used to get discarded as
        # "spurious". It isn't: 44 is the ACTIVITIES MENU (see
        # `activities.py`), and it's distinguished on its own, without a
        # threshold, because its rows resolve to `{0xFF0X, 0x1F}` (an
        # activity) instead of `{ordinal, 0x7E}` (a device screen). So the
        # criterion becomes the one that actually defines a Devices menu:
        # ALL of its rows jump to a screen. Measured: on `config_raw.bin` and
        # `config_empaquetada.bin` this gives EXACTLY [74, 90, 141],
        # the same as the original >=3 threshold, and still gives it with
        # sheet 1 trimmed to 2 and to 1 row. Verified dict for dict against
        # the old criterion on 6 blobs; the ONLY one that changes verdict is
        # `output/cuarto.bin`, a HISTORICAL blob that draws 4 rows on sheet 1
        # and only declares 3 touch zones (the bug from the `fourth_device.py` era):
        # rejecting it is correct -- that blob has a row that's visible but
        # can't be touched.
        if not rows:
            continue
        kr_h1 = read_key_register(b, slot["keyreg"] - BASE) or []
        jumps_to_screen = 0
        is_activity = False
        for _cod, ident, category in kr_h1:
            if category != 0x7F or not 0 <= ident < len(_dest11):
                continue
            rs = _slots(b, _dest11[ident]) or []
            if any(tg == 0x7E for _v, tg in rs):
                jumps_to_screen += 1
            if any(tg == 0x1F and (_v >> 8) == 0xFF for _v, tg in rs):
                is_activity = True
        if is_activity or jumps_to_screen < len(rows):
            continue
        out.append(
            {
                "ordinal": k,
                "t6": T6 + 3 + 3 * k,
                "trailer": t,
                "hdr": tr["hdr"] - BASE,
                "prologo": ins[0][2][0],
                "slot": tr["slots"][0] - BASE,
                "slots": [s - BASE for s in tr["slots"]],
                "N": tr["N"],
                "K": slot["K"],
                "keyreg": slot["keyreg"] - BASE,
                "prog": prog,
                "rows": rows,
                "pie": max(rows) + 6,  # right after the last row
            }
        )
    return out


# --------------------------------------------------------- IR generation ---


def repeats_of(proto: dict) -> int:
    """2 frames if the protocol declares its own `KeyCode.Start`, 3 if not.

    Measured against the blob with a hard boundary: families without
    `Start` (Sony 12, Sony 15 -- and Magnavox, which is the Philips's) give
    **80/80 and 66/66** byte for byte with 3 frames. Toshiba, the only one
    with `Start`, gives **51/51** with 2 frames (1 Start frame + 1 Repeat)
    -- the one failure there was (1/51) was NOT the synthesizer's fault: it
    was that `check_ir_against_factory` was cutting the press waveform
    against the hold pointer, and on Toshiba that pointer is SHARED by all
    the commands (it points at the factory's single Repeat frame, which
    carries no payload and is therefore the same for any key) -- it's not
    adjacent to each record's press the way it is on Sony. See
    `check_ir_against_factory`.
    """
    return 2 if (proto.get("KeyCode") or {}).get("Start") else 3


def adjusted_hold_waveform(proto: dict, value: int) -> bytes:
    """`commands.hold_wave()` minus the final closing word.

    In the blob the hold waveform ends EXACTLY where the following
    command starts (197/197). The 2 extra bytes `commands.hold_wave()` adds
    are the `01` flag and that neighboring record's first `00` padding byte.
    """
    return command_records.hold_wave(proto, value, command_records.LSB_FIRST_BY_DEFAULT)[:-2]


def check_ir_against_factory(b, protos) -> dict:
    """Re-emits the waveforms that are ALREADY in the blob and compares with
    a hard boundary.

    The PRESS waveform is read with `irscan.read_waveform`, which
    self-terminates (two consecutive spaces, the last one short) -- it is
    NOT cut against the hold pointer (`hp`). Cutting against `hp` is correct
    for Sony, where the hold is unique per command and sits right next to the
    press (that's how the bottom half, the hold one, verifies it). NOT for
    Toshiba: the **51** Toshiba records with a non-null hold share ONE SINGLE
    pointer (0x0476CC) -- NEC's Repeat frame carries no payload, so the
    factory dedupes it -- and that spot falls BEFORE the press itself in 50
    of the 51, meaning cutting there gave an empty or garbage slice. That,
    and only that, was the 1/51: the synthesizer was already correct.

    ABLATION (measured against `config_raw.bin`, the 4 combinations):

        hp-2 cut    + fixed 50 ms entry ..... Toshiba 1/51
        hp-2 cut    + measured entry ........ Toshiba 1/51
        read_waveform + fixed 50 ms entry ... Toshiba 51/51
        read_waveform + measured entry ...... Toshiba 51/51

    In other words the cut is the ONLY cause; the entry gap changes nothing.
    It's not a coincidence: the 197 records this control measures use a
    50,000 us entry gap **without a single exception**, and the 39 that
    don't (100/300/500 ms) are exactly the ones with `hv == 0`, which this
    control skips. On Toshiba the split is clean: 51 with hold and a 50 ms
    entry, 11 without hold and a 500 ms entry.

    That's why the entry gap is NOT measured and accepted as is: it's
    measured and COMPARED against `sintir.ENTRADA_US`, the value `ir_block()`
    actually emits. If it were derived from each waveform, the control would
    be fitting the parameter to the data instead of testing the one the
    generator uses -- it would still pass the day a record showed up with a
    different entry gap, instead of flagging it.
    """
    regs = sorted({r[0] - 15 for r in _records(b)})
    conocidos = set(regs)
    press_ok = press_tot = hold_ok = hold_tot = 0
    by_protocol: dict[str, list[int]] = {}
    entradas_raras: list[tuple[str, int]] = []
    rangos_hold: set[tuple[int, int]] = set()  # the hold is SHARED: see below
    for r in regs:
        pp = u24(b, r + 16) - BASE
        hv = u24(b, r + 19)
        hp = hv - BASE if hv else None
        if hp is None:
            continue
        real_w = irscan.read_waveform(b, pp)
        d = irscan.decode(real_w)
        if not d:
            continue
        name, _, value = d
        proto = protos.get(name)
        if proto is None:
            continue
        acum = by_protocol.setdefault(name, [0, 0])
        press_tot += 1
        acum[1] += 1
        # the entry gap is NOT fit to the waveform: the one `ir_block` emits gets tested
        entrada = synth_ir.entrada_de(real_w)
        if entrada != synth_ir.ENTRADA_US:
            entradas_raras.append((name, entrada))
        mia = synth_ir.a_bytes(
            synth_ir.sintetizar(
                proto,
                value,
                repeats_of(proto),
                command_records.LSB_FIRST_BY_DEFAULT,
                synth_ir.ENTRADA_US,
            )
        )
        if synth_ir.a_bytes(real_w) == mia:
            press_ok += 1
            acum[0] += 1
        nxt = min((x for x in conocidos if x > hp), default=None)
        if nxt is not None:
            hold_tot += 1
            rangos_hold.add((hp, nxt))
            if b[hp:nxt] == adjusted_hold_waveform(proto, value):
                hold_ok += 1
    return {
        "press": (press_ok, press_tot),
        "hold": (hold_ok, hold_tot),
        "hold_rangos": len(rangos_hold),
        "entradas_raras": entradas_raras,
        "by_protocol": by_protocol,
    }


def ir_block(protos, cmds, dev_index: int, arranque: int):
    """Waveforms + 25 B records for all the commands, in the factory's form.

    Each command is emitted as ONE contiguous unit `press | 00 00 | hold |
    reg` (that's how it is at the factory: the hold waveform ends EXACTLY
    where the record starts), and the unit gets **aligned** so it doesn't
    cross a 64 KB boundary: the config path that reads the 25 B record walks
    sequentially from +11 to +24 without redoing the bank latch. The factory
    doesn't have a single unit straddling a boundary (236/236); the padding
    costs nothing against the 2.2 MiB of margin.
    """
    out = bytearray()
    idx = []
    dev_id = (dev_index << 8) | 0x01
    relleno = 0
    for ordinal, (name, proto_name, value) in enumerate(cmds):
        proto = protos[proto_name]
        periodo, mitad = command_records.carrier(proto.get("CarrierFrequency") or 38000)
        press = synth_ir.a_bytes(
            synth_ir.sintetizar(
                proto, value, repeats_of(proto), command_records.LSB_FIRST_BY_DEFAULT
            )
        )
        hold = adjusted_hold_waveform(proto, value)
        largo = len(press) + 2 + len(hold) + 25
        at = arranque + len(out)
        if crosses_page(at, largo):
            gap = PAGE_SIZE - (at % PAGE_SIZE)
            out += b"\x00" * gap
            relleno += gap
            at = arranque + len(out)
        off_press = at
        out += press + b"\x00\x00"
        off_hold = arranque + len(out)
        out += hold
        off_reg = arranque + len(out)
        out += command_records.command_record(off_reg, off_press, off_hold, periodo, mitad)
        idx.append(
            {
                "name": name,
                "protocolo": proto_name,
                "value": value,
                "cmd_id": (dev_index << 8) | ordinal,
                "dev_id": dev_id,
                "off_registro": off_reg,
                "off_press": off_press,
                "off_hold": off_hold,
                "largo": largo,
            }
        )
    return bytes(out), idx, relleno


# ------------------------------------ section [5]: the command index ---


def read_section5(b, off: int | None = None) -> list[dict]:
    """Walks section [5] with the SAME arithmetic as `cmd_setup_ir`.

    Returns a list per device: {'sub', 'n', 'regs'}, where `regs` are the
    offsets of the 25 B records (the table points at record+11).
    """
    if off is None:
        off = u24(b, MAESTRO_S5) - BASE
    out = []
    for k1 in range(b[off]):
        sub = u24(b, off + S5_ENC + 3 * k1) - BASE
        if not 0 <= sub < len(b) - 3:
            raise SystemExit("device %d's sub-table falls outside" % k1)
        n = u16(b, sub + 1)
        out.append(
            {
                "sub": sub,
                "n": n,
                "regs": [u24(b, sub + SUB_ENC + 3 * j) - BASE - 11 for j in range(n)],
            }
        )
    return out


def build_subtable(regs: list[int]) -> bytes:
    """`<00><u16 count><count x ptr24 -> record+11>`, the factory's form."""
    return (
        bytes([0]) + len(regs).to_bytes(2, "little") + b"".join(p(r + 11) for r in regs)
    )


def build_section5(subs: list[int]) -> bytes:
    """`<u8 count><count x ptr24 -> sub-table>`."""
    return bytes([len(subs)]) + b"".join(p(s) for s in subs)


def crosses_page(off: int, largo: int) -> bool:
    """Whether the `off..off+largo` walk crosses a 64 KB boundary.

    `cfg_index_x3` adds the offset to `[0x19C:0x19D:0x19E]` with carry, but
    `[0x19E]` is the window's TBLPTRU (0x13, forced by `cfg_follow_ptr`) and
    the real bank is set by the latch from the pointer's high byte. A carry
    that reaches `[0x19E]` takes the read out of the mapped window. No table
    is allowed to cross that boundary.
    """
    return off // PAGE_SIZE != (off + largo) // PAGE_SIZE


def resolve_section5(b, cmd_id: int) -> tuple[int | None, str]:
    """Walks section [5] with `cmd_setup_ir`'s EXACT arithmetic, but **with**
    the range checks the firmware doesn't do.

    It's the hang simulator: the firmware computes `base += k1*3+1; follow;
    += k2*3+3; follow` and jumps wherever that lands. Here every step gets
    validated and the exact reason it would go out of range is returned,
    instead of a boolean.

    Returns `(25 B record offset, reason)`; `reason` is "" if it resolved.
    """
    off = u24(b, MAESTRO_S5) - BASE
    if not 0 <= off < len(b):
        return None, "section [5] falls outside the blob"
    k1, k2 = cmd_id >> 8, cmd_id & 0xFF
    n_dev = b[off]
    if k1 >= n_dev:
        return None, "k1=%d out of range (the header declares %d)" % (k1, n_dev)
    sub = u24(b, off + S5_ENC + 3 * k1) - BASE
    if not 0 <= sub < len(b) - 3:
        return None, "device %d's sub-table falls outside the blob" % k1
    n = u16(b, sub + 1)
    if k2 >= n:
        return None, "k2=%d out of range (the sub-table declares %d)" % (k2, n)
    q = u24(b, sub + SUB_ENC + 3 * k2) - BASE  # points at record+11
    reg = q - 11
    if not 0 <= reg and reg + 25 <= len(b):
        return None, "record %#08x falls outside the blob" % reg
    if b[reg + 11] != 1:
        return None, "the type byte at +11 is %#04x, not 1" % b[reg + 11]
    if u24(b, reg + 12) - BASE != reg + 4:
        return None, "the self-pointer at +12 doesn't close over record+4"
    return reg, ""


def check_section5(b) -> tuple[bool, str]:
    """POSITIVE CONTROL: re-emit the factory's section [5] byte for byte.

    If the emitter doesn't reproduce exactly what's already there, it's no
    good for adding a new entry. It's also required that the union of the
    sub-tables be EXACTLY the set of records `commands.records()` finds, each
    one exactly once -- 236/236 at the factory.
    """
    off = u24(b, MAESTRO_S5) - BASE
    devs = read_section5(b, off)
    partes = []
    ok = True
    for k1, d in enumerate(devs):
        real = b[d["sub"] : d["sub"] + SUB_ENC + 3 * d["n"]]
        igual = build_subtable(d["regs"]) == real
        ok &= igual
        partes.append("dev%d n=%d %s" % (k1, d["n"], "OK" if igual else "FAIL"))
    igual5 = (
        build_section5([d["sub"] for d in devs])
        == b[off : off + S5_ENC + 3 * len(devs)]
    )
    ok &= igual5
    all_records = [r for d in devs for r in d["regs"]]
    delta = {r[0] - 15 for r in _records(b)}
    cerrado = len(all_records) == len(set(all_records)) == len(delta) and set(all_records) == delta
    ok &= cerrado
    return ok, (
        "section [5] @%#08x: %s; header re-emitted %s; the %d sub-table "
        "records are EXACTLY the %d from commands.records(), each exactly "
        "once: %s"
        % (
            off,
            ", ".join(partes),
            "OK" if igual5 else "FAIL",
            len(all_records),
            len(delta),
            "YES" if cerrado else "NO",
        )
    )


# ----------------------------------------------------------- bytecode ---


def inline_text(x: int, y: int, glyph_codes: bytes) -> bytes:
    """`05 <X> <Y> <glyphs> 00`: the form in which the blob stores each string."""
    return bytes([0x05, x & 0xFF, y & 0xFF]) + glyph_codes


def device_row(y: int, off_text: int, con_attr: bool = True) -> bytes:
    """A row's bytes: large icon, small icon, [font attribute], name.
    Verified byte for byte against tabla[6][74]'s THREE rows:

        row 0 (Y=38):  BMP BMP ATTR TXT   <- the attribute IS declared
        row 1 (Y=92):  BMP BMP     TXT    <- NO, inherits the one row 0 left
        row 2 (Y=146): BMP BMP     TXT    <- NO, same

    The factory declares the attribute ONLY ONCE per screen (on the first
    row it draws), not per row. `con_attr=False` reproduces the 2nd and 3rd
    row's shape -- needed to pack more than one row per sheet
    (`program_menu_sheet`); with a single row per sheet (the old case,
    before this fix) `con_attr` was always `True` and it matches."""
    cuerpo = (
        bytes([0x02, X_ICONO_GRANDE, y])
        + p(ICONO_GRANDE)
        + bytes([0x02, X_ICONO_CHICO, y + 1])
        + p(ICONO_CHICO)
    )
    if con_attr:
        cuerpo += bytes([0x10, ATTR_FILA])
    cuerpo += bytes([0x04, TAG_NAME, y + 19]) + p(off_text)
    return cuerpo


def jump_object(kind: int, page_ordinal: int) -> bytes:
    """The 10 B object that makes touching a row open its own screen.

    Copied byte for byte from the one page 74's "TV" row uses to reach page
    103 (real object at `0x29d36`: `03 ca 0f 75 67 00 7e 01 00 9a`):

        <count=3> <{tipo,0x75}> <{ordinal_pagina,0x7E}> <{1,0x9A}>

    The literal `1` in the third slot has no resolved purpose (class 0x9A
    isn't tracked, see ESTADO.md), but its VALUE is constant across the 9
    verified factory instances (74/90/141's rows): it's copied as is, not
    invented.
    """
    return (
        bytes([3])
        + relocate.slot(kind, 0x75)
        + relocate.slot(page_ordinal, 0x7E)
        + relocate.slot(1, 0x9A)
    )


def return_object(kind: int, ordinal_menu: int) -> bytes:
    """The object that makes the commands screen's softkey GO BACK.

    Measured in `table[6][142]`, the K=5 screen the whole grid geometry
    comes from: its THREE sub-screens send zone `0xB0` (the bottom-right
    softkey) to the SAME destination,
    `<02><{0x0FCA,0x75}><{90,0x7E}>` -- page 90, which is a Devices menu.

    Two differences from `jump_object()`, both copied from the factory:
      * it's **2** slots, not 3: the return does NOT carry the final
        `{1,0x9A}`. The `0x9A` shows up only when ENTERING a device (12/12
        in menus 41/74/90/141); none of 142's 3 returns has it.
      * the ordinal is the MENU's, not the screen's.

    Without this the commands screen would be a dead end: its trailer is
    N=1, and the side strips only page when N>1.
    """
    return bytes([2]) + relocate.slot(kind, 0x75) + relocate.slot(ordinal_menu, 0x7E)


# --------------------------------------------- fonts, section [7] ---


def fonts_by_attribute(b) -> dict[int, dict]:
    """`{attribute: {'height': u8, 'ptr': [71 pointers]}}` -- section [7].

    Format, verified by shape and by usage:

        section [7]   <u16 n=18><18 x ptr24>            (one font per ATTRIBUTE)
        each font     <u8 height><u16 71><71 x ptr24>   (ptr 0 = ABSENT glyph)

    The slot index is **the glyph code minus 1** (code 0 is the string
    terminator). Shape control: font `i`'s header ends exactly where font
    `i+1`'s first glyph starts, 17/17.
    """
    off = u24(b, 0x0C + 4 * 7) - BASE
    out = {}
    for j in range(u16(b, off)):
        f = u24(b, off + 2 + 3 * j) - BASE
        out[j] = {
            "height": b[f],
            "ptr": [u24(b, f + 3 + 3 * k) for k in range(u16(b, f + 1))],
        }
    return out


def missing_glyphs(fonts: dict, attr: int, codigos) -> list[int]:
    """The codes in `codigos` that `attr`'s font CANNOT draw.

    The firmware doesn't skip a missing glyph: it **cuts the string right
    there**. That's why "Philips" with ATTR 4 comes out as "Phili" -- the 'p'
    (0x2b) is the 6th character and its slot is zero.
    """
    f = fonts.get(attr)
    if f is None:
        return list(codigos)
    return [c for c in codigos if c and (c - 1 >= len(f["ptr"]) or not f["ptr"][c - 1])]


def text_width(b, fonts: dict, attr: int, codigos) -> int:
    """A string's width in px: byte 0 of each glyph's bitmap, summed.

    It's the same count control (h) already printed; it's factored out
    because now it also **positions** the text (see `centered_x()`).
    """
    f = fonts[attr]
    return sum(b[f["ptr"][c - 1] - BASE] for c in codigos if c and f["ptr"][c - 1])


def centered_x(columna: int, width: int) -> int:
    """A grid label's X, with the FACTORY rule.

    `x = floor(C - width/2)`, C = 46.5 (left column) / 129.5 (right).
    Derived and validated 14/14 EXACT against the 14 ATTR 9 labels of
    `table[6][142]`'s three slots -- the same screen `GRILLA` comes from.
    The previous version nailed X at 28/111, which off-centers a narrow
    label by up to 15 px (a digit measures 6 px: it would go to 28 when the
    factory puts it at 43).
    """
    return int(CENTRO_COLUMNA[columna] - width / 2)


def _attribute_glyph_pairs(b) -> set[tuple[int, int]]:
    """The (live attribute, glyph code) pairs the factory actually DRAWS.

    Walks the programs of `table[6]`'s 156 screens with the linear
    interpreter, carrying forward the attribute the last `10 <attr>` left,
    and following `CALL`, `JMP` and both kinds of `SWITCH` branch. It's the
    sample section [7]'s model gets validated against."""
    t6 = u24(b, MAESTRO_T6) - BASE
    pares, vistos = set(), set()

    def walks(off, attr, prof=0):
        if prof > 6:
            return
        o = off
        ini = off
        while 0 <= o < len(b) and o - ini < 6000:
            if (o, attr) in vistos:
                return
            vistos.add((o, attr))
            op = b[o]
            if op == 0x00:
                return
            if op == 0x01:
                o += 7
            elif op == 0x02:
                o += 6
            elif op == 0x04:
                d = u24(b, o + 3) - BASE
                pares.update((attr, c) for c in b[d : b.find(b"\x00", d)])
                o += 6
            elif op == 0x05:
                e = b.find(b"\x00", o + 3)
                pares.update((attr, c) for c in b[o + 3 : e])
                o = e + 1
            elif op == 0x10:
                attr = b[o + 1]
                o += 2
            elif op == 0x11:
                o += 4
            elif op == 0x12:
                q = o + 3
                dest = []
                for _ in range(b[o + 2]):
                    dest.append(u24(b, q + 1) - BASE)
                    q += 4
                n2 = b[q]
                q += 1
                for _ in range(n2):
                    dest.append(u24(b, q + 2) - BASE)
                    q += 5
                for d in dest:
                    walks(d, attr, prof + 1)
                o = q
            elif op == 0x14:
                o = u24(b, o + 1) - BASE
            elif op == 0x16:
                walks(u24(b, o + 1) - BASE, attr, prof + 1)
                o += 4
            else:
                return

    for k in range(u16(b, t6)):
        t = read_trailer(b, u24(b, t6 + 3 + 3 * k) - BASE, max_n=200)
        if t is None:
            continue
        for sp in t["slots"]:
            s = read_slot(b, sp - BASE)
            if s:
                walks(s["prog"] - BASE, 0)
    return pares


def _font_check(b, fonts) -> tuple[tuple[int, int], tuple[int, int]]:
    """(violations, total) with the correct indexing, and the violations with
    both shifted indexings. The correct one has to give 0 and the shifted
    ones, many."""
    pares = [(x, c) for x, c in _attribute_glyph_pairs(b) if c]

    def violates(corrimiento):
        n = 0
        for attr, c in pares:
            f = fonts.get(attr)
            i = c - 1 + corrimiento
            if f is None or not (0 <= i < len(f["ptr"])) or not f["ptr"][i]:
                n += 1
        return n

    return (violates(0), len(pares)), (violates(1), violates(-1))


def menu_sheet_layout(n_total: int) -> list[int]:
    """[extra_sheet_1_row_count, extra_sheet_2_row_count, ...]: packs
    `n_total` device rows (all the ones that go BEYOND the factory's sheet 1
    -- the ones already added in previous runs + this run's) `MAX_ROWS_PER_SHEET`
    at a time, in the order they were added. ALWAYS gives
    `ceil(n_total / MAX_ROWS_PER_SHEET)` sheets, all full except the last.

    THIS IS THE TASK. `main()` used to do the equivalent of `[1] * n_total`
    (one sheet per device, each with a single row on top) -- which is why 6
    devices came out as 6 sheets instead of 2."""
    if n_total <= 0:
        return []
    k, r = divmod(n_total, MAX_ROWS_PER_SHEET)
    return [MAX_ROWS_PER_SHEET] * k + ([r] if r else [])


def partition(items: list, tamanos: list[int]) -> list[list]:
    """`items` cut according to `tamanos` (which have to add up to `len(items)`)."""
    if sum(tamanos) != len(items):
        raise SystemExit(
            "partition: sizes add up to %d, there are %d items"
            % (sum(tamanos), len(items))
        )
    out, cursor = [], 0
    for t in tamanos:
        out.append(items[cursor : cursor + t])
        cursor += t
    return out


def read_extra_rows(b, o: dict, menu_order: list[int]) -> list[tuple[int, int]]:
    """[(name_text_offset, id_jump), ...] for ALL the device rows ALREADY
    added on menu object `o`'s EXTRA sheets (`o['slots'][1:]`; the factory's
    sheet 1 never enters here), in the order they were added -- sheet by
    sheet, and top to bottom within each sheet.

    Compatible with the TWO shapes the input blob may carry:
      * the OLD one (the bug: one sheet per device): each extra sheet
        carries a SINGLE row, always in the top position.
      * the NEW one (this fix): each extra sheet carries 1..MAX_FILAS_POR_HOJA.
    Both share the SAME rule, which is what makes it possible to read one
    shape and write the other: sheet row `k` (k=0 on top) draws its name at
    position `k` of the program (top to bottom -- that's how
    `program_menu_sheet` emits it) and its zone in the key register is
    `menu_order[k]` (the same `menu_order`, by K=4's GEOMETRY, that `main()`
    already uses -- measured 3/3 against sheet 1: `menu_order[:3]` =
    [0xb0, 0xb1, 0xb2] resolves, through the jump object, to three DIFFERENT
    ordinals, in the same order as rows Y=38/92/146)."""
    row_codes = menu_order[:MAX_ROWS_PER_SHEET]
    out: list[tuple[int, int]] = []
    for slot_off in o["slots"][1:]:
        s = read_slot(b, slot_off)
        if s is None:
            raise SystemExit(
                "extra sheet of %d at %#08x doesn't parse as a slot"
                % (o["ordinal"], slot_off)
            )
        kr = read_key_register(b, s["keyreg"] - BASE)
        if kr is None:
            raise SystemExit(
                "extra sheet of %d: key register at %#08x doesn't parse"
                % (o["ordinal"], s["keyreg"])
            )
        id_by_code = {cod: ident for cod, ident, cls in kr if cod in row_codes}
        textos = [
            ar[2]
            for _o, op, ar in disassemble(b, s["prog"] - BASE)
            if op == "TXT" and ar[0] == TAG_NAME
        ]
        if len(textos) != len(id_by_code):
            raise SystemExit(
                "extra sheet of %d (slot %#08x): draws %d names but declares "
                "%d row zones in the key register -- they don't match"
                % (o["ordinal"], slot_off, len(textos), len(id_by_code))
            )
        for k, off_text in enumerate(textos):
            if k >= len(row_codes):
                raise SystemExit(
                    "extra sheet of %d (slot %#08x) draws %d names, more than "
                    "MAX_FILAS_POR_HOJA=%d"
                    % (o["ordinal"], slot_off, len(textos), MAX_ROWS_PER_SHEET)
                )
            cod = row_codes[k]
            if cod not in id_by_code:
                raise SystemExit(
                    "extra sheet of %d (slot %#08x): row %d (Y=%d) doesn't "
                    "declare zone %#04x in its key register"
                    % (o["ordinal"], slot_off, k, Y_ROW_0 + k * ROW_STEP, cod)
                )
            out.append((off_text, id_by_code[cod]))
    return out


def program_menu_sheet(
    prologo_off: int,
    rows_text_offs: list[int],
    pie: bytes | None = None,
    own_off: int | None = None,
) -> bytes:
    """`CALL prologue` + up to `MAX_ROWS_PER_SHEET` rows (top to bottom, at
    the SAME Y=38/92/146 positions as the factory's sheet 1 -- the structure
    is copied, coordinates aren't invented) + the FOOT + `FIN`.

    Generalizes `programa_hoja2` (an earlier version of this function, which
    only knew how to emit ONE fixed row at Y_FILA_0 -- hence the bug: one
    sheet per device). No indicator (these sheets are N=1 from their own
    point of view: nothing to page within themselves) and no `JMP pie` to
    the SWITCH inherited from the original object (bug B, historical: it
    drew one extra line -- see the module's docstring).

    `pie` is THIS sheet's bottom/left softkey, already in bytes:

      * `left_foot_case0()`  -- the static shape, `table[6][141]` slot 0's;
      * `b"SWITCH"` with `own_off` -- the full SWITCH gets assembled (the
        shape from `table[6][74]` and `[90]` slot 0).

    When `pie` carries a SWITCH, its two bodies go AFTER the `FIN`, so it's
    necessary to know where the program is going to land: that's `own_off`."""
    if not 1 <= len(rows_text_offs) <= MAX_ROWS_PER_SHEET:
        raise SystemExit(
            "program_menu_sheet: %d rows, has to be 1..%d"
            % (len(rows_text_offs), MAX_ROWS_PER_SHEET)
        )
    cuerpo = bytes([0x16]) + p(prologo_off)
    for k, off_text in enumerate(rows_text_offs):
        cuerpo += device_row(Y_ROW_0 + k * ROW_STEP, off_text, con_attr=(k == 0))
    if pie is None:
        return cuerpo + b"\x00"
    if pie == b"SWITCH":
        if own_off is None:
            raise SystemExit("program_menu_sheet with SWITCH needs off_propio")
        off_sw = own_off + len(cuerpo)
        sw, cuerpos = left_foot_switch(off_sw, off_sw + LARGO_SWITCH + 1)
        return cuerpo + sw + b"\x00" + cuerpos
    return cuerpo + pie + b"\x00"


def program_commands(
    prologo_off: int,
    buttons: list[dict],
    off_txt_volver: int,
    indicador: tuple | None = None,
    retorno_xy: tuple[int, int] = SOFTKEY,
    retorno_attr: int = ATTR_SOFTKEY,
    own_off: int | None = None,
) -> bytes:
    """The bytecode for ONE commands page: `CALL prologue`, `ATTR
    <retorno_attr>` + the return label at `retorno_xy` (the bottom/RIGHT
    softkey, 'Devices', the only zone with no bitmap, drawn BEFORE the
    grid), `ATTR 9` (once) + the grid buttons (bitmap + label each), the page
    indicator's pair, the bottom/LEFT softkey's SWITCH, `FIN`, and finally
    the SWITCH's two bodies.

    `buttons` carries 1, 2, 4, 5 or 6 -- an entry from `PLANTILLA_POR_CANTIDAD`.

    THE LEFT FOOT (bug 2, half the label). `own_off` is the file offset
    this program is going to land at; it's needed because the SWITCH and its
    two bodies are referenced by absolute pointer. It's emitted in the
    factory's exact shape: SWITCH immediately before the `FIN` and the
    bodies after, which is what `table[6][74]` and `[90]` do (23/23
    occurrences of case 0 in the blob are a SWITCH's body, only 1 is
    static). If `own_off` is None no left foot is drawn -- for that the
    template must not have a zone there, which `main()` checks.

    The return label is 'Devices', not a command: its zone is reserved for
    going back to the menu on every sub-screen."""
    if len(buttons) not in PLANTILLA_POR_CANTIDAD:
        raise SystemExit(
            "program_commands accepts %s grid buttons (two more zones are "
            "the bottom softkeys), got %d"
            % (
                " or ".join(str(k) for k in sorted(PLANTILLA_POR_CANTIDAD)),
                len(buttons),
            )
        )
    out = bytearray(bytes([0x16]) + p(prologo_off))
    out += bytes([0x10, retorno_attr])
    out += bytes([0x04, retorno_xy[0], retorno_xy[1]]) + p(off_txt_volver)
    out += bytes([0x10, ATTR_ETIQUETA])
    for j, bt in enumerate(buttons):
        bx, by, _tx, ty = GRILLA[j]
        out += bytes([0x02, bx, by]) + p(BUTTON_BMP[j])
        out += bytes([0x04, bt["x_txt"], ty]) + p(bt["off_txt"])
    if indicador is not None:
        attr, digito, sep = indicador
        out += bytes([0x10, attr])
        for x, y, ptr in (digito, sep):
            out += bytes([0x04, x, y]) + p(ptr)
    if own_off is None:
        out += b"\x00"
        return bytes(out)
    off_sw = own_off + len(out)
    sw, cuerpos = left_foot_switch(off_sw, off_sw + LARGO_SWITCH + 1)
    out += sw + b"\x00" + cuerpos
    return bytes(out)


def build_table6(b, changes: dict[int, int], nuevas: list[int]) -> bytes:
    """Copies `table[6]` WHOLE (format `<u16 count><pad 00><count x ptr24>`,
    entries at +3) into a new block, with `changes`'s entries repointed and
    `nuevas` (file offsets) appended at the end. The old table isn't touched
    by even one byte: this is a new block for the tail, with its own
    master-index pointer (`MAESTRO_T6`, repointed separately)."""
    n_orig = u16(b, T6)
    total = n_orig + len(nuevas)
    out = bytearray(total.to_bytes(2, "little") + b"\x00")
    for k in range(n_orig):
        if k in changes:
            out += p(changes[k])
        else:
            out += b[T6 + 3 + 3 * k : T6 + 6 + 3 * k]
    for d in nuevas:
        out += p(d)
    return bytes(out)


# ------------------------------------------------------------- closing ---


def close_blob(out: bytearray) -> bytearray:
    """Aligns to even, writes `PTYY`, the closing pointer and the XOR-16."""
    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = p(nc)
    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi
    return out


def _slots(b, d):
    if d is None or not 0 <= d < len(b):
        return None
    c = b[d]
    if not 0 < c < 40 or d + 1 + 3 * c > len(b):
        return None
    return [
        (int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little"), b[d + 3 + 3 * j])
        for j in range(c)
    ]


# =========================================================== program ===


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("config", help="Hub resources JSON")
    ap.add_argument("--device", default="Philips TV")
    ap.add_argument("--index", type=int, default=3, help="new device index")
    ap.add_argument("--name", default="Philips", help="how it reads in the menu")
    ap.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="CMD=ETQ,CMD=ETQ,...",
        help="one commands sub-screen: 6 buttons (K=5 template) or 2 "
        "(K=32 template). Repeatable, once per page. If none is passed "
        "ALL of the Hub's commands get split with PAGINAS_POR_DEFECTO",
    )
    ap.add_argument(
        "--sin-indicador",
        action="store_true",
        help="don't draw the '<n> / <total> pages' indicator top-left. The "
        "indicator is an exact clone of the one the factory already has on "
        "ordinal %d (same attributes, same X/Y and the SAME text pointers); "
        "this flag exists only to drop it if something looks wrong" % ORDINAL_INDICADOR,
    )
    ap.add_argument("--salida")
    ap.add_argument("--ezhex")
    ap.add_argument("--plantilla", help=".EZHex of the same device, for the header")
    ap.add_argument(
        "--factory-screens",
        type=int,
        default=PANTALLAS_FABRICA_BASELINE,
        help="how many tabla[6] ordinals THIS remote's factory baseline has "
        "(default: %d, measured on backups/config_raw.bin -- pass the value "
        "recorded for the CONNECTED remote if it differs)" % PANTALLAS_FABRICA_BASELINE,
    )
    a = ap.parse_args()
    factory_screens = a.factory_screens

    b = pathlib.Path(a.blob).read_bytes()
    print("input blob: %s  %d B" % (a.blob, len(b)))

    # ---------------------------------------------------------- anchors ---
    if b[:4] != b"GSPM":
        raise SystemExit("the blob doesn't start at GSPM")
    # tabla[6] is taken from the MASTER INDEX, not from the factory constant.
    # Each addition relocates tabla[6] whole to the tail and repoints 0x24,
    # so the `T6` constant is only valid for the factory blob: running the
    # tool on its own output (a SECOND device) was reading the old table.
    # It's fixed here, once, and every reader in the module sees it.
    global T6
    t6_vivo = int.from_bytes(b[MAESTRO_T6 : MAESTRO_T6 + 4], "little") - BASE
    if not 0 <= t6_vivo < len(b) - 3:
        raise SystemExit(
            "the master index at %#04x doesn't point to a valid tabla[6] (%#08x)"
            % (MAESTRO_T6, t6_vivo)
        )
    n6 = u16(b, t6_vivo)
    outside = [
        k for k in range(n6) if not 0 <= u24(b, t6_vivo + 3 + 3 * k) - BASE < len(b)
    ]
    if outside:
        raise SystemExit(
            "tabla[6] isn't aligned at +3 (%d destinations out of range)" % len(outside)
        )
    if n6 < factory_screens:
        raise SystemExit(
            "tabla[6] has %d entries and the factory baseline is %d "
            "(--pantallas-fabrica): the blob isn't a config for this remote"
            % (n6, factory_screens)
        )
    T6 = t6_vivo
    new_ordinal = n6  # appended at the end: the new commands screen
    print(
        "tabla[6] at %#08x%s: %d entries -> extended to %d (new ordinal %d)"
        % (
            T6,
            "" if T6 == 0x01C699 else " (relocated by an earlier addition)",
            n6,
            n6 + 1,
            new_ordinal,
        )
    )

    # ---- section [5]: the IR command index, the cause of the hang ----
    ok5, det5 = check_section5(b)
    devs5 = read_section5(b)
    print(
        "\nsection [5] -- THE IR COMMAND INDEX (two levels, k1=device "
        "index, k2=command ordinal):\n   POSITIVE control -- %s" % det5
    )
    if not ok5:
        raise SystemExit(
            "section [5]'s model doesn't reproduce the factory: nothing gets generated"
        )
    if len(devs5) < 3:
        raise SystemExit(
            "section [5] declares %d devices and the factory has 3: the blob "
            "isn't a config for this remote" % len(devs5)
        )
    if a.index != len(devs5):
        raise SystemExit(
            "--indice %d: section [5] declares %d devices, so the new index "
            "has to be %d" % (a.index, len(devs5), len(devs5))
        )
    print(
        "   NEGATIVE control -- k1=%d (the high byte of the cmd_id for ALL "
        "of the new device's commands) does NOT exist: the header says %d "
        "and the ptr24 the firmware would read at +%d is %#08x, i.e. %s. "
        "That's the hang: `cfg_follow_ptr` validates nothing and jumps there anyway."
        % (
            a.index,
            b[u24(b, MAESTRO_S5) - BASE],
            S5_ENC + 3 * a.index,
            u24(b, u24(b, MAESTRO_S5) - BASE + S5_ENC + 3 * a.index),
            "outside the blob"
            if not (
                0
                <= u24(b, u24(b, MAESTRO_S5) - BASE + S5_ENC + 3 * a.index) - BASE
                < len(b)
            )
            else "garbage",
        )
    )

    fabrica = scan_table6(b)
    malos = [i for i, t in fabrica if t is None]
    if malos:
        raise SystemExit("factory trailers that don't parse: %s" % malos)
    con_n = [(i, t) for i, t in fabrica if t["N"] > 1]
    # the factory has 14. Before packing MAX_FILAS_POR_HOJA at a time, every
    # addition ALWAYS added +3 (the 3 menus, N=1->2) +1 (the new commands
    # screen, if it happened to get N>1) -- hence the `14+4k` that used to be
    # here. With packing the 3 menus move to N>1 ONLY once (on the first
    # addition) and stay there -- subsequent additions do NOT add them
    # again, they only add their own commands screen. So the only real,
    # invariant floor is the factory's: never less than 14.
    if len(con_n) < 14:
        raise SystemExit(
            "there are %d objects with N>1 in tabla[6] and the factory has 14: "
            "the blob isn't a config for this remote" % len(con_n)
        )
    flags_f, ks_f, ops_f = set(), set(), set()
    for i, t in con_n:
        for sp in t["slots"]:
            s = read_slot(b, sp - BASE)
            if s is None:
                raise SystemExit("factory slot out of range on ordinal %d" % i)
            ks_f.add(s["K"])
            ops_f.add(b[s["prog"] - BASE])
        flags_f.add(t["flag"])
    # the flag and the initial opcode of N=1 objects also have to be in the
    # bank -- our commands object is N=1
    for i, t in fabrica:
        if t and t["N"] == 1:
            flags_f.add(t["flag"])
            s = read_slot(b, t["slots"][0] - BASE)
            if s:
                ks_f.add(s["K"])
                ops_f.add(b[s["prog"] - BASE])
    print(
        "control (a): %d/%d tabla[6] trailers parse with read_trailer(); "
        "%d with N>1" % (n6, n6, len(con_n))
    )
    print(
        "   factory bank -- flags %s   K %s   program's initial opcode %s"
        % (sorted(flags_f), sorted(hex(k) for k in ks_f), sorted(hex(o) for o in ops_f))
    )
    if K_MENU not in ks_f or K_COMANDOS not in ks_f:
        raise SystemExit(
            "K=%d or K=%d doesn't appear at the factory" % (K_MENU, K_COMANDOS)
        )

    # control of the resources that get reused by pointer
    for etq, off, wh in (
        ("large icon", ICONO_GRANDE, (164, 50)),
        ("small icon", ICONO_CHICO, (51, 48)),
        *[("button %d" % i, o, (81, 50)) for i, o in enumerate(BUTTON_BMP)],
    ):
        cab = b[off : off + 4]
        if cab[0] or cab[2] or (cab[1], cab[3]) != wh:
            raise SystemExit(
                "%s at %#08x: header %s = %dx%d, expected %dx%d"
                % (etq, off, cab.hex(" "), cab[1], cab[3], *wh)
            )
    print(
        "   reused resources: 2 icons + %d button bitmaps, headers OK" % len(BUTTON_BMP)
    )

    objs = menu_objects(b)
    if len(objs) != 3:
        raise SystemExit("expected 3 Devices menu objects, found %d" % len(objs))
    print("\nDevices menu objects (all three, like fourth_device.py):")
    for o in objs:
        print(
            "   tabla[6][%3d] @%#08x  N=%d  header %#08x  sheet1 prog %#08x  "
            "prologue %#08x  K=%d  %d rows Y=%s  foot %#08x"
            % (
                o["ordinal"],
                o["t6"],
                o["N"],
                o["hdr"],
                o["prog"],
                o["prologo"],
                o["K"],
                len(o["rows"]),
                [b[f + 2] for f in o["rows"]],
                o["pie"],
            )
        )
        if o["K"] != K_MENU:
            raise SystemExit("object %d doesn't have K=4" % o["ordinal"])
        reg = read_key_register(b, o["keyreg"])
        if reg is None or [e[0] for e in reg] != list(CODIGOS_MENU):
            raise SystemExit("%d's key register isn't the canonical one" % o["ordinal"])
        cab = read_key_register(b, o["hdr"])
        if cab is None or 0x2D not in [e[0] for e in cab]:
            raise SystemExit(
                "%d's header doesn't have the pager's 0x2d entry" % o["ordinal"]
            )
    cen = census_strips(b)
    print(
        "   HEADER STRIP CENSUS (the control that separates N=1 from N>1):\n"
        "      N=1 : %3d declare 0xAE/0xAF, %3d don't\n"
        "      N>1 : %3d declare 0xAE/0xAF, %3d don't    <- perfect separation\n"
        "      headers that don't parse: %s"
        % (
            cen["n1_con"],
            cen["n1_sin"],
            len(cen["nm_con"]),
            cen["nm_sin"],
            cen["no_parsea"] or "none",
        )
    )
    if cen["n1_sin"] or cen["nm_con"] or cen["no_parsea"]:
        raise SystemExit(
            "the factory strip census doesn't separate 142/142 vs 0/14: %s" % cen
        )
    # At the factory the 3 menu headers carry the 2 NULL strips and they need
    # to be removed. On an already-modified blob the header is the COPY an
    # earlier addition left, and it already comes without them: both forms
    # are valid, what's NOT accepted is a strip declared with content (that
    # one DOES hijack the event and breaks paging).
    con_franja, sin_franja = [], []
    for o in objs:
        cab = read_key_register(b, o["hdr"])
        franjas_hoy = [e for e in cab if e[0] in CODIGOS_FRANJA]
        if any(e[1] or e[2] for e in franjas_hoy):
            raise SystemExit(
                "object %d declares a NON-null strip (%s): with that the "
                "global pager doesn't get the event" % (o["ordinal"], franjas_hoy)
            )
        (con_franja if len(franjas_hoy) == 2 else sin_franja).append(
            (o["ordinal"], len(franjas_hoy))
        )
    if sin_franja and con_franja:
        raise SystemExit(
            "the 3 menus aren't in the same state: with 2 null strips %s, "
            "without them %s" % (con_franja, sin_franja)
        )
    if any(n not in (0, 2) for _o, n in con_franja + sin_franja):
        raise SystemExit(
            "some menu header declares only 1 strip: %s" % (con_franja + sin_franja)
        )
    print(
        "   the 3 menu headers %s -- N>1 doesn't declare them, so the "
        "global pager gets the event"
        % (
            "carry the factory's 2 NULL strips and they're about to be removed"
            if con_franja
            else "already come WITHOUT strips (a copy left by an earlier addition)"
        )
    )

    # ------------------------------- touch geometry, measured not assumed ---
    plantillas = read_section19(b)
    print(
        "\nsection [19]: %d touch-zone templates, self-pointer closes on all of them"
        % len(plantillas)
    )
    ks_en_uso = (K_MENU,) + tuple(sorted(set(PLANTILLA_POR_CANTIDAD.values())))
    for K in ks_en_uso:
        tags = [z[0] for z in plantillas[K]]
        missing = [t for t in FRANJAS if t not in tags]
        if missing:
            raise SystemExit(
                "template K=%d does NOT have the paging strips %s: the "
                "sub-screen would be a dead end" % (K, missing)
            )
    menu_order = zones_in_reading_order(plantillas[K_MENU])
    order_by_k = {
        K: zones_in_reading_order(plantillas[K])
        for K in set(PLANTILLA_POR_CANTIDAD.values())
    }
    cmd_order = order_by_k[K_COMANDOS]
    feet_by_k = {K: template_feet(plantillas[K]) for K in order_by_k}
    buttons_by_k = {K: template_buttons(plantillas[K]) for K in order_by_k}
    pies_menu = template_feet(plantillas[K_MENU])
    print(
        "   K=%d (menu):    %d content zones + 2 strips -> reading order "
        "%s;  bottom softkeys: %s"
        % (
            K_MENU,
            len(menu_order),
            [hex(c) for c in menu_order],
            {k: hex(v) for k, v in sorted(pies_menu.items())},
        )
    )
    for n_bot, K in sorted(PLANTILLA_POR_CANTIDAD.items(), reverse=True):
        print(
            "   K=%d (%d commands): %d content zones + 2 strips -> reading "
            "order %s;  grid %s;  bottom softkeys %s"
            % (
                K,
                n_bot,
                len(order_by_k[K]),
                [hex(c) for c in order_by_k[K]],
                [hex(c) for c in buttons_by_k[K]],
                {k: hex(v) for k, v in sorted(feet_by_k[K].items())},
            )
        )
        if set(feet_by_k[K]) != {"IZQ", "DER"}:
            raise SystemExit(
                "template K=%d doesn't have BOTH bottom softkeys (%s): the "
                "commands screen's header lights up both LEDs, and lighting an "
                "LED over a zone that doesn't exist is exactly bug 2 (factory "
                "census: 0/82 ordinals light channel 4/5 without declaring a "
                "left foot)" % (K, sorted(feet_by_k[K]) or "none")
            )
        if len(buttons_by_k[K]) < n_bot:
            raise SystemExit(
                "template K=%d has %d grid cells, %d are needed"
                % (K, len(buttons_by_k[K]), n_bot)
            )
        if (
            feet_by_k[K]["DER"] != CODIGO_RETORNO_POR_CANTIDAD[n_bot]
            or feet_by_k[K]["IZQ"] != CODIGO_PIE_IZQ_POR_CANTIDAD[n_bot]
        ):
            raise SystemExit(
                "the geometry gives feet %s for K=%d, the constants say "
                "IZQ=%#04x DER=%#04x"
                % (
                    feet_by_k[K],
                    K,
                    CODIGO_PIE_IZQ_POR_CANTIDAD[n_bot],
                    CODIGO_RETORNO_POR_CANTIDAD[n_bot],
                )
            )
    # POSITIVE CONTROL that EACH partial template is K=5 with its grid
    # trimmed (positions 0..n_bot-1 in reading order, IDENTICAL rectangle
    # byte for byte -- comparing by RECTANGLE, not by tag: family B
    # reassigns the tags). The return (position n_bot) is compared
    # separately: in family A it has to be the SAME rectangle as K=5's
    # return; in B, a DIFFERENT one (if it came out equal it would be a
    # suspicious coincidence, not the wide zone that was measured) AND the
    # touch code has to be the one measured in `CODIGO_RETORNO_POR_CANTIDAD`.
    rects_cmd = _rects_by_code(plantillas[K_COMANDOS])
    ref_grilla = [rects_cmd[c] for c in buttons_by_k[K_COMANDOS]]  # 6 cells
    ref_pies = {lad: rects_cmd[c] for lad, c in feet_by_k[K_COMANDOS].items()}
    for n_bot, K in sorted(PLANTILLA_POR_CANTIDAD.items()):
        rects_k = _rects_by_code(plantillas[K])
        grilla_ok = all(
            rects_k[buttons_by_k[K][j]] == ref_grilla[j] for j in range(n_bot)
        )
        pies_ok = all(rects_k[c] == ref_pies[lad] for lad, c in feet_by_k[K].items())
        print(
            "   K=%d (%d commands): grid identical to K=%d in its %d cells: %s;  "
            "BOTH bottom softkeys with the same rectangle as K=%d: %s"
            % (
                K,
                n_bot,
                K_COMANDOS,
                n_bot,
                "OK" if grilla_ok else "FAIL",
                K_COMANDOS,
                "OK" if pies_ok else "FAIL",
            )
        )
        if not (grilla_ok and pies_ok):
            raise SystemExit(
                "template K=%d (%d commands) doesn't reproduce the geometry "
                "measured against the factory: nothing gets generated" % (K, n_bot)
            )
    # TOUCH-GEOMETRY REGRESSION ANCHOR. The template family changed from
    # K=5/K=32 (a single bottom softkey) to K=25/K=29 (both). What was
    # already VERIFIED ON THE DEVICE -- the 32 buttons and the 'Devices'
    # that returns to the menu -- can't have moved a single pixel: every
    # rectangle from the old template is required to still exist, identical,
    # in the new one.
    for K_old, K_new, etq in (
        (0x05, K_COMANDOS, "6 commands"),
        (0x20, K_COMANDOS_2, "2 commands"),
    ):
        old_rects = _rects_by_code(plantillas[K_old])
        new_rects = set(_rects_by_code(plantillas[K_new]).values())
        cubiertos = {c: (r in new_rects) for c, r in old_rects.items()}
        nuevos = set(_rects_by_code(plantillas[K_new]).values()) - set(
            old_rects.values()
        )
        print(
            "   ANCHOR K=%d -> K=%d (%s): of the old template's %d "
            "rectangles, %d still EXIST IDENTICAL in the new one%s.  "
            "Rectangles the new one ADDS: %d %s"
            % (
                K_old,
                K_new,
                etq,
                len(old_rects),
                sum(cubiertos.values()),
                ""
                if all(cubiertos.values())
                else "  MISSING %s" % [hex(c) for c, ok in cubiertos.items() if not ok],
                len(nuevos),
                sorted(nuevos),
            )
        )
        if not all(cubiertos.values()):
            raise SystemExit(
                "template K=%d doesn't preserve K=%d's touch geometry: "
                "something already verified on the device would move"
                % (K_new, K_old)
            )
    # EXHAUSTIVE NEGATIVE CONTROL over the 33 templates: which ones have BOTH
    # bottom softkeys AND the canonical grid as a prefix. They have to be
    # exactly the two that are used -- if there were more, the choice would
    # be arbitrary; if there were fewer, there'd be nothing to choose from.
    familia = {}
    for K, zones in plantillas.items():
        pz = template_feet(zones)
        if set(pz) != {"IZQ", "DER"}:
            continue
        bt = template_buttons(zones)
        rk = _rects_by_code(zones)
        if [rk[c] for c in bt] == ref_grilla[: len(bt)] and all(
            rk[c] == ref_pies[lad] for lad, c in pz.items()
        ):
            familia[K] = len(bt)
    print(
        "   exhaustive NEGATIVE control: of section [19]'s %d templates, "
        "the ones with BOTH bottom softkeys at the factory rectangle AND "
        "the canonical grid as a prefix are %s (expected exactly {%d: 6, %d: 4}, "
        "the ones from ordinals 78/103/140)"
        % (
            len(plantillas),
            {hex(k): v for k, v in sorted(familia.items())},
            K_COMANDOS,
            K_COMANDOS_2,
        )
    )
    if familia != {K_COMANDOS: 6, K_COMANDOS_2: 4}:
        raise SystemExit(
            "the template family with both bottom softkeys isn't the "
            "measured one: %s" % familia
        )
    # positive control on the calibration: K=4's first zone has to be the one
    # page 74 sends (through the jump object) to the first device
    reg74 = read_key_register(b, objs[0]["keyreg"])
    first_dest = next((e[1] for e in reg74 if e[0] == menu_order[0]), None)
    sec_ctl = relocate.sections(b)
    dest_ctl = relocate.table(b, sec_ctl[11][0])
    first_page = None
    if first_dest is not None and first_dest < len(dest_ctl):
        rs = _slots(b, dest_ctl[first_dest])
        if rs:
            first_page = next((v for v, c in rs if c == 0x7E), None)
    print(
        "   positive control: K=%d's 1st zone in reading order is %#04x, and on "
        "page 74 that zone resolves (through the jump object) to page %s -- "
        "the menu's top row. Geometry and navigation agree without either "
        "having been used to derive the other."
        % (K_MENU, menu_order[0], first_page)
    )
    if menu_order[0] != 0xB0 or first_page is None:
        raise SystemExit(
            "the touch calibration doesn't close: check before generating anything"
        )
    if len(cmd_order) < 8:
        raise SystemExit(
            "template K=%d has %d zones, 8 are needed (6 cells + both "
            "bottom softkeys)" % (K_COMANDOS, len(cmd_order))
        )

    # ------------- THE CENSUS THAT CLOSES BOTH BOTTOM-SOFTKEY BUGS ---
    #
    # A softkey that doesn't exist can't be labeled, nor can one that isn't
    # declared be lit up. Both tables have to separate WITHOUT COUNTEREXAMPLES
    # in both directions, or nothing gets generated.
    cen_sk = census_bottom_softkeys(b)
    ps, po = cen_sk["by_slot"], cen_sk["by_ordinal"]
    print(
        "\nBOTTOM TOUCH SOFTKEYS CENSUS (156 factory ordinals):\n"
        "   (1) BY SLOT -- the template has the zone, does the key register "
        "declare it?  (%d slots with an EMPTY register are left out: those "
        "declare no zone at all, neither foot nor grid)\n"
        "       LEFT foot : %3d declared / %3d NOT declared\n"
        "       RIGHT foot: %3d declared / %3d NOT declared\n"
        "   (2) BY ORDINAL -- zone declared  <->  LED channel lit on the "
        "ENTER hook (RECURSIVE reader over the class-0x7F references):\n"
        "       LEFT foot / channels %s: %3d zone+light, %3d zone with NO light, "
        "%3d LIGHT WITH NO ZONE, %3d neither\n"
        "       RIGHT foot / channels %s: %3d zone+light, %3d zone with NO light, "
        "%3d LIGHT WITH NO ZONE, %3d neither"
        % (
            cen_sk["inert"],
            ps["IZQ"][0],
            ps["IZQ"][1],
            ps["DER"][0],
            ps["DER"][1],
            CANALES_PIE["IZQ"],
            *po["IZQ"],
            CANALES_PIE["DER"],
            *po["DER"],
        )
    )
    if ps["IZQ"][1] or ps["DER"][1]:
        raise SystemExit(
            "there are factory slots with an undeclared foot zone: the "
            "invariant doesn't hold, check before generating anything"
        )
    if any(po[lado][1] or po[lado][2] for lado in po):
        raise SystemExit(
            "the LED channel <-> bottom softkey mapping does NOT separate: %s" % po
        )
    print(
        "   -> PERFECT separation in both directions and on both sides. This is "
        "where the mapping comes from: channel 4 or 5 = bottom/LEFT softkey, "
        "channel 6 or 7 = bottom/RIGHT softkey. And this is where bug 2's "
        "diagnosis comes from: the previous blob's commands screen lit channel 4 "
        "over a template (K=5) with no left-foot zone -- 'LIGHT WITH NO ZONE', "
        "the case the factory doesn't commit even once across 156 screens."
    )

    # ---- THE MODEL ORDINAL: the commands screen the factory already has ----
    #
    # It's not picked by hand: it's the ordinal the Devices menu's FIRST row
    # sends to through the jump object, i.e. `first_page`, already
    # derived above from 74's key register without using anything from this
    # section. From it come, copied by index and without inventing anything:
    # the two hook objects (ENTER/EXIT, with both softkeys' and the strips'
    # channels) and the left-foot zone's action.
    tr_modelo = read_trailer(b, u24(b, T6 + 3 + 3 * first_page) - BASE, max_n=200)
    cab_modelo = read_header(b, tr_modelo["hdr"] - BASE)
    gancho_modelo = {
        cod: ident for cod, ident, cls in cab_modelo[0] if cls == CATEGORY_ACTION
    }
    t11_ctl = sec_ctl[11][0]
    atomos_gancho = {
        cod: _obj11_atoms(b, t11_ctl, gancho_modelo[cod])
        for cod in (CODE_ENTER, CODE_EXIT)
    }
    canales_modelo = {
        cod: sorted(led_channels(b, t11_ctl, gancho_modelo[cod]))
        for cod in (CODE_ENTER, CODE_EXIT)
    }
    ks_modelo = []
    accion_pie_modelo = set()
    for sp in tr_modelo["slots"]:
        s = read_slot(b, sp - BASE)
        ks_modelo.append(s["K"])
        kr = read_key_register(b, s["keyreg"] - BASE)
        cod_izq = template_feet(plantillas[s["K"]]).get("IZQ")
        for e in kr or []:
            if e[0] == cod_izq:
                accion_pie_modelo.add((e[1], e[2]))
    print(
        "\nMODEL ORDINAL %d (the commands screen menu %d's 1st row opens, "
        "derived -- not chosen):\n"
        "   templates of its %d slots: %s   (the same two that are going to be used)\n"
        "   ENTER hook = [11][%d] = %s\n"
        "      -> channels (channel,state): %s\n"
        "   EXIT hook  = [11][%d] = %s\n"
        "      -> channels (channel,state): %s\n"
        "   left-foot zone action across its %d slots: %s"
        % (
            first_page,
            objs[0]["ordinal"],
            len(ks_modelo),
            [hex(k) for k in ks_modelo],
            gancho_modelo[CODE_ENTER],
            " ".join("{%04X,%02X}" % x for x in atomos_gancho[CODE_ENTER]),
            canales_modelo[CODE_ENTER],
            gancho_modelo[CODE_EXIT],
            " ".join("{%04X,%02X}" % x for x in atomos_gancho[CODE_EXIT]),
            canales_modelo[CODE_EXIT],
            len(tr_modelo["slots"]),
            sorted(accion_pie_modelo),
        )
    )
    if set(ks_modelo) - set(PLANTILLA_POR_CANTIDAD.values()):
        raise SystemExit(
            "model ordinal %d uses templates %s, which aren't the ones from "
            "the chosen family %s"
            % (first_page, ks_modelo, sorted(set(PLANTILLA_POR_CANTIDAD.values())))
        )
    if accion_pie_modelo != {ACCION_PIE_IZQ}:
        raise SystemExit(
            "model ordinal %d's left-foot action is %s, expected "
            "%s" % (first_page, sorted(accion_pie_modelo), (ACCION_PIE_IZQ,))
        )
    canales_on_modelo = {c for c, e in canales_modelo[CODE_ENTER] if e == 2}
    canales_off_modelo = {c for c, e in canales_modelo[CODE_EXIT] if e == 0}
    esperado_on = {0, 2, CANALES_PIE["IZQ"][0], CANALES_PIE["DER"][0]}
    if canales_on_modelo != esperado_on or canales_off_modelo != esperado_on:
        raise SystemExit(
            "model ordinal %d's hook lights %s and turns off %s, expected "
            "%s (the 2 paging strips + the 2 bottom softkeys)"
            % (
                first_page,
                sorted(canales_on_modelo),
                sorted(canales_off_modelo),
                sorted(esperado_on),
            )
        )
    print(
        "   control: lights and turns off EXACTLY %s = the 2 paging strips "
        "(0 and 2) + the bottom/left softkey (%d) + the bottom/right one (%d). "
        "This is the hook the new screen was missing: the old one only had %d "
        "and the strips."
        % (
            sorted(esperado_on),
            CANALES_PIE["IZQ"][0],
            CANALES_PIE["DER"][0],
            CANALES_PIE["IZQ"][0],
        )
    )

    # ---- THE BOTTOM/LEFT SOFTKEY'S LABEL, RE-EMITTED ----
    #
    # POSITIVE CONTROL, the hardest one there is: `left_foot_switch()` is
    # asked to emit the block at the offset where the factory already has
    # it, and it's compared BYTE FOR BYTE against the blob. If the SWITCH's
    # model were wrong (the length, the case order, the JMP destination,
    # anything) it wouldn't match.
    reemitidos, fallados, inline = 0, [], []
    for i in range(u16(b, T6)):
        t = read_trailer(b, u24(b, T6 + 3 + 3 * i) - BASE, max_n=200)
        if t is None:
            continue
        for sp in t["slots"]:
            s = read_slot(b, sp - BASE)
            if s is None:
                continue
            for o_ins, op, ar in disassemble(b, s["prog"] - BASE):
                if op != "SWITCH" or ar[0] != SEL_PIE or ar[1] != 2:
                    continue
                c0 = u24(b, o_ins + 4) - BASE
                # two ways to write the same foot: by POINTER (opcode 0x04,
                # 23 of 24) or with INLINE glyphs (opcode 0x05, 1 of 24:
                # ordinal 41). The one that gets re-emitted is the pointer
                # one, the one that reuses the factory's strings.
                if b[c0 + 2] == 0x05:
                    inline.append((i, o_ins))
                    continue
                sw, cuerpos = left_foot_switch(o_ins, c0)
                if b[o_ins : o_ins + len(sw)] == sw and (
                    b[c0 : c0 + len(cuerpos)] == cuerpos
                ):
                    reemitidos += 1
                else:
                    fallados.append((i, o_ins))
    n_caso0 = b.count(left_foot_case0())
    n_caso1 = b.count(left_foot_case1())
    print(
        "\ncontrol (k) -- THE BOTTOM/LEFT SOFTKEY'S LABEL:\n"
        "   the case 0 block (`ATTR %d` + `TXT(%d,%d) -> 'Activities'`) "
        "appears %d times at the factory; case 1's ('Current' + 'Activity'), %d\n"
        "   foot SWITCH(sel=%#04x) re-emitted byte for byte AT ITS OWN OFFSET, "
        "with its two bodies: %d/%d  (failed: %s; with INLINE glyphs instead "
        "of by pointer, i.e. another form of the same foot: %s)\n"
        "   the FACTORY's text pointers are reused (%#06x/%#06x/%#06x): not a "
        "single new string is written, so no glyph can be missing"
        % (
            ATTR_PIE_IZQ,
            XY_ACTIVITIES[0],
            XY_ACTIVITIES[1],
            n_caso0,
            n_caso1,
            SEL_PIE,
            reemitidos,
            reemitidos + len(fallados),
            fallados or "none",
            inline or "none",
            PTR_ACTIVITIES,
            PTR_CURRENT,
            PTR_ACTIVITY,
        )
    )
    if fallados or reemitidos < 20:
        raise SystemExit(
            "the left foot's SWITCH model doesn't reproduce the factory "
            "(%d re-emitted, %d failed): nothing gets generated"
            % (reemitidos, len(fallados))
        )

    # which form each menu's SHEET 1 draws the foot in -- sheet 2 copies that one
    sheet1_foot_by_object = {}
    for o in objs:
        ins = disassemble(b, o["prog"])
        if any(op == "SWITCH" and ar[0] == SEL_PIE for _x, op, ar in ins):
            sheet1_foot_by_object[o["ordinal"]] = b"SWITCH"
            forma = "SWITCH(sel=%#04x) with its two branches" % SEL_PIE
        elif any(
            op == "TXT" and (ar[0], ar[1]) == XY_ACTIVITIES and ar[2] == PTR_ACTIVITIES
            for _x, op, ar in ins
        ):
            sheet1_foot_by_object[o["ordinal"]] = left_foot_case0()
            forma = "static (fixed 'Activities')"
        else:
            raise SystemExit(
                "menu %d's sheet 1 doesn't draw the left foot: there's nothing "
                "to copy it from for sheet 2" % o["ordinal"]
            )
        print(
            "   menu %3d's sheet 1: left foot %s -> sheet 2 is going to draw "
            "the same one" % (o["ordinal"], forma)
        )

    # ------------------------------------------------------------ the Hub ---
    protos, devs = command_records.load_hub_config(a.config)
    i_dev, dev = command_records.choose_device(devs, a.device)
    if dev is None:
        raise SystemExit(
            "%r not found; available: %s"
            % (a.device, [command_records.device_name(d) for d in devs])
        )
    cmds, saltados = command_records.commands_of(dev, protos)
    print(
        "\ndevice %r (position %d in the Hub): %d commands, %d skipped"
        % (command_records.device_name(dev), i_dev, len(cmds), len(saltados))
    )
    ctl = check_ir_against_factory(b, protos)
    print(
        "IR control against the factory (hard boundary, can fail on length):\n"
        "   press waveform  %d/%d  (with the %d us entry gap ir_block emits, "
        "not one measured per record: records with a different entry gap, %s)\n"
        "   hold waveform   %d/%d comparisons, but only %d DISTINCT RANGES "
        "from the blob -- the %d Toshiba records share a single Repeat "
        "pointer, so that's 1 independent data point, not %d"
        % (
            *ctl["press"],
            synth_ir.ENTRADA_US,
            ctl["entradas_raras"] or "none",
            *ctl["hold"],
            ctl["hold_rangos"],
            ctl["by_protocol"].get("Toshiba 32 Bit", [0, 0])[1],
            ctl["by_protocol"].get("Toshiba 32 Bit", [0, 0])[1],
        )
    )
    for name, (ok, tot) in sorted(ctl["by_protocol"].items()):
        print("      %-18s press %3d/%-3d" % (name, ok, tot))
    if ctl["entradas_raras"]:
        raise SystemExit(
            "there are factory records with an entry gap different from the "
            "one ir_block emits (%d us): %s -- the generator would have to "
            "derive it, not nail it down" % (synth_ir.ENTRADA_US, ctl["entradas_raras"])
        )
    if ctl["hold"][0] != ctl["hold"][1]:
        raise SystemExit("the hold waveform doesn't reproduce the blob: not continuing")
    proto_nuestro = {c[1] for c in cmds}
    reproducidos = {n for n, (ok, tot) in ctl["by_protocol"].items() if ok == tot}
    verificados = sorted(proto_nuestro & reproducidos)
    sin_verificar = sorted(proto_nuestro - reproducidos)
    if verificados:
        print(
            "   new device's protocol(s) verified byte for byte against "
            "THEIR OWN family in the blob: %s (%s)"
            % (
                ", ".join(verificados),
                ", ".join("%s %d/%d" % (n, *ctl["by_protocol"][n]) for n in verificados),
            )
        )
    if sin_verificar:
        print(
            "   [ASSUMED] new device's protocol(s) WITHOUT any commands of "
            "their own in the blob to compare against: %s -- inherits the "
            "rule measured on the families that did match byte for byte (%s)"
            % (", ".join(sin_verificar), ", ".join(sorted(reproducidos)) or "none")
        )
    print(
        "   [ASSUMED] this confirms the waveform's FORM (frame, gap, trailer, "
        "entry gap); the %d commands' payload VALUES for %r are not in the "
        "factory blob, so their exact bits still haven't been checked against "
        "a real code" % (len(cmds), a.device)
    )

    # --------------------------------------- the buttons, page by page ---
    glyph_table, _ = glyphs.extender(b, glyphs.vocabulario(a.config))
    fonts = fonts_by_attribute(b)  # needed already for abbreviate_if_needed

    # ---- commands whose label this remote has no alphabet for -------------
    #
    # The blob stores text as GLYPH INDICES and this remote's table has 71 of
    # them: no Q, no X, no Z, because no factory label ever needed one. A
    # command whose name uses one cannot be drawn at ANY length -- it is not
    # a width problem and no abbreviation or repair reaches it, the glyph is
    # not in the hardware.
    #
    # Aborting the whole device over one button was the old behaviour and it
    # cost the user the other 67 (measured: an LG OLED55C8 dies on its 'Live
    # Zoom'). Leaving it OUT and SAYING SO is what `app/ir_manual.py` already
    # does on the `.ir` path ("left undrawable does NOT get imported, the
    # letter is named"), so this is that same rule, not a new one.
    #
    # The filter runs HERE, before `distribute_generic()` and before
    # `ir_block()`, so the page split, the coverage gate and the IR block all
    # see the same list: a dropped command leaves no waveform behind either.
    left_out: list[tuple[str, str, list[str]]] = []
    if not a.page:
        etq_previas = hub_labels(a.config, dev, cmds)
        vivos = []
        for c in cmds:
            etq = etq_previas.get(c[0], c[0])
            missing = undrawable_chars(etq, fonts, ATTR_ETIQUETA, glyph_table)
            (left_out.append((c[0], etq, missing)) if missing else vivos.append(c))
        if left_out:
            print(
                "\nLEFT OUT -- %d command(s) this remote has no glyphs for:"
                % len(left_out)
            )
            for cmd_name, etq, missing in left_out:
                print(
                    "   %r (label %r) needs %s -- not in the remote's 71 glyphs"
                    % (cmd_name, etq, ", ".join(repr(ch) for ch in missing))
                )
            print(
                "   the other %d command(s) DO get added; nothing else changes."
                % len(vivos)
            )
            if not vivos:
                raise SystemExit(
                    "every one of this device's labels needs a glyph the "
                    "remote does not have, so there is nothing left to add."
                )
            cmds = vivos

    by_name = {c[0]: c for c in cmds}
    es_philips_de_referencia = {c[0] for c in cmds} == {
        c for pag in PAGINAS_POR_DEFECTO for c in pag
    }
    if a.page:
        pedidas = [
            [
                tuple(t.split("=", 1)) if "=" in t else (t, ETIQUETAS.get(t, t))
                for t in pag.split(",")
                if t
            ]
            for pag in a.page
        ]
    elif es_philips_de_referencia:
        # the original THEMATIC split, byte-for-byte reproducible: preserved
        # as is so as not to change the output already verified on the device.
        print(
            "\nsplit: %r matches the PAGINAS_POR_DEFECTO set (Philips, "
            "32 commands) -- using the historical thematic split, not the generic one"
            % a.device
        )
        pedidas = [
            [(c, ETIQUETAS.get(c, c)) for c in pag if c in by_name]
            for pag in PAGINAS_POR_DEFECTO
        ]
    else:
        # GENERIC SPLIT -- see `distribute_generic()`/`page_sizes()`.
        # Labels come from the Hub itself (`hub_labels`, the same text the
        # official Logitech app already draws) and are shortened ONLY if
        # they don't fit in the 81-px cell (`abbreviate_if_needed`).
        hub_label_map = hub_labels(a.config, dev, cmds)
        print(
            "\nGENERIC split: %d commands -> sizes %s (%d sub-screens, the "
            "minimum ceil(%d/6)=%d; the six possible remainders have a template)"
            % (
                len(cmds),
                page_sizes(len(cmds)),
                len(page_sizes(len(cmds))),
                len(cmds),
                -(-len(cmds) // 6),
            )
        )
        rotulos_finales = {}
        for name, etq_hub in hub_label_map.items():
            final = abbreviate_if_needed(etq_hub, b, fonts, ATTR_ETIQUETA, glyph_table)
            rotulos_finales[name] = final
            if final != etq_hub:
                print(
                    "   abbreviated: %r -> %r (didn't fit in 81 px)" % (etq_hub, final)
                )
        pedidas = [
            [(c, rotulos_finales[c]) for c in pag] for pag in distribute_generic(cmds)
        ]
    pedidas = [pg for pg in pedidas if pg]
    # THE COVERAGE GATE: the requirement is that ALL of the Hub's commands
    # fit in, not one short and not one repeated. It fails loudly, not silently.
    puestos = [c for pg in pedidas for c, _e in pg]
    missing_cmds = [c[0] for c in cmds if c[0] not in puestos]
    repetidos = sorted({c for c in puestos if puestos.count(c) > 1})
    ajenos = [c for c in puestos if c not in by_name]
    if ajenos:
        raise SystemExit("commands that aren't on the device: %s" % ajenos)
    if missing_cmds or repetidos:
        raise SystemExit(
            "the split doesn't cover the %d commands exactly once: missing %s, "
            "repeated %s" % (len(cmds), missing_cmds or "none", repetidos or "none")
        )
    for k, pg in enumerate(pedidas):
        if len(pg) not in PLANTILLA_POR_CANTIDAD:
            raise SystemExit(
                "page %d has %d buttons; there's only a verified factory "
                "template for %s" % (k + 1, len(pg), sorted(PLANTILLA_POR_CANTIDAD))
            )
    pages = []
    for pg in pedidas:
        page_buttons = []
        for cmd, label in pg:
            cod = glyphs.codificar(label, glyph_table)
            if cod is None:
                inv = {v: k for k, v in glyph_table.items()}
                raise SystemExit(
                    "label %r can't be written: missing glyphs %r"
                    % (label, "".join(sorted({c for c in label if c not in inv})))
                )
            page_buttons.append({"cmd": cmd, "label": label, "glyphs": cod})
        pages.append(page_buttons)
    buttons = [bt for pg in pages for bt in pg]  # the N, flattened
    name_text = glyphs.codificar(a.name, glyph_table)
    if name_text is None:
        raise SystemExit("name %r can't be written with the glyph table" % a.name)
    txt_volver = glyphs.codificar(ETIQUETA_VOLVER, glyph_table)
    if txt_volver is None:
        raise SystemExit("return label %r can't be written" % ETIQUETA_VOLVER)
    print(
        "\nsplit of the Hub's %d commands across %d sub-screens (the last zone "
        "of each one, %r, is the RETURN to the menu, not a command):"
        % (len(cmds), len(pages), ETIQUETA_VOLVER)
    )
    for k, pg in enumerate(pages):
        print(
            "   page %d/%d  K=%-2d  %s"
            % (
                k + 1,
                len(pages),
                PLANTILLA_POR_CANTIDAD[len(pg)],
                ", ".join("%s->%r" % (x["cmd"], x["label"]) for x in pg),
            )
        )
    print(
        "   COVERAGE: %d/%d of the Hub's commands, each exactly once, "
        "0 repeated, 0 foreign" % (len(buttons), len(cmds))
    )

    # ------------- (h) THE FONT GATE: every glyph that gets drawn exists ---
    #
    # This is the gate that was missing and that let the "Phili" bug through.
    # Validating against `glyphs.codificar()` is NOT enough: that table is
    # the GLOBAL substitution one (which character is each code) and knows
    # nothing about which FONT draws each attribute. The firmware cuts the
    # string when the glyph doesn't exist in the live attribute's font.
    # (`fonts` was already computed before the split, because
    # `abbreviate_if_needed` needs it.)
    #
    # The return label is drawn with the ATTR of EACH family actually used
    # (family A = ATTR_SOFTKEY, family B = ATTR_ETIQUETA -- see
    # RETORNO_POR_CANTIDAD): if the split only used one, only one gets checked.
    attrs_retorno = sorted({RETORNO_POR_CANTIDAD[len(pg)][1] for pg in pages})
    a_dibujar = (
        [("row name", ATTR_FILA, a.name, name_text)]
        + [("grid label", ATTR_ETIQUETA, x["label"], x["glyphs"]) for x in buttons]
        + [
            ("return label (ATTR %d)" % attr, attr, ETIQUETA_VOLVER, txt_volver)
            for attr in attrs_retorno
        ]
    )
    faltantes = []
    for what, attr, text, cod in a_dibujar:
        absent = missing_glyphs(fonts, attr, cod)
        if absent:
            inv = {v: k for k, v in glyph_table.items()}
            rep = "".join(
                sorted(
                    glyph_table.get(k + 1, "?")
                    for k, q in enumerate(fonts[attr]["ptr"])
                    if q
                )
            )
            faltantes.append(
                "%s %r with ATTR %d: missing glyphs %r (would draw %r). That "
                "font's repertoire is %r"
                % (
                    what,
                    text,
                    attr,
                    "".join(glyph_table.get(c, "?") for c in absent),
                    "".join(
                        glyph_table.get(c, "?")
                        for c in cod[: cod.index(absent[0]) if absent[0] in cod else 0]
                    ),
                    rep,
                )
            )
            del inv
    print(
        "\ncontrol (h) -- glyph coverage against section [7] (%d fonts, "
        "format <u8 height><u16 71><71 x ptr24>, ptr 0 = ABSENT glyph, "
        "index = code-1):" % len(fonts)
    )
    for what, attr, text, cod in a_dibujar:
        f = fonts[attr]
        width = text_width(b, fonts, attr, cod)
        print(
            "   ATTR %2d (height %2d, %2d glyphs)  %-20r %s  %d px"
            % (
                attr,
                f["height"],
                sum(1 for q in f["ptr"] if q),
                text,
                "OK" if not missing_glyphs(fonts, attr, cod) else "CUT",
                width,
            )
        )
    if faltantes:
        for x in faltantes:
            print("   FAIL: %s" % x)
        raise SystemExit(
            "there is text the firmware would cut: nothing gets generated. "
            "Change the attribute (ATTR 9 writes almost everything) or the text."
        )

    # POSITIVE and NEGATIVE control of the gate itself, against the factory:
    pos, neg = _font_check(b, fonts)
    print(
        "   POSITIVE control of the gate: of the %d (attribute, glyph) pairs "
        "the factory actually draws across the 156 screens, %d would "
        "violate the gate (has to be 0)" % (pos[1], pos[0])
    )
    print(
        "   NEGATIVE control: with the indexing shifted by 1 (the wrong "
        "hypothesis) the violations are %d and %d -- i.e. the gate "
        "DISTINGUISHES, it doesn't just accept everything" % (neg[0], neg[1])
    )
    if pos[0] or not (neg[0] and neg[1]):
        raise SystemExit("the font model doesn't hold up against the factory")

    # ---- (i) EACH LABEL'S X: the centering rule, derived from the factory ---
    #
    # `GRILLA` used to nail the text's X at 28 (left) and 111 (right). The
    # factory does NOT do that: it centers. The rule is derived from the
    # anchor (tabla[6][142], the same screen GRILLA came from) and 14/14 is
    # REQUIRED before using it.
    anchor_ind = read_trailer(b, u24(b, T6 + 3 + 3 * 142) - BASE, max_n=200)
    muestras, aciertos = [], 0
    for sp in anchor_ind["slots"]:
        s_a = read_slot(b, sp - BASE)
        attr_vivo = None
        for _o, op, ar in disassemble(b, s_a["prog"] - BASE):
            if op == "ATTR":
                attr_vivo = ar[0]
            elif op == "TXT" and attr_vivo == ATTR_ETIQUETA:
                x_real, _y, ptr = ar
                cod = b[ptr : b.index(b"\x00", ptr)]
                anc = text_width(b, fonts, ATTR_ETIQUETA, cod)
                col = 0 if x_real < 88 else 1
                muestras.append((x_real, anc, col))
                aciertos += centered_x(col, anc) == x_real
    print(
        "\ncontrol (i) -- labels' X gets CENTERED, not nailed down: the rule "
        "x = floor(C - width/2) with C=%.1f/%.1f reproduces %d/%d of "
        "tabla[6][142]'s ATTR %d labels (the same anchor GRILLA came from)"
        % (*CENTRO_COLUMNA, aciertos, len(muestras), ATTR_ETIQUETA)
    )
    if not muestras or aciertos != len(muestras):
        raise SystemExit(
            "the centering rule doesn't reproduce the factory: nothing gets drawn"
        )
    # NEGATIVE control: the OLD rule (X nailed at 28/111) has to fail almost
    # everything -- otherwise the two rules would be indistinguishable and this
    # wouldn't prove anything.
    viejos = sum(1 for x, _a, col in muestras if x == (0x1C, 0x6F)[col])
    print(
        "   NEGATIVE control: the old rule (X nailed at 28/111) gets %d/%d "
        "right -- i.e. centering is NOT a cosmetic rewrite of the same thing"
        % (viejos, len(muestras))
    )
    if viejos == len(muestras):
        raise SystemExit("control (i) doesn't distinguish the two rules")
    for pg in pages:
        for j, bt in enumerate(pg):
            bt["width"] = text_width(b, fonts, ATTR_ETIQUETA, bt["glyphs"])
            bt["x_txt"] = centered_x(j % 2, bt["width"])
            # no label may spill out of its 81-px cell or off the screen
            x0 = GRILLA[j][0]
            if bt["x_txt"] < x0 or bt["x_txt"] + bt["width"] > x0 + 81:
                raise SystemExit(
                    "label %r (%d px) doesn't fit in its button: x=%d, cell "
                    "%d..%d" % (bt["label"], bt["width"], bt["x_txt"], x0, x0 + 81)
                )
    print(
        "   the %d centered labels fit in their 81-px cell; the widest one "
        "is %r at %d px"
        % (
            len(buttons),
            max(buttons, key=lambda x: x["width"])["label"],
            max(x["width"] for x in buttons),
        )
    )

    # --------- (j) THE PAGE INDICATOR: cloned from the factory, not invented ---
    indicador = None
    if not a.sin_indicador:
        ind = indicator_for(b, fonts, glyph_table, len(pages))
        missing_ind = []
        for etq, (_x, _y, ptr) in [
            ("total", ind["total"]),
            ("pages", ind["pages"]),
            ("separator", ind["sep"]),
        ] + [("digit %d" % (k + 1), d) for k, d in enumerate(ind["digitos"])]:
            if ptr is None:  # synthesized digit: its encoding was already checked
                continue
            cod = b[ptr : b.index(b"\x00", ptr)]
            if missing_glyphs(fonts, ind["attr"], cod):
                missing_ind.append(etq)

        def _string_at(ptr, k=None):
            """A factory text pointer's string, or the synthesized one."""
            if ptr is None:
                cod = ind["sintetizados"][k][2]
                return "".join(glyph_table.get(c, "?") for c in cod.rstrip(b"\x00"))
            return "".join(glyph_table.get(c, "?") for c in b[ptr : b.index(b"\x00", ptr)])

        print(
            "\ncontrol (j) -- the '<n> / <total> pages' indicator, COMPOSED for "
            "%d pages (the factory only has indicators for N=3, 6 and 10):\n"
            "   the FORM (ATTR %d, prologue, 'pages', separator) comes whole from "
            "tabla[6][%d]\n"
            "   digits 1..%d are tabla[6][%d]'s factory text pointers "
            "(N=%d); their first %d are IDENTICAL to %d's, "
            "control %d/%d, which is why the missing ones can be requested from it\n"
            "   each digit's X is DERIVED: x + width == %d in 10/10 of the "
            "factory digits (including the '4', 6 px -> x=12, and the '10', "
            "10 px -> x=8, which are the ones that disprove 'x nailed at 13')\n"
            "   from the prologue: total %r at (%d,%d) + %r at (%d,%d)\n"
            "   from each slot: %s + separator %r at (%d,%d)\n"
            "   NEW strings that had to be written: %s; glyphs that would be "
            "missing in ATTR %d: %s"
            % (
                ind["N"],
                ind["attr"],
                ORDINAL_INDICADOR,
                ind["de_fabrica"],
                ORDINAL_INDICADOR_LARGO,
                read_indicator(b, ORDINAL_INDICADOR_LARGO)["N"],
                read_indicator(b, ORDINAL_INDICADOR)["N"],
                ORDINAL_INDICADOR,
                read_indicator(b, ORDINAL_INDICADOR)["N"],
                read_indicator(b, ORDINAL_INDICADOR)["N"],
                X_FIN_DIGITO,
                _string_at(ind["total"][2], ind["N"] - 1),
                ind["total"][0],
                ind["total"][1],
                _string_at(ind["pages"][2]),
                ind["pages"][0],
                ind["pages"][1],
                " ".join(
                    "%r@%d" % (_string_at(d[2], k), d[0])
                    for k, d in enumerate(ind["digitos"])
                ),
                _string_at(ind["sep"][2]),
                ind["sep"][0],
                ind["sep"][1],
                ", ".join(
                    "%r" % _string_at(None, k) for k in sorted(ind["sintetizados"])
                )
                or "none (every digit is a factory pointer)",
                ind["attr"],
                missing_ind or "none",
            )
        )
        if missing_ind:
            raise SystemExit("the indicator has missing glyphs: %s" % missing_ind)
        indicador = ind
    else:
        print("\ncontrol (j) -- page indicator DISABLED by --sin-indicador")

    # ---- (j2) THE TRAILER'S N: the factory goes up to 10, are more needed here? ----
    #
    # The factory's maximum N is 10 (ordinals 69 and 140; census of the 156
    # trailers: N=1 on 142, N=2 on 4, N=3 on 3, N=5 on 3, N=6 on 2, N=10 on 2).
    # Going past that has NO positive precedent, so it's stated explicitly
    # instead of assumed fine. What IS measured is that the firmware has no
    # cap at all: `0x0284BC` (the pager, the only CALL to that routine in the
    # 128 KB) reads N LIVE from the trailer and compares it in 16 bits --
    #
    #   0x08534  MOVLW 0x04 ; MOVWF [0x1EF] ; CALL 0x02BA14   <- ptr_add(4)
    #   0x0853C  MOVLW 0x38 ; MOVLW 0x0D    ; CALL 0x02B90A   <- N -> [0xD38:D39]
    #   0x08552  INCF [0x21D] ; ADDWFC [0x21E]                <- page++ (16 b)
    #   0x0855A  MOVF [0xD38],W ; SUBWF [0x21D],W
    #   0x08562  MOVF [0xD39],W ; SUBWFB [0x21E],W            <- 16-bit subtraction
    #   0x08568  BNC 0x8570 ; CLRF [0x21D] ; CLRF [0x21E]     <- wraps to 0
    #   0x0858A  MOVFF 0xD38,0x21D ; ... SUBWF 0x01           <- backward: N-1
    #
    # The ONLY literals in the whole path are 0x04 (the N field's offset
    # inside the trailer), 0x00 and 0x01 (the arithmetic). There's no
    # comparison against 10, or against 16, or against anything: N=11 runs
    # exactly the same instructions as N=10 and as the N=6 already verified
    # on the device. It's a NEGATIVE control (no cap is found), not a
    # positive one, and that's why it's flagged as something to WATCH.
    n_fabrica = {}
    for _i, t in fabrica:
        n_fabrica[t["N"]] = n_fabrica.get(t["N"], 0) + 1
    max_fab = max(n_fabrica)
    print(
        "\ncontrol (j2) -- trailer N: the new screen needs N=%d; the factory "
        "spreads %s and its maximum is N=%d (ordinals %s)"
        % (
            len(pages),
            dict(sorted(n_fabrica.items())),
            max_fab,
            [i for i, t in fabrica if t["N"] == max_fab],
        )
    )
    if len(pages) > max_fab:
        print(
            "   [ASSUMED] N=%d has NO factory precedent. Measured against it: "
            "pager 0x0284BC reads N live from the trailer (+4) and compares it "
            "in 16 bits; the only literals on the path are 0x04/0x00/0x01, "
            "i.e. there is NO cap. But 'I don't see a cap' isn't a positive "
            "control: the %d pages need to be WATCHED paging with the strips."
            % (len(pages), len(pages))
        )

    # ----------------------------- [10] and [11]: the jump object + commands ---
    sec = relocate.sections(b)
    dest0 = relocate.table(b, sec[11][0])
    id_base = len(dest0)
    kind = relocate.ir_type(b)
    id_jump = id_base  # the first new id: the SHARED jump object
    id_volver = id_base + 1  # the second: the softkey's return to the menu
    ordinal_volver = objs[0][
        "ordinal"
    ]  # where it returns to (the factory uses a fixed one)
    cuerpos_ir, ids_a = relocate.device_objects(a.index, len(cmds), id_base + 2, kind)
    id_gancho = {
        CODE_ENTER: id_base + 2 + len(cuerpos_ir),
        CODE_EXIT: id_base + 3 + len(cuerpos_ir),
    }
    a10, z10 = sec[10]
    s10 = bytearray(b[a10:z10])
    offs = [len(s10)]
    s10 += jump_object(kind, new_ordinal)
    offs.append(len(s10))
    s10 += return_object(kind, ordinal_volver)
    for c in cuerpos_ir:
        offs.append(len(s10))
        s10 += c
    # ---- the commands screen's OWN two hook objects ----
    #
    # THE TRAP THIS AVOIDS. Up through the previous blob, the commands screen
    # inherited menu 74's header as is, hooks included: ordinals 74 and 156
    # pointed at the SAME `[11][1115]`, and `arrow_backlight.py`, which grows objects
    # IN PLACE, had also collapsed 1115/1179/1427 into a single address
    # (0x147FFE) because they ended up with identical bytes. Touching that
    # object to fix the commands screen would have changed the lighting on
    # all THREE Devices menus -- which use the K=4 template, with no right
    # foot: they would have gotten the same "light with no zone" defect
    # that's being fixed here injected into them.
    #
    # That's why the new screen gets its own fresh objects, the way the
    # factory does (0 hook indices shared between ordinals across the 156).
    # The content isn't invented: it's a BYTE-FOR-BYTE COPY of the model
    # ordinal's hook, which references existing factory objects by index --
    # `[11][700]` (channel 4 ON, left foot), `[11][680]` (channel 6 ON, right
    # foot) and `[11][892]` (channels 0 and 2, the strips).
    bytes_gancho = {}
    for cod in (CODE_ENTER, CODE_EXIT):
        bytes_gancho[cod] = arrow_backlight._arma_obj11(atomos_gancho[cod])
        offs.append(len(s10))
        s10 += bytes_gancho[cod]
    print(
        "\nsection [10]: %d B -> %d B  (+%d objects: 1 shared jump + "
        "1 return to menu %d + %d command + %d wrapper + 2 hooks of its own "
        "for the commands screen)"
        % (
            z10 - a10,
            len(s10),
            4 + len(cuerpos_ir),
            ordinal_volver,
            len(cmds),
            len(cmds),
        )
    )
    print(
        "   OWN hooks (byte-for-byte copy of model ordinal %d's, which "
        "reference existing factory objects by index -- no action is created):\n"
        "      ENTER -> new id %d = %s\n"
        "      EXIT  -> new id %d = %s\n"
        "   this way the commands screen does NOT share a hook with menus "
        "74/90/141 (which use K=%d, with no right foot, and don't need to "
        "light channel %d)"
        % (
            first_page,
            id_gancho[CODE_ENTER],
            " ".join("{%04X,%02X}" % x for x in atomos_gancho[CODE_ENTER]),
            id_gancho[CODE_EXIT],
            " ".join("{%04X,%02X}" % x for x in atomos_gancho[CODE_EXIT]),
            K_MENU,
            CANALES_PIE["DER"][0],
        )
    )

    # ---------------------------- [9]: the menu's key registers ---
    a9, z9 = sec[9]
    s9 = bytearray(b[a9:z9])
    # THE EXTRA SHEETS' BOTTOM/LEFT SOFTKEY (bug 1, historical). The K=4
    # template DOES have that zone (0xB3) and the factory declares it on
    # 120/120 slots. It's restored with the EXACT entry that THAT SAME
    # object's sheet 1 has -- 74 and 90 send to {2085,0x72}, 141 to its own
    # {2726,0x7F} -- so ALL of the same menu's sheets behave the same way and
    # no destination is invented. It's the SAME entry for a given object's
    # 3/4/... extra sheets, not one per sheet.
    cod_pie_menu = pies_menu["IZQ"]
    menu_foot_by_object = {}
    for o in objs:
        kr1 = read_key_register(b, o["keyreg"])
        ent = [e for e in kr1 or [] if e[0] == cod_pie_menu]
        if len(ent) != 1:
            raise SystemExit(
                "menu %d's sheet 1 doesn't declare the bottom/left softkey "
                "%#04x: %s" % (o["ordinal"], cod_pie_menu, kr1)
            )
        menu_foot_by_object[o["ordinal"]] = ent[0]

    # THE PACKING (the task). `row_codes` are the K=4 template's 3 row
    # zones, IN top-to-bottom ORDER (`menu_order` already comes sorted that
    # way by geometry, and the foot -- K=4's only remaining zone -- is left
    # out because it's the one with the lowest touch Y, see
    # `template_feet`). For each of the 3 menu objects: the rows ALREADY
    # added in previous runs are read (`read_extra_rows`, compatible with
    # the old 1-row-per-sheet format), this device's is appended at the end
    # (its text doesn't exist yet -- it goes in as `None`, filled in below
    # once `off_name_text` has been emitted) and EVERYTHING gets
    # re-distributed with `menu_sheet_layout`. The extra sheets are
    # REWRITTEN whole: none of them is the factory's sheet 1, so there's no
    # byte that "can't be moved" (the same criterion `reubicar.relocate()`
    # already applies to [9]/[10]/[11]: the old stuff stays behind, unreachable).
    row_codes = menu_order[:MAX_ROWS_PER_SHEET]
    rows_by_object: dict[int, list[tuple[int | None, int]]] = {}
    for o in objs:
        rows_by_object[o["ordinal"]] = read_extra_rows(b, o, menu_order)
    n_extra_before = {k: len(v) for k, v in rows_by_object.items()}
    if len(set(n_extra_before.values())) != 1:
        raise SystemExit(
            "the 3 menus don't have the same number of extra rows -- they're "
            "out of sync, can't be packed: %s" % n_extra_before
        )
    for o in objs:
        rows_by_object[o["ordinal"]].append((None, id_jump))  # this addition's
    n_extra_total = next(iter(n_extra_before.values())) + 1
    layout = menu_sheet_layout(n_extra_total)
    print(
        "\nDEVICES MENU PACKING (the task): %d extra row(s) already existed "
        "(%s) + 1 from this addition = %d -> %d extra sheet(s) of %d each, %s"
        % (
            n_extra_total - 1,
            n_extra_before,
            n_extra_total,
            len(layout),
            MAX_ROWS_PER_SHEET,
            layout,
        )
    )
    # HOW MANY DEVICES FIT (the task's control (d)). The briefing's premise
    # -- "the 54 step (0x26,0x5c,0x92,0xc8) gives 0xfe on the fifth and
    # overflows on the sixth" -- does NOT apply to the live path: those bytes
    # are the row's Y coordinate, and with packing EACH sheet restarts at
    # Y=38/92/146 (see `dibujo.row_y`), so it never goes past 146+19=165.
    # The real bounds, in the order they get hit first. The ONLY measured
    # bound on paging is the largest N the factory allows itself in
    # tabla[6]: it's censused here over the `factory_screens` ORIGINAL
    # ordinals (0..pantallas_fabrica-1 -- this remote's baseline, NOT
    # whatever tabla[6] happens to hold on THIS input blob: on a second or
    # later addition round n6 already includes earlier rounds' own new
    # ordinals, and those must stay OUT of a "what does the factory allow"
    # census), pulling out the 3 menus (which this addition rewrites) so as
    # not to census our own as if it were the factory's.
    ords_menu = {o["ordinal"] for o in objs}
    censo_n_fab: dict[int, list[int]] = {}
    for k in range(factory_screens):
        t_k = read_trailer(b, u24(b, T6 + 3 + 3 * k) - BASE, max_n=200)
        if t_k is not None and k not in ords_menu:
            censo_n_fab.setdefault(t_k["N"], []).append(k)
    n_fab_max = max(censo_n_fab)
    ords_fab_max = censo_n_fab[n_fab_max]
    print(
        "   FACTORY N census in tabla[6] (%d ordinals, minus the 3 menus): "
        "%s" % (factory_screens, {n: len(v) for n, v in sorted(censo_n_fab.items())})
    )
    print(
        "   HOW MANY DEVICES FIT: the menu ends up with N = 1 + ceil(n/%d) "
        "sheets (n = devices added). The largest N the FACTORY uses in "
        "tabla[6] is %d (ordinals %s, measured on the input blob), so up to "
        "n=%d the menu's paging stays within an N already tested by the "
        "factory; past that it's [ASSUMED]. With n=%d this run's N is "
        "%d. The tool's guardrail: `read_trailer(max_n=20)` refuses to "
        "re-read a trailer with N>20 (n>%d) and control (a) aborts. And "
        "section [5] declares its device count in ONE byte: structural "
        "ceiling 255, with `check_section5`/`resolve_section5` aborting if "
        "k1>=n_dev or k2>=n (the firmware checks neither: it hangs)."
        % (
            MAX_ROWS_PER_SHEET,
            n_fab_max,
            ords_fab_max,
            (n_fab_max - 1) * MAX_ROWS_PER_SHEET,
            n_extra_total,
            1 + len(layout),
            19 * MAX_ROWS_PER_SHEET,
        )
    )
    menu_pages_by_object: dict[int, list[list[tuple[int | None, int]]]] = {}
    rel_menu_pages_by_object: dict[int, list[int]] = {}
    for o in objs:
        menu_pages = partition(rows_by_object[o["ordinal"]], layout)
        menu_pages_by_object[o["ordinal"]] = menu_pages
        # WATCH OUT: do NOT call this `offs` -- that name is already used
        # further below by the `objetos_extra` list for section [10] (the
        # jump/IR object's offset), in the SAME `main()` scope. Reusing it
        # here silently overwrote it (a real bug, found by running the
        # control): section [11] ended up extended by 1 object instead of
        # 68, and `arrow_backlight.py` blew up with an IndexError resolving a hook
        # id that pointed outside the table.
        rel_pag_o = []
        for pg in menu_pages:
            entradas = [
                (row_codes[k], ident, 0x7F) for k, (_t, ident) in enumerate(pg)
            ]
            entradas.append(menu_foot_by_object[o["ordinal"]])
            rel_pag_o.append(len(s9))
            s9 += build_key_register(entradas)
        rel_menu_pages_by_object[o["ordinal"]] = rel_pag_o
    print(
        "   per menu object: %s"
        % ", ".join(
            "%d -> %d extra sheet(s) (%s rows)"
            % (
                ordinal,
                len(menu_pages_by_object[ordinal]),
                [len(pg) for pg in menu_pages_by_object[ordinal]],
            )
            for ordinal in sorted(rel_menu_pages_by_object)
        )
    )
    # one commands page = one key register. Each button's zone is chosen by
    # the template's GEOMETRY (section [19]'s reading order), not by the
    # order the register gets serialized in (which is ORDEN_CANONICO).
    rel_by_page, entries_by_page, return_zones = [], [], []
    left_zones = []
    cmd_order_by_page = []
    for pg in pages:
        K = PLANTILLA_POR_CANTIDAD[len(pg)]
        k_order = buttons_by_k[K]  # ONLY the grid cells: the two bottom
        #                                  softkeys get assigned separately
        cmd_order_by_page.append(k_order)
        rel_by_page.append(len(s9))
        entradas = []
        for j, bt in enumerate(pg):
            cod = k_order[j]
            ordinal = [c[0] for c in cmds].index(bt["cmd"])
            entradas.append((cod, ids_a[ordinal], 0x7F))
            bt["cmd_id"] = (a.index << 8) | ordinal
            bt["dev_id"] = (a.index << 8) | 0x01
            bt["ordinal"] = ordinal
            bt["zone"] = cod
            bt["id_a"] = ids_a[ordinal]
        # the LAST zone in reading order is NOT a command: it's the return
        # to the menu. In family A it's 0xB0 (same as 142's 3 sub-screens);
        # in family B it's a different one (0xB4/0xB5), measured on 49/50 --
        # checked against `CODIGO_RETORNO_POR_CANTIDAD` above, in the geometry.
        return_zone = feet_by_k[K]["DER"]
        if return_zone != CODIGO_RETORNO_POR_CANTIDAD[len(pg)]:
            raise SystemExit(
                "the %d-command page sends the return to %#04x, expected "
                "%#04x" % (len(pg), return_zone, CODIGO_RETORNO_POR_CANTIDAD[len(pg)])
            )
        return_zones.append(return_zone)
        entradas.append((return_zone, id_volver, 0x7F))
        # THE BOTTOM/LEFT SOFTKEY (the other half of bug 2). Same zone and
        # same action as the model ordinal: `{2085,0x72}`, a FACTORY object
        # referenced by index. Without this entry, the channel-4 LED the
        # hook lights up would sit over a dead key.
        left_zone = feet_by_k[K]["IZQ"]
        if left_zone != CODIGO_PIE_IZQ_POR_CANTIDAD[len(pg)]:
            raise SystemExit(
                "the %d-command page puts the left foot at %#04x, expected "
                "%#04x" % (len(pg), left_zone, CODIGO_PIE_IZQ_POR_CANTIDAD[len(pg)])
            )
        left_zones.append(left_zone)
        entradas.append((left_zone, *ACCION_PIE_IZQ))
        entries_by_page.append(entradas)
        s9 += build_key_register(entradas)
    n_menu_sheets_total = sum(len(v) for v in menu_pages_by_object.values())
    print(
        "section [ 9]: %d B -> %d B  (+%d key registers: %d extra menu "
        "sheet(s) (packed %d row at a time, zones %s) x %d objects, and %d "
        "commands pages)"
        % (
            z9 - a9,
            len(s9),
            n_menu_sheets_total + len(pages),
            len(layout),
            MAX_ROWS_PER_SHEET,
            [hex(c) for c in row_codes],
            len(objs),
            len(pages),
        )
    )
    for k, pg in enumerate(pages):
        print(
            "   page %d: label -> zone (by [19]'s geometry, not by the "
            "register's order): %s + return at %#04x"
            % (
                k + 1,
                ", ".join("%r->%#04x" % (x["label"], x["zone"]) for x in pg),
                return_zones[k],
            )
        )
    print(
        "   bottom/RIGHT softkey: return to menu %d, declared on ALL %d "
        "pages, each one in its OWN template's zone (%s), labeled %r with "
        "ATTR %d at %s\n"
        "   bottom/LEFT softkey: %s on ALL %d pages (zones %s), the SAME "
        "factory action the model ordinal %d uses in its 6 slots -- labeled "
        "with the two-branch SWITCH ('Activities' / 'Current Activity')"
        % (
            ordinal_volver,
            len(pages),
            "/".join("%#04x" % z for z in sorted(set(return_zones))),
            ETIQUETA_VOLVER,
            ATTR_SOFTKEY,
            SOFTKEY,
            "{%d,%#04x}" % ACCION_PIE_IZQ,
            len(pages),
            "/".join("%#04x" % z for z in sorted(set(left_zones))),
            first_page,
        )
    )

    blob1 = bytearray(
        relocate.relocate(b, {9: bytes(s9), 10: bytes(s10)}, objetos_extra=offs)
    )
    sec1 = relocate.sections(blob1)
    off_menu_pages_by_object = {
        k: [sec1[9][0] + r for r in offs] for k, offs in rel_menu_pages_by_object.items()
    }
    off_pag_cmds = [sec1[9][0] + r for r in rel_by_page]

    # ------------------------------------------------------- the new tail ---
    close1 = u24(blob1, 4) - BASE
    out = bytearray(blob1[: close1 - 2])

    # Every structure the config path walks sequentially gets noted here
    # with its REAL LENGTH, and gets emitted ALIGNED: nothing may straddle a
    # 64 KB boundary (`crosses_page()`). The previous version only looked at
    # 4 of each command record's 25 bytes.
    estructuras: list[tuple[str, int, int]] = [
        (
            "key register of menu %d's sheet %d" % (ordinal, k + 2),
            off,
            1 + 4 * (len(pg) + 1),  # that sheet's rows + the foot softkey
        )
        for ordinal, offs in sorted(off_menu_pages_by_object.items())
        for k, (off, pg) in enumerate(zip(offs, menu_pages_by_object[ordinal]))
    ] + [
        ("key register of page %d" % (k + 1), o, 1 + 4 * len(e))
        for k, (o, e) in enumerate(zip(off_pag_cmds, entries_by_page))
    ]
    relleno_total = 0

    def emit(blk, etq: str | None = None) -> int:
        nonlocal relleno_total
        at = len(out)
        if etq is not None and crosses_page(at, len(blk)):
            gap = PAGE_SIZE - (at % PAGE_SIZE)
            out.extend(b"\x00" * gap)
            relleno_total += gap
            at = len(out)
        out.extend(blk)
        if etq is not None:
            estructuras.append((etq, at, len(blk)))
        return at

    off_name_text = emit(inline_text(TAG_NAME, Y_ROW_0 + 19, name_text)) + 3
    for pg in pages:
        for j, bt in enumerate(pg):
            y = GRILLA[j][3]
            bt["off_txt"] = emit(inline_text(bt["x_txt"], y, bt["glyphs"])) + 3
    off_txt_volver = emit(inline_text(SOFTKEY[0], SOFTKEY[1], txt_volver)) + 3
    # the indicator digits the factory doesn't have (11 onward): emitted
    # here, alongside the other strings, and they repoint `indicador` BEFORE
    # it's used to build the prologue and the page programs.
    resolve_indicator(indicador, emit)
    if indicador is not None and indicador["sintetizados"]:
        print(
            "   indicator digits written as a new string: %s"
            % ", ".join(
                "%d -> %#08x" % (k + 1, indicador["digitos"][k][2])
                for k in sorted(indicador["sintetizados"])
            )
        )

    ir, idx_ir, relleno_ir = ir_block(protos, cmds, a.index, len(out))
    off_ir = emit(ir)
    relleno_total += relleno_ir
    for e in idx_ir:
        estructuras.append(("command record %r" % e["name"], e["off_registro"], 25))
        estructuras.append(("IR unit %r" % e["name"], e["off_press"], e["largo"]))
    print(
        "\nIR block: %d commands, %d B at %#08x..%#08x (64 KB alignment "
        "padding: %d B)" % (len(idx_ir), len(ir), off_ir, off_ir + len(ir), relleno_ir)
    )

    # -------------------- extra sheets of the 3 menu objects (74/90/141) ---
    #
    # Now that `off_name_text` exists, the `None` placeholders
    # `rows_by_object` left for THIS addition's row get filled in (the
    # already-existing rows, read back from the input blob, already carried
    # their own text pointer -- no need to re-emit those strings).
    for o in objs:
        menu_pages_by_object[o["ordinal"]] = [
            [(off_name_text if t is None else t, ident) for t, ident in pg]
            for pg in menu_pages_by_object[o["ordinal"]]
        ]

    nuevos_trailers = []
    cabeceras_nuevas = []
    for o in objs:
        old_header = read_key_register(blob1, o["hdr"])
        new_header = [e for e in old_header if e[0] not in CODIGOS_FRANJA]
        quitadas = [e for e in old_header if e[0] in CODIGOS_FRANJA]
        off_hdr = emit(
            build_raw_register(new_header), "header of menu %d" % o["ordinal"]
        )
        cabeceras_nuevas.append((o, off_hdr, old_header, new_header, quitadas))

        menu_pages = menu_pages_by_object[o["ordinal"]]
        offs_keyreg = off_menu_pages_by_object[o["ordinal"]]
        slots_extra = []
        for k, pg in enumerate(menu_pages):
            # EVERY extra sheet's foot = the SAME one this object's sheet 1
            # draws (the factory doesn't vary the foot sheet by sheet)
            at = len(out)
            prog_k = emit(
                program_menu_sheet(
                    o["prologo"],
                    [t for t, _ident in pg],
                    pie=sheet1_foot_by_object[o["ordinal"]],
                    own_off=at,
                )
            )
            if prog_k != at:
                raise SystemExit(
                    "menu %d's sheet %d program moved from %#08x to %#08x"
                    % (o["ordinal"], k + 2, at, prog_k)
                )
            slots_extra.append(
                emit(
                    bytes([K_MENU]) + p(offs_keyreg[k]) + p(prog_k),
                    "slot for menu %d's sheet %d" % (o["ordinal"], k + 2),
                )
            )
        # the factory's sheet 1, UNTOUCHED (not one byte, not even copied) +
        # ALL the extra sheets, PACKED `MAX_ROWS_PER_SHEET` at a time. It
        # used to do `list(o["slots"]) + [slot1]` -- keep the old extra
        # sheets and add one more -- which is exactly the bug (with 2
        # devices it gave 2 sheets of 1 row instead of 1 sheet of 2).
        slots_menu = [o["slots"][0]] + slots_extra
        tr = emit(
            bytes([0x00])
            + p(off_hdr)
            + len(slots_menu).to_bytes(2, "little")
            + b"".join(p(s) for s in slots_menu),
            "trailer of menu %d" % o["ordinal"],
        )
        nuevos_trailers.append((o, tr))
        print(
            "   object %3d: new header %#08x (%d->%d entries, removed %s), "
            "%d extra sheet(s) %s, trailer %#08x N=%d (sheet 1 %#08x untouched)"
            % (
                o["ordinal"],
                off_hdr,
                len(old_header),
                len(new_header),
                " ".join("%02x:id=%d:cls=%#04x" % e for e in quitadas) or "none",
                len(slots_extra),
                [hex(s) for s in slots_extra],
                tr,
                len(slots_menu),
                o["slots"][0],
            )
        )

    # ------------- the commands screen, ONE ordinal with N sub-screens ---
    #
    # Now N>1, so the header **also** cannot declare 0xAE/0xAF: if it does,
    # the strip's event gets swallowed and the screen doesn't page (factory
    # census: 142/142 with N=1 declare them, 0/14 with N>1).
    #
    # And also hooks 0x06/0x07 get repointed to the screen's OWN objects:
    # 74's header sends them to `[11][1115]`/`[11][1116]`, which are the
    # menu's -- they light the left foot's channel and nothing else. The
    # screen's own light both feet + the strips, which is what the new
    # template declares.
    old_cmd_header = read_key_register(blob1, objs[0]["hdr"])
    new_cmd_header = [
        (cod, id_gancho.get(cod, ident), cls)
        for cod, ident, cls in old_cmd_header
        if cod not in CODIGOS_FRANJA
    ]
    off_hdr_cmd = emit(build_raw_register(new_cmd_header), "commands screen header")
    print(
        "   commands screen header: hooks repointed %s -> %s "
        "(the screen's own objects), the rest of %d's header untouched"
        % (
            {
                "%02x" % cod: ident
                for cod, ident, cls in old_cmd_header
                if cod in id_gancho
            },
            {"%02x" % c: i for c, i in sorted(id_gancho.items())},
            objs[0]["ordinal"],
        )
    )
    # the screen's own prologue: 74's + the indicator's TOTAL. The original
    # ISN'T touched (the 3 menu screens keep using it, and they carry no indicator).
    if indicador is None:
        prologo_cmd = objs[0]["prologo"]
    else:
        prologo_cmd = emit(
            prologue_with_indicator(blob1, objs[0]["prologo"], indicador)
        )
    slots_cmd = []
    for k, pg in enumerate(pages):
        par = (
            None
            if indicador is None
            else (indicador["attr"], indicador["digitos"][k], indicador["sep"])
        )
        retorno_xy, retorno_attr = RETORNO_POR_CANTIDAD[len(pg)]
        at = len(out)
        prog_k = emit(
            program_commands(
                prologo_cmd,
                pg,
                off_txt_volver,
                par,
                retorno_xy=retorno_xy,
                retorno_attr=retorno_attr,
                own_off=at,
            )
        )
        if prog_k != at:
            raise SystemExit(
                "page %d's program moved from %#08x to %#08x" % (k + 1, at, prog_k)
            )
        K = PLANTILLA_POR_CANTIDAD[len(pg)]
        slots_cmd.append(
            emit(
                bytes([K]) + p(off_pag_cmds[k]) + p(prog_k),
                "slot for page %d" % (k + 1),
            )
        )
        print(
            "   page %d/%d: K=%-2d %d buttons, keyreg %#08x, prog %#08x, slot %#08x"
            % (
                k + 1,
                len(pages),
                K,
                len(pg),
                off_pag_cmds[k],
                prog_k,
                slots_cmd[-1],
            )
        )
    tr_cmd = emit(
        bytes([0x00])
        + p(off_hdr_cmd)
        + len(slots_cmd).to_bytes(2, "little")
        + b"".join(p(s) for s in slots_cmd),
        "commands screen trailer",
    )
    print(
        "\ncommands screen (new ordinal %d): header %#08x (%d's WITHOUT "
        "0xAE/0xAF, since it's now N=%d and pages), prologue %#08x%s, trailer %#08x"
        % (
            new_ordinal,
            off_hdr_cmd,
            objs[0]["ordinal"],
            len(slots_cmd),
            prologo_cmd,
            ""
            if indicador is None
            else " (copy of %#08x + the indicator's total)" % objs[0]["prologo"],
            tr_cmd,
        )
    )

    # ---------------------------------------------------- tabla[6] whole ---
    t6_changes = {o["ordinal"]: tr for o, tr in nuevos_trailers}
    new_t6 = emit(build_table6(blob1, t6_changes, [tr_cmd]), "tabla[6] whole")
    print(
        "\ntabla[6] relocated whole: %#08x, %d entries (156 + 1), 3 repointed "
        "(%s)" % (new_t6, n6 + 1, sorted(t6_changes))
    )

    # ------------------- section [5]: the new device's entry ---
    # Appended AT THE END, after tabla[6], so that everything already
    # verified on the device keeps its exact offset: the new blob is the
    # previous one + these two blocks + the 3-byte repoint at 0x20.
    new_sub = emit(
        build_subtable([e["off_registro"] for e in idx_ir]),
        "sub-table for device %d" % a.index,
    )
    new_s5 = emit(
        build_section5([d["sub"] for d in devs5] + [new_sub]), "section [5]"
    )
    print(
        "\nsection [5] extended: device %d's sub-table at %#08x "
        "(%d B: <00><u16 %d><%d x ptr24 -> record+11>), new header at "
        "%#08x (%d B: <%d><4 x ptr24>, the first 3 UNTOUCHED)"
        % (
            a.index,
            new_sub,
            SUB_ENC + 3 * len(idx_ir),
            len(idx_ir),
            len(idx_ir),
            new_s5,
            S5_ENC + 3 * (len(devs5) + 1),
            len(devs5) + 1,
        )
    )

    close_blob(out)
    out[MAESTRO_S5 : MAESTRO_S5 + 4] = (BASE + new_s5).to_bytes(4, "little")
    out[MAESTRO_T6 : MAESTRO_T6 + 4] = (BASE + new_t6).to_bytes(4, "little")
    # the checksum gets recalculated AFTER repointing
    nc = u24(out, 4) - BASE
    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2], out[nc - 1] = lo, hi
    fresh = bytes(out)

    # ------------------------------------- key backlighting ---
    # This used to be done by a separate `arrow_backlight.py` run over this tool's
    # output, which is why `output/final_todo.bin` -- the blob that's
    # ALREADY running on the device -- wasn't reproducible with a single
    # command. It's integrated here so the artifact that gets burned is
    # exactly the one these controls validate. It only copies 3 tabla[11]
    # objects to the tail and repoints their entries: it moves nothing, and
    # stays above nada_se_movio's cutoff.
    before_arrows = fresh
    ordinales_led = tuple(o["ordinal"] for o in objs) + (new_ordinal,)
    fresh, inf_flechas = arrow_backlight.turn_on_paging_arrows(fresh, ordinales=ordinales_led)
    print("\npaging-key backlighting (arrow_backlight.py, integrated):")
    for ln in inf_flechas:
        print("   " + ln)

    print("\nblob: %d -> %d B  (+%d)" % (len(b), len(fresh), len(fresh) - len(b)))

    # ================================================== CONTROLS ==========
    fallos = []

    # (0) the arrows: the 3 ENTER hooks have to carry the turn-on action, and
    # the EXIT ones the turn-off. Positive and negative control with the
    # same reader, over the FINAL blob and over the one from before applying them.
    def _hooks(bl):
        t11 = arrow_backlight._section(bl, 11)
        t6 = arrow_backlight._section(bl, 6)
        r = {}
        for ordi in ordinales_led:
            tr = u24(bl, t6 + 3 + 3 * ordi) - BASE
            hdr = u24(bl, tr + 1) - BASE
            for cod, acc in (
                (arrow_backlight.CODE_ENTER, arrow_backlight.ACC_FLECHAS_ON_02),
                (arrow_backlight.CODE_EXIT, arrow_backlight.ACC_FLECHAS_OFF_02),
            ):
                g = [
                    x
                    for x in arrow_backlight._atomos_cab(bl, hdr)
                    if x[0] == cod and x[2] == arrow_backlight.CATEGORY_ACTION
                ]
                r[(ordi, cod)] = (
                    bool(g)
                    and (acc, arrow_backlight.CATEGORY_ACTION)
                    in arrow_backlight._obj11(bl, t11, g[0][1])[1]
                )
        return r

    g_desp, g_before = _hooks(fresh), _hooks(before_arrows)
    ok_led = all(g_desp.values())
    print(
        "control (0) -- paging keys lit: %d/%d hooks "
        "(ENTER->%#06x, EXIT->%#06x) on ordinals %s: %s\n"
        "   NEGATIVE control -- before applying arrow_backlight.py it was %d/%d "
        "(the ENTER ones were missing: that's what it adds)"
        % (
            sum(g_desp.values()),
            len(g_desp),
            arrow_backlight.ACC_FLECHAS_ON_02,
            arrow_backlight.ACC_FLECHAS_OFF_02,
            "/".join(str(o) for o in ordinales_led),
            "OK" if ok_led else "FAIL",
            sum(g_before.values()),
            len(g_before),
        )
    )
    if not ok_led:
        fallos.append("the paging keys didn't end up lit")
    # the NEGATIVE control ("arrow_backlight.py added something") only makes sense
    # if there was something to add. On a blob with an earlier addition the
    # 3 menus are ALREADY lit, and the new commands screen inherits the
    # hooks from its model header: arrow_backlight.py has nothing to do and that's
    # correct, not a failure. What's never acceptable is for it to TURN OFF
    # something that was on.
    apagados = [k for k, v in g_before.items() if v and not g_desp.get(k)]
    if apagados:
        fallos.append("arrow_backlight.py TURNED OFF hooks that were already on: %s" % apagados)
    if sum(g_before.values()) == sum(g_desp.values()):
        print(
            "   (arrow_backlight.py added nothing: the %d hooks were already there -- "
            "the input blob already had an addition. The positive control is "
            "still 8/8.)" % sum(g_before.values())
        )

    # (a) the new trailers with the SAME reader as the 156 factory ones.
    # The task's control (a): the sheet count has to be 1+ceil(n/3).
    for o, tr in nuevos_trailers:
        t = read_trailer(fresh, tr)
        n_esperado = 1 + len(menu_pages_by_object[o["ordinal"]])
        if t is None or t["N"] != n_esperado or t["flag"] not in flags_f:
            fallos.append(
                "new trailer for %d doesn't parse or doesn't have the "
                "factory's shape (N=%s, expected %d = 1 + ceil(%d/%d))"
                % (
                    o["ordinal"],
                    t and t["N"],
                    n_esperado,
                    n_extra_total,
                    MAX_ROWS_PER_SHEET,
                )
            )
            continue
        # The task's control (d): sheet 1 stays BYTE FOR BYTE the same as at
        # the factory -- verified by offset identity (nothing is ever written
        # there) and the whole slot also gets re-compared against the one
        # from `objs` (read from
        # `b`, the INPUT blob, before any change).
        if t["slots"][0] - BASE != o["slots"][0]:
            fallos.append(
                "new trailer for %d's sheet 1 is NOT the original (slot0 "
                "%#08x, expected %#08x)"
                % (o["ordinal"], t["slots"][0] - BASE, o["slots"][0])
            )
        elif b[o["slot"] : o["slot"] + 7] != fresh[o["slots"][0] : o["slots"][0] + 7]:
            fallos.append("%d's sheet 1 slot changed bytes" % o["ordinal"])
        for j, sp in enumerate(t["slots"]):
            s = read_slot(fresh, sp - BASE)
            if s is None:
                fallos.append("slot%d of %d doesn't parse" % (j, o["ordinal"]))
                continue
            if s["K"] not in ks_f:
                fallos.append(
                    "slot%d of %d: K=%#x doesn't exist at the factory"
                    % (j, o["ordinal"], s["K"])
                )
            if fresh[s["prog"] - BASE] not in ops_f:
                fallos.append(
                    "slot%d of %d: the program starts at %#04x, no factory "
                    "slot starts like that" % (j, o["ordinal"], fresh[s["prog"] - BASE])
                )
            reg = read_key_register(fresh, s["keyreg"] - BASE)
            if reg is None:
                fallos.append("slot%d of %d: invalid key register" % (j, o["ordinal"]))
                continue
            pos = [CANONICAL_ORDER.index(e[0]) for e in reg]
            if pos != sorted(pos):
                fallos.append(
                    "slot%d of %d: BROKEN key order %s" % (j, o["ordinal"], pos)
                )
    t_cmd = read_trailer(fresh, tr_cmd, max_n=200)
    slots_leidos, regs_cmd = [], []
    if t_cmd is None or t_cmd["N"] != len(pages) or t_cmd["flag"] not in flags_f:
        fallos.append(
            "the commands screen's trailer doesn't parse or N!=%d" % len(pages)
        )
    else:
        for k, sp in enumerate(t_cmd["slots"]):
            s_k = read_slot(fresh, sp - BASE)
            slots_leidos.append(s_k)
            esperado_k = PLANTILLA_POR_CANTIDAD[len(pages[k])]
            if s_k is None or s_k["K"] != esperado_k:
                fallos.append(
                    "commands screen slot %d doesn't have K=%d" % (k, esperado_k)
                )
                regs_cmd.append(None)
                continue
            if fresh[s_k["prog"] - BASE] not in ops_f:
                fallos.append("page %d's program doesn't start like the factory's" % k)
            r_k = read_key_register(fresh, s_k["keyreg"] - BASE)
            regs_cmd.append(r_k)
            # n grid buttons + BOTH bottom softkeys
            if r_k is None or len(r_k) != len(pages[k]) + 2:
                fallos.append(
                    "page %d's key register doesn't have %d zones "
                    "(%d grid + the 2 bottom softkeys)"
                    % (k, len(pages[k]) + 2, len(pages[k]))
                )
            elif [CANONICAL_ORDER.index(e[0]) for e in r_k] != sorted(
                CANONICAL_ORDER.index(e[0]) for e in r_k
            ):
                fallos.append("page %d's key register has a broken order" % k)
    print(
        "control (a): the %d menu trailers parse with N=1+%d (packed "
        "%d at a time) and slot0 SAME as the original; the commands screen "
        "parses with N=%d and its %d slots give K=%s with %s zones in canonical order"
        % (
            len(nuevos_trailers),
            len(layout),
            MAX_ROWS_PER_SHEET,
            len(pages),
            len(slots_leidos),
            "/".join(str(s["K"]) if s else "?" for s in slots_leidos),
            "/".join(str(len(r)) if r else "?" for r in regs_cmd),
        )
    )

    # (a3) THE INHERITED PAGER: the new screen inherits from 74 the header's
    # `0x2d` hook and the prologue's atom. Since 74 has N=1 and the new one
    # N>1, it has to be proven that those TWO objects are the same ones the
    # factory's multi-page screens use -- otherwise it would be inheriting a
    # foreign page state. They're compared by CONTENT against ordinal 142's
    # (N=3) and the indicator's (N=6), the two K=5 / 6-page screens already
    # tested by the factory.
    def _obj_by_id(bl, i):
        s_ = relocate.sections(bl)
        d_ = relocate.table(bl, s_[11][0])
        return _slots(bl, d_[i]) if 0 <= i < len(d_) else None

    new_final_header = read_key_register(fresh, off_hdr_cmd)
    id_2d = next((e[1] for e in new_final_header if e[0] == 0x2D), None)
    new_pager = _obj_by_id(fresh, id_2d) if id_2d is not None else None
    referencias = {}
    for ordi in (142, ORDINAL_INDICADOR):
        tr_f = read_trailer(b, u24(b, T6 + 3 + 3 * ordi) - BASE, max_n=200)
        cab_f = read_header(b, tr_f["hdr"] - BASE)[0]
        i2d = next((e[1] for e in cab_f if e[0] == 0x2D), None)
        referencias[ordi] = (tr_f["N"], i2d, _obj_by_id(b, i2d) if i2d else None)
    ok_pag = any(v[2] is not None and v[2] == new_pager for v in referencias.values())
    # and the prologue's atom, which the factory repeats identically on 74 / 142 / the indicator's
    new_atoms = [
        ar
        for _o, op, ar in disassemble(fresh, prologo_cmd)
        if op == "ATOMO" and ar[1] == 0x7F
    ]
    at_ref = [
        ar
        for _o, op, ar in disassemble(
            b, indicador["prologo"] if indicador else objs[0]["prologo"]
        )
        if op == "ATOMO" and ar[1] == 0x7F
    ]
    ok_atomo = len(new_atoms) == len(at_ref) == 1 and _obj_by_id(
        fresh, new_atoms[0][0]
    ) == _obj_by_id(b, at_ref[0][0])
    print(
        "control (a3) -- THE INHERITED PAGER: the new header declares the "
        "0x2d hook -> [11][%s] = %s, and the factory uses %s in its "
        "multi-page screens %s: SAME CONTENT %s.\n"
        "   the prologue's atom -> %s, against the %d-page factory screen's: "
        "SAME CONTENT %s (i.e. the atom does NOT carry its own page state: "
        "74/N=1, 142/N=3 and %d/N=6 all three have the same one)"
        % (
            id_2d,
            new_pager,
            {k: v[2] for k, v in referencias.items()},
            {k: "N=%d" % v[0] for k, v in referencias.items()},
            "YES" if ok_pag else "NO",
            _obj_by_id(fresh, new_atoms[0][0]) if new_atoms else None,
            referencias[ORDINAL_INDICADOR][0],
            "YES" if ok_atomo else "NO",
            ORDINAL_INDICADOR,
        )
    )
    if not (ok_pag and ok_atomo):
        fallos.append(
            "the new screen doesn't inherit the same pager/atom as the "
            "factory's multi-page ones"
        )

    # (a2) extended tabla[6]: 157 entries, the 157th resolves to the new trailer
    new_n6 = u16(fresh, new_t6)
    ok_t6 = (
        int.from_bytes(fresh[MAESTRO_T6 : MAESTRO_T6 + 4], "little") - BASE == new_t6
    )
    ok_last = (
        new_n6 == n6 + 1
        and u24(fresh, new_t6 + 3 + 3 * new_ordinal) - BASE == tr_cmd
    )
    print(
        "control (a2): master index[6] repointed to %#08x: %s; tabla[6] has "
        "%d entries and the %d resolves to the commands trailer: %s"
        % (
            new_t6,
            "YES" if ok_t6 else "NO",
            new_n6,
            new_ordinal,
            "YES" if ok_last else "NO",
        )
    )
    if not (ok_t6 and ok_last):
        fallos.append("tabla[6] didn't end up correctly extended/repointed")

    # (b) the canonical order across the WHOLE new blob (via the NEW tabla[6])
    n6c = u16(fresh, new_t6)
    rotos = 0
    no_parsean = 0
    for i in range(n6c):
        t = read_trailer(fresh, u24(fresh, new_t6 + 3 + 3 * i) - BASE, max_n=200)
        if t is None:
            no_parsean += 1
            continue
        for sp in t["slots"]:
            s = read_slot(fresh, sp - BASE)
            reg = s and read_key_register(fresh, s["keyreg"] - BASE)
            if not reg:
                continue
            pos = [CANONICAL_ORDER.index(e[0]) for e in reg if e[0] in CANONICAL_ORDER]
            if pos != sorted(pos):
                rotos += 1
    print(
        "control (b): %d/%d (new) tabla[6] trailers parse; key registers "
        "with a broken order: %d" % (n6c - no_parsean, n6c, rotos)
    )
    if rotos or no_parsean:
        fallos.append("%d don't parse / %d with a broken order" % (no_parsean, rotos))

    # (b2) THE STRIP INVARIANT, now over the new tabla[6]
    def census_over(new_blob, table_off):
        n = u16(new_blob, table_off)
        r = {"n1_con": 0, "n1_sin": 0, "nm_con": [], "nm_sin": 0}
        for k in range(n):
            t = read_trailer(
                new_blob, u24(new_blob, table_off + 3 + 3 * k) - BASE, max_n=200
            )
            if t is None:
                continue
            c = read_header(new_blob, t["hdr"] - BASE)
            if c is None:
                continue
            has = any(e[0] in CODIGOS_FRANJA for e in c[0])
            if t["N"] > 1:
                (
                    r["nm_con"].append(k)
                    if has
                    else r.__setitem__("nm_sin", r["nm_sin"] + 1)
                )
            else:
                r["n1_con" if has else "n1_sin"] += 1
        return r

    cen2 = census_over(fresh, new_t6)
    print(
        "control (b2) -- the invariant that was broken in the previous burn:\n"
        "   N=1 -> %d declare strips / %d don't ;  N>1 -> %d declare / %d don't "
        "(0 expected on N>1)"
        % (cen2["n1_con"], cen2["n1_sin"], len(cen2["nm_con"]), cen2["nm_sin"])
    )
    if cen2["nm_con"]:
        fallos.append("objects with N>1 that declare the strips: %s" % cen2["nm_con"])
    for o, off_hdr, old_header, new_header, quitadas in cabeceras_nuevas:
        rel = read_key_register(fresh, off_hdr)
        # 2 removed if the header came from the factory; 0 if it came from
        # an earlier addition, which had already pulled them out. What
        # matters is the result: that the re-read header declares NO strip
        # at all (verified by (b2)).
        if rel != new_header or len(quitadas) not in (0, 2):
            fallos.append(
                "%d's new header doesn't re-read the same or 2 strips weren't removed"
                % o["ordinal"]
            )
    print(
        "   the %d menu headers re-read with read_key_register() and keep "
        "their remaining 4 entries (06 07 b7 2d) byte for byte" % len(cabeceras_nuevas)
    )

    # (c) EVERY row of EVERY extra sheet: the key register is the expected
    # one (its rows + the foot softkey) AND its jump object resolves exactly
    # to the real factory pattern. Also the task's control (b)/(c): each row
    # resolves to a DIFFERENT screen ordinal (nobody repeats, and nobody
    # resolves to their neighbor) and the 3 menu objects (74/90/141) show
    # the SAME set of devices.
    sec_final = relocate.sections(fresh)
    dest_final = relocate.table(fresh, sec_final[11][0])
    salto_ok = True
    ordinals_by_object: dict[int, list[int | None]] = {}
    for o, tr in nuevos_trailers:
        t = read_trailer(fresh, tr)
        menu_pages = menu_pages_by_object[o["ordinal"]]
        ordinales_o: list[int | None] = []
        for k, pg in enumerate(menu_pages):
            s_k = read_slot(fresh, t["slots"][1 + k] - BASE)
            reg_k = read_key_register(fresh, s_k["keyreg"] - BASE)
            entradas_esp = [
                (row_codes[j], ident, 0x7F) for j, (_t, ident) in enumerate(pg)
            ] + [menu_foot_by_object[o["ordinal"]]]
            if reg_k != read_key_register(build_key_register(entradas_esp), 0):
                salto_ok = False
                fallos.append(
                    "%d's sheet %d doesn't declare its rows + foot softkey: %s"
                    % (o["ordinal"], k + 2, reg_k)
                )
            for j, (_t, ident) in enumerate(pg):
                # each row resolves to `<{kind,0x75}><{ordinal,0x7E}>...` --
                # the same pattern `jump_object()` emits (the final
                # `{1,0x9A}` is only required, below, for THIS addition's
                # object: the ones inherited from previous runs have the
                # factory's shape, already verified when they were created).
                rs_j = (
                    _slots(fresh, dest_final[ident])
                    if ident < len(dest_final)
                    else None
                )
                ord_j = next((v for v, cl in (rs_j or []) if cl == 0x7E), None)
                if rs_j is None or rs_j[:2] != [(kind, 0x75), (ord_j, 0x7E)]:
                    salto_ok = False
                    fallos.append(
                        "row %d of %d's sheet %d (id %d): slots %s don't "
                        "have the shape {tipo,0x75}{ordinal,0x7E}..."
                        % (j, o["ordinal"], k + 2, ident, rs_j)
                    )
                ordinales_o.append(ord_j)
        ordinals_by_object[o["ordinal"]] = ordinales_o
    # THIS addition's own jump object in particular, with the complete
    # 3-slot pattern (the one that was already checked before packing)
    rs = _slots(fresh, dest_final[id_jump]) if id_jump < len(dest_final) else None
    esperado = [(kind, 0x75), (new_ordinal, 0x7E), (1, 0x9A)]
    if rs != esperado:
        salto_ok = False
        fallos.append(
            "this addition's jump object (id %d) has slots %s, expected "
            "%s" % (id_jump, rs, esperado)
        )
    # CONTROL (b): each device appears EXACTLY once -- no ordinal repeated
    # and none missing, within a single menu object.
    for ordinal_menu, ords in ordinals_by_object.items():
        if None in ords:
            salto_ok = False
            fallos.append(
                "menu %d has a row that doesn't resolve an ordinal" % ordinal_menu
            )
        elif len(set(ords)) != len(ords):
            salto_ok = False
            fallos.append(
                "menu %d repeats a screen ordinal among its rows: %s"
                % (ordinal_menu, ords)
            )
    # the 3 menu objects (74/90/141) have to show the SAME set of devices,
    # in the same order -- they're 3 replicas of the same menu.
    sets_by_object = {k: v for k, v in ordinals_by_object.items()}
    ref = next(iter(sets_by_object.values()))
    sincronizados = all(v == ref for v in sets_by_object.values())
    if not sincronizados:
        salto_ok = False
        fallos.append(
            "the 3 menu objects do NOT show the same set/order of "
            "devices: %s" % sets_by_object
        )
    print(
        "control (c): %d extra sheet(s) x %d menu objects, every row with "
        "its key register and its jump object {tipo,0x75}{ordinal,0x7E}...: "
        "%s\n"
        "   CONTROL (b) -- each device appears EXACTLY once per menu "
        "object, and the 3 objects (74/90/141) agree byte for byte on "
        "which ordinal they show: %s -- ordinals, in order: %s"
        % (
            len(layout),
            len(objs),
            "OK" if salto_ok else "FAIL",
            "YES" if sincronizados else "NO",
            ref,
        )
    )
    if not salto_ok:
        fallos.append("the jump object doesn't reproduce the factory's pattern")

    # (c2) the softkey's RETURN, with the factory's 2-slot shape. Each page
    # declares its return in ITS OWN template's ZONE (zonas_volver[k] --
    # family A and B use different zones, see above).
    rv = _slots(fresh, dest_final[id_volver]) if id_volver < len(dest_final) else None
    esperado_v = [(kind, 0x75), (ordinal_volver, 0x7E)]
    con_retorno = sum(
        1
        for k, r in enumerate(regs_cmd)
        if r and (return_zones[k], id_volver, 0x7F) in r
    )
    volver_ok = rv == esperado_v and con_retorno == len(pages)
    print(
        "control (c2): the return (id %d) has slots %s (expected %s -- 2 "
        "slots, WITHOUT the {1,0x9A}, just like 142's 3 returns) and it's "
        "declared, each one in its own template's zone (%s), %d/%d pages: %s"
        % (
            id_volver,
            rv,
            esperado_v,
            "/".join("%#04x" % z for z in sorted(set(return_zones))),
            con_retorno,
            len(pages),
            "OK" if volver_ok else "FAIL",
        )
    )
    if not volver_ok:
        fallos.append("the commands screen's return didn't get hooked up")

    # (c3) NO new object may end up ORPHANED. This is `output/pagina2b.bin`'s
    # failure mode, which was already burned: it had tabla[6] extended to
    # 157 and the well-formed {156,0x7E} object, but NO key register
    # referenced it, so there was no way to trigger it. The 157 ordinals x
    # all their slots get swept and every new id is required to have
    # something point at it.
    referenciados = set()
    for i in range(u16(fresh, new_t6)):
        t = read_trailer(fresh, u24(fresh, new_t6 + 3 + 3 * i) - BASE, max_n=200)
        if t is None:
            continue
        for sp in t["slots"]:
            s = read_slot(fresh, sp - BASE)
            reg = s and read_key_register(fresh, s["keyreg"] - BASE)
            for e in reg or []:
                if e[2] == 0x7F:
                    referenciados.add(e[1])
    # Now EVERYTHING is required to be hooked up: THIS addition's jump, the
    # return, the commands, AND ALSO the row for EVERY device (inherited or
    # new) on EVERY one of the packed sheets -- stricter than before (which
    # only looked at the new jump), because packing rewrites the extra
    # sheets whole and a badly-repointed inherited row would end up orphaned.
    all_row_ids = sorted(
        {
            ident
            for pgs in menu_pages_by_object.values()
            for pg in pgs
            for _t, ident in pg
        }
    )
    debe_engancharse = (
        [("row (id=%d)" % i, i) for i in all_row_ids]
        + [("return", id_volver)]
        + [("command %r" % bt["label"], bt["id_a"]) for bt in buttons]
    )
    huerfanos = [n for n, q in debe_engancharse if q not in referenciados]
    print(
        "control (c3) -- ORPHANS: swept the new tabla[6]'s %d ordinals and "
        "their slots; new ids referenced by some class-0x7F zone: %d/%d. "
        "Orphans: %s"
        % (
            u16(fresh, new_t6),
            len(debe_engancharse) - len(huerfanos),
            len(debe_engancharse),
            huerfanos or "none",
        )
    )
    if huerfanos:
        fallos.append("new objects nobody references: %s" % huerfanos)

    # (d) the button -> (command, device) chain for what ALREADY existed + the new
    before = relocate.chain(b)
    after = relocate.chain(fresh)
    conservados = {k: v for k, v in after.items() if k in before}
    igual = conservados == before
    new_buttons = {k: v for k, v in after.items() if k not in before}
    print(
        "\ncontrol (d): reubicar.chain() -- %d pre-existing buttons, %d after; "
        "the pre-existing ones stay %s; new buttons: %d"
        % (
            len(before),
            len(after),
            "IDENTICAL" if igual else "DIFFERENT",
            len(new_buttons),
        )
    )
    if not igual:
        fallos.append("the pre-existing buttons' chain changed")
    # Every grid zone has to resolve to the (cmd_id, dev_id) PAIR it was
    # wired to -- the cmd_id alone isn't enough, because the dev_id is
    # precisely what selects section [5]'s sub-table.
    esperados_par = {(bt["cmd_id"], bt["dev_id"]) for bt in buttons}
    present = set(new_buttons.values())
    missing_pairs = esperados_par - present
    if missing_pairs:
        fallos.append(
            "(cmd_id, dev_id) pairs missing from the chain: %s"
            % sorted("%#06x/%#06x" % x for x in missing_pairs)
        )
    # and each one also has to be REACHABLE through the final blob's section
    # [5], walking it with the firmware's exact arithmetic and verifying
    # that the record it lands on is ITS OWN. This is what avoids the hang,
    # so it's done EXHAUSTIVELY: all 32, one by one, with their reason.
    devs_chk = read_section5(fresh)
    by_cmd_id = {e["cmd_id"]: e for e in idx_ir}
    print(
        "\n   REACHABILITY THROUGH SECTION [5], command by command "
        "(k1 = cmd_id's high byte -> sub-table; k2 = low byte -> record):"
    )
    inalcanzables = []
    for pg_i, pg in enumerate(pages):
        for bt in pg:
            c, d = bt["cmd_id"], bt["dev_id"]
            k1, k2 = c >> 8, c & 0xFF
            reg, reason = resolve_section5(fresh, c)
            esperado_reg = by_cmd_id[c]["off_registro"]
            en_cadena = (c, d) in present
            ok_b = (
                reg == esperado_reg
                and not reason
                and k1 == (d >> 8)
                and k1 < len(devs_chk)
                and k2 < devs_chk[k1]["n"]
                and en_cadena
            )
            print(
                "      p%d %-10s zone %#04x  cmd_id %#06x (k1=%d<%d, k2=%2d<%d)  "
                "dev_id %#06x  -> record %s  chain %s  %s"
                % (
                    pg_i + 1,
                    bt["label"],
                    bt["zone"],
                    c,
                    k1,
                    len(devs_chk),
                    k2,
                    devs_chk[k1]["n"] if k1 < len(devs_chk) else -1,
                    d,
                    ("%#08x" % reg) if reg is not None else "NO (%s)" % reason,
                    "OK" if en_cadena else "MISSING",
                    "OK" if ok_b else "FAIL",
                )
            )
            if not ok_b:
                inalcanzables.append(
                    "%s/%#06x: %s" % (bt["label"], c, reason or "-")
                )
    if inalcanzables:
        fallos.append("zones section [5] can't resolve: %s" % inalcanzables)
    print(
        "   %d/%d commands resolve their EXACT (cmd_id, dev_id) pair and "
        "their own 25 B record, with k1 and k2 WITHIN range: %s"
        % (
            len(esperados_par) - len(inalcanzables),
            len(esperados_par),
            "YES" if not inalcanzables else "NO -- %s" % inalcanzables,
        )
    )
    # NEGATIVE control on the same walker: a k2 outside the sub-table (the
    # hang) and a nonexistent k1 have to be REJECTED with their reason.
    out_of_range_k2 = resolve_section5(fresh, (a.index << 8) | devs_chk[a.index]["n"])
    out_of_range_k1 = resolve_section5(fresh, (len(devs_chk) << 8) | 0)
    print(
        "   walker's NEGATIVE control -- cmd_id with k2=%d (one past the "
        "sub-table): %s ; with k1=%d (a device that doesn't exist): %s"
        % (
            devs_chk[a.index]["n"],
            out_of_range_k2[1] or "RESOLVES (wrong)",
            len(devs_chk),
            out_of_range_k1[1] or "RESOLVES (wrong)",
        )
    )
    if out_of_range_k2[0] is not None or out_of_range_k1[0] is not None:
        fallos.append("section [5]'s walker doesn't reject out-of-range indices")

    # (e) records and waveforms: nothing lost, everything new findable
    records_before = {r[0] for r in _records(b)}
    r_desp = {r[0] for r in _records(fresh)}
    nuevos_reg = {e["off_registro"] + 15 for e in idx_ir}
    print(
        "control (e): command records %d -> %d (+%d); the %d generated "
        "records commands.records() finds: %d/%d"
        % (
            len(records_before),
            len(r_desp),
            len(r_desp) - len(records_before),
            len(idx_ir),
            len(nuevos_reg & r_desp),
            len(nuevos_reg),
        )
    )
    if len(nuevos_reg & r_desp) != len(nuevos_reg):
        fallos.append("there are generated records commands.records() doesn't find")
    if not records_before <= r_desp:
        fallos.append("pre-existing command records got lost")

    # (f) every emitted pointer resolves within the blob, and the text re-decodes
    def within(off, largo=1):
        return 0 <= off and off + largo <= len(fresh)

    for etq, off, largo in (
        [
            ("name text", off_name_text, len(name_text)),
            ("return label", off_txt_volver, len(txt_volver)),
        ]
        + [
            ("label %s" % x["label"], x["off_txt"], len(x["glyphs"]))
            for x in buttons
        ]
        + [
            ("key register for menu %d's sheet %d" % (ordinal, k + 2), off, 1)
            for ordinal, offs in sorted(off_menu_pages_by_object.items())
            for k, off in enumerate(offs)
        ]
        + [
            ("key register for page %d" % (k + 1), o, 1)
            for k, o in enumerate(off_pag_cmds)
        ]
    ):
        if not within(off, largo):
            fallos.append("%s out of range" % etq)
    for etq, off, text in [
        ("row name", off_name_text, a.name),
        ("return label", off_txt_volver, ETIQUETA_VOLVER),
    ] + [(x["label"], x["off_txt"], x["label"]) for x in buttons]:
        leido = "".join(
            glyph_table.get(c, "?") for c in fresh[off : fresh.index(b"\x00", off)]
        )
        if leido != text:
            fallos.append("text %r re-decodes as %r" % (text, leido))
    n_keyregs_menu = sum(len(v) for v in off_menu_pages_by_object.values())
    print(
        "control (f): the %d text pointers and the %d new key registers "
        "resolve, and the %d texts re-decode byte for byte with the glyph table"
        % (len(buttons) + 2, n_keyregs_menu + len(pages), len(buttons) + 2)
    )

    # (f2) THE SCREEN, RE-READ WITH THE DISASSEMBLER: every page has to draw
    # the factory bitmaps at the factory coordinates, its centered labels,
    # the softkey and (if applicable) the indicator's pair.
    prob_prog = []
    for k, sp in enumerate(t_cmd["slots"] if t_cmd else []):
        s_k = read_slot(fresh, sp - BASE)
        ins = disassemble(fresh, s_k["prog"] - BASE)
        bmps = [ar for _o, op, ar in ins if op == "BMP"]
        txts = [ar for _o, op, ar in ins if op == "TXT"]
        esperados_bmp = [
            (GRILLA[j][0], GRILLA[j][1], BUTTON_BMP[j]) for j in range(len(pages[k]))
        ]
        if bmps != esperados_bmp:
            prob_prog.append("page %d: bitmaps %s != %s" % (k + 1, bmps, esperados_bmp))
        # 1 softkey + n labels + (2 for the indicator)
        n_txt = 1 + len(pages[k]) + (0 if indicador is None else 2)
        if len(txts) != n_txt:
            prob_prog.append(
                "page %d: %d texts, expected %d" % (k + 1, len(txts), n_txt)
            )
            continue
        for j, bt in enumerate(pages[k]):
            if txts[1 + j][:2] != (bt["x_txt"], GRILLA[j][3]):
                prob_prog.append(
                    "page %d: label %r ended up at %s and not at (%d,%d)"
                    % (
                        k + 1,
                        bt["label"],
                        txts[1 + j][:2],
                        bt["x_txt"],
                        GRILLA[j][3],
                    )
                )
        if indicador is not None and txts[-2:] != [
            indicador["digitos"][k],
            indicador["sep"],
        ]:
            prob_prog.append(
                "page %d: the indicator pair isn't the factory's" % (k + 1)
            )
    if indicador is not None:
        ins_pro = disassemble(fresh, prologo_cmd)
        ins_fab = disassemble(b, indicador["prologo"])
        new_shape = [op for _o, op, _a in ins_pro]
        forma_fab = [op for _o, op, _a in ins_fab]
        if new_shape != forma_fab:
            prob_prog.append(
                "new prologue %s doesn't have the factory's shape %s"
                % (new_shape, forma_fab)
            )
        new_pair = _indicator_pair(ins_pro)
        if new_pair != (indicador["attr"], [indicador["total"], indicador["pages"]]):
            prob_prog.append("the indicator's total didn't end up like the factory's")
    print(
        "control (f2) -- the %d pages RE-READ with the disassembler: %d/%d "
        "draw the factory bitmaps at their coordinates, their centered "
        "labels and the softkey%s. The new prologue has the SAME "
        "instruction shape as the factory's (%s). Problems: %s"
        % (
            len(pages),
            len(pages) - len({x.split(":")[0] for x in prob_prog}),
            len(pages),
            "" if indicador is None else " + the cloned indicator pair",
            "-".join(op for _o, op, _a in disassemble(b, indicador["prologo"]))
            if indicador is not None
            else "no indicator",
            prob_prog or "none",
        )
    )
    if prob_prog:
        fallos.append(
            "the re-read screen doesn't match what was asked for: %s" % prob_prog
        )

    # (h) SECTION [5], simulating the firmware's walk over the FINAL blob
    devs_n = read_section5(fresh)
    ok_h = len(devs_n) == len(devs5) + 1
    # the 3 factory ones have to resolve to the SAME records as before
    for k1, (old, nuev) in enumerate(zip(devs5, devs_n)):
        if old != nuev:
            ok_h = False
            fallos.append("section [5]'s device %d changed" % k1)
    # and the new one, to the 32 generated records, IN ORDINAL ORDER
    esperados = [e["off_registro"] for e in idx_ir]
    d3 = devs_n[a.index] if a.index < len(devs_n) else None
    ok_order = d3 is not None and d3["regs"] == esperados
    # every entry has to land on a real record: self-pointer == reg+4
    ok_auto = d3 is not None and all(
        u24(fresh, r + 12) - BASE == r + 4 and fresh[r + 11] == 1 for r in d3["regs"]
    )
    # and each command object's cmd_id has to index ITS OWN record
    ok_cmd = all(
        (e["cmd_id"] >> 8) == a.index
        and d3 is not None
        and (e["cmd_id"] & 0xFF) < d3["n"]
        and d3["regs"][e["cmd_id"] & 0xFF] == e["off_registro"]
        for e in idx_ir
    )
    # The 64 KB gate, with each structure's REAL LENGTH. The previous
    # version looked at `(record+11, 4)`: 4 of the 25 bytes. The config path
    # enters the record at +11, follows the self-pointer to +4 and advances
    # up to +24 (that's where both waveforms' pointers are), so the WHOLE
    # record has to fit in one page; same for trailers, slots, key
    # registers and headers, which that same path walks sequentially
    # without redoing the bank latch. They're also emitted already aligned
    # (`emit(..., label)`), so this verifies the result, it doesn't produce it.
    cruces = [(n, o, L) for n, o, L in estructuras if crosses_page(o, L)]
    print(
        "\ncontrol (h) -- THE CAUSE OF THE HANG. The final blob's section "
        "[5] is walked with `cmd_setup_ir`'s exact arithmetic (+k1*3+1, +k2*3+3):\n"
        "   devices: %d -> %d\n"
        "   the %d factory ones resolve to the SAME records as before "
        "(%s), without moving a byte\n"
        "   device %d resolves to %d records and they're the %d generated "
        "ones, in ordinal order: %s\n"
        "   each entry lands on a real record (type byte = 1 and "
        "self-pointer == record+4): %s\n"
        "   command objects' cmd_id -> their own record via the "
        "table: %s\n"
        "   of the %d structures the config path walks (section [5], "
        "sub-table, the %d WHOLE 25 B records, the %d waveform units, "
        "the trailers, the slots, the key registers, the headers and "
        "tabla[6]), how many cross a 64 KB boundary: %s  (alignment "
        "padding emitted: %d B)"
        % (
            len(devs5),
            len(devs_n),
            len(devs5),
            "YES" if ok_h else "NO",
            a.index,
            d3["n"] if d3 else 0,
            len(esperados),
            "YES" if ok_order else "NO",
            "YES" if ok_auto else "NO",
            "YES" if ok_cmd else "NO",
            len(estructuras),
            len(idx_ir),
            len(idx_ir),
            "none" if not cruces else cruces,
            relleno_total,
        )
    )
    if not (ok_h and ok_order and ok_auto and ok_cmd) or cruces:
        fallos.append(
            "the new section [5] doesn't resolve the way the firmware walks it"
        )
    # NEGATIVE control on the 64 KB gate: if it doesn't reject a case built
    # on purpose, it distinguishes nothing. The two forms that matter are tested.
    neg_64 = [
        ("25 B record at %#08x" % (PAGE_SIZE - 16), crosses_page(PAGE_SIZE - 16, 25)),
        ("N=6 trailer at %#08x" % (PAGE_SIZE - 8), crosses_page(PAGE_SIZE - 8, 6 + 3 * 6)),
        ("25 B record at %#08x" % (PAGE_SIZE - 64), crosses_page(PAGE_SIZE - 64, 25)),
    ]
    print(
        "   64 KB gate's NEGATIVE control: %s (the first two have to give "
        "True and the third False)" % ", ".join("%s -> %s" % x for x in neg_64)
    )
    if not (neg_64[0][1] and neg_64[1][1] and not neg_64[2][1]):
        fallos.append("the 64 KB gate doesn't distinguish")
    # NEGATIVE control on gate (h): on the INPUT blob, the same walk for
    # k1 = new index has to fail. If it came out fine, this control
    # distinguishes nothing.
    try:
        neg_h = len(read_section5(b)) > a.index
    except SystemExit:
        neg_h = False
    print(
        "   NEGATIVE control -- the same walk over the INPUT blob "
        "finds device %d: %s (has to be NO: that's the bug)"
        % (a.index, "YES" if neg_h else "NO")
    )
    if neg_h:
        fallos.append("control (h) doesn't distinguish: the input blob already had it")

    # (g) THE GATE: nothing in the original body moved
    repuntes = [MAESTRO_S5, MAESTRO_T6]
    extra = {q + k for q in repuntes for k in range(3)}
    ok, dif = write.nothing_moved(b, fresh, extra)
    sin_declarar = sorted(set(dif) - write.ALLOWED - extra)
    print(
        "\ncontrol (g) -- grabar.nada_se_movio(%s, output): %s\n"
        "   different bytes below the old closing pointer: %d\n"
        "   %s\n"
        "   declared by PERMITIDOS ([4:7] + master index [9][10][11]): %s\n"
        "   declared by --repunta: %s\n"
        "   UNDECLARED: %s"
        % (
            pathlib.Path(a.blob).name,
            "YES -- not a single byte is left over" if ok else "NO",
            len(dif),
            " ".join("%#08x" % x for x in dif),
            " ".join("%#08x" % x for x in sorted(set(dif) & write.ALLOWED)),
            " ".join("%#08x" % x for x in sorted(set(dif) & extra)),
            sin_declarar if sin_declarar else "none",
        )
    )
    if not ok:
        fallos.append("there are original-body bytes moved or undeclared")
    # negative control: WITHOUT declaring the repoints, nada_se_movio has to
    # give False -- and also declaring ONLY one of the two, which is the real
    # mistake this guards against (repointing section [5] and forgetting to declare it).
    ok_neg, _ = write.nothing_moved(b, fresh, set())
    parciales = []
    for absent in repuntes:
        sub = {q + k for q in repuntes if q != absent for k in range(3)}
        parciales.append((absent, write.nothing_moved(b, fresh, sub)[0]))
    print(
        "   NEGATIVE control -- without declaring any --repunta: nada_se_movio gives "
        "%s (has to be NO)\n"
        "   NEGATIVE control -- declaring all but one: %s (both NO)"
        % (
            "YES" if ok_neg else "NO",
            ", ".join(
                "without %#08x -> %s" % (q, "YES" if v else "NO") for q, v in parciales
            ),
        )
    )
    if ok_neg or any(v for _, v in parciales):
        fallos.append(
            "nada_se_movio's negative control doesn't distinguish: gives YES without declaring everything"
        )

    # ---- (l) THE BOTTOM SOFTKEYS, RE-READ FROM THE FINAL BLOB ----
    #
    # The control that closes both bugs. It runs on the GENERATED blob and
    # over the 157 ordinals (not the factory's 156), with the same readers
    # used for the census. Three things, each with its counterpart:
    #
    #   (l1) the whole census, now including our screens: still no "LIGHT
    #        WITH NO ZONE" and no "ZONE WITH NO LIGHT";
    #   (l2) each of the 6 new pages and each of the 3 sheet 2s DRAW the
    #        left foot's label -- and to see it you have to DESCEND into
    #        the SWITCH's branches, which is where it lives;
    #   (l3) the commands screen's hook lights and turns off all 4 channels
    #        (the 2 strips + the 2 bottom softkeys), like the model ordinal.
    cen_fin = census_bottom_softkeys(fresh)
    pof = cen_fin["by_ordinal"]
    print(
        "\ncontrol (l) -- THE BOTTOM SOFTKEYS IN THE FINAL BLOB (%d ordinals, not "
        "the factory's %d):\n"
        "   (l1) zone declared <-> LED channel lit:\n"
        "        LEFT foot : %3d zone+light, %3d zone with NO light, %3d LIGHT WITH NO ZONE, "
        "%3d neither\n"
        "        RIGHT foot: %3d zone+light, %3d zone with NO light, %3d LIGHT WITH NO ZONE, "
        "%3d neither" % (u16(fresh, new_t6), u16(b, T6), *pof["IZQ"], *pof["DER"])
    )
    if any(pof[lado][1] or pof[lado][2] for lado in pof):
        fallos.append(
            "the final blob breaks the bottom softkeys' zone<->light "
            "invariant: %s" % pof
        )

    def _disasm_with_branches(bl, off, prof=0, visto=None):
        """`disassemble()` + descending into the SWITCH's branches. Without
        this, the left foot's label is INVISIBLE: it lives inside a branch."""
        if visto is None:
            visto = set()
        if off in visto or prof > 4:
            return []
        visto.add(off)
        ins = disassemble(bl, off)
        out_ = list(ins)
        for o_i, op_i, ar_i in ins:
            if op_i == "SWITCH":
                for j in range(ar_i[1]):
                    out_ += _disasm_with_branches(
                        bl, u24(bl, o_i + 4 + 4 * j) - BASE, prof + 1, visto
                    )
        return out_

    def _foot_label(bl, prog_off):
        """{'IZQ'/'DER': [(x,y)]} of the texts in the foot strip."""
        r = {}
        for _o, op_i, ar_i in _disasm_with_branches(bl, prog_off):
            if op_i in ("TXT", "TXTIN") and 188 <= ar_i[1] <= 216:
                r.setdefault("IZQ" if ar_i[0] < 88 else "DER", []).append(
                    (ar_i[0], ar_i[1])
                )
        return r

    screens_detail = []
    for o in objs:
        t_o = read_trailer(fresh, u24(fresh, new_t6 + 3 + 3 * o["ordinal"]) - BASE)
        for k in range(1, t_o["N"]):  # ALL the extra sheets, not just the first
            s_o = read_slot(fresh, t_o["slots"][k] - BASE)
            screens_detail.append(
                (
                    "menu %3d's sheet %d" % (o["ordinal"], k + 1),
                    s_o,
                    _foot_label(fresh, s_o["prog"] - BASE),
                )
            )
    for k, sp in enumerate(t_cmd["slots"]):
        s_k = read_slot(fresh, sp - BASE)
        screens_detail.append(
            (
                "commands page %d/%d" % (k + 1, len(pages)),
                s_k,
                _foot_label(fresh, s_k["prog"] - BASE),
            )
        )
    print("   (l2) screen by screen, what the user has to SEE at the bottom:")
    for etq_p, s_p, rot in screens_detail:
        kr_p = read_key_register(fresh, s_p["keyreg"] - BASE) or []
        cods_p = {e[0] for e in kr_p}
        pies_p = template_feet(plantillas[s_p["K"]])
        est = []
        for lado in ("IZQ", "DER"):
            if lado not in pies_p:
                est.append("%s: the template has no zone" % lado)
                continue
            est.append(
                "%s zone %#04x %s, label %s"
                % (
                    lado,
                    pies_p[lado],
                    "declared" if pies_p[lado] in cods_p else "NOT DECLARED",
                    rot.get(lado, "ABSENT"),
                )
            )
        print("        %-22s K=%-4s  %s" % (etq_p, hex(s_p["K"]), " | ".join(est)))
        for lado in pies_p:
            if pies_p[lado] not in cods_p:
                fallos.append("%s: %s foot not declared" % (etq_p, lado))
            if lado not in rot:
                fallos.append("%s: %s foot has no label" % (etq_p, lado))
    t_cmd_fin = read_trailer(fresh, tr_cmd, max_n=200)
    cab_fin = read_header(fresh, t_cmd_fin["hdr"] - BASE)
    g_fin = {cod: i for cod, i, cls in cab_fin[0] if cls == CATEGORY_ACTION}
    t11_fin = u24(fresh, 0x0C + 4 * 11) - BASE
    on_fin = sorted(
        c for c, e in led_channels(fresh, t11_fin, g_fin[CODE_ENTER]) if e == 2
    )
    off_fin = sorted(
        c for c, e in led_channels(fresh, t11_fin, g_fin[CODE_EXIT]) if e == 0
    )
    print(
        "   (l3) the commands screen (ordinal %d) lights channels %s on "
        "ENTER and turns off %s on EXIT; the factory's model ordinal %d does %s/%s"
        % (
            new_ordinal,
            on_fin,
            off_fin,
            first_page,
            sorted(c for c, e in canales_modelo[CODE_ENTER] if e == 2),
            sorted(c for c, e in canales_modelo[CODE_EXIT] if e == 0),
        )
    )
    if on_fin != sorted(esperado_on) or off_fin != sorted(esperado_on):
        fallos.append(
            "the commands screen doesn't light/turn off the model's 4 channels"
        )
    # and the 3 menus must NOT have changed their lighting: K=4 has no right foot
    for o in objs:
        t_o = read_trailer(fresh, u24(fresh, new_t6 + 3 + 3 * o["ordinal"]) - BASE)
        c_o = read_header(fresh, t_o["hdr"] - BASE)
        g_o = {cod: i for cod, i, cls in c_o[0] if cls == CATEGORY_ACTION}
        on_o = {c for c, e in led_channels(fresh, t11_fin, g_o[CODE_ENTER]) if e == 2}
        if on_o & set(CANALES_PIE["DER"]):
            fallos.append(
                "menu %d lights the RIGHT foot's channel, which K=%d doesn't have"
                % (o["ordinal"], K_MENU)
            )
    print(
        "   NEGATIVE control: menus %s still do NOT light the right foot's "
        "channel (their K=%d template has no such zone) -- the commands "
        "screen's fix didn't leak into them" % ([o["ordinal"] for o in objs], K_MENU)
    )

    if fallos:
        print("\nFAILED CONTROLS -- nothing gets written:")
        for f in fallos:
            print("   - %s" % f)
        return 1

    # ------------------------------------------------------------ output ---
    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("\nwrote %s (%d B)" % (a.salida, len(fresh)))
        r = subprocess.run(
            [sys.executable, "configcheck.py", a.salida],
            capture_output=True,
            text=True,
            cwd=str(pathlib.Path(__file__).parent),
        )
        print("configcheck.py:")
        for ln in r.stdout.strip().splitlines():
            print("   " + ln)
        if r.returncode:
            print("   configcheck FAILED: the EZHex isn't assembled")
            return 1
        if a.ezhex:
            if not a.plantilla:
                raise SystemExit("--ezhex needs --plantilla")
            r = subprocess.run(
                [sys.executable, "ezhex.py", "armar", a.plantilla, a.salida, a.ezhex],
                capture_output=True,
                text=True,
                cwd=str(pathlib.Path(__file__).parent),
            )
            print("ezhex.py armar:")
            for ln in r.stdout.strip().splitlines():
                print("   " + ln)
            if r.returncode:
                return 1

    print("\n" + "=" * 70)
    print("REPOINTS TO DECLARE (%d, 3 B each):" % len(repuntes))
    print("   " + " ".join("--repunta %#08x" % q for q in repuntes))
    print("\nBURN COMMAND (a human runs this, this tool does NOT burn):")
    print(
        "   python3 write.py %s \\\n     --referencia %s \\\n     %s"
        % (
            a.ezhex or "<output.EZHex>",
            a.blob,
            " ".join("--repunta %#08x" % q for q in repuntes),
        )
    )
    print("=" * 70)

    print(
        "\nWHAT THE USER HAS TO SEE ON SCREEN (just look; do NOT press anything "
        "except what's asked):"
    )
    print("   1. Devices menu: draws as always, 3 devices. NO CHANGES.")

    # The visual report is BUILT from the result, not from fixed text: packing
    # makes the sheet have 1, 2 or 3 rows, and this run's addition can land on
    # top / in the middle / at the bottom. A paragraph hardcoded to "A SINGLE
    # ROW at the very top" described the bug, not the fix -- and since the
    # user validates by LOOKING AT THE SCREEN against this list, with that
    # text they couldn't tell the fix working apart from a failure.
    def _row_name(off_txt: int) -> str:
        return "".join(
            glyph_table.get(c, "?") for c in fresh[off_txt : fresh.index(b"\x00", off_txt)]
        )

    last_rows = menu_pages_by_object[objs[0]["ordinal"]][-1]
    nombres_ult = [_row_name(t) for t, _ident in last_rows]
    new_row_k = len(last_rows) - 1  # this run's addition ALWAYS goes at the end
    # the position is named by the template's STRIP (the 3 touch zones
    # 0xB0/0xB1/0xB2 are always at Y=38/92/146), not by "how many rows there
    # are": with 2 rows the bottom one occupies the MIDDLE strip and the
    # bottom one stays EMPTY -- and that's exactly what the user has to be
    # able to check.
    franja = ("TOP (Y=38)", "MIDDLE (Y=92)", "BOTTOM (Y=146)")[new_row_k]
    vacias = [
        "%s (Y=%d)" % (("TOP", "MIDDLE", "BOTTOM")[j], Y_ROW_0 + j * ROW_STEP)
        for j in range(len(last_rows), MAX_ROWS_PER_SHEET)
    ]
    print(
        "   2. Touch the RIGHT strip (right edge of the screen): the menu's\n"
        "      SHEET %d shows up, and it has %d device ROW(S), not just one.\n"
        "      Top to bottom they read: %s\n"
        "      %r is row %d of %d and goes in the %s strip: large icon at Y=%d,\n"
        "      small icon at Y=%d and the FULL name at Y=%d. Above it go the\n"
        "      devices added BEFORE, in that same order.\n"
        "      Strip(s) that stay EMPTY on that sheet: %s -- no phantom row or\n"
        "      loose icon may appear there.\n"
        "      *** THIS IS THIS ROUND'S FIX: before, each device went to its OWN\n"
        "      sheet (with 2 devices you got sheet 2 = one and sheet 3 = the\n"
        "      other, each with 2 empty strips). Now they get filled %d at a\n"
        "      time and only the %dth opens a new sheet.\n"
        "      *** FIXED (bug 1): at the bottom LEFT that sheet says the SAME\n"
        "      thing as sheet 1 -- 'Activities' if no activity is running, or\n"
        "      'Current / Activity' on two lines if there is. The bottom/left\n"
        "      touch key ended up declared and does the same thing as on sheet 1.\n"
        "      Before, that sheet said NOTHING there, with the light on.\n"
        "      (On menus 74 and 90 the label is the two-branch one; on 141 it's\n"
        "      the fixed 'Activities' -- because that's how each one's sheet 1 is.)"
        % (
            1 + len(layout),
            len(last_rows),
            " / ".join("%d) %s" % (j + 1, n) for j, n in enumerate(nombres_ult)),
            a.name,
            new_row_k + 1,
            len(last_rows),
            franja,
            Y_ROW_0 + new_row_k * ROW_STEP,
            Y_ROW_0 + 1 + new_row_k * ROW_STEP,
            Y_ROW_0 + 19 + new_row_k * ROW_STEP,
            ", ".join(vacias) or "none, the sheet came out full",
            MAX_ROWS_PER_SHEET,
            MAX_ROWS_PER_SHEET + 1,
        )
    )
    print(
        "   3. From sheet %d, TOUCH %s's row (the %dth, %s strip): its own\n"
        "      commands screen opens (ordinal %d), not the neighbor's. And\n"
        "      touching the TOP row has to open %r's -- if it opens the %s's,\n"
        "      the row<->zone pairing got crossed and it needs to be reverted."
        % (
            1 + len(layout),
            a.name,
            new_row_k + 1,
            franja,
            new_ordinal,
            nombres_ult[0],
            a.name,
        )
    )
    print(
        "\n   *** FIXED (bug 2): THE COMMANDS SCREEN'S TWO BOTTOM SOFTKEYS.\n"
        "   On the %d pages, at the bottom you see and it lights up:\n"
        "      LEFT   label 'Activities' (or 'Current / Activity' if an\n"
        "             activity is running) + LIGHT. Before: light with no label.\n"
        "      RIGHT  label 'Devices' + LIGHT. Before: label with no light.\n"
        "   And now both are REAL keys: the touch template went from K=5/\n"
        "   K=32 (which have no bottom-left zone) to K=25/K=29, the two the\n"
        "   factory uses in commands screens 78/103/140. The rectangles of\n"
        "   the %d buttons, of the 2 strips and of 'Devices' are BYTE FOR BYTE\n"
        "   the same as before: nothing that already worked moved."
        % (len(pages), len(cmds))
    )
    print(
        "\n   WHAT'S NEW -- the commands screen now has %d PAGES and you move\n"
        "   from one to the next with the SAME side strips (and the same lit\n"
        "   physical keys) that already page the Devices menu. Top left,\n"
        "   below the 'Devices' title, it says '<n> / %d pages'.\n"
        "   Bottom right it says 'Devices' on all %d and returns to the menu.\n"
        "   Page by page, from top-left to bottom-right:"
        % (len(pages), len(pages), len(pages))
    )
    for k, pg in enumerate(pages):
        rows = ["", "", ""]
        for j, bt in enumerate(pg):
            rows[j // 2] += (
                ("%-12s" % bt["label"]) if j % 2 == 0 else bt["label"]
            )
        print(
            "\n   PAGE %d of %d   (indicator on top: '%d / %d pages')"
            % (k + 1, len(pages), k + 1, len(pages))
        )
        for f in rows:
            if f.strip():
                print("      %s" % f)
        if len(pg) < 6:
            print(
                "      (this page draws ONLY %d buttons, on the top row: it's "
                "template K=%d, the one the factory uses in the last slot of "
                "ordinal %d's commands screen. The middle and bottom rows "
                "stay EMPTY -- no phantom buttons should appear.)"
                % (len(pg), PLANTILLA_POR_CANTIDAD[len(pg)], first_page)
            )
        print("      [Devices]  <- bottom right, returns to the menu")
    print(
        "\n   WHAT **DOESN'T** CHANGE COMPARED TO THE BLOB ALREADY RUNNING:\n"
        "   * the %d buttons are in the SAME place, with the SAME label and\n"
        "     wired to the SAME IR object (verified: each command's touch\n"
        "     rectangle is identical byte for byte, and so are the %d press\n"
        "     and hold waveforms);\n"
        "   * the bottom-right 'Devices' softkey occupies the SAME rectangle;\n"
        "   * the '<n> / %d pages' indicator, the title, the bitmaps and\n"
        "     paging with the strips stay the same;\n"
        "   * the Devices menu and its sheet 1 weren't touched: sheet 1 still\n"
        "     points at the ORIGINAL factory slot.\n"
        "   The only things added are the bottom/LEFT softkey (zone + label\n"
        "   + light) and, on the right one, the light it was missing."
        % (len(cmds), len(cmds), len(pages))
    )
    print(
        "\n   WHAT TO WATCH, IN ORDER:\n"
        "   a. That the Devices menu and %s's sheet stay the same as today.\n"
        "   b. That entering the commands shows PAGE 1 with the usual 6, and\n"
        "      '1 / %d pages' on top.\n"
        "   c. Touch the right strip: it has to move to page 2 (arrows /\n"
        "      Select / Menu) and the indicator has to say '2 / %d pages'. Keep\n"
        "      going up to %d and back to 1 (it wraps around).\n"
        "   d. On page %d you have to see TWO buttons and nothing else.\n"
        "   e. 'Devices' at the bottom right returns to the menu from ANY page.\n"
        "   f. WHAT'S NEW THIS ROUND -- look at the BOTTOM EDGE on the %d\n"
        "      pages: BOTH softkeys have to be there, each with its label\n"
        "      AND its light. Left 'Activities' (or 'Current Activity'), right\n"
        "      'Devices'. Neither one may end up lit with no text, or with\n"
        "      text and unlit.\n"
        "   g. And on the Devices menu's sheet %d (the one with %s -- there are\n"
        "      %d extra sheet(s) besides the factory's sheet 1, packed %d at a\n"
        "      time): at the bottom left it has to say the same thing as sheet 1\n"
        "      when paging through it.\n"
        "   h. Only after that: press one button per page and see that the\n"
        "      TV responds and that the remote does NOT hang. And touch the\n"
        "      bottom/left softkey: it has to lead to Activities, same as\n"
        "      from the factory TV's commands screen."
        % (
            a.name,
            len(pages),
            len(pages),
            len(pages),
            len(pages),
            len(pages),
            1 + len(layout),
            a.name,
            len(layout),
            MAX_ROWS_PER_SHEET,
        )
    )
    print(
        "\n   [UNMEASURED RISK -- THE NUMBER PAD AND THE TOGGLE BIT]\n"
        "   The %s protocol declares `Payload.ToggleBit = 1` (it's 13-bit RC5)\n"
        "   and the Hub's %d payloads carry that bit FROZEN at the same\n"
        "   value: the remote plays back a static waveform from flash, so the\n"
        "   toggle never alternates. The 6 commands already tested on the\n"
        "   TV (Power, Vol +/-, Ch +/-, Mute) are exactly the ones tolerant to\n"
        "   repetition, where a frozen toggle is invisible. Digits are NOT:\n"
        "   typing a channel with two EQUAL digits (11, 22, 33) is the case where\n"
        "   the toggle is the only thing that tells 'pressed again' apart from\n"
        "   'still pressing'. THE CONCRETE TEST: on page 4, type 22 or 11 and see\n"
        "   whether the TV takes both digits. If it only takes one, that's the\n"
        "   problem, NOT the screen or section [5].\n"
        "   No file in this project looks at that bit today (grep -i toggle over\n"
        "   sintir/comandos/dispositivo/irscan: zero)."
        % (", ".join(sorted({c[1] for c in cmds})), len(cmds))
    )
    print(
        "\n   IF IT HANGS -- HOW TO RECOVER (the old config isn't touched):\n"
        "   a. Pull the batteries for 10 s, or hold POWER while turning it on to\n"
        "      enter SAFEMODE (measured).\n"
        "   b. Re-burn the EXACT factory backup:\n"
        "        python3 write.py ../backups/one_20260724_210614_a.EZHex \\\n"
        "          --referencia ../backups/config_raw.bin\n"
        "   c. `erase_config` only erases from 0x040000 on: the bootloader, the\n"
        "      safemode and the embedded backup config are NEVER touched."
    )
    print("\n   WHAT EACH RESULT RULES OUT:")
    print(
        "   - BOTH bottom softkeys with label AND light on the %d pages -> this\n"
        "     closes the LED channel <-> softkey mapping (channel 4 = left, channel\n"
        "     6 = right) that comes from the factory census 74/0/0/82 and 90/0/0/66,\n"
        "     and it closes that the LIGHT and the TOUCH ZONE are the same thing:\n"
        "     the K=5 template had no bottom-left zone, which is why the light was\n"
        "     orphaned. This is this round's load-bearing result.\n"
        "   - the left one says 'Activities' but does NOT respond to touch -> the\n"
        "     label is fine but the factory action {%d,%#04x} doesn't do what's\n"
        "     assumed: class 0x72 is a namespace that's never been traced and was\n"
        "     copied by index from ordinal %d. It's the only link in this round\n"
        "     that couldn't be resolved by reading, only copied.\n"
        "   - the left one says 'Current Activity' on two lines and NOT\n"
        "     'Activities' -> not a bug: it's branch 1 of the sel=%#04x SWITCH,\n"
        "     meaning an activity is running. The factory does exactly the same\n"
        "     thing on the Devices menu's sheet 1 and on ordinal %d's 6 pages; if\n"
        "     the two said different things, THAT would be the bug.\n"
        "   - phantom buttons show up in the middle row of the last page ->\n"
        "     K=%d declares 4 cells and only %d are used; a key register\n"
        "     declaring fewer zones than its template has has precedent on 82\n"
        "     factory slots, and sheet 2 (K=4, 1 of 3 cells) already tested it on\n"
        "     the device.\n"
        "   - the %d pages page with the strips -> the N>1 trailer and the header\n"
        "     WITHOUT 0xAE/0xAF also work on an ordinal WE created, not only on\n"
        "     the factory's 14."
        % (
            len(pages),
            ACCION_PIE_IZQ[0],
            ACCION_PIE_IZQ[1],
            first_page,
            SEL_PIE,
            first_page,
            K_COMANDOS_2,
            len(pages[-1]),
            len(pages),
        )
    )
    print(
        "   - the strips do NOT page the commands screen -> the header is still\n"
        "     hijacking the event, or the firmware doesn't page an ordinal outside\n"
        "     the original 156 (the Devices menu DOES page with N=2, so the\n"
        "     mechanism itself is proven)."
    )
    print(
        "   - page %d shows 2 buttons and not 6 -> K=%d behaves like its only\n"
        "     factory precedent (tabla[6][142] slot 2). If extra buttons show\n"
        "     up, the template wasn't the one that was measured."
        % (len(pages), K_COMANDOS_2)
    )
    print(
        "   - the indicator says '<n> / %d pages' -> the indicator's real\n"
        "     pattern was SPLIT across two programs (the total in the prologue,\n"
        "     the number and the bar in each slot), and cloning it whole closes\n"
        "     bug (A), which had correctly diagnosed the symptom and only half\n"
        "     diagnosed the cause. If odd text or a different color shows up, it\n"
        "     gets regenerated with --sin-indicador." % len(pages)
    )
    print(
        "   - the digits on pages 4 and 5 look CENTERED in their button -> the\n"
        "     rule x = floor(C - width/2) is the factory's (14/14 against\n"
        "     tabla[6][142]). With the old nailed-down X they'd be 15 px off\n"
        "     to the left."
    )
    print(
        "   - touching any of the %d buttons does NOT hang -> section [5] with 4\n"
        "     devices and k2 in 0..%d resolves for ALL of them, not just the 6\n"
        "     already tested. The cause of the hang is closed for the whole\n"
        "     set." % (len(buttons), len(cmds) - 1)
    )
    print(
        "\n   NOTE: the only text string that doesn't come from the factory blob\n"
        "   or from the Hub's JSON is the label abbreviation. The indicator, the\n"
        "   'Devices' label, the title and the %d button bitmaps are pointers to\n"
        "   data that was already in the blob." % len(BUTTON_BMP)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
