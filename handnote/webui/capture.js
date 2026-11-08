const SPEC_URL = "/api/symbols/template.json?cell_mm=24&cols=7&rows=10&dpi=300";
const STORE_KEY = "handnote:capture:v2";
const LEGACY_KEY = "handnote:capture:v1";
const VIEW_KEY = "handnote:capture:view";
const MAX_SETS = 8;
const LOGICAL = 96;
const ZOOM_LOGICAL = 480;
const INK = "#1e1f2c";

//: Characters that recur most in maths answers; written in every set they
//: give the renderer real handwritten variants instead of synthetic ones.
const PRESETS = {
  数字: "0123456789",
  字母: "xyznabcdefk",
  运算: "+-=()<>,.",
  希腊: "αβθλπσω",
};
const DEFAULT_MULTI = "0123456789xyn+-=()";

const state = {
  spec: null,
  specKey: "",
  byChar: new Map(), // char -> cell
  width: 0.05,
  set: 0,
  sets: new Map(), // set number -> Map(cell index -> strokes)
  history: [], // [{ set, index }] across all sets, newest last
  drawing: null,
  view: "all", // "all" | "multi"
  multi: DEFAULT_MULTI,
  zoom: null, // { order: [{set, index}], pos }
};

const $ = (id) => document.getElementById(id);

function specKey(spec) {
  return `${spec.cols}x${spec.rows}-${spec.cells.length}-${spec.page_size_px.join("x")}`;
}

async function boot() {
  state.spec = await (await fetch(SPEC_URL)).json();
  state.specKey = specKey(state.spec);
  for (const cell of state.spec.cells) state.byChar.set(cell.char, cell);
  $("width").addEventListener("input", () => {
    state.width = Number($("width").value);
    $("width-out").textContent = state.width.toFixed(3);
    redrawAll();
    save();
  });
  state.width = Number($("width").value);
  $("width-out").textContent = state.width.toFixed(3);

  buildGrid();
  restore();
  restoreView();
  buildSetPicker();
  buildMulti();
  buildStats();
  buildZoom();

  $("set").addEventListener("change", () => switchSet(Number($("set").value)));
  $("keep").addEventListener("input", () => {
    $("keep-out").textContent = Number($("keep").value).toFixed(2);
  });
  $("undo").addEventListener("click", undo);
  $("fab-undo").addEventListener("click", undo);
  $("zoom-open").addEventListener("click", () => openZoom());
  $("reset-all").addEventListener("click", resetAll);
  $("export").addEventListener("click", exportJson);
  $("build").addEventListener("click", buildFont);
  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  document.addEventListener("keydown", onKey);
  setView(state.view);
  updateProgress();
  setStatus("就绪");
}

// -- data -------------------------------------------------------------------

function setMap(k) {
  if (!state.sets.has(k)) state.sets.set(k, new Map());
  return state.sets.get(k);
}

function strokesOf(set, index) {
  return setMap(set).get(index) || [];
}

function addStroke(set, index, stroke) {
  const map = setMap(set);
  if (!map.has(index)) map.set(index, []);
  map.get(index).push(stroke);
  state.history.push({ set, index });
  refresh(set, index);
  save();
  updateProgress();
}

function undo() {
  // Newest stroke first; skip entries already removed by "clear".
  while (state.history.length) {
    const { set, index } = state.history.pop();
    const strokes = setMap(set).get(index);
    if (!strokes || !strokes.length) continue;
    strokes.pop();
    if (!strokes.length) setMap(set).delete(index);
    refresh(set, index);
    save();
    updateProgress();
    setStatus(`已撤销 · ${charOf(index)}（第 ${set + 1} 套）`);
    return;
  }
  setStatus("没有可撤销的笔画");
}

function clearCell(set, index) {
  if (!setMap(set).has(index)) return;
  setMap(set).delete(index);
  refresh(set, index);
  save();
  updateProgress();
}

function charOf(index) {
  return state.spec.cells[index] ? state.spec.cells[index].char : "?";
}

// -- drawing surfaces -------------------------------------------------------

function newCanvas(size) {
  const canvas = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = INK;
  ctx.fillStyle = INK;
  return { canvas, ctx, size };
}

/** Wire pointer drawing on a surface; ``target()`` names the set and cell. */
function attachDrawing(surface, target) {
  const { canvas } = surface;
  const pointOf = (event) => {
    const rect = canvas.getBoundingClientRect();
    const pressure = event.pressure > 0 ? event.pressure : 0.5;
    return [
      Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
      Math.min(1, Math.max(0, pressure)),
    ];
  };
  const finish = (event) => {
    const drawing = state.drawing;
    if (!drawing || drawing.surface !== surface) return;
    drawing.points.push(pointOf(event));
    state.drawing = null;
    addStroke(drawing.set, drawing.index, { points: drawing.points });
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button > 0) return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    const { set, index } = target();
    const point = pointOf(event);
    state.drawing = { surface, set, index, points: [point] };
    dot(surface, point);
  });
  canvas.addEventListener("pointermove", (event) => {
    const drawing = state.drawing;
    if (!drawing || drawing.surface !== surface) return;
    event.preventDefault();
    const point = pointOf(event);
    const last = drawing.points[drawing.points.length - 1];
    if (Math.hypot(point[0] - last[0], point[1] - last[1]) < 0.003) return;
    drawing.points.push(point);
    segment(surface, last, point);
  });
  canvas.addEventListener("pointerup", finish);
  canvas.addEventListener("pointercancel", finish);
}

function dot(surface, point) {
  const { ctx, size } = surface;
  ctx.beginPath();
  ctx.arc(point[0] * size, point[1] * size, Math.max(1, state.width * size / 2), 0, Math.PI * 2);
  ctx.fill();
}

function segment(surface, from, to) {
  const { ctx, size } = surface;
  ctx.lineWidth = Math.max(1, state.width * size);
  ctx.beginPath();
  ctx.moveTo(from[0] * size, from[1] * size);
  ctx.lineTo(to[0] * size, to[1] * size);
  ctx.stroke();
}

function paint(surface, strokes) {
  surface.ctx.clearRect(0, 0, surface.size, surface.size);
  for (const stroke of strokes) {
    const points = stroke.points;
    dot(surface, points[0]);
    for (let i = 1; i < points.length; i += 1) segment(surface, points[i - 1], points[i]);
  }
}

/** A grid cell. ``fixedSet`` pins it to one set; ``null`` follows the picker. */
function makeCell(cell, fixedSet = null) {
  const el = document.createElement("div");
  el.className = "cell";
  el.dataset.index = cell.index;
  el._fixedSet = fixedSet;
  const guide = document.createElement("span");
  guide.className = "guide";
  guide.textContent = cell.char;
  const idx = document.createElement("span");
  idx.className = "idx";
  idx.textContent = fixedSet === null ? cell.index + 1 : `${fixedSet + 1}`;
  const surface = newCanvas(LOGICAL);
  const zoom = document.createElement("button");
  zoom.className = "cell-zoom";
  zoom.type = "button";
  zoom.title = "放大书写";
  zoom.textContent = "⤢";
  zoom.addEventListener("click", () => openZoom(el));
  el.append(guide, idx, surface.canvas, zoom);
  el._surface = surface;
  attachDrawing(surface, () => ({ set: setOf(el), index: cell.index }));
  return el;
}

function setOf(el) {
  return el._fixedSet === null ? state.set : el._fixedSet;
}

function redrawEl(el) {
  const strokes = strokesOf(setOf(el), Number(el.dataset.index));
  paint(el._surface, strokes);
  el.classList.toggle("filled", strokes.length > 0);
}

function refresh(set, index) {
  document.querySelectorAll(`.cell[data-index="${index}"]`).forEach((el) => {
    if (setOf(el) === set) redrawEl(el);
  });
  const here = zoomTarget();
  if (here && here.set === set && here.index === index) paintZoom();
  updateMultiCounts();
}

function redrawAll() {
  document.querySelectorAll(".cell").forEach(redrawEl);
  if (state.zoom) paintZoom();
}

// -- all-characters view ----------------------------------------------------

function buildSetPicker() {
  const select = $("set");
  select.innerHTML = "";
  for (let k = 0; k < MAX_SETS; k += 1) {
    const option = document.createElement("option");
    const count = setMap(k).size;
    option.value = String(k);
    option.textContent = `第 ${k + 1} 套` + (count ? ` · ${count}` : "");
    select.appendChild(option);
  }
  select.value = String(state.set);
}

function switchSet(k) {
  state.set = k;
  document.querySelectorAll("#pages .cell").forEach(redrawEl);
  buildSetPicker();
  updateProgress();
  save();
}

function buildGrid() {
  const host = $("pages");
  const byPage = new Map();
  for (const cell of state.spec.cells) {
    if (!byPage.has(cell.page)) byPage.set(cell.page, []);
    byPage.get(cell.page).push(cell);
  }
  for (const [page, cells] of [...byPage.entries()].sort((a, b) => a[0] - b[0])) {
    const section = document.createElement("section");
    section.className = "cap-page";
    const title = document.createElement("h2");
    title.textContent = `第 ${page + 1} 页 · 字符 ${cells[0].index + 1}–${cells[cells.length - 1].index + 1}`;
    const grid = document.createElement("div");
    grid.className = "grid";
    grid.style.setProperty("--cols", state.spec.cols);
    for (const cell of cells) grid.appendChild(makeCell(cell));
    section.append(title, grid);
    host.appendChild(section);
  }
}

// -- high-frequency view ----------------------------------------------------

function multiCells() {
  const seen = new Set();
  const cells = [];
  for (const ch of state.multi) {
    const cell = state.byChar.get(ch);
    if (cell && !seen.has(ch)) {
      seen.add(ch);
      cells.push(cell);
    }
  }
  return cells;
}

function buildMulti() {
  const presets = $("multi-presets");
  presets.innerHTML = "";
  for (const [name, chars] of Object.entries(PRESETS)) {
    const btn = document.createElement("button");
    btn.className = "btn ghost small";
    btn.type = "button";
    btn.textContent = `+ ${name}`;
    btn.title = chars;
    btn.addEventListener("click", () => {
      setMulti(state.multi + chars);
    });
    presets.appendChild(btn);
  }
  const reset = document.createElement("button");
  reset.className = "btn ghost small";
  reset.type = "button";
  reset.textContent = "恢复默认";
  reset.addEventListener("click", () => setMulti(DEFAULT_MULTI));
  presets.appendChild(reset);
  const input = $("multi-chars");
  input.value = state.multi;
  input.addEventListener("change", () => setMulti(input.value));
  renderMultiRows();
}

function setMulti(text) {
  state.multi = [...new Set([...text.replace(/\s+/g, "")])].join("");
  $("multi-chars").value = state.multi;
  saveView();
  renderMultiRows();
}

function renderMultiRows() {
  const host = $("multi-rows");
  host.innerHTML = "";
  const cells = multiCells();
  const unknown = [...state.multi].filter((ch) => !state.byChar.has(ch));
  $("multi-unknown").textContent = unknown.length
    ? `模板里没有：${unknown.join(" ")}（已忽略）` : "";
  for (const cell of cells) {
    const row = document.createElement("div");
    row.className = "multi-row";
    const label = document.createElement("div");
    label.className = "multi-label";
    label.innerHTML = `<span class="multi-char"></span><span class="multi-count"></span>`;
    label.querySelector(".multi-char").textContent = cell.char;
    label.dataset.index = cell.index;
    const strip = document.createElement("div");
    strip.className = "multi-strip";
    for (let k = 0; k < MAX_SETS; k += 1) {
      const el = makeCell(cell, k);
      strip.appendChild(el);
      redrawEl(el);
    }
    row.append(label, strip);
    host.appendChild(row);
  }
  updateMultiCounts();
}

function updateMultiCounts() {
  document.querySelectorAll(".multi-label").forEach((label) => {
    const index = Number(label.dataset.index);
    let n = 0;
    for (let k = 0; k < MAX_SETS; k += 1) if (strokesOf(k, index).length) n += 1;
    label.querySelector(".multi-count").textContent = `${n}/${MAX_SETS}`;
    label.classList.toggle("done", n >= 3);
  });
}

function setView(view) {
  state.view = view === "multi" ? "multi" : "all";
  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === state.view);
  });
  $("pages").hidden = state.view !== "all";
  $("multi").hidden = state.view !== "multi";
  $("set-field").hidden = state.view !== "all";
  $("reset-all").hidden = state.view !== "all";
  if (state.view === "multi") renderMultiRows();
  else document.querySelectorAll("#pages .cell").forEach(redrawEl);
  saveView();
}

function saveView() {
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify({ view: state.view, multi: state.multi }));
  } catch (err) { /* view prefs are optional */ }
}

function restoreView() {
  const saved = readDraft(VIEW_KEY);
  if (!saved) return;
  if (saved.view) state.view = saved.view;
  if (typeof saved.multi === "string" && saved.multi) state.multi = saved.multi;
}

// -- frequency stats from the note ----------------------------------------

const STATS_KEY = "handnote:capture:stats";
const MD_KEY = "handnote:markdown";

//: LaTeX commands -> the glyph they draw, so stats count what is written.
const TEX_GLYPH = {
  times: "×", cdot: "⋅", div: "÷", pm: "±", mp: "∓", leq: "≤", le: "≤", geq: "≥", ge: "≥",
  neq: "≠", ne: "≠", approx: "≈", equiv: "≡", sim: "∼", cong: "≅", ll: "≪", gg: "≫",
  in: "∈", notin: "∉", ni: "∋", subset: "⊂", supset: "⊃", subseteq: "⊆", supseteq: "⊇",
  subsetneq: "⊊", supsetneq: "⊋", cup: "∪", cap: "∩", setminus: "∖", emptyset: "∅",
  varnothing: "∅", wedge: "∧", land: "∧", vee: "∨", lor: "∨", neg: "¬", lnot: "¬",
  forall: "∀", exists: "∃", therefore: "∴", because: "∵", to: "→", rightarrow: "→",
  leftarrow: "←", leftrightarrow: "↔", Rightarrow: "⇒", Leftarrow: "⇐",
  Leftrightarrow: "⇔", iff: "⇔", implies: "⇒", mapsto: "↦", uparrow: "↑", downarrow: "↓",
  infty: "∞", propto: "∝", partial: "∂", nabla: "∇", sum: "∑", prod: "∏", int: "∫",
  sqrt: "√", circ: "°", prime: "′", langle: "⟨", rangle: "⟩", lceil: "⌈", rceil: "⌉",
  lfloor: "⌊", rfloor: "⌋", ast: "∗",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ",
  eta: "η", theta: "θ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ",
  pi: "π", rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ",
  psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

/**
 * Turn one Markdown line into the characters a reader sees, keeping columns.
 *
 * Inside $..$ the delimiters and structural characters ({ } ^ _ &) become
 * blanks, commands become their glyph padded with blanks, and "-" counts as
 * the minus sign. Returns an array with one entry per source character.
 */
function visibleChars(line) {
  const out = [];
  let math = false;
  for (let i = 0; i < line.length;) {
    const ch = line[i];
    if (ch === "$") {
      math = !math;
      out.push(" ");
      i += 1;
      continue;
    }
    if (ch === "\\") {
      const name = /^[A-Za-z]+/.exec(line.slice(i + 1));
      if (name) {
        const glyph = TEX_GLYPH[name[0]] || " ";
        out.push(glyph, ...Array(name[0].length).fill(" "));
        i += 1 + name[0].length;
      } else {
        // Escaped symbol (\{ \% ...) draws itself; "\\" is a line break.
        const next = line[i + 1] || "";
        out.push(" ", next === "\\" || !next ? " " : next);
        i += 2;
      }
      continue;
    }
    if (math && "{}^_&".includes(ch)) out.push(" ");
    else if (math && ch === "-") out.push("−");
    else out.push(ch);
    i += 1;
  }
  return out.slice(0, line.length);
}

/** Count each character and how many of its occurrences have a near twin. */
function charStats(text, rows, cols) {
  const where = new Map(); // char -> [[line, col]]
  text.split(/\r?\n/).forEach((line, li) => {
    visibleChars(line).forEach((ch, col) => {
      if (/\s/.test(ch)) return;
      if (!where.has(ch)) where.set(ch, []);
      where.get(ch).push([li, col]);
    });
  });
  const stats = [];
  let order = 0;
  for (const [ch, spots] of where) {
    let near = 0;
    for (let a = 0; a < spots.length; a += 1) {
      const [la, ca] = spots[a];
      for (let b = 0; b < spots.length; b += 1) {
        if (a === b) continue;
        const [lb, cb] = spots[b];
        if (Math.abs(la - lb) <= rows && Math.abs(ca - cb) <= cols) {
          near += 1;
          break;
        }
      }
    }
    stats.push({ ch, count: spots.length, near, order: order++ });
  }
  return stats;
}

function statsSettings() {
  const num = (id, lo, hi, dflt) => {
    const v = Math.round(Number($(id).value));
    return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
  };
  return {
    sort: $("stats-sort").value,
    rows: num("stats-rows", 0, 10, 1),
    cols: num("stats-cols", 1, 40, 5),
    top: num("stats-top", 1, 100, 20),
    template: $("stats-template").checked,
  };
}

function rankedStats() {
  const cfg = statsSettings();
  const key = cfg.sort === "count" ? "count" : "near";
  const other = key === "count" ? "near" : "count";
  const all = charStats($("stats-text").value, cfg.rows, cfg.cols)
    .filter((s) => !cfg.template || state.byChar.has(s.ch))
    .sort((a, b) => b[key] - a[key] || b[other] - a[other] || a.order - b.order);
  return { cfg, all, top: all.slice(0, cfg.top).map((s) => s.ch).join("") };
}

function renderStats() {
  const { cfg, all } = rankedStats();
  const body = $("stats-rows-body");
  body.innerHTML = "";
  all.slice(0, Math.max(cfg.top + 20, 40)).forEach((s, i) => {
    const tr = document.createElement("tr");
    if (i === cfg.top - 1) tr.className = "cut";
    if (i >= cfg.top) tr.classList.add("out");
    const cells = [i + 1, s.ch, s.count, s.near, state.byChar.has(s.ch) ? "✓" : "—"];
    cells.forEach((value, c) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (c === 1) td.className = "ch";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  $("stats-note").textContent = all.length
    ? `共 ${all.length} 种字符；红线以上为前 ${Math.min(cfg.top, all.length)} 个`
    : "没有可统计的字符";
  try { localStorage.setItem(STATS_KEY, JSON.stringify(cfg)); } catch (err) { /* optional */ }
}

function loadNote() {
  let text = "";
  try { text = localStorage.getItem(MD_KEY) || ""; } catch (err) { text = ""; }
  $("stats-text").value = text;
  if (!text) $("stats-note").textContent = "渲染器里还没有笔记，可以直接粘贴文本";
  renderStats();
}

async function copyTop() {
  const { top } = rankedStats();
  if (!top) return;
  try {
    await navigator.clipboard.writeText(top);
  } catch (err) {
    const tmp = document.createElement("textarea");
    tmp.value = top;
    document.body.appendChild(tmp);
    tmp.select();
    document.execCommand("copy");
    tmp.remove();
  }
  $("stats-note").textContent = `已复制：${top}`;
}

function buildStats() {
  const saved = readDraft(STATS_KEY);
  if (saved) {
    if (saved.sort) $("stats-sort").value = saved.sort;
    if (saved.rows !== undefined) $("stats-rows").value = saved.rows;
    if (saved.cols) $("stats-cols").value = saved.cols;
    if (saved.top) $("stats-top").value = saved.top;
    if (saved.template !== undefined) $("stats-template").checked = saved.template;
  }
  ["stats-sort", "stats-rows", "stats-cols", "stats-top", "stats-template"].forEach((id) => {
    $(id).addEventListener("input", renderStats);
    $(id).addEventListener("change", renderStats);
  });
  let typing = 0;
  $("stats-text").addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(renderStats, 250);
  });
  $("stats-reload").addEventListener("click", loadNote);
  $("stats-copy").addEventListener("click", copyTop);
  $("stats-fill").addEventListener("click", () => setMulti(rankedStats().top));
  $("stats-add").addEventListener("click", () => setMulti(state.multi + rankedStats().top));
  $("stats").addEventListener("toggle", () => {
    if ($("stats").open && !$("stats-text").value) loadNote();
  });
}

// -- zoomed writing ---------------------------------------------------------

function buildZoom() {
  const surface = newCanvas(ZOOM_LOGICAL);
  surface.canvas.id = "zoom-canvas";
  $("zoom-pad").appendChild(surface.canvas);
  state.zoomSurface = surface;
  attachDrawing(surface, () => zoomTarget());
  $("zoom-prev").addEventListener("click", () => stepZoom(-1));
  $("zoom-next").addEventListener("click", () => stepZoom(1));
  $("zoom-undo").addEventListener("click", undo);
  $("zoom-clear").addEventListener("click", () => {
    const here = zoomTarget();
    if (here) clearCell(here.set, here.index);
  });
  $("zoom-close").addEventListener("click", closeZoom);
  $("zoom").addEventListener("click", (event) => {
    if (event.target.id === "zoom") closeZoom();
  });
}

/** Writing order for the zoom view: the visible cells, in page order. */
function zoomOrder() {
  if (state.view === "multi") {
    const order = [];
    for (const cell of multiCells()) {
      for (let k = 0; k < MAX_SETS; k += 1) order.push({ set: k, index: cell.index });
    }
    return order;
  }
  return state.spec.cells.map((cell) => ({ set: state.set, index: cell.index }));
}

function openZoom(el) {
  const order = zoomOrder();
  if (!order.length) return;
  let pos = 0;
  if (el) {
    const set = setOf(el);
    const index = Number(el.dataset.index);
    pos = Math.max(0, order.findIndex((t) => t.set === set && t.index === index));
  } else {
    const blank = order.findIndex((t) => !strokesOf(t.set, t.index).length);
    pos = blank < 0 ? 0 : blank;
  }
  state.zoom = { order, pos };
  $("zoom").hidden = false;
  document.body.classList.add("zooming");
  paintZoom();
}

function closeZoom() {
  state.zoom = null;
  $("zoom").hidden = true;
  document.body.classList.remove("zooming");
}

function zoomTarget() {
  return state.zoom ? state.zoom.order[state.zoom.pos] : null;
}

function stepZoom(delta) {
  if (!state.zoom) return;
  const n = state.zoom.order.length;
  state.zoom.pos = (state.zoom.pos + delta + n) % n;
  paintZoom();
}

function paintZoom() {
  const here = zoomTarget();
  if (!here) return;
  const ch = charOf(here.index);
  $("zoom-guide").textContent = ch;
  $("zoom-title").textContent =
    `${ch}  ·  第 ${here.set + 1} 套  ·  ${state.zoom.pos + 1} / ${state.zoom.order.length}`;
  paint(state.zoomSurface, strokesOf(here.set, here.index));
}

function onKey(event) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) &&
    event.target.type !== "range" && event.target.type !== "checkbox";
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === "z") {
    if (typing) return;
    event.preventDefault();
    undo();
    return;
  }
  if (!state.zoom || typing) return;
  if (event.key === "Escape") closeZoom();
  else if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    stepZoom(1);
  } else if (event.key === "ArrowLeft") stepZoom(-1);
  else if (event.key === "Backspace" || event.key === "Delete") {
    const here = zoomTarget();
    clearCell(here.set, here.index);
  }
}

// -- progress, persistence, build -------------------------------------------

function resetAll() {
  const map = setMap(state.set);
  if (!map.size) return;
  if (!window.confirm(`清空第 ${state.set + 1} 套已写内容？`)) return;
  const indexes = [...map.keys()];
  map.clear();
  indexes.forEach((index) => refresh(state.set, index));
  save();
  buildSetPicker();
  updateProgress();
}

function filledSets() {
  return [...state.sets.entries()].filter(([, cells]) => cells.size > 0);
}

function updateProgress() {
  const used = filledSets().length;
  const current = setMap(state.set).size;
  $("progress").textContent =
    `第 ${state.set + 1} 套 ${current} / ${state.spec.cells.length}` +
    (used > 1 ? ` · 共 ${used} 套` : "");
  $("build").disabled = used === 0;
  $("fab-undo").disabled = !state.history.length;
  $("undo").disabled = !state.history.length;
  $("zoom-undo").disabled = !state.history.length;
  const option = $("set").options[state.set];
  if (option) option.textContent = `第 ${state.set + 1} 套` + (current ? ` · ${current}` : "");
}

function payload() {
  const cells = [];
  for (const [set, map] of state.sets) {
    for (const [index, strokes] of map) {
      if (!strokes.length) continue;
      cells.push({ index, set, strokes: strokes.map((s) => s.points) });
    }
  }
  cells.sort((a, b) => a.set - b.set || a.index - b.index);
  return { version: 1, source: "browser", width: state.width, cells };
}

let saveTimer = 0;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        specKey: state.specKey, width: state.width, set: state.set, capture: payload(),
      }));
    } catch (err) {
      setStatus("草稿保存失败");
    }
  }, 300);
}

function readDraft(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch (err) {
    return null;
  }
}

function restore() {
  // v1 drafts had a single set; they load as set 1.
  const draft = readDraft(STORE_KEY) || readDraft(LEGACY_KEY);
  if (!draft || draft.specKey !== state.specKey) return;
  if (draft.width) {
    state.width = draft.width;
    $("width").value = draft.width;
    $("width-out").textContent = Number(draft.width).toFixed(3);
  }
  for (const cell of draft.capture.cells || []) {
    if (!cell.strokes || !cell.strokes.length) continue;
    const k = Number.isInteger(cell.set) ? cell.set : 0;
    if (k < 0 || k >= MAX_SETS) continue;
    setMap(k).set(cell.index, cell.strokes.map((points) => ({ points })));
  }
  state.set = Number.isInteger(draft.set) && draft.set < MAX_SETS ? draft.set : 0;
  redrawAll();
  setStatus("已恢复草稿");
}

function exportJson() {
  const blob = new Blob([JSON.stringify({ spec: state.spec, capture: payload() }, null, 1)],
    { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "handnote-capture.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}

async function buildFont() {
  if (!filledSets().length) return;
  setStatus("构建中…");
  $("result").textContent = "";
  try {
    const res = await fetch("/api/symbols/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spec: state.spec,
        capture: payload(),
        merge: $("merge").checked,
        replace: $("replace").checked,
        keep: Number($("keep").value),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    const missing = data.missing || [];
    $("result").innerHTML =
      `构建完成：<b>${data.glyphs}</b> 个字形` +
      (data.sets > 1 ? `，最多 <b>${data.sets}</b> 套（变体 ${data.alternates} 个）` : "") +
      (data.merged_added !== undefined ? `，合并新增 <b>${data.merged_added}</b> 个码位` : "") +
      (data.merged_replaced ? `，覆盖 <b>${data.merged_replaced.length}</b> 个原有字符` : "") +
      ` · <a href="${data.url}" download>下载 TTF</a>` +
      (data.merged_into
        ? ` · <a class="btn primary small" href="/?font=${encodeURIComponent(data.font)}">在渲染器中使用 →</a>`
        : "") +
      (missing.length
        ? `<div class="miss">未写：${missing.join(" ")}</div>`
        : "");
    setStatus(`${data.glyphs} 字形 · ${data.size}px`);
  } catch (err) {
    setStatus("构建失败");
    $("result").textContent = "构建失败: " + err.message;
  }
}

function setStatus(text) {
  $("status").textContent = text;
}

boot().catch((err) => setStatus("启动失败: " + err.message));
