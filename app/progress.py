#!/usr/bin/env python3
"""Convierte el stdout de `config_work/write.py` en EVENTOS estructurados,
en vivo, linea por linea -- lo que le hace falta a una barra de progreso.

`write.py` ya imprime todo lo que hace falta (ver su `main()`): identidad,
si el archivo lo acepto libconcord, si "nada se movio" contra la
referencia, el arranque de la escritura, y durante la escritura misma un
callback de progreso que imprime `"   stage %d: %d%%"` cada vez que el
porcentaje redondeado a la decena cambia. Lo que falta es alguien leyendo
esas lineas A MEDIDA QUE SALEN (no al final, con `subprocess.run`, que es
lo que hace `Api.remote_record()` hoy) y traduciendolas a algo que una
barra pueda pintar.

Este archivo tiene DOS partes con garantias MUY distintas:

  1. `parse_line()` -- PURA. Un string entra, un dict (o None) sale. No
     toca el disco, la red ni el USB. Se puede probar entera con strings
     de muestra copiados literal de `write.py`, sin hardware -- y este
     archivo lo hace al final (`if __name__ == "__main__"`).

  2. `ejecutar_en_vivo()` -- la que de verdad correria `write.py` como
     subprocess con las lineas llegando en tiempo real (`Popen` +
     iterar sobre `proc.stdout` a medida que hay datos, en vez de esperar a
     que termine). **ESTO ESCRIBE FLASH SI SE LA LLAMA CON ARGV QUE
     ESCRIBE** -- ni una sola vez se llama a esta funcion en este modulo ni
     en esta sesion (PROHIBIDO del brief: nunca ejecutar write.py contra
     el aparato). Queda para que `Api.sync_apply_start()` la use el
     dia que haya un mando conectado y las DOS llaves esten puestas
     (`ack=="GRABAR"` -- la confirmacion explicita, verificada en Python --
     y la compuerta verde) -- exactamente las mismas que ya exige
     `Api.remote_record()` hoy. (`RE_HARMONY_SOLO_LECTURA=1` apaga el
     camino de escritura entero, pero no hace falta ninguna variable de
     entorno para PERMITIRLO.)
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

#: Seconds given to the reader thread to DRAIN whatever is left in the
#: pipe AFTER the process has already exited. Measured: with a normal
#: `write.py` normal el lector ya salio antes de que `proc.wait()`
#: returns, so this is never used in the happy case. It exists for the
#: pathological case -- a grandchild of the process that kept the write
#: end of the pipe open -- where `for line in stdout` NEVER sees EOF
#: ve EOF NUNCA aunque `write.py` ya no exista.
GRACIA_LECTOR = 3.0

# -- regexes construidos DIRECTO de los `print(...)` reales de write.py --
# (ver `config_work/write.py::main()`, lineas citadas en cada comentario).
RE_AVANCE = re.compile(r"^\s*stage (?P<etapa>\d+): (?P<pct>\d+)%$")
RE_IDENTIDAD = re.compile(
    r"^remote: arch (?P<arch>\d+), skin (?P<skin>\d+), "
    r"firmware (?P<fw_mayor>\d+)\.(?P<fw_menor>\d+)$"
)
RE_ARCHIVO_ACEPTADO = re.compile(r"^file accepted by libconcord, type (?P<tipo>\d+)$")
RE_NADA_SE_MOVIO = re.compile(
    r"^nothing moved relative to (?P<referencia>.+): (?P<ok>YES|NO) "
    r"\((?P<n>\d+) different bytes\)$"
)
RE_REPUNTES_DECLARADOS = re.compile(
    r"^declared repoints: (?P<repuntes>.*?)\s+bytes outside what was declared: "
    r"(?P<sin_declarar>.*)$"
)
RE_ESCRITURA_INICIADA = re.compile(
    r"^writing the configuration \(this takes a while\)\.\.\.$"
)
RE_RESULTADO = re.compile(r"^result: (?P<codigo>\d+)(?:\s{2}(?P<detalle>.*))?$")
RE_NO_ESCRIBE = re.compile(r"^NOT WRITING: (?P<motivo>.+)$")
RE_VERIFICAR_SOLO = re.compile(r"^--verificar-solo: nothing was written\.$")

#: Milestones with NO percentage of their own (they do not come from
#: libconcord's callback) that I assign a fixed number anyway, so the bar
#: has SOMETHING to show before the real write starts (which is, by
#: far, the longest stage -- measured indirectly: it walks the WHOLE
#: config area in pages, just like `flash_dump.c`).
_HITOS_FIJOS = {
    "identidad": 5,
    "file_accepted": 10,
    "gate": 15,
    "escritura_iniciada": 20,
}

# =====================================================================
# EL CAMINO DE LECTURA (Connect), simetrico al de arriba
# =====================================================================
#
# La lectura del flash son ~80 transacciones USB de 16 KiB y la pantalla se
# quedaba MUDA todo ese rato: `estado_mando.refrescar()` corria
# `read_flash_baseline.py` con `subprocess.run`, que no deja ver nada hasta
# que termina. Ahora ese script imprime su avance MEDIDO (ver
# `config_work/read_flash_baseline.py`: `PREFIJO_ETAPA` / `PREFIJO_LEIDO`) y
# esto lo traduce a eventos, con la misma forma que ya tenia el grabado --
# no una segunda invencion.
#
# La diferencia que importa: aca el porcentaje sale de BYTES QUE LLEGARON,
# no de un reloj. `bytes_leidos/bytes_totales` viaja crudo en el evento
# para que la pantalla pueda poner el numero al lado de la barra; si la
# barra se moviera sola sin esos numeros seria una animacion, que es
# justamente lo que no se quiere.
#
# GRUPOS POR POSICION, NO POR NOMBRE, y no es cuestion de gusto. El export
# publico renombra identificadores al ingles, y `text` es una CLAVE del
# mapa (`text` -> `text`). Renombra los usos (`m["text"]` pasa a
# `m["text"]`) pero NO puede tocar el `(?P<text>...)` de adentro de un
# string crudo: son bytes de una expresion regular, no un identificador.
# Resultado medido sobre el arbol exportado: `IndexError: no such group` en
# CADA linea ETAPA -- y como el hilo lector de `ejecutar_en_vivo` se traga
# la excepcion, se perdian TODOS los eventos y la barra quedaba muerta en el
# repo publico mientras andaba perfecto aca. Un grupo posicional no tiene
# nombre que renombrar.
RE_LECTURA_ETAPA = re.compile(r"^ETAPA ([a-z_]+): (.*)$")
RE_LECTURA_LEIDO = re.compile(r"^LEIDO (\d+)/(\d+)$")

#: Hitos de la LECTURA. `read` arranca en 8 y los bytes se reparten
#: 10..95, asi que la parte larga (las ~80 transacciones) es la que ocupa
#: casi toda la barra -- que es lo honesto: es donde se va el tiempo.
_HITOS_LECTURA = {
    "buscar": 2,
    "identidad": 5,
    "read": 8,
    "validar": 96,
}
_LECTURA_DESDE = 10
_LECTURA_HASTA = 95


def parse_line(line: str) -> dict | None:
    """UNA linea de stdout de `write.py` -> un evento, o `None` si la
    linea no matchea ningun formato conocido (se reporta igual, como
    `{'kind': 'otro', 'text': line}`, para no perder informacion -- una
    barra de progreso puede ignorarla, pero un log no deberia).

    Nunca lanza: una linea inesperada (una version distinta de libconcord,
    un warning nuevo) cae en `'otro'`, no rompe el que esta iterando.
    """
    if line is None:
        return None
    text = line.rstrip("\n")
    cruda = text.strip()
    if not cruda:
        return None

    m = RE_AVANCE.match(text)
    if m:
        return {
            "kind": "avance",
            "etapa_libconcord": int(m["etapa"]),
            "porcentaje": int(m["pct"]),
            "text": cruda,
        }

    m = RE_IDENTIDAD.match(cruda)
    if m:
        return {
            "kind": "identidad",
            "arch": int(m["arch"]),
            "skin": int(m["skin"]),
            "firmware": "%s.%s" % (m["fw_mayor"], m["fw_menor"]),
            "porcentaje": _HITOS_FIJOS["identidad"],
            "text": cruda,
        }

    m = RE_ARCHIVO_ACEPTADO.match(cruda)
    if m:
        return {
            "kind": "file_accepted",
            "porcentaje": _HITOS_FIJOS["file_accepted"],
            "text": cruda,
        }

    m = RE_NADA_SE_MOVIO.match(cruda)
    if m:
        return {
            "kind": "gate",
            "passed": m["ok"] == "YES",
            "diferencias": int(m["n"]),
            "porcentaje": _HITOS_FIJOS["gate"],
            "text": cruda,
        }

    m = RE_REPUNTES_DECLARADOS.match(cruda)
    if m:
        sin_declarar = m["sin_declarar"].strip()
        return {
            "kind": "compuerta_repuntes",
            "undeclared_empty": sin_declarar == "none" or sin_declarar == "[]",
            "text": cruda,
        }

    if RE_ESCRITURA_INICIADA.match(cruda):
        return {
            "kind": "escritura_iniciada",
            "porcentaje": _HITOS_FIJOS["escritura_iniciada"],
            "text": cruda,
        }

    m = RE_RESULTADO.match(cruda)
    if m:
        codigo = int(m["codigo"])
        return {
            "kind": "resultado",
            "codigo": codigo,
            "ok": codigo == 0,
            "detail": (m["detail"] or "").strip() or None,
            "porcentaje": 100,
            "text": cruda,
        }

    m = RE_NO_ESCRIBE.match(cruda)
    if m:
        return {"kind": "rechazado", "reason": m["reason"], "text": cruda}

    if RE_VERIFICAR_SOLO.match(cruda):
        return {"kind": "verificar_solo", "porcentaje": 100, "text": cruda}

    return {"kind": "otro", "text": cruda}


def porcentaje_global(eventos: Iterable[dict]) -> int:
    """The LAST percentage seen among the events, or 0 if none carried
    one. The `'avance'` events (the ones from libconcord's real callback,
    inside the write) already arrive rescaled to [20, 99] -- the rest of
    the bar (0-20 and the final 100) are the fixed milestones above.
    Rescaling `'avance'` is THE RESPONSIBILITY OF THIS CALCULATION, not of
    `parse_line()` (which leaves the raw `stage N: pct%`, inventing
    nothing, for whoever wants libconcord's real number).
    """
    pct = 0
    for ev in eventos:
        if ev is None:
            continue
        if ev.get("kind") == "avance":
            crudo = ev.get("porcentaje") or 0
            pct = max(pct, 20 + round(crudo * 0.79))  # 20..99
        elif ev.get("porcentaje") is not None:
            pct = max(pct, ev["porcentaje"])
    return min(pct, 100)


def etapa_actual(eventos: Iterable[dict]) -> str:
    """The text of the LAST useful line -- what a bar has to put next
    to the percentage for it to mean anything. This used to be computed by
    the JS reading `e.line`, a key no event has (events carry `text`):
    the result was that the bar said "starting..." right to the end. Now
    Python computes it and it travels in the snapshot, so there are no two
    implementations that can drift apart.
    """
    etapa = ""
    for ev in eventos:
        if not ev:
            continue
        text = (ev.get("text") or "").strip()
        if text:
            etapa = text
    return etapa


def parsear_linea_lectura(line: str) -> dict | None:
    """UNA linea de stdout de `read_flash_baseline.py` -> un evento, o
    `None` si estaba vacia. Simetrica de `parse_line()`, y con las
    mismas dos garantias: es PURA (string entra, dict sale) y NUNCA lanza.

    Los dos formatos que ese script emite a proposito para esto:

        ETAPA <slug>: <texto para una persona>   -> {'tipo': 'etapa', ...}
        LEIDO <n>/<total>                        -> {'tipo': 'leido', ...}

    Todo lo demas (el DEBUG que libconcord tira por su cuenta, el volcado
    final `%-24s %s`, la linea JSON) cae en `'otro'`: se conserva para el
    log, pero no mueve la barra. Que la barra la muevan SOLO las lineas
    medidas es el requisito: nada de animacion que avanza sola.

    "NUNCA lanza" es literal y esta ATRAPADO, no confiado: una linea rara
    tiene que caer en `'otro'`, no matar la barra. Ver el comentario de los
    grupos posicionales arriba -- ya hubo una version en la que esto SI
    lanzaba, y como el hilo lector se traga la excepcion, la barra quedaba
    muerta sin un solo mensaje de error.
    """
    if line is None:
        return None
    text = line.rstrip("\n")
    cruda = text.strip()
    if not cruda:
        return None

    try:
        m = RE_LECTURA_LEIDO.match(cruda)
        if m:
            leidos = int(m.group(1))
            totales = int(m.group(2))
            return {
                "kind": "leido",
                "bytes_leidos": leidos,
                "bytes_totales": totales,
                "porcentaje": _pct_lectura(leidos, totales),
                "text": "read %s of %s bytes from the remote" % (leidos, totales),
            }

        m = RE_LECTURA_ETAPA.match(cruda)
        if m:
            slug = m.group(1)
            ev = {
                "kind": "etapa",
                "slug": slug,
                "text": m.group(2).strip() or slug,
            }
            if slug in _HITOS_LECTURA:
                ev["porcentaje"] = _HITOS_LECTURA[slug]
            return ev
    except Exception:  # noqa: BLE001
        pass  # una linea que no se pudo interpretar es 'otro', no un cuelgue

    return {"kind": "otro", "text": cruda}


def _pct_lectura(leidos: int, totales: int) -> int:
    """Bytes -> porcentaje, en la franja [_LECTURA_DESDE, _LECTURA_HASTA].

    `totales <= 0` (un mando que reportara 0 en `get_config_bytes_total()`)
    devuelve el piso, no una division por cero ni un 100 mentiroso.
    """
    if totales <= 0:
        return _LECTURA_DESDE
    frac = max(0.0, min(1.0, leidos / float(totales)))
    return _LECTURA_DESDE + int(round(frac * (_LECTURA_HASTA - _LECTURA_DESDE)))


def bytes_de(eventos: Iterable[dict]) -> tuple[int, int]:
    """`(leidos, totales)` del ULTIMO evento `'leido'`, o `(0, 0)`.

    Existe para que la pantalla pueda escribir los numeros REALES al lado
    de la barra. Sin esto la barra seria indistinguible de una animacion,
    que es exactamente lo que el pedido descarta.
    """
    leidos = totales = 0
    for ev in eventos:
        if ev and ev.get("kind") == "leido":
            leidos = ev.get("bytes_leidos") or 0
            totales = ev.get("bytes_totales") or 0
    return leidos, totales


class TrabajoGrabado:
    """Estado en vivo de UNA corrida de `write.py`, para que algo (un
    metodo de `Api`, un hilo) lo llene y otra cosa (el polling de la UI) lo
    lea sin pisarse -- protegido por un lock, porque `ejecutar_en_vivo`
    corre en un hilo aparte del que atiende el polling.
    """

    def __init__(self, id_: str | None = None) -> None:
        self.id = id_ or uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self.eventos: list[dict] = []
        self.terminado = False
        self.ok: bool | None = None
        self.returncode: int | None = None
        self.error: str | None = None
        self.iniciado_en = time.time()
        self.terminado_en: float | None = None
        #: History row this run left recorded. It is filled in
        #: DESPUES de que `write.py` termina (quien corre el trabajo la
        #: it), and it travels in the `snapshot()` because the UI needs it for
        #: the start-up question (RULE 2): without this id there would be nowhere
        #: to store the answer to "did it start up OK?".
        self.write_id: int | None = None
        #: Why it could NOT be recorded in the history, if it could not. This
        #: used to be swallowed by an `except: pass` and the user was left
        #: with a write done and zero trace, with no idea why.
        self.write_entry_error: str | None = None

    def add(self, evento: dict) -> None:
        with self._lock:
            self.eventos.append(evento)

    def marcar_fin(
        self, ok: bool, returncode: int | None, error: str | None = None
    ) -> None:
        with self._lock:
            self.terminado = True
            self.ok = ok
            self.returncode = returncode
            self.error = error
            self.terminado_en = time.time()

    def snapshot(self, start: int = 0) -> dict:
        """The new events since index `start` (for incremental polling:
        the caller keeps how many events it has already seen and only asks
        for the missing ones), plus the global percentage and whether it
        has finished.

        THE `ok` AND `error` KEYS ARE NOT HERE, AND THEY CANNOT COME BACK.
        This dict is sent to the UI with `_ok(**snapshot)`, and `_ok()`
        builds the envelope `{"ok": True, ...}` and then does `update(kw)`:
        an `ok` in here OVERWRITES the envelope's one. Since `self.ok` is
        `None` while the write runs, the UI got `ok: null` on the first
        poll, read it as "the call failed", cut the polling short and
        stayed stuck on "Writing to your remote" forever -- with the write
        running and then finished, without anyone finding out. THAT was
        BUG 1. The result of the WRITE travels as `grabado_ok` /
        `error_grabado`; `ok` / `error` belong to the ENVELOPE, to the call.
        """
        with self._lock:
            nuevos = list(self.eventos[start:])
            total = len(self.eventos)
            porcentaje = porcentaje_global(self.eventos)
            return {
                "id": self.id,
                "eventos_nuevos": nuevos,
                "total_events": total,
                "porcentaje": porcentaje,
                "etapa": etapa_actual(self.eventos),
                "terminado": self.terminado,
                "grabado_ok": self.ok,
                "returncode": self.returncode,
                "error_grabado": self.error,
                "write_id": self.write_id,
                "write_entry_error": self.write_entry_error,
                "segundos_transcurridos": round(
                    (self.terminado_en or time.time()) - self.iniciado_en, 1
                ),
            }


class TrabajoLectura(TrabajoGrabado):
    """Estado en vivo de UNA lectura del flash (el boton **Connect**).

    Hereda de `TrabajoGrabado` a proposito: el mecanismo -- lock, lista de
    eventos, snapshot incremental por indice -- ya estaba resuelto y
    probado ahi, y tener dos copias es como se desincronizan. Lo unico que
    cambia es QUE se publica en el snapshot:

      * `bytes_leidos` / `bytes_totales`: los numeros MEDIDOS, para que la
        pantalla pueda ponerlos al lado de la barra. Sin ellos la barra es
        una animacion.
      * `etapa`: el texto de la ultima linea `ETAPA ...`, no el de
        cualquier linea (que en una lectura seria siempre "read N of M").
      * `state`: el dict de `estado_mando.refrescar()` cuando termino --
        LA respuesta. Es lo que la pantalla pinta al final, sin tener que
        pedirlo por separado (y sin volver a leer el mando para pedirlo).
      * `lectura_ok` / `error_lectura`: gemelos de `grabado_ok` /
        `error_grabado`.

    LAS CLAVES `ok` Y `error` SIGUEN PROHIBIDAS, por la misma razon que en
    el padre: este dict cruza a JS por `_ok(**snapshot)` y un `ok` adentro
    PISA el sobre de la llamada. Fue el BUG 1 del grabado y no se va a
    repetir en la lectura.
    """

    def __init__(self, id_: str | None = None) -> None:
        super().__init__(id_)
        #: La respuesta de `estado_mando.refrescar()`. La pone el hilo que
        #: corre la lectura, ANTES de `marcar_fin()`, por la misma razon de
        #: orden que el grabado: la UI cambia de pantalla en cuanto ve
        #: `terminado`, y sin esto no tendria que pintar.
        self.state: dict | None = None

    def snapshot(self, start: int = 0) -> dict:
        with self._lock:
            nuevos = list(self.eventos[start:])
            total = len(self.eventos)
            porcentaje = porcentaje_global(self.eventos)
            leidos, totales = bytes_de(self.eventos)
            etapa = ""
            for ev in self.eventos:
                if ev and ev.get("kind") == "etapa":
                    etapa = (ev.get("text") or "").strip() or etapa
            # Terminada la lectura la barra va a 100 SOLO si termino: un
            # 100% con el proceso todavia corriendo es la mentira clasica.
            if self.terminado:
                porcentaje = 100
            return {
                "id": self.id,
                "eventos_nuevos": nuevos,
                "total_events": total,
                "porcentaje": porcentaje,
                "etapa": etapa,
                "bytes_leidos": leidos,
                "bytes_totales": totales,
                "terminado": self.terminado,
                "lectura_ok": self.ok,
                "error_lectura": self.error,
                "state": self.state,
                "segundos_transcurridos": round(
                    (self.terminado_en or time.time()) - self.iniciado_en, 1
                ),
            }


def ejecutar_en_vivo(
    argv: list[str],
    cwd: Path,
    on_evento: Callable[[dict], None],
    timeout: float = 1800.0,
    parser: Callable[[str], dict | None] = parse_line,
) -> dict:
    """Corre `argv` con `Popen`, leyendo stdout LINEA A LINEA a medida que
    llega (no al final) y llamando `on_evento(parser(line))` por cada una
    que no sea vacia.

    `parser` decide QUE proceso se esta siguiendo, y es lo unico que
    distingue los dos usos:

      * `parse_line` (default) -- `write.py`, el camino de ESCRITURA,
        construido por `aparato.build_record_line()`.
      * `parsear_linea_lectura` -- `config_work/read_flash_baseline.py`,
        el camino de LECTURA (el boton Connect). SOLO LEE.

    Esta funcion no sabe ni le importa cual es: **lo que decide si se
    escribe flash o no es `argv`, no esta funcion.** El manejo de proceso
    (hilo lector, timeout duro sobre `wait()`, matar el grupo) es el mismo
    para los dos porque los cuelgues que arregla son los mismos, y tener
    dos copias endurecidas por separado es como una se queda atras.

    **ESTO ESCRIBE FLASH SI `argv` ES EL DE `write.py` SIN
    `--verificar-solo`.** Con el argv de `read_flash_baseline.py` no hay
    una sola primitiva de escritura en el camino (ese script solo llama
    `get_identity` y `read_flash_at`). Sus dos consumidores son
    `Api.sync_apply_start()` (escribe, detras de las dos llaves) y
    `estado_mando.refrescar()` (lee, sin ninguna).

    Devuelve `{'ok', 'returncode', 'transcript', 'eventos', 'expirado',
    'error'}` al terminar (ademas de haber ido llamando `on_evento` en
    vivo). **NUNCA lanza por culpa del proceso**: un timeout o una muerte
    fea vuelven como `ok=False` + `error` con texto, porque quien llama
    tiene que poder cambiar la pantalla SIEMPRE.

    POR QUE ESTA ESCRITA ASI (medido, no razonado -- ver el falso
    `write.py` de `/tmp/fakegrab/`):

      La version anterior leia con `for line in proc.stdout:` en el hilo
      que llamaba, y recien despues hacia `proc.wait()`. O sea que la
      senial de "termino" era **el EOF de la tuberia**, no la muerte del
      proceso. Son cosas distintas: si `write.py` termina pero deja un
      nieto vivo con el extremo de escritura abierto, EOF no llega NUNCA
      y ese `for` se cuelga para siempre -- con el proceso ya muerto, la
      app al 0% de CPU y la pantalla clavada en "Writing to your remote".
      Reproducido exacto con `grabar_zombi.py`.

      Y el timeout tampoco salvaba: solo se miraba DENTRO del `for`, o
      sea solo cuando llegaba una linea. Un `write.py` que se cuelga
      callado no imprime nada, no entra al cuerpo del `for`, y el
      timeout no se evalua jamas. Reproducido con `grabar_colgado.py`.

    Ahora: el stdout lo lee un HILO APARTE, y esta funcion espera
    `proc.wait(timeout=...)`, que es la senial dura. Cuando el proceso
    muere se le dan `GRACIA_LECTOR` segundos al lector para vaciar lo que
    quede y, si sigue trabado (nieto con la tuberia abierta), se mata el
    GRUPO de procesos entero -- por eso `start_new_session=True`: pone a
    `write.py` y a toda su descendencia en un grupo propio, para poder
    matarlo sin tocar ni a la app ni a la terminal.
    """
    kwargs = {}
    if os.name == "posix":
        # Its own process group: makes the descendants killable without
        # any risk of suicide (the app is in ANOTHER group).
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **kwargs,
    )

    transcript: list[str] = []
    eventos: list[dict] = []
    lock = threading.Lock()

    def _read() -> None:
        # An `on_evento` that blows up must NOT kill the reading nor leave
        # the waiter hanging: the exception is swallowed per event.
        #
        # Y EL `parser` TAMPOCO, por linea. Antes estaba solo el `try` de
        # afuera, o sea que un parser que lanzaba en la PRIMERA linea
        # cortaba el `for` entero: cero eventos, barra muerta, y ni un
        # mensaje -- el proceso terminaba bien y el transcript quedaba
        # completo, asi que no habia por donde agarrarlo. Paso de verdad en
        # el arbol exportado (un grupo de regex renombrado). El transcript
        # se guarda ANTES de parsear justo por esto: pase lo que pase con
        # el parser, la salida cruda no se pierde.
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                with lock:
                    transcript.append(line)
                try:
                    ev = parser(line)
                except Exception:  # noqa: BLE001
                    continue
                if ev is None:
                    continue
                with lock:
                    eventos.append(ev)
                try:
                    on_evento(ev)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    lector = threading.Thread(target=_read, daemon=True, name="grabar-stdout")
    lector.start()

    inicio = time.time()
    expirado = False
    error: str | None = None
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        expirado = True
        error = (
            "write.py did not finish in %d s and was killed. The remote may "
            "have been left half-written: DO NOT unplug it, check the History "
            "tab." % int(timeout)
        )
        _matar_grupo(proc)

    # The process is gone. The reader gets a short window to drain the
    # pipe -- and not a second more: if a grandchild has it open,
    # waiting on it is exactly the hang being fixed.
    lector.join(timeout=GRACIA_LECTOR)
    if lector.is_alive():
        # Alguien que no es `write.py` sigue con la tuberia abierta.
        # Matar el grupo entero le manda EOF al lector.
        _matar_grupo(proc)
        lector.join(timeout=GRACIA_LECTOR)
    lector_huerfano = lector.is_alive()

    returncode = proc.returncode
    if returncode is None:
        returncode = -1
    if error is None and returncode != 0:
        error = "write.py finished with code %s" % returncode

    with lock:
        salida = "".join(transcript)
        eventos_finales = list(eventos)

    return {
        "ok": (not expirado) and returncode == 0,
        "returncode": returncode,
        "transcript": salida,
        "eventos": eventos_finales,
        "expirado": expirado,
        "error": error,
        "lector_huerfano": lector_huerfano,
        "segundos": round(time.time() - inicio, 1),
    }


def _matar_grupo(proc: subprocess.Popen) -> None:
    """Kills `proc` and all its descendants, without being able to kill the app.

    With `start_new_session=True` the child is the leader of its own group,
    i.e. `pgid == proc.pid`. It is checked anyway that this group is NOT
    the app's before firing: a `killpg` on your own group takes the whole
    window down with it.
    """
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass
    if os.name != "posix":
        return
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:  # noqa: BLE001
        pgid = proc.pid
    try:
        if pgid and pgid != os.getpgrp() and pgid != os.getpid():
            os.killpg(pgid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    # Console check: PURE, zero subprocess, zero USB. Sample lines
    # muestra copiadas LITERAL de los `print(...)` de `config_work/write.py`
    # (cited above in each regex) plus one line from the real callback as
    # `avance()` emits it right there.
    muestras = [
        "remote: arch 12, skin 4, firmware 2.19",
        "file accepted by libconcord, type 3",
        "nothing moved relative to /tmp/ref.bin: YES (0 different bytes)",
        "declared repoints: 0x00000020, 0x00000024   bytes outside what was declared: none",
        "",
        "writing the configuration (this takes a while)...",
        "   stage 0: 0%",
        "   stage 0: 30%",
        "   stage 0: 70%",
        "   stage 1: 100%",
        "result: 0",
        "something this version of libconcord did not say before",
    ]
    fallas = []
    eventos = []
    for m in muestras:
        ev = parse_line(m)
        eventos.append(ev)
        print("%-70r -> %s" % (m, ev))

    tipos = [e["kind"] for e in eventos if e]
    for esperado in (
        "identidad",
        "file_accepted",
        "gate",
        "compuerta_repuntes",
        "escritura_iniciada",
        "avance",
        "resultado",
        "otro",
    ):
        if esperado not in tipos:
            fallas.append("expected at least one event tipo=%r, none showed up" % esperado)

    pct = porcentaje_global([e for e in eventos if e])
    print("\nporcentaje_global(muestras completas) = %d" % pct)
    if pct != 100:
        fallas.append(
            "with a 'result: 0' at the end, the global percentage had to be 100"
        )

    # NEGATIVE: an empty or whitespace-only line is not an event (there is
    # nothing to paint for it, and counting it inflates `total_events` in vain).
    if parse_line("   ") is not None:
        fallas.append("NEGATIVE FAILED: a blank line should not produce an event")
    if parse_line("") is not None:
        fallas.append("NEGATIVE FAILED: an empty line should not produce an event")

    # TrabajoGrabado: snapshot incremental.
    t = TrabajoGrabado()
    for e in eventos:
        if e:
            t.add(e)
    s1 = t.snapshot(start=0)
    if s1["total_events"] != len(s1["eventos_nuevos"]):
        fallas.append("snapshot(desde=0) had to bring every event")
    t.marcar_fin(ok=True, returncode=0)
    s2 = t.snapshot(start=s1["total_events"])
    if s2["eventos_nuevos"]:
        fallas.append("incremental snapshot brought events that had already been seen")
    if not s2["terminado"] or s2["grabado_ok"] is not True:
        fallas.append(
            "snapshot after marcar_fin(ok=True) had to say terminado+ok"
        )
    # BUG 1, nailed down as a permanent control: if the snapshot brings
    # back `ok` (or `error`), `_ok(**snapshot)` uses it to OVERWRITE the
    # call envelope and the UI believes the bridge went down.
    for prohibida in ("ok", "error"):
        if prohibida in s2:
            fallas.append(
                "snapshot() brought key %r: it overwrites _ok()'s envelope and leaves "
                "the screen stuck on 'Writing to your remote'" % prohibida
            )
    for obligatoria in ("grabado_ok", "error_grabado", "etapa", "write_entry_error"):
        if obligatoria not in s2:
            fallas.append(
                "snapshot() did not bring %r, read by the UI by exact name" % obligatoria
            )

    # ------------------------------------------------------------------
    # EL CAMINO DE LECTURA (Connect). Mismas dos garantias, mismos
    # negativos. Lineas copiadas LITERAL de lo que
    # `config_work/read_flash_baseline.py` imprime.
    # ------------------------------------------------------------------
    print()
    muestras_l = [
        "DEBUG (FindRemote): Testing: 046D, C121",
        "ETAPA buscar: looking for your remote over USB",
        "ETAPA identidad: found it -- arch 12, skin 4, firmware 2.19",
        "ETAPA leer: reading its memory",
        "LEIDO 0/3932160",
        "LEIDO 16384/3932160",
        "LEIDO 3932160/3932160",
        "ETAPA validar: checking what came back",
        "bytes_leidos             3932160",
        '{"encontrado": true, "valido": true}',
    ]
    eventos_l = []
    for m in muestras_l:
        ev = parsear_linea_lectura(m)
        eventos_l.append(ev)
        print("%-70r -> %s" % (m, ev))

    tipos_l = [e["kind"] for e in eventos_l if e]
    for esperado in ("etapa", "leido", "otro"):
        if esperado not in tipos_l:
            fallas.append("lectura: esperaba un evento tipo=%r, no aparecio" % esperado)

    # NEGATIVO QUE IMPORTA: NADA que no sea una linea medida puede mover la
    # barra. Si el DEBUG de libconcord, el volcado `%-24s %s` o la linea
    # JSON trajeran porcentaje, la barra avanzaria con basura -- que es
    # exactamente la "animacion que se mueve sola" que no se quiere.
    for linea in (
        "DEBUG (FindRemote): Testing: 046D, C121",
        "bytes_leidos             3932160",
        '{"encontrado": true, "valido": true}',
    ):
        ev = parsear_linea_lectura(linea)
        if ev.get("porcentaje") is not None:
            fallas.append(
                "NEGATIVO FALLIDO: %r movio la barra (porcentaje=%r) y no es "
                "una medicion" % (linea, ev["porcentaje"])
            )
    if (
        parsear_linea_lectura("   ") is not None
        or parsear_linea_lectura("") is not None
    ):
        fallas.append("NEGATIVE FAILED: an empty line should not produce an event")

    # EL CONTROL QUE ATRAPA EL BUG DEL EXPORT. Las dos regexes de lectura
    # tienen que usar grupos POSICIONALES: un `(?P<name>...)` puede quedar
    # apuntando a un nombre que el renombre al ingles ya movio del otro
    # lado, y el sintoma no es un error visible -- es una barra muerta.
    # Medido sobre el arbol exportado: `IndexError: no such group` en CADA
    # linea ETAPA, tragado por el hilo lector, cero eventos.
    for name, rx in (
        ("RE_LECTURA_ETAPA", RE_LECTURA_ETAPA),
        ("RE_LECTURA_LEIDO", RE_LECTURA_LEIDO),
    ):
        if rx.groupindex:
            fallas.append(
                "%s usa grupos CON NOMBRE (%s). El renombre del export toca "
                "los usos (m['x']) pero no el nombre adentro del string "
                "crudo: tienen que ser posicionales."
                % (name, ", ".join(rx.groupindex))
            )
    # Y que "NUNCA lanza" sea cierto con basura de verdad.
    for basura in ("ETAPA", "ETAPA :", "LEIDO /", "LEIDO 1/", "ETAPA x: ", "\x00\xff"):
        try:
            ev = parsear_linea_lectura(basura)
        except Exception as exc:  # noqa: BLE001
            fallas.append("parsear_linea_lectura(%r) lanzo %r" % (basura, exc))
            continue
        if ev is not None and ev.get("kind") not in ("etapa", "leido", "otro"):
            fallas.append(
                "parsear_linea_lectura(%r) dio un tipo raro: %r" % (basura, ev)
            )

    # Y el hilo lector no puede morirse por UNA linea: un parser que lanza
    # en la primera linea dejaba cero eventos y la barra muerta.
    def _parser_que_explota(line):
        if "ETAPA" in line:
            raise IndexError("no such group")
        return parsear_linea_lectura(line)

    import sys as _sys
    import tempfile as _tf

    with _tf.TemporaryDirectory(prefix="progreso_lector_") as _d:
        _guion = Path(_d) / "escupir.py"
        _guion.write_text(
            "print('ETAPA buscar: x', flush=True)\n"
            "print('LEIDO 10/20', flush=True)\n"
            "print('LEIDO 20/20', flush=True)\n"
        )
        _vistos: list[dict] = []
        _r = ejecutar_en_vivo(
            [_sys.executable, str(_guion)],
            Path(_d),
            _vistos.append,
            timeout=30.0,
            parser=_parser_que_explota,
        )
        if not _r["ok"]:
            fallas.append("el subprocess de control no termino bien: %r" % _r["error"])
        tipos_v = [e.get("kind") for e in _vistos]
        print(
            "\nlector con un parser que lanza en la 1ra linea -> eventos: %r" % tipos_v
        )
        if tipos_v.count("leido") != 2:
            fallas.append(
                "una linea que hizo lanzar al parser se llevo puestas las "
                "demas: llegaron %r, tenian que llegar los dos 'leido'" % tipos_v
            )
        if "ETAPA buscar: x" not in _r["transcript"]:
            fallas.append(
                "la linea que hizo lanzar al parser tampoco quedo en el "
                "transcript: se perdio la salida cruda"
            )

    # El porcentaje sale de BYTES, y esta acotado por los dos extremos.
    if parsear_linea_lectura("LEIDO 0/3932160")["porcentaje"] != _LECTURA_DESDE:
        fallas.append("lectura: 0 bytes tenia que dar el piso %d" % _LECTURA_DESDE)
    if parsear_linea_lectura("LEIDO 3932160/3932160")["porcentaje"] != _LECTURA_HASTA:
        fallas.append("lectura: todo leido tenia que dar el tope %d" % _LECTURA_HASTA)
    if parsear_linea_lectura("LEIDO 0/0")["porcentaje"] != _LECTURA_DESDE:
        fallas.append("lectura: totales=0 tenia que dar el piso, no romper")
    medio = parsear_linea_lectura("LEIDO 1966080/3932160")["porcentaje"]
    if not (_LECTURA_DESDE < medio < _LECTURA_HASTA):
        fallas.append(
            "lectura: la mitad de los bytes dio %r, fuera de la franja" % medio
        )

    if bytes_de([e for e in eventos_l if e]) != (3932160, 3932160):
        fallas.append("bytes_de() no devolvio los bytes del ULTIMO 'leido'")

    tl = TrabajoLectura()
    for e in eventos_l:
        if e:
            tl.add(e)
    sl = tl.snapshot(start=0)
    print(
        "\nTrabajoLectura.snapshot() sin terminar = %d%% (%s/%s B) etapa=%r"
        % (sl["porcentaje"], sl["bytes_leidos"], sl["bytes_totales"], sl["etapa"])
    )
    # EL BUG 1 otra vez, en el camino de lectura: `ok`/`error` dentro del
    # snapshot pisan el sobre de `_ok()` y la pantalla lee "el puente se
    # cayo" con la lectura andando.
    for prohibida in ("ok", "error"):
        if prohibida in sl:
            fallas.append(
                "TrabajoLectura.snapshot() trajo la clave %r: pisa el sobre de "
                "_ok() y deja la pantalla clavada en 'connecting'" % prohibida
            )
    for obligatoria in (
        "bytes_leidos",
        "bytes_totales",
        "etapa",
        "porcentaje",
        "terminado",
        "lectura_ok",
        "error_lectura",
        "state",
    ):
        if obligatoria not in sl:
            fallas.append(
                "TrabajoLectura.snapshot() no trajo %r, que la UI lee por "
                "nombre exacto" % obligatoria
            )
    # Un 100% con la lectura todavia corriendo es la mentira clasica.
    if sl["porcentaje"] >= 100:
        fallas.append(
            "snapshot sin terminar dio %d%%: no puede llegar a 100 antes de "
            "terminar" % sl["porcentaje"]
        )
    # La etapa es el texto de la ultima linea ETAPA, no el de cualquier linea
    # (si fuera cualquiera diria siempre "read N of M bytes").
    if sl["etapa"] != "checking what came back":
        fallas.append("la etapa tenia que ser la ultima ETAPA, dio %r" % sl["etapa"])
    tl.marcar_fin(ok=True, returncode=0)
    if tl.snapshot(start=0)["porcentaje"] != 100:
        fallas.append("terminada, la barra tenia que cerrar en 100")

    print()
    if fallas:
        print("SELFTEST: FAILED")
        for f in fallas:
            print("  - %s" % f)
        raise SystemExit(1)
    print("SELFTEST: PASSED (parseo puro, 0 subprocess, 0 USB).")
    print(
        "NOTA: ejecutar_en_vivo() NO se ejercito ACA -- con el argv de "
        "write.py correria un grabado de verdad, y esta prohibido. Con el "
        "argv de read_flash_baseline.py solo lee, y ese camino SI corre en "
        "la app cada vez que se toca Connect."
    )
