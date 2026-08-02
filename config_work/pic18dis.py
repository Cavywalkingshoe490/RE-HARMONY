"""Minimal PIC18 disassembler, enough to read the Harmony One flash driver.

Ghidra covers the general case; this exists to script targeted questions ("who
writes TBLPTR", "which routines issue AT49 command bytes") over the whole image
without round-tripping through the GUI.

PIC18 stores 16-bit instruction words little-endian: MOVLW 0x14 is bytes 14 0E.
"""

# Access-bank SFRs, by the low byte of their 0xF00-based address. The three
# indirect pointers are easy to transpose and doing so silently rewrites what
# the code appears to do, so they are spelled out here from the PIC18 datasheet
# map: FSR0 is the 0xFE9/0xFEA pair, FSR1 the 0xFE1/0xFE2 pair, FSR2 the
# 0xFD9/0xFDA pair, and each has its own INDF/POSTINC/POSTDEC/PREINC/PLUSW.
SFR = {
    0xF8: "TBLPTRU",
    0xF7: "TBLPTRH",
    0xF6: "TBLPTRL",
    0xF5: "TABLAT",
    # These were missing, and their absence hid the entire self-programming
    # routine: a grep for "EECON1" found nothing because they printed as raw
    # 0xA6/0xA7, and from that came the false conclusion "the firmware never
    # touches EECON1". Same mistake as the BTG mask: a gap in the table reads
    # as the absence of the fact. Careful reading these: they only count as
    # such for Access Bank accesses; with a MOVLB before them, 0xA6 is plain
    # RAM in the selected bank, not this register.
    0xA6: "EECON1",
    0xA7: "EECON2",
    0xA8: "EEDATA",
    0xA9: "EEADR",
    0xFF: "TOSU",
    0xFE: "TOSH",
    0xFD: "TOSL",
    0xFB: "PCLATU",
    0xFA: "PCLATH",
    0xF9: "PCL",
    0xF4: "PRODH",
    0xF3: "PRODL",
    0xF2: "INTCON",
    0xF1: "INTCON2",
    0xF0: "INTCON3",
    0xEF: "INDF0",
    0xEE: "POSTINC0",
    0xED: "POSTDEC0",
    0xEC: "PREINC0",
    0xEB: "PLUSW0",
    0xEA: "FSR0H",
    0xE9: "FSR0L",
    0xE8: "WREG",
    0xE7: "INDF1",
    0xE6: "POSTINC1",
    0xE5: "POSTDEC1",
    0xE4: "PREINC1",
    0xE3: "PLUSW1",
    0xE2: "FSR1H",
    0xE1: "FSR1L",
    0xE0: "BSR",
    0xDF: "INDF2",
    0xDE: "POSTINC2",
    0xDD: "POSTDEC2",
    0xDC: "PREINC2",
    0xDB: "PLUSW2",
    0xDA: "FSR2H",
    0xD9: "FSR2L",
    0xD8: "STATUS",
    0xC9: "SSPBUF",
    0xC8: "SSPADD",
    0xC7: "SSPSTAT",
    0xC6: "SSPCON1",
    0xC5: "SSPCON2",
    0xD7: "TMR0H",
    0xD6: "TMR0L",
    0xD5: "T0CON",
    0xCB: "PR2",
    0xCA: "T2CON",
    0xCC: "TMR2",
    # The CCP capture/compare names used to live here, taken from the 18F452
    # map. This device is NOT an 18F452 -- that identity was never established
    # -- and printing "CCPR1L" for 0xFBE asserts a peripheral the evidence does
    # not support: 0xBA, 0xBD, 0xBE and 0xBF have zero references in the whole
    # image, and 0xBB/0xBC are only ever written, never read. A disassembler
    # that names a register names a hypothesis, so these print as addresses
    # until their function is derived from use.
    #   0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF -- unidentified
    #
    # These three were briefly named IRCTL / IRTMRL / IRTMRH. That was the same
    # mistake in a new coat: the behaviour below is observed, but "IR" asserted
    # a function that rests on an unproven link to the learning receiver, and a
    # whole hypothesis was then built on the names. What is actually known:
    #   0xB6  written 5 times, never read; takes 0x00, 0x04, 0x05
    #   0xB7  low half of a free-running 16-bit counter, zero writes in 64 KB,
    #   0xB8  high half; sampled twice with an XORWF retry to read it atomically
    # Named for what they do, not for what they might be for.
    0x82: "PORTC",
    0x81: "PORTB",
    0x8B: "LATC",
    0x8A: "LATB",
    0x94: "TRISC",
    0x93: "TRISB",
}


def nm(f, access=True):
    """Name a file register.

    PIC18 encodes an access bit: a=0 selects the Access Bank (where f >= 0x80
    is an SFR), a=1 selects the bank held in BSR (plain RAM). Naming a banked
    operand after an SFR is wrong and was the source of a false "13 writes to
    PCLATH" reading -- those were banked writes to ordinary RAM.
    """
    if access and f >= 0x80:
        return SFR.get(f, "0x%02X" % f)
    return "0x%02X%s" % (f, "" if access else "(bnk)")


def dw(x):
    """The destination suffix of a two-operand ALU instruction.

    PIC18 encodes "oooo ooda ffff ffff": bit 9 is d, bit 8 is a. d=1 leaves the
    result in the file register, d=0 puts it in W. Ignoring d prints ANDWF and
    DECF with no destination at all, which makes the two cases look identical.
    That mattered while reading the image decoder: "MOVLW 0x7F ; ANDWF f ;
    MOVWF g" only makes sense with d=0, and read as d=1 it says the run counter
    is always 0x7F.
    """
    return "" if x & 0x200 else ",W"


def sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def decode(fw, o):
    """Return (text, words_consumed).

    Every byte- and bit-oriented instruction carries the access bit in bit 8, and
    dropping it renames banked RAM after an SFR. That is how a plain 16-bit
    counter in bank 13 read as writes to LATC, which put eight routines in the
    wrong peripheral group. Pass it to nm() everywhere, without exception.
    """

    def w(i):
        return fw[i] | (fw[i + 1] << 8)

    x = w(o)
    n = w(o + 2) if o + 3 < len(fw) else 0

    if (x & 0xF000) == 0xC000 and (n & 0xF000) == 0xF000:
        return "MOVFF 0x%03X,%s" % (
            x & 0xFFF,
            nm(n & 0xFF) if (n & 0xFFF) >= 0xF00 else "0x%03X" % (n & 0xFFF),
        ), 2
    simple = {
        0x0012: "RETURN",
        0x0011: "RETFIE",
        0x0000: "NOP",
        0x0003: "SLEEP",
        0x0004: "CLRWDT",
        0x00FF: "RESET",
        0x0008: "TBLRD*",
        0x0009: "TBLRD*+",
        0x000A: "TBLRD*-",
        0x000B: "TBLRD+*",
        0x000C: "TBLWT*",
        0x000D: "TBLWT*+",
        0x000E: "TBLWT*-",
        0x000F: "TBLWT+*",
    }
    if x in simple:
        return simple[x], 1
    if (x & 0xFF00) == 0x0E00:
        return "MOVLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0F00:
        return "ADDLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0800:
        return "SUBLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0900:
        return "IORLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0B00:
        return "ANDLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0A00:
        return "XORLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0100:
        return "MOVLB %d" % (x & 0x0F), 1
    if (x & 0xFE00) == 0x6E00:
        return "MOVWF %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6A00:
        return "CLRF  %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFC00) == 0x5000:
        return "MOVF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x) or ",f"), 1
    if (x & 0xFC00) == 0x2400:
        return "ADDWF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x2000:
        return "ADDWFC %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x5C00:
        return "SUBWF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    # 0101 01da = SUBFWB, 0101 10da = SUBWFB. SUBFWB was missing, so every
    # borrow-chain subtraction decoded as .word and read like a data gap in the
    # middle of ordinary arithmetic.
    if (x & 0xFC00) == 0x5800:
        return "SUBWFB %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x5400:
        return "SUBFWB %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x1800:
        return "XORWF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x1400:
        return "ANDWF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x1000:
        return "IORWF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x2800:
        return "INCF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x0400:
        return "DECF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x2C00:
        return "DECFSZ %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x3C00:
        return "INCFSZ %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    # The 0110 block is eight opcodes differing only in bits 10-9, so getting one
    # wrong silently renames an operation. Datasheet order: CPFSLT CPFSEQ CPFSGT
    # TSTFSZ SETF CLRF NEGF MOVWF -- 0x6C00 is NEGF, and it was CPFSGT here.
    if (x & 0xFE00) == 0x6000:
        return "CPFSLT %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6200:
        return "CPFSEQ %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6400:
        return "CPFSGT %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6600:
        return "TSTFSZ %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6800:
        return "SETF  %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFE00) == 0x6C00:
        return "NEGF  %s" % nm(x & 0xFF, not (x & 0x100)), 1
    # rotates, swap, complement and the multiplies were all missing
    if (x & 0xFC00) == 0x3400:
        return "RLCF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x3000:
        return "RRCF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x4400:
        return "RLNCF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x4000:
        return "RRNCF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x3800:
        return "SWAPF %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFC00) == 0x1C00:
        return "COMF  %s%s" % (nm(x & 0xFF, not (x & 0x100)), dw(x)), 1
    if (x & 0xFE00) == 0x0200:
        return "MULWF %s" % nm(x & 0xFF, not (x & 0x100)), 1
    if (x & 0xFF00) == 0x0D00:
        return "MULLW 0x%02X" % (x & 0xFF), 1
    if (x & 0xFF00) == 0x0C00:
        return "RETLW 0x%02X" % (x & 0xFF), 1
    # BTG is "0111 bbba ffff ffff", so the whole 0x7000-0x7FFF range. The mask
    # here was 0xF800, which only covered bit numbers 0 to 3: BTG on bits 4, 5
    # and 6 came out as ".word". That hid the USB data toggle, where
    # "ANDWF / BTG f,6 / IORWF 0x88" alternates the buffer status between 0x88
    # and 0xC8 -- read as a .word it looked like the status was a constant.
    if (x & 0xF000) == 0x7000:
        return "BTG   %s,%d" % (nm(x & 0xFF, not (x & 0x100)), (x >> 9) & 7), 1
    # LFSR is two words. Not knowing that left its second word decoding as a
    # stray .word 0xFxxx, which is most of what the listing could not read.
    if (x & 0xFFC0) == 0xEE00 and (n & 0xF000) == 0xF000:
        return "LFSR  FSR%d,0x%03X" % ((x >> 4) & 3, ((x & 0xF) << 8) | (n & 0xFF)), 2
    if (x & 0xF000) == 0x9000:
        return "BCF   %s,%d" % (nm(x & 0xFF, not (x & 0x100)), (x >> 9) & 7), 1
    if (x & 0xF000) == 0x8000:
        return "BSF   %s,%d" % (nm(x & 0xFF, not (x & 0x100)), (x >> 9) & 7), 1
    if (x & 0xF000) == 0xB000:
        return "BTFSC %s,%d" % (nm(x & 0xFF, not (x & 0x100)), (x >> 9) & 7), 1
    if (x & 0xF000) == 0xA000:
        return "BTFSS %s,%d" % (nm(x & 0xFF, not (x & 0x100)), (x >> 9) & 7), 1
    if (x & 0xFF00) == 0xEC00:
        return "CALL  0x%06X" % ((((n & 0xFFF) << 8) | (x & 0xFF)) * 2), 2
    if (x & 0xFF00) == 0xEF00:
        return "GOTO  0x%06X" % ((((n & 0xFFF) << 8) | (x & 0xFF)) * 2), 2
    if (x & 0xF800) == 0xD800:
        return "RCALL 0x%04X" % (o + 2 + 2 * sx(x & 0x7FF, 11)), 1
    if (x & 0xF800) == 0xD000:
        return "BRA   0x%04X" % (o + 2 + 2 * sx(x & 0x7FF, 11)), 1
    for op, mn in (
        (0xE0, "BZ"),
        (0xE1, "BNZ"),
        (0xE2, "BC"),
        (0xE3, "BNC"),
        (0xE4, "BOV"),
        (0xE5, "BNOV"),
        (0xE6, "BN"),
        (0xE7, "BNN"),
    ):
        if (x >> 8) == op:
            return "%-5s 0x%04X" % (mn, o + 2 + 2 * sx(x & 0xFF, 8)), 1
    return ".word 0x%04X" % x, 1


def listing(fw, start, end, base=0x020000):
    out = []
    o = start
    while o < end and o + 1 < len(fw):
        txt, sz = decode(fw, o)
        out.append((base + o, o, fw[o] | (fw[o + 1] << 8), txt))
        o += 2 * sz
    return out


def listing_banked(fw, start, end, base=0x020000):
    """Like listing(), but resolves banked operands to absolute RAM addresses.

    PIC18 file operands are 8 bits plus an access bit. When the access bit says
    "banked", the real address is BSR<<8 | f, and BSR is set by a MOVLB that may
    be several instructions earlier. Reading such an operand without tracking
    MOVLB is a guess that looks like a fact: in this project it turned a plain
    16-bit counter in bank 13 into writes to LATC, and put eight routines in the
    wrong peripheral group.

    So this walks the code carrying BSR forward and rewrites "0xNN(bnk)" into
    "[0xBNN]" once the bank is known. BSR is unknown until the first MOVLB in
    range, and those operands stay marked "(bnk?)".
    """
    out, bsr = [], None
    o = start
    while o < end and o + 1 < len(fw):
        word = fw[o] | (fw[o + 1] << 8)
        txt, size = decode(fw, o)
        if (word & 0xFF00) == 0x0100:
            bsr = word & 0x0F
        elif "(bnk)" in txt:
            reg = word & 0xFF
            txt = txt.replace(
                "0x%02X(bnk)" % reg,
                "[0x%03X]" % ((bsr << 8) | reg)
                if bsr is not None
                else "0x%02X(bnk?)" % reg,
            )
        out.append((base + o, o, word, txt, bsr))
        o += 2 * size
    return out
