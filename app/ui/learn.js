"use strict";

/* Learn screen -- captures IR codes with the Harmony One's RECEIVER.
 *
 * A separate file from `app.js` on purpose: it doesn't touch a single line
 * of the other four screens. It reuses the global helpers `app.js` already
 * declares (`$`, `$$`, `esc`, `chip`, `nota`, `llamar`, `ocupado`) -- in a
 * classic script, top-level declarations are shared, so it's enough to load
 * this AFTER `app.js`.
 *
 * Three rules that live in the code, not just in a comment:
 *   1. **Two captures or nothing.** A command isn't accepted until two
 *      captures of the same key in a row give the same result
 *      (`learn_compare`). If they don't match, it asks again. Saving just
 *      one means saving the noise.
 *   2. **The name is validated BEFORE.** While it's typed, against the
 *      fonts and the glyph table of the real blob. The save button doesn't
 *      exist in the DOM while the name doesn't pass.
 *   3. **The test bench is disclosed.** Every simulated capture gets marked
 *      `simulado` and the screen repeats it in the list and in the summary:
 *      a waveform from the blob can never be mistaken for one captured from
 *      the air.
 */

/* ------------------------------------------------------------ state --- */

let APR = null; // what learn_status() returns
let APR_HECHOS = {}; // {commandName: {etiqueta, analisis, comparacion, simulado}}
let APR_CANCELAR = false;

/* ----------------------------------------------------------- the waveform --- */

/** `[[isMark, us], ...]` -> an SVG. Marks are filled bars; spaces are gaps.
 *  The axis is linear in time, so a gap between frames looks like what it
 *  is: a long empty stretch. Frame cuts are marked with a dashed line at
 *  the measured threshold (`umbral_hueco_us`). */
function aprOndaSVG(atomos, ancho, alto) {
  ancho = ancho || 560;
  alto = alto || 62;
  if (!atomos || !atomos.length) return '<div class="vacio">no waveform</div>';
  const total = atomos.reduce((a, x) => a + x[1], 0) || 1;
  const k = ancho / total;
  const y0 = 8;
  const h = alto - 20;
  let t = 0;
  let barras = "";
  let cortes = "";
  const umbral = (APR && APR.gap_threshold_us) || 10000;
  for (const [esMarca, us] of atomos) {
    const x = t * k;
    const w = Math.max(us * k, 0.35);
    if (esMarca) {
      barras += '<rect class="marca" x="' + x.toFixed(2) + '" y="' + y0 +
        '" width="' + w.toFixed(2) + '" height="' + h + '" />';
    } else if (us >= umbral) {
      const xm = (t + us / 2) * k;
      cortes += '<line class="corte" x1="' + xm.toFixed(2) + '" y1="2" x2="' +
        xm.toFixed(2) + '" y2="' + (alto - 8) + '" />';
    }
    t += us;
  }
  return (
    '<svg class="apr-onda" viewBox="0 0 ' + ancho + " " + alto +
    '" preserveAspectRatio="none" role="img" aria-label="captured waveform">' +
    '<line class="eje" x1="0" y1="' + (alto - 8) + '" x2="' + ancho + '" y2="' + (alto - 8) + '" />' +
    cortes + barras + "</svg>"
  );
}

/** The waveform's footer: the numbers and, above all, whether the protocol was recognized. */
function aprPieOnda(an) {
  const r = an.summary || {};
  const partes = [
    r.atomos + " timings",
    (r.duracion_us / 1000).toFixed(1) + " ms",
    r.tramas + " frame" + (r.tramas === 1 ? "" : "s"),
  ];
  if (an.carrier_hz) partes.push(an.carrier_hz + " Hz");
  return '<div class="apr-onda-pie">' + esc(partes.join("  ·  ")) + "</div>";
}

/** Recognized or not: this is what decides whether a clean waveform or noise gets saved. */
function aprChipProtocolo(an) {
  const rec = an && an.reconocido;
  if (!rec) {
    return (
      chip("protocol not recognized", "espera") +
      '<div class="apr-onda-pie">the RAW waveform will be saved exactly as ' +
      "captured, with whatever noise it has</div>"
    );
  }
  const tienelo = APR && APR.protocolos && APR.protocolos[rec.protocolo];
  return (
    chip("recognized: " + rec.protocolo, "si") +
    '<div class="apr-onda-pie">' +
    (tienelo
      ? "the library has the factory timings for this protocol: the " +
        "regenerated CLEAN waveform gets saved, not the capture"
      : "recognized, but the library doesn't have that protocol's " +
        "definition: the raw waveform gets saved") +
    "  ·  " + esc(rec.via) + "</div>"
  );
}

/* -------------------------------------------------- 1. the device ------ */

let aprTimerValidar = null;

function aprEtiquetasElegidas() {
  return Object.values(APR_HECHOS).map((x) => x.label);
}

async function aprValidarNombre() {
  const caja = $("#apr-validacion");
  const nombre = $("#apr-nombre").value.trim();
  if (!nombre) {
    caja.innerHTML = '<div class="vacio">type the name you\'ll see on the control</div>';
    aprPintarGuardar();
    return;
  }
  const r = await llamar("learn_validate", nombre, aprEtiquetasElegidas());
  if (!r.ok) {
    caja.innerHTML = nota("alarma", esc(r.error));
    aprPintarGuardar();
    return;
  }
  APR_VALIDACION = r;
  // `r.ok` means "the call went through"; `r.valido` means "the name can be
  // written". These are two different things, and confusing them painted an
  // empty error note.
  if (r.valido && !(r.problemas || []).length) {
    caja.innerHTML = nota(
      "ok",
      "<b>" + esc(nombre) + "</b> can be shown: there's a font that draws " +
        "all its letters and the control knows how to write them."
    );
  } else {
    let filas = "";
    for (const d of r.problemas || []) {
      const faltan = []
        .concat(d.dibuja_ok ? [] : d.draw_missing)
        .concat(d.escribe_ok ? [] : d.write_missing);
      const unicas = Array.from(new Set(faltan));
      filas +=
        "<li><b>" + esc(d.text) + "</b> (" + esc(d.what) + "): " +
        (unicas.length
          ? 'not possible with <span class="apr-letras">' +
            esc(unicas.join(" ")) + "</span>"
          : esc(d.draw_warning || "not possible")) +
        (d.dibuja_ok
          ? " &mdash; the font draws them but the control doesn't know how " +
            "to write them (they're missing from the glyph table: download " +
            "a device from the catalog and there will be more vocabulary)"
          : " &mdash; no font on the device has those strokes") +
        "</li>";
    }
    caja.innerHTML = nota(
      "alarma",
      "This won't be possible to write on the control:<ul>" + filas + "</ul>"
    );
  }
  aprPintarGuardar();
}

let APR_VALIDACION = null;

/* ----------------------------------------------------- 2. the commands - */

function aprPintarComandos() {
  const cont = $("#apr-comandos");
  if (!APR) return;
  const grupos = {};
  for (const c of APR.commands) {
    (grupos[c.grupo] = grupos[c.grupo] || []).push(c);
  }
  let html = "";
  for (const g of Object.keys(grupos)) {
    html += '<div class="apr-grupo"><h4>' + esc(g) + '</h4><div class="apr-rejilla">';
    for (const c of grupos[g]) {
      const hecho = APR_HECHOS[c.name];
      const clase = hecho ? " listo" : "";
      let sub;
      if (hecho) {
        const rec = hecho.analisis.reconocido;
        sub =
          (rec ? "recognized: " + rec.protocolo : "raw waveform") +
          (hecho.simulado ? "  ·  BENCH" : "");
      } else {
        sub = "not learned";
      }
      html +=
        '<div class="apr-cmd' + clase + '"><div class="apr-cmd-izq">' +
        '<div class="apr-cmd-etq">' + esc(c.label) + "</div>" +
        '<div class="apr-cmd-sub">' + esc(sub) + "</div></div>" +
        '<button class="accion' + (hecho ? "" : " primaria") +
        '" data-apr-cmd="' + esc(c.name) + '">' +
        (hecho ? "redo" : "learn") + "</button></div>";
    }
    html += "</div></div>";
  }
  cont.innerHTML = html;
  $$("[data-apr-cmd]").forEach((b) => {
    b.addEventListener("click", () => aprAprenderComando(b.dataset.aprCmd));
  });
  aprPintarGuardar();
}

/* ------------------------------------------------------- the modal ------ */

function aprModal(html) {
  $("#apr-modal-cuerpo").innerHTML = html;
  $("#apr-modal").hidden = false;
}

function aprCerrarModal() {
  $("#apr-modal").hidden = true;
  $("#apr-modal-cuerpo").innerHTML = "";
}

function aprCabecera(etiqueta, paso) {
  return (
    "<h3>" + esc(etiqueta) + "</h3>" +
    '<div class="apr-paso">' + esc(paso) + "</div>"
  );
}

function aprBotonCerrar(texto) {
  return (
    '<div class="fila"><button class="accion" id="apr-cerrar">' +
    esc(texto || "close") + "</button></div>"
  );
}

function aprEngancharCerrar() {
  const b = $("#apr-cerrar");
  if (b) b.addEventListener("click", aprCerrarModal);
}

/** Waits for the user to hit "I'm pointing now" before blocking. libconcord
 *  doesn't emit any prompt and the call is synchronous: if it fires without
 *  warning, the 5-second window runs out before anyone points. */
function aprEsperarListo(etiqueta, paso, banco) {
  return new Promise((resolve) => {
    let extra = "";
    if (banco) {
      extra =
        nota(
          "aviso",
          "<b>Test bench.</b> The remote won't be touched: the sample " +
            "waveform you picked -- one your control already had -- will be " +
            "used instead. Good for seeing " +
            "the whole screen without hardware, not for adding a real device."
        ) + aprSelectorBanco("apr-banco-modal");
    }
    aprModal(
      aprCabecera(etiqueta, paso) +
        '<p class="apr-instruccion">' + esc(APR.textos.apuntar) + "</p>" +
        extra +
        '<div class="fila"><button class="accion primaria" id="apr-ya">' +
        (banco ? "use this waveform" : "I'm pointing now -- capture") +
        '</button><button class="accion" id="apr-cerrar">cancel</button></div>'
    );
    $("#apr-ya").addEventListener("click", () => {
      const sel = $("#apr-banco-modal");
      resolve({ seguir: true, offset: sel ? Number(sel.value) : null });
    });
    $("#apr-cerrar").addEventListener("click", () => {
      aprCerrarModal();
      resolve({ seguir: false });
    });
  });
}

function aprSelectorBanco(id) {
  if (!APR || !APR.banco || !APR.banco.length) {
    return '<div class="vacio">your control has no sample waveforms to offer</div>';
  }
  let ops = "";
  APR.banco.forEach((b, i) => {
    ops +=
      '<option value="' + b.offset + '">' +
      esc("Sample " + (i + 1) + " -- " + b.protocolo) +
      "</option>";
  });
  return (
    '<label for="' + id + '">A real waveform your control already had</label>' +
    '<select id="' + id + '">' + ops + "</select>"
  );
}

/** A single capture: real (subprocess -> libconcord) or from the bench. */
async function aprUnaCaptura(nombre, offset) {
  if (offset !== null && offset !== undefined) {
    const sesgo = $("#apr-banco-sesgo") && $("#apr-banco-sesgo").checked ? 1200 : 0;
    return await llamar("learn_capture_bank", offset, sesgo);
  }
  return await llamar("learn_capture", nombre);
}

/** The full flow for a command: two captures, a comparison, and only then
 *  is it accepted. This is point 3 of the requirement, and it lives here
 *  instead of in Python because it's the part that talks to the user --
 *  the decision of whether they match is made by Python (`learn_compare`). */
async function aprAprenderComando(nombre) {
  const cmd = APR.commands.find((c) => c.name === nombre);
  if (!cmd) return;
  const banco = $("#apr-usar-banco").checked;
  APR_CANCELAR = false;

  const capturas = [];
  for (let i = 0; i < 2; i++) {
    const listo = await aprEsperarListo(
      cmd.label,
      "capture " + (i + 1) + " of 2",
      banco
    );
    if (!listo.seguir) return;

    aprModal(
      aprCabecera(cmd.label, "capture " + (i + 1) + " of 2") +
        '<p class="apr-instruccion"><span class="apr-latido"></span>' +
        (banco
          ? "reading a waveform your control already had..."
          : "listening on the receiver... press the key NOW (up to 5 seconds)") +
        "</p>"
    );

    const r = await aprUnaCaptura(nombre, banco ? listo.offset : null);
    if (!r.ok) {
      aprModal(
        aprCabecera(cmd.label, "capture " + (i + 1) + " of 2") +
          nota("alarma", "<b>Couldn't capture.</b><br>" + esc(r.error)) +
          nota(
            "aviso",
            "If the remote isn't plugged in, or is busy with another " +
              "operation, this is what you'll see. You can also use the " +
              "test bench to go through the screen without hardware."
          ) +
          aprBotonCerrar()
      );
      aprEngancharCerrar();
      return;
    }
    capturas.push(r.analisis);

    // The waveform is shown as soon as it arrives: the user sees WHAT came in, not a progress bar.
    let avisos = "";
    for (const a of r.analisis.avisos || []) avisos += nota("aviso", esc(a));
    aprModal(
      aprCabecera(cmd.label, "capture " + (i + 1) + " of 2  ·  received") +
        aprOndaSVG(r.analisis.atomos) +
        aprPieOnda(r.analisis) +
        '<div style="margin-top:10px">' + aprChipProtocolo(r.analisis) + "</div>" +
        avisos +
        (r.simulada
          ? nota("aviso", "<b>Test bench</b>: this waveform came from your control's own configuration, not from the air.")
          : "") +
        '<div class="fila"><button class="accion primaria" id="apr-seguir">' +
        (i === 0 ? "now the second capture" : "compare the two") +
        '</button><button class="accion" id="apr-cerrar">cancel</button></div>'
    );
    const seguir = await new Promise((res) => {
      $("#apr-seguir").addEventListener("click", () => res(true));
      $("#apr-cerrar").addEventListener("click", () => {
        aprCerrarModal();
        res(false);
      });
    });
    if (!seguir) return;
  }

  // -- the gate from point 3: two captures or nothing gets saved ----------
  const cmp = await llamar("learn_compare", capturas[0], capturas[1]);
  if (!cmp.ok) {
    aprModal(
      aprCabecera(cmd.label, "comparison") +
        nota("alarma", esc(cmp.error)) + aprBotonCerrar()
    );
    aprEngancharCerrar();
    return;
  }

  const lado = (an, n) =>
    '<div style="margin-top:12px"><div class="apr-onda-pie">capture ' + n +
    "</div>" + aprOndaSVG(an.atomos, 560, 46) + aprPieOnda(an) + "</div>";

  if (!cmp.coinciden) {
    aprModal(
      aprCabecera(cmd.label, "don't match") +
        nota(
          "alarma",
          "<b>The two captures don't match, so it's not accepted.</b><br>" +
            esc(cmp.detail) +
            "<br><br>Saving an unconfirmed capture means saving whatever " +
            "noise it had, and afterward the device won't respond. Try " +
            "again: bring the original remote close, aim at the top tip of " +
            "the Harmony, and press the key just once."
        ) +
        lado(capturas[0], 1) + lado(capturas[1], 2) +
        '<div class="fila"><button class="accion primaria" id="apr-reintentar">' +
        "try again</button><button class=\"accion\" id=\"apr-cerrar\">leave it</button></div>"
    );
    $("#apr-reintentar").addEventListener("click", () => {
      aprCerrarModal();
      aprAprenderComando(nombre);
    });
    aprEngancharCerrar();
    return;
  }

  // -- accepted --------------------------------------------------------------
  APR_HECHOS[nombre] = {
    name: nombre,
    label: cmd.label,
    analisis: capturas[1],
    comparacion: cmp,
    simulado: !!capturas[1].simulada || banco,
  };
  aprPintarComandos();
  aprValidarNombre();

  aprModal(
    aprCabecera(cmd.label, "accepted") +
      nota("ok", "<b>The two captures match.</b><br>" + esc(cmp.detail)) +
      aprOndaSVG(capturas[1].atomos) +
      aprPieOnda(capturas[1]) +
      '<div style="margin-top:10px">' + aprChipProtocolo(capturas[1]) + "</div>" +
      aprBotonCerrar("done")
  );
  aprEngancharCerrar();
}

/* --------------------------------------------------------- 3. save ----- */

function aprPintarGuardar() {
  const cont = $("#apr-resumen");
  const zona = $("#apr-zona-guardar");
  const n = Object.keys(APR_HECHOS).length;
  const fab = $("#apr-fab").value.trim();
  const mod = $("#apr-mod").value.trim();
  const nom = $("#apr-nombre").value.trim();
  const nombreOk = APR_VALIDACION && APR_VALIDACION.valido;
  const simulados = Object.values(APR_HECHOS).filter((x) => x.simulado).length;

  if (!n) {
    cont.innerHTML = '<div class="vacio">there\'s no accepted command yet</div>';
  } else {
    const limpios = Object.values(APR_HECHOS).filter(
      (x) => x.analisis.reconocido && APR.protocolos[x.analisis.reconocido.protocolo]
    ).length;
    cont.innerHTML =
      '<dl class="datos">' +
      "<dt>commands</dt><dd>" + n + "</dd>" +
      "<dt>clean waveform</dt><dd>" + limpios + " (protocol recognized, regenerated from factory)</dd>" +
      "<dt>raw waveform</dt><dd>" + (n - limpios) + " (recorded exactly as captured)</dd>" +
      (simulados ? "<dt>from the bench</dt><dd>" + simulados + " -- NOT real captures</dd>" : "") +
      "</dl>" +
      (simulados
        ? nota(
            "alarma",
            "<b>" + simulados + " command(s) came from the test bench</b>, not from the " +
              "receiver. They're good for testing the screen; if you save them, the " +
              "device will repeat codes the remote already had."
          )
        : "");
  }

  // Rule 1 of this screen: the button doesn't stay disabled, it isn't drawn.
  const puede = n > 0 && fab && mod && nom && nombreOk;
  zona.innerHTML = puede
    ? '<button class="accion primaria" id="apr-guardar">Save the device</button>'
    : '<div class="vacio">missing: ' +
      esc(
        [
          n ? null : "learn at least one command",
          fab ? null : "manufacturer",
          mod ? null : "model",
          nom ? null : "name",
          !nom || nombreOk ? null : "a name that can be written on the control",
        ]
          .filter(Boolean)
          .join(", ")
      ) + "</div>";
  const b = $("#apr-guardar");
  if (b) b.addEventListener("click", aprGuardar);
}

async function aprGuardar() {
  const btn = $("#apr-guardar");
  ocupado(btn, true, "saving...");
  const r = await llamar(
    "learn_save",
    $("#apr-fab").value.trim(),
    $("#apr-mod").value.trim(),
    $("#apr-nombre").value.trim(),
    Object.values(APR_HECHOS).map((x) => ({
      name: x.name,
      label: x.label,
      analisis: x.analisis,
      comparacion: x.comparacion,
      simulado: x.simulado,
    }))
  );
  ocupado(btn, false);
  const caja = $("#apr-guardado");
  if (!r.ok) {
    let extra = "";
    if (r.validacion && r.validacion.problemas) {
      for (const d of r.validacion.problemas) {
        extra +=
          "<li><b>" + esc(d.text) + "</b>: missing " +
          '<span class="apr-letras">' +
          esc(
            Array.from(
              new Set([].concat(d.draw_missing || [], d.write_missing || []))
            ).join(" ")
          ) + "</span></li>";
      }
      extra = extra ? "<ul>" + extra + "</ul>" : "";
    }
    caja.innerHTML = nota("alarma", "<b>Wasn't saved.</b><br>" + esc(r.error) + extra);
    return;
  }
  caja.innerHTML =
    nota(
      "ok",
      // `r.target` keeps arriving from Python and is still part of the
      // answer -- it just isn't shown. A folder path on this computer is
      // nothing the person needs in order to add their TV.
      "<b>Saved.</b> " + esc(r.commands) + " command(s): " +
        esc(r.limpios) + " with clean waveform and " + esc(r.crudos) + " with raw waveform." +
        "<br>It now shows up in <b>Control</b> like any other device: " +
        "choose it, apply it, and the button that writes to your remote only " +
        "appears once everything checks out."
    ) + (r.raw_warning ? nota("aviso", esc(r.raw_warning)) : "");
}

/* ---------------------------------------------------------- startup ---- */

async function aprIniciar() {
  // Already initialized: revalidate anyway, because the banner can go stale.
  //
  // QUIRK MEASURED in the real window (with the title changed to avoid
  // confusing it with another instance, and with a sentinel value written by
  // JS to prove the snapshot was of this window): WebKit **restores** what
  // was typed in a previous run and DRAWS it, but `input.value` read from JS
  // keeps returning "" until the field is actually touched. In other words:
  // the fields look filled in and the banner says "type the name", and both
  // are consistent with what the engine reports. This can't be fixed from
  // JS by reading harder; what can be done is revalidate as soon as there's
  // any interaction (here, and in `change`/`focus` below), which is when the
  // restored value finally becomes visible to the script.
  //
  // It's PURELY cosmetic and fails CLOSED: the save button isn't drawn if
  // validation didn't come back green, and `learn_save` validates again on
  // the Python side before writing anything.
  if (APR) {
    aprValidarNombre();
    return;
  }
  const r = await llamar("learn_status");
  if (!r.ok) {
    $("#apr-comandos").innerHTML = nota("alarma", esc(r.error));
    return;
  }
  APR = r;
  $("#apr-sin-probar").innerHTML =
    "<b>Not tested against the device.</b> " + esc(r.textos.sin_probar);
  $("#apr-dos-veces").textContent = r.textos.dos_veces;
  $("#apr-banco-select").innerHTML = aprSelectorBanco("apr-banco-lista");
  $("#apr-blob").textContent = r.blob_origen || r.blob;
  aprPintarComandos();
  aprValidarNombre();
}

$('button.nav[data-p="aprender"]').addEventListener("click", aprIniciar);

// `change` and `focus` in addition to `input`: the values WebKit restores
// don't fire `input`, so without this the banner can be left talking about
// an empty field that no longer is one (see the comment in `aprIniciar`).
["apr-nombre", "apr-fab", "apr-mod"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  const revalidar = () => {
    clearTimeout(aprTimerValidar);
    aprTimerValidar = setTimeout(aprValidarNombre, 220);
  };
  el.addEventListener("input", revalidar);
  el.addEventListener("change", revalidar);
  el.addEventListener("focus", revalidar);
});
