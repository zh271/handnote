const SPEC_URL = "/api/symbols/template.json?cell_mm=24&cols=7&rows=10&dpi=300";
const STORE_KEY = "handnote:capture:v1";
const LOGICAL = 96;
const INK = "#1e1f2c";

const state = {
  spec: null,
  specKey: "",
  width: 0.05,
  strokes: new Map(),
  history: [],
  drawing: null,
  dirty: false,
};

const $ = (id) => document.getElementById(id);

function specKey(spec) {
  return `${spec.cols}x${spec.rows}-${spec.cells.length}-${spec.page_size_px.join("x")}`;
}

async function boot() {
  state.spec = await (await fetch(SPEC_URL)).json();
  state.specKey = specKey(state.spec);
  $("width").addEventListener("input", () => {
    state.width = Number($("width").value);
    $("width-out").textContent = state.width.toFixed(3);
    save();
  });
  state.width = Number($("width").value);
  $("width-out").textContent = state.width.toFixed(3);

  buildGrid();
  restore();

  $("undo").addEventListener("click", undo);
  $("reset-all").addEventListener("click", resetAll);
  $("export").addEventListener("click", exportJson);
  $("build").addEventListener("click", buildFont);
  updateProgress();
  setStatus("就绪");
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

function makeCell(cell) {
  const el = document.createElement("div");
  el.className = "cell";
  el.dataset.index = cell.index;
  const guide = document.createElement("span");
  guide.className = "guide";
  guide.textContent = cell.char;
  const idx = document.createElement("span");
  idx.className = "idx";
  idx.textContent = cell.index + 1;
  const canvas = document.createElement("canvas");
  el.append(guide, idx, canvas);

  const dpr = window.devicePixelRatio || 1;
  canvas.width = LOGICAL * dpr;
  canvas.height = LOGICAL * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = INK;
  ctx.fillStyle = INK;

  el._ctx = ctx;
  el._canvas = canvas;

  canvas.addEventListener("pointerdown", (event) => startStroke(event, el, ctx));
  canvas.addEventListener("pointermove", (event) => moveStroke(event, el, ctx));
  canvas.addEventListener("pointerup", (event) => endStroke(event, el));
  canvas.addEventListener("pointercancel", (event) => endStroke(event, el));
  canvas.addEventListener("pointerleave", (event) => {
    if (state.drawing && state.drawing.el === el) endStroke(event, el);
  });
  return el;
}

function pointOf(event, el) {
  const rect = el._canvas.getBoundingClientRect();
  const u = (event.clientX - rect.left) / rect.width;
  const v = (event.clientY - rect.top) / rect.height;
  const pressure = event.pressure > 0 ? event.pressure : 0.5;
  return [
    Math.min(1, Math.max(0, u)),
    Math.min(1, Math.max(0, v)),
    Math.min(1, Math.max(0, pressure)),
  ];
}

function startStroke(event, el, ctx) {
  event.preventDefault();
  el._canvas.setPointerCapture(event.pointerId);
  const point = pointOf(event, el);
  state.drawing = { el, ctx, index: Number(el.dataset.index), stroke: { points: [point] } };
  dot(ctx, point);
}

function moveStroke(event, el, ctx) {
  const drawing = state.drawing;
  if (!drawing || drawing.el !== el) return;
  event.preventDefault();
  const points = drawing.stroke.points;
  const point = pointOf(event, el);
  const last = points[points.length - 1];
  if (Math.hypot(point[0] - last[0], point[1] - last[1]) < 0.004) return;
  points.push(point);
  segment(ctx, last, point);
}

function endStroke(event, el) {
  const drawing = state.drawing;
  if (!drawing || drawing.el !== el) return;
  drawing.stroke.points.push(pointOf(event, el));
  const index = drawing.index;
  if (!state.strokes.has(index)) state.strokes.set(index, []);
  state.strokes.get(index).push(drawing.stroke);
  state.history.push({ index });
  el.classList.add("filled");
  state.drawing = null;
  save();
  updateProgress();
}

function dot(ctx, point) {
  ctx.beginPath();
  ctx.arc(point[0] * LOGICAL, point[1] * LOGICAL, Math.max(1, state.width * LOGICAL / 2), 0, Math.PI * 2);
  ctx.fill();
}

function segment(ctx, from, to) {
  ctx.lineWidth = Math.max(1, state.width * LOGICAL);
  ctx.beginPath();
  ctx.moveTo(from[0] * LOGICAL, from[1] * LOGICAL);
  ctx.lineTo(to[0] * LOGICAL, to[1] * LOGICAL);
  ctx.stroke();
}

function redraw(index) {
  const el = document.querySelector(`.cell[data-index="${index}"]`);
  if (!el) return;
  const ctx = el._ctx;
  ctx.clearRect(0, 0, LOGICAL, LOGICAL);
  const strokes = state.strokes.get(index) || [];
  for (const stroke of strokes) {
    const points = stroke.points;
    dot(ctx, points[0]);
    for (let i = 1; i < points.length; i += 1) segment(ctx, points[i - 1], points[i]);
  }
  el.classList.toggle("filled", strokes.length > 0);
}

function undo() {
  const last = state.history.pop();
  if (!last) return;
  const strokes = state.strokes.get(last.index) || [];
  strokes.pop();
  if (!strokes.length) state.strokes.delete(last.index);
  redraw(last.index);
  save();
  updateProgress();
}

function resetAll() {
  if (!state.strokes.size) return;
  if (!window.confirm("清空全部已写内容？")) return;
  const indexes = [...state.strokes.keys()];
  state.strokes.clear();
  state.history = [];
  indexes.forEach(redraw);
  localStorage.removeItem(STORE_KEY);
  updateProgress();
}

function updateProgress() {
  $("progress").textContent = `${state.strokes.size} / ${state.spec.cells.length}`;
  $("build").disabled = state.strokes.size === 0;
}

function payload() {
  const cells = [];
  for (const [index, strokes] of state.strokes) {
    cells.push({ index, strokes: strokes.map((s) => s.points) });
  }
  cells.sort((a, b) => a.index - b.index);
  return { version: 1, source: "browser", width: state.width, cells };
}

let saveTimer = 0;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        specKey: state.specKey, width: state.width, capture: payload(),
      }));
    } catch (err) {
      setStatus("草稿保存失败");
    }
  }, 300);
}

function restore() {
  let draft = null;
  try {
    draft = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
  } catch (err) {
    draft = null;
  }
  if (!draft || draft.specKey !== state.specKey) return;
  if (draft.width) {
    state.width = draft.width;
    $("width").value = draft.width;
    $("width-out").textContent = Number(draft.width).toFixed(3);
  }
  for (const cell of draft.capture.cells || []) {
    if (!cell.strokes || !cell.strokes.length) continue;
    state.strokes.set(cell.index, cell.strokes.map((points) => ({ points })));
    redraw(cell.index);
  }
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
  if (!state.strokes.size) return;
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
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    const missing = data.missing || [];
    $("result").innerHTML =
      `构建完成：<b>${data.glyphs}</b> 个字形` +
      (data.merged_added !== undefined ? `，合并新增 <b>${data.merged_added}</b> 个码位` : "") +
      ` · <a href="${data.url}" download>下载 TTF</a>` +
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
