"use strict";

/* RE-HARMONY -- the UI talks ONLY to app/api.py.
 *
 * This UI does not add or remove any device on any Logitech account, and
 * shows nothing that depends on any device other than the Harmony One the
 * user has plugged in.
 *
 * Three rules that are not cosmetic and are written here:
 *   1. If the gate didn't pass, the record button IS NOT DRAWN. It doesn't
 *      stay in the DOM disabled: it doesn't exist, so no accidental click
 *      can fire it.
 *   2. After recording (via the app or by hand in the terminal), the user is
 *      asked whether the control booted up fine, and the answer is saved in
 *      the history.
 *   3. The loop-closure text comes from Python (`estado().textos`), not from
 *      the HTML: it can't be softened by touching only the template.
 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const api = () => window.pywebview.api;

let ESTADO = null;

/* ------------------------------------------------------------- helpers -- */

function esc(x) {
  return String(x === null || x === undefined ? "" : x).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function chip(texto, clase) {
  return '<span class="chip ' + (clase || "") + '">' + esc(texto) + "</span>";
}

function nota(clase, html) {
  return '<div class="nota ' + clase + '">' + html + "</div>";
}

/* FIRST USE. A fresh clone has nothing read off the remote yet, and the three
 * read-only screens used to answer that with the PATH of a file that does not
 * exist -- asking the user, in the app's own error message, to produce by hand
 * the very thing the app exists to produce.
 *
 * What is drawn instead is Python's text (`api.TEXTO_PRIMER_USO`, or the
 * measured reason when the remote answered badly, or the libconcord one) and,
 * when there IS something to try, the button that tries it. The button says
 * out loud what it does, because a stranger being asked to plug a device into
 * a program that reconfigures remotes deserves to know it is not going to be
 * written to. `puede_leer` comes from Python: the JS does not decide whether
 * reading is worth offering. */
function notaPrimerUso(r) {
  const boton = r.puede_leer
    ? '<p class="primer-uso-accion">' +
      '<button class="accion" data-leer-mando="1">Read my remote</button>' +
      '<span class="vacio"> reads only &mdash; nothing is written</span></p>'
    : "";
  return nota("aviso", esc(r.error || "") + boton);
}

/* ONE handler for every "Read my remote" button, wherever it got drawn. It
 * runs the same read Connect runs -- `control_conectar_iniciar()`, which is
 * `read_flash_baseline.py` and its two read-only primitives, with the
 * progress bar following it -- and that call repaints the three cards on its
 * own, so there is nothing else to chain. */
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest && ev.target.closest("[data-leer-mando]");
  if (!btn) return;
  ocupado(btn, true, "reading your remote...");
  try {
    await actualizarEstadoControl();
  } finally {
    ocupado(btn, false);
  }
});

function mostrar(sel, texto) {
  const el = $(sel);
  el.hidden = false;
  el.textContent = texto;
}

function ocupado(btn, si, textoTrabajando) {
  btn.disabled = si;
  if (si) {
    btn.dataset.texto = btn.textContent;
    btn.textContent = textoTrabajando || "working...";
  } else if (btn.dataset.texto) {
    btn.textContent = btn.dataset.texto;
  }
}

/** Every call to the api goes through here: if Python blows up, the UI says so.
 *
 * A REJECTED call is not the same thing as a call that came back saying no.
 * The bridge only rejects for two reasons, and both of them are the app
 * breaking, never a check refusing:
 *
 *   - Python raised (KeyError, TypeError...) and pywebview turned the
 *     traceback into a rejection (`webview/util.py:js_bridge_call`).
 *   - Python returned something `json.dumps` can't serialize -- the real
 *     window serializes with NO `default=`, so a stray `set` kills the whole
 *     call with "Object of type set is not JSON serializable".
 *
 * So `clase` is set from the REJECTION CHANNEL, not by reading the wording:
 * this file must never try to tell a crash from a refusal by pattern-matching
 * the message. Python names the class (`cambios.CLASE_*`) whenever it manages
 * to return at all; this is only the case where it didn't get to return.
 * `e.name` is the Python exception class and `e.stack` the Python traceback
 * (pywebview's `api.js:_checkValue` copies both onto the Error).
 */
async function llamar(metodo, ...args) {
  try {
    const r = await api()[metodo](...args);
    if (r) return r;
    return {
      ok: false,
      error: "empty response from " + metodo,
      category: "aplicacion",
      es_bug: true,
    };
  } catch (e) {
    return {
      ok: false,
      error: String(e && e.message ? e.message : e),
      category: "aplicacion",
      es_bug: true,
      technical_detail: {
        reason: "the call to " + metodo + "() never came back",
        stderr: (e && e.name ? e.name + ": " : "") + String(e && e.message ? e.message : e),
        traza: (e && e.stack) || "",
      },
    };
  }
}

/* ------------------------------------------------- the three failures --
 *
 * THE POINT OF THIS BLOCK. Until now every one of these painted the same
 * red "the check did not pass: nothing will be written", including the ones
 * where the app had simply crashed. Telling a user that verification
 * protected him when the app tripped over its own bug teaches him to
 * distrust the message -- and then to ignore it on the day it is real.
 *
 * WHICH ONE IT IS, IS DECIDED IN PYTHON (`cambios.CLASE_*`, forwarded by
 * `Api.sync_preparar()` in `clase`). This function only paints. There is no
 * sniffing of the error text here on purpose: that heuristic is exactly the
 * kind of key-shaped contract that has already broken three times.
 */
function claseDeFallo(r) {
  if (!r) return "aplicacion";
  if (r.category) return r.category;
  if (r.es_bug) return "aplicacion";
  // NO CLASS AT ALL. Python never labelled this one, so nobody can say a
  // check refused anything -- and the app must not award itself a protection
  // it cannot prove it ran. The honest default is "we broke", never
  // "verification saved you": over-claiming protection is the exact failure
  // this block was written to delete, and it is the one that costs trust.
  // Any Python path that reaches here and is NOT a bug has to send `clase`.
  return "aplicacion";
}

/** The technical text that goes BEHIND "See more" -- never on the surface. */
function detalleTecnicoHTML(r) {
  const d = (r && r.technical_detail) || {};
  const partes = [];
  if (d.label) partes.push("change: " + d.label);
  if (d.kind) partes.push("type: " + d.kind);
  if (d.reason) partes.push("reason: " + d.reason);
  if (d.command) partes.push("command: " + d.command);
  const bloques = [];
  if (partes.length) bloques.push(partes.join("\n"));
  if (d.stderr) bloques.push("--- stderr ---\n" + d.stderr);
  if (d.stdout) bloques.push("--- stdout ---\n" + d.stdout);
  if (d.traza) bloques.push("--- python traceback ---\n" + d.traza);
  if (!bloques.length && r && r.error) bloques.push(String(r.error));
  if (!bloques.length) return "";
  return "<pre>" + esc(bloques.join("\n\n")) + "</pre>";
}

/** ONE note for a failed call, painted according to the class Python gave it.
 * `caso` says WHERE the note is going, which changes two things: the closing
 * instruction (what the user was trying to do) and whether the headline is
 * printed at all.
 *
 * `caso === "sync"`: the Sync modal ALREADY paints the class sentence in its
 * status line -- `r.linea`, resolved in Python by `LINEA_POR_CLASE`, the same
 * string this note would open with. Printing it again one line below is the
 * "never say the same thing twice" rule broken in the exact spot where the
 * user is most alarmed and least able to skim: the screen read
 * "This is a bug in the app, not a problem with your remote." twice in a row.
 * There the note carries only what the line above does NOT say.
 * `caso === "trailer"` (the Control tab, queueing): there is no status line
 * above, so the headline is the only place the class is stated and it stays. */
function notaFallo(r, caso) {
  const clase = claseDeFallo(r);
  const tec = detalleTecnicoHTML(r);
  const verMas = tec
    ? '<details class="ver-mas"><summary>See more</summary>' + tec + "</details>"
    : "";
  // The headline is dropped ONLY where something else already says it. Never
  // guessed from the text: `caso` is passed by the caller that knows.
  const titulo = (t) => (caso === "sync" ? "" : "<b>" + esc(t) + "</b><br>");

  if (clase === "aplicacion") {
    // NOT protection. Say so plainly, and do not put a traceback in a
    // stranger's face: it goes behind "See more".
    //
    // TWO texts, one per moment, both from Python. Queueing used to borrow
    // Sync's: it told the user the app broke "while preparing the changes"
    // and closed with "your changes are still on the Sync list" -- when the
    // change had just been REFUSED and was not on any list. A reassurance
    // that is false is worse than none: it is the same disease as calling a
    // crash "verification protecting you".
    const enCola = caso === "trailer";
    return (
      nota(
        "rota",
        titulo(
          T("kind_app", "This is a bug in the app, not a problem with your remote.")
        ) +
          esc(
            enCola
              ? T(
                  "kind_app_detail_queue",
                  "The app hit an error of its own while adding this change to " +
                    "the Sync list. Nothing was queued and your remote was " +
                    "never touched. The technical detail is under 'See more'."
                )
              : T(
                  "kind_app_detail",
                  "The app hit an error of its own while preparing the changes. " +
                    "Nothing was written and your remote was never touched. The " +
                    "technical detail is under 'See more'."
                )
          ) +
          "<br><span class='vacio'>" +
          (enCola
            ? "Nothing else on the Sync list was touched: what was already " +
              "queued is still there, exactly as it was."
            : "Your changes are still on the Sync list: nothing was lost and " +
              "nothing was half-done.") +
          "</span>" +
          verMas
      )
    );
  }

  if (clase === "gate") {
    // This one IS the protection working, and it keeps the words it had.
    return nota(
      "alarma",
      titulo(T("kind_gate", "The check did not pass: nothing will be written.")) +
        "This is verification blocking something that doesn't add up -- " +
        "it's exactly what's supposed to happen instead of risking the " +
        "control." +
        verMas
    );
  }

  // "herramienta": a tool stopped on a check of its OWN. Real reason, plain
  // words, and what to do about it -- not a traceback and not a gate that
  // never ran.
  const motivo =
    (r && r.technical_detail && r.technical_detail.reason) || (r && r.error) || "";
  return nota(
    "aviso",
    titulo(
      T("kind_tool", "This change can't be applied: nothing will be written.")
    ) +
      (motivo ? esc(motivo) + "<br>" : "") +
      esc(
        caso === "trailer"
          ? "Nothing was queued and your remote was not touched. Fix what it " +
              "says above and add it again."
          : "Nothing was written and nothing was left half-done. Take that " +
              "change off the list (or fix what it says above) and run Sync again."
      ) +
      verMas
  );
}

/* ------------------------------------------------------------ arranque -- */

function esperarPywebview() {
  return new Promise((res) => {
    if (window.pywebview && window.pywebview.api) return res();
    window.addEventListener("pywebviewready", () => res(), { once: true });
  });
}

async function main() {
  await esperarPywebview();

  ESTADO = await llamar("status");
  if (!ESTADO.ok) {
    $("#pie-estado").textContent = "api is broken: " + ESTADO.error;
    return;
  }

  // The loop-closure text no longer lives in a fixed card: it's shown at
  // the moment (`responderArranqueControl`), after confirming the control
  // booted up fine -- exactly as it comes from Python, untouched.
  // The env-var lock is gone: writing from the app is the default, and what
  // protects the control is the gate plus the red confirmation on screen.
  // The footer only calls out the exception (read-only mode).
  $("#pie-estado").innerHTML =
    "Python " + esc(ESTADO.python) + " &middot; " + esc(ESTADO.plataforma) + "<br>" +
    (grabadoPermitido()
      ? "writing enabled (Sync asks before it writes)"
      : '<span style="color:var(--alarma)">READ ONLY</span>');

  pintarEntorno();
  pintarDetalleTecnico(null);

  // Buttons are only enabled now: before `pywebviewready` a click would be a
  // silent no-op.
  $$("button.accion").forEach((b) => (b.disabled = false));

  cargarCuenta();
  cargarLocales();
  // Stranded downloads have to be visible on the FIRST paint, not only after
  // a save fails: today there are five of them on disk from before this card
  // existed, one of them from the user's own attempt.
  cargarPendientes();
  cargarHistorial();
  cargarAncla();
  refrescarSync();
  // Asking the control what it really has is the FIRST thing, and the rest
  // of the screens wait for it: `_control_blob()` in Python prefers the
  // freshly-read flash over any cached file, so painting Activities and
  // Keys before this answers would paint them from a file and then not
  // repaint. `actualizarEstadoControl()` calls them itself when it's done.
  await actualizarEstadoControl();
}

/* ----------------------------------------------------------------- nav -- */

$$("button.nav").forEach((b) => {
  b.addEventListener("click", () => {
    $$("button.nav").forEach((o) => o.removeAttribute("aria-current"));
    b.setAttribute("aria-current", "page");
    $$("main section").forEach((s) => s.classList.remove("viva"));
    $("#" + b.dataset.p).classList.add("viva");
  });
});

/* ============================== SYNC ====================================== *
 *
 * The one button that writes. Every screen (Control, Activities, Keys) now
 * ADDS to a list instead of writing on its own; Sync shows what's in the
 * list, prepares it all as ONE file, runs the gate ONCE, writes ONCE, and
 * asks ONCE whether the control booted.
 *
 * Why one write and not one per change: `cambios.aplicar_todos()` in Python
 * chains blob -> blob (each step starts from the blob the previous step
 * produced) and takes the union of the repoints, so N changes come out as a
 * single file. Writing them one at a time would be worse, not just slower:
 * each change is generated against "the current reference", so two files
 * built from the same starting blob would each be missing the other's
 * change, and the second write would silently undo the first.
 *
 * The four hard rules are NOT diluted by batching:
 *   1. THE GATE RULES. `sync_preparar()` runs the gate in Python over the
 *      combined file. If it doesn't come back green, the write button is
 *      never created -- not disabled, not drawn at all.
 *   2. AFTER WRITING, THE BOOT QUESTION. One write means one question, and
 *      it is the same `preguntarArranqueControl` the single-change paths
 *      use -- not a copy that could drift.
 *   3. The size-check wording still comes from Python (ESTADO.textos).
 *   4. What gets lost on a delete is still spelled out where the delete is
 *      added to the list, and repeated in this summary.
 */

let SYNC_ITEMS = [];       // mirror of cambios_listar(); Python holds the truth
let SYNC_ABIERTO = false;  // the modal is open
let SYNC_RECHAZO = null;   // RULE 1: {firma, html} -- a rejected batch stays rejected
let SYNC_CORRIENDO = false; // a write is in flight: don't let the modal close

function syncFirma() {
  return SYNC_ITEMS.map((c) => c.id).join("|");
}

/** Re-reads the pending list FROM PYTHON and repaints the bar. Never keeps
 * its own copy as the source of truth: the list lives in `Api._cambios`,
 * which is also what `sync_preparar()` reads -- so what the bar counts and
 * what would get written can't drift apart. */
async function refrescarSync() {
  const r = await llamar("changes_list");
  SYNC_ITEMS = r.ok ? r.items || [] : [];
  const n = SYNC_ITEMS.length;
  const btn = $("#btn-sync");
  const vacio = $("#sync-vacio");
  if (btn) {
    btn.hidden = n === 0;
    btn.textContent = n === 1 ? "Sync 1 change" : "Sync " + n + " changes";
  }
  if (vacio) vacio.hidden = n !== 0;
  // RULE 2: while a write is running, or while "did it boot up fine?" is
  // still unanswered, the modal body is NOT repainted. Repainting it here
  // erased the question -- the write empties the pending list, so this
  // function came back with zero items and painted "Nothing waiting" right
  // on top of the only place the question is ever asked.
  if (SYNC_ABIERTO && !SYNC_CORRIENDO && !ESPERANDO_ARRANQUE) pintarResumenSync();
  return SYNC_ITEMS;
}

/** Adds one change to the list. Returns true if it went in. Nothing is
 * generated, no gate runs, the device is not touched: that's Sync's job,
 * over the whole list at once. */
async function agregarCambio(tipo, parametros, etiqueta) {
  const r = await llamar("changes_add", tipo, parametros, etiqueta || null);
  if (!r.ok) return r;
  SYNC_RECHAZO = null; // a different batch: the old rejection doesn't apply
  await refrescarSync();
  return r;
}

function abrirModalSync() {
  SYNC_ABIERTO = true;
  $("#sync-modal").hidden = false;
  pintarResumenSync();
}

function cerrarModalSync() {
  // RULE 2's companion: while a write is running, or while there's an
  // unanswered boot question, the modal does not close. Closing it would
  // hide the only place the question is asked.
  if (SYNC_CORRIENDO || ESPERANDO_ARRANQUE) return;
  SYNC_ABIERTO = false;
  $("#sync-modal").hidden = true;
  refrescarSync();
}

/* --- how a moment looks -------------------------------------------------
 *
 * ONE line of status and ONE button per moment. Sync used to print four
 * paragraphs that all said the same thing ("nothing has been written yet"
 * twice, in two different boxes), and the user could not tell whether it
 * was syncing or waiting for him. The paragraphs are not deleted -- they
 * moved into a single CLOSED "Show details", which is where a wall of text
 * belongs. What stays visible with no click: the status line, the list of
 * changes, one button, and (if something is being deleted) the one line
 * that says what gets lost. */
/** The obligatory texts live in Python (`status().textos`), so they can't be
 * softened by editing the UI. The second argument is only the fallback for
 * a key Python doesn't send -- never a rewording of one it does. */
function T(clave, porDefecto) {
  const t = (ESTADO && ESTADO.textos) || {};
  return t[clave] || porDefecto || "";
}

function syncLinea(clase, texto) {
  return (
    '<div class="sync-estado ' + clase + '" id="sync-estado">' +
    '<span class="sync-punto" aria-hidden="true"></span>' +
    '<span class="sync-estado-txt">' + texto + "</span></div>"
  );
}

function syncVerMas(titulo, html) {
  return (
    '<details class="ver-mas sync-vermas"><summary>' + esc(titulo) +
    "</summary>" + html + "</details>"
  );
}

/** The list of pending changes, each with its `remove`. This is the one
 * block of text that earns its place: it is short and it is the answer to
 * "what am I about to do". */
function syncListaHTML(conQuitar) {
  return (
    '<div class="sync-lista">' +
    SYNC_ITEMS.map(
      (c) =>
        '<div class="sync-item"><span class="sync-item-txt">' +
        esc(c.label) + "</span>" +
        (conQuitar
          ? '<button class="quitar" data-sync-quitar="' + esc(c.id) +
            '">remove</button>'
          : "") +
        "</div>"
    ).join("") +
    "</div>"
  );
}

function syncCablearQuitar() {
  $("#sync-cuerpo")
    .querySelectorAll("[data-sync-quitar]")
    .forEach((b) =>
      b.addEventListener("click", async () => {
        await llamar("changes_remove", b.dataset.syncQuitar);
        SYNC_RECHAZO = null;
        SYNC_PREPARADO = null; // the prepared file was for another list
        await refrescarSync();
      })
    );
}

/* THE KEYS THAT WERE BOUND ON THEIR OWN, and the ones that couldn't be.
 *
 * A device this app adds used to arrive with its own page declaring four
 * rows: you went into it in Devices and not one rubber key did anything.
 * Now the page comes wired from the factory template, and that happens
 * inside the same prepared file -- so it is checked by the same gate and
 * written by the same single write.
 *
 * It goes ABOVE "Show details", visible with no click, for the same reason
 * the delete warning does: it is part of what the user is about to accept,
 * not technical trivia. Every number and every reason arrives already
 * decided from Python (`sync_preparar().key_template`), so the wording
 * cannot be softened by editing the UI.
 */
function syncPlantillaHTML(r) {
  const items = (r && r.key_template) || [];
  if (!items.length) return "";
  return (
    '<div class="sync-plantilla">' +
    items
      .map((p) => {
        const quien = esc(p.name || "device " + p.k1);
        if (!p.ok) {
          // No promise that it can be fixed from Keys: whether that tab
          // can offer anything depends on the same reason that failed
          // here. What IS always true is that the tab shows the state.
          return (
            "<div><b>" + quien + ": its keys were NOT bound.</b> " +
            esc(p.error || "no reason given") +
            " The device is still added, and the <b>Keys</b> tab shows, " +
            "device by device, what is bound and what is not.</div>"
          );
        }
        const faltan = (p.no_command || [])
          .map((f) => esc(f.key) + " (no " + esc(f.rol) + ")")
          .join(", ");
        const tuyas = (p.respetadas || []).map((f) => esc(f.key)).join(", ");
        return (
          "<div><b>" + quien + ": " + p.n_ligadas + " key" +
          (p.n_ligadas === 1 ? "" : "s") +
          " bound automatically</b> on its own page, so they work as soon as " +
          "you go into it." +
          (faltan
            ? " <b>Not bound:</b> " + faltan +
              " -- this device doesn't have those commands."
            : "") +
          (tuyas ? " <b>Left as you set them:</b> " + tuyas + "." : "") +
          (p.n_ya_tuyas
            ? " " + p.n_ya_tuyas + " were already waiting in this list, so " +
              "they were not queued twice."
            : "") +
          "</div>"
        );
      })
      .join("") +
    "</div>"
  );
}

/* RULE 4: what gets lost on a delete does NOT go inside "Show details". A
 * delete is the only irreversible thing here, so it keeps its own visible
 * line -- one line, not a paragraph. */
/** Los comandos que el alta dejo AFUERA porque el mando no tiene los glifos
 * de su etiqueta. Va arriba de la lista y no adentro de "Show details": el
 * usuario esta por escribir su mando y tiene que saber ANTES que ese boton no
 * va a estar. Lo mide `add_device.py` con las fuentes del blob delante;
 * aca solo se pinta. */
function syncOmitidosHTML(r) {
  const filas = [];
  (r && r.steps ? r.steps : []).forEach((p) => {
    (p.left_out || []).forEach((o) =>
      filas.push({ donde: p.label || p.kind || "", ...o })
    );
  });
  if (!filas.length) return "";
  return (
    '<div class="sync-perdida"><b>' + filas.length + " command" +
    (filas.length === 1 ? "" : "s") + " won't be added.</b> This remote " +
    "stores text as glyphs and its set has 71 of them \u2014 no Q, X or Z, " +
    "because no factory label ever used one. These labels need a glyph it " +
    "doesn't have, so they can't be drawn at any length and no abbreviation " +
    "reaches them:<ul>" +
    filas
      .map(
        (o) =>
          "<li><b>" + esc(o.label) + "</b> (needs " + esc(o.missing) + ")" +
          (o.donde ? ' <span class="vacio">in ' + esc(o.donde) + "</span>" : "") +
          "</li>"
      )
      .join("") +
    "</ul>Everything else in that device does get added.</div>"
  );
}

function syncPerdidaHTML() {
  const n = SYNC_ITEMS.filter((c) => c.kind === "remove_device").length;
  if (!n) return "";
  return (
    '<div class="sync-perdida">Removing a device also removes the activities ' +
    "that used it and the keys inside them. Going back means restoring an " +
    "earlier version from <b>History</b>.</div>"
  );
}

/** Whether writing from the app is offered. The env-var lock is gone: the
 * real protection is the gate (no green gate -> no button in the DOM) plus
 * the explicit red confirmation on screen. Only Python saying "read only"
 * turns it off, and if Python still refuses at write time the modal falls
 * back to the by-hand command instead of dead-ending. */
function grabadoPermitido() {
  if (!ESTADO) return false;
  if (ESTADO.solo_lectura === true) return false;
  return true;
}

/* --- moment 1: preparing ------------------------------------------------ */
async function pintarResumenSync() {
  const cuerpo = $("#sync-cuerpo");
  $("#sync-titulo").textContent = "Sync";
  if (!SYNC_ITEMS.length) {
    cuerpo.innerHTML =
      '<div class="vacio">' +
      esc(T("sync_no_changes", "There are no changes waiting.")) +
      " Make a change on Control, Activities or Keys and it shows up here.</div>";
    return;
  }
  const res = await llamar("changes_summary");
  const rechazado = SYNC_RECHAZO && SYNC_RECHAZO.firma === syncFirma();
  const n = SYNC_ITEMS.length;

  cuerpo.innerHTML =
    (rechazado
      ? // The remembered failure repaints as ITSELF: an app bug does not come
        // back as "the check did not pass" just because the modal was
        // reopened. Line and class were stored with it in `syncRechazar()`.
        syncLinea(
          SYNC_ESTADO_CLASE[SYNC_RECHAZO.category] || "alarma",
          esc(
            SYNC_RECHAZO.linea ||
              T("sync_not_passed", "The check did not pass: nothing will be written.")
          )
        )
      : syncLinea(
          "espera",
          "<b>" + n + (n === 1 ? " change" : " changes") + " ready to apply.</b>"
        )) +
    syncListaHTML(true) +
    syncPerdidaHTML() +
    syncVerMas(
      "Show details",
      (res.ok && res.text
        ? '<div class="sync-parrafo">' + esc(res.text) + "</div>"
        : "") +
        '<div class="sync-parrafo">Nothing has been written to the control ' +
        "yet. The next step builds one single file with every change in it " +
        "and checks that not one byte moved that nobody asked for. The " +
        "button that writes the control is only created if that check " +
        "passes.</div>"
    ) +
    // A remembered rejection replaces the "Check and prepare" button, never
    // the way out: "Discard everything" stays, or the only escape from a
    // batch the gate refuses would be closing the app.
    (rechazado
      ? SYNC_RECHAZO.html +
        '<div class="fila sync-acciones">' +
        '<button class="accion chico" id="btn-sync-vaciar">Discard everything</button></div>'
      : '<div class="fila sync-acciones">' +
        '<button class="accion primaria grande" id="btn-sync-preparar">' +
        "Check and prepare</button>" +
        '<button class="accion chico" id="btn-sync-vaciar">Discard everything</button></div>');

  syncCablearQuitar();
  const prep = $("#btn-sync-preparar");
  if (prep) prep.addEventListener("click", ejecutarSync);
  const vac = $("#btn-sync-vaciar");
  if (vac)
    vac.addEventListener("click", async () => {
      await llamar("changes_clear");
      SYNC_RECHAZO = null;
      SYNC_PREPARADO = null;
      await refrescarSync();
    });
}

/* The status line and the dot that go with each class. An app bug does NOT
 * get the red "protection" dot: it is not the control refusing anything. */
const SYNC_ESTADO_CLASE = {
  gate: "alarma",
  herramienta: "aviso",
  aplicacion: "rota",
};

/* RULE 1, for the batch: the rejection is remembered against the exact list
 * that was rejected. Removing or adding a change gives a different
 * signature and it can be tried again; putting the same list back repaints
 * the alarm instead of the button.
 *
 * `linea` and `clase` are remembered TOO. They used to not be, so a repaint
 * re-wrote every remembered failure -- including a crash -- as
 * `sync_no_paso`, undoing the distinction one tab-switch later. */
function syncRechazar(html, textoLinea, clase) {
  clase = clase || "gate";
  textoLinea =
    textoLinea ||
    T("clase_" + clase, "") ||
    T("sync_not_passed", "The check did not pass: nothing will be written.");
  SYNC_RECHAZO = {
    firma: syncFirma(),
    html: html,
    linea: textoLinea,
    category: clase,
  };
  const prep = $("#btn-sync-preparar");
  if (prep) prep.remove(); // leaves the DOM, doesn't sit there `disabled`
  const linea = $("#sync-estado");
  if (linea) {
    linea.className = "sync-estado " + (SYNC_ESTADO_CLASE[clase] || "alarma");
    linea.querySelector(".sync-estado-txt").innerHTML = esc(textoLinea);
    // The reason goes right under the line that says it stopped, not at the
    // bottom under the buttons.
    linea.insertAdjacentHTML("afterend", html);
  } else {
    $("#sync-cuerpo").insertAdjacentHTML("beforeend", html);
  }
}

/* --- moment 2: prepare the whole batch and run the gate, ONCE ---------- */
async function ejecutarSync() {
  const btn = $("#btn-sync-preparar");
  if (btn) ocupado(btn, true, "checking...");
  const linea = $("#sync-estado");
  if (linea) {
    linea.className = "sync-estado trabajando";
    linea.querySelector(".sync-estado-txt").innerHTML =
      "<b>Checking.</b> Building one file with every change and verifying " +
      "nothing else moved.";
  }

  const r = await llamar("sync_preparar");
  if (btn) ocupado(btn, false);
  SYNC_PREPARADO = r;
  pintarDetalleTecnico(r); // the technical dump lives on the Control tab

  // THE FIX. This used to say "verification blocking something that doesn't
  // add up" for EVERY failure -- including the ones where the app itself had
  // crashed with a KeyError before any check ran. `notaFallo()` paints the
  // class Python decided, and `r.linea` is the matching status line, also
  // resolved in Python.
  if (!r.ok) {
    // "sync": the status line right above already carries `r.linea` (the
    // class sentence). The note must not repeat it word for word.
    syncRechazar(notaFallo(r, "sync"), r.linea, claseDeFallo(r));
    return;
  }
  if (!r.ready) {
    // The gate ran and said no. This one IS the protection, and it keeps
    // exactly the words it had.
    syncRechazar(
      nota(
        "alarma",
        "<b>Writing isn't offered.</b> Verification found changes nobody " +
          "asked for in the prepared file. For safety this goes no further, " +
          "and the button is already gone."
      ),
      r.linea ||
        T("kind_gate", "The check did not pass: nothing will be written."),
      "gate"
    );
    return;
  }

  // RULE 1: the gate passed, so the body is REPAINTED -- the prepare button
  // never coexists with the write button, and the checked state is one
  // line, not another paragraph stacked under the previous ones.
  pintarVerificadoSync(r);
}

/* --- moment 2, painted: one line, the list, one RED button ------------- */
function pintarVerificadoSync(r) {
  const cuerpo = $("#sync-cuerpo");
  $("#sync-titulo").textContent = "Sync";
  cuerpo.innerHTML =
    syncLinea(
      "ok",
      esc(T("sync_verified", "Checked. Nothing moved that you didn't ask for.")) +
        (grabadoPermitido()
          ? ""
          : ' <span class="vacio">Read-only mode: the command to run in a ' +
            'terminal is under "Show details".</span>')
    ) +
    syncListaHTML(true) +
    syncOmitidosHTML(r) +
    syncPlantillaHTML(r) +
    syncPerdidaHTML() +
    syncVerMas(
      "Show details",
      '<div class="sync-parrafo">The ' + SYNC_ITEMS.length + " change" +
        (SYNC_ITEMS.length === 1 ? " was" : "s were") +
        " built into one file and compared against what is on the control " +
        "right now: not a single byte moved that nobody asked for. Nothing " +
        "has been written yet.</div>" +
        (r.file
          ? '<dl class="datos"><dt>File</dt><dd>' + esc(r.file) + "</dd>" +
            (r.referencia && r.referencia.blob
              ? "<dt>Compared against</dt><dd>" + esc(r.referencia.blob) + "</dd>"
              : "") +
            "</dl>"
          : "") +
        (r.command
          ? '<div class="sync-parrafo">Same thing, by hand in a terminal:</div>' +
            "<pre>cd " + esc(r.command.cwd) + "\n" + esc(r.command.command) +
            "</pre>"
          : "")
    ) +
    // THIS is what replaced the env-var lock: an explicit confirmation on
    // the screen. The button is red, it is the only red thing here, and it
    // says what it does -- not "Confirm". The line under it is Python's
    // wording, not the UI's.
    '<div class="fila sync-acciones">' +
    (grabadoPermitido()
      ? '<button class="accion peligro grande" id="btn-sync-escribir">' +
        "Write to my control</button>"
      : '<button class="accion primaria" id="btn-sync-ya-grabe">' +
        "I already recorded it by hand</button>") +
    "</div>" +
    (grabadoPermitido()
      ? '<div class="sync-perdida sync-confirmar">' +
        esc(T("sync_confirmar", "This writes your remote's memory. It cannot " +
          "be undone from here.")) +
        "</div>"
      : "") +
    "<div id='sync-progreso'></div>";

  syncCablearQuitar();
  const esc_ = $("#btn-sync-escribir");
  if (esc_)
    esc_.addEventListener("click", () => {
      esc_.remove(); // leaves the DOM: no second click, not even a disabled one
      const vm = cuerpo.querySelector("details.sync-vermas");
      if (vm) vm.remove();
      syncEscribir(r);
    });
  const man = $("#btn-sync-ya-grabe");
  if (man)
    man.addEventListener("click", () => {
      man.remove();
      syncRegistrarManual(r);
    });
}

/* The env-var lock is gone, but Python still has the last word. If it
 * refuses the write, the modal offers the by-hand command right there
 * instead of leaving the user staring at an error. */
function syncCaidaAMano(r, motivo) {
  const zona = $("#sync-progreso");
  const linea = $("#sync-estado");
  if (linea) {
    linea.className = "sync-estado aviso";
    linea.querySelector(".sync-estado-txt").innerHTML =
      "<b>The app didn't write.</b> " + esc(motivo);
  }
  const barra = $("#sync-prog-relleno");
  if (barra && barra.parentNode) barra.parentNode.remove();
  const ptxt = $("#sync-prog-txt");
  if (ptxt) ptxt.remove();
  zona.innerHTML =
    (r.command
      ? "<pre>cd " + esc(r.command.cwd) + "\n" + esc(r.command.command) + "</pre>"
      : "") +
    '<div class="fila sync-acciones"><button class="accion primaria" ' +
    'id="btn-sync-ya-grabe">I already recorded it by hand</button></div>';
  $("#btn-sync-ya-grabe").addEventListener("click", () => {
    $("#btn-sync-ya-grabe").remove();
    syncRegistrarManual(r);
  });
}

let SYNC_PREPARADO = null; // last sync_preparar(), for the write step

/** The pseudo-result the shared recording helpers expect. Built from what
 * `sync_preparar()` returned so `preguntarArranqueControl` and
 * `responderArranqueControl` are the SAME code as the single-change paths
 * -- there is no second, possibly laxer, implementation of RULE 2. */
function syncComoResultado(r) {
  return {
    file: r.file,
    referencia: r.referencia,
    gate: r.gate,
    command: r.command,
    name:
      SYNC_ITEMS.length === 1
        ? SYNC_ITEMS[0].label
        : SYNC_ITEMS.length + " changes applied together",
    commands: null,
    screen: "sync",
  };
}

/* --- moment 3: the write itself, with the progress bar ----------------
 *
 * While this runs nothing on the modal can be touched: the change list and
 * its `remove` buttons are gone, the close button is disabled (on top of
 * `SYNC_CORRIENDO` already refusing to close) and the box is marked busy. */
function syncBloquear(si) {
  const caja = document.querySelector(".sync-caja");
  if (caja) {
    caja.classList.toggle("sync-bloqueado", !!si);
    caja.setAttribute("aria-busy", si ? "true" : "false");
  }
  const cerrar = $("#btn-sync-cerrar");
  if (cerrar) cerrar.disabled = !!si;
}

async function syncEscribir(r) {
  const cuerpo = $("#sync-cuerpo");
  SYNC_CORRIENDO = true;
  syncBloquear(true);
  $("#sync-titulo").textContent = "Sync";
  cuerpo.innerHTML =
    syncLinea(
      "trabajando",
      "<b>" + esc(T("sync_escribiendo", "Writing to your remote. Don't unplug it.")) +
        "</b>"
    ) +
    '<div class="sync-barra-prog"><div class="sync-barra-prog-relleno" ' +
    'id="sync-prog-relleno" style="width:0%"></div></div>' +
    '<div class="sync-prog-txt" id="sync-prog-txt">starting...</div>' +
    '<details class="ver-mas sync-vermas"><summary>Show details</summary>' +
    '<pre class="sync-log" id="sync-log"></pre></details>' +
    "<div id='sync-progreso'></div>";
  const zona = $("#sync-progreso");
  zona.innerHTML = "<div id='sync-final'></div>";

  const ini = await llamar("sync_apply_start", "GRABAR");
  if (!ini.ok) {
    SYNC_CORRIENDO = false;
    syncBloquear(false);
    syncCaidaAMano(r, ini.error || "the write was refused.");
    return;
  }
  const cambiosEscritos = ini.changes || SYNC_ITEMS.slice();
  const resultado = syncComoResultado(r);
  if (cambiosEscritos.length)
    resultado.name =
      cambiosEscritos.length === 1
        ? cambiosEscritos[0].label
        : cambiosEscritos.length + " changes applied together";

  let vistos = 0;
  let etapa = "starting";
  let fallosSeguidos = 0;
  // Nothing below this line may leave the screen stuck on "Writing to your
  // remote": every exit path goes through `syncCerrarEscritura`, and the
  // whole loop is wrapped so a DOM node that vanished can't kill the
  // polling silently (an exception inside an async function is an
  // unhandled rejection -- no error on screen, no progress, forever).
  try {
    for (;;) {
      const p = await llamar("sync_progreso", ini.trabajo_id, vistos);
      if (!p.ok) {
        // A one-off hiccup in the bridge is not a reason to abandon a
        // write that is still running. Several in a row is.
        fallosSeguidos += 1;
        if (fallosSeguidos >= 5) {
          syncCerrarEscritura();
          syncEstadoLinea("alarma", "<b>Lost track of the write.</b>");
          zona.innerHTML += nota(
            "alarma",
            "<b>Lost track of the write</b> (" + esc(p.error) + "). It may " +
              "have finished anyway: <b>don't unplug the remote</b> and check " +
              "the History tab."
          );
          cargarHistorial();
          return;
        }
        await new Promise((res) => setTimeout(res, 600));
        continue;
      }
      fallosSeguidos = 0;
      vistos = p.total_events;
      const log = $("#sync-log");
      (p.eventos_nuevos || []).forEach((e) => {
        // `texto` -- the key the events actually carry (`progress.py`).
        // This used to read `e.linea`, which no event has: the log stayed
        // empty and the stage line said "starting" until the end.
        const t = e && e.text;
        if (!t) return;
        if (log) log.textContent += t + "\n";
        etapa = String(t).trim() || etapa;
      });
      if (log) log.scrollTop = log.scrollHeight;
      const relleno = $("#sync-prog-relleno");
      if (relleno) relleno.style.width = (p.porcentaje || 0) + "%";
      const ptxt = $("#sync-prog-txt");
      if (ptxt)
        ptxt.textContent =
          (p.porcentaje || 0) + "% - " + (p.etapa || etapa) +
          " - " + (p.segundos_transcurridos || 0) + "s";

      if (p.terminado) {
        syncTerminado(p, resultado);
        return;
      }
      await new Promise((res) => setTimeout(res, 600));
    }
  } catch (e) {
    // The write is NOT known to have failed -- what failed is this screen.
    // Say so, and point at the history, which Python already wrote.
    syncCerrarEscritura();
    syncEstadoLinea("alarma", "<b>The progress screen broke.</b>");
    zona.innerHTML += nota(
      "alarma",
      "<b>The progress screen broke</b> (" + esc(String(e && e.message ? e.message : e)) +
        "). The write itself may have gone through: <b>don't unplug the " +
        "remote</b> and check the History tab."
    );
    cargarHistorial();
  }
}

/** Unlocks the modal. Anything that stops following the write calls this,
 * so there is exactly one place that can leave the box busy. */
function syncCerrarEscritura() {
  SYNC_CORRIENDO = false;
  syncBloquear(false);
}

/** Rewrites the headline of the write screen. Every exit path calls it:
 * leaving "Writing to your remote. Don't unplug it." on screen when nothing
 * is being written any more is exactly the lie this fixes. */
function syncEstadoLinea(clase, html) {
  const linea = $("#sync-estado");
  if (!linea) return;
  linea.className = "sync-estado " + clase;
  const txt = linea.querySelector(".sync-estado-txt");
  if (txt) txt.innerHTML = html;
  const ptxt = $("#sync-prog-txt");
  if (ptxt && clase !== "ok") ptxt.textContent = "stopped following the write";
}

/** The end of the write, no matter how it ended. The hard signal is
 * `p.terminado` -- the subprocess is gone -- not whether the log parsed. */
function syncTerminado(p, resultado) {
  syncCerrarEscritura();
  // `p.ok` is the BRIDGE's flag (did the call work). `p.grabado_ok` /
  // `p.error_grabado` are the WRITE's. They used to be the same two names,
  // and the write's `null` (still running) silently overwrote the call's
  // `true` -- which is what froze this screen. Don't merge them again.
  const bien = p.returncode === 0 && p.grabado_ok !== false;
  // Deliberately terse: the question right below already says to unplug it
  // and look at the screen, and saying it twice is exactly what this
  // rewrite is getting rid of.
  syncEstadoLinea(
    bien ? "ok" : "alarma",
    bien
      ? "<b>Written.</b>"
      : "<b>Finished with an error (code " + esc(p.returncode) + ").</b>" +
        (p.error_grabado ? " " + esc(p.error_grabado) : "")
  );
  const relleno = $("#sync-prog-relleno");
  if (relleno) relleno.style.width = "100%";
  const ptxt = $("#sync-prog-txt");
  if (ptxt)
    ptxt.textContent =
      (bien ? "done" : "stopped") + " - " + (p.etapa || "") +
      " - " + (p.segundos_transcurridos || 0) + "s";

  const fin = $("#sync-final") || $("#sync-progreso");
  const anotado = p.write_id !== null && p.write_id !== undefined;
  if (fin) {
    fin.innerHTML = "";
    // RULE 2, unchanged and shared: same function the single-change
    // paths call, so it can't drift into something softer here.
    if (anotado) {
      preguntarArranqueControl(p.write_id, resultado, fin);
    } else {
      fin.innerHTML += nota(
        "alarma",
        "<b>Couldn't log this write in the history</b>, so there's nowhere " +
          "to save whether it booted." +
          (p.write_entry_error ? " (" + esc(p.write_entry_error) + ")" : "") +
          " Check the History tab before doing anything else."
      );
    }
  }
  cargarHistorial();
  // `refrescarSync()` repaints the modal body when nothing is holding it.
  // `preguntarArranqueControl` sets `ESPERANDO_ARRANQUE`, which holds it --
  // but on the branch where there is no history row, nothing does, and the
  // repaint would erase the very warning that says the write is untracked.
  if (anotado) refrescarSync();
}

async function syncRegistrarManual(r) {
  const zona = $("#sync-progreso");
  const resultado = syncComoResultado(r);
  const gr = await llamar(
    "remote_register_manual_recording",
    r.file,
    r.referencia.blob,
    r.repoints_int,
    0,
    "applied via Sync, recorded by hand from the terminal",
    resultado.name,
    null
  );
  if (!gr.ok) {
    zona.innerHTML = nota("alarma", esc(gr.error));
    return;
  }
  zona.innerHTML = nota("ok", "Logged in the history as #" + gr.write_id + ".");
  preguntarArranqueControl(gr.write_id, resultado, zona);
  await llamar("changes_clear");
  cargarHistorial();
  refrescarSync();
}

$("#btn-sync").addEventListener("click", abrirModalSync);
$("#btn-sync-cerrar").addEventListener("click", cerrarModalSync);
$("#sync-modal").addEventListener("click", (e) => {
  if (e.target && e.target.id === "sync-modal") cerrarModalSync();
});

/* ============================== ACCOUNT =================================== */

function pintarEntorno() {
  const falta = ESTADO.absent || {};
  const claves = Object.keys(falta);
  $("#entorno").innerHTML =
    '<dl class="datos">' +
    "<dt>Project</dt><dd>" + esc(ESTADO.raiz) + "</dd>" +
    "<dt>App data</dt><dd>" + esc(ESTADO.datos) + "</dd>" +
    "<dt>Python</dt><dd>" + esc(ESTADO.python) + " (" + esc(ESTADO.plataforma) + ")</dd>" +
    "<dt>Anchor</dt><dd>" + esc(ESTADO.ancla_md5) + "</dd>" +
    "</dl>" +
    (claves.length
      ? nota(
          "aviso",
          "<b>Modules that failed to load.</b> Anything that depends on them stays off.<br>" +
            claves
              .map((k) => "<code>" + esc(k) + "</code>: " + esc(falta[k]))
              .join("<br>")
        )
      : nota("ok", "All modules loaded."));
}

/**
 * The Account card.
 *
 * `readKeychain` defaults to FALSE and that is deliberate. Reading the keychain
 * makes macOS pop up a permission dialog, and this used to run on the first
 * paint with Account as the landing tab: the app asked for a credential
 * before the user had touched anything, twice, on every single launch. An app
 * that asks to draw its first screen teaches people to click Allow without
 * reading it.
 *
 * Now the first paint draws the card WITHOUT the keychain, and the saved
 * account is only looked up when the user presses the button that says so.
 */
async function cargarCuenta(readKeychain = false) {
  const c = await llamar("account_status", readKeychain);
  const el = $("#cuenta-estado");
  el.className = "";
  // The password form only exists when signing in can actually happen.
  // Showing a disabled password box would still be ASKING for a credential
  // the app has no way to use, so it is removed from the screen instead.
  $("#cuenta-formulario").hidden = !c.ok;
  $("#cuenta-sin-cliente").hidden = !!c.ok;
  // Y el boton del llavero con ellos, POR EL MISMO MOTIVO. Sin el modulo de
  // sesion, `account_status` corta antes de tocar el llavero y devuelve
  // ok:false; el boton quedaba a la vista y al apretarlo no pasaba
  // literalmente nada. Un boton que no puede funcionar es peor que ninguno:
  // el que lo aprieta no sabe si fallo la app o fallo el.
  $("#btn-keychain").hidden = !c.ok;
  if (!c.ok) {
    el.innerHTML = nota("aviso", esc(c.error));
    ["#btn-login", "#btn-renovar", "#btn-olvidar"].forEach(
      (s) => ($(s).disabled = true)
    );
    return;
  }
  if (c.email && !$("#email").value) $("#email").value = c.email;
  $("#btn-keychain").hidden = !!c.keychain_read;
  el.innerHTML =
    (c.keychain_read
      ? chip(c.email || "no email saved", c.email ? "si" : "") +
        chip(
          c.hay_password ? "password in the keychain" : "no password saved",
          c.hay_password ? "si" : ""
        )
      : chip("keychain not read", "")) +
    chip(c.token_lip ? "signed in" : "not signed in yet", c.token_lip ? "si" : "no");
}

// The ONLY path that reaches the keychain without a sign-in attempt, and it
// takes a click to get there.
$("#btn-keychain").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "reading the keychain...");
  await cargarCuenta(true);
  ocupado(b, false);
});

$("#btn-login").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "signing in...");
  const r = await llamar(
    "account_login",
    $("#email").value,
    $("#password").value,
    $("#recordar").checked
  );
  ocupado(b, false);
  $("#password").value = "";
  mostrar("#cuenta-salida", r.ok ? "session started: " + r.email : "ERROR: " + r.error);
  // WITH the keychain: the user just signed in, so the permission was already
  // asked for and granted in this same click. Refreshing without it would
  // blank out the state they just created.
  cargarCuenta(true);
});

$("#btn-renovar").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "renewing...");
  const r = await llamar("account_renew", $("#email").value);
  ocupado(b, false);
  mostrar("#cuenta-salida", r.ok ? "session renewed" : "ERROR: " + r.error);
  cargarCuenta(true);
});

$("#btn-olvidar").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "deleting...");
  const r = await llamar("account_forget", $("#email").value);
  ocupado(b, false);
  mostrar("#cuenta-salida", r.ok ? "password deleted from the keychain" : "ERROR: " + r.error);
  cargarCuenta(true);
});

/* ============================= CATALOG ===================================== */

/* Where each ready device came from. None of the three creates or deletes
 * anything on the account: the catalog is read-only, the .ir is a local
 * file, and "captured before" means config that was already on disk. */
const ORIGEN_TEXTO = {
  catalogo: "from the catalog",
  manual: "from an .ir file",
  capturado: "captured before",
};

/* IS THIS ONE ACTUALLY USABLE?
 *
 * The contract, published by `Api.catalog_local()` on every item:
 *
 *   `aplicable`        -- true/false. True means the file has the commands
 *                         AND the timing definition of their protocol, which
 *                         is what `config_work/synth_ir.py` needs to synthesize
 *                         the IR waveform. False means Sync would refuse it.
 *   `falta_protocolo`  -- when `aplicable` is false because of a missing
 *                         protocol, the NAME of that protocol (e.g.
 *                         "Toshiba 32 Bit"). Null/absent when the item is
 *                         fine, or when it is unusable for some other reason.
 *
 * READ THE KEYS ONCE, HERE. This is the exact shape that has already bitten
 * this project four times (`tramas`, `validos`, `ok`, `config_json` vs
 * `json`): a key renamed on one side of the bridge that keeps returning
 * something plausible. Everything that paints applicability goes through
 * this function, so there is one place to fix if the name ever moves.
 *
 * THE FALLBACK IS DELIBERATE AND IT IS NOT A GUESS. Until `catalog_local()`
 * publishes `aplicable`, the field arrives `undefined` -- and `undefined` is
 * NOT "false", it is "this build doesn't say". Painting "Not usable yet" on
 * every device because a key is missing would be its own lie. So when the
 * field is absent the answer is derived from the one signal that IS on every
 * item today and means precisely the same thing: whether the protocol list
 * came back non-empty. The measured 8 devices agree with that reading (4
 * with a protocol are the applicable ones, 4 without are the broken ones),
 * and once the field lands it wins outright.
 */
function aplicabilidad(i) {
  const declarado = i && i.aplicable;
  const apto =
    declarado === undefined || declarado === null
      ? (i.protocolos || []).length > 0
      : !!declarado;
  return {
    // A file whose commands could not even be RE-COUNTED is not addable
    // whatever the protocol says: `catalog_local()` sets `comandos: null` and
    // puts the exception in `problema` when `catalogo.read_local_export()`
    // throws. Letting that through on the strength of a protocol name would
    // send the user to Sync with a file that cannot be read.
    apto: apto && i.commands !== null,
    ilegible: i.commands === null,
    problema: (i && i.problema) || null,
    // Same treatment: only trust the field, never invent a protocol name.
    absent: (i && i.missing_protocol) || null,
    // Is the verdict the backend's, or ours from the protocol list? Only
    // used to word the explanation honestly.
    declarado: !(declarado === undefined || declarado === null),
  };
}

/* Why this one can't be added, in the user's terms, plus what to do about it.
 * A device with no protocol timing is not a corrupt file: it is a file that
 * is missing one piece, and that piece can be obtained. */
function porqueNoAplica(i) {
  const a = aplicabilidad(i);
  // The file itself could not be read. That is a different failure from a
  // missing protocol and must not be dressed up as one -- there is nothing
  // to download that fixes it.
  if (a.ilegible) {
    return (
      "<b>This file can't be read.</b> Its commands could not be counted, so " +
      "there is nothing here to add to the control. Downloading the device " +
      "again, or importing a <b>.ir</b> for it, replaces it with a good copy; " +
      "<b>Delete</b> gets it out of the way." +
      (a.problema
        ? "<div class='chico' style='margin-top:6px;word-break:break-word'>" +
          esc(a.problema) + "</div>"
        : "")
    );
  }
  /* A missing protocol is only ONE of the ways a device can be unusable, and
   * for a while it was the only one this function knew how to talk about --
   * so a device blocked by something else got told to go download a timing
   * definition it already had. `motivo_no_aplicable` is written in Python by
   * whoever actually measured the failure; when it is there it is the true
   * story and it wins. Only the missing-protocol case is worded here, because
   * only that one has a remedy the user performs elsewhere. */
  if (i && i.missing_category && i.missing_category !== "protocolo") {
    return (
      "<b>Can't be added to the control yet.</b> " +
      esc(i.not_applicable_reason || "the reason wasn't measured") +
      (i.reparable
        ? "<div class='chico' style='margin-top:8px'>" +
          '<button class="accion chico" data-reparar-local="' + esc(i.dir) +
          '">Repair this device</button>' +
          " Nothing is written to your remote or your Logitech account." +
          "</div>"
        : "")
    );
  }
  const nombre = a.absent ? "<b>" + esc(a.absent) + "</b>" : "its protocol";
  return (
    "<b>Can't be added to the control yet.</b> This file has its commands, but " +
    "not the timing definition of " + nombre + " -- the microsecond pattern the " +
    "remote has to blink. Without it there is no waveform to write, so Sync " +
    "would refuse it." +
    "<div class='chico' style='margin-top:6px'>Two ways to get it:" +
    "<ul style='margin:4px 0 0 18px;padding:0'>" +
    "<li>Download any other device that uses " + (a.absent ? nombre : "the same protocol") +
    " from the catalog above -- the timing arrives with it and this one starts " +
    "working too, on its own.</li>" +
    "<li>Import a <b>.ir</b> file for it (the box above): a Flipper Zero " +
    ".ir carries the waveform already measured, so it needs no protocol at all.</li>" +
    "</ul></div>"
  );
}

/* One row of the ready-devices table. Split out of `cargarLocales()` so the
 * inline delete confirmation can be inserted right after the row it belongs
 * to instead of at the bottom of the table. */
function filaLocal(i) {
  const a = aplicabilidad(i);
  const nombre = (i.fabricante || "?") + " " + (i.modelo || "?");
  return (
    '<tr class="' + (a.apto ? "" : "no-aplicable") + '" data-fila="' + esc(i.dir) + '">' +
    "<td><b>" + esc(nombre) + "</b><br>" +
    '<span class="vacio">' +
    esc((i.downloaded_at || "").slice(0, 19).replace("T", " ")) + "</span></td>" +
    // The count, or a SHORT marker when it couldn't be counted. The raw
    // exception used to be printed straight into this cell: a
    // hundred-character `ProtocolError: ...` in a numeric column stretched
    // it and pushed every other column off the row. The text still gets
    // shown in full -- in the reason row underneath, where it has the width.
    "<td>" +
    (i.commands === null ? chip("unreadable", "no") : esc(i.commands)) +
    "</td>" +
    "<td>" + chip(ORIGEN_TEXTO[i.origin] || i.origin || "?", "si") + "</td>" +
    "<td>" +
    // "<name> -- missing" is only claimed when a missing protocol is
    // actually the problem. For a file that could not be read at all the
    // protocol is simply unknown, and saying it is missing would send the
    // user off to download a protocol that would not fix anything.
    ((i.protocolos || []).length
      ? (i.protocolos || []).map((p) => chip(p)).join("")
      : a.absent && !a.ilegible
        ? chip(a.absent + " -- missing", "no")
        : '<span class="vacio">-</span>') +
    "</td>" +
    "<td>" + (a.apto ? chip("Ready", "si") : chip("Not usable yet", "no")) + "</td>" +
    '<td class="acciones">' +
    // "Use in Control" is NOT drawn for a device Control would reject. Same
    // rule as the record button: it does not sit there disabled, it does not
    // exist -- so the dead end is impossible to walk into instead of being
    // discovered three screens later in Sync.
    (a.apto
      ? '<button class="accion chico" data-usar="' + esc(i.config_json) +
        '">Use in Control</button>'
      : "") +
    '<button class="accion chico peligro" data-borrar-local="' +
    esc(i.dir) + '" data-nombre-local="' + esc(nombre) +
    '">Delete</button></td></tr>' +
    // The explanation rides UNDER its own row, so "this one is broken" and
    // "here is why" can never drift apart on screen.
    (a.apto
      ? ""
      : '<tr class="no-aplicable-motivo"><td colspan="6">' +
        nota("aviso", porqueNoAplica(i)) +
        "</td></tr>")
  );
}

async function cargarLocales() {
  const r = await llamar("catalog_local");
  const el = $("#locales");
  el.className = "";
  if (!r.ok) {
    el.innerHTML = nota("aviso", esc(r.error));
    CATALOGO_LOCAL = [];
    pintarSeleccion();
    mapeoPintarDispositivos();
    return;
  }
  CATALOGO_LOCAL = r.items;
  pintarSeleccion();
  mapeoPintarDispositivos();
  if (!r.items.length) {
    el.className = "vacio";
    el.textContent =
      "there aren't any yet: search the catalog or import an .ir file";
    return;
  }
  el.innerHTML =
    "<table><thead><tr><th>Device</th><th>Commands</th><th>Where it came from</th>" +
    "<th>Protocol</th><th>Ready to add?</th><th></th></tr></thead><tbody>" +
    r.items.map(filaLocal).join("") +
    "</tbody></table>";

  el.querySelectorAll("[data-usar]").forEach((b) =>
    b.addEventListener("click", () => {
      $('button.nav[data-p="check"]').click();
      $("#s-json").value = b.dataset.usar;
      $("#s-json").dispatchEvent(new Event("change"));
    })
  );
  el.querySelectorAll("[data-borrar-local]").forEach((b) =>
    b.addEventListener("click", () =>
      confirmarBorradoLocal(b.dataset.borrarLocal, b.dataset.nombreLocal)
    )
  );
  el.querySelectorAll("[data-reparar-local]").forEach((b) =>
    b.addEventListener("click", () => repararLocal(b, b.dataset.repararLocal))
  );
}

/* Repair, the remedy the error message already promised. It used to say
 * "Repairing that device in Catalog renames it and makes it usable again"
 * while no Repair existed anywhere -- a remedy that is named and absent is
 * the same dead end as a delete that does nothing.
 *
 * Like `borrarLocal`, the outcome goes to `#locales-aviso`, OUTSIDE the zone
 * `cargarLocales()` repaints: writing it into the repainted node is how the
 * delete confirmation used to vanish into a detached element. And the button
 * is captured as an argument rather than read from `e.currentTarget`, which
 * is null after the first `await`. */
async function repararLocal(btn, dir) {
  if (btn) ocupado(btn, true, "repairing...");
  const r = await llamar("catalog_repair", dir);
  const aviso = $("#locales-aviso");
  if (!r.ok) {
    if (btn) ocupado(btn, false);
    aviso.innerHTML = nota("alarma", "<b>Couldn't repair it.</b> " + esc(r.error));
    return;
  }
  // `ok` here means the call ran, NOT that the device ended usable -- the two
  // are different answers and the screen has to show which one it got.
  aviso.innerHTML = nota(
    r.aplicable ? "ok" : "aviso",
    (r.aplicable ? "<b>Repaired.</b> " : "<b>Still can't be added.</b> ") +
      esc(r.mensaje || "")
  );
  await cargarLocales();
}

/* Deleting from the LOCAL catalog. This is NOT the same as removing a
 * device from the control, and the confirmation says so: what disappears is
 * the copy downloaded to this computer, so nothing about the remote changes
 * and nothing about your Logitech account changes -- the same device can be
 * downloaded again from the public catalog. That's also why this one does
 * NOT go through Sync: Sync is for things that get written to the control,
 * and this never touches it. */
function confirmarBorradoLocal(dir, nombre) {
  // THE CONFIRMATION IS PUT WHERE THE USER CLICKED. It used to be written
  // into a single `#local-borrar` div parked after the whole table: with
  // nine devices listed, clicking Delete on the first one opened the
  // question about nine rows further down, off-screen -- so the honest
  // reading of the screen was "the button does nothing". It now goes into a
  // row inserted directly beneath the device it is asking about.
  const fila = $('#locales tr[data-fila="' + (window.CSS && CSS.escape ? CSS.escape(dir) : dir) + '"]');
  if (!fila) return;
  cerrarBorradoLocal();
  // A "X was deleted" line from a PREVIOUS delete must not still be sitting
  // above the table while a new question is being asked about another
  // device: two devices, one stale sentence, and no way to tell which one it
  // is about.
  $("#locales-aviso").innerHTML = "";
  const tr = document.createElement("tr");
  tr.className = "confirmar-borrado";
  tr.id = "local-borrar-fila";
  tr.innerHTML =
    '<td colspan="6"><div id="local-borrar">' +
    nota(
      "aviso",
      "<b>Delete " + esc(nombre) + " from this computer?</b>" +
        "<ul style='margin:8px 0 0 18px;padding:0'>" +
        "<li>It disappears from this list and from the Control dropdown.</li>" +
        "<li><b>Your control is not touched.</b> Nothing is written to the " +
        "remote by this: if this device is already on it, it stays there -- " +
        "to remove it from the remote, use <b>Remove</b> on the Control tab.</li>" +
        "<li><b>Your Logitech account is not touched.</b> The catalog is only " +
        "ever read, never modified. You can search and download this again.</li>" +
        "<li>Anything you learned or imported by hand for it, though, is " +
        "gone for good: there's no copy of that anywhere else.</li>" +
        "</ul>"
    ) +
    '<div class="fila"><button class="accion peligro" id="btn-local-si">' +
    "Yes, delete it from this computer</button>" +
    '<button class="accion" id="btn-local-no">Better not</button></div></div></td>';
  fila.insertAdjacentElement("afterend", tr);
  $("#btn-local-no").addEventListener("click", cerrarBorradoLocal);
  $("#btn-local-si").addEventListener("click", (e) => borrarLocal(e.currentTarget, dir, nombre));
}

function cerrarBorradoLocal() {
  const previa = $("#local-borrar-fila");
  if (previa) previa.remove();
}

/* The delete itself.
 *
 * WHY THE RESULT IS NOT WRITTEN INTO THE CONFIRMATION BOX. On success this
 * calls `cargarLocales()`, which replaces the whole of `#locales` -- table,
 * confirmation row and all. Writing "it was deleted" into a node that the
 * very next statement detaches is what made a delete that WORKED look like a
 * delete that did nothing: measured, the note landed on a node with
 * `isConnected === false` while the live box on the page was empty. The
 * outcome goes to `#locales-aviso`, which lives outside `#locales` and
 * survives the repaint. */
async function borrarLocal(btn, dir, nombre) {
  // `btn` is only ever the button to grey out while the call is in flight;
  // losing it must never cost the delete itself.
  if (btn) ocupado(btn, true, "deleting...");
  const r = await llamar("catalog_delete", dir);
  const aviso = $("#locales-aviso");
  if (r.ok) {
    cerrarBorradoLocal();
    aviso.innerHTML = nota(
      "ok",
      "<b>" + esc(nombre) + " was deleted from this computer.</b> Your remote " +
        "and your Logitech account were not touched."
    );
    await cargarLocales();
    return;
  }
  if (btn) ocupado(btn, false);

  // THE DEAD END THIS SCREEN USED TO LEAVE THE USER IN. A device queued for
  // Sync cannot be deleted (the queued change points at a file that would
  // stop existing). Combined with a device Sync refuses to apply, that is a
  // trap with no exit on this screen: it can't be applied and it can't be
  // deleted, and the message only said "take it off the Sync list first"
  // without saying where or offering to. `catalog_delete` now names the
  // blocking changes in `bloqueado_por` ([{id, etiqueta}]), so the way out
  // can be offered right here.
  const bloqueos = r.bloqueado_por || [];
  if (!bloqueos.length) {
    $("#local-borrar").innerHTML = nota(
      "alarma",
      "<b>Couldn't delete it.</b> " + esc(r.error)
    );
    return;
  }
  $("#local-borrar").innerHTML =
    nota(
      "alarma",
      "<b>Couldn't delete " + esc(nombre) + " yet.</b> It is waiting in Sync to " +
        "be written to the remote, and deleting it now would leave that " +
        "waiting change pointing at a file that no longer exists:" +
        "<ul style='margin:8px 0 0 18px;padding:0'>" +
        bloqueos.map((b) => "<li>" + esc(b.label) + "</li>").join("") +
        "</ul>" +
        "<div class='chico' style='margin-top:6px'>Taking it off the Sync list " +
        "writes nothing to the remote -- the waiting change is only a list on " +
        "this computer.</div>"
    ) +
    '<div class="fila"><button class="accion peligro" id="btn-local-forzar">' +
    "Take it off the Sync list and delete it</button>" +
    '<button class="accion" id="btn-local-no2">Leave it alone</button></div>';
  $("#btn-local-no2").addEventListener("click", cerrarBorradoLocal);
  $("#btn-local-forzar").addEventListener("click", async (e) => {
    // THE BUTTON IS CAPTURED BEFORE THE FIRST `await`, NEVER READ AFTER ONE.
    // `event.currentTarget` is only set while the event is being dispatched;
    // once this handler yields at an `await` the dispatch is over and it
    // reads back null. Passing that null on to `ocupado()` threw
    // "Cannot set properties of null (setting 'disabled')" -- and because the
    // throw happened inside an async handler nobody awaits, it surfaced
    // nowhere: the Sync change was dropped, the delete never ran, and the
    // screen just sat there. Same family as the key-contract traps.
    const boton = e.currentTarget;
    ocupado(boton, true, "removing from Sync...");
    for (const b of bloqueos) {
      const q = await llamar("changes_remove", b.id);
      if (!q.ok) {
        $("#local-borrar").innerHTML = nota(
          "alarma",
          "<b>Couldn't take it off the Sync list.</b> " + esc(q.error)
        );
        return;
      }
    }
    // Exactly what Sync's own "remove" button does after dropping a change
    // (`syncCablearQuitar`): a prepared blob was built for the OLD list, so
    // it is stale the moment the list changes, and the bar has to be
    // recounted from Python rather than from anything cached here.
    SYNC_RECHAZO = null;
    SYNC_PREPARADO = null;
    await refrescarSync();
    await borrarLocal(boton, dir, nombre);
  });
}

$("#btn-buscar").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "searching...");
  // Logitech's catalog can take a while to answer -- up to 20-30 seconds
  // isn't a hang, so the wait says so instead of leaving a static button
  // as the only sign anything is happening.
  const el = $("#resultados");
  el.className = "cargando";
  el.textContent = "searching Logitech's catalog... this can take up to 30 seconds";
  const r = await llamar(
    "catalog_search",
    $("#email").value,
    $("#fab").value,
    $("#mod").value
  );
  ocupado(b, false);
  el.className = "";
  if (!r.ok) {
    el.innerHTML = nota("aviso", esc(r.error));
    return;
  }
  if (!r.items.length) {
    el.className = "vacio";
    el.textContent = "no results";
    return;
  }
  el.innerHTML =
    "<table><thead><tr><th>Device</th><th></th></tr></thead><tbody>" +
    r.items
      .map(
        (i) =>
          "<tr><td>" + esc(i.label) + "</td>" +
          '<td class="acciones"><button class="accion chico" data-guardar="' +
          i.index + '">Save</button></td></tr>'
      )
      .join("") +
    "</tbody></table>";
  el.querySelectorAll("[data-guardar]").forEach((btn) =>
    btn.addEventListener("click", () => guardar(btn, Number(btn.dataset.guardar)))
  );
});

async function guardar(btn, indice) {
  // No add or remove on any account: read-only access to the public
  // catalog, which is why there's no confirmation checkbox to tick first.
  ocupado(btn, true, "saving...");
  const el = $("#bajada");
  el.hidden = false;
  el.className = "cargando";
  el.textContent = "fetching it from Logitech's catalog... this can take up to 30 seconds";
  const r = await llamar("catalog_save", $("#email").value, indice);
  ocupado(btn, false);
  el.className = "";
  await pintarGuardado(el, r);
}

/* THE ONE PLACE THAT RENDERS THE OUTCOME OF A SAVE, shared by the catalog
 * Save button and by Resume, because they publish the SAME contract
 * (`Api._materializar_paquete`).
 *
 * WHAT CHANGED AND WHY. `catalog_save()` used to answer `ok=true` with
 * `materializado=false` when the protocol was missing, and this screen read
 * `ok` as "it saved" and said "saved" -- over a device that had NOT been
 * written anywhere and that therefore appeared in no list to send to the
 * remote. That is the whole bug the user hit: "it says yes and it doesn't
 * show up in the list". Now `ok` means one thing only: the device folder
 * exists and Control lists it.
 *
 * The failure is NOT all one colour, and that is the second half of telling
 * the truth. `bajado=true` means the package DID come down and is on this
 * computer -- an "aviso" (something is missing, nothing is lost, here is the
 * way out). Without it, nothing came down at all -- an "alarma". */
async function pintarGuardado(el, r) {
  if (r.ok) {
    // `aviso` says, in plain language, what happened. The path it used to
    // come with (`r.target`) is an internal cache location with nothing a
    // person can do with it, so it isn't shown.
    el.innerHTML = nota("ok", esc(r.warning || ""));
  } else {
    el.innerHTML = nota(
      r.downloaded ? "aviso" : "alarma",
      (r.downloaded ? "<b>Downloaded, but NOT saved as a device.</b><br>" : "") +
        esc(r.error || "").replace(/\n/g, "<br>")
    );
  }
  // Both lists have to reflect it without reloading anything: a success adds
  // a row to "Ready to add", and a download-without-protocol adds a row to
  // "Downloaded, waiting for a protocol" (and a success REMOVES it from
  // there, which is what makes Resume feel finished).
  await cargarLocales();
  await cargarPendientes();
}

/* ------- downloaded packages that never became a device ---------------- *
 *
 * `catalog_pending()` measures readiness the same way `catalog_resume()`
 * will decide it -- a dry `materialize()` against the library as it is right
 * now -- so the button the user sees and the answer they get cannot
 * disagree. Nothing here is frozen at download time: a package that is not
 * ready today turns ready the moment an .ir of its family is imported. */
async function cargarPendientes() {
  const tarjeta = $("#tarjeta-pendientes");
  const el = $("#pendientes");
  const r = await llamar("catalog_pending");
  if (!r.ok) {
    tarjeta.hidden = false;
    el.className = "";
    el.innerHTML = nota("aviso", esc(r.error));
    return;
  }
  // Empty is the normal state: the card only exists when there is something
  // stranded, so an empty one would be noise on every healthy screen.
  if (!r.items.length) {
    tarjeta.hidden = true;
    el.innerHTML = "";
    return;
  }
  tarjeta.hidden = false;
  el.className = "";
  el.innerHTML =
    "<table><thead><tr><th>Device</th><th>Commands</th><th>Downloaded</th>" +
    "<th>What's missing</th><th></th></tr></thead><tbody>" +
    r.items.map(filaPendiente).join("") +
    "</tbody></table>";
  el.querySelectorAll("[data-retomar]").forEach((b) =>
    b.addEventListener("click", () => retomar(b, b.dataset.retomar))
  );
}

function filaPendiente(i) {
  const falta = i.ready
    ? chip("its protocol is on disk now", "si")
    : (i.missing || []).length
      ? (i.missing || [])
          .map((f) => chip((f || "(unrecognized KeyCode)") + " -- missing", "no"))
          .join("")
      : chip(i.reason || "its protocol is not on disk", "no");
  return (
    '<tr class="' + (i.ready ? "" : "no-aplicable") +
    '" data-fila-pendiente="' + esc(i.paquete_id) + '">' +
    "<td>" + esc(i.name || i.paquete_id) + "</td>" +
    "<td>" + (i.commands == null ? "?" : i.commands) + "</td>" +
    "<td>" + esc((i.downloaded_at || "").slice(0, 10) || "--") + "</td>" +
    "<td>" + falta + "</td>" +
    '<td class="acciones"><button class="accion chico' +
    (i.ready ? " primaria" : "") +
    '" data-retomar="' + esc(i.paquete_id) + '">Resume</button></td>' +
    "</tr>"
  );
}

/* Resume is offered even when the package is NOT ready, on purpose: pressing
 * it then answers with the exact protocol that is missing and how to get it,
 * which is a better dead end than a greyed-out button that explains nothing.
 * The outcome goes to `#pendientes-aviso`, outside the node
 * `cargarPendientes()` repaints -- the same lesson the delete confirmation
 * cost once, when a note that WORKED landed on a detached element. */
async function retomar(btn, paqueteId) {
  if (btn) ocupado(btn, true, "resuming...");
  const r = await llamar("catalog_resume", paqueteId);
  if (btn) ocupado(btn, false);
  await pintarGuardado($("#pendientes-aviso"), r);
}

/* ------------------------------------------- import an .ir by hand ----- *
 *
 * The other way to get a device, and the only one that works when the
 * protocol isn't on disk yet. Doesn't use the internet or the account: a
 * Flipper Zero `.ir` brings the waveform measured in microseconds.
 *
 * The name is validated LIVE against the two real checks (that a font can
 * draw it, and that the glyph table can write it) -- the same checks the
 * importer runs before touching disk.
 */
let IR_RUTA = null;
let IR_TIMER = null;

$("#btn-ir-elegir").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "opening...");
  const r = await llamar("choose_file");
  ocupado(b, false);
  if (!r.ok || !r.path) return;
  await analizarIr(r.path);
});

/* The real body of the "pick an .ir" step: everything except opening the
 * native macOS dialog. Kept separate so verification can exercise EXACTLY
 * this path (window.evaluate_js('analizarIr(ruta)')) without having to
 * automate a system dialog that isn't part of the app. */
async function analizarIr(ruta) {
  IR_RUTA = ruta;
  $("#ir-archivo").textContent = ruta;
  $("#ir-archivo").className = "";
  const a = await llamar("catalog_ir_analyze", IR_RUTA);
  const caja = $("#ir-analisis");
  if (!a.ok) {
    caja.innerHTML = nota("aviso", esc(a.error));
    $("#ir-datos").hidden = true;
    return;
  }
  const res = a.summary;
  caja.innerHTML =
    nota(
      res.soportados ? "ok" : "aviso",
      "<b>" + res.soportados + " of " + res.total +
        "</b> commands can be imported." +
        (res.no_soportados
          ? " " + res.no_soportados +
            " can't. <code>raw</code> blocks (which carry the measured " +
            "waveform) are accepted, and so are <code>parsed</code> blocks " +
            "for protocols whose formula is verified against real commands " +
            "(NEC, SIRC, SIRC15); everything else needs to be re-exported " +
            "as <code>raw</code>."
          : "")
    ) +
    nota(
      "info",
      "The <b>label</b> is what you'll see on the control's screen. It comes " +
        "from the <code>.ir</code> file's name, swapping out whatever the " +
        "device can't draw (it has no <code>_</code>, <code>+</code>, or " +
        "<code>Q</code>/<code>X</code>/<code>Z</code>)."
    ) +
    '<div class="marco-lista">' +
    "<table><thead><tr><th>Command</th><th>Type</th><th>Label on the " +
    "control</th><th></th></tr></thead><tbody>" +
    a.commands
      .map(
        (c) =>
          "<tr><td>" + esc(c.name) + "</td><td>" + esc(c.kind) +
          (c.protocolo ? " <span class=\"pista\">" + esc(c.protocolo) + "</span>" : "") +
          "</td><td>" +
          (c.soportado ? "<b>" + esc(c.rotulo || "") + "</b>" : "--") +
          "</td><td>" +
          (c.soportado
            ? chip(c.atomos + " timings", "si")
            : chip(c.reason || "not supported", "no")) +
          ((c.avisos || []).length
            ? '<div class="pista">' + esc(c.avisos.join(" / ")) + "</div>"
            : "") +
          "</td></tr>"
      )
      .join("") +
    "</tbody></table></div>";
  $("#ir-datos").hidden = !res.soportados;
  await validarNombreIr();
}

["#ir-fab", "#ir-mod", "#ir-nombre"].forEach((sel) =>
  $(sel).addEventListener("input", () => {
    clearTimeout(IR_TIMER);
    IR_TIMER = setTimeout(validarNombreIr, 350);
  })
);

async function validarNombreIr() {
  const caja = $("#ir-aviso-nombre");
  const nombre = $("#ir-nombre").value.trim();
  const fab = $("#ir-fab").value.trim();
  const mod = $("#ir-mod").value.trim();
  $("#btn-ir-importar").disabled = true;
  if (!IR_RUTA || !nombre || !fab || !mod) {
    caja.innerHTML = "";
    return;
  }
  const v = await llamar("catalog_ir_validate", IR_RUTA, fab, mod, nombre);
  if (!v.ok && v.error) {
    caja.innerHTML = nota("aviso", esc(v.error));
    return;
  }
  if (v.ok) {
    caja.innerHTML = nota(
      "ok",
      "The control can write " + esc(nombre) + " and the " +
        (v.rotulos || []).length + " labels of its buttons."
    );
    $("#btn-ir-importar").disabled = false;
    return;
  }
  const partes = [];
  (v.rotulos || [])
    .filter((x) => !x.ok)
    .forEach((x) =>
      partes.push(
        "the label <b>" + esc(x.rotulo) + "</b> (from command " +
          esc(x.name) + ") can't be drawn: " + esc(x.reason || "")
      )
    );
  if (!v.font_ok)
    partes.push(
      "no font on the control draws: <b>" +
        esc((v.font_missing || []).join(", ")) + "</b>"
    );
  if (!v.glyphs_ok)
    partes.push(
      "the control has no glyph for: <b>" +
        esc((v.glyphs_missing || []).join(", ")) +
        "</b> (its fonts carry 71 characters, and Q, X and Z aren't among them)"
    );
  if (v.rotulo_volver_ok === false)
    partes.push(
      "the fixed <b>Devices</b> label can't be written either (missing " +
        esc((v.back_label_missing || []).join(", ")) +
        "): those characters ARE in the control's fonts, so this is a bug, not something a download fixes"
    );
  caja.innerHTML = nota("aviso", partes.join("<br>"));
}

$("#btn-ir-importar").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  ocupado(b, true, "importing...");
  const r = await llamar(
    "catalog_ir_import",
    IR_RUTA,
    $("#ir-fab").value.trim(),
    $("#ir-mod").value.trim(),
    $("#ir-nombre").value.trim()
  );
  ocupado(b, false);
  const el = $("#ir-salida");
  el.hidden = false;
  if (!r.ok) {
    el.innerHTML = nota("alarma", esc(r.error));
    return;
  }
  el.innerHTML =
    nota(
      "ok",
      "<b>Imported: " + esc(r.name) + "</b> (" + r.commands + " commands" +
        (r.commands_skipped ? ", " + r.commands_skipped + " skipped" : "") +
        "). Now go to <b>Control</b> to add it: choose it, apply it, and " +
        "the write button only shows up once everything checks out."
    ) + (r.gaps_warning ? nota("aviso", esc(r.gaps_warning)) : "");
  await cargarLocales();
  // THE MOMENT THE STRANDED LIST CHANGES. An imported .ir is how a protocol
  // enters the library, and a package that was "waiting for a protocol" a
  // second ago can be ready now -- with the same protocol, for the whole
  // family. Repainting here is what turns the promise in that card into
  // something the user can see happen.
  await cargarPendientes();
});

/* ============================== CONTROL ==================================== *
 *
 * A single status line up top, one button below. The technical bits
 * (reference, repoints, gate output, the command) live in #detalle-tecnico,
 * collapsed -- not erased, just hidden.
 *
 * The four things that never get sacrificed, in plain language:
 *   1. The gate rules. `pintarZonaAplicar` draws NO button at all while no
 *      device is chosen with a label the control can draw. The gate itself
 *      moved: this screen now QUEUES the addition, and the gate runs once
 *      over the whole queue in `sync_preparar()`. If it doesn't come back
 *      green there, the write button is never created -- see `ejecutarSync`
 *      and `syncRechazar`, which keep the rejection against that exact
 *      batch so repainting can't bring the button back.
 *   2. After recording, the user is asked whether it booted up fine, and the
 *      answer is saved (`preguntarArranqueControl`) -- the same function,
 *      called from Sync, not a second copy that could get softer.
 *   3. The size check is stated in those exact words, never as a bare
 *      "verified" (`responderArranqueControl` uses
 *      `ESTADO.textos.loop_closure` exactly as it comes from Python).
 *   4. Downloading from the catalog still warns on the Catalog screen -- this
 *      doesn't change here.
 *
 * And a fifth, which is a safety one: writing the control's memory NEVER
 * shares a click with preparing it. Sync checks first and only then draws a
 * separate red button, with a warning that it writes the device.
 */

let CATALOGO_LOCAL = [];       // catalog_local().items, cached for the <select>
let DISPOSITIVO_ELEGIDO = null; // the chosen item from control_devices_from_json
let ETIQUETA_VALIDA = false;
let TIMER_ETIQUETA = null;
/* ULTIMA_APLICACION / COMPUERTA_RECHAZO lived here. Both belonged to the
   old "apply straight from this screen" path: there is no per-screen gate
   result to remember any more, because the gate runs in Sync. */
let ESPERANDO_ARRANQUE = false; // there's an unanswered "did it boot up fine?" -- RULE 2
let ZONA_CONGELADA = null;      // combination whose result stays on screen

/* The combination about to be applied. If it changes, the previous rejection
 * no longer applies: it's a different attempt, so the button is offered
 * again. */
function combinacionActual() {
  return JSON.stringify([
    $("#s-json").value,
    DISPOSITIVO_ELEGIDO ? DISPOSITIVO_ELEGIDO.name : null,
    $("#s-nombre").value.trim(),
  ]);
}

/* COMING BACK TO THIS TAB IS NOT A RECONNECT.
 *
 * This used to be `addEventListener("click", actualizarEstadoControl)`: every
 * single time you came back to Control, the screen wiped itself to "asking
 * your control what it has..." and fired the whole ~2-minute flash read
 * again. That is the reported bug -- the connected state was lost on a tab
 * change and you had to press the button again -- and it was worse than
 * losing it: if anything else held the device lock at that moment, Python
 * answered "there is already an operation in progress" and the screen went
 * red, on a remote that was plugged in and working.
 *
 * Now: paint what was LAST MEASURED (it lives in Python, `_ultimo_estado_real`,
 * so it survives anything the DOM does), and re-measure PRESENCE -- a cheap
 * `get_identity`, no flash read. So the state survives the tab change and is
 * still measured every time: unplug the remote, come back, and it stops
 * saying connected because the probe says so, not because a flag flipped. */
$('button.nav[data-p="check"]').addEventListener("click", volverAControl);
/* "Connect" is the ONLY gesture that unfreezes the delete zone: it re-reads
 * the control from scratch, so a previous rejection stops applying. Leaving
 * the tab and coming back is NOT enough -- if it were, RULE 1 could be
 * dodged with two clicks. */
$("#btn-refrescar-control").addEventListener("click", () => {
  if (ESPERANDO_ARRANQUE) return actualizarEstadoControl();
  BORRADO_RECHAZO = null;
  BORRADO_CONGELADO = false;
  CONFIRMANDO = null;
  $("#zona-borrar").innerHTML = "";
  actualizarEstadoControl();
});
$("#btn-ir-catalogo").addEventListener("click", () => {
  $('button.nav[data-p="catalogo"]').click();
});

/* THE fix for "the app lies". This used to call `control_status`, which
 * mixes "is it plugged in" with `_last_devices_snapshot()` -- a CACHED FILE
 * from an old catalog download, read whether or not a remote is connected.
 * That's why the screen could say "your control has 5 devices" with the
 * cable out, and why Refresh appeared to do nothing: it re-read the same
 * file and painted the same sentence.
 *
 * Now it calls `control_estado_real()`, which returns exactly one of three
 * situations and never blends them:
 *   desconectado          -- we did NOT talk to a remote. Whatever is shown
 *                            below is labelled as the last configuration
 *                            THIS PROJECT wrote, never as the device state.
 *   conectado_verdad      -- the remote answered AND its raw flash dumped
 *                            and validated. These numbers are the device.
 *   conectado_sin_verdad  -- the remote answered but what came back isn't a
 *                            valid config. Says the measured reason; does
 *                            NOT invent a device count.
 *
 * Connect calls this again, from scratch. There is no cached answer that
 * survives it.
 *
 * THREE THINGS CHANGED HERE, and they are all the same complaint: the read
 * takes ~2 minutes and the screen pretended it was instant.
 *
 *   1. The button says CONNECT. "Refresh" named neither of the two things it
 *      does -- connect to the remote, and read it.
 *   2. It has a MEASURED progress bar. `control_conectar_iniciar()` starts
 *      the read on a Python thread and `control_conectar_progreso()` reports
 *      bytes that actually arrived, chunk by 16 KiB chunk. Nothing here
 *      advances on a timer: if the remote stalls, the bar stalls.
 *   3. The connected state SURVIVES a tab change. It lives in Python
 *      (`_ultimo_estado_real`), so nothing the DOM does can lose it, and
 *      coming back re-measures PRESENCE with a cheap `get_identity` instead
 *      of re-reading the flash. Unplug the remote and come back and it stops
 *      saying connected -- because it was measured, not because a flag was
 *      cleared.
 *
 * WHERE THE STATE LIVES, and why not here. `ESTADO_REAL` is a mirror for
 * painting. The truth is Python's `_ultimo_estado_real` / `_verdad_actual`,
 * which is also what `_control_blob()` serves to Catalog/Activities/Keys --
 * one copy, so the screen and the blob cannot disagree about which remote is
 * connected. */
let ESTADO_REAL = null;      // mirror of the last measured state, for painting
let CONECTADO_MANDO = false; // shared with Activities/Keys, set only here
let CONECTANDO = false;      // a read is in flight, followed by this screen

/* --- the progress bar ---------------------------------------------------
 *
 * MEASURED, not animated. Every number it shows comes from bytes that came
 * back over USB: `read_flash_baseline.py` prints `LEIDO <n>/<total>` after
 * each 16 KiB chunk lands, `progreso.parsear_linea_lectura` turns that into
 * an event, and `control_conectar_progreso` hands it here. Nothing on this
 * screen moves on a timer -- if the remote stops answering, the bar stops,
 * which is the whole point of not faking it. */
function conectarMostrarBarra(si) {
  const caja = $("#conectar-avance");
  if (caja) caja.hidden = !si;
  if (!si) return;
  const r = $("#conectar-prog-relleno");
  if (r) r.style.width = "0%";
  const t = $("#conectar-prog-txt");
  if (t) t.textContent = "starting...";
}

function conectarPintarAvance(p) {
  const relleno = $("#conectar-prog-relleno");
  if (relleno) relleno.style.width = (p.porcentaje || 0) + "%";
  const txt = $("#conectar-prog-txt");
  if (!txt) return;
  const partes = [(p.porcentaje || 0) + "%"];
  if (p.bytes_totales)
    partes.push(
      miles(p.bytes_leidos || 0) + " of " + miles(p.bytes_totales) + " bytes read"
    );
  if (p.etapa) partes.push(p.etapa);
  partes.push((p.segundos_transcurridos || 0) + "s");
  txt.textContent = partes.join(" - ");
}

function miles(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/* --- coming back to the tab ---------------------------------------------
 *
 * Show what was last measured IMMEDIATELY (no spinner, no read), then
 * re-measure whether the remote is still there. Three cases, and the middle
 * one is the one that keeps this honest:
 *
 *   nothing measured yet   -> full read, same as pressing Connect. There is
 *                             nothing to keep, so keeping nothing is not an
 *                             option.
 *   still plugged in       -> the state stays. No 2-minute re-read to learn
 *                             something we already know.
 *   NOT plugged in any more-> the state drops to disconnected, because the
 *                             probe measured it. Python drops its own
 *                             `_verdad_actual` in the same call, so no other
 *                             screen keeps serving that remote's blob either.
 */
async function volverAControl() {
  if (CONECTANDO) return;             // a read is running: don't touch it
  const rec = await llamar("control_estado_recordado");
  if (!rec.ok || !rec.hay) return actualizarEstadoControl();

  pintarEstadoReal(rec.state, rec.edad_segundos);
  if (rec.state.state !== "conectado_verdad") return;

  const pres = await llamar("control_presencia");
  if (!pres.ok || pres.ocupado || CONECTANDO) return;  // don't downgrade blind
  if (pres.presente) {
    // Measured again, just now. Say so instead of leaving a stale timestamp
    // implied: this line is the difference between "still connected" and
    // "was connected once".
    const d = $("#estado-control-detalle");
    if (d)
      d.innerHTML =
        chip("still plugged in -- checked just now", "si") +
        syncChipsIdentidad(pres.identidad || rec.state.identidad) +
        nota(
          "info",
          "What's below was read from your control's flash " +
            esc(hace(rec.edad_segundos)) +
            ". It's still the same remote and it's still plugged in. Tap " +
            "<b>Connect</b> to read it again."
        );
    return;
  }
  // Gone. This is not a flag going out: a probe measured its absence.
  ESTADO_REAL = null;
  CONECTADO_MANDO = false;
  const linea = $("#estado-control");
  linea.className = "estado-grande";
  linea.textContent = "Your Harmony One isn't plugged in any more.";
  $("#estado-control-detalle").innerHTML = nota(
    "aviso",
    "It was read earlier in this session, but it isn't answering over USB " +
      "now" +
      (pres.reason ? " (" + esc(pres.reason) + ")" : "") +
      ". Nothing below is your control's state. Plug it back in and tap " +
      "<b>Connect</b>."
  );
  $("#tarjeta-elegir").hidden = true;
  $("#tarjeta-aplicar").hidden = true;
  cargarDispositivosDelControl();
  cargarActividades();
  initTeclas();
}

function hace(segundos) {
  const s = Math.max(0, Math.round(segundos || 0));
  if (s < 90) return s + " s ago";
  const m = Math.round(s / 60);
  if (m < 90) return m + " min ago";
  return Math.round(m / 60) + " h ago";
}

/** THE Connect button: the full read, with the bar following it.
 *
 * Never fires a second read on top of a running one. Python refuses too
 * (`control_conectar_iniciar` hands back the SAME job id with
 * `ya_en_curso`), so even if this guard were bypassed there would still be
 * exactly one read -- the two are belt and braces on purpose, because the
 * failure mode is three parallel flash reads on one device. */
async function actualizarEstadoControl() {
  if (CONECTANDO) return;
  const linea = $("#estado-control");
  linea.className = "estado-grande cargando";
  linea.textContent = T(
    "conectando",
    "connecting to your control and reading its memory..."
  );
  $("#estado-control-detalle").innerHTML = "";

  const ini = await llamar("control_conectar_iniciar");
  if (!ini.ok) {
    linea.className = "estado-grande";
    ESTADO_REAL = null;
    CONECTADO_MANDO = false;
    linea.innerHTML = nota("aviso", esc(ini.error));
    return;
  }

  CONECTANDO = true;
  conectarMostrarBarra(true);
  const boton = $("#btn-refrescar-control");
  if (boton) boton.disabled = true;

  let vistos = 0;
  let fallosSeguidos = 0;
  let r = null;
  try {
    for (;;) {
      const p = await llamar("control_conectar_progreso", ini.trabajo_id, vistos);
      if (!p.ok) {
        // One hiccup in the bridge is not a reason to abandon a read that
        // is still running. Several in a row is.
        fallosSeguidos += 1;
        if (fallosSeguidos >= 5) {
          linea.innerHTML = nota(
            "alarma",
            "<b>Lost track of the read</b> (" + esc(p.error) + "). Tap " +
              "<b>Connect</b> to try again."
          );
          return;
        }
        await new Promise((res) => setTimeout(res, 600));
        continue;
      }
      fallosSeguidos = 0;
      vistos = p.total_events;
      conectarPintarAvance(p);
      if (p.terminado) {
        r = p.state ||
          { ok: false, error: p.error_lectura || "the read did not finish" };
        break;
      }
      await new Promise((res) => setTimeout(res, 400));
    }
  } finally {
    // ALWAYS: an exception here cannot be allowed to leave the button dead
    // and the bar spinning on a read that is over.
    CONECTANDO = false;
    conectarMostrarBarra(false);
    if (boton) boton.disabled = false;
  }
  pintarEstadoReal(r);
}

/** Paints one of the three situations. Split out of the read so that
 * `volverAControl()` can repaint the LAST MEASURED one without reading
 * anything -- one painter, so the tab-switch path can never drift into
 * saying something the read path wouldn't.
 *
 * `edadSegundos` is how old that measurement is. It exists so the repaint
 * cannot say "read from the device just now" about a read from four
 * minutes ago -- the whole reason this screen was rewritten is that it was
 * claiming things it had not measured. Fresh read: leave it null. */
function pintarEstadoReal(r, edadSegundos) {
  const linea = $("#estado-control");
  linea.className = "estado-grande";
  ESTADO_REAL = r;

  if (!r || !r.ok) {
    CONECTADO_MANDO = false;
    linea.innerHTML = nota("aviso", esc((r && r.error) || "the read did not finish"));
    $("#tarjeta-elegir").hidden = true;
    $("#tarjeta-aplicar").hidden = true;
    cargarDispositivosDelControl();
    cargarActividades();
    initTeclas();
    return;
  }

  CONECTADO_MANDO = r.state === "conectado_verdad";

  if (r.state === "desconectado") {
    linea.textContent = "Your Harmony One isn't plugged in.";
    // The wording comes from Python (`estado_mando.TEXTO_DESCONECTADO`) for
    // the same reason as the loop-closure text: so it can't be softened by
    // editing only the template.
    $("#estado-control-detalle").innerHTML =
      nota("aviso", esc(r.mensaje)) +
      (r.last_local_config
        ? nota(
            "info",
            "The last thing this project wrote to a control was <b>" +
              esc(r.last_local_config.name || "a change") +
              "</b>, on " +
              esc((r.last_local_config.fecha || "").slice(0, 10)) +
              " (history entry #" + esc(r.last_local_config.write_id) +
              "). That is a record of what THIS APP did, not a reading of " +
              "the device."
          )
        : "");
    $("#tarjeta-elegir").hidden = true;
    $("#tarjeta-aplicar").hidden = true;
    cargarDispositivosDelControl();
    cargarActividades();
    initTeclas();
    return;
  }

  if (r.state === "conectado_sin_verdad") {
    linea.textContent = "Your control answered, but its configuration didn't read back.";
    $("#estado-control-detalle").innerHTML =
      nota("alarma", esc(r.mensaje)) +
      nota(
        "aviso",
        "Nothing is being guessed from this: no device count is shown, " +
          "because none was measured. Unplug it, plug it back in, and tap " +
          "<b>Connect</b>. If it keeps happening, don't write anything to it."
      ) +
      syncChipsIdentidad(r.identidad);
    // Adding a device on top of a config we could not read is exactly the
    // case where the gate has nothing trustworthy to compare against.
    $("#tarjeta-elegir").hidden = true;
    $("#tarjeta-aplicar").hidden = true;
    cargarDispositivosDelControl();
    cargarActividades();
    initTeclas();
    return;
  }

  // conectado_verdad: these numbers came out of the remote's flash.
  linea.textContent = r.mensaje;
  $("#estado-control-detalle").innerHTML =
    chip(
      edadSegundos === null || edadSegundos === undefined
        ? "read from the device just now"
        : "read from the device " + hace(edadSegundos),
      "si"
    ) +
    syncChipsIdentidad(r.identidad) +
    (r.parece_de_fabrica ? chip("looks like the factory configuration") : "");

  $("#tarjeta-elegir").hidden = false;
  pintarSeleccion();
  cargarDispositivosDelControl();
  cargarActividades();
  initTeclas();
}

function syncChipsIdentidad(id) {
  if (!id) return "";
  const fw =
    id.fw_mayor !== null && id.fw_mayor !== undefined
      ? id.fw_mayor + "." + id.fw_menor
      : null;
  return (
    (fw ? chip("firmware " + esc(fw)) : "") +
    (id.config_usada !== null && id.config_usada !== undefined
      ? chip(id.config_usada + " B used of " + id.config_total)
      : "")
  );
}

/* ==================== WHAT THE CONTROL HAS TODAY =========================== *
 *
 * And removing one. The same four hard rules as adding, with not a single
 * exception for removal:
 *
 *   1. THE GATE RULES. The write-to-device button IS NOT DRAWN while
 *      `control_delete` doesn't return `listo=true` (which is the
 *      `nada_se_movio` gate run in Python, not here). And if it says NO,
 *      the "Remove" button LEAVES THE DOM (`.remove()`, not `hidden`) and
 *      the rejection gets recorded in BORRADO_RECHAZO: no repaint brings it
 *      back while the same device is being talked about.
 *   2. After recording, the user is asked whether the control booted up
 *      fine, with the SAME `preguntarArranqueControl` as adding.
 *   3. The size check is stated in those exact words (it comes from Python).
 *   4. Confirmation BEFORE deleting, spelling out exactly what's lost.
 *
 * And a fifth one that belongs to this screen: `borrable` is NOT decided by
 * the JS. It comes from Python, which derives it from the two declared
 * aborts in `delete_device.py`. If the JS ignored it and called anyway,
 * `control_delete` rejects it again.
 */

let DISPOSITIVOS = [];          // last control_list_devices()
let BORRADO_RECHAZO = null;     // {k1, html} -- see RULE 1
let BORRADO_CONGELADO = false;  // there's a delete result that isn't repainted
let CONFIRMANDO = null;         // k1 with the confirmation open

async function cargarDispositivosDelControl() {
  const caja = $("#lista-dispositivos");
  if (!caja) return;
  $("#tarjeta-dispositivos").hidden = false;
  if (!DISPOSITIVOS.length)
    caja.innerHTML = '<div class="cargando">reading the configuration...</div>';

  const r = await llamar("remote_list_devices");
  if (!r.ok) {
    DISPOSITIVOS = [];
    $("#dispositivos-origen").textContent = "";
    // "Couldn't read what your control has" followed by the path of a file
    // that does not exist is what a clone used to get here. When Python says
    // the reason is that nothing has been read yet, the way forward is drawn
    // instead of the dead end.
    caja.innerHTML = r.primer_uso
      ? notaPrimerUso(r)
      : nota("alarma", "<b>Couldn't read what your control has.</b> " + esc(r.error));
    return;
  }
  DISPOSITIVOS = r.devices || [];
  ULTIMO_LISTADO = r;
  // The heading and the source line say WHICH of the two this is. The old
  // text always claimed "what your control has now" and then admitted in
  // small print that it came from a file -- which is the complaint. Now the
  // heading itself changes, because the difference matters more than the
  // footnote did.
  const enVivo = CONECTADO_MANDO;
  const titulo = $("#tarjeta-dispositivos").querySelector("h3");
  if (titulo)
    titulo.textContent = enVivo
      ? "What your control has now"
      : "What this app last wrote (NOT read from the control)";
  $("#dispositivos-origen").innerHTML = enVivo
    ? "Read straight out of your control's flash a moment ago -- this is the " +
      "device, not a file."
    : "<b>This is not your control's state.</b> Source: " +
      esc((r.referencia || {}).origin || "the saved configuration") +
      ". Plug the control in and tap <b>Connect</b> to read the real thing.";
  pintarListaDispositivos();
}

let ULTIMO_LISTADO = null;

/* The "why it can't be removed" is stated ONCE per reason, below the list --
 * not repeated on every row. With 3 factory devices, repeating it filled
 * half the screen with the same paragraph and buried what you actually
 * could do. */
function explicarNoBorrables() {
  const POR_QUE = {
    ultimo_de_la_hoja_1:
      "<b>The last device left on the menu's first page can't be " +
      "removed.</b> The control would end up with no device to show when " +
      "opening the list, and that state doesn't exist out of the factory. " +
      "Remove another one first, or add a new one.",
    rechazado:
      "<b>One of these was already tried in this session and verification " +
      'said no.</b> The reason is under "View details". It won\'t be ' +
      "offered again until you tap <b>Connect</b>.",
    en_sync:
      "<b>One of these is already on the Sync list to be removed.</b> It's " +
      "still on the control until you run <b>Sync</b>; take it off the Sync " +
      "list if you changed your mind.",
  };
  const motivos = [];
  DISPOSITIVOS.forEach((d) => {
    if (!d.borrable && d.reason && motivos.indexOf(d.reason) === -1)
      motivos.push(d.reason);
  });
  return motivos.map((m) => nota("aviso", POR_QUE[m] || esc(m))).join("");
}

function pintarListaDispositivos() {
  const caja = $("#lista-dispositivos");
  if (!DISPOSITIVOS.length) {
    caja.innerHTML = '<div class="vacio">no device found</div>';
    return;
  }
  caja.innerHTML = DISPOSITIVOS.map((d) => {
    const insignia = d.de_fabrica
      ? chip("came with the control")
      : chip("you added this one", "si");
    let derecha;
    if (d.borrable) {
      derecha =
        '<button class="accion chico" data-borrar="' + d.k1 + '">Remove</button>';
    } else if (d.reason === "rechazado") {
      derecha = '<span class="vacio">verification blocked it</span>';
    } else if (d.reason === "en_sync") {
      derecha = '<span class="vacio">queued for removal in Sync</span>';
    } else {
      // RULE 1 applied to removal: no button, not even disabled. It doesn't exist.
      derecha = '<span class="vacio">it is the last one left in the list</span>';
    }
    return (
      '<div class="disp-item"><div class="disp-izq">' +
      '<div class="disp-nombre">' + esc(d.name) +
      (d.incomplete_glyphs
        ? ' <span class="chip no">name not fully readable</span>'
        : "") +
      "</div>" +
      '<div class="disp-datos">' + d.commands + " commands &middot; " + insignia +
      "</div></div>" +
      '<div class="disp-der">' + derecha + "</div></div>"
    );
  }).join("") + explicarNoBorrables();

  caja.querySelectorAll("[data-borrar]").forEach((b) => {
    b.addEventListener("click", () => confirmarBorrado(Number(b.dataset.borrar), b));
  });

  // RULE 2 / frozen: if there's an unanswered boot-up question, or a delete
  // result on screen, that zone isn't touched.
  if (ESPERANDO_ARRANQUE || BORRADO_CONGELADO) return;
  if (BORRADO_RECHAZO) {
    $("#zona-borrar").innerHTML = BORRADO_RECHAZO.html;
    return;
  }
  if (CONFIRMANDO === null) $("#zona-borrar").innerHTML = "";
}

/* RULE 4: it asks first, spelling out exactly what's lost. The "Remove"
 * button is taken out of the DOM while confirming, so two open paths never
 * coexist. */
function confirmarBorrado(k1, boton) {
  const d = DISPOSITIVOS.find((x) => x.k1 === k1);
  if (!d) return;
  CONFIRMANDO = k1;
  if (boton) boton.remove();
  // WHAT GETS LOST: Python says it, not the JS. `se_pierde` comes from
  // `actividades.frase_humana()` -- the SAME function `delete_device.py` prints
  // when it runs for real, so the confirmation can't promise one thing and
  // have the tool do another. If it doesn't arrive for some reason, this
  // falls back to generic text instead of showing an empty list.
  const sePierde =
    (d.se_pierde && d.se_pierde.length
      ? d.se_pierde
      : [
          esc(d.name) + " disappears from the device list, along with its " +
            d.commands + " commands and its screen.",
        ]
    )
      .map((f) => "<li>" + esc(f) + "</li>")
      .join("");
  $("#zona-borrar").innerHTML =
    nota(
      "aviso",
      "<b>Are you sure you want to remove " + esc(d.name) + " from the control?</b>" +
        "<ul style='margin:8px 0 0 18px;padding:0'>" +
        sePierde +
        "<li>The other " + (DISPOSITIVOS.length - 1) + " devices stay " +
        "intact: that's verified one by one before offering to record.</li>" +
        "</ul><p style='margin:10px 0 0'>The control isn't touched yet, and " +
        "won't be by this button: the removal goes on the <b>Sync</b> list " +
        "with everything else. Nothing is written until you run Sync, and " +
        "even then only if the check passes.</p>"
    ) +
    '<div class="fila">' +
    '<button class="accion peligro" id="btn-borrar-si">Yes, remove ' + esc(d.name) + "</button>" +
    '<button class="accion" id="btn-borrar-no">Better not</button>' +
    "</div><div id='resultado-borrar'></div>";
  $("#btn-borrar-si").addEventListener("click", () => borrarDelControl(k1));
  $("#btn-borrar-no").addEventListener("click", () => {
    CONFIRMANDO = null;
    $("#zona-borrar").innerHTML = "";
    pintarListaDispositivos();
  });
}

/* Now this ADDS the removal to the Sync list instead of preparing and
 * writing on its own. The gate still runs -- once, in `sync_preparar()`,
 * over the whole batch -- and the write still only happens from Sync.
 * `borrable` is still decided by Python, not here, and if the JS enqueued
 * something Python won't do, `cambios.aplicar_todos()` fails the batch and
 * nothing is written. */
async function borrarDelControl(k1) {
  const d = DISPOSITIVOS.find((x) => x.k1 === k1) || {};
  const si = $("#btn-borrar-si");
  const no = $("#btn-borrar-no");
  const res = $("#resultado-borrar");
  ocupado(si, true, "adding to Sync...");
  if (no) no.remove();

  const r = await agregarCambio(
    "remove_device",
    { k1: k1 },
    "Remove '" + (d.name || "device " + k1) + "'" +
      (d.commands ? " (" + d.commands + " commands)" : "")
  );
  CONFIRMANDO = null;

  if (!r.ok) {
    rechazarBorrado(
      k1,
      nota("alarma", "<b>Couldn't queue that removal.</b> " + esc(r.error))
    );
    return;
  }
  if (si) si.remove();
  res.innerHTML = nota(
    "ok",
    "<b>Added to Sync.</b> " + esc(d.name) + " will be removed when you run " +
      "<b>Sync</b>. Nothing has been written to the control."
  );
  // The row shouldn't keep offering "Remove" for something already queued.
  DISPOSITIVOS = DISPOSITIVOS.map((x) =>
    x.k1 === k1 ? Object.assign({}, x, { borrable: false, reason: "en_sync" }) : x
  );
  BORRADO_CONGELADO = true;
  pintarListaDispositivos();
}

/* Removes the button from the DOM and leaves the alarm. While the same
 * device is being talked about, no repaint offers to remove it again. */
function rechazarBorrado(k1, html) {
  BORRADO_RECHAZO = { k1: k1, html: html };
  $("#zona-borrar").innerHTML = html;
  DISPOSITIVOS = DISPOSITIVOS.map((d) =>
    d.k1 === k1 ? Object.assign({}, d, { borrable: false, reason: "rechazado" }) : d
  );
  pintarListaDispositivos();
}

function pintarSeleccion() {
  const cajaSin = $("#sin-catalogo");
  const cajaCon = $("#con-catalogo");
  if (!cajaSin || !cajaCon) return; // the Control tab may not be mounted yet
  const hay = CATALOGO_LOCAL.length > 0;
  cajaSin.hidden = hay;
  cajaCon.hidden = !hay;
  if (!hay) {
    $("#tarjeta-aplicar").hidden = true;
    return;
  }
  const sel = $("#s-json");
  const previo = sel.value;
  // THE DEAD END, CLOSED AT ITS SOURCE. A device with no protocol timing is
  // offered here, accepted, queued -- and only refused at Sync, three
  // screens later, which is exactly the trip the user described. It stays
  // VISIBLE (hiding it would just make the device the user downloaded look
  // like it vanished) but it cannot be selected, and the option itself says
  // what is missing.
  sel.innerHTML =
    '<option value="">pick one...</option>' +
    CATALOGO_LOCAL.map((i) => {
      const a = aplicabilidad(i);
      return (
        '<option value="' + esc(i.config_json) + '"' +
        (a.apto ? "" : " disabled") + ">" +
        esc((i.fabricante || "?") + " " + (i.modelo || "")) +
        // Where it came from: the SAME device can exist via two different
        // paths (from the catalog and captured before), and without this
        // the two options would read the same.
        esc(
          " -- " + (ORIGEN_TEXTO[i.origin] || i.origin || "?") +
            (i.commands === null ? "" : ", " + i.commands + " commands") +
            (a.apto
              ? ""
              : a.absent
                ? "  [needs the timing for " + a.absent + " -- see Catalog]"
                : "  [missing its protocol timing -- see Catalog]")
        ) +
        "</option>"
      );
    }).join("");
  // A remembered selection is only restored if it is still selectable: the
  // device may have been the applicable one last repaint and not be now.
  sel.value =
    previo &&
    CATALOGO_LOCAL.some((i) => i.config_json === previo && aplicabilidad(i).apto)
      ? previo
      : "";
  sel.dispatchEvent(new Event("change"));
}

$("#s-json").addEventListener("change", async () => {
  const ruta = $("#s-json").value;
  DISPOSITIVO_ELEGIDO = null;
  $("#s-disp-envoltorio").hidden = true;
  $("#s-disp").innerHTML = "";
  $("#resumen-cambio").innerHTML = "";
  // An unanswered boot-up question, or the result of the last thing applied
  // (with its rollback), doesn't get hidden by a repaint.
  if (!ESPERANDO_ARRANQUE && !ZONA_CONGELADA) $("#tarjeta-aplicar").hidden = true;
  if (!ruta) return;
  $("#resumen-cambio").innerHTML = '<div class="cargando">reading the file...</div>';
  const r = await llamar("remote_devices_from_json", ruta);
  if ($("#s-json").value !== ruta) return; // the user already picked something else
  if (!r.ok || !r.items.length) {
    $("#resumen-cambio").innerHTML = nota(
      "aviso",
      esc(r.error || "that file doesn't bring any device")
    );
    return;
  }

  // This file brings ALL the devices captured in that run (not just the new
  // one). The backend only guesses which one is "the one you were looking
  // for" -- the one matching the manufacturer+model you asked for in
  // Catalog -- so you're not forced to pick among 5 devices when you really
  // only added one.
  const entrada = CATALOGO_LOCAL.find((i) => i.config_json === ruta);
  let objetivo = entrada
    ? r.items.find(
        (i) => i.fabricante === entrada.fabricante && i.modelo === entrada.modelo
      )
    : null;
  if (!objetivo && r.items.length === 1) objetivo = r.items[0];

  if (objetivo) {
    $("#s-disp-envoltorio").hidden = true;
    elegirDispositivo(objetivo);
  } else {
    // Couldn't guess which one is the new one: let the user pick, as a fallback.
    $("#s-disp-envoltorio").hidden = false;
    $("#s-disp").innerHTML =
      '<option value="">pick one...</option>' +
      r.items
        .map(
          (i) =>
            '<option value="' + esc(i.name) + '">' + esc(i.name) +
            " -- " + i.commands + " commands</option>"
        )
        .join("");
    $("#resumen-cambio").innerHTML = nota(
      "aviso",
      "Couldn't guess which of the " + r.items.length +
        " devices in that file is the one you added. Pick it below."
    );
  }
});

$("#s-disp").addEventListener("change", async () => {
  const r = await llamar("remote_devices_from_json", $("#s-json").value);
  if (!r.ok) return;
  const elegido = r.items.find((i) => i.name === $("#s-disp").value);
  if (elegido) elegirDispositivo(elegido);
});

function elegirDispositivo(item) {
  DISPOSITIVO_ELEGIDO = item;
  if (!$("#s-nombre").value.trim()) {
    $("#s-nombre").value = (item.fabricante || item.name || "").split(" ")[0];
  }
  validarEtiquetaControl();
  pintarResumenCambio();
}

$("#s-nombre").addEventListener("input", () => {
  clearTimeout(TIMER_ETIQUETA);
  TIMER_ETIQUETA = setTimeout(() => {
    validarEtiquetaControl();
    pintarResumenCambio();
  }, 300);
});

async function validarEtiquetaControl() {
  const v = $("#s-nombre").value.trim();
  const el = $("#s-etiqueta-aviso");
  ETIQUETA_VALIDA = false;
  if (!v) {
    el.innerHTML = "";
    return;
  }
  const r = await llamar("remote_validate_label", v);
  if ($("#s-nombre").value.trim() !== v) return; // it changed while we were waiting
  if (!r.ok) {
    el.innerHTML = nota("aviso", esc(r.error));
  } else if (!r.dibujable) {
    el.innerHTML = nota(
      "alarma",
      "<b>The control can't write that name.</b> It's missing the letters <b>" +
        esc(r.missing.join(", ")) + "</b> -- try without them."
    );
  } else {
    ETIQUETA_VALIDA = true;
    el.innerHTML = r.warning ? nota("aviso", esc(r.warning)) : "";
  }
  pintarZonaAplicar();
}

/* What the device about to be added is going to bring WIRED. Read out of
 * the very file `add_device.py` is going to walk, so it is not a guess:
 * the order of that file's command list is the `k2` order. Kept in a
 * variable because the Sync line uses the same sentence -- the numbers the
 * user reads here and the numbers on the Sync list have one source.
 * `null` = not asked yet or couldn't be built; the UI then says nothing
 * about keys rather than promising something. */
let PLANTILLA_PREVIA = null;

function plantillaPreviaTexto() {
  // The sentence is WRITTEN IN PYTHON (`Api._frase_plantilla`) and used
  // verbatim, for the same reason the obligatory texts are: two places
  // wording the same fact drift, and then the Sync list says less than the
  // screen the change came from.
  return (PLANTILLA_PREVIA && PLANTILLA_PREVIA.frase) || "";
}

async function pintarResumenCambio() {
  const el = $("#resumen-cambio");
  if (!DISPOSITIVO_ELEGIDO) {
    el.innerHTML = "";
    PLANTILLA_PREVIA = null;
    pintarZonaAplicar();
    return;
  }
  const nombre = $("#s-nombre").value.trim();
  const cabecera =
    "<b>About to add:</b> " + esc(nombre || "(needs a name)") +
    " -- " + DISPOSITIVO_ELEGIDO.commands + " commands (" +
    esc(DISPOSITIVO_ELEGIDO.fabricante || "") + " " +
    esc(DISPOSITIVO_ELEGIDO.modelo || "") + ")";
  el.innerHTML = nota("ok", cabecera);
  pintarZonaAplicar();

  // THE KEYS IT WILL BRING. Asked here, once, and shown BEFORE the user
  // decides -- the whole point of the request is that going into the
  // device just works, so how many keys that means has to be visible at
  // the moment of adding, not discovered afterwards by pressing them.
  const ruta = $("#s-json").value;
  const cual = DISPOSITIVO_ELEGIDO.name;
  const r = await llamar("device_template_preview", ruta, cual);
  if (
    !DISPOSITIVO_ELEGIDO ||
    DISPOSITIVO_ELEGIDO.name !== cual ||
    $("#s-json").value !== ruta
  ) {
    return; // the user already picked something else
  }
  PLANTILLA_PREVIA = r.ok ? r : null;
  if (!r.ok) {
    // No promise is made. The device can still be added; it just arrives
    // with its page empty, exactly as it did before, and that is said.
    el.innerHTML =
      nota("ok", cabecera) +
      nota(
        "aviso",
        "<b>Its keys won't be bound automatically.</b> " + esc(r.error) +
          " The device still gets added, and the <b>Keys</b> tab shows, " +
          "device by device, what is bound and what is not."
      );
    return;
  }
  el.innerHTML =
    nota("ok", cabecera) +
    nota(
      "info",
      "<b>" + r.n_ligadas + " of the " + r.roles_totales +
        " standard keys will be bound on its own page</b>, so going into it " +
        "in Devices just works." +
        (r.n_missing
          ? " <b>Not these " + r.n_missing + ":</b> " +
            r.missing
              .map((f) => esc(f.key) + " (no " + esc(f.rol) + ")")
              .join(", ") +
            " -- this device doesn't have those commands."
          : "")
    );
}

/* Without a chosen device + a label the control can actually draw, the
 * button DOES NOT EXIST. It no longer writes anything: it puts the addition
 * on the Sync list. The gate hasn't been softened -- it moved to
 * `sync_preparar()`, where it runs once over everything that's queued, and
 * the write button still only appears if it comes back green. */
function pintarZonaAplicar() {
  const tarjeta = $("#tarjeta-aplicar");
  if (!tarjeta) return;
  // RULE 2: if there's an unanswered boot-up question, this zone isn't
  // touched. Typing another letter in the name can't erase that question.
  if (ESPERANDO_ARRANQUE) return;
  // And the result of the last thing queued doesn't erase itself either.
  if (ZONA_CONGELADA && ZONA_CONGELADA === combinacionActual()) return;
  ZONA_CONGELADA = null;
  if (!DISPOSITIVO_ELEGIDO || !ETIQUETA_VALIDA) {
    tarjeta.hidden = true;
    $("#zona-aplicar").innerHTML = "";
    return;
  }
  tarjeta.hidden = false;
  $("#zona-aplicar").innerHTML =
    '<button class="accion primaria grande" id="btn-aplicar">Add to Sync</button>' +
    '<p class="sub chico">This doesn\'t touch your control. It goes on the ' +
    "Sync list; the check and the write happen there, for everything at " +
    "once.</p>" +
    '<div id="resultado-aplicar"></div>';
  $("#btn-aplicar").addEventListener("click", aplicarAlControl);
}

async function aplicarAlControl() {
  const btn = $("#btn-aplicar");
  const res = $("#resultado-aplicar");
  const nombre = $("#s-nombre").value.trim();
  ocupado(btn, true, "adding to Sync...");

  const r = await agregarCambio(
    "add_device",
    {
      config_json: $("#s-json").value,
      name: nombre,
      device: DISPOSITIVO_ELEGIDO ? DISPOSITIVO_ELEGIDO.name : null,
    },
    "Add '" + nombre + "'" +
      (DISPOSITIVO_ELEGIDO
        ? " -- " + DISPOSITIVO_ELEGIDO.commands + " commands (" +
          (DISPOSITIVO_ELEGIDO.fabricante || "") + " " +
          (DISPOSITIVO_ELEGIDO.modelo || "") + ")"
        : "") +
      // The SAME sentence the card above is showing, so the Sync list
      // doesn't quietly say less than the screen the change came from.
      plantillaPreviaTexto()
  );
  ocupado(btn, false);

  if (!r.ok) {
    // Same three-way split as Sync: queueing can fail because a value is
    // missing (a tool's own check) or because the app broke. Neither of them
    // is verification protecting the control, and neither may claim to be.
    res.innerHTML = notaFallo(r, "trailer");
    return;
  }
  btn.remove();
  ZONA_CONGELADA = combinacionActual();
  res.innerHTML = nota(
    "ok",
    "<b>Added to Sync.</b> " + esc(nombre) +
      " will be added when you run <b>Sync</b>" +
      (PLANTILLA_PREVIA && PLANTILLA_PREVIA.ok
        ? ", with " + PLANTILLA_PREVIA.n_ligadas +
          " of its keys already bound on its own page"
        : "") +
      ". Nothing has been written to " +
      "the control yet, and nothing will be until the check passes there."
  );
}

/* `caja` is the container where the result goes. By default the "add" one;
 * the REMOVE path passes its own. It's the same code for both: there's no
 * second, possibly laxer, implementation of recording. */
async function grabarAhora(r, caja) {
  const res = caja || $("#resultado-aplicar");
  res.innerHTML = '<div class="cargando">recording to the control... don\'t unplug it</div>';
  const gr = await llamar(
    "remote_record",
    r.file,
    r.referencia.blob,
    r.gate.repoints_int,
    "GRABAR",
    r.name,
    r.commands
  );
  if (!gr.ok) {
    res.innerHTML = nota("alarma", "<b>Didn't record.</b> " + esc(gr.error));
    return;
  }
  res.innerHTML = gr.returncode === 0
    ? nota(
        "aviso",
        "<b>Write was sent (result 0).</b> That only says the software " +
          "didn't fail -- it still needs to be confirmed that the control booted up fine."
      )
    : nota("alarma", "<b>Finished with an error (code " + gr.returncode + ").</b>");
  preguntarArranqueControl(gr.write_id, r, res);
  cargarHistorial();
}

async function registrarManualControl(r, caja) {
  const res = caja || $("#resultado-aplicar");
  const gr = await llamar(
    "remote_register_manual_recording",
    r.file,
    r.referencia.blob,
    r.gate.repoints_int,
    0,
    "recorded by hand from the terminal",
    r.name,
    r.commands
  );
  if (!gr.ok) {
    res.innerHTML += nota("alarma", esc(gr.error));
    return;
  }
  res.innerHTML += nota("ok", "Logged in the history as #" + gr.write_id + ".");
  preguntarArranqueControl(gr.write_id, r, res);
  cargarHistorial();
}

/* RULE 2: after recording (via the app or by hand) the user is asked
 * whether it booted up fine, and the answer is saved -- a result of 0 isn't
 * enough. Applies the same to adding and to removing: it's the same code,
 * with whatever box gets passed in. The nodes are looked up INSIDE `res`
 * (not by a global id) precisely so both paths can coexist without
 * stepping on each other. */
function preguntarArranqueControl(id, r, caja) {
  const res = caja || $("#resultado-aplicar");
  ESPERANDO_ARRANQUE = true;
  res.innerHTML +=
    nota(
      "aviso",
      "<b>Did the control boot up fine?</b><br>Unplug it and look at the screen. The " +
        "write finishing without error doesn't prove the control ended up usable -- " +
        "in this project there was already a time it \"went fine\" and the control " +
        "ended up in a boot loop. Only looking at the screen confirms this."
    ) +
    '<div class="fila"><button class="accion primaria" data-arranco="si">Yes, it booted up</button>' +
    '<button class="accion peligro" data-arranco="no">It didn\'t boot up</button></div>' +
    '<div data-respuesta-arranque></div>';
  const el = res.querySelector("[data-respuesta-arranque]");
  res
    .querySelector('[data-arranco="si"]')
    .addEventListener("click", () => responderArranqueControl(id, true, r, el));
  res
    .querySelector('[data-arranco="no"]')
    .addEventListener("click", () => responderArranqueControl(id, false, r, el));
}

/* RULE 3: the size check is stated in those exact words -- never as a bare
 * "verified". The text comes from Python (ESTADO.textos), not from here. */
async function responderArranqueControl(id, arranco, r, caja) {
  const rr = await llamar(
    "history_confirm_startup",
    id,
    arranco,
    arranco ? "the control booted up fine" : "the control did NOT boot up"
  );
  const el = caja || $("[data-respuesta-arranque]");
  if (!rr.ok) {
    el.innerHTML = nota("alarma", esc(rr.error));
    return; // still unanswered: the zone stays frozen on purpose
  }
  ESPERANDO_ARRANQUE = false;
  // The question is answered: its two buttons LEAVE the DOM, so the answer
  // can't be given twice and the screen doesn't keep asking something that
  // was already answered. The question text stays as the record of it.
  const boton = el.parentNode && el.parentNode.querySelector('[data-arranco]');
  const filaBotones = boton && boton.closest(".fila");
  if (filaBotones) filaBotones.remove();
  // Whatever gets answered stays on screen (especially the rollback
  // command): it doesn't repaint itself away, nor by touching something
  // else on the screen.
  if (r && r.screen === "sync") {
    // A Sync write covers several screens at once, so there is no single
    // zone to freeze: the answer stays in the Sync modal, which is where
    // it was asked, and the modal can be closed once it's answered.
    ZONA_CONGELADA = null;
  } else if (r && r.screen === "activities") {
    // the change was in Activities: the Control screen has nothing to
    // freeze, and `combinacionActual()` there would be a signature of
    // something else.
    ACT_CONGELADA = true;
  } else if (r && r.accion === "erase") {
    BORRADO_CONGELADO = true;
  } else {
    ZONA_CONGELADA = combinacionActual();
  }
  if (!arranco) {
    if (rr.ofrecer_rollback) {
      const rb = await llamar("history_command_rollback", rr.ofrecer_rollback.id);
      el.innerHTML =
        nota(
          "alarma",
          "<b>Saved as failed.</b> The last one confirmed good is #" +
            rr.ofrecer_rollback.id + "."
        ) +
        (rb.ok
          ? "<pre>cd " + esc(rb.cwd) + "\n" + esc(rb.command) + "</pre>"
          : nota("aviso", esc(rb.error)));
    } else {
      el.innerHTML = nota(
        "alarma",
        "<b>Saved as failed.</b> There's no earlier entry confirmed good to " +
          "go back to."
      );
    }
    el.innerHTML += nota(
      "aviso",
      "When you're done, tap <b>Connect</b> above to re-read the control."
    );
    cargarHistorial();
    return;
  }

  el.innerHTML = nota(
    "ok",
    r && r.screen === "activities"
      ? "Saved. The activity “" + esc(r.name) + "” is left the way you set it."
      : "Saved. The control is left with " + esc(r.name) + "."
  );
  const cl = await llamar("remote_loop_closure", r.file);
  if (cl.ok) {
    // The quick check is SIZE, and it says so. What changed is that the
    // old "there is nothing to compare against" is no longer true: the raw
    // flash CAN be read back and its sha256 compared, so the real
    // byte-for-byte check is offered here as an explicit, optional step
    // with its cost stated up front -- never claimed as impossible.
    el.innerHTML += nota(
      cl.coincide ? "ok" : "aviso",
      "<b>Size check:</b> " +
        (cl.coincide
          ? "the control reports having used the same number of bytes as the file that was sent."
          : "the control reports " + cl.declara + " B and the file has " +
            cl.tamano_archivo + " B -- they don't match, worth a look.") +
        '<br><span class="vacio">' +
        esc(cl.advertencia || T("size_only", "")) +
        "</span>"
    );
    if (cl.byte_a_byte === true) {
      el.innerHTML += notaByteAByte(cl.coincide !== false, cl);
    } else if (cl.byte_a_byte_disponible) {
      const seg = cl.byte_a_byte_segundos || 136;
      el.innerHTML +=
        '<div data-byte-a-byte>' +
        '<div class="fila"><button class="accion" id="btn-byte-a-byte">' +
        "Check byte for byte (~" + esc(seg) + " s)</button></div>" +
        '<div class="sync-parrafo">' +
        esc(T("verificacion_opcional", cl.loop_closure || "")) +
        "</div></div>";
      const bb = el.querySelector("#btn-byte-a-byte");
      if (bb)
        bb.addEventListener("click", () => verificarByteAByte(bb, r.file, el));
    }
  }
  cargarHistorial();
  actualizarEstadoControl();
}

/* The real loop closure, byte for byte. It is a READ (`sync_verificar_grabado`
 * dumps the flash and compares sha256), it takes about 136 s, and it is
 * never fired on its own -- the button says the cost before it is clicked. */
function notaByteAByte(coincide, v) {
  return nota(
    coincide ? "ok" : "alarma",
    "<b>Byte-for-byte check:</b> " +
      esc(
        v.linea ||
          (coincide
            ? "what's on the remote is exactly the file that was written."
            : "the remote's flash does NOT match the file that was written.")
      ) +
      (v.leido_sha256
        ? '<br><span class="vacio">read ' + esc(v.leido_sha256) + "<br>file " +
          esc(v.esperado_sha256 || "") + "</span>"
        : "")
  );
}

async function verificarByteAByte(btn, archivo, caja) {
  const destino = caja.querySelector("[data-byte-a-byte]") || caja;
  ocupado(btn, true, "reading the flash... this takes a couple of minutes");
  const v = await llamar("sync_verificar_grabado", archivo || "");
  ocupado(btn, false);
  if (!v.ok) {
    destino.insertAdjacentHTML(
      "beforeend",
      nota(
        "aviso",
        "<b>Couldn't read the flash back.</b> " + esc(v.error) +
          "<br>Nothing was written by this: it is a read that didn't finish."
      )
    );
    return;
  }
  // The offer (button + what it costs) is replaced by the answer: it does
  // not stay on screen inviting a second 136 s read of the same thing.
  destino.innerHTML = notaByteAByte(!!v.coincide, v);
}

/* "View details": the technical bits, collapsed, not erased. Covers both a
 * success (r.generar/r.gate/r.command/r.referencia) and a failure,
 * where generar()/compuerta() sit inside r.technical_detail. */
function pintarDetalleTecnico(r) {
  const el = $("#detalle-tecnico");
  if (!el) return;
  if (!r) {
    el.innerHTML = '<span class="vacio">Nothing has been applied in this session yet.</span>';
    return;
  }
  const dt = r.technical_detail || {};
  const gen = r.generar || dt.generar || dt.erase || dt.listar;
  const comp = r.gate || dt.gate;
  let html = "";
  // `sync_preparar()` now puts the failed step's own technical detail in a
  // FLAT `detalle_tecnico` (`_detalle_del_paso_fallido()`: etiqueta/tipo/
  // clase/motivo/stderr/stdout/traza) -- a different shape from the nested
  // `{generar, compuerta}` this function was written for. Both are handled:
  // reading the flat one as if it were nested would silently print nothing,
  // which is how a technical tab goes blank exactly when it is needed.
  if (dt.stderr || dt.stdout || dt.traza || dt.reason) {
    html +=
      '<p class="sub chico">The step that stopped' +
      (dt.label ? " (<b>" + esc(dt.label) + "</b>)" : "") +
      (dt.category ? " &middot; kind of failure: <code>" + esc(dt.category) + "</code>" : "") +
      "</p>" +
      detalleTecnicoHTML(r);
  }
  if (r.referencia) {
    html +=
      '<dl class="datos"><dt>Reference used</dt><dd>' + esc(r.referencia.blob) + "</dd>" +
      "<dt>Why that one</dt><dd>" + esc(r.referencia.origin) + "</dd></dl>";
  }
  if (gen) {
    html +=
      '<p class="sub chico">Output from <code>' +
      esc(gen.herramienta || "add_device.py") + "</code>:</p><pre>" +
      esc((gen.stdout || "") + (gen.stderr ? "\n--- stderr ---\n" + gen.stderr : "")) +
      "</pre>" +
      '<p class="sub chico"><code>--repunta</code> detected: <code>' +
      esc((gen.repuntes || []).join(", ") || "none") + "</code></p>";
  }
  if (comp) {
    html +=
      '<p class="sub chico">Gate output (<code>nada_se_movio</code>):</p><pre>' +
      esc(comp.salida_cruda || "") + "</pre>";
  }
  if (r.command) {
    html +=
      '<p class="sub chico">Recording command:</p><pre>cd ' + esc(r.command.cwd) +
      "\n" + esc(r.command.command) + "</pre>";
  }
  el.innerHTML = html || '<span class="vacio">no data</span>';
}

/* ============================= HISTORY ===================================== */

async function cargarAncla() {
  const r = await llamar("anchor_status");
  $("#ancla").innerHTML = r.ok
    ? (r.coincide
        ? nota("ok", "<b>Matches.</b> This is exactly the file that's recorded on your control today.")
        : nota("alarma", "<b>Doesn't match.</b> The file on disk changed since it was last written to your control.")) +
      '<details class="ver-mas"><summary>Show details</summary>' +
      '<dl class="datos"><dt>File</dt><dd>' + esc(r.file) + "</dd>" +
      "<dt>Fingerprint on disk</dt><dd>" + esc(r.md5) + "</dd>" +
      "<dt>Fingerprint expected</dt><dd>" + esc(r.esperado) + "</dd>" +
      "<dt>Size</dt><dd>" + esc(r.tamano) + " B</dd></dl></details>"
    : nota("aviso", esc(r.error));
}

$("#btn-ancla").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  $("#ancla-pasos").innerHTML =
    '<div class="cargando">rebuilding everything from scratch, in a ' +
    "temporary location that never touches your saved files...</div>";
  ocupado(b, true, "checking...");
  const r = await llamar("anchor_regenerate", null);
  ocupado(b, false);
  const pasos = r.steps || [];
  $("#ancla-chips").innerHTML = r.ok
    ? (r.coincide ? chip("matches", "si") : chip("DOESN'T match", "no")) +
      (r.negativo_correcto ? chip("safety check: working", "si") : chip("safety check: NOT working", "no"))
    : chip("failed", "no");
  const tabla = pasos.length
    ? "<table><thead><tr><th>Step</th><th>fingerprint obtained</th>" +
      "<th>fingerprint expected</th><th></th></tr></thead><tbody>" +
      pasos
        .map(
          (p) =>
            "<tr><td>" + p.passed + ". " + esc(p.name) + "</td><td><code>" +
            esc(p.md5 || "-") + "</code></td><td><code>" + esc(p.esperado) +
            "</code></td><td>" +
            (p.coincide ? chip("same", "si") : chip("different", "no")) +
            "</td></tr>"
        )
        .join("") +
      "</tbody></table>"
    : "";
  $("#ancla-pasos").innerHTML = r.ok
    ? nota(
        r.coincide ? "ok" : "alarma",
        "<b>" +
          (r.coincide
            ? "Reproduces the control's file byte for byte."
            : "Does NOT reproduce the control's file.") +
          "</b> " +
          (r.coincide
            ? "Rebuilding everything from scratch gives back exactly what's on your remote today."
            : "Something changed along the way that builds the file.") +
          "<br>Verification " + (r.compuerta_paso1 && r.compuerta_paso2 ? "passed" : "did NOT pass") +
          ", and it correctly " +
          (r.negativo_correcto
            ? "catches a broken file when one is forced on purpose"
            : "FAILED to catch a broken file when one was forced on purpose -- that's serious") +
          "."
      ) +
      '<details class="ver-mas"><summary>Show details</summary>' +
      tabla +
      "<p>Fingerprint obtained: <code>" + esc(r.md5) + "</code></p>" +
      "<p>Working directory: <code>" + esc(r.dir) + "</code></p></details>"
    : nota("alarma", "<b>Failed.</b> " + esc(r.error));
});

async function cargarHistorial() {
  const r = await llamar("history");
  const el = $("#tabla-hist");
  el.className = "";
  if (!r.ok) {
    el.innerHTML = nota("aviso", esc(r.error));
    return;
  }
  if (!r.items.length) {
    el.className = "vacio";
    el.textContent = "no recording has been logged yet";
    return;
  }
  el.innerHTML =
    "<table><thead><tr><th>#</th><th>Date</th><th>File</th><th>Gate</th>" +
    "<th>Booted up</th><th></th></tr></thead><tbody>" +
    r.items
      .map(
        (i) =>
          "<tr><td>" + i.id + "</td>" +
          "<td>" + esc((i.fecha || "").slice(0, 19).replace("T", " ")) + "</td>" +
          "<td>" +
          (i.etiqueta_dispositivo ? "<b>" + esc(i.etiqueta_dispositivo) + "</b><br>" : "") +
          esc(i.file_name) +
          (i.repoints_hex.length
            ? '<br><span class="vacio">' + esc(i.repoints_hex.join(", ")) + "</span>"
            : "") +
          (i.existe_copia ? "" : "<br>" + chip("lost copy", "no")) +
          "</td><td>" +
          (i.compuerta_ok === null
            ? chip("no data")
            : i.compuerta_ok
              ? chip("passed", "si")
              : chip("did NOT pass", "no")) +
          (i.resultado === 0
            ? chip("result 0")
            : i.resultado === null
              ? ""
              : chip("result " + i.resultado, "no")) +
          "</td><td>" +
          (i.verificado_por_usuario === null
            ? chip("not confirmed", "espera")
            : i.verificado_por_usuario
              ? chip("yes", "si")
              : chip("NO", "no")) +
          '</td><td class="acciones">' +
          (i.verificado_por_usuario === null
            ? '<button class="accion chico" data-si="' + i.id + '">Booted up</button>' +
              '<button class="accion chico" data-no="' + i.id + '">Didn\'t boot up</button>'
            : "") +
          '<button class="accion chico" data-rb="' + i.id + '">Rollback</button>' +
          "</td></tr>"
      )
      .join("") +
    "</tbody></table>";

  el.querySelectorAll("[data-si]").forEach((b) =>
    b.addEventListener("click", () => marcar(Number(b.dataset.si), true))
  );
  el.querySelectorAll("[data-no]").forEach((b) =>
    b.addEventListener("click", () => marcar(Number(b.dataset.no), false))
  );
  el.querySelectorAll("[data-rb]").forEach((b) =>
    b.addEventListener("click", async () => {
      const rb = await llamar("history_command_rollback", Number(b.dataset.rb));
      mostrar(
        "#rollback",
        rb.ok ? "cd " + rb.cwd + "\n" + rb.command : "ERROR: " + rb.error
      );
    })
  );
}

async function marcar(id, arranco) {
  const r = await llamar(
    "history_confirm_startup",
    id,
    arranco,
    arranco ? "the control booted up fine" : "the control did NOT boot up"
  );
  if (!r.ok) {
    mostrar("#rollback", "ERROR: " + r.error);
    return;
  }
  if (!arranco && r.ofrecer_rollback) {
    const rb = await llamar("history_command_rollback", r.ofrecer_rollback.id);
    mostrar(
      "#rollback",
      "to go back to #" + r.ofrecer_rollback.id + ":\n" +
        (rb.ok ? "cd " + rb.cwd + "\n" + rb.command : "ERROR: " + rb.error)
    );
  }
  cargarHistorial();
}

/* =============================== KEYS ======================================= */
/* The Keys screen: the remote's PHOTO, and what each button does.
 *
 * Three classes of button, and the difference is NOT cosmetic -- they're
 * three different mechanisms in the blob:
 *
 *   1. the RUBBER keys (numbers, volume, channel, the d-pad, transport,
 *      mute, menu/info/guide/exit). Bound PER ACTIVITY, in the keyboard
 *      context table in section [10]'s header: the (context, code) row
 *      points to a {cmd_id, dev_id} object. Editable with
 *      `teclas_fisicas.aplicar_fisica()`. There are 36 codes;
 *   2. the eight TOUCHSCREEN zones (0xAB 0xAC 0xB0..0xB5), bound PER SCREEN
 *      in table[6] and drawn inside the LCD with the REAL geometry from
 *      section [19] of the blob (the layer's viewBox is in the pixels of
 *      the photo crop, so zone and pixel line up);
 *   3. the ones that don't hang off any command in any context: they're
 *      drawn, they can be tapped, and tapping them says WHY they can't be
 *      changed -- they're not hidden and don't stay clickable with no
 *      effect. The reason is measured by Python (`teclas_foto.modelo()`),
 *      not written here.
 *
 * The photo and its zones are generated by
 * `config_work/build_key_map.py`; this file doesn't have a single
 * hand-written remote coordinate.
 *
 * The four hard rules apply just like in Control:
 *   1. if the gate doesn't pass, the record button IS NOT DRAWN (`remove()`);
 *   2. after recording, the user is asked whether the control booted up
 *      fine;
 *   3. the size check is not a byte-by-byte verification, and that text
 *      comes from Python;
 *   4. the warning that this writes the control's memory shows up BEFORE.
 */

let TECLAS_MODELO = null; // {pantallas, dispositivos, ...} from today's blob
let TECLAS_FOTO = null; // {foto, contextos, por_codigo, actividades, resumen}
let TECLAS_PAGINAS = []; // paginas_dispositivo: each device's own page + its keys
let TECLAS_PLANTILLA = []; // plantilla: per device, the standard-key plan
let TECLAS_PLANTILLA_ERROR = ""; // why there is no plan at all (module missing)
let TECLAS_AVISO_SITIO = ""; // why "where it works" exists (from Python)
let TECLAS_EDITABLES = []; // the eight screen-zone codes
let TECLAS_AVISO = ""; // human summary of what can and can't be done
let TECLAS_ACT = null; // chosen keyboard context (an Activity)
// TECLAS_PANTALLA (which LCD page was picked) is gone along with the screen
// zones: this screen only edits the rubber keys now.
let TECLAS_SEL = null; // key of the chosen button, or null
const TECLAS_PEND = new Map(); // key -> pending change
let TECLAS_RECHAZO = null; // kept: cleared on edits, read by nothing else now
let TECLAS_LISTENERS = false; // the one-time wiring at the end of initTeclas()
let TECLAS_LISTO = false; // gate already passed: next step is recording

const claveFisica = (ctx, c) => "fis:" + ctx + ":" + c;

/* --------------------------------------------------------------- data -- */

function teclasDispositivo(k1) {
  if (!TECLAS_MODELO) return null;
  return TECLAS_MODELO.devices.find((d) => d.k1 === k1) || null;
}

function teclasNombreDispositivo(k1) {
  const d = teclasDispositivo(k1);
  return d ? d.name : "device " + k1;
}

function teclasNombreComando(k1, k2) {
  const d = teclasDispositivo(k1);
  const c = d && d.commands[k2];
  if (c && c.name) return c.name;
  const j = TECLAS_FOTO && TECLAS_FOTO.command_names;
  const n = j && j[String((k1 << 8) | k2)];
  if (n && n.command) return n.command;
  return "command " + k2;
}

function teclasPaginasDe(k1) {
  if (!TECLAS_MODELO) return [];
  const out = [];
  TECLAS_MODELO.screens.forEach((p) => {
    (p.slots || []).forEach((s) => {
      const propias = s.keys.filter((t) => t.k1 === k1);
      if (propias.length) out.push({ ordinal: p.ordinal, slot: s.slot, n: propias.length });
    });
  });
  return out;
}

/** The row of the chosen context for a code, or null. */
function teclasFilaFisica(codHex) {
  if (!TECLAS_FOTO || TECLAS_ACT === null) return null;
  const info = TECLAS_FOTO.by_code[codHex];
  if (!info) return null;
  return info.contextos.find((c) => c.contexto === TECLAS_ACT) || null;
}

function teclasInfoCodigo(codHex) {
  return (TECLAS_FOTO && TECLAS_FOTO.by_code[codHex]) || null;
}

/* ---------------------------------------------------- WHERE it takes effect --
 *
 * A rubber key is not bound once. The remote decides what to send by looking
 * at WHERE you are, and there are two different tables:
 *
 *   - the device's own page (Devices -> that device): the header of its
 *     table[6] trailer. This is how the three devices the remote came with
 *     are wired -- 38 rubber keys each, every one pointing at that same
 *     device. It works with no activity running, which is what somebody who
 *     just added a TV is going to try;
 *   - an activity's keyboard context ([10][n]): only in force while that
 *     activity is running, and only on screens that don't claim the key
 *     first -- a device page claims it first.
 *
 * Grabada #7 wrote in the second one and the user tested in the first: the
 * change was correct, verified, and invisible. So the site is CHOSEN here,
 * out loud, and a site that cannot work is not offered -- the reason is
 * shown in its place. `paginas_dispositivo` and every `motivo` come measured
 * from Python (`teclas_fisicas.mapear_dispositivos`).
 */
function teclasPaginaDe(k1) {
  return TECLAS_PAGINAS.find((p) => p.k1 === k1) || null;
}

function teclasSitios(codNum, k1) {
  const codHex = "0x" + Number(codNum).toString(16).toUpperCase().padStart(2, "0");
  const fuera = [];
  const pag = k1 === null || k1 === undefined ? null : teclasPaginaDe(Number(k1));
  if (pag) {
    const info = (pag.codigos || {})[codHex];
    fuera.push({
      clave: "dev:" + pag.screen,
      kind: "device",
      screen: pag.screen,
      label: "on " + pag.name + "'s own page (Devices → " + pag.name + ")",
      donde: pag.name + "'s page",
      editable: !!(info && info.editable),
      reason: info ? info.reason : "this page doesn't list that key",
      hoy: info && info.cmd_id !== undefined ? info : null,
    });
  }
  const info = teclasInfoCodigo(codHex);
  ((TECLAS_FOTO && TECLAS_FOTO.activities) || []).forEach((a) => {
    const fila =
      info && info.contextos.find((c) => c.contexto === a.contexto);
    if (!fila) return;
    fuera.push({
      clave: "ctx:" + a.contexto,
      kind: "fisica",
      contexto: a.contexto,
      label: "only while the activity " + a.name + " is running",
      donde: a.name,
      editable: !!fila.editable,
      reason: fila.human_reason || fila.reason,
      hoy: fila,
    });
  });
  return fuera;
}

/** Can this key be changed ANYWHERE? (any device page, or any activity) */
function teclasTieneSitio(codNum) {
  const codHex = "0x" + Number(codNum).toString(16).toUpperCase().padStart(2, "0");
  const info = teclasInfoCodigo(codHex);
  if (info && info.editable_fisica) return true;
  return TECLAS_PAGINAS.some((p) => {
    const c = (p.codigos || {})[codHex];
    return c && c.editable;
  });
}

/** The name of the chosen activity, read from the blob. */
function teclasNombreActividad() {
  const a = (TECLAS_FOTO && TECLAS_FOTO.activities) || [];
  const x = a.find((v) => v.contexto === TECLAS_ACT);
  // With no activity configured there is no name to give, and "activity
  // null" is not one. The device pages still work, so the screen says which
  // of the two it is instead of printing a placeholder.
  if (!x) return TECLAS_ACT === null ? "(no activity configured)" : "activity " + TECLAS_ACT;
  return x.name;
}

/* ------------------------------------------------------------- rendering -- */

function teclasPintarBarra() {
  const selA = $("#teclas-actividad");
  const acts = (TECLAS_FOTO && TECLAS_FOTO.activities) || [];
  if (selA && selA.options.length !== acts.length) {
    selA.innerHTML = acts
      .map((a) => '<option value="' + a.contexto + '">' + esc(a.name) + "</option>")
      .join("");
  }
  if (selA) {
    if (TECLAS_ACT === null && acts.length) TECLAS_ACT = acts[0].contexto;
    if (TECLAS_ACT !== null) selA.value = String(TECLAS_ACT);
  }
  const pista = $("#teclas-actividad-pista");
  if (pista) {
    // This selector no longer decides WHERE a change is written -- that is
    // chosen per key, next to the command ("Where it should work"). All it
    // does now is pick which activity's column you are looking at.
    pista.textContent = acts.length
      ? "only changes what you're looking at; where a change gets written is " +
        "chosen with the key, below"
      : "this control has no activity with rubber keys bound to it -- device " +
        "pages still work";
  }
  // The LCD device/page pickers used to live here. They're gone: this
  // screen is about the RUBBER keys, which is what "Keys" means to
  // someone holding the remote. The touchscreen zones change meaning with
  // whatever menu the LCD is showing, so editing them from a static
  // picture was the confusing part -- see `teclasPintarCobertura()`, which
  // now says so instead of offering the selectors.
}

/** The state of a key on the photo: CSS class + reason text. */
function teclasEstadoDeZona(t) {
  const cod = t.codigo;
  if (!cod) {
    return {
      category: "bloqueada",
      clave: "bloq:" + t.id,
      reason:
        "This button doesn't have a confirmed name. Most buttons could be " +
        "matched to a real command your remote already sends, and that's " +
        "how their names were found; this one isn't tied to any, so " +
        "nothing gets guessed for it.",
    };
  }
  const codNum = parseInt(cod, 16);
  // --- a softkey printed against the LCD: not edited from this screen -----
  if (TECLAS_EDITABLES.includes(codNum)) {
    return {
      category: "bloqueada",
      clave: "bloq:" + t.id,
      reason:
        "This one is a screen button: what it does depends on which menu " +
        "the LCD is showing at that moment, not on the activity. It isn't " +
        "edited from this screen.",
    };
  }
  // --- rubber key ---------------------------------------------------------
  //
  // Selectable if it can be bound ANYWHERE: on a device's own page or in an
  // activity. It used to be selectable only when the chosen activity already
  // had a row for it, which hid the site that actually works for a device you
  // just added -- its page had no row for any rubber key, so every one of
  // them was painted "can't be changed" while the factory pages bind 38.
  const fila = teclasFilaFisica(cod);
  const info = teclasInfoCodigo(cod);
  if (!teclasTieneSitio(codNum)) {
    return {
      category: "bloqueada",
      clave: "bloq:" + t.id,
      reason:
        (info && info.human_reason) ||
        "no device page and no activity can carry this key: the remote has " +
          "nothing recorded for it anywhere.",
      motivo_tecnico: info && info.reason,
    };
  }
  const clave = claveFisica(TECLAS_ACT, codNum);
  return {
    category: TECLAS_PEND.has(clave) ? "pendiente" : fila && fila.editable ? "asignada" : "libre",
    clave: clave,
    row: fila && fila.editable ? fila : null,
  };
}

/** Draws the zone layer on top of the photo. */
function teclasPintarMando() {
  const svg = $("#capa-zonas");
  const foto = TECLAS_FOTO && TECLAS_FOTO.foto;
  if (!svg) return;
  if (!foto) {
    svg.innerHTML = "";
    return;
  }
  const W = foto.crop_width_px;
  const H = foto.crop_height_px;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  const L = foto.lcd.box_px; // [x0,y0,x1,y1] in pixels of the crop
  const pw = foto.lcd.panel_ancho;
  const ph = foto.lcd.panel_alto;
  const partes = [];

  // the LCD frame: it's not a key, it's not selectable. It's just an outline.
  partes.push(
    '<rect class="lcd-marco" x="' + L[0] + '" y="' + L[1] + '" width="' +
      (L[2] - L[0]) + '" height="' + (L[3] - L[1]) + '" rx="2" />'
  );

  // The LCD's touch zones used to be drawn here, clickable, driven by a
  // page picker. They aren't any more: the screen's buttons mean different
  // things depending on the menu the remote is showing, so a fixed picture
  // of them was a picture of one arbitrary moment. The LCD is left as a
  // plain outline, with nothing selectable inside it.
  partes.push(
    '<text class="lcd-vacio" x="' + (L[0] + (L[2] - L[0]) / 2) + '" y="' +
      (L[1] + (L[3] - L[1]) / 2) + '">screen</text>'
  );

  // --- the keys on the photo ----------------------------------------------
  foto.keys.forEach((t) => {
    const st = teclasEstadoDeZona(t);
    // The two bottom softkeys are the SAME key as the LCD's bottom strip
    // (0xAB/0xAC): both get drawn, with the SAME key id, so you can tap
    // either the physical bar on the photo or the label on the screen.
    // Neither one is skipped.
    const c = t.box_px;
    const clases = ["z", st.category];
    if (TECLAS_SEL === st.clave) clases.push("seleccionada");
    const cuerpo =
      t.forma === "circle"
        ? '<ellipse class="cara" cx="' + ((c[0] + c[2]) / 2).toFixed(2) + '" cy="' +
          ((c[1] + c[3]) / 2).toFixed(2) + '" rx="' + ((c[2] - c[0]) / 2).toFixed(2) +
          '" ry="' + ((c[3] - c[1]) / 2).toFixed(2) + '" />'
        : '<rect class="cara" x="' + c[0] + '" y="' + c[1] + '" width="' +
          (c[2] - c[0]) + '" height="' + (c[3] - c[1]) + '" rx="4" />';
    const hoy = st.row
      ? st.row.command_name || teclasNombreComando(st.row.k1, st.row.k2)
      : null;
    const titulo = t.label + (hoy ? " — today: " + hoy : st.reason ? " — can't be changed" : "");
    partes.push(
      '<g class="' + clases.join(" ") + '" data-tecla="' + esc(st.clave) +
        '" tabindex="0" role="button" aria-label="' + esc(titulo) + '">' +
        "<title>" + esc(titulo) + "</title>" + cuerpo + "</g>"
    );
  });

  svg.innerHTML = partes.join("");
  Array.from(svg.querySelectorAll("[data-tecla]")).forEach((el) => {
    el.addEventListener("click", () => teclasElegir(el.dataset.tecla));
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        teclasElegir(el.dataset.tecla);
      }
    });
  });

  const pie = $("#mando-pie");
  if (pie && foto) {
    pie.textContent =
      "Real photo of the remote. The rectangle in the middle is the LCD " +
      "screen -- tap its buttons the same as the physical ones.";
  }
}

/* ------------------------------------------------------ the side panel ---- */

function teclasPintarElegida() {
  const el = $("#mapeo-tecla");
  const bloque = $("#mapeo-asignar");
  if (!TECLAS_SEL) {
    el.className = "vacio";
    el.textContent = "Tap a button on the photo.";
    bloque.hidden = true;
    return;
  }

  // --- a key that CANNOT be changed: it says why ---------------------------
  if (TECLAS_SEL.startsWith("bloq:")) {
    const id = TECLAS_SEL.slice(5);
    const foto = TECLAS_FOTO && TECLAS_FOTO.foto;
    const t = foto && foto.keys.find((x) => x.id === id);
    const st = t ? teclasEstadoDeZona(t) : null;
    el.className = "";
    el.innerHTML =
      "<b>" + esc((t && t.label) || "that key") + "</b><br>" +
      nota(
        "aviso",
        "<b>This key can't be changed.</b><br>" +
          esc((st && st.reason) || "no reason recorded") +
          (st && st.extra ? "<br>" + esc(st.extra) : "") +
          (t && t.nota ? '<div class="pista">' + esc(t.nota) + "</div>" : "") +
          // The measured detail is NOT deleted -- it moves behind "Show
          // details". Someone adding a TV doesn't need it; whoever wants to
          // check the reason is one click away. `st.motivo_tecnico` and
          // `t.codigo` keep arriving from Python exactly as before.
          ((st && st.motivo_tecnico) || (t && t.codigo)
            ? "<details class='ver-mas'><summary>Show details</summary>" +
              (st && st.motivo_tecnico
                ? '<div class="pista">' + esc(st.motivo_tecnico) + "</div>"
                : "") +
              (t && t.codigo
                ? '<div class="pista">code the remote uses for this key: ' +
                  esc(t.codigo) + "</div>"
                : "") +
              "</details>"
            : "")
      );
    bloque.hidden = true;
    return;
  }

  // The `pan:` (LCD zone) branch used to be here. Screen zones aren't drawn
  // or selectable any more, so TECLAS_SEL can never hold one -- there is no
  // dead branch left behind to mislead whoever reads this next.

  // --- a rubber key -------------------------------------------------------
  const [, ctx, c] = TECLAS_SEL.split(":");
  const cod = Number(c);
  const codHex = "0x" + cod.toString(16).toUpperCase().padStart(2, "0");
  const foto = TECLAS_FOTO && TECLAS_FOTO.foto;
  const t = foto && foto.keys.find((x) => x.codigo === codHex);
  const fila = teclasFilaFisica(codHex);
  const pend = TECLAS_PEND.get(TECLAS_SEL);
  const info = teclasInfoCodigo(codHex);
  const otros = (info ? info.contextos : []).filter(
    (x) => x.contexto !== Number(ctx) && x.editable
  );
  // What the key does TODAY, per place -- not only in the activity picked at
  // the top. The line that was here said "today it does nothing" for a key
  // that does something on three device pages.
  const hoyPaginas = TECLAS_PAGINAS.map((p) => {
    const c = (p.codigos || {})[codHex];
    if (!c || c.cmd_id === undefined) return null;
    return esc(p.name) + "'s page → " + esc(teclasNombreComando(c.k1_hoy, c.k2));
  }).filter(Boolean);
  el.className = "";
  el.innerHTML =
    "<b>" + esc((t && t.label) || "unlabeled button") + "</b>" +
    '<span class="pista"> rubber key</span><br>' +
    (fila
      ? "in the activity " + esc(teclasNombreActividad()) + " it does <b>" +
        esc(fila.command_name || teclasNombreComando(fila.k1, fila.k2)) +
        "</b> for <b>" + esc(fila.device_name || teclasNombreDispositivo(fila.k1)) + "</b>"
      : "in the activity " + esc(teclasNombreActividad()) + " it does nothing") +
    (hoyPaginas.length
      ? '<div class="pista">on the device pages: ' + hoyPaginas.join(" · ") + "</div>"
      : '<div class="pista">no device page sends anything with this key today</div>') +
    (otros.length
      ? '<div class="pista">in the other activities: ' +
        otros
          .map(
            (x) =>
              esc(x.name) + " → " +
              esc(x.command_name || teclasNombreComando(x.k1, x.k2))
          )
          .join(" · ") + "</div>"
      : "") +
    (t && t.nota ? '<div class="pista">' + esc(t.nota) + "</div>" : "") +
    (pend
      ? "<br>" +
        nota(
          "aviso",
          "It's about to do <b>" + esc(teclasNombreComando(pend.k1, pend.k2)) +
            "</b> for <b>" + esc(teclasNombreDispositivo(pend.k1)) +
            "</b> once you apply, <b>" + esc(pend.donde_frase || pend.donde) + "</b>."
        )
      : "");
  bloque.hidden = false;
  teclasPintarSelector(pend);
}

function teclasPintarSelector(pend) {
  const selD = $("#mapeo-dispositivo");
  if (selD.options.length <= 1 && TECLAS_MODELO) {
    selD.innerHTML =
      '<option value="">pick one...</option>' +
      TECLAS_MODELO.devices
        .map((d) => '<option value="' + d.k1 + '">' + esc(d.name) + "</option>")
        .join("");
  }
  selD.value = pend ? String(pend.k1) : "";
  selD.dispatchEvent(new Event("change"));
  if (pend) $("#mapeo-comando").value = String(pend.k2);
  teclasPintarSitios(pend);
  $("#btn-mapeo-quitar").disabled = !pend;
}

/* The "where it should work" selector. Only the sites that CAN work are
 * listed; the ones that can't show their measured reason instead, and if
 * none can, the assign button stays off -- the same rule the rest of the app
 * follows: a path with no way out doesn't get drawn. */
function teclasPintarSitios(pend) {
  const sel = $("#mapeo-sitio");
  const pista = $("#mapeo-sitio-pista");
  const btn = $("#btn-mapeo-asignar");
  if (!sel) return;
  const vD = $("#mapeo-dispositivo").value;
  if (!TECLAS_SEL || !TECLAS_SEL.startsWith("fis:") || vD === "") {
    sel.disabled = true;
    sel.innerHTML = '<option value="">choose a device first</option>';
    if (pista) pista.textContent = "";
    if (btn) btn.disabled = true;
    return;
  }
  const cod = Number(TECLAS_SEL.split(":")[2]);
  const sitios = teclasSitios(cod, Number(vD));
  const buenos = sitios.filter((s) => s.editable);
  sel.innerHTML = buenos
    .map((s) => '<option value="' + esc(s.clave) + '">' + esc(s.label) + "</option>")
    .join("");
  sel.disabled = buenos.length === 0;
  if (pend && pend.sitio && buenos.some((s) => s.clave === pend.sitio)) {
    sel.value = pend.sitio;
  }
  const malos = sitios.filter((s) => !s.editable);
  if (pista) {
    pista.innerHTML = buenos.length
      ? esc(
          buenos.length === 1
            ? "one place can carry this key."
            : "the device's own page works with no activity running; the " +
              "activity only while it is running."
        ) +
        (malos.length
          ? " " +
            esc(
              malos
                .map((s) => "Not " + s.label + ": " + (s.reason || "not editable"))
                .join(" ")
            )
          : "")
      : nota(
          "aviso",
          "<b>This key can't be changed for that device.</b> " +
            esc(
              malos
                .map((s) => s.label + ": " + (s.reason || "not editable"))
                .join(" · ")
            ) ||
            "no place in this configuration can carry it."
        );
  }
  if (btn) {
    btn.disabled =
      buenos.length === 0 ||
      $("#mapeo-comando").value === "" ||
      $("#mapeo-dispositivo").value === "";
  }
}

function teclasPintarAcciones() {
  const acciones = $("#teclas-acciones");
  const n = TECLAS_PEND.size;
  acciones.innerHTML =
    (TECLAS_LISTO
      ? ""
      : '<button class="accion primaria" id="btn-teclas-aplicar">Add ' + n +
        " key change" + (n === 1 ? "" : "s") + " to Sync</button>") +
    '<button class="accion" id="btn-teclas-descartar">Discard everything</button>';
  acciones.hidden = n === 0;
  if (n) {
    const ap = $("#btn-teclas-aplicar");
    if (ap) ap.addEventListener("click", teclasAplicar);
    $("#btn-teclas-descartar").addEventListener("click", teclasDescartar);
  }
}

function teclasDescartar() {
  TECLAS_PEND.clear();
  TECLAS_RECHAZO = null;
  TECLAS_LISTO = false;
  $("#teclas-resultado").innerHTML = "";
  $("#teclas-siguiente").innerHTML = "";
  teclasDetalles(null);
  teclasRepintar();
}

function teclasPintarPendientes() {
  const el = $("#mapeo-lista");
  if (TECLAS_PEND.size === 0) {
    el.className = "vacio";
    el.textContent = "none yet";
    teclasPintarAcciones();
    return;
  }
  el.className = "";
  el.innerHTML = Array.from(TECLAS_PEND.entries())
    .map(
      ([k, v]) =>
        '<div class="mapeo-item"><span>' +
        esc(v.rotulo) + " &rarr; <b>" + esc(teclasNombreComando(v.k1, v.k2)) +
        "</b> (" + esc(teclasNombreDispositivo(v.k1)) + ")" +
        // WHERE it will work, on every queued line. It used to print only
        // for the activity site and read " on PC", which is the half of the
        // sentence that made #7 look like it should work everywhere.
        (v.donde_frase || v.donde
          ? '<span class="pista"> — ' + esc(v.donde_frase || v.donde) + "</span>"
          : "") +
        '</span><button class="quitar" data-k="' + esc(k) + '">remove</button></div>'
    )
    .join("");
  Array.from(el.querySelectorAll(".quitar")).forEach((b) => {
    b.addEventListener("click", () => {
      TECLAS_PEND.delete(b.dataset.k);
      TECLAS_RECHAZO = null;
      TECLAS_LISTO = false;
      teclasRepintar();
    });
  });
  teclasPintarAcciones();
}

function teclasPintarCobertura() {
  const el = $("#teclas-cobertura-cuerpo");
  if (!el || !TECLAS_FOTO) return;
  const r = TECLAS_FOTO.summary;
  const foto = TECLAS_FOTO.foto;
  const noed = r.no_editables
    .map((c) => {
      const i = TECLAS_FOTO.by_code[c];
      const z = foto && foto.keys.find((t) => t.codigo === c);
      // Lead with the button's name whenever the photo has one. Only 2 of
      // these 11 do: the other 9 are keys the remote's firmware declares
      // that this remote's face doesn't have, so there is no honest name to
      // give them -- say that instead of dressing up the code as a name.
      // The code still shows, at the end, for whoever wants to check.
      return "<tr><td>" +
        (z
          ? "<b>" + esc(z.label) + "</b>"
          : "<b>a key with no button on this remote</b>") +
        '<br><span class="pista">' + esc(c) + "</span></td><td>" +
        esc((i && (i.human_reason || i.reason)) || "") + "</td></tr>";
    })
    .join("");
  const sinCodigo = (foto ? foto.keys.filter((t) => !t.codigo) : [])
    .map(
      (t) =>
        "<tr><td><b>" + esc(t.label) + "</b></td><td>" +
        esc(t.nota || "doesn't send any command to a device, so there's " +
          "nothing to change on it") + "</td></tr>"
    )
    .join("");
  el.className = "";
  el.innerHTML =
    "<p><b>" + r.editables_fisicas + " of " + r.inventario + "</b> keys can " +
    "be changed from this screen: the rubber ones, grouped by activity " +
    "(the same key sends something different depending on which activity " +
    "the remote is in).</p>" +
    "<p>" + esc(TECLAS_AVISO_SITIO) + "</p>" +
    (TECLAS_PAGINAS.length
      ? "<p>The device pages this remote has, and how many of its keys each " +
        "one can carry:</p><table class=\"tabla\"><tbody>" +
        TECLAS_PAGINAS.map(
          (p) =>
            "<tr><td><b>" + esc(p.name) + "</b><br><span class=\"pista\">" +
            p.pages + " screen" + (p.pages === 1 ? "" : "s") +
            " of buttons</span></td><td>" + p.n_rows +
            " keys claimed on its page, " + p.n_editables +
            " can be assigned here</td></tr>"
        ).join("") +
        "</tbody></table>"
      : "") +
    "<p>Your remote also has <b>" + r.editables_screen + "</b> buttons " +
    "drawn on the LCD. They exist and they work, but they aren't edited " +
    "here: what each one does depends on which menu the screen is showing " +
    "at that moment, so there is no single answer to \"what does this " +
    "button do\" to put in a picture.</p>" +
    "<p>" + esc(TECLAS_AVISO) + "</p>" +
    "<p>The " + r.no_editables.length + " codes that can't be changed, " +
    "and why:</p>" +
    '<table class="tabla"><tbody>' + noed + "</tbody></table>" +
    (sinCodigo
      ? "<p>And the keys on the photo that couldn't be tied to a code " +
        "with evidence:</p><table class=\"tabla\"><tbody>" + sinCodigo +
        "</tbody></table>"
      : "");
}

function teclasRepintar() {
  teclasPintarBarra();
  teclasPintarMando();
  teclasPintarElegida();
  teclasPintarPendientes();
  teclasPintarCobertura();
  teclasPintarPlantilla();
}

/* ------------------------------------------------- THE STANDARD KEYS -----
 *
 * What the factory does and this project didn't: a device's own page
 * declares the WHOLE keyboard tied to that one device (49 rows, the same
 * 49 in the same order on the three pages the remote came with). A page
 * this app writes declares four rows, so standing on that device none of
 * the rubber keys does anything -- the page's header is what wins
 * (`0x02E2F2` matches by code and eats the event right there).
 *
 * Everything painted here is DECIDED IN PYTHON and arrives already
 * counted and already worded: `keys_model().plantilla`, one entry per
 * device, from `plantilla_teclas.plan_dispositivo`. The JS does not count
 * keys, does not decide what "can't be bound" means and does not write a
 * single reason of its own -- the same rule the rest of this screen
 * follows for `motivo`.
 *
 * RULE, out loud on the screen: a key that already points somewhere is
 * NEVER overwritten. Python separates `por_ligar` from `respetada`, and
 * `respetada` is what the user set by hand.
 */
function teclasPintarPlantilla() {
  const zona = $("#teclas-plantilla");
  if (!zona) return;
  if (TECLAS_PLANTILLA_ERROR) {
    zona.className = "";
    zona.innerHTML = nota(
      "alarma",
      "<b>The standard-key plan couldn't be built.</b> " +
        esc(TECLAS_PLANTILLA_ERROR) +
        " Keys can still be changed one at a time above."
    );
    return;
  }
  if (!TECLAS_PLANTILLA.length) {
    zona.className = "vacio";
    zona.textContent =
      "No device on this control has a page of its own that the firmware " +
      "reaches, so there is nowhere to bind standard keys.";
    return;
  }
  zona.className = "plantilla-lista";
  zona.innerHTML = TECLAS_PLANTILLA.map((p) => {
    const cab =
      '<div class="plantilla-cab"><b>' +
      esc(p.name || "device " + p.k1) +
      "</b> " +
      '<span class="vacio">Devices → ' + esc(p.name) +
      " (page " + esc(p.screen) + ")</span></div>";

    // 1. Python couldn't build a plan for this one. It says why, in its own
    //    words, and NO button is drawn -- a button that can't do anything is
    //    exactly the thing this screen has been bitten by twice.
    if (!p.ok || !p.plan_posible) {
      return (
        '<div class="plantilla-item">' + cab +
        '<div class="plantilla-linea">' +
        esc(p.summary || p.no_plan_reason || p.error || "") +
        "</div></div>"
      );
    }

    // What is left to offer = the plan minus what is ALREADY waiting in
    // Sync. Python counts it (`n_en_sync`); without it the button kept
    // offering the same 31 keys that were already queued and the second
    // press queued nothing, with no word about why.
    const enSync = p.n_en_sync || 0;
    const faltanEncolar = Math.max(0, (p.n_to_bind || 0) - enSync);

    const cuentas = [];
    if (faltanEncolar) cuentas.push(chip(faltanEncolar + " to bind", "pendiente"));
    if (enSync) cuentas.push(chip(enSync + " waiting in Sync", "pendiente"));
    if (p.n_ya_ligadas)
      cuentas.push(chip(p.n_ya_ligadas + " already bound", "asignada"));
    if (p.n_respetadas)
      cuentas.push(chip(p.n_respetadas + " yours, kept", "asignada"));
    if (p.n_no_command)
      cuentas.push(chip(p.n_no_command + " it can't", "bloqueada"));
    if (p.n_bloqueadas)
      cuentas.push(chip(p.n_bloqueadas + " refused", "bloqueada"));

    // 2. WHICH ones can't, one by one, with the measured reason. Without
    //    this the only way to find out is pressing every key on the remote.
    const noSePuede = (p.no_command || []).length
      ? '<div class="plantilla-falta"><b>Won\'t work on this device:</b> ' +
        (p.no_command || [])
          .map((f) => esc(f.key) + " (no " + esc(f.rol) + ")")
          .join(", ") +
        ". " + esc(p.name) + " doesn't have those commands, so those keys " +
        "are left alone.</div>"
      : "";
    const tuyas = (p.respetadas || []).length
      ? '<div class="plantilla-falta"><b>Left exactly as you set them:</b> ' +
        (p.respetadas || []).map((f) => esc(f.key)).join(", ") +
        ". Binding the standard keys never overwrites a key you chose.</div>"
      : "";
    const bloq = (p.bloqueadas || []).length
      ? '<div class="plantilla-falta"><b>The page refuses:</b> ' +
        (p.bloqueadas || [])
          .map((f) => esc(f.key) + " -- " + esc(f.reason))
          .join("; ") +
        "</div>"
      : "";

    // 3. The button EXISTS only when there is something for it to do.
    const boton = faltanEncolar
      ? '<div class="fila"><button class="accion primaria chico" ' +
        'data-plantilla-k1="' + esc(p.k1) + '">Bind the ' + esc(faltanEncolar) +
        " standard key" + (faltanEncolar === 1 ? "" : "s") + " on " +
        esc(p.name) + "</button></div>"
      : "";
    const yaEnCola =
      enSync && !faltanEncolar
        ? '<div class="plantilla-falta"><b>Already on the Sync list:</b> ' +
          enSync + " key" + (enSync === 1 ? "" : "s") +
          ". Press <b>Sync</b> to write them.</div>"
        : "";

    return (
      '<div class="plantilla-item">' + cab +
      '<div class="plantilla-chips">' + cuentas.join(" ") + "</div>" +
      '<div class="plantilla-linea">' + esc(p.summary || "") + "</div>" +
      noSePuede + tuyas + bloq + yaEnCola + boton +
      '<div class="plantilla-res" data-plantilla-res="' + esc(p.k1) + '"></div>' +
      "</div>"
    );
  }).join("");

  zona.querySelectorAll("[data-plantilla-k1]").forEach((b) =>
    b.addEventListener("click", () => teclasPlantillaEncolar(b))
  );
}

/* Queues the whole standard binding for a device that is ALREADY on the
 * remote. It goes down the SAME road a hand-made key change takes
 * (`reasignar_tecla`, subtype `dispositivo`), so the gate,
 * `teclas_alcance` and `nada_se_movio` still have the last word in
 * `sync_preparar()`. Nothing is written here. */
async function teclasPlantillaEncolar(btn) {
  const k1 = Number(btn.dataset.plantillaK1);
  ocupado(btn, true, "adding to Sync...");
  const r = await llamar("keys_template_queue", k1);
  ocupado(btn, false);
  if (!r.ok) {
    const caja = $('[data-plantilla-res="' + k1 + '"]');
    if (caja)
      caja.innerHTML = nota("alarma", "<b>Nothing was queued.</b> " + esc(r.error));
    return;
  }
  // THE BAR. `keys_template_queue` queues 31 changes inside PYTHON in one
  // call, so none of them went through `agregarCambio()` -- the only place
  // that refreshes the counter. Without this the bar kept saying "Sync 1
  // change" with 31 waiting. Caught by the jsdom render.
  await refrescarSync();
  // And the card is re-read too, so it stops offering keys that are now
  // queued. `keys_model` counts `n_en_sync` outside its cache for exactly
  // this: the blob didn't change, the Sync list did.
  const m = await llamar("keys_model");
  if (m.ok) TECLAS_PLANTILLA = m.plantilla || [];
  teclasPintarPlantilla();
  const res = $('[data-plantilla-res="' + k1 + '"]');
  if (!res) return;
  res.innerHTML = nota(
    "ok",
    "<b>" + r.encolados + " key" + (r.encolados === 1 ? "" : "s") +
      " added to Sync.</b> " + esc(r.summary || "") +
      " Nothing has been written to the control: press <b>Sync</b> when " +
      "you're ready." +
      (r.fallos && r.fallos.length
        ? "<br>Couldn't queue: " + esc(r.fallos.join("; "))
        : "")
  );
}

/* Called by `cargarLocales()` when the local catalog changes, so the "Keys"
 * screen doesn't keep showing a device list that no longer matches. It used to
 * be called without ever being defined, which threw a ReferenceError in the
 * middle of `cargarLocales()` and left the Catalog card stuck on "loading..."
 * forever, with nothing in the UI to show why. The guard matters: the catalog
 * loads long before "Keys" does, and repainting an uninitialised screen would
 * throw the same way. */
function mapeoPintarDispositivos() {
  if (!TECLAS_MODELO) return;
  teclasRepintar();
}

function teclasElegir(clave) {
  TECLAS_SEL = clave;
  teclasRepintar();
}

/* ------------------------------------------------------------- apply ---- */

function teclasDetalles(r) {
  const cuerpo = $("#teclas-detalles-cuerpo");
  if (!r) {
    cuerpo.className = "vacio";
    cuerpo.textContent = "there's nothing technical to show yet";
    return;
  }
  cuerpo.className = "";
  const filas = (r.checks || [])
    .map(
      (c) =>
        "<tr><td>" + esc(c.name) + "</td><td>" +
        (c.ok ? "OK" : "FAIL") + "</td><td>" + esc(c.detail) + "</td></tr>"
    )
    .join("");
  cuerpo.innerHTML =
    (r.referencia ? "<p>Reference: <code>" + esc(r.referencia.blob) + "</code><br>" +
      esc(r.referencia.origin || "") + "</p>" : "") +
    (filas ? "<table class=\"tabla\"><tbody>" + filas + "</tbody></table>" : "") +
    (r.changes
      ? "<p>Changes (raw):</p><pre>" +
        esc(
          r.changes
            .map((c) =>
              (c.kind === "fisica"
              ? "context " + c.contexto
              : c.kind === "device"
              ? "device page " + c.screen +
                (c.new_row ? " (row created, header rebuilt at 0x" +
                  (c.new_header || 0).toString(16) + ")" : "")
              : "screen " + c.screen + " slot " + c.slot) +
            " key 0x" + c.codigo.toString(16) +
            (c.campo ? "  field 0x" + c.campo.toString(16) : "") +
            "  object " + c.old_object + " -> " + c.new_object +
            "  cmd_id 0x" + c.cmd_id.toString(16) +
            " dev_id 0x" + c.dev_id.toString(16)
            )
            .join("\n")
        ) + "</pre>"
      : "") +
    (r.repuntes ? "<p>Declared repoints: <code>" + esc(r.repuntes.join(", ")) + "</code></p>" : "") +
    (r.gate ? "<pre>" + esc(r.gate.salida_cruda || "") + "</pre>" : "") +
    (r.md5 ? "<p>md5 " + esc(r.md5) + " &middot; " + r.tamano + " B (+" + r.crecio + ")</p>" : "") +
    (r.error ? nota("alarma", esc(r.error)) : "") +
    (r.technical_detail ? "<pre>" + esc(JSON.stringify(r.technical_detail, null, 1)) + "</pre>" : "");
}

/* `teclasFirmaLote` / `teclasRechazar` lived here: they remembered a gate
 * rejection against this screen's exact batch of keys. The gate no longer
 * runs from this screen -- it runs once in `sync_preparar()` over everything
 * queued -- so the equivalent lives in `syncRechazar`, keyed on the Sync
 * list instead of on this one batch. RULE 1 is not weakened by the move:
 * the button that writes is still never drawn unless Python says green.
 */

/* Hands the pending key changes to Sync -- ONE entry per key, with the
 * exact field names `cambios._paso_teclas()` reads: `subtipo`, `contexto`,
 * `codigo`, `k1`, `k2`. One entry per key, not one entry for the batch,
 * because that is the shape Python already declares; and it costs nothing,
 * since `_paso_teclas()` regroups every queued key change and applies them
 * in ONE pass anyway (the same reason `keys_apply` takes a list: doing them
 * one at a time would recompute the section relocation once per key).
 * Upside for the user: a single key can be pulled back off the Sync list
 * without discarding the rest.
 *
 * `subtipo` is sent explicitly. `_paso_teclas()` defaults a missing one to
 * "pantalla", which would send a rubber key down the screen-zone path and
 * fail on the missing `pantalla`/`slot` fields -- exactly the kind of
 * name-on-one-side-only break this project has been bitten by twice. */
async function teclasAplicar() {
  const btn = $("#btn-teclas-aplicar");
  const res = $("#teclas-resultado");
  $("#teclas-siguiente").innerHTML = "";
  ocupado(btn, true, "adding to Sync...");

  const pendientes = Array.from(TECLAS_PEND.values());
  const fallos = [];
  for (const v of pendientes) {
    // The human wording is built HERE on purpose: this screen already knows
    // the command's name from its own dropdown, while Python's
    // `_etiqueta_cambio()` would have to re-read ~950 command slots to work
    // it out again.
    const params =
      v.kind === "device"
        ? {
            subtipo: "device",
            screen: v.screen,
            codigo: v.codigo,
            k1: v.k1,
            k2: v.k2,
          }
        : {
            subtipo: "fisica",
            contexto: v.contexto,
            codigo: v.codigo,
            k1: v.k1,
            k2: v.k2,
          };
    const r = await agregarCambio(
      "reassign_key",
      params,
      "Key " + v.rotulo + " -> " + teclasNombreComando(v.k1, v.k2) +
        " (" + teclasNombreDispositivo(v.k1) + ", " +
        (v.kind === "device" ? "on " : "in ") + v.donde + ")"
    );
    if (!r.ok) fallos.push(v.rotulo + ": " + r.error);
  }
  ocupado(btn, false);

  if (fallos.length) {
    res.innerHTML = nota(
      "alarma",
      "<b>Some key changes couldn't be queued.</b><ul>" +
        fallos.map((f) => "<li>" + esc(f) + "</li>").join("") +
        "</ul>"
    );
    return;
  }
  TECLAS_LISTO = true;
  if (btn) btn.remove();
  teclasPintarAcciones();
  res.innerHTML = nota(
    "ok",
    "<b>Added to Sync.</b> " + pendientes.length + " key change" +
      (pendientes.length === 1 ? "" : "s") +
      " will be written when you run <b>Sync</b>, together with anything " +
      "else waiting there. Nothing has been written to the control."
  );
}

/* -------------------------------------------------------------- init ---- */

async function initTeclas() {
  const est = $("#teclas-estado");
  const r = await llamar("keys_model");
  if (!r.ok) {
    est.className = "";
    est.innerHTML = r.primer_uso
      ? notaPrimerUso(r)
      : nota("alarma", "Couldn't read the key map: " + esc(r.error));
    // The standard-keys card would otherwise sit on "loading..." forever,
    // which reads like the app is still working. It says the same thing the
    // line above says, and offers no button.
    TECLAS_PLANTILLA = [];
    TECLAS_PLANTILLA_ERROR = r.error || "the key map could not be read";
    teclasPintarPlantilla();
    return;
  }
  TECLAS_MODELO = r.modelo;
  TECLAS_FOTO = r.foto || null;
  TECLAS_PAGINAS = r.paginas_dispositivo || [];
  TECLAS_PLANTILLA = r.plantilla || [];
  TECLAS_PLANTILLA_ERROR = r.plantilla_error || "";
  TECLAS_AVISO_SITIO = r.aviso_sitio || "";
  TECLAS_EDITABLES = r.editables || [];
  TECLAS_AVISO = r.aviso_no_editables || "";
  est.className = "";
  // Same honesty as the Control and Activities screens: say whether this is
  // the device or a file. `keys_model` reads `_control_blob()`, which
  // prefers the flash `control_estado_real()` just read -- so when the
  // remote is connected this really is the device, and when it isn't, this
  // says so instead of implying otherwise.
  est.innerHTML =
    (CONECTADO_MANDO
      ? nota(
          "info",
          "This is what your control does today, read out of its own flash " +
            "a moment ago."
        )
      : nota(
          "aviso",
          "<b>This is not read from your control.</b> Nothing is plugged in " +
            "right now, so what you see below comes from " +
            esc(r.referencia.origin || "the saved configuration") +
            ". You can look, but plug the remote in and tap <b>Connect</b> " +
            "on the <b>Control</b> tab before changing anything."
        )) +
    (r.foto_error
      ? nota(
          "alarma",
          "The remote's photo couldn't be built (" + esc(r.foto_error) +
            "): there is nothing to click on."
        )
      : "") +
    (r.paginas_error
      ? nota(
          "alarma",
          "The devices' own pages couldn't be read (" + esc(r.paginas_error) +
            "): keys can only be bound to an activity until that is fixed."
        )
      : "");
  $(".teclas-barra").hidden = false;
  teclasRepintar();

  // The listeners below are wired ONCE. `initTeclas()` now runs on every
  // Refresh (it has to: the key map comes from the blob, which Refresh
  // replaces), and `addEventListener` does not de-duplicate identical
  // arrow functions -- without this guard, the third Refresh would repaint
  // the screen three times per click.
  if (TECLAS_LISTENERS) return;
  TECLAS_LISTENERS = true;

  $("#teclas-actividad").addEventListener("change", (e) => {
    TECLAS_ACT = Number(e.target.value);
    TECLAS_SEL = null;
    teclasRepintar();
  });
  // The #teclas-dispositivo / #teclas-pantalla listeners lived here. Those
  // two <select>s no longer exist in the HTML, and `$()` returns null for a
  // missing id -- leaving these would have thrown a TypeError in the middle
  // of initTeclas() and killed the rest of the screen with no visible error.

  $("#mapeo-dispositivo").addEventListener("change", (e) => {
    const sc = $("#mapeo-comando");
    const v = e.target.value;
    if (v === "") {
      sc.disabled = true;
      sc.innerHTML = '<option value="">pick a device first</option>';
      $("#btn-mapeo-asignar").disabled = true;
      teclasPintarSitios(null);
      return;
    }
    const d = teclasDispositivo(Number(v));
    sc.disabled = false;
    sc.innerHTML =
      '<option value="">pick what it should do...</option>' +
      d.commands
        .map(
          (c) =>
            '<option value="' + c.k2 + '">' +
            esc(c.name || teclasNombreComando(d.k1, c.k2)) + "</option>"
        )
        .join("");
    $("#btn-mapeo-asignar").disabled = true;
    // The site list depends on WHICH device was picked: the first option is
    // that device's own page.
    teclasPintarSitios(TECLAS_SEL ? TECLAS_PEND.get(TECLAS_SEL) : null);
  });
  $("#mapeo-comando").addEventListener("change", (e) => {
    $("#btn-mapeo-asignar").disabled =
      e.target.value === "" || $("#mapeo-sitio").disabled;
  });

  $("#btn-mapeo-asignar").addEventListener("click", () => {
    if (!TECLAS_SEL || TECLAS_SEL.startsWith("bloq:")) return;
    if ($("#mapeo-dispositivo").value === "" || $("#mapeo-comando").value === "") return;
    const k1 = Number($("#mapeo-dispositivo").value);
    const k2 = Number($("#mapeo-comando").value);
    if (TECLAS_SEL.startsWith("fis:")) {
      const [, , c] = TECLAS_SEL.split(":").map(Number);
      const codHex = "0x" + Number(c).toString(16).toUpperCase().padStart(2, "0");
      const foto = TECLAS_FOTO && TECLAS_FOTO.foto;
      const t = foto && foto.keys.find((x) => x.codigo === codHex);
      // WHERE, chosen explicitly. Nothing is queued without a site that
      // Python said can work: `teclasPintarSitios` only lists those, and the
      // button is off when the list is empty.
      const clave = $("#mapeo-sitio").value;
      const sitio = teclasSitios(Number(c), k1).find((s) => s.clave === clave);
      if (!sitio || !sitio.editable) return;
      TECLAS_PEND.set(TECLAS_SEL, {
        kind: sitio.kind,
        sitio: sitio.clave,
        contexto: sitio.contexto,
        screen: sitio.screen,
        codigo: Number(c),
        k1: k1,
        k2: k2,
        rotulo: (t && t.label) || "unlabeled button",
        donde: sitio.donde,
        donde_frase:
          sitio.kind === "device"
            ? "whenever you're on " + sitio.donde + " (no activity needed)"
            : "only while the activity " + sitio.donde + " is running",
      });
    }
    TECLAS_RECHAZO = null;
    TECLAS_LISTO = false;
    teclasRepintar();
  });
  $("#btn-mapeo-quitar").addEventListener("click", () => {
    if (!TECLAS_SEL) return;
    TECLAS_PEND.delete(TECLAS_SEL);
    TECLAS_RECHAZO = null;
    TECLAS_LISTO = false;
    teclasRepintar();
  });
}

/* ============================ ACTIVITIES ===================================== */

let ACT = null;              // the last activities_list response
let ACT_ABIERTA = null;      // ordinal of the activity being edited
let ACT_RECHAZO = null;      // {clave, html} -- RULE 1: the rejection is remembered
let ACT_CONGELADA = false;   // after recording, the zone isn't repainted on its own

/* The signature of a change: as long as it stays the same, a rejection does
 * NOT offer the button again. Changing anything gives a different
 * signature and it can be retried; going back to the rejected combination
 * repaints the alarm. */
function actClave(ordinal, accion, argumento) {
  return ordinal + "|" + accion + "|" + (argumento === null || argumento === undefined ? "" : argumento);
}

/* The explanation the user asked for, in plain words and NOT hidden behind
 * a collapsed <details>: "I don't understand how I load activities onto it"
 * is answered by saying what an activity is here and where they come from,
 * before the list, not after it. */
function actPintarComo() {
  const el = $("#act-como");
  if (!el) return;
  el.innerHTML =
    "<p>An activity is one button that turns several devices on together " +
    "and puts each one on the right input -- \"Watch TV\" turns on the TV " +
    "and the sound bar and switches to HDMI 2.</p>" +
    "<p><b>You don't load activities onto the control from here, and this " +
    "app never invents one.</b> The activities below are the ones your " +
    "control already has, read out of its own configuration. They got " +
    "there from the factory or from Logitech's official software.</p>" +
    "<p>What you <i>can</i> do here: rename one, delete one, and change " +
    "which device each of its buttons drives. Every one of those goes on " +
    "the <b>Sync</b> list and is written in one go when you run Sync -- " +
    "nothing is written the moment you click.</p>";
}

async function cargarActividades() {
  if (ACT_CONGELADA) return;
  const cont = $("#act-lista");
  if (!cont) return;
  actPintarComo();

  // Same bug as the Control screen's, on this screen: `activities_list()`
  // always reads `_control_blob()` -- a file -- and never checked whether a
  // remote was actually connected, so this list looked like "your control's
  // activities" with the cable out. The connection state is NOT re-measured
  // here (that would mean a second full flash read): it's whatever
  // `actualizarEstadoControl()` last measured, which is the one place that
  // asks.
  if (!CONECTADO_MANDO) {
    ACT = null;
    $("#act-origen").innerHTML = "";
    cont.innerHTML = nota(
      "aviso",
      "<b>These would not be your control's activities.</b> Nothing is " +
        "plugged in right now, or its configuration didn't read back, so " +
        "there is nothing real to list. Plug your Harmony One in over USB " +
        "and read it -- it only reads, nothing is written." +
        '<p class="primer-uso-accion">' +
        '<button class="accion" data-leer-mando="1">Read my remote</button>' +
        "</p>"
    );
    $("#act-tarjeta-editar").hidden = true;
    $("#act-crear").innerHTML =
      '<span class="vacio">connect the control first</span>';
    return;
  }

  cont.innerHTML = '<span class="cargando">reading your control\'s configuration...</span>';
  const r = await llamar("activities_list");
  if (!r.ok) {
    ACT = null;
    cont.innerHTML = r.primer_uso
      ? notaPrimerUso(r)
      : nota("alarma", "<b>Couldn't read the activities.</b> " + esc(r.error));
    return;
  }
  ACT = r;
  $("#act-origen").innerHTML =
    "Source of this data: " + esc(r.referencia.origin) + ".";
  actPintarLista();
  actPintarCrear();
  actPintarMetodo();
}

function actNombreAparato(k1) {
  const d = (ACT.devices || []).find((x) => x.k1 === k1);
  return (d && d.name) || "device " + k1;
}

/* What the activity does with a device, in plain English. `es_encendido`
 * and the command's name come from Python; nothing here is translated by
 * hand. */
function actFrasePalanca(p) {
  if (/_Power_\d+$/.test(p.propiedad || "")) {
    return p.value ? "turns it on" : "turns it off";
  }
  if (/_Input_\d+$/.test(p.propiedad || "")) {
    return "switches it to " + esc(nombreDeEntrada(p.command, p.value));
  }
  return esc(p.propiedad || "?") + " = " + p.value +
    (p.command ? " (" + esc(p.command) + ")" : "");
}

/* The name of an input, the way it's written on the back of a TV.
 * The remote stores it as a number (0, 1, 2...) plus the command's own
 * name ("InputHdmi1"); the number is an internal position and saying it
 * out loud only ever confused -- input 0 IS HDMI 1. If there's no command
 * name to go on, it falls back to counting from 1, never from 0. */
function nombreDeEntrada(comando, valor) {
  if (!comando) return "input " + (Number(valor) + 1);
  let s = String(comando).replace(/^Input/, "");
  if (!s) return "input " + (Number(valor) + 1);
  s = s.replace(/([a-z])([A-Z0-9])/g, "$1 $2").replace(/([A-Z])(\d)/g, "$1 $2");
  return s.replace(/\b(hdmi|av|tv|dvi|vga|usb|ld|pc|rgb|cd|dvd|sat)\b/gi, (w) =>
    w.toUpperCase()
  );
}

function actPintarLista() {
  const cont = $("#act-lista");
  cont.innerHTML = (ACT.activities || [])
    .map((a) => {
      let cuerpo;
      if (!a.determinado) {
        cuerpo = nota(
          "aviso",
          "<b>It's not possible to tell which devices this activity touches.</b> " +
            "Its configuration doesn't store any device variable, so the app " +
            "can neither confirm nor rule it out. That's not the same as " +
            "&ldquo;uses none&rdquo;."
        );
      } else if (!a.devices.length) {
        cuerpo = '<p class="sub chico">It doesn\'t send any command to any device.</p>';
      } else {
        cuerpo =
          "<ul class='act-aparatos'>" +
          a.devices
            .map(
              (d) =>
                "<li><b>" + esc(d.name) + "</b>: " +
                (d.palancas.length
                  ? d.palancas.map(actFrasePalanca).join(", ")
                  : "sends it commands") +
                "</li>"
            )
            .join("") +
          "</ul>";
      }
      const etiquetas =
        chip(a.en_menu ? "in the menu" : "physical key", a.en_menu ? "" : "gris") +
        (a.name_in_blob ? "" : chip("name set by the app", "gris"));
      return (
        '<div class="item act-item">' +
        '<div class="item-cab"><b>' + esc(a.name) + "</b> " + etiquetas + "</div>" +
        cuerpo +
        '<div class="fila">' +
        (a.en_menu
          ? '<button class="accion chico" data-act-editar="' + a.ordinal + '">Edit</button>' +
            '<button class="accion chico peligro" data-act-borrar="' + a.ordinal + '">Delete</button>'
          : '<span class="vacio chico">Not in the control\'s menu (it hangs off a physical key): it can\'t be renamed or removed from here.</span>') +
        "</div></div>"
      );
    })
    .join("");
  $$("[data-act-editar]").forEach((b) =>
    b.addEventListener("click", () => actAbrirEditor(Number(b.dataset.actEditar)))
  );
  $$("[data-act-borrar]").forEach((b) =>
    b.addEventListener("click", () => actConfirmarBorrado(Number(b.dataset.actBorrar)))
  );
}

function actPintarCrear() {
  const el = $("#act-crear");
  const c = ACT.create || {};
  if (c.se_puede) {
    el.innerHTML = nota("ok", "A new activity can be created.");
    return;
  }
  el.innerHTML =
    nota(
      "aviso",
      "<b>Creating a new activity from the app isn't possible yet.</b> " +
        "It's not that it's unwritten: three things about the control need to " +
        "be known first, and each one is measured against your configuration."
    ) +
    "<ul class='act-faltan'>" +
    (c.missing || [])
      .map(
        (f) =>
          "<li><b>Missing " + esc(f.what) + ".</b>" +
          "<details class='chico'><summary>Why</summary>" +
          '<span class="vacio chico">' + esc(f.measured) + "</span>" +
          (f.salida_conocida
            ? "<br><span class='chico'>Possible path: " + esc(f.salida_conocida) + "</span>"
            : "<br><span class='chico'>No known path yet.</span>") +
          "</details></li>"
      )
      .join("") +
    "</ul>";
}

function actPintarMetodo() {
  const el = $("#act-metodo");
  const g = ACT.gold_check || {};
  const c = g.contingencia || {};
  const sh = g.shifts || {};
  el.classList.remove("vacio");
  el.innerHTML =
    "<p>The app does <b>not</b> hard-code which activities touch a device: " +
    "it follows the chain the configuration itself stores -- each activity's " +
    "entry hook writes state variables, and each variable has, in the state " +
    "machine, the infrared command it fires.</p>" +
    "<p>The two checks that keep this from being just a story:</p><ul>" +
    "<li><b>It separates.</b> Of the state-machine records that have " +
    "transitions, the ones tied to a variable with a device name reach a " +
    "command in <b>" + c.named_with_command + " of " +
    (c.named_with_command + c.named_without_command) + "</b> cases; the ones with " +
    "no name reach one in <b>" + c.unnamed_with_command + " of " +
    (c.unnamed_with_command + c.unnamed_without_command) + "</b>. Clean separation.</li>" +
    "<li><b>The index is the identity.</b> Lining up the record's index " +
    "with the variable's number, the declared bound matches the name " +
    "<b>" + (sh["0"] ? sh["0"][0] + " of " + sh["0"][1] : "?") + "</b>. " +
    "Shifting the index one slot either way, the best you get is " +
    Math.max(
      ...Object.keys(sh)
        .filter((k) => k !== "0")
        .map((k) => sh[k][0])
    ) +
    ".</li>" +
    (ACT.oraculo_ir
      ? "<li><b>An independent witness.</b> Each command's name " +
        "(&ldquo;InputHdmi1&rdquo;, &ldquo;PowerOn&rdquo;) doesn't come from the " +
        "variable's name: it comes from decoding the <b>infrared waveform</b> " +
        "saved on your control and looking it up in your Hub's configuration. " +
        "Both paths point to the same device.</li>"
      : "<li>Command names couldn't be resolved: the Hub's configuration is " +
        "missing on disk. Numbers are shown instead.</li>") +
    "</ul>" +
    "<p><b>What's not known:</b> what &ldquo;Turn everything off&rdquo; turns " +
    "off. Its entry hook doesn't write any device variable, so the chain " +
    "doesn't explain it. The app says so instead of assuming it uses " +
    "none.</p>";
}

/* ------------------------------------------------------------ edit ----- */

function actAbrirEditor(ordinal) {
  const a = (ACT.activities || []).find((x) => x.ordinal === ordinal);
  if (!a) return;
  ACT_ABIERTA = ordinal;
  const usados = new Set(a.devices.map((d) => d.k1));
  const palancas = ACT.palancas || {};

  // which devices COULD be added: the ones that have some power-on lever
  // in the state machine and aren't in the activity today.
  const agregables = Object.keys(palancas)
    .map(Number)
    .filter((k1) => !usados.has(k1) && palancas[String(k1)].some((p) => p.es_encendido));

  let html =
    "<h3>Edit &ldquo;" + esc(a.name) + "&rdquo;</h3>" +
    '<p class="sub chico">Each change is prepared and verified separately. ' +
    "They're applied one at a time: this one first, and once you record it, the next.</p>";

  // --- the name
  html +=
    '<div class="act-bloque"><h4>What it\'s called</h4>' +
    '<label for="act-nombre">Name in the control\'s menu</label>' +
    '<input type="text" id="act-nombre" spellcheck="false" value="' + esc(a.name) + '" />' +
    '<p class="sub chico">The control doesn\'t draw <b>Q</b>, <b>X</b>, or <b>Z</b> ' +
    "in any of its fonts. If the name can't be drawn, this rejects it " +
    "before touching anything.</p>" +
    '<div class="fila"><button class="accion" id="act-btn-renombrar">Change the name</button></div></div>';

  // --- the devices it already uses
  html += '<div class="act-bloque"><h4>Which devices it turns on</h4>';
  if (!a.determinado) {
    html += nota(
      "aviso",
      "It's not known which devices this activity touches, so the list " +
        "can't be edited without risk."
    );
  } else if (!a.devices.length) {
    html += '<p class="sub chico">Today it doesn\'t turn on any.</p>';
  } else {
    html += "<ul class='act-aparatos'>";
    a.devices.forEach((d) => {
      const lista = palancas[String(d.k1)] || [];
      const entrada = lista.find((p) => !p.es_encendido && p.values.length > 1);
      const power = lista.find((p) => p.es_encendido);
      const puestoEntrada = d.palancas.find((p) => /_Input_\d+$/.test(p.propiedad || ""));
      html +=
        "<li><b>" + esc(d.name) + "</b> &mdash; " +
        d.palancas.map(actFrasePalanca).join(", ") +
        "<div class='fila'>";
      if (entrada) {
        html +=
          '<select data-act-entrada="' + d.k1 + '" data-act-prop="' + esc(entrada.name) + '">' +
          entrada.values
            .map(
              (v) =>
                // `v.value` stays the option's value -- that IS the payload
                // that gets sent back. Only the label changes.
                '<option value="' + v.value + '"' +
                (puestoEntrada && puestoEntrada.value === v.value ? " selected" : "") +
                ">" + esc(nombreDeEntrada(v.command, v.value)) +
                "</option>"
            )
            .join("") +
          "</select>" +
          '<button class="accion chico" data-act-guardar-entrada="' + d.k1 + '">Change the input</button>';
      }
      if (power) {
        html +=
          '<button class="accion chico peligro" data-act-sacar="' + d.k1 +
          '" data-act-prop="' + esc(power.name) + '">Remove ' + esc(d.name) + " from this activity</button>";
      }
      html += "</div></li>";
    });
    html += "</ul>";
  }
  html += "</div>";

  // --- the ones that could be added
  html += '<div class="act-bloque"><h4>Add a device</h4>';
  if (agregables.length) {
    html +=
      '<div class="fila">' +
      agregables
        .map(
          (k1) =>
            '<button class="accion chico" data-act-agregar="' + k1 +
            '" data-act-prop="' +
            esc(palancas[String(k1)].find((p) => p.es_encendido).name) +
            '">Add ' + esc(actNombreAparato(k1)) + "</button>"
        )
        .join("") +
      "</div>";
  } else {
    const sinPalanca = (ACT.devices || [])
      .filter((d) => !palancas[String(d.k1)])
      .map((d) => d.name || "device " + d.k1);
    html += nota(
      "aviso",
      "<b>There's no other device to add.</b>" +
        (sinPalanca.length
          ? " " + esc(sinPalanca.join(", ")) + (sinPalanca.length > 1 ? " don't have" : " doesn't have") +
            " any state variable in the control's configuration, so an " +
            "activity can't turn them on or change their input. That's " +
            "what happens to devices you added yourself: the control " +
            "manages them by hand, not from an activity."
          : "")
    );
  }
  html += "</div>";

  html += '<div class="fila"><button class="accion" id="act-btn-cerrar">Close</button></div>';
  html += "<div id='act-resultado'></div>";

  $("#act-tarjeta-editar").hidden = false;
  $("#act-editar").innerHTML = html;
  $("#act-tarjeta-editar").scrollIntoView({ behavior: "smooth", block: "nearest" });

  $("#act-btn-cerrar").addEventListener("click", () => {
    ACT_ABIERTA = null;
    $("#act-tarjeta-editar").hidden = true;
    $("#act-editar").innerHTML = "";
  });
  $("#act-btn-renombrar").addEventListener("click", () =>
    actPreparar(ordinal, "renombrar", $("#act-nombre").value.trim(), "act-btn-renombrar")
  );
  $$("[data-act-guardar-entrada]").forEach((b) =>
    b.addEventListener("click", () => {
      const k1 = b.dataset.actGuardarEntrada;
      const sel = $('[data-act-entrada="' + k1 + '"]');
      actPreparar(
        ordinal,
        "change_value",
        sel.dataset.actProp + "=" + sel.value,
        null,
        b
      );
    })
  );
  $$("[data-act-sacar]").forEach((b) =>
    b.addEventListener("click", () =>
      actPreparar(ordinal, "remove_set", b.dataset.actProp, null, b)
    )
  );
  $$("[data-act-agregar]").forEach((b) =>
    b.addEventListener("click", () =>
      actPreparar(ordinal, "add_set", b.dataset.actProp + "=1", null, b)
    )
  );

  if (ACT_RECHAZO && ACT_RECHAZO.ordinal === ordinal) {
    $("#act-resultado").innerHTML = ACT_RECHAZO.html;
  }
}

/* ------------------------------------------------------------ delete --- */

function actConfirmarBorrado(ordinal) {
  const a = (ACT.activities || []).find((x) => x.ordinal === ordinal);
  if (!a) return;
  ACT_ABIERTA = ordinal;
  const aparatos = a.determinado
    ? (a.devices.length
        ? a.devices.map((d) => d.name).join(", ")
        : "no device")
    : null;
  $("#act-tarjeta-editar").hidden = false;
  $("#act-editar").innerHTML =
    nota(
      "aviso",
      "<b>Are you sure you want to remove &ldquo;" + esc(a.name) + "&rdquo; from the control?</b>" +
        "<ul style='margin:8px 0 0 18px;padding:0'>" +
        "<li>It disappears from the activities menu and can no longer be started.</li>" +
        (aparatos
          ? "<li>The devices it turned on (" + esc(aparatos) + ") are <b>not</b> " +
            "deleted: they stay in the device list and keep being managed by hand.</li>"
          : "<li>It's not known which devices it touched, but no device gets deleted.</li>") +
        "<li>The other activities stay intact: that's verified before " +
        "offering to record.</li>" +
        "</ul><p style='margin:10px 0 0'>The control isn't touched yet: first " +
        "the file gets prepared and verified. If something doesn't add up, this goes no further.</p>"
    ) +
    '<div class="fila">' +
    '<button class="accion peligro" id="act-btn-borrar-si">Yes, remove ' + esc(a.name) + "</button>" +
    '<button class="accion" id="act-btn-borrar-no">Better not</button>' +
    "</div><div id='act-resultado'></div>";
  $("#act-btn-borrar-si").addEventListener("click", () =>
    actPreparar(ordinal, "erase", null, "act-btn-borrar-si", null, "act-btn-borrar-no")
  );
  $("#act-btn-borrar-no").addEventListener("click", () => {
    ACT_ABIERTA = null;
    $("#act-tarjeta-editar").hidden = true;
    $("#act-editar").innerHTML = "";
  });
  if (ACT_RECHAZO && ACT_RECHAZO.ordinal === ordinal) {
    $("#act-resultado").innerHTML = ACT_RECHAZO.html;
  }
}

/* ------------------------------------------------ prepare and verify ---- */

async function actPreparar(ordinal, accion, argumento, idBoton, boton, idOtro) {
  const res = $("#act-resultado");
  const b = idBoton ? $("#" + idBoton) : boton;
  const clave = actClave(ordinal, accion, argumento);

  if (ACT_RECHAZO && ACT_RECHAZO.clave === clave) {
    // RULE 1: the same already-rejected combination doesn't offer anything again.
    res.innerHTML = ACT_RECHAZO.html;
    return;
  }
  if (b) ocupado(b, true, "adding to Sync...");

  // Goes on the Sync list instead of preparing + writing on its own. The
  // gate is not skipped: it runs once, over this change together with
  // everything else queued, inside `sync_preparar()`.
  const enc = await agregarCambio("edit_activity", {
    ordinal: ordinal,
    accion: accion,
    argumento: argumento === undefined ? null : argumento,
  });
  if (b) ocupado(b, false);
  if (!enc.ok) {
    res.innerHTML = nota(
      "alarma",
      "<b>Couldn't queue that change.</b> " + esc(enc.error)
    );
    return;
  }
  if (b) b.remove();
  if (idOtro && $("#" + idOtro)) $("#" + idOtro).remove();
  ACT_RECHAZO = null;
  ACT_CONGELADA = true; // this zone keeps its result until the screen reloads
  res.innerHTML = nota(
    "ok",
    "<b>Added to Sync.</b> " + esc((enc.change && enc.change.label) || "") +
      ". Nothing has been written to the control: run <b>Sync</b> when " +
      "you've made all the changes you want."
  );
  res.scrollIntoView({ behavior: "smooth", block: "center" });
}

/* The old body of `actPreparar` -- prepare + gate + write, for ONE activity
 * change on its own -- was deleted here, not commented out. That path now
 * lives in Sync, once, for the whole batch. `activity_prepare` in Python is
 * untouched and still works; nothing in the UI calls it any more.
 */

function actPintarDetalle(r) {
  const el = $("#act-detalle-tecnico");
  if (!el) return;
  el.classList.remove("vacio");
  const t = (r && r.technical_detail) || {};
  const g = (r && r.generar) || t.editar || {};
  const c = (r && r.gate) || t.gate || {};
  el.innerHTML =
    "<h4>Reference used</h4><pre>" +
    esc(((r && r.referencia) || {}).blob || "") + "</pre>" +
    "<h4>edit_activity.py</h4><pre>" + esc(g.command || "") + "</pre>" +
    "<pre>" + esc(g.stdout || g.stderr || "") + "</pre>" +
    "<h4>--repunta detected</h4><pre>" + esc((g.repuntes || []).join(" ")) + "</pre>" +
    "<h4>Gate (grabar.nada_se_movio)</h4><pre>" +
    esc(JSON.stringify(c, null, 1)) + "</pre>" +
    (r && r.command
      ? "<h4>Recording command</h4><pre>cd " + esc(r.command.cwd) + "\n" +
        esc(r.command.command) + "</pre>"
      : "");
}


main();
