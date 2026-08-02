#!/usr/bin/env python3
"""Runs the project's checks and says, for each one, whether it PASSED, FAILED
or COULD NOT BE RUN -- and why.

## The rule

**No check dies with a `FileNotFoundError`.** A check that needs something
that isn't there is skipped with a message explaining what is missing and how
to get it. A raw traceback is the first thing whoever clones the repo sees,
and it says nothing.

There are two families:

`SIEMPRE`     need neither the remote nor any config: the `.ir` parser, IR
              waveform synthesis, the `.EZHex` container, the window
              plumbing, the app's imports. They have to pass on any machine,
              freshly cloned.

              The declared exception is **libconcord**: it is checked that it
              is there and that it is the PATCHED one (that it exports
              `read_flash_at` and `read_misc_at`). Not there -> SKIPPED with
              the instructions. There but without the two functions ->
              FAILED, because that is exactly the trap: upstream's libconcord
              loads perfectly and then cannot read anything off the remote.
              See `README.md`.

`CON_BASE`    need YOUR baseline (`backups/config_raw.bin`), which comes off
              your own remote with `python3 first_run.py`. Without it they
              skip saying exactly that.

`CON_ANCLA`   need the author's regression anchor -- one specific blob with
              one specific md5 -- which is not published. In a clone they
              will **never** be able to run, and that is said this plainly
              instead of pretending.

## Usage

    python3 check.py            # everything that can be run
    python3 check.py --solo-rapidos
    python3 check.py --lista    # what there is, without running anything
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
APP = RAIZ / "app"
CONFIG_WORK = RAIZ / "config_work"
BASE = RAIZ / "backups" / "config_raw.bin"

PASO, FALLO, SALTEADO = "PASO", "FALLO", "SALTEADO"

FALTA_BASE = (
    "needs your baseline. Run `python3 first_run.py` with the remote "
    "plugged in: it reads its config (read-only) and leaves it in "
    "backups/config_raw.bin"
)
FALTA_ANCLA = (
    "needs the author's regression anchor, which is not published -- it is the "
    "config of HIS remote. This check cannot run in a clone, and "
    "eso es correcto"
)
# The three lines break where each piece stands on its own. That is not
# cosmetic: the translation extractor looks PIECE by piece (they are three
# separate strings, not one), and a piece without two Spanish words is not
# recognised as prose and gets published as is. That is how this very
# constant shipped mixing both languages in one line, and nobody saw it.
FALTA_LIBCONCORD = (
    "I could not find libconcord. It is from another project (Concordance, GPLv3) and is "
    "compiled separately and PATCHED, which is how it is needed here. "
    "The instructions are in README.md. "
    "Without it the app still opens, but it can neither read nor write the remote"
)


class Check:
    def __init__(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: Path,
        familia: str,
        requiere: Path | None = None,
        reason: str = "",
        rc_ok=(0,),
        rc_salteado=(),
    ):
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.familia = familia
        self.requiere = requiere
        self.reason = reason
        self.rc_ok = rc_ok
        self.rc_salteado = rc_salteado

    def run(self, timeout: float) -> tuple[str, str, float]:
        if self.requiere is not None and not self.requiere.exists():
            return SALTEADO, self.reason, 0.0
        t0 = time.time()
        try:
            r = subprocess.run(  # noqa: S603 -- scripts from the repo itself
                [sys.executable, *self.argv],
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return FALLO, "timed out after %ss" % timeout, time.time() - t0
        except OSError as exc:
            return FALLO, str(exc), time.time() - t0
        dt = time.time() - t0
        if r.returncode in self.rc_ok:
            return PASO, (r.stdout.strip().splitlines() or [""])[-1][:160], dt
        if r.returncode in self.rc_salteado:
            return (
                SALTEADO,
                (r.stdout.strip().splitlines() or [""])[-1][:160] or self.reason,
                dt,
            )
        salida = (r.stderr or r.stdout or "").strip().splitlines()
        return (
            FALLO,
            "rc=%d  %s" % (r.returncode, salida[-1][:160] if salida else ""),
            dt,
        )


def _checks() -> list[Check]:
    return [
        # --- SIEMPRE ------------------------------------------------------
        Check(
            ".EZHex container emitted from scratch",
            [str(CONFIG_WORK / "ezhex_emitir.py"), "--autoprueba"],
            cwd=CONFIG_WORK,
            familia="SIEMPRE",
        ),
        Check(
            "the app's import sweep",
            [str(RAIZ / "check.py"), "--imports"],
            cwd=RAIZ,
            familia="SIEMPRE",
        ),
        Check(
            "window plumbing (without opening it)",
            [str(APP / "main.py"), "--selftest"],
            cwd=APP,
            familia="SIEMPRE",
        ),
        Check(
            "the 3 remote situations, with no remote",
            [str(APP / "remote_status.py")],
            cwd=APP,
            familia="SIEMPRE",
        ),
        Check(
            "the cloud/.ir -> remote translation, with no account and nothing downloaded",
            [str(APP / "check_library.py")],
            cwd=RAIZ,
            familia="SIEMPRE",
        ),
        Check(
            "the public catalog client, with no account and no network",
            [str(APP / "catalog_client.py")],
            cwd=APP,
            familia="SIEMPRE",
        ),
        Check(
            "the login, with no account and no keychain",
            [str(APP / "session.py")],
            cwd=APP,
            familia="SIEMPRE",
        ),
        # The one place where an exception turns into text for the
        # person who pressed the button. If this goes soft, every sign
        # in the app says "ValueError" again and a caught bug reads
        # exactly like a feature that is off on purpose.
        Check(
            "how a failure is told to whoever pressed the button",
            [str(APP / "_runtime.py")],
            cwd=APP,
            familia="SIEMPRE",
        ),
        Check(
            "libconcord, with the two patched reads",
            [str(RAIZ / "check.py"), "--libconcord"],
            cwd=RAIZ,
            familia="SIEMPRE",
            reason=FALTA_LIBCONCORD,
            rc_salteado=(2,),
        ),
        # --- CON_BASE -----------------------------------------------------
        Check(
            "reading and deriving the baseline",
            [str(CONFIG_WORK / "read_flash_baseline.py"), "--selftest"],
            cwd=CONFIG_WORK,
            familia="CON_BASE",
            requiere=BASE,
            reason=FALTA_BASE,
        ),
        Check(
            "importing a .ir (Flipper/IRDB) end-to-end",
            [str(APP / "check_ir_manual.py")],
            cwd=RAIZ,
            familia="CON_BASE",
            requiere=BASE,
            reason=FALTA_BASE,
        ),
        Check(
            "IR capture with the remote's own receiver",
            [str(APP / "check_learn.py")],
            cwd=RAIZ,
            familia="CON_BASE",
            requiere=BASE,
            reason=FALTA_BASE,
        ),
        # No `requiere` here: this one skips ITSELF, and does it better than we
        # would. It looks for its reference configuration down the three paths the
        # app uses, and if it finds none it exits with rc=2 naming the one that is
        # missing. `requiere=BASE` here would skip it before it ever asked.
        Check(
            "the key contract of the add path, end to end",
            [str(APP / "check_contract.py")],
            cwd=RAIZ,
            familia="CON_BASE",
            reason=FALTA_BASE,
            rc_salteado=(2,),
        ),
        # --- CON_ANCLA ----------------------------------------------------
        Check(
            "the written chain, against the md5 that is on the remote",
            [str(APP / "check_load_bearing.py")],
            cwd=RAIZ,
            familia="CON_ANCLA",
            reason=FALTA_ANCLA,
            rc_salteado=(2,),
        ),
        Check(
            "firmware key reach",
            [str(APP / "check_keys_reach.py")],
            cwd=RAIZ,
            familia="CON_ANCLA",
            reason=FALTA_ANCLA,
            rc_salteado=(2,),
        ),
        Check(
            "the automatic key planner",
            [str(APP / "check_keys_auto.py")],
            cwd=RAIZ,
            familia="CON_ANCLA",
            reason=FALTA_ANCLA,
            rc_salteado=(2,),
        ),
    ]


def _libconcord() -> int:
    """That libconcord is there, and that it is the PATCHED one.

    It is the project's only requirement that does not install with `pip`,
    and the one that fails most silently: upstream's libconcord loads
    perfectly and then cannot read anything, because arch 12 has
    `firmware_base = 0` and without `read_flash_at()` every read lands at
    address 0. Finding that out with the remote plugged in is too late.

    Codes: 0 it is there and it works, 2 it is not there (skipped), 1 it is
    there but missing the functions (that one IS a failure: it has to be
    recompiled with the patch).

    Doesn't touch USB: `CDLL` loads the file and two symbols are looked at.
    `init_concord()` is not called, nor anything that talks to the device.
    """
    import ctypes  # noqa: PLC0415

    sys.path.insert(0, str(CONFIG_WORK))
    try:
        import write  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print("LIBCONCORD: FALLO -- no pude importar config_work/write.py: %s" % exc)
        return 1

    path = getattr(write, "LIB", None)
    if not path:
        print("LIBCONCORD: missing.\n%s" % FALTA_LIBCONCORD)
        return 2
    try:
        lib = ctypes.CDLL(path)
    except OSError as exc:
        print(
            "LIBCONCORD: could not load %s (%s).\n%s" % (path, exc, FALTA_LIBCONCORD)
        )
        return 2

    missing = [n for n in ("read_flash_at", "read_misc_at") if not hasattr(lib, n)]
    if missing:
        print(
            "LIBCONCORD: FAILED -- %s exists but does not export %s.\n"
            "It is upstream's libconcord, unpatched. That way it loads fine and "
            "then it can read NOTHING off the remote: arch 12 has "
            "firmware_base = 0.\n"
            "Recompilala con tools/libconcord/libconcord-re-harmony.patch "
            "-- ver README.md." % (path, ", ".join(missing))
        )
        return 1
    print("LIBCONCORD: PASO -- %s exporta read_flash_at y read_misc_at" % path)
    return 0


def _imports() -> int:
    """That every published module of `app/` imports. It is the cheapest check
    and the one that broke the most times: a broken `import` is seen by
    nobody until the screen comes up blank."""
    sys.path[:0] = [str(APP), str(CONFIG_WORK)]
    fallas = []
    modulos = sorted(
        p.stem for p in APP.glob("*.py") if not p.stem.startswith("control_")
    )
    for m in modulos:
        if m in ("main", "__init__"):
            continue
        try:
            __import__(m)
        except Exception as exc:  # noqa: BLE001
            fallas.append("%s: %s: %s" % (m, type(exc).__name__, exc))
    # The app itself has to be able to say WHAT it is missing without breaking.
    try:
        import api

        missing = set(getattr(api, "FALTA", {}))
        # This set used to be non-empty: while `catalog.py` and `session.py` were
        # not published they HAD to be missing, and if they showed up it meant
        # there was another copy of the project on `sys.path`. Both are published
        # now -- without the APK bridge inside them -- so what is expected now is
        # that NOTHING is missing, and any name in `api.FALTA` is a module this
        # repository claims to publish and the app could not import.
        if missing:
            fallas.append(
                "api.py could not import modules that ARE published: %s"
                % sorted(missing)
            )
        print("api.FALTA = %s  (esperado: vacio)" % sorted(missing))
    except Exception as exc:  # noqa: BLE001
        fallas.append("api: %s: %s" % (type(exc).__name__, exc))

    # The measurement that WAS MISSING. An empty `api.FALTA` said the app was
    # complete while the Search button blew up on the press: the catalog
    # client was imported inside the function, so the module imported
    # cleanly and the path did not exist. A clean import is not a promise
    # that the button works. This asks from the outside.
    try:
        import catalog

        if catalog.CLIENT_MISSING is not None:
            fallas.append(
                "catalog.py imports but its client does not: Search and Download "
                "quedarian apagados (%s)" % catalog.CLIENT_MISSING
            )
        else:
            print("catalog: the client is here; Search and Download have something to talk to")
    except Exception as exc:  # noqa: BLE001
        fallas.append("catalogo: %s: %s" % (type(exc).__name__, exc))

    # AND THE SEAM BETWEEN THEM, walked across all three modules with the
    # network stubbed. The previous check asks each module whether it
    # imported; this one asks whether they FIT. They did not: `session.py`
    # and `catalog_client.py` were rewritten in parallel, each console
    # check passed on its own, and pressing Search raised
    # `AttributeError: 'HarmonySession' object has no attribute
    # 'access_token'` three frames deep. Nothing here opens a socket: the
    # one method that would (`JsonClient.post`) is replaced for the length
    # of the walk.
    try:
        import catalog
        import catalog_client as cc
        import session

        def _canned(_self, url, _payload, *, token=None, headers=None):
            if url == session.HARMONY_SIGNIN_URL:
                return 200, {"AccountId": "a", "AuthToken": "t", "Email": None}
            if url == cc.DISCOVERY_URL:
                return 200, {
                    "GetJsonOperationsResult": [
                        {
                            "Identifier": cc.SEARCH_OPERATION,
                            "Address": "https://svcs.myharmony.com/Dm/Dm.svc/json/",
                            "Name": "SearchGlobalDevices",
                        }
                    ]
                }
            if url.endswith("/json2/SearchGlobalDevices"):
                return 200, {
                    "SearchGlobalDevicesResult": {
                        "Matches": [
                            {
                                "Manufacturer": "Acme",
                                "DeviceModel": "X-1",
                                "Id-": 1,
                                "GlobalLanguageVersionId-": 2,
                                "GlobalDeviceSearchType": 2,
                                "DeviceType": 1,
                                "IsMultiCode": False,
                            }
                        ]
                    }
                }
            return 404, None

        import tempfile as _tempfile

        original = session.JsonClient.post
        session.JsonClient.post = _canned
        try:
            with _tempfile.TemporaryDirectory() as scratch:
                token_file = Path(scratch) / "token.json"
                session.save_lip_tokens(
                    session.LipTokens(access_token="A", id_token="I", refresh_token="R"),
                    token_file,
                )
                abierta = session.ensure_session("x@example.com", token_file=token_file)
                resultado = catalog.search(abierta, "Acme", "X-1")
        finally:
            session.JsonClient.post = original

        if len(resultado.matches) != 1 or resultado.matches[0].model != "X-1":
            fallas.append(
                "the seam sesion->catalogo does not carry results: %r"
                % (resultado.matches,)
            )
        else:
            print(
                "seam: ensure_session() -> search() -> %d match(es), one type "
                "across the three modules" % len(resultado.matches)
            )
    except Exception as exc:  # noqa: BLE001
        fallas.append(
            "the seam sesion->catalogo_cliente->catalogo broke: %s: %s"
            % (type(exc).__name__, exc)
        )

    if fallas:
        print("IMPORTS: FALLO")
        for f in fallas:
            print("  -", f)
        return 1
    print("IMPORTS: PASO (%d modulos)" % len(modulos))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lista", action="store_true")
    ap.add_argument("--imports", action="store_true", help="uso interno")
    ap.add_argument("--libconcord", action="store_true", help="uso interno")
    ap.add_argument("--fast-only", action="store_true")
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args()

    if a.imports:
        return _imports()
    if a.libconcord:
        return _libconcord()

    checks = _checks()
    if a.fast_only:
        checks = [c for c in checks if c.familia == "SIEMPRE"]

    if a.lista:
        for c in checks:
            print("%-10s %s" % (c.familia, c.name))
        return 0

    print("linea base: %s" % (BASE if BASE.exists() else "NOT THERE (%s)" % FALTA_BASE))
    print("=" * 78)
    resultados = []
    for c in checks:
        veredicto, detail, dt = c.run(a.timeout)
        resultados.append((veredicto, c.name, detail))
        print("%-9s %-52s %6.1fs" % (veredicto, c.name[:52], dt))
        if detail:
            print("          %s" % detail[:150])
    print("=" * 78)
    n = {v: sum(1 for r in resultados if r[0] == v) for v in (PASO, FALLO, SALTEADO)}
    print("%d paso(s), %d fallo(s), %d salteado(s)" % (n[PASO], n[FALLO], n[SALTEADO]))
    if n[FALLO]:
        print("\nWhat failed:")
        for v, name, detail in resultados:
            if v == FALLO:
                print("  - %s: %s" % (name, detail))
    return 1 if n[FALLO] else 0


if __name__ == "__main__":
    raise SystemExit(main())
