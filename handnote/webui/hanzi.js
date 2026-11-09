// Handwritten hanzi page: write 60-100 characters following the trace guide,
// fine-tune the SDT model on them, then generate the rest and build a font.
"use strict";

const STORE_KEY = "handnote:hanzi:v1";
const NOTE_KEY = "handnote:markdown";
const SETS = 4;
const PAD_SIZE = 420;
const GUIDE = "#ebe5dc";
const GRID = "#e9d6d2";
const MIN_CHARS = 30;
const GOOD_CHARS = 60;

const state = {
  width: 0.05,
  set: 0,
  sets: new Map(), // set -> Map(char -> [{points}])
  history: [], // [{set, char}]
  checks: new Map(), // `${set}:${char}` -> result
  info: new Map(), // char -> {strokes, medians} | null
  recommended: "",
  list: "next",
  queue: [],
  custom: "",
  current: "",
  model: null, // last /api/hanzi/model/status
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
  $("strokes").textContent = info ? `${got} / ${info.strokes} 笔` : "没有这个字的笔顺数据";
  const result = state.checks.get(`${state.set}:${ch}`);
  const el = $("check");
  el.className = "hz-check";
  el.textContent = "";
  if (result) {
    el.classList.add(result.ok ? "ok" : "bad");
    el.textContent = result.ok ? "✓ 笔顺正确" : `✗ ${result.reason}（可以重写，也可以保留）`;
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
  refreshCover();
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
    setStatus("这一列已经写完");
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
    refreshCover();
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
  refreshCover();
}

function switchSet(k) {
  state.set = k;
  $("set").value = String(k);
  save();
  if (state.current) select(state.current);
}

// -- lists ------------------------------------------------------------------------

function noteText() {
  try {
    return localStorage.getItem(NOTE_KEY) || "";
  } catch (err) {
    return "";
  }
}

function noteChars() {
  return [...new Set([...noteText()].filter((ch) => CJK.test(ch)))];
}

function refreshCover() {
  const n = writtenChars().size;
  $("cover-bar").style.width = `${Math.min(100, n)}%`;
  const hint = n < MIN_CHARS ? `至少写 ${MIN_CHARS} 个才能训练`
    : n < GOOD_CHARS ? `可以训练了，写到 ${GOOD_CHARS}–100 个效果更稳定` : "可以训练了";
  $("cover-text").innerHTML = `已写 <b>${n}</b> 字 · ${hint}`;
  scheduleModelStatus();
}

function listChars() {
  const written = writtenChars();
  if (state.list === "done") return [...written];
  if (state.list === "note") return noteChars().filter((ch) => !written.has(ch));
  const custom = [...state.custom].filter((ch) => CJK.test(ch));
  return [...new Set([...custom, ...state.recommended])];
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
      ? "笔记里的字都写过了（或渲染器里还没有笔记）"
      : state.list === "done" ? "还没有写字" : "列表是空的";
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

// -- model ----------------------------------------------------------------------

let statusTimer = null;
function scheduleModelStatus() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(modelStatus, 800);
}

async function modelStatus() {
  const capture = payload();
  let s;
  try {
    s = await api("/api/hanzi/model/status", { capture: capture.chars.length ? capture : null });
  } catch (err) {
    $("model-status").textContent = `状态读取失败：${err.message}`;
    return null;
  }
  state.model = s;
  const n = writtenChars().size;
  $("model-install").hidden = s.torch;
  $("model-fetch-box").hidden = !s.torch || (s.weights.checkpoint && s.weights.content);
  const ready = s.torch && s.weights.checkpoint && s.weights.content;
  let text;
  if (!s.torch) text = "未安装模型依赖。";
  else if (!ready) text = "模型还没下载。";
  else if (s.model) text = `已用当前的 ${s.model.chars} 个字训练好（${s.model.iters} 步，压感：${s.model.pressure === "learned" ? "学自你的笔迹" : "通用规则"}）。`;
  else text = n < MIN_CHARS ? `模型已就绪。再写 ${MIN_CHARS - n} 个字就能训练。` : "模型已就绪，可以训练。";
  if (s.running) text += ` 正在${s.running.kind === "train" ? "训练" : "生成"}…`;
  $("model-status").textContent = text;
  const busy = Boolean(s.running);
  $("train").disabled = !ready || n < MIN_CHARS || busy;
  $("train").textContent = s.model ? "重新训练" : "训练模型";
  $("generate").disabled = !ready || !s.model || busy;
  return s;
}

async function poll(job, onProgress) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    const status = await api(`/api/hanzi/job/${job}`);
    if (status.state === "error") throw new Error(status.error);
    if (status.state === "done") return status.report;
    onProgress(status.progress || {});
  }
}

function minutes(seconds) {
  return seconds >= 90 ? `${Math.round(seconds / 60)} 分钟` : `${Math.round(seconds)} 秒`;
}

async function fetchModel() {
  $("model-fetch").disabled = true;
  try {
    const { job } = await api("/api/hanzi/model/fetch", {});
    await poll(job, (p) => {
      if (p.total) {
        const what = p.file === "checkpoint" ? "模型权重" : "字形数据";
        $("model-fetch").textContent = p.file === "checkpoint"
          ? `${what} ${(p.done / 1048576).toFixed(0)} / ${(p.total / 1048576).toFixed(0)} MB`
          : `${what}…`;
      }
    });
    setStatus("模型已下载");
  } catch (err) {
    setStatus(`下载失败：${err.message}`);
  } finally {
    $("model-fetch").disabled = false;
    $("model-fetch").textContent = "下载模型";
    modelStatus();
  }
}

async function train(resume) {
  const capture = payload();
  $("train").disabled = true;
  $("train-meter").hidden = false;
  $("train-bar").style.width = "0";
  $("train-result").textContent = "正在准备…";
  try {
    let job = resume;
    if (!job) {
      const started = await api("/api/hanzi/model/train", { capture, force: Boolean(state.model && state.model.model) });
      job = started.job;
    }
    const report = await poll(job, (p) => {
      if (!p.iters) return;
      $("train-bar").style.width = `${(100 * p.iter) / p.iters}%`;
      $("train-result").textContent = `第 ${p.iter} / ${p.iters} 步 · 验证损失 ${p.val.toFixed(3)} · 还要约 ${minutes(p.eta)}`;
    });
    $("train-bar").style.width = "100%";
    const warn = (report.warnings || []).join("；");
    $("train-result").textContent = `训练完成，用时 ${minutes(report.seconds)}。${warn}`;
  } catch (err) {
    $("train-result").textContent = `训练失败：${err.message}`;
  } finally {
    modelStatus();
  }
}

function targetText() {
  const mode = $("targets").value;
  if (mode === "note") return { targets: noteChars().join(""), gb1: false };
  if (mode === "custom") return { targets: $("custom-targets").value, gb1: false };
  return { targets: "", gb1: true };
}

async function generate(resume) {
  $("generate").disabled = true;
  $("gen-meter").hidden = false;
  $("gen-bar").style.width = "0";
  $("gen-result").textContent = "正在准备…";
  try {
    let job = resume;
    if (!job) {
      const merge = $("merge").value;
      const started = await api("/api/hanzi/model/generate", {
        capture: payload(),
        ...targetText(),
        sets: Number($("sets").value),
        merge: merge === "" ? true : merge === "none" ? false : merge,
        replace: $("replace").checked,
        pressure: pen.gain,
      });
      job = started.job;
    }
    const r = await poll(job, (p) => {
      if (p.phase === "build") {
        $("gen-result").textContent = "正在描摹字形、构建字体…";
      } else if (p.total) {
        $("gen-bar").style.width = `${(100 * p.done) / p.total}%`;
        const left = p.done ? (p.seconds / p.done) * (p.total - p.done) : 0;
        $("gen-result").textContent = `正在写第 ${p.done} / ${p.total} 个字 · 还要约 ${minutes(left)}`;
      }
    });
    $("gen-bar").style.width = "100%";
    const skipped = r.skipped ? `；${r.skipped.length} 个字没写好，保留原字体（${r.skipped.slice(0, 20)}${r.skipped.length > 20 ? "…" : ""}）` : "";
    $("gen-result").innerHTML = `完成：生成 ${r.generated} 字，手写 ${r.written} 字${skipped}`
      + ` · <a href="${r.url}">下载字体</a>`
      + ` · <a class="btn primary small" href="/?font=${encodeURIComponent(r.font)}">在渲染器中使用 →</a>`;
    loadFonts();
  } catch (err) {
    $("gen-result").textContent = `生成失败：${err.message}`;
  } finally {
    modelStatus();
  }
}

async function loadFonts() {
  const select = $("merge");
  const keep = select.value;
  select.textContent = "";
  const add = (value, label) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  };
  add("", "默认字体（志莽行书）");
  try {
    const info = await api("/api/state");
    for (const font of info.fonts || []) {
      if (font.source === "upload") add(font.path, font.name);
    }
  } catch (err) {
    // The default option is enough.
  }
  add("none", "不合并，只要手写汉字");
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

// -- import / export ----------------------------------------------------------------

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
    if (data.width) state.width = Number(data.width) || state.width;
    save();
    setStatus(`已导入 ${count} 个字`);
    refreshCover();
    renderChips();
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
  pen.bind($("pressure"), $("pressure-out"), $("simulate"), () => pad.paint(strokesOf(state.current)));
  $("undo").addEventListener("click", undo);
  $("clear").addEventListener("click", clearCurrent);
  $("prev").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  $("show-guide").addEventListener("change", drawGuide);
  $("show-order").addEventListener("change", drawGuide);
  $("export").addEventListener("click", exportJson);
  $("import").addEventListener("change", (e) => e.target.files[0] && importJson(e.target.files[0]));
  $("model-fetch").addEventListener("click", fetchModel);
  $("train").addEventListener("click", () => train());
  $("generate").addEventListener("click", () => generate());
  $("targets").addEventListener("change", () => {
    $("custom-targets").hidden = $("targets").value !== "custom";
  });
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
  refreshCover();
  renderChips();
  if (!state.current && state.queue.length) {
    const written = writtenChars();
    select(state.queue.find((ch) => !written.has(ch)) || state.queue[0]);
  }
  await loadFonts();
  const s = await modelStatus();
  // A job started before a reload keeps running on the server: follow it.
  if (s && s.running) {
    if (s.running.kind === "train") train(s.running.id);
    else if (s.running.kind === "generate") generate(s.running.id);
  }
}

async function boot() {
  restore();
  pad = createPad($("pad"), { size: PAD_SIZE, width: state.width, onStroke });
  wire();
  drawGuide();
  const status = await api("/api/hanzi/status");
  state.recommended = status.recommended || "";
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
