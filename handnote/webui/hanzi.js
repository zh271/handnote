// Handwritten hanzi page: write a few hundred characters in stroke order,
// see how many others can be composed from their components, build a font.
"use strict";

const STORE_KEY = "handnote:hanzi:v1";
const NOTE_KEY = "handnote:markdown";
const SETS = 4;
const PAD_SIZE = 420;
const GUIDE = "#ebe5dc";
const GRID = "#e9d6d2";

const state = {
  width: 0.05,
  set: 0,
  sets: new Map(), // set -> Map(char -> [{points}])
  history: [], // [{set, char}]
  checks: new Map(), // `${set}:${char}` -> result
  info: new Map(), // char -> {strokes, medians, parts} | null
  plan: null,
  list: "next",
  queue: [],
  custom: "",
  current: "",
};

const $ = (id) => document.getElementById(id);
const CJK = /[㐀-鿿]/u;
let pad = null;

// -- persistence ----------------------------------------------------------------

function setMap(k) {
  if (!state.sets.has(k)) state.sets.set(k, new Map());
  return state.sets.get(k);
}

function strokesOf(ch, set = state.set) {
  return setMap(set).get(ch) || [];
}

function save() {
  const sets = {};
  for (const [k, map] of state.sets) {
    if (map.size) sets[k] = Object.fromEntries(map);
  }
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      width: state.width, set: state.set, custom: state.custom, sets,
    }));
  } catch (err) {
    setStatus("浏览器存储已满，请先导出 JSON");
  }
}

function restore() {
  let draft = null;
  try {
    draft = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
  } catch (err) {
    draft = null;
  }
  if (!draft) return;
  state.width = Number(draft.width) || 0.05;
  state.set = Math.min(SETS - 1, Math.max(0, Number(draft.set) || 0));
  state.custom = String(draft.custom || "");
  loadSets(draft.sets || {});
}

function loadSets(sets) {
  for (const [k, chars] of Object.entries(sets)) {
    const map = setMap(Number(k));
    for (const [ch, strokes] of Object.entries(chars)) {
      if (Array.isArray(strokes) && strokes.length) map.set(ch, strokes);
    }
  }
}

function payload() {
  const chars = [];
  for (const [set, map] of state.sets) {
    for (const [ch, strokes] of map) {
      if (strokes.length) chars.push({ char: ch, set, strokes });
    }
  }
  return { version: 1, kind: "hanzi", width: state.width, chars };
}

function writtenChars() {
  const out = new Set();
  for (const map of state.sets.values()) for (const [ch, s] of map) if (s.length) out.add(ch);
  return out;
}

// -- server ---------------------------------------------------------------------

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function ensureInfo(chars) {
  const need = [...new Set(chars)].filter((ch) => !state.info.has(ch));
  for (let i = 0; i < need.length; i += 60) {
    const part = need.slice(i, i + 60).join("");
    const data = await api(`/api/hanzi/char?c=${encodeURIComponent(part)}`);
    for (const ch of part) state.info.set(ch, data[ch] || null);
  }
}

// -- writing --------------------------------------------------------------------

function drawGuide() {
  const info = state.info.get(state.current);
  const showGuide = $("show-guide").checked;
  const showOrder = $("show-order").checked;
  pad.guide((ctx, size) => {
    ctx.save();
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    for (const [a, b, c, d] of [[0, .5, 1, .5], [.5, 0, .5, 1], [0, 0, 1, 1], [1, 0, 0, 1]]) {
      ctx.beginPath();
      ctx.moveTo(a * size, b * size);
      ctx.lineTo(c * size, d * size);
      ctx.stroke();
    }
    ctx.restore();
    if (!info) return;
    if (showGuide) {
      ctx.strokeStyle = GUIDE;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.lineWidth = size * 0.07;
      for (const median of info.medians) {
        ctx.beginPath();
        median.forEach(([u, v], i) => (i ? ctx.lineTo(u * size, v * size) : ctx.moveTo(u * size, v * size)));
        ctx.stroke();
      }
    }
    if (showOrder) {
      ctx.fillStyle = "#b9a9a4";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      info.medians.forEach((median, i) => {
        const [u, v] = median[0];
        ctx.fillText(String(i + 1), u * size - 8, v * size - 8);
      });
    }
  });
}

async function select(ch) {
  if (!ch) return;
  state.current = ch;
  await ensureInfo([ch]);
  drawGuide();
  pad.paint(strokesOf(ch));
  updateHead();
  renderChips();
}

function updateHead() {
  const ch = state.current;
  const info = state.info.get(ch);
  $("char").textContent = ch || "·";
  const got = strokesOf(ch).length;
  $("strokes").textContent = info ? `${got} / ${info.strokes} 笔` : "没有这个字的笔顺数据，只能整字使用";
  const result = state.checks.get(`${state.set}:${ch}`);
  const el = $("check");
  el.className = "hz-check";
  el.textContent = "";
  if (result) {
    el.classList.add(result.ok ? "ok" : "bad");
    el.textContent = result.ok ? "✓ 笔顺正确，可拆成部件" : `✗ ${result.reason}`;
  }
}

function onStroke(points) {
  const ch = state.current;
  if (!ch) return;
  const map = setMap(state.set);
  if (!map.has(ch)) map.set(ch, []);
  map.get(ch).push({ points: points.map((p) => p.map((v) => Math.round(v * 10000) / 10000)) });
  state.history.push({ set: state.set, char: ch });
  state.checks.delete(`${state.set}:${ch}`);
  pad.paint(strokesOf(ch));
  save();
  updateHead();
  const info = state.info.get(ch);
  if (info && strokesOf(ch).length >= info.strokes) check(ch);
}

async function check(ch) {
  const set = state.set;
  try {
    const result = await api("/api/hanzi/check", { char: ch, strokes: strokesOf(ch, set) });
    state.checks.set(`${set}:${ch}`, result);
    if (state.current === ch) updateHead();
    renderChips();
    schedulePlan();
    if (result.ok && $("auto-next").checked && state.current === ch) {
      setTimeout(() => state.current === ch && step(1), 350);
    }
  } catch (err) {
    setStatus(`校验失败：${err.message}`);
  }
}

function step(delta) {
  const queue = state.queue;
  if (!queue.length) return;
  let i = queue.indexOf(state.current);
  i = i < 0 ? 0 : Math.min(queue.length - 1, Math.max(0, i + delta));
  if (delta > 0 && i === queue.indexOf(state.current)) {
    setStatus("这一列已经写完，点“推荐”刷新");
    return;
  }
  select(queue[i]);
}

function undo() {
  while (state.history.length) {
    const { set, char } = state.history.pop();
    const strokes = setMap(set).get(char);
    if (!strokes || !strokes.length) continue;
    strokes.pop();
    if (!strokes.length) setMap(set).delete(char);
    state.checks.delete(`${set}:${char}`);
    save();
    if (set !== state.set) switchSet(set);
    select(char);
    setStatus(`已撤销 · ${char}`);
    return;
  }
  setStatus("没有可撤销的笔画");
}

function clearCurrent() {
  const ch = state.current;
  if (!setMap(state.set).has(ch)) return;
  setMap(state.set).delete(ch);
  state.checks.delete(`${state.set}:${ch}`);
  save();
  select(ch);
  schedulePlan();
}

function switchSet(k) {
  state.set = k;
  $("set").value = String(k);
  save();
  if (state.current) select(state.current);
}

// -- lists and coverage -----------------------------------------------------------

let planTimer = null;
function schedulePlan() {
  clearTimeout(planTimer);
  planTimer = setTimeout(refreshPlan, 1200);
}

function noteText() {
  try {
    return localStorage.getItem(NOTE_KEY) || "";
  } catch (err) {
    return "";
  }
}

async function refreshPlan() {
  const capture = payload();
  try {
    const plan = await api("/api/hanzi/plan", {
      capture: capture.chars.length ? capture : null, n: 80, prefer: noteText(),
    });
    state.plan = plan;
    const pct = (100 * plan.available) / plan.total;
    $("cover-bar").style.width = `${pct.toFixed(1)}%`;
    $("cover-text").innerHTML = `已写 <b>${plan.written}</b> 字 · 可生成 <b>${plan.available}</b> / ${plan.total}`
      + `（${pct.toFixed(1)}%，其中拼合 ${plan.composed}）`;
    renderChips();
    if (!state.current && state.queue.length) select(state.queue[0]);
    preview();
  } catch (err) {
    $("cover-text").textContent = `统计失败：${err.message}`;
  }
}

function listChars() {
  const written = writtenChars();
  if (state.list === "done") return [...written];
  if (state.list === "note") {
    const missing = new Set(state.plan ? state.plan.missing : "");
    const seen = new Set();
    const out = [];
    for (const ch of noteText()) {
      if (CJK.test(ch) && !seen.has(ch)) {
        seen.add(ch);
        if (missing.has(ch) && !written.has(ch)) out.push(ch);
      }
    }
    return out;
  }
  const custom = [...state.custom].filter((ch) => CJK.test(ch));
  const next = state.plan ? [...state.plan.next] : [];
  return [...new Set([...custom, ...next])];
}

function renderChips() {
  const host = $("chips");
  const chars = listChars();
  // Keep the character being written in the queue so "next" still works.
  state.queue = chars.includes(state.current) || !state.current ? chars : [state.current, ...chars];
  host.textContent = "";
  if (!chars.length) {
    const span = document.createElement("span");
    span.className = "empty";
    span.textContent = state.list === "note"
      ? "笔记里的字都能生成了（或渲染器里还没有笔记）"
      : state.list === "done" ? "还没有写字" : "没有需要推荐的字了";
    host.appendChild(span);
    return;
  }
  for (const ch of chars) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = ch;
    const result = state.checks.get(`${state.set}:${ch}`);
    if (strokesOf(ch).length) button.classList.add(result && !result.ok ? "bad" : "done");
    if (ch === state.current) button.classList.add("current");
    button.title = result && !result.ok ? result.reason : ch;
    button.addEventListener("click", () => select(ch));
    host.appendChild(button);
  }
}

// -- preview and build ----------------------------------------------------------

let previewTimer = null;
function preview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const chars = [...$("try").value].filter((ch) => CJK.test(ch)).join("");
    const host = $("previews");
    if (!chars) {
      host.textContent = "";
      return;
    }
    const capture = payload();
    try {
      const data = await api("/api/hanzi/preview", {
        capture: capture.chars.length ? capture : null, chars, sets: 1, pressure: pen.gain,
      });
      host.textContent = "";
      for (const item of data.chars) {
        const fig = document.createElement("figure");
        const cap = document.createElement("figcaption");
        if (item.ok) {
          const img = document.createElement("img");
          img.src = item.variants[0].png;
          img.alt = item.char;
          fig.appendChild(img);
          const v = item.variants[0];
          cap.textContent = v.written ? "手写"
            : v.parts.map((p) => `${p.key.length > 2 ? "…" : p.key}←${p.source}`).join(" ");
        } else {
          const miss = document.createElement("div");
          miss.className = "miss";
          miss.textContent = item.char;
          fig.appendChild(miss);
          cap.textContent = "缺部件";
        }
        fig.appendChild(cap);
        host.appendChild(fig);
      }
    } catch (err) {
      host.textContent = `预览失败：${err.message}`;
    }
  }, 300);
}

async function loadFonts() {
  const select = $("merge");
  select.textContent = "";
  const add = (value, label) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  };
  add("", "默认字体（志莽行书）");
  try {
    const state_ = await api("/api/state");
    for (const font of state_.fonts || []) {
      if (font.source === "upload") add(font.path, font.name);
    }
  } catch (err) {
    // The default option is enough.
  }
  add("none", "不合并，只要手写汉字");
}

async function build() {
  const capture = payload();
  if (!capture.chars.length) {
    setStatus("还没有写字");
    return;
  }
  const merge = $("merge").value;
  const body = {
    capture,
    sets: Number($("sets").value),
    merge: merge === "" ? true : merge === "none" ? false : merge,
    replace: $("replace").checked,
    pressure: pen.gain,
  };
  $("build").disabled = true;
  $("build-meter").hidden = false;
  $("build-bar").style.width = "0";
  $("build-result").textContent = "正在构建…";
  try {
    const { job } = await api("/api/hanzi/build", body);
    for (;;) {
      await new Promise((r) => setTimeout(r, 800));
      const status = await api(`/api/hanzi/build/${job}`);
      if (status.total) {
        $("build-bar").style.width = `${(100 * status.done) / status.total}%`;
        $("build-result").textContent = `正在生成字形 ${status.done} / ${status.total}`;
      }
      if (status.state === "error") throw new Error(status.error);
      if (status.state === "done") {
        const r = status.report;
        $("build-bar").style.width = "100%";
        $("build-result").innerHTML = `完成：手写 ${r.written} 字，拼合 ${r.composed} 字，变体 ${r.alternates} 个`
          + ` · <a href="${r.url}">下载字体</a>`
          + ` · <a class="btn primary small" href="/?font=${encodeURIComponent(r.font)}">在渲染器中使用 →</a>`;
        loadFonts();
        return;
      }
    }
  } catch (err) {
    $("build-result").textContent = `构建失败：${err.message}`;
  } finally {
    $("build").disabled = false;
  }
}

function exportJson() {
  const blob = new Blob([JSON.stringify(payload())], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "handnote-hanzi.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function importJson(file) {
  try {
    const data = JSON.parse(await file.text());
    let count = 0;
    for (const item of data.chars || []) {
      if (typeof item.char !== "string" || !Array.isArray(item.strokes)) continue;
      setMap(Number(item.set) || 0).set(item.char, item.strokes);
      count += 1;
    }
    save();
    setStatus(`已导入 ${count} 个字`);
    refreshPlan();
    if (state.current) select(state.current);
  } catch (err) {
    setStatus(`导入失败：${err.message}`);
  }
}

function setStatus(text) {
  $("status").textContent = text;
}

// -- boot -----------------------------------------------------------------------

function wire() {
  for (let k = 0; k < SETS; k += 1) {
    const option = document.createElement("option");
    option.value = String(k);
    option.textContent = `第 ${k + 1} 套`;
    $("set").appendChild(option);
  }
  $("set").value = String(state.set);
  $("set").addEventListener("change", () => switchSet(Number($("set").value)));
  $("width").value = String(state.width);
  $("width-out").textContent = state.width.toFixed(3);
  $("width").addEventListener("input", () => {
    state.width = Number($("width").value);
    pad.width = state.width;
    $("width-out").textContent = state.width.toFixed(3);
    pad.paint(strokesOf(state.current));
    save();
  });
  pen.bind($("pressure"), $("pressure-out"), $("simulate"), () => {
    pad.paint(strokesOf(state.current));
    preview();
  });
  $("undo").addEventListener("click", undo);
  $("clear").addEventListener("click", clearCurrent);
  $("prev").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  $("show-guide").addEventListener("change", drawGuide);
  $("show-order").addEventListener("change", drawGuide);
  $("export").addEventListener("click", exportJson);
  $("import").addEventListener("change", (e) => e.target.files[0] && importJson(e.target.files[0]));
  $("try").addEventListener("input", preview);
  $("build").addEventListener("click", build);
  $("add-chars").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const chars = [...event.target.value].filter((ch) => CJK.test(ch)).join("");
    event.target.value = "";
    if (!chars) return;
    state.custom = [...new Set([...chars, ...state.custom])].join("");
    save();
    setList("next");
    select(chars[0]);
  });
  document.querySelectorAll("[data-list]").forEach((button) => {
    button.addEventListener("click", () => setList(button.dataset.list));
  });
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      undo();
    } else if (event.key === "ArrowRight" || event.key === "Enter") {
      step(1);
    } else if (event.key === "ArrowLeft") {
      step(-1);
    }
  });
}

function setList(name) {
  state.list = name;
  document.querySelectorAll("[data-list]").forEach((b) => b.classList.toggle("active", b.dataset.list === name));
  renderChips();
}

async function start() {
  $("fetching").hidden = true;
  await loadFonts();
  await refreshPlan();
}

async function boot() {
  restore();
  pad = createPad($("pad"), { size: PAD_SIZE, width: state.width, onStroke });
  wire();
  drawGuide();
  const status = await api("/api/hanzi/status");
  if (status.available) {
    start();
    return;
  }
  $("fetching").hidden = false;
  $("cover-text").textContent = "需要先下载笔顺数据";
  $("fetch").addEventListener("click", async () => {
    $("fetch").disabled = true;
    $("fetch").textContent = "下载中…（约半分钟）";
    try {
      await api("/api/hanzi/char?c=%E6%B0%B8");
      start();
    } catch (err) {
      $("fetch").disabled = false;
      $("fetch").textContent = "重试";
      setStatus(`下载失败：${err.message}`);
    }
  });
}

boot();
