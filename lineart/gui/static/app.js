// Lineart Studio – frontend for lineart.gui.server (no build step, no deps).
"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid);
  return e;
};

const S = {
  meta: null,
  status: null,
  cfg: null,              // working config (source of truth while editing)
  preset: "medium",
  stage: "preprocess",
  views: {},              // stage id -> selected view id
  runMode: "stage",
  advOpen: {},            // stage id -> advanced section open
  natural: null,          // {w, h} of the displayed image
  tf: { x: 0, y: 0, s: 1 },
  busySince: null,
};

try {
  S.runMode = localStorage.getItem("lineart.runMode") || "stage";
  S.views = JSON.parse(localStorage.getItem("lineart.views") || "{}");
} catch (_) { /* storage unavailable */ }
const store = (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} };

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, body, opts = {}) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: opts.headers || { "Content-Type": "application/json" },
    body: opts.raw ? body : JSON.stringify(body),
  };
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

const stageSpec = (id) => S.meta.stages.find((s) => s.id === id);
const stageIndex = (id) => S.meta.stages.findIndex((s) => s.id === id);
const stageState = (id) => S.status?.stages.find((s) => s.id === id) || { status: "missing" };

function getParam(cfg, key) {
  const [a, b] = key.split(".");
  return b ? cfg[a][b] : cfg[a];
}
function setParam(cfg, key, v) {
  const [a, b] = key.split(".");
  if (b) cfg[a][b] = v; else cfg[a] = v;
}
const clone = (o) => JSON.parse(JSON.stringify(o));

// ---------------------------------------------------------------------------
// Status handling & polling
// ---------------------------------------------------------------------------
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      applyStatus(await api("/api/status"));
      if (!S.status.busy) break;
      await new Promise((r) => setTimeout(r, 350));
    }
  } catch (e) {
    showError(e.message);
  } finally {
    polling = false;
  }
}

function applyStatus(st) {
  const wasBusy = S.status?.busy;
  S.status = st;
  if (st.busy && !wasBusy) S.busySince = Date.now();
  if (!st.busy) S.busySince = null;
  showError(st.error);
  renderStepper();
  renderProgress();
  renderInfo();
  renderStageRunButton();
  updateImage();
}

let pushTimer = null;
function schedulePush(delay) {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(push, delay);
}

// Send the working config; start computing according to the run mode.
async function push(forceRun = null) {
  clearTimeout(pushTimer);
  const body = { config: S.cfg, preset: S.preset };
  const target = forceRun || (S.runMode === "all" ? "style" : S.runMode === "stage" ? S.stage : null);
  if (target) body.run = target;
  try {
    applyStatus(await api("/api/config", body));
    if (S.status.busy) poll();
  } catch (e) {
    showError(e.message);
  }
}

function needsRun(upto) {
  const i = stageIndex(upto);
  return S.meta.stages.slice(0, i + 1).some((s) => stageState(s.id).status !== "fresh");
}

function showError(msg) {
  const bar = $("#errorBar");
  bar.hidden = !msg;
  bar.textContent = msg ? `Fehler: ${msg}` : "";
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------
function renderSources() {
  const sel = $("#source");
  sel.replaceChildren();
  const cur = S.status.image;
  let found = false;
  for (const f of S.meta.sources) {
    const selected = cur && cur.path && cur.path.endsWith("/" + f);
    found ||= selected;
    sel.append(el("option", { value: f, selected }, f));
  }
  if (cur && !found) {
    sel.prepend(el("option", { value: "", selected: true }, cur.path ? cur.name : `${cur.name} (hochgeladen)`));
  }
  if (!cur) sel.prepend(el("option", { value: "", selected: true }, "– Bild wählen –"));
}

function renderProgress() {
  const st = S.status;
  $("#progress").hidden = !st.busy;
  if (st.busy) {
    const spec = st.stage ? stageSpec(st.stage) : null;
    const secs = S.busySince ? Math.round((Date.now() - S.busySince) / 1000) : 0;
    $("#progressText").textContent = `${spec ? spec.title : "Starte"} … ${secs} s`;
  }
  $("#runBtn").disabled = !st.image;
  $("#exportBtn").disabled = !st.image;
}

async function openSource(file) {
  try {
    applyStatus(await api("/api/open", { file }));
    onImageChanged();
  } catch (e) { showError(e.message); }
}

async function upload(file) {
  if (!file) return;
  try {
    applyStatus(await api("/api/upload", file, {
      raw: true, headers: { "X-Filename": encodeURIComponent(file.name) },
    }));
    onImageChanged();
  } catch (e) { showError(e.message); }
}

function onImageChanged() {
  S.natural = null;
  renderSources();
  if (S.runMode !== "manual") push();
  else updateImage();
}

// ---------------------------------------------------------------------------
// Stepper
// ---------------------------------------------------------------------------
const STATUS_LABEL = { fresh: "aktuell", stale: "veraltet", missing: "offen", running: "rechnet…" };

function renderStepper() {
  const ol = $("#stepper");
  ol.replaceChildren();
  S.meta.stages.forEach((spec, i) => {
    const st = stageState(spec.id);
    const running = S.status.busy && S.status.stage === spec.id;
    const status = running ? "running" : st.status;
    const time = st.time != null && st.status !== "missing" ? `${st.time.toFixed(1)} s` : "";
    ol.append(el("li", {
      class: `step ${status} ${spec.id === S.stage ? "active" : ""}`,
      title: `${spec.title}: ${STATUS_LABEL[status]} (Taste ${i + 1})`,
      onclick: () => selectStage(spec.id),
    },
      el("div", { class: "num" }, el("span", {}, String(i + 1))),
      el("div", { class: "title" }, spec.title),
      el("div", { class: "sub" }, el("span", {}, STATUS_LABEL[status]), el("span", { class: "time" }, time)),
    ));
  });
}

function selectStage(id) {
  if (S.stage === id) return;
  S.stage = id;
  renderStepper();
  renderViews();
  renderPanel();
  updateImage();
  if (S.runMode === "stage" && S.status.image && needsRun(id)) push();
}

// ---------------------------------------------------------------------------
// Parameter panel
// ---------------------------------------------------------------------------
function fmt(v, p) {
  if (p.kind === "int") return String(Math.round(v));
  const dec = Math.max(0, -Math.floor(Math.log10(p.step || 0.01)));
  return Number(v).toFixed(Math.min(dec, 3));
}

function paramControl(p, stageId) {
  const val = getParam(S.cfg, p.key);
  const presetVal = getParam(S.meta.presets[S.preset], p.key);
  const wrap = el("div", { class: "param", "data-key": p.key });
  const mark = () => wrap.classList.toggle("modified", getParam(S.cfg, p.key) !== presetVal);

  const commit = (v, live) => {
    setParam(S.cfg, p.key, v);
    mark();
    if (live) schedulePush(p.live ? 180 : 0);
  };

  const reset = el("button", {
    class: "icon reset", title: `Auf Preset-Wert ${presetVal} zurücksetzen`, type: "button",
    onclick: () => { commit(presetVal, true); renderPanel(); },
  }, "↺");

  if (p.kind === "select") {
    const sel = el("select", { onchange: (e) => commit(e.target.value, true) },
      p.options.map(([v, label]) => el("option", { value: v, selected: v === val }, label)));
    wrap.append(el("div", { class: "phead" },
      el("span", { class: "changed" }), el("label", {}, p.label), reset), sel);
  } else {
    const parse = (s) => (p.kind === "int" ? parseInt(s, 10) : parseFloat(s));
    const range = el("input", { type: "range", min: p.min, max: p.max, step: p.step, value: val,
      "aria-label": p.label });
    const num = el("input", { class: "num", type: "number", step: p.step, value: fmt(val, p),
      "aria-label": p.label });
    range.addEventListener("input", () => {
      const v = parse(range.value);
      num.value = fmt(v, p);
      setParam(S.cfg, p.key, v);
      mark();
      if (p.live) schedulePush(180);
    });
    range.addEventListener("change", () => commit(parse(range.value), true));
    num.addEventListener("change", () => {
      const v = parse(num.value);
      if (Number.isNaN(v)) { num.value = fmt(getParam(S.cfg, p.key), p); return; }
      range.value = v;
      commit(v, true);
    });
    wrap.append(
      el("div", { class: "phead" },
        el("span", { class: "changed" }), el("label", {}, p.label), reset, num,
        el("span", { class: "unit" }, p.unit || "")),
      range);
  }
  wrap.append(el("p", { class: "phelp" }, p.help));
  mark();
  return wrap;
}

function renderPanel() {
  const spec = stageSpec(S.stage);
  const i = stageIndex(S.stage);
  const panel = $("#panel");
  panel.replaceChildren();
  panel.append(
    el("div", { class: "kicker" }, `Schritt ${i + 1} von ${S.meta.stages.length}`),
    el("h2", {}, spec.title),
    el("p", { class: "desc" }, spec.description),
    spec.tip ? el("p", { class: "tip" }, spec.tip) : null,
  );
  const basic = spec.params.filter((p) => !p.advanced);
  const adv = spec.params.filter((p) => p.advanced);
  panel.append(el("div", { class: "section-title" }, el("span", {}, "Parameter"),
    el("button", { class: "ghost small", type: "button", title: "Alle Parameter dieses Schritts auf das Preset setzen",
      onclick: () => {
        for (const p of spec.params) setParam(S.cfg, p.key, clone(getParam(S.meta.presets[S.preset], p.key)));
        renderPanel(); push();
      } }, "Schritt zurücksetzen")));
  basic.forEach((p) => panel.append(paramControl(p, spec.id)));
  if (adv.length) {
    const det = el("details", { class: "adv", open: !!S.advOpen[spec.id] },
      el("summary", {}, `Erweitert (${adv.length})`), adv.map((p) => paramControl(p, spec.id)));
    det.addEventListener("toggle", () => { S.advOpen[spec.id] = det.open; });
    panel.append(det);
  }
  panel.append(el("button", { class: "btn primary stage-run", id: "stageRun", type: "button",
    onclick: () => push(S.stage) }, "Bis hier berechnen"));
  panel.append(el("div", { class: "section-title" }, el("span", {}, "Ergebnis")), el("div", { id: "info" }));
  renderInfo();
  renderStageRunButton();
}

function renderStageRunButton() {
  const b = $("#stageRun");
  if (!b) return;
  const need = S.status?.image && needsRun(S.stage);
  b.hidden = !need || (S.status.busy && S.runMode !== "manual");
}

function renderInfo() {
  const box = $("#info");
  if (!box) return;
  const st = stageState(S.stage);
  box.replaceChildren();
  if (!st.info) {
    box.append(el("p", { class: "muted small" }, "Noch nicht berechnet."));
    return;
  }
  const rows = Object.entries(st.info).filter(([k]) => k !== "palette");
  if (st.time != null) rows.push(["Rechenzeit", `${st.time.toFixed(2)} s`]);
  box.append(el("table", { class: "info" },
    rows.map(([k, v]) => el("tr", {}, el("td", { class: "muted" }, k), el("td", {}, String(v))))));
  if (st.info.palette) {
    box.append(el("div", { class: "swatches" }, st.info.palette.map((c) =>
      el("div", { class: "swatch", style: `background:${c.hex}`, title: `${c.hex} · ${c.regions} Flächen` },
        el("span", {}, String(c.regions))))));
  }
  if (st.status === "stale") {
    box.append(el("p", { class: "muted small" }, "Werte gehören zu einer älteren Einstellung."));
  }
}

// ---------------------------------------------------------------------------
// Viewer
// ---------------------------------------------------------------------------
function currentView() {
  const spec = stageSpec(S.stage);
  const v = S.views[S.stage];
  return spec.views.find((x) => x.id === v) || spec.views[0];
}

function renderViews() {
  const box = $("#views");
  box.replaceChildren();
  const cur = currentView();
  stageSpec(S.stage).views.forEach((v, i) => box.append(el("button", {
    class: "tab", role: "tab", "aria-selected": v.id === cur.id ? "true" : "false", type: "button",
    title: v.help,
    onclick: () => {
      S.views[S.stage] = v.id;
      store("lineart.views", JSON.stringify(S.views));
      renderViews();
      updateImage();
    },
  }, v.label)));
  $("#viewHelp").textContent = cur.help;
}

let loadToken = 0;
function updateImage() {
  if (!S.meta || !S.status) return;
  const st = stageState(S.stage);
  const view = currentView();
  const empty = $("#empty");
  const badge = $("#badge");
  const running = S.status.busy;

  // Underlay: the scaled original from stage 1.
  const pre = stageState("preprocess");
  const under = $("#under");
  const underSrc = pre.key ? `/api/view/preprocess/original?k=${pre.key}` : "";
  if (under.dataset.src !== underSrc) {
    under.dataset.src = underSrc;
    if (underSrc) under.src = underSrc; else under.removeAttribute("src");
  }

  if (!S.status.image) {
    showEmpty("Kein Bild geladen. Bild oben auswählen oder in das Fenster ziehen.");
    return;
  }
  if (st.status === "missing") {
    $("#canvas").hidden = true;
    badge.hidden = true;
    empty.hidden = false;
    empty.replaceChildren(running
      ? el("div", {}, el("div", { class: "spinner" }), "Wird berechnet …")
      : el("div", {}, "Dieser Schritt ist noch nicht berechnet.", el("br"),
        el("button", { class: "btn primary", style: "margin-top:12px", onclick: () => push(S.stage) },
          "Bis hier berechnen")));
    return;
  }
  empty.hidden = true;
  $("#canvas").hidden = false;

  badge.hidden = st.status === "fresh";
  if (st.status === "stale") {
    badge.replaceChildren(
      running ? el("span", { class: "spinner" }) : null,
      running ? "Veraltet – wird neu berechnet …" : "Veraltet – Parameter geändert");
  }

  const src = `/api/view/${S.stage}/${view.id}?k=${st.key}`;
  const layer = $("#layer");
  if (layer.dataset.src === src) return;
  const token = ++loadToken;
  const img = new Image();
  img.onload = () => {
    if (token !== loadToken) return;
    layer.dataset.src = src;
    layer.src = src;
    const nat = { w: img.naturalWidth, h: img.naturalHeight };
    if (!S.natural || S.natural.w !== nat.w || S.natural.h !== nat.h) {
      // Keep the visible region when only the working resolution changed.
      const factor = S.natural ? S.natural.w / nat.w : null;
      S.natural = nat;
      const c = $("#canvas");
      c.style.width = `${nat.w}px`;
      c.style.height = `${nat.h}px`;
      if (factor) { S.tf.s *= factor; applyTransform(); } else fit();
    }
  };
  img.onerror = () => { if (token === loadToken) showError(`Ansicht konnte nicht geladen werden (${view.label})`); };
  img.src = src;
}

function showEmpty(text) {
  $("#canvas").hidden = true;
  $("#badge").hidden = true;
  const empty = $("#empty");
  empty.hidden = false;
  empty.replaceChildren(el("div", {}, text));
}

// --- zoom & pan -----------------------------------------------------------
function applyTransform() {
  const { x, y, s } = S.tf;
  const c = $("#canvas");
  c.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  c.classList.toggle("pixelated", s >= 2.5);
  $("#zoomLabel").textContent = `${Math.round(s * 100)} %`;
}

function fit() {
  if (!S.natural) return;
  const r = $("#stageWrap").getBoundingClientRect();
  const pad = 24;
  const s = Math.min((r.width - pad * 2) / S.natural.w, (r.height - pad * 2) / S.natural.h);
  S.tf = { s, x: (r.width - S.natural.w * s) / 2, y: (r.height - S.natural.h * s) / 2 };
  applyTransform();
}

function zoomAt(factor, cx, cy) {
  const r = $("#stageWrap").getBoundingClientRect();
  if (cx === undefined) { cx = r.width / 2; cy = r.height / 2; }
  const s = Math.min(32, Math.max(0.03, S.tf.s * factor));
  const k = s / S.tf.s;
  S.tf = { s, x: cx - (cx - S.tf.x) * k, y: cy - (cy - S.tf.y) * k };
  applyTransform();
}

function setupViewer() {
  const wrap = $("#stageWrap");
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag = null;
  wrap.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button")) return;
    drag = { x: e.clientX, y: e.clientY, tx: S.tf.x, ty: S.tf.y };
    wrap.setPointerCapture(e.pointerId);
    wrap.classList.add("dragging");
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!drag) return;
    S.tf.x = drag.tx + e.clientX - drag.x;
    S.tf.y = drag.ty + e.clientY - drag.y;
    applyTransform();
  });
  const end = () => { drag = null; wrap.classList.remove("dragging"); };
  wrap.addEventListener("pointerup", end);
  wrap.addEventListener("pointercancel", end);
  wrap.addEventListener("dblclick", fit);
  $("#zoomIn").onclick = () => zoomAt(1.25);
  $("#zoomOut").onclick = () => zoomAt(0.8);
  $("#zoomLabel").onclick = fit;
  $("#zoom1").onclick = () => zoomAt(1 / S.tf.s);
  new ResizeObserver(() => { if (S.natural && !drag) fit(); }).observe(wrap);

  const blend = $("#blend");
  const applyBlend = () => { $("#layer").style.opacity = String(1 - blend.value / 100); };
  blend.addEventListener("input", applyBlend);

  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea, dialog")) return;
    if (e.key === "o" || e.key === "O") $("#layer").style.opacity = "0";
    else if (e.key === "f" || e.key === "F") fit();
    else if (e.key === "+") zoomAt(1.25);
    else if (e.key === "-") zoomAt(0.8);
    else if (/^[1-9]$/.test(e.key)) {
      const spec = S.meta.stages[Number(e.key) - 1];
      if (spec) selectStage(spec.id);
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "o" || e.key === "O") applyBlend();
  });
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------
function shellQuote(s) {
  return /^[\w./=:-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
}

function cliCommand() {
  const img = S.status.image;
  const input = img && img.path ? img.path : "<bild>";
  const parts = ["python", "pipeline.py", "-i", shellQuote(input), "-d", S.preset];
  const preset = S.meta.presets[S.preset];
  for (const st of S.meta.stages) {
    for (const p of st.params) {
      const v = getParam(S.cfg, p.key);
      if (v !== getParam(preset, p.key) && p.flag) parts.push(p.flag, String(v));
    }
  }
  return parts.join(" ");
}

function openExport() {
  const dlg = $("#exportDlg");
  $("#outDir").textContent = S.meta.output_dir;
  $("#cliCmd").textContent = cliCommand();
  const name = S.status.image ? S.status.image.name : "bild";
  const input = S.status.image && S.status.image.path ? S.status.image.path : "<bild>";
  $("#cliCfg").textContent = `python pipeline.py -i ${shellQuote(input)} --config ${S.meta.output_dir}/${name}.config.json`;
  $("#exportResult").replaceChildren();
  refreshDownloads();
  dlg.showModal();
  if (needsRun("style")) push("style");
}

function refreshDownloads() {
  const ready = !needsRun("style");
  document.querySelectorAll("[data-dl]").forEach((a) => {
    a.classList.toggle("disabled", !ready);
    a.href = ready ? `/api/download/${a.dataset.dl}?k=${stageState("style").key}` : "#";
  });
}

async function waitUntilComplete() {
  push("style");
  for (;;) {
    await new Promise((r) => setTimeout(r, 300));
    if (!S.status.busy && !polling) {
      if (!needsRun("style")) return;
      if (S.status.error) throw new Error(S.status.error);
      push("style");
    }
  }
}

async function doExport() {
  const res = $("#exportResult");
  const btn = $("#doExport");
  btn.disabled = true;
  try {
    if (needsRun("style")) {
      res.replaceChildren(el("span", { class: "spinner" }), " Pipeline wird zu Ende berechnet …");
      await waitUntilComplete();
    }
    res.replaceChildren(el("span", { class: "spinner" }), " Speichere und prüfe …");
    const mode = document.querySelector("input[name=mode]:checked").value;
    const out = await api("/api/export", { mode, preview: $("#exportPng").checked });
    const checks = Object.entries(out.checks).map(([k, c]) => el("li", {},
      `${k}.svg: `, c.ok ? el("span", { class: "ok" }, "Prüfung bestanden") : el("span", { class: "err" }, c.errors.join("; ")),
      c.fill_gap_ratio != null ? ` · Füll-Lücken ${(c.fill_gap_ratio * 100).toFixed(3)} %` : ""));
    res.replaceChildren(el("strong", {}, `${out.files.length} Dateien gespeichert`),
      el("ul", {}, checks, out.files.map((f) => el("li", { class: "muted" }, el("code", {}, f.split("/").pop())))));
  } catch (e) {
    res.replaceChildren(el("span", { class: "err" }, `Export fehlgeschlagen: ${e.message}`));
  } finally {
    btn.disabled = false;
    refreshDownloads();
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function setupDrop() {
  const drop = $("#drop");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    depth++; drop.hidden = false;
  });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; drop.hidden = true; } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0; drop.hidden = true;
    upload(e.dataTransfer.files[0]);
  });
}

async function init() {
  S.meta = await api("/api/meta");
  const st = await api("/api/status");
  S.cfg = clone(st.config);
  S.preset = st.preset;
  S.status = st;

  $("#preset").value = S.preset;
  $("#runMode").value = S.runMode;
  $("#preset").addEventListener("change", (e) => {
    S.preset = e.target.value;
    // Keep resolution and backend: they are about speed, not about the look.
    const keep = { work_size: S.cfg.work_size, edge_backend: S.cfg.edge_backend };
    S.cfg = Object.assign(clone(S.meta.presets[S.preset]), keep);
    renderPanel();
    push();
  });
  $("#runMode").addEventListener("change", (e) => {
    S.runMode = e.target.value;
    store("lineart.runMode", S.runMode);
    renderStageRunButton();
    if (S.runMode !== "manual") push();
  });
  $("#source").addEventListener("change", (e) => { if (e.target.value) openSource(e.target.value); });
  $("#upload").addEventListener("change", (e) => { upload(e.target.files[0]); e.target.value = ""; });
  $("#runBtn").addEventListener("click", () => push("style"));
  $("#exportBtn").addEventListener("click", openExport);
  $("#doExport").addEventListener("click", doExport);
  $("#copyCli").addEventListener("click", () => {
    navigator.clipboard?.writeText($("#cliCmd").textContent);
    $("#copyCli").textContent = "Kopiert";
    setTimeout(() => { $("#copyCli").textContent = "Kopieren"; }, 1200);
  });
  setInterval(() => {
    if (S.status?.busy) renderProgress();
    if ($("#exportDlg").open) refreshDownloads();
  }, 1000);

  setupViewer();
  setupDrop();
  renderSources();
  renderViews();
  renderPanel();
  applyStatus(st);
  if (st.image && S.runMode !== "manual") push();
}

window.__S = S;   // exposed for debugging and UI tests
init().catch((e) => showError(e.message));
