"""History with rollback for the RE-HARMONY desktop app.

SQLite in the user's data directory (NEVER in the repo). It is the record of
what got grabbed onto the remote: when, with which .EZHex, with which gate
result, and whether the user confirmed the remote booted fine afterward.

Does not touch the device or reimplement anything from
`write.py`/`add_device.py`. Its only neighbor in the repo is
`config_work/ezhex.py::split`, to split the header/binary of an `.EZHex` and
get the sha256 of the real GSPM blob (not of the file with its header, which
changes if header fields are tweaked without touching the binary).

`verified_by_user` exists because `result == 0` only says libconcord
did not fail -- in this project there was a real case where the write
"went fine" and the remote ended up in a boot loop. That's why
`for_rollback()` always also returns the `--repoint` values for that entry:
they are the two pieces of data needed to reconstruct `write.py`'s write
command.

## What is load-bearing here and what is not

`grabadas` + `for_rollback()` are the load-bearing part: they are the only
record of what went onto a physical remote, and losing a row means losing
the ability to put the previous config back. Their columns are fixed by
`write.py`'s command line and do not change.

`dispositivos_en` and `catalogo` are NOT load-bearing: they are a cache of
what the account bridge reported, and their columns follow whatever that
bridge's device/catalog records happen to carry. They are kept isolated in
their own functions (`record_devices`, `cache_catalog`) exactly so that a
change over there cannot drag down `grabadas` + rollback. If the bridge is
absent, those two tables simply stay empty and everything else still works.

NOTE on the SQL below: table and column names (`grabadas`, `fecha`,
`repuntes`, `compuerta_ok`, `verificado_por_usuario`, `dispositivos_en`,
`catalogo`, ...) are kept in Spanish ON PURPOSE. `app/api.py` reads these
same rows with `SELECT *` and forwards the dict straight to the JS UI, which
addresses each field by this exact name (see `app/ui/app.js`). Renaming a
column here without updating both `api.py` and `app.js` in lockstep would
break the History screen silently. Python-side names (functions, parameters,
local variables) are translated freely; the SQL vocabulary is not.

## `mandos`: which PHYSICAL remote a recording belongs to

Added so the app can stop assuming there is only ever one Harmony One (see
`config_work/read_flash_baseline.py` and the audit in ESTADO.md /
`app/check_load_bearing.py`'s neighborhood -- "el baseline esta hardcodeado").
`grabadas.mando_id` is nullable and additive (existing rows from before this
column existed keep `mando_id IS NULL`; nothing that already reads
`grabadas` by column name breaks).

Identity is matched with a DECLARED precedence, weakest link first because
none of the three is perfect on its own:

  1. **`serial`** (`libconcord.get_serial(1..3)`, concatenated) -- the
     strongest one available: it's the unit's own hardware serial, stable
     across every write this app makes. Empty/unavailable on some units
     (unconfirmed which, without hardware to test against).
  2. **`baseline_sha256`** -- sha256 of the raw flash baseline read at FIRST
     CONTACT with that remote (see `read_flash_baseline.py`). Stable only
     until the first edit changes the flash; still useful to recognize
     "this is the same remote I just read the baseline of, in the same
     session", and as a cross-check against `serial`.
  3. **the soft tuple** `(arch, skin, fw_mayor, fw_menor)` -- always
     available (comes from `get_identity()`, no raw read needed), but does
     NOT distinguish two identical-model units on the same firmware. Used
     only as a last resort, and `identify_or_create_remote()` says so in the
     row it returns (`identidad_confianza`).

None of this is exercised against real hardware in this session (no remote
connected) -- see `read_flash_baseline.py`'s docstring for exactly what
is and isn't verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# config_work/ is not an installable package (it has no __init__.py): it is
# imported via sys.path, exactly as the parent plan allows ("The modules that
# are already a clean library ARE imported directly... ezhex.py").
_CONFIG_WORK = Path(__file__).resolve().parent.parent / "config_work"
if str(_CONFIG_WORK) not in sys.path:
    sys.path.insert(0, str(_CONFIG_WORK))
import ezhex  # noqa: E402  (deliberately late import, see above)

SCHEMA = """
CREATE TABLE IF NOT EXISTS grabadas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                   TEXT NOT NULL,              -- ISO-8601 UTC
    ezhex_path              TEXT NOT NULL,              -- own copy, not output/
    sha256                  TEXT NOT NULL,              -- sha256 of the GSPM binary
    referencia_sha256       TEXT,                       -- sha256 of the --referencia used
    repuntes                TEXT NOT NULL DEFAULT '[]', -- JSON list[int] of --repunta
    compuerta_ok            INTEGER,                    -- 0/1/NULL (nada_se_movio)
    resultado               INTEGER,                    -- update_configuration return code
    verificado_por_usuario  INTEGER,                    -- NULL=unreviewed, 1=OK, 0=BAD
    notas                   TEXT
);

CREATE TABLE IF NOT EXISTS dispositivos_en (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    grabada_id       INTEGER NOT NULL REFERENCES grabadas(id) ON DELETE CASCADE,
    posicion         INTEGER,
    manufacturer     TEXT,
    model            TEXT,
    name             TEXT,
    global_device_id TEXT,
    n_comandos       INTEGER
);

CREATE TABLE IF NOT EXISTS catalogo (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    consultado_en    TEXT NOT NULL,
    manufacturer     TEXT,
    model            TEXT,
    global_device_id TEXT,
    device_type      INTEGER,
    profile_uri      TEXT,
    crudo            TEXT   -- raw JSON, in case account_export's model changes
);

CREATE TABLE IF NOT EXISTS mandos (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    primera_vez             TEXT NOT NULL,   -- ISO-8601 UTC, first time seen
    ultima_vez              TEXT NOT NULL,   -- ISO-8601 UTC, most recently seen
    serial                  TEXT,            -- get_serial(1..3) joined; strongest identity
    arch                    INTEGER,
    skin                    INTEGER,
    fw_mayor                INTEGER,
    fw_menor                INTEGER,
    config_usada            INTEGER,         -- get_config_bytes_used() at LAST sighting
    config_total            INTEGER,         -- get_config_bytes_total()
    baseline_sha256         TEXT,            -- sha256 of the raw baseline, if ever read
    baseline_path           TEXT,            -- own copy of that raw baseline, if any
    n_dispositivos_fabrica  INTEGER,         -- section [5] count, ONLY from a virgin dump
    n_pantallas_fabrica     INTEGER,         -- tabla[6] count, ONLY from a virgin dump
    identidad_confianza     TEXT,            -- 'serial' | 'baseline_sha256' | 'debil'
    apodo                   TEXT,            -- user-given label, optional
    notas                   TEXT
);
"""

#: Columns added to `grabadas` AFTER it first shipped. Same idempotent
#: pattern `app/api.py::_connect()` already uses for its own additions
#: (`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`) -- kept here, not there,
#: because `mando_id` is part of the recording model itself, not an
#: app-front convenience.
_COLUMNAS_AGREGADAS_GRABADAS = {
    "mando_id": "INTEGER REFERENCES mandos(id)",
}

#: Same, for `mandos`. These separate what a dump MEASURES from what may be
#: called FACTORY. The `_fabrica` columns were being filled from whatever
#: dump arrived first, so reading this project's own remote -- already
#: written -- would have recorded "factory: 158 screens, 5 devices" instead
#: of 156/3, permanently (the UPDATE uses COALESCE, so the first value wins
#: and is never revised). The `_actual` pair refreshes on every sighting and
#: is always safe; `baseline_es_de_fabrica` records whether the dump the
#: `_fabrica` pair came from was virgin at all.
_COLUMNAS_AGREGADAS_MANDOS = {
    "n_dispositivos_actual": "INTEGER",
    "n_pantallas_actual": "INTEGER",
    "baseline_es_de_fabrica": "INTEGER",
}


def data_directory() -> Path:
    """The app's data directory, outside the repo. Creates it if missing."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "HarmonyOne"
    elif sys.platform == "win32":
        # Windows guarda el estado por aplicacion en APPDATA. La rama faltaba
        # aca aunque `app/api.py` ya la tenia: dos lugares decidiendo lo
        # mismo, y uno de los dos sin la mitad de los casos.
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "HarmonyOne"
    elif sys.platform.startswith("linux"):
        # Convencion XDG Base Directory: la variable si esta, si no el
        # directorio de datos por omision del usuario.
        base = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
            / "HarmonyOne"
        )
    else:
        # BSD y cualquier otro Unix: la convencion de XDG es la que mas se
        # parece. Explicito para que se vea que es un DEFAULT y no un sistema
        # que alguien probo.
        base = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
            / "HarmonyOne"
        )
    base.mkdir(parents=True, exist_ok=True)
    (base / "ezhex").mkdir(exist_ok=True)
    return base


def connect(directory: Path | None = None) -> sqlite3.Connection:
    """Opens (and creates if needed) the database in `directory`, or in the
    user's real one."""
    directory = Path(directory) if directory is not None else data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ezhex").mkdir(exist_ok=True)
    conn = sqlite3.connect(directory / "registro.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {f["name"] for f in conn.execute("PRAGMA table_info(grabadas)")}
    for col, kind in _COLUMNAS_AGREGADAS_GRABADAS.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE grabadas ADD COLUMN {col} {kind}")
    cols_m = {f["name"] for f in conn.execute("PRAGMA table_info(mandos)")}
    for col, kind in _COLUMNAS_AGREGADAS_MANDOS.items():
        if col not in cols_m:
            conn.execute(f"ALTER TABLE mandos ADD COLUMN {col} {kind}")
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["repuntes"] = json.loads(d["repuntes"] or "[]")
    d["compuerta_ok"] = None if d["compuerta_ok"] is None else bool(d["compuerta_ok"])
    d["verificado_por_usuario"] = (
        None
        if d["verificado_por_usuario"] is None
        else bool(d["verificado_por_usuario"])
    )
    return d


def record(
    ezhex_source: Path,
    *,
    reference_sha256: str | None = None,
    repoints: Sequence[int] = (),
    gate_ok: bool | None = None,
    result: int | None = None,
    notes: str = "",
    mando_id: int | None = None,
    directory: Path | None = None,
) -> int:
    """Copies `ezhex_source` into the app's own directory, computes the
    sha256 of the GSPM binary (via `ezhex.split`, not of the whole file with
    its header), inserts the row into `grabadas`, and returns its id.

    `mando_id` (optional, see `mandos` / `identify_or_create_remote()`) says
    WHICH physical remote this write was for. It's optional and defaults to
    NULL on purpose: existing callers that don't identify the remote keep
    working exactly as before, and `history()` treats `mando_id=None` rows
    as "unattributed" rather than guessing.

    If the copy to disk fails after the row was inserted, the row is rolled
    back (no `grabadas` entry is left without its matching `.EZHex`).
    """
    ezhex_source = Path(ezhex_source)
    data = ezhex_source.read_bytes()
    _header, binary = ezhex.split(data)  # raises ValueError if it isn't a valid .EZHex
    sha = hashlib.sha256(binary).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = connect(directory)
    try:
        cur = conn.execute(
            "INSERT INTO grabadas "
            "(fecha, ezhex_path, sha256, referencia_sha256, repuntes, "
            " compuerta_ok, resultado, verificado_por_usuario, notas, mando_id) "
            "VALUES (?, '', ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                timestamp,
                sha,
                reference_sha256,
                json.dumps(list(repoints)),
                None if gate_ok is None else int(gate_ok),
                result,
                notes,
                mando_id,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None

        dest_dir = (
            Path(directory) if directory is not None else data_directory()
        ) / "ezhex"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{new_id:06d}_{ezhex_source.name}"
        dest.write_bytes(data)

        conn.execute(
            "UPDATE grabadas SET ezhex_path = ? WHERE id = ?", (str(dest), new_id)
        )
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def history(
    limit: int | None = None,
    *,
    mando_id: int | None = None,
    directory: Path | None = None,
) -> list[dict[str, Any]]:
    """Rows from `grabadas`, most recent first.

    `mando_id`, if given, restricts to recordings attributed to that one
    physical remote (see `mandos`). Default `None` keeps the old behavior
    (every row, regardless of which remote or none), so existing callers
    are unaffected.
    """
    conn = connect(directory)
    try:
        sql = "SELECT * FROM grabadas"
        params: list[Any] = []
        if mando_id is not None:
            sql += " WHERE mando_id = ?"
            params.append(mando_id)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def mark_verified(
    id: int,
    ok: bool,
    notes: str | None = None,
    *,
    directory: Path | None = None,
) -> None:
    """Marks whether the user confirmed that the remote booted fine after
    grabbing `id`.

    `notes`, if given, replaces the existing note; if not, leaves it as is.
    Raises `ValueError` if `id` does not exist.
    """
    conn = connect(directory)
    try:
        if notes is None:
            cur = conn.execute(
                "UPDATE grabadas SET verificado_por_usuario = ? WHERE id = ?",
                (int(ok), id),
            )
        else:
            cur = conn.execute(
                "UPDATE grabadas SET verificado_por_usuario = ?, notas = ? WHERE id = ?",
                (int(ok), notes, id),
            )
        if cur.rowcount == 0:
            conn.rollback()
            raise ValueError(
                f"there is no recording #{id} in the history: reopen the "
                f"History screen and pick one from the list"
            )
        conn.commit()
    finally:
        conn.close()


def for_rollback(id: int, *, directory: Path | None = None) -> dict[str, Any]:
    """`{'ezhex_path': Path, 'repuntes': list[int]}` for entry `id`.

    They are exactly the two pieces of data needed to reconstruct
    `write.py`'s command (`<ezhex> --referencia <ref> [--repunta OFFSET
    ...]`). Raises `ValueError` if the id does not exist, `FileNotFoundError`
    if the `.EZHex` copy is no longer on disk.
    """
    conn = connect(directory)
    try:
        row = conn.execute("SELECT * FROM grabadas WHERE id = ?", (id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(
            f"there is no recording #{id} in the history: reopen the History "
            f"screen and pick one from the list"
        )
    ezhex_path = Path(row["ezhex_path"])
    if not ezhex_path.is_file():
        raise FileNotFoundError(
            f"the .EZHex copy of id={id} is not on disk: {ezhex_path}"
        )
    return {"ezhex_path": ezhex_path, "repuntes": json.loads(row["repuntes"] or "[]")}


# --------------------------------------------------------------------------
# mandos: which physical remote is which
# --------------------------------------------------------------------------


def _mando_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def identidad_debil(identidad: dict[str, Any]) -> tuple[int | None, ...]:
    """The `(arch, skin, fw_mayor, fw_menor)` tuple used as the LAST-RESORT
    matching key -- see the `mandos` docstring at the top of this module for
    why it's weak (two identical-model units on the same firmware collide)."""
    return (
        identidad.get("arch"),
        identidad.get("skin"),
        identidad.get("fw_mayor"),
        identidad.get("fw_menor"),
    )


def serial_utilizable(serial: str | None) -> bool:
    """Whether a serial can be used as an identity at all.

    `libconcord`'s `make_guid()` (`remote.cpp`) formats 16 raw flash bytes as
    a GUID, so a remote whose serial area was never programmed -- or was
    erased -- returns a perfectly non-empty string of all F's or all 0's.
    Treating that as an identity silently folds every such unit into ONE
    `mandos` row, and labels it with the HIGHEST confidence ('serial').
    Normalising `""` alone is not enough.
    """
    if not serial:
        return False
    hexes = [c for c in serial.lower() if c in "0123456789abcdef"]
    if not hexes:
        return False
    return not (all(c == "f" for c in hexes) or all(c == "0" for c in hexes))


def identify_or_create_remote(
    identidad: dict[str, Any],
    *,
    serial: str | None = None,
    baseline_sha256: str | None = None,
    baseline_path: Path | None = None,
    n_dispositivos_fabrica: int | None = None,
    n_pantallas_fabrica: int | None = None,
    n_dispositivos_actual: int | None = None,
    n_pantallas_actual: int | None = None,
    baseline_es_de_fabrica: bool | None = None,
    directory: Path | None = None,
) -> dict[str, Any]:
    """Finds the `mandos` row for the remote described by `identidad`
    (`{'arch', 'skin', 'fw_mayor', 'fw_menor', 'config_usada', 'config_total'}`,
    the same shape `app/remote.py::identify()`'s `identidad` stage returns),
    or creates one. Returns the row as a dict, plus `fresh: bool`.

    Matching precedence (see the `mandos` docstring): `serial` if given and
    non-empty, else `baseline_sha256` if given, else the soft tuple
    `identidad_debil()` against the MOST RECENTLY SEEN row that matches --
    best-effort, and the returned row says so via `identidad_confianza`.

    On a match, enriches the existing row: fills in any of
    `serial`/`baseline_sha256`/`baseline_path`/`n_dispositivos_fabrica`/
    `n_pantallas_fabrica` that were NULL before and are given now, and always
    bumps `ultima_vez` and refreshes `config_usada`/`config_total` (those
    drift over the remote's life as devices get added/removed -- they are
    NOT part of any matching key past first contact).
    """
    # An empty OR degenerate serial (all F / all 0) is not an identity: see
    # `serial_utilizable()`. Two different units would otherwise collapse into
    # one row carrying the highest confidence label.
    serial = serial if serial_utilizable(serial) else None

    # The FACTORY numbers are only accepted from a dump that still looks
    # virgin (`leer_flash_baseline.derivar()['parece_de_fabrica']`). Anything
    # else is this remote's CURRENT state, which is a different fact and goes
    # in the `_actual` columns. Passing factory numbers with
    # `baseline_es_de_fabrica` False or None drops them rather than freezing a
    # fiction that the COALESCE below would never revise.
    if baseline_es_de_fabrica is not True:
        n_dispositivos_fabrica = None
        n_pantallas_fabrica = None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = connect(directory)
    try:
        row = None
        confianza = None
        if serial:
            row = conn.execute(
                "SELECT * FROM mandos WHERE serial = ?", (serial,)
            ).fetchone()
            if row is not None:
                confianza = "serial"
        if row is None and baseline_sha256:
            row = conn.execute(
                "SELECT * FROM mandos WHERE baseline_sha256 = ?", (baseline_sha256,)
            ).fetchone()
            if row is not None:
                confianza = "baseline_sha256"
        # The weak tuple is ONLY tried when the caller gave NO strong
        # identity at all this call. If a `serial` (or `baseline_sha256`)
        # WAS given but matched no existing row, that's grounds to create a
        # NEW mando -- not to silently fold it into an unrelated remote
        # that merely happens to share arch/skin/firmware.
        if row is None and not serial and not baseline_sha256:
            arch, skin, fw_mayor, fw_menor = identidad_debil(identidad)
            if arch is not None:
                row = conn.execute(
                    "SELECT * FROM mandos WHERE arch=? AND skin=? AND "
                    "fw_mayor=? AND fw_menor=? ORDER BY ultima_vez DESC LIMIT 1",
                    (arch, skin, fw_mayor, fw_menor),
                ).fetchone()
                if row is not None:
                    confianza = "debil"

        if row is not None:
            mando_id = row["id"]
            conn.execute(
                "UPDATE mandos SET ultima_vez=?, "
                "serial=COALESCE(?, serial), "
                "config_usada=COALESCE(?, config_usada), "
                "config_total=COALESCE(?, config_total), "
                "baseline_sha256=COALESCE(?, baseline_sha256), "
                "baseline_path=COALESCE(?, baseline_path), "
                "n_dispositivos_fabrica=COALESCE(?, n_dispositivos_fabrica), "
                "n_pantallas_fabrica=COALESCE(?, n_pantallas_fabrica), "
                # the _actual pair is the opposite of the _fabrica one: it
                # describes the remote NOW, so a fresh reading always wins
                "n_dispositivos_actual=COALESCE(?, n_dispositivos_actual), "
                "n_pantallas_actual=COALESCE(?, n_pantallas_actual), "
                "baseline_es_de_fabrica=COALESCE(?, baseline_es_de_fabrica), "
                "identidad_confianza=? "
                "WHERE id=?",
                (
                    now,
                    serial,
                    identidad.get("config_usada"),
                    identidad.get("config_total"),
                    baseline_sha256,
                    str(baseline_path) if baseline_path else None,
                    n_dispositivos_fabrica,
                    n_pantallas_fabrica,
                    n_dispositivos_actual,
                    n_pantallas_actual,
                    None
                    if baseline_es_de_fabrica is None
                    else int(baseline_es_de_fabrica),
                    confianza,
                    mando_id,
                ),
            )
            conn.commit()
            fresh = False
        else:
            arch, skin, fw_mayor, fw_menor = identidad_debil(identidad)
            confianza = (
                "serial"
                if serial
                else ("baseline_sha256" if baseline_sha256 else "debil")
            )
            cur = conn.execute(
                "INSERT INTO mandos "
                "(primera_vez, ultima_vez, serial, arch, skin, fw_mayor, fw_menor, "
                " config_usada, config_total, baseline_sha256, baseline_path, "
                " n_dispositivos_fabrica, n_pantallas_fabrica, "
                " n_dispositivos_actual, n_pantallas_actual, "
                " baseline_es_de_fabrica, identidad_confianza) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    now,
                    serial,
                    arch,
                    skin,
                    fw_mayor,
                    fw_menor,
                    identidad.get("config_usada"),
                    identidad.get("config_total"),
                    baseline_sha256,
                    str(baseline_path) if baseline_path else None,
                    n_dispositivos_fabrica,
                    n_pantallas_fabrica,
                    n_dispositivos_actual,
                    n_pantallas_actual,
                    None
                    if baseline_es_de_fabrica is None
                    else int(baseline_es_de_fabrica),
                    confianza,
                ),
            )
            mando_id = cur.lastrowid
            conn.commit()
            fresh = True

        row = conn.execute("SELECT * FROM mandos WHERE id = ?", (mando_id,)).fetchone()
        d = _mando_row_to_dict(row)
        d["fresh"] = fresh
        return d
    finally:
        conn.close()


def mandos_listar(*, directory: Path | None = None) -> list[dict[str, Any]]:
    """Every remote this app has ever identified, most recently seen first."""
    conn = connect(directory)
    try:
        rows = conn.execute("SELECT * FROM mandos ORDER BY ultima_vez DESC").fetchall()
        return [_mando_row_to_dict(r) for r in rows]
    finally:
        conn.close()


#: Where the project's single hardcoded baseline lives. It is the factory
#: dump of THIS user's remote, taken before anything was written to it.
BASELINE_HEREDADO = (
    Path(__file__).resolve().parent.parent / "backups" / "config_raw.bin"
)


def baseline_vigente(
    mando_id: int | None = None, *, directory: Path | None = None
) -> dict[str, Any]:
    """THE CUT POINT: which blob should be used as the reference baseline,
    and why -- instead of every caller reaching for `backups/config_raw.bin`.

    Returns `{'path', 'origen', 'mando_id', 'sha256', 'es_de_fabrica',
    'motivo'}` where `origen` is:

      * `'remote'`      -- a baseline read off that remote and stored for it
      * `'heredado'`   -- the checked-in `backups/config_raw.bin`
      * `None`         -- nothing usable found

    **This does not migrate anybody by itself.** Today `app/api.py`,
    `app/learn_ir.py`, `app/check_load_bearing.py`, `app/check_learn.py`
    and `app/check_ir_manual.py` all still open `backups/config_raw.bin`
    directly, and `app/check_load_bearing.py` MUST keep doing so -- its whole
    job is reproducing one exact md5 from one exact input. The others are the
    ones worth moving here, one at a time, each with its own control.

    The fallback is deliberately loud in `reason`: deriving from another
    unit's factory dump is a real (if usually harmless) approximation, and it
    should be visible rather than assumed.
    """
    row = None
    if mando_id is not None:
        conn = connect(directory)
        try:
            row = conn.execute(
                "SELECT * FROM mandos WHERE id = ?", (mando_id,)
            ).fetchone()
        finally:
            conn.close()

    if row is not None and row["baseline_path"]:
        p = Path(row["baseline_path"])
        if p.is_file():
            de_fabrica = row["baseline_es_de_fabrica"]
            return {
                "path": p,
                "origin": "remote",
                "mando_id": row["id"],
                "sha256": row["baseline_sha256"],
                "es_de_fabrica": None if de_fabrica is None else bool(de_fabrica),
                "reason": (
                    "remote %d's own baseline%s"
                    % (
                        row["id"],
                        ""
                        if de_fabrica
                        else " -- WATCH OUT: it does not look like a virgin dump, its "
                        "counts are NOT the factory ones",
                    )
                ),
            }

    if BASELINE_HEREDADO.is_file():
        return {
            "path": BASELINE_HEREDADO,
            "origin": "heredado",
            "mando_id": row["id"] if row is not None else None,
            "sha256": None,
            "es_de_fabrica": True,
            "reason": (
                "no baseline of its own for this remote: using "
                "backups/config_raw.bin, which is the factory dump of "
                "THIS user's remote. For another unit it is an approximation "
                "-- lee su baseline con config_work/read_flash_baseline.py "
                "BEFORE writing anything to it."
            ),
        }

    return {
        "path": None,
        "origin": None,
        "mando_id": None,
        "sha256": None,
        "es_de_fabrica": None,
        "reason": "there is no baseline of its own and no backups/config_raw.bin",
    }


def record_devices(
    write_id: int,
    devices: list[dict[str, Any]],
    *,
    directory: Path | None = None,
) -> None:
    """Records the devices left on the remote after `write_id`.

    Each dict in `devices` accepts `posicion, manufacturer, model, name,
    global_device_id, n_comandos` (missing ones stay NULL). Schema inferred
    from `account_export` -- see the module's docstring.
    """
    conn = connect(directory)
    try:
        exists = conn.execute(
            "SELECT 1 FROM grabadas WHERE id = ?", (write_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"there is no recording #{write_id} in the history")
        conn.executemany(
            "INSERT INTO dispositivos_en "
            "(grabada_id, posicion, manufacturer, model, name, global_device_id, n_comandos) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    write_id,
                    d.get("posicion"),
                    d.get("manufacturer"),
                    d.get("model"),
                    d.get("name"),
                    d.get("global_device_id"),
                    d.get("n_commands"),
                )
                for d in devices
            ],
        )
        conn.commit()
    finally:
        conn.close()


def cache_catalog(
    items: list[dict[str, Any]], *, directory: Path | None = None
) -> None:
    """Caches `GlobalCatalogClient.search()`/`device_summaries()` results
    locally.

    Each dict accepts `manufacturer, model, global_device_id, device_type,
    profile_uri` and optionally the rest of the raw JSON (saved whole in
    `crudo` via `json.dumps`, in case `account_export`'s model changes).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = connect(directory)
    try:
        conn.executemany(
            "INSERT INTO catalogo "
            "(consultado_en, manufacturer, model, global_device_id, device_type, "
            " profile_uri, crudo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    now,
                    it.get("manufacturer"),
                    it.get("model"),
                    it.get("global_device_id"),
                    it.get("device_type"),
                    it.get("profile_uri"),
                    json.dumps(it),
                )
                for it in items
            ],
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    # CHECK: own database in a temp directory (never touches the user's real
    # directory). Records three entries, lists them, marks the third as
    # failed, asks for the rollback of the previous one (the second) and
    # verifies its copied .EZHex exists on disk and its sha256 matches the
    # original GSPM binary.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        source = d / "source"
        source.mkdir()

        # Three simulated ".EZHex" files: minimal header + a different GSPM
        # binary each, so each one's sha256 is distinguishable.
        cases = [
            ("a", [], True, 0),
            ("b", [0x20, 0x24], True, 0),
            ("c", [], False, 1),  # this is the one that gets marked as failed
        ]
        paths: dict[str, Path] = {}
        binaries: dict[str, bytes] = {}
        for tag, _repoints, _gate, _result in cases:
            binary = b"GSPM" + (tag.encode() * 64)
            data = b"<INFORMATION><X>1</X></INFORMATION>\n" + binary
            p = source / f"one_{tag}.EZHex"
            p.write_bytes(data)
            paths[tag] = p
            binaries[tag] = binary

        ids: dict[str, int] = {}
        print("== record ==")
        for tag, repoints, gate, result in cases:
            i = record(
                paths[tag],
                reference_sha256="deadbeef",
                repoints=repoints,
                gate_ok=gate,
                result=result,
                notes=f"simulated {tag}",
                directory=d,
            )
            ids[tag] = i
            print(f"  recorded {tag} -> id {i}")

        print("\n== history (just recorded) ==")
        rows = history(directory=d)
        assert len(rows) == 3, f"expected 3 rows, there are {len(rows)}"
        for row in rows:
            print(" ", row)

        print("\n== mark id c as failed ==")
        mark_verified(ids["c"], False, "remote in boot loop", directory=d)
        rows = history(directory=d)
        row_c = next(r for r in rows if r["id"] == ids["c"])
        assert row_c["verificado_por_usuario"] is False, row_c
        assert row_c["notas"] == "remote in boot loop", row_c
        print(
            f"  id {ids['c']} -> verificado_por_usuario={row_c['verificado_por_usuario']!r}"
        )

        print("\n== ask for rollback of the previous one (b) ==")
        rb = for_rollback(ids["b"], directory=d)
        print(f"  {rb}")
        assert rb["repuntes"] == [0x20, 0x24], rb
        assert rb["ezhex_path"].is_file(), "the .EZHex copy of b is not on disk"

        _header_copy, binary_copy = ezhex.split(rb["ezhex_path"].read_bytes())
        sha_copy = hashlib.sha256(binary_copy).hexdigest()
        sha_original = hashlib.sha256(binaries["b"]).hexdigest()
        assert sha_copy == sha_original, (sha_copy, sha_original)
        print(f"  sha256 copy == sha256 original: {sha_copy}")

        print("\n== final history ==")
        for row in history(directory=d):
            print(" ", row)

        # --- Negatives: every check needs its negative. ---
        print("\n== negatives ==")

        try:
            mark_verified(9999, True, directory=d)
        except ValueError as e:
            print(f"  mark_verified(nonexistent id) -> ValueError OK: {e}")
        else:
            raise AssertionError(
                "mark_verified with a nonexistent id should have raised"
            )

        try:
            for_rollback(9999, directory=d)
        except ValueError as e:
            print(f"  for_rollback(nonexistent id) -> ValueError OK: {e}")
        else:
            raise AssertionError(
                "for_rollback with a nonexistent id should have raised"
            )

        # 'a's .EZHex copy disappears from disk (simulates external cleanup /
        # full disk).
        copy_a = Path(history(directory=d)[-1]["ezhex_path"])
        assert copy_a.name.endswith("one_a.EZHex"), copy_a
        copy_a.unlink()
        try:
            for_rollback(ids["a"], directory=d)
        except FileNotFoundError as e:
            print(f"  for_rollback(deleted copy) -> FileNotFoundError OK: {e}")
        else:
            raise AssertionError("for_rollback with a deleted copy should have raised")

        # .EZHex without a valid <INFORMATION> header: record() cannot
        # accept it, and must not leave an orphan row in 'grabadas' if it fails.
        garbage = source / "garbage.EZHex"
        garbage.write_bytes(b"this is not an EZHex")
        rows_before = len(history(directory=d))
        try:
            record(garbage, directory=d)
        except ValueError as e:
            print(f"  record(invalid EZHex) -> ValueError OK: {e}")
        else:
            raise AssertionError("record with an invalid EZHex should have raised")
        rows_after = len(history(directory=d))
        assert rows_after == rows_before, (
            "record() left an orphan row after failing",
            rows_before,
            rows_after,
        )
        print(f"  no orphan row after the failure: {rows_before} == {rows_after}")

        # --- mandos: multi-remote identity ---
        print("\n== mandos ==")

        identidad_uno = {
            "arch": 12,
            "skin": 1,
            "fw_mayor": 3,
            "fw_menor": 11,
            "config_usada": 1316666,
            "config_total": 1572864,
        }
        identidad_dos = {
            "arch": 12,
            "skin": 1,
            "fw_mayor": 3,
            "fw_menor": 11,
            "config_usada": 998000,
            "config_total": 1572864,
        }

        # Two DIFFERENT physical remotes, same model/firmware (the case a
        # tuple-only fingerprint could not tell apart): distinguished by
        # `serial`, the strong identity.
        m1 = identify_or_create_remote(identidad_uno, serial="SN-AAA-001", directory=d)
        m2 = identify_or_create_remote(identidad_dos, serial="SN-BBB-002", directory=d)
        assert m1["fresh"] and m2["fresh"], (m1, m2)
        assert m1["id"] != m2["id"], "two different serials collapsed into one mando"
        assert m1["identidad_confianza"] == "serial", m1
        print(
            f"  two remotes, same model/firmware, different serial -> "
            f"mando #{m1['id']} != mando #{m2['id']}"
        )

        # Re-identifying the FIRST remote (serial repeats, config_usada
        # DRIFTED because a device got added) must return the SAME id, not
        # create a third row, and must refresh config_usada.
        identidad_uno_editada = dict(identidad_uno, config_usada=1349000)
        m1_otra_vez = identify_or_create_remote(
            identidad_uno_editada, serial="SN-AAA-001", directory=d
        )
        assert not m1_otra_vez["fresh"], m1_otra_vez
        assert m1_otra_vez["id"] == m1["id"], (m1_otra_vez, m1)
        assert m1_otra_vez["config_usada"] == 1349000, m1_otra_vez
        assert len(mandos_listar(directory=d)) == 2, mandos_listar(directory=d)
        print(
            f"  re-identified by serial -> SAME mando #{m1_otra_vez['id']}, "
            f"config_usada refreshed to {m1_otra_vez['config_usada']}"
        )

        # No serial and no baseline: falls back to the weak tuple. A THIRD
        # remote sharing arch/skin/fw with m1 but with no serial reported
        # correctly collides with m1 (documented limitation, not a bug) --
        # `identidad_confianza` says 'debil' so a caller can tell.
        m1_sin_serial = identify_or_create_remote(identidad_uno, directory=d)
        assert not m1_sin_serial["fresh"], m1_sin_serial
        assert m1_sin_serial["id"] == m1["id"], m1_sin_serial
        assert m1_sin_serial["identidad_confianza"] == "debil", m1_sin_serial
        print(
            f"  no serial given -> falls back to the weak tuple, lands on "
            f"mando #{m1_sin_serial['id']} (confianza=debil, as documented)"
        )

        # Recordings attributed to each mando: history(mando_id=...) filters.
        gid1 = record(
            paths["a"], reference_sha256="ref1", mando_id=m1["id"], directory=d
        )
        gid2 = record(
            paths["b"], reference_sha256="ref2", mando_id=m2["id"], directory=d
        )
        hist_m1 = history(mando_id=m1["id"], directory=d)
        hist_m2 = history(mando_id=m2["id"], directory=d)
        assert [r["id"] for r in hist_m1] == [gid1], hist_m1
        assert [r["id"] for r in hist_m2] == [gid2], hist_m2
        assert len(history(directory=d)) > len(hist_m1) + len(hist_m2), (
            "history() without mando_id should still return every row, "
            "including the ones recorded with mando_id=None earlier"
        )
        print(
            f"  history(mando_id=#{m1['id']}) -> only {[r['id'] for r in hist_m1]}; "
            f"history(mando_id=#{m2['id']}) -> only {[r['id'] for r in hist_m2]}; "
            f"unfiltered history() still returns every row"
        )

        # -- NEGATIVES the earlier version of this self-test never touched --
        print("\n== mandos: negatives ==")

        # (1) A degenerate serial is NOT an identity. `make_guid()` turns
        # unprogrammed/erased flash into a non-empty all-F GUID, so two
        # different units would otherwise merge into one row -- and be
        # labelled with the HIGHEST confidence while doing it.
        assert serial_utilizable("ABC123-0001") is True
        assert serial_utilizable("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF") is False
        assert serial_utilizable("00000000-0000-0000-0000-000000000000") is False
        assert serial_utilizable("") is False and serial_utilizable(None) is False
        deg = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"
        d1 = identify_or_create_remote(
            {"arch": 12, "skin": 2, "fw_mayor": 4, "fw_menor": 5},
            serial=deg,
            baseline_sha256="sha-unidad-A",
            directory=d,
        )
        d2 = identify_or_create_remote(
            {"arch": 12, "skin": 2, "fw_mayor": 4, "fw_menor": 5},
            serial=deg,
            baseline_sha256="sha-unidad-B",
            directory=d,
        )
        assert d1["id"] != d2["id"], (d1, d2)
        assert d1["identidad_confianza"] != "serial", d1
        print(
            f"  degenerate serial (all F) -> does NOT merge two units "
            f"(#{d1['id']} != #{d2['id']}), and is not labelled 'serial'"
        )

        # (2) Factory counts are refused from a dump that isn't virgin.
        sucio = identify_or_create_remote(
            {"arch": 12, "skin": 2, "fw_mayor": 4, "fw_menor": 5},
            serial="UNIDAD-SUCIA-1",
            n_dispositivos_fabrica=5,
            n_pantallas_fabrica=158,
            n_dispositivos_actual=5,
            n_pantallas_actual=158,
            baseline_es_de_fabrica=False,
            directory=d,
        )
        assert sucio["n_dispositivos_fabrica"] is None, sucio
        assert sucio["n_pantallas_fabrica"] is None, sucio
        assert sucio["n_dispositivos_actual"] == 5, sucio
        assert sucio["n_pantallas_actual"] == 158, sucio
        print(
            "  ALREADY EDITED dump (158/5) -> _fabrica stays NULL, _actual "
            "stores 158/5, baseline_es_de_fabrica=0"
        )

        limpio = identify_or_create_remote(
            {"arch": 12, "skin": 2, "fw_mayor": 4, "fw_menor": 5},
            serial="UNIDAD-LIMPIA-1",
            n_dispositivos_fabrica=3,
            n_pantallas_fabrica=156,
            n_dispositivos_actual=3,
            n_pantallas_actual=156,
            baseline_es_de_fabrica=True,
            directory=d,
        )
        assert limpio["n_dispositivos_fabrica"] == 3, limpio
        assert limpio["n_pantallas_fabrica"] == 156, limpio
        print("  volcado VIRGEN (156/3) -> _fabrica SI se guarda")

        # (3) ...and a later dirty sighting of the SAME remote must not
        # overwrite the good factory numbers, nor invent new ones.
        otra = identify_or_create_remote(
            {"arch": 12, "skin": 2, "fw_mayor": 4, "fw_menor": 5},
            serial="UNIDAD-LIMPIA-1",
            n_dispositivos_fabrica=9,
            n_pantallas_fabrica=999,
            n_dispositivos_actual=5,
            n_pantallas_actual=158,
            baseline_es_de_fabrica=False,
            directory=d,
        )
        assert otra["id"] == limpio["id"], (otra, limpio)
        assert otra["n_dispositivos_fabrica"] == 3, otra
        assert otra["n_pantallas_fabrica"] == 156, otra
        assert otra["n_dispositivos_actual"] == 5, otra
        print(
            "  second DIRTY sighting of the same remote -> _fabrica still 156/3, "
            "_actual pasa a 158/5"
        )

        # (4) baseline_vigente(): falls back, and says so.
        vig = baseline_vigente(directory=d)
        assert vig["origin"] == "heredado", vig
        assert "aproximacion" in vig["reason"], vig
        print("  baseline_vigente() with no remote -> origen=%r, with a motivo" % vig["origin"])

        print("\nCHECK OK")
