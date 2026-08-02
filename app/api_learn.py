#!/usr/bin/env python3
"""The JS surface of the **Learn** screen -- a mixin of `app/api.py`.

Lives in its own file and hooks in as a base class of `Api` so `api.py`
changes by three lines instead of two hundred. Every public method of a
mixin stays exactly as exposed to the JS as `Api`'s own (pywebview looks at
the object, not at the class where the method was declared).

This module **has no logic of its own**: it translates between the JS and
`app/learn_ir.py`, which is where the decisions live (recognize the
protocol, compare two captures, build the JSON). Same as the rest of
`api.py`, every method returns a serializable dict with `ok`.

Nothing here writes flash or touches `account_export/` outside the output folder
`app/library.py` already uses for any new device.
"""

from __future__ import annotations

from pathlib import Path

import _runtime


def _ok(**kw) -> dict:
    d: dict = {"ok": True}
    d.update(kw)
    return d


def _err(msg, **kw) -> dict:
    d: dict = {"ok": False, "error": str(msg)}
    d.update(kw)
    return d


def _err_de(exc: BaseException, prefijo: str = "", **kw) -> dict:
    """Same contract as `api._err_de`: the reason in words, plus
    `falla_interna: true` when what happened is a bug in the app and not a
    situation the person can act on. `_err(exc)` used to paste `str(exc)`
    straight in, so a `KeyError` reached this screen as `'keys'` -- a
    cartel that looks like a reason and is not one."""
    text = _runtime.reason(exc)
    d = _err("%s: %s" % (prefijo, text) if prefijo else text, **kw)
    if _runtime.falla_interna(exc):
        d["falla_interna"] = True
    return d


#: This screen's mandatory texts live in Python, not in the HTML -- same
#: rule as `TEXTO_CIERRE_DE_LAZO` in `api.py`: they cannot be softened by
#: touching only the template.
TEXTO_APUNTAR = (
    "Point the device's ORIGINAL remote at the front of the Harmony One "
    "(where the receiver is, at the top tip) from about 5 cm away, and "
    "press the key ONCE, briefly. If the key is held down, the waveform "
    "gets cut off at 1000 durations with no warning."
)
TEXTO_DOS_VECES = (
    "Every command is captured TWICE and the two are compared before it "
    "gets accepted. If they don't match, it asks again: saving a single "
    "capture means saving whatever noise it had, and afterward the device "
    "does not respond."
)
TEXTO_SIN_PROBAR = (
    "Capturing against the device has NOT been tested yet: it has not "
    "been run even once with the remote plugged in. What HAS been tested, "
    "with no hardware, is everything that happens after the waveform "
    "arrives -- the whole rest of this screen."
)


class ApiAprender:
    """`aprender_*` methods. Mixed into `Api` (see `app/api.py`)."""

    # -- internal helpers (not exposed by pywebview: start with `_`) -------

    def _learn_module(self):
        try:
            import learn_ir

            return learn_ir
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("learn_ir.py does not import: %s" % exc) from exc

    def _learn_blob(self) -> tuple[Path, bytes, dict]:
        """The reference blob -- the SAME one the Control screen uses to
        validate labels: the last grabbed entry confirmed as good, or the
        factory one. Not chosen independently: if the two ever differed, the
        Learn screen would accept a name that Control would later reject."""
        ref = self._current_reference()
        p = Path(ref["blob"])
        return p, p.read_bytes(), ref

    # -- state ---------------------------------------------------------

    def learn_status(self) -> dict:
        """Everything the screen needs to paint itself the first time."""
        try:
            ai = self._learn_module()
        except RuntimeError as exc:
            return _err_de(exc)

        try:
            blob_p, blob, ref = self._learn_blob()
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not read the reference blob")

        protos: dict[str, str] = {}
        try:
            import library

            protos = {n: p.origin for n, p in library.available_protocols().items()}
        except Exception:  # noqa: BLE001
            pass

        # The test bank: REAL waveforms already grabbed on the device, so the
        # whole screen can be walked through without the remote. It is not a
        # capture, and the UI says so in plain words.
        try:
            banco = ai.available_banks(blob, tope=12)
        except Exception:  # noqa: BLE001
            banco = []

        return _ok(
            commands=ai.STANDARD_COMMANDS,
            blob=str(blob_p),
            blob_origen=ref.get("origin"),
            protocolos=protos,
            banco=banco,
            gap_threshold_us=ai.GAP_THRESHOLD_US,
            tolerancia_rel=ai.TOLERANCIA_REL,
            tolerancia_abs_us=ai.TOLERANCIA_ABS_US,
            max_duraciones=ai.MAX_IR_SIGNAL_LENGTH,
            textos={
                "apuntar": TEXTO_APUNTAR,
                "dos_veces": TEXTO_DOS_VECES,
                "sin_probar": TEXTO_SIN_PROBAR,
            },
        )

    # -- name validation (point 5: BEFORE anything else) --------------------

    def learn_validate(self, name: str, labels=None) -> dict:
        """`fonts.choose()` + the glyph table, live while typing.

        These are the TWO checks: one says whether some font DRAWS those
        letters (what the brief asks for) and the other whether the blob
        knows how to WRITE them. A letter can have a stroke and no glyph
        index, so both have to run.
        """
        try:
            ai = self._learn_module()
            _p, blob, _ref = self._learn_blob()
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        etqs = [str(e) for e in (labels or []) if e]
        try:
            v = ai.validate_texts(str(name or ""), etqs, blob)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not validate")
        # This response's `ok` means "the call worked"; whether the name is
        # usable or not is separate, in `valido`. Collapsing the two into the
        # same key used to make a rejected name look like "the api broke",
        # with an EMPTY error note since there was no `error` to show.
        v = dict(v)
        v["valido"] = bool(v.pop("ok", False))
        return _ok(**v)

    # -- capture ----------------------------------------------------------

    def learn_capture(self, key: str = "") -> dict:
        """ONE capture against the device. Blocks for up to ~5 s waiting for
        the signal: the UI has to have already shown "point it and press"
        BEFORE this, because libconcord issues no prompt at all.

        Runs `config_work/learn.py` as a **subprocess** -- never imports
        libconcord in the window's process (see `app/learn_ir.py`'s
        docstring: `_handle_ir_response` can read out of range).
        """
        try:
            ai = self._learn_module()
        except RuntimeError as exc:
            return _err_de(exc)
        r = ai.capture(str(key or "") or None)
        if not r.get("ok"):
            return _err(r.get("error") or "the capture failed", **r)
        return _ok(captura=r["captura"], analisis=r["analisis"])

    def learn_capture_bank(self, offset, sesgo_us=0) -> dict:
        """A SIMULATED capture from a real waveform in the factory blob.

        Does not touch the remote. It exists for two things: letting the
        whole screen be walked through without hardware, and as a positive
        check that recognition and comparison do what they claim.
        `sesgo_us` reproduces libconcord's calibration divergence (inflates
        the first mark): with a bias, the protocol still has to be
        recognized, but through a later frame.
        """
        try:
            ai = self._learn_module()
            _p, blob, _ref = self._learn_blob()
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        try:
            off = int(offset)
        except (TypeError, ValueError):
            return _err("invalid offset: %r" % offset)
        try:
            sesgo = int(sesgo_us or 0)
        except (TypeError, ValueError):
            sesgo = 0
        sim = ai.simulated_capture(blob, off, sesgo_1ra_marca_us=sesgo)
        if not sim.get("ok"):
            return _err(sim.get("error") or "could not simulate")
        return _ok(captura=sim, analisis=ai.analyze(sim), simulada=True)

    # -- compare (point 3) ------------------------------------------------

    def learn_compare(self, analisis_a: dict, analisis_b: dict) -> dict:
        """Two captures of the same key -> gets accepted or asked for again."""
        try:
            ai = self._learn_module()
        except RuntimeError as exc:
            return _err_de(exc)
        try:
            return _ok(**ai.compare(analisis_a or {}, analisis_b or {}))
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not compare")

    # -- save (point 4) -------------------------------------------------

    def learn_save(
        self, fabricante: str, modelo: str, name: str, commands=None
    ) -> dict:
        """Writes the learned device with the SAME JSON shape
        `config_work/add_device.py` consumes, in the SAME folder as the
        catalog path (`app/library.py:escribir()`). From then on the
        Control screen does not tell this device apart from one downloaded
        from the catalog: generate, gate, apply, with not a single special
        case.
        """
        try:
            ai = self._learn_module()
            _p, blob, _ref = self._learn_blob()
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)

        limpios = []
        for c in commands or []:
            if not isinstance(c, dict):
                continue
            an = c.get("analisis")
            if not isinstance(an, dict) or not an.get("atomos"):
                continue
            limpios.append(
                {
                    "name": str(c.get("name") or "").strip(),
                    "label": str(c.get("label") or "").strip(),
                    "analisis": an,
                    "comparacion": c.get("comparacion"),
                    "simulado": bool(c.get("simulado")),
                }
            )
        if not limpios:
            return _err("no command has been accepted yet")
        if any(not c["name"] or not c["label"] for c in limpios):
            return _err("there is a command with no name or no label")

        try:
            r = ai.save(limpios, str(fabricante), str(modelo), str(name), blob)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not save")
        if not r.get("ok"):
            return _err(r.get("error") or "could not save", **r)
        return _ok(**{k: v for k, v in r.items() if k != "ok"})
