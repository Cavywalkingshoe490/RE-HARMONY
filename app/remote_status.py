#!/usr/bin/env python3
"""Las TRES situaciones reales del control, y nada mas que esas tres.

Hoy `Api.remote_status()` mezcla dos cosas que no son lo mismo: si el
Harmony One esta enchufado, y que hay guardado en disco de una sesion
anterior (`_last_devices_snapshot()`, que lee JSON cacheado en
`account_export/output/` SIN mirar si hay mando conectado). El resultado es lo
que reporta el brief: la pantalla Control puede decir "tu control tiene 5
dispositivos" con el cable desenchufado, porque esta leyendo un archivo, no
el aparato.

Este modulo resuelve UNA sola pregunta -- "que hay REALMENTE, ahora mismo" --
y la respuesta es siempre una de tres, nunca una mezcla:

    DESCONECTADO          -- no se encontro el mando. Como mucho se puede
                             ofrecer "la ultima configuracion que este
                             proyecto le escribio", con esa frase exacta,
                             nunca como si fuera el estado del aparato.
    CONECTADO_VERDAD      -- se encontro Y se pudo leer el volcado crudo de
                             flash (`read_flash_baseline.py`), y ese volcado
                             valida (cookie GSPM, cierre PTYY). ESTE blob --
                             no un archivo cacheado -- es la verdad: se
                             puede correr `list_devices.py`/`activities.py`/
                             `keys_map.py` sobre el directamente.
    CONECTADO_SIN_VERDAD  -- se encontro el mando (identity respondio) pero
                             el volcado no valido (chunks fallidos, cookie
                             mala, cierre malo). Se dice el motivo medido,
                             nunca se inventa un numero de dispositivos.

`Connect` en la UI == llamar `refrescar()` de nuevo: no hay estado
escondido que sobreviva a un Connect, cada llamada vuelve a preguntarle al
aparato desde cero (mismo espiritu que el comentario de `app/ui/app.js`
sobre RULE 1: "Connect is the ONLY gesture that unfreezes").

El boton se llama **Connect** y no "Refresh" porque eso es lo que hace:
conectarse al mando y leerlo. Y como la lectura son ~80 transacciones USB
de 16 KiB (~2 min medidos), `refrescar()` acepta `on_evento` y va
informando el avance REAL -- bytes que llegaron sobre bytes totales, no un
reloj. Ver `progreso.parsear_linea_lectura`.

Y hay una tercera pregunta, mas barata, que no es ninguna de las tres:
`presencia()` -- "¿sigue enchufado?" -- que solo identifica (`get_identity`)
sin leer el flash. Sirve para que el estado conectado sobreviva al cambio
de solapa sin volverse una bandera prendida: al volver a Control se muestra
lo ultimo medido y se REMIDE la presencia, en vez de releer todo o de
creerle a una variable.

Todo lo que toca el dispositivo va por SUBPROCESS a
`config_work/read_flash_baseline.py --json` (el mismo patron que
`app/remote.py` ya usa para `read_config.py`: aislar el acceso a libconcord en un
proceso aparte, nunca importar el ctypes-loader dentro del proceso largo de
pywebview). Es de SOLO LECTURA: `read_flash_baseline.py` solo llama
`get_identity()` y `read_flash_at()` -- ni un solo `write_*`/`erase_*`. Ver
el PROHIBIDO del brief: esto nunca se corrio contra hardware real en esta
sesion porque no hay mando conectado; lo que SI se corrio y paso es el
camino "no se encontro el mando" (ver abajo, y la nota de verificacion al
pie de este archivo).
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import _runtime

try:  # blando a proposito: sin `progress.py` la lectura sigue andando, muda
    import progress
except Exception:  # noqa: BLE001
    progress = None  # type: ignore[assignment]

CONFIG_WORK = Path(__file__).resolve().parent.parent / "config_work"
LEER_BASELINE_PY = CONFIG_WORK / "read_flash_baseline.py"

DESCONECTADO = "desconectado"
CONECTADO_VERDAD = "conectado_verdad"
CONECTADO_SIN_VERDAD = "conectado_sin_verdad"

#: Mandatory text for the DESCONECTADO case -- see the brief: "At most:
#: 'the last configuration this project wrote to it', clearly
#: set apart." It lives in Python, not in the template, for the same
#: reason as `TEXTO_CIERRE_DE_LAZO` in `app/api.py`: so it cannot be softened
#: by touching only the HTML.
#: In ENGLISH, like the rest of the UI. It lives in Python all the same (not
#: in the template) for the usual reason: so the warning cannot be softened
#: by touching only the HTML.
#: Dice **Connect**, no "Refresh": el boton se llama asi porque eso es lo
#: que hace -- se conecta al mando y lo lee. "Refresh" no nombraba ninguna
#: de las dos cosas. El texto vive aca y no en la plantilla por la razon de
#: siempre; que ademas obliga a que si el boton se renombra, se renombre en
#: los dos lados o el control de `app/_verif_conectar.py` lo canta.
TEXTO_DESCONECTADO = (
    "Your Harmony One wasn't found over USB. What's shown below is NOT the "
    "state of your control: it's the last configuration THIS PROJECT wrote, "
    "saved in the local history. Plug the cable in and tap Connect."
)
TEXTO_SIN_VERDAD_PREFIJO = (
    "Your Harmony One is connected and said who it is, but what it returned "
    "when its configuration was read back is not a valid config: "
)


def _last_json_line(text: str) -> dict | None:
    """La ULTIMA linea de `text` que parsea como JSON.

    `read_flash_baseline.py --json` imprime, antes que nada, cualquier
    DEBUG que libconcord mismo tire por stdout (medido: "DEBUG (FindRemote):
    ..." antes de una sola linea JSON al final) -- por eso no alcanza con
    tomar la primera linea ni con parsear todo el stdout entero.
    """
    last = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            last = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
    return last


def _read_flash(
    target: Path,
    timeout: float,
    on_evento: Callable[[dict], None] | None = None,
) -> tuple[dict | None, str]:
    """Corre `read_flash_baseline.py --salida <target> --json` por
    subprocess. Devuelve `(info_o_None, texto_de_diagnostico)`.

    `info` es lo que `read_raw_baseline()` devuelve (siempre, ya no
    lanza), mas `encontrado`. `None` significa que ni siquiera se pudo
    correr el script (no encontrado, timeout del propio subprocess, etc.) --
    distinto de `encontrado=False`, que es una respuesta LIMPIA del script
    diciendo "no hable con el mando".

    `on_evento` -- si se pasa -- recibe un evento por linea A MEDIDA QUE
    SALE (ver `progreso.parsear_linea_lectura`). Es LO QUE HACE POSIBLE LA
    BARRA: sin esto la lectura entera (~80 transacciones USB de 16 KiB,
    ~2 min medidos) transcurria con `subprocess.run`, que no devuelve nada
    hasta el final, y la pantalla se quedaba muda todo ese rato. Con
    `on_evento=None` el comportamiento observable es el de siempre.

    Es SOLO LECTURA en los dos caminos: el argv es el mismo y ese script
    no llama una sola primitiva de escritura.
    """
    if not LEER_BASELINE_PY.exists():
        return None, "%s no existe" % LEER_BASELINE_PY
    argv = [
        *_runtime.interprete(),
        str(LEER_BASELINE_PY),
        "--salida",
        str(target),
        "--json",
    ]

    if on_evento is not None and progress is not None:
        # Camino en vivo. Reusa el manejo de proceso ya endurecido de
        # `progreso.ejecutar_en_vivo()` -- hilo lector, timeout duro sobre
        # `wait()` (no sobre "llego una linea"), y matar el GRUPO si algo
        # queda con la tuberia abierta. Son los mismos cuelgues: un lector
        # que espera EOF para siempre no distingue si el proceso escribia
        # o leia.
        r = progress.ejecutar_en_vivo(
            argv,
            CONFIG_WORK,
            on_evento,
            timeout=timeout,
            parser=progress.parsear_linea_lectura,
        )
        # `ejecutar_en_vivo` junta stderr dentro de stdout (`stderr=STDOUT`),
        # asi que el transcript trae las dos cosas -- y la linea JSON sigue
        # siendo la ultima que empieza con `{`.
        text = r.get("transcript") or ""
        if r.get("expirado"):
            return None, (
                "the remote answered but reading its flash took longer than "
                "%ss. It is found over USB; only the full read timed out." % timeout
            )
        info = _last_json_line(text)
        if info is None:
            return None, (
                (r.get("error") or "").strip()
                or text.strip()[-400:]
                or "could not interpret the response"
            )
        return info, text

    try:
        r2 = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(CONFIG_WORK),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, (
            "the remote answered but reading its flash took longer than %ss. "
            "It is found over USB; only the full read timed out." % timeout
        )
    except OSError as exc:
        return None, str(exc)
    info = _last_json_line(r2.stdout)
    if info is None:
        return None, (r2.stderr or "").strip() or "could not interpret the response"
    return info, r2.stderr or ""


def presencia(timeout: float = 45.0) -> dict:
    """¿Sigue enchufado? MEDIDO, y barato: corre
    `read_flash_baseline.py --identidad --json`, que hace `init_concord` +
    `get_identity` y nada mas -- NO lee el flash.

    Existe por el requisito de que el estado conectado sobreviva al cambio
    de solapa SIN convertirse en una bandera que quedo prendida. Volver a
    Control muestra al instante lo ULTIMO MEDIDO y dispara esto: si el
    mando ya no esta, el estado se cae; si esta, se queda. Lo que no hace
    es volver a pagar los ~2 min de la lectura entera para responder una
    pregunta de si/no.

    Devuelve `{'presente': bool, 'identidad': {...}|None, 'reason': str|None}`
    y NUNCA lanza.
    """
    if not LEER_BASELINE_PY.exists():
        return {
            "presente": False,
            "identidad": None,
            "reason": "%s no existe" % LEER_BASELINE_PY,
        }
    argv = [
        *_runtime.interprete(),
        str(LEER_BASELINE_PY),
        "--identidad",
        "--json",
    ]
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(CONFIG_WORK),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "presente": False,
            "identidad": None,
            "reason": "identifying the remote took longer than %ss" % timeout,
        }
    except OSError as exc:
        return {"presente": False, "identidad": None, "reason": str(exc)}
    info = _last_json_line(r.stdout)
    if info is None:
        return {
            "presente": False,
            "identidad": None,
            "reason": (r.stderr or "").strip() or "could not interpret the response",
        }
    if not info.get("encontrado"):
        return {
            "presente": False,
            "identidad": None,
            "reason": info.get("reason") or "the remote was not found",
        }
    return {
        "presente": True,
        "identidad": {
            "arch": info.get("arch"),
            "skin": info.get("skin"),
            "fw_mayor": info.get("fw_mayor"),
            "fw_menor": info.get("fw_menor"),
            "config_usada": info.get("config_usada"),
            "config_total": info.get("config_total"),
            "serial": info.get("serial"),
        },
        "reason": None,
    }


def refrescar(
    directorio_datos: Path,
    *,
    timeout: float = 300.0,
    last_local_config: dict | None = None,
    on_evento: Callable[[dict], None] | None = None,
) -> dict:
    """LA funcion de este modulo. Vuelve a preguntar, siempre, nunca cachea.

    `directorio_datos`: donde dejar el volcado crudo cuando se pudo leer
    (queda en `<directorio_datos>/verdad_actual.bin`, sobrescrito en cada
    llamada -- es SIEMPRE la ultima lectura, no un historial).

    `last_local_config`: lo que el llamador ya sepa del historial local
    (tipicamente `registro.history()` filtrado al ultimo grabado y
    confirmado) -- se adjunta tal cual bajo esa clave en el caso
    DESCONECTADO, SIN reformular como si fuera el estado del aparato. Este
    modulo no importa `history.py` (no le hace falta: el llamador ya lo
    tiene resuelto via `Api._current_reference()` / `history()`).

    `on_evento`: si se pasa, recibe un evento POR LINEA a medida que la
    lectura avanza (`progreso.parsear_linea_lectura`), incluyendo un
    `{'kind': 'leido', 'bytes_leidos': n, 'bytes_totales': N}` por cada
    chunk de 16 KiB que llega. Es lo que le da a la barra numeros medidos
    en vez de una animacion. Sin el, esta funcion se comporta exactamente
    como siempre.

    Devuelve siempre:
        {'estado': DESCONECTADO|CONECTADO_VERDAD|CONECTADO_SIN_VERDAD,
         'mensaje': texto para la linea de estado,
         'identidad': {...} | None,   # arch/skin/firmware/serial si se supo
         'blob': str | None,          # SOLO si CONECTADO_VERDAD
         'motivo': str | None,        # SOLO si CONECTADO_SIN_VERDAD
         'ultima_config_local': ... | None,   # SOLO si DESCONECTADO
         'medido_en': ISO-8601 UTC}
    """
    directorio_datos = Path(directorio_datos)
    directorio_datos.mkdir(parents=True, exist_ok=True)
    target = directorio_datos / "verdad_actual.bin"
    measured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    info, stderr = _read_flash(target, timeout, on_evento)

    if info is None or not info.get("encontrado"):
        reason = (info or {}).get("reason") if info else None
        return {
            "state": DESCONECTADO,
            "mensaje": TEXTO_DESCONECTADO,
            "identidad": None,
            "blob": None,
            "reason": reason or stderr or "the remote was not found",
            "last_local_config": last_local_config,
            "measured_at": measured_at,
        }

    identidad = {
        "arch": info.get("arch"),
        "skin": info.get("skin"),
        "fw_mayor": info.get("fw_mayor"),
        "fw_menor": info.get("fw_menor"),
        "config_usada": info.get("config_usada"),
        "config_total": info.get("config_total"),
        "serial": info.get("serial"),
    }

    if not info.get("valido"):
        return {
            "state": CONECTADO_SIN_VERDAD,
            "mensaje": TEXTO_SIN_VERDAD_PREFIJO
            + (
                info.get("reason")
                or "; ".join(info.get("problemas") or [])
                or "motivo no medido"
            ),
            "identidad": identidad,
            "blob": None,
            "reason": info.get("reason") or "; ".join(info.get("problemas") or []),
            "last_local_config": None,
            "measured_at": measured_at,
        }

    return {
        "state": CONECTADO_VERDAD,
        "mensaje": (
            "Connected. %d device(s) and %d screen(s), according to what is "
            "written on the remote right now -- read from its flash, not "
            "from a file."
            % (
                info.get("n_dispositivos_actual") or 0,
                info.get("n_pantallas_actual") or 0,
            )
        ),
        "identidad": identidad,
        "blob": str(target),
        "sha256": info.get("sha256"),
        "n_devices": info.get("n_dispositivos_actual"),
        "n_screens": info.get("n_pantallas_actual"),
        "parece_de_fabrica": info.get("parece_de_fabrica"),
        "reason": None,
        "last_local_config": None,
        "measured_at": measured_at,
    }


if __name__ == "__main__":
    # Console check: with NO remote connected (there is not one in this session,
    # see the brief's PROHIBIDO), it has to return a clean DESCONECTADO --
    # never a traceback, never "connected" by accident. It is exactly
    # the half of the three situations that CAN be verified without
    # hardware, and it is the one that before this change had no clean ending
    # (`read_raw_baseline` raised an uncaught exception).
    import tempfile

    with tempfile.TemporaryDirectory(prefix="estado_mando_selftest_") as tmp:
        r = refrescar(Path(tmp), timeout=20.0)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        assert r["state"] == DESCONECTADO, (
            "sin mando conectado, esperaba DESCONECTADO, dio %r" % r["state"]
        )
        assert r["blob"] is None
        print("\nOK: sin mando conectado -> estado=DESCONECTADO, blob=None.")
        print(
            "NOTE: CONECTADO_VERDAD and CONECTADO_SIN_VERDAD could not be "
            "exercised in this session -- there is no remote connected."
        )
