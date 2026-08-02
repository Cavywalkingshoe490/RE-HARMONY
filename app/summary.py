#!/usr/bin/env python3
"""Turns the list of pending changes (`changes.SesionCambios`) into a
paragraph of plain English -- what gets shown BEFORE writing, so that
confirming Sync is an informed decision and not an act of faith.

PURE: it does not touch disk, it does not import `config_work/` nor
`generate.py`. It gets the `Change` objects already resolved (with their
human `label` already computed by whoever added them -- see
`Api.changes_add()`) and only groups them and writes them up. It never
decides whether something can be applied or not: that was already decided by
whoever added the change to the list (or the gate will decide it when
applying).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

#: IN ENGLISH: it is text shown verbatim in the UI, which is in
#: English. The logic and the comments stay in Spanish.
_TITULOS = {
    "add_device": ("Add {n} device", "Add {n} devices"),
    "remove_device": ("Remove {n} device", "Remove {n} devices"),
    "edit_activity": ("Edit {n} activity", "Edit {n} activities"),
    "reassign_key": ("Reassign {n} key", "Reassign {n} keys"),
}

_ORDEN = (
    "add_device",
    "remove_device",
    "edit_activity",
    "reassign_key",
)

# NO closing paragraph gets pasted at the end of the summary any more. The
# user said it: he pressed Sync and got four blocks of text saying almost the
# same thing, and this was one of them ("None of this has been written to your
# control yet..."), repeating what the screen's status line already
# says in a single sentence ("3 changes ready to apply."). The change list
# stays -- it is short and it is useful; the sermon is gone.
CIERRE = ""

SIN_CAMBIOS = "There are no changes waiting."


def _titulo(kind: str, n: int) -> str:
    singular, plural = _TITULOS.get(kind, ("{n} change", "{n} changes"))
    return (singular if n == 1 else plural).format(n=n)


def summarize_changes(changes: list[Any]) -> dict:
    """`changes`: list of `changes.Change` (or of dicts with the same
    keys -- `to_dict()`, so it also works with what already went out to and
    came back from the UI). Returns:

        {'texto': the full paragraph in plain English,
         'grupos': [{'tipo', 'titulo', 'items': [etiqueta, ...]}, ...],
         'cantidad': total number of changes}

    The order of the GROUPS is fixed (`_ORDEN`, above) -- add before delete,
    activities before keys -- so that the summary always reads the same no
    matter what order the screens got touched in. Within each group, the
    items keep the order they were added in.
    """
    items = [c if isinstance(c, dict) else c.to_dict() for c in (changes or [])]
    if not items:
        return {"text": SIN_CAMBIOS, "grupos": [], "count": 0}

    by_kind: OrderedDict[str, list[str]] = OrderedDict()
    for c in items:
        by_kind.setdefault(c["kind"], []).append(
            c.get("label") or "(no description)"
        )

    grupos = []
    for kind in _ORDEN:
        if kind in by_kind:
            labels = by_kind.pop(kind)
            grupos.append(
                {
                    "kind": kind,
                    "titulo": _titulo(kind, len(labels)),
                    "items": labels,
                }
            )
    # any future type that is not in `_ORDEN` (should not happen, but
    # better to show it than to lose it silently).
    for kind, labels in by_kind.items():
        grupos.append(
            {"kind": kind, "titulo": _titulo(kind, len(labels)), "items": labels}
        )

    lines = []
    for g in grupos:
        lines.append("%s:" % g["titulo"])
        for it in g["items"]:
            lines.append("  - %s" % it)
    text = "\n".join(lines)
    if CIERRE:
        text += "\n\n" + CIERRE

    return {"text": text, "grupos": grupos, "count": len(items)}


if __name__ == "__main__":
    # Console check: PURE, no external dependencies.
    from dataclasses import dataclass

    @dataclass
    class _FalsoCambio:
        kind: str
        label: str

        def to_dict(self):
            return {"kind": self.kind, "label": self.label}

    fallas = []

    vacio = summarize_changes([])
    if vacio["text"] != SIN_CAMBIOS or vacio["count"] != 0:
        fallas.append("an empty list had to give SIN_CAMBIOS and cantidad=0")

    muestra = [
        _FalsoCambio("reassign_key", "Tecla 0xAB: DirectionUp -> Mute"),
        _FalsoCambio("add_device", "Agregar 'Apple TV' (42 comandos)"),
        _FalsoCambio("remove_device", "Borrar 'LG TV' (36 comandos)"),
        _FalsoCambio("add_device", "Agregar 'Sonos' (12 comandos)"),
    ]
    r = summarize_changes(muestra)
    print(r["text"])
    print()
    print("grupos:", [(g["kind"], len(g["items"])) for g in r["grupos"]])

    if r["count"] != 4:
        fallas.append("cantidad esperada 4, dio %r" % r["count"])
    orden_grupos = [g["kind"] for g in r["grupos"]]
    if orden_grupos != ["add_device", "remove_device", "reassign_key"]:
        fallas.append(
            "the group order did not respect _ORDEN (agregar, borrar, editar, teclas): %r"
            % orden_grupos
        )
    agregar_grupo = next(g for g in r["grupos"] if g["kind"] == "add_device")
    if len(agregar_grupo["items"]) != 2:
        fallas.append("the 'agregar_dispositivo' group had to gather the 2 device additions")
    # NOW the other way around: the text has to be ONLY the list, no sermon. If
    # somebody pastes a closing paragraph back in, this has to catch it.
    # Every line is either a group title or an item "  - ...": nothing else.
    for linea in r["text"].split("\n"):
        if not (linea.endswith(":") or linea.startswith("  - ")):
            fallas.append(
                "the text brought loose text back (closing paragraph?): %r" % linea
            )
    if "written" in r["text"] or "Sync" in r["text"]:
        fallas.append("the 'nothing has been written yet' sermon came back into the summary")

    # it also has to accept dicts (what comes back from the UI serialised),
    # not only `Change` objects.
    r2 = summarize_changes([c.to_dict() for c in muestra])
    if r2["text"] != r["text"]:
        fallas.append("with dicts instead of Cambio objects, the text did not come out the same")

    print()
    if fallas:
        print("SELFTEST: FAILED")
        for f in fallas:
            print("  -", f)
        raise SystemExit(1)
    print("SELFTEST: PASSED (pure, no subprocess, no USB).")
