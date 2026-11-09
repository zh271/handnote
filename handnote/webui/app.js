const NUMERIC = [
  "body_size", "line_height", "char_spacing", "digit_spacing",
  "symbol_spacing", "punct_squeeze", "math_gap",
  "display_size", "math_scale", "jitter_rot",
  "jitter_dx", "jitter_dy", "ink_alpha_min", "glyph_sets", "glyph_strength",
  "paper_noise", "seed",
  "rule_alpha", "rule_width", "rule_pitch", "rule_top", "cell_size",
  "margin_top", "margin_x", "margin_bottom",
];

// Paper settings remembered across visits.
const PAPER_KEYS = [
  "paper_style", "paper_color", "rule_color", "rule_alpha", "rule_width",
  "rule_pitch", "rule_top", "cell_size", "margin_rule", "paper_snap", "paper_image",
  "cell_snap", "grid_cols", "grid_rows", "count_marks",
  "margin_top", "margin_x", "margin_bottom",
];
const PAPER_STORE = "handnote:paper";

const state = {
  defaults: {},
  timer: 0,
  seq: 0,
  busy: false,
  lastId: null,
  presets: [],
  paperImage: "",
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  const ctype = res.headers.get("content-type") || "";
  const body = ctype.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

function setStatus(text) {
  $("status").textContent = text;
}

function readConfig() {
  const cfg = {};
  for (const key of NUMERIC) {
    const el = $(key);
    if (!el) continue;
    const value = Number(el.value);
    cfg[key] = Number.isFinite(value) ? value : 0;
  }
  cfg.page_numbers = $("page_numbers").checked;
  cfg.paper = $("paper").checked;
  cfg.vector_pdf = $("vector_pdf").checked;
  cfg.font_path = $("font-select").value;
  cfg.paper_style = $("paper_style").value;
  cfg.paper_color = $("paper_color_on").checked ? $("paper_color").value : "";
  cfg.rule_color = $("rule_color").value;
  cfg.margin_rule = $("margin_rule").checked;
  cfg.paper_snap = $("paper_snap").checked;
  cfg.cell_snap = $("cell_snap").checked;
  cfg.count_marks = $("count_marks").checked;
  cfg.grid_cols = Math.max(0, Math.round(Number($("grid_cols").value) || 0));
  cfg.grid_rows = Math.max(0, Math.round(Number($("grid_rows").value) || 0));
  cfg.paper_image = cfg.paper_style === "image" ? (state.paperImage || "") : "";
  return cfg;
}

function applyConfig(cfg) {
  for (const key of NUMERIC) {
    const el = $(key);
    if (el && cfg[key] !== undefined) el.value = cfg[key];
  }
  $("page_numbers").checked = cfg.page_numbers !== false;
  $("paper").checked = cfg.paper !== false;
  $("vector_pdf").checked = cfg.vector_pdf !== false;
  applyPaper(cfg);
}

function applyPaper(cfg) {
  for (const key of PAPER_KEYS) {
    const el = $(key);
    if (el && (el.type === "range" || el.type === "number") && cfg[key] !== undefined) {
      el.value = cfg[key];
    }
  }
  if (cfg.paper_style !== undefined) $("paper_style").value = cfg.paper_style;
  if (cfg.paper_color !== undefined) {
    $("paper_color_on").checked = !!cfg.paper_color;
    if (cfg.paper_color) $("paper_color").value = cfg.paper_color;
  }
  if (cfg.rule_color) $("rule_color").value = cfg.rule_color;
  if (cfg.margin_rule !== undefined) $("margin_rule").checked = !!cfg.margin_rule;
  if (cfg.paper_snap !== undefined) $("paper_snap").checked = cfg.paper_snap !== false;
  if (cfg.cell_snap !== undefined) $("cell_snap").checked = cfg.cell_snap !== false;
  if (cfg.count_marks !== undefined) $("count_marks").checked = cfg.count_marks !== false;
  if (cfg.grid_cols !== undefined || cfg.grid_rows !== undefined) syncCountSelect();
  if (cfg.paper_image !== undefined) state.paperImage = cfg.paper_image;
  syncPaper();
  syncOutputs();
}

function syncCountSelect() {
  const value = `${Number($("grid_cols").value) || 0}x${Number($("grid_rows").value) || 0}`;
  const select = $("essay-count");
  const known = [...select.options].some((option) => option.value === value);
  if (select.value !== "custom" || known) select.value = known ? value : "custom";
}

function syncPaper() {
  const style = $("paper_style").value;
  $("cell-opts").classList.toggle("hidden", !["essay", "tianzi", "grid"].includes(style));
  $("essay-opts").classList.toggle("hidden", !["essay", "tianzi"].includes(style));
  $("essay-custom").classList.toggle("hidden", $("essay-count").value !== "custom");
  $("paper_color").disabled = !$("paper_color_on").checked;
  $("scan-clear").classList.toggle("hidden", $("paper_style").value !== "image");
  const current = readConfig();
  document.querySelectorAll("#paper-presets button").forEach((btn) => {
    const preset = state.presets.find((p) => p.key === btn.dataset.key);
    const same = preset && Object.entries(preset.config).every(([key, value]) =>
      key === "paper_image" || String(current[key]) === String(value));
    btn.classList.toggle("active", !!same);
  });
}

function savePaper() {
  const cfg = readConfig();
  const keep = {};
  for (const key of PAPER_KEYS) keep[key] = cfg[key];
  try { localStorage.setItem(PAPER_STORE, JSON.stringify(keep)); } catch (err) { /* optional */ }
}

function syncOutputs() {
  document.querySelectorAll("[data-out]").forEach((node) => {
    const el = $(node.dataset.out);
    if (!el) return;
    const value = Number(el.value);
    if (value === 0 && node.dataset.zero) { node.textContent = node.dataset.zero; return; }
    node.textContent = Number.isInteger(value) ? value : value.toFixed(2);
  });
}

function schedule(delay = 450) {
  clearTimeout(state.timer);
  state.timer = setTimeout(render, delay);
}

async function render() {
  if (state.busy) {
    schedule(600);
    return;
  }
  const markdown = $("markdown").value;
  if (!markdown.trim()) {
    $("preview").innerHTML = '<div class="empty">左侧输入 Markdown，<br>公式用 $..$ 包住。</div>';
    setStatus("空内容");
    return;
  }
  state.busy = true;
  const seq = ++state.seq;
  setStatus("渲染中…");
  try {
    const data = await api("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown, config: readConfig() }),
    });
    if (seq !== state.seq) return;
    state.lastId = data.id;
    paint(data);
    setStatus(`${data.pages.length} 页 · ${data.elapsed}s`);
  } catch (err) {
    setStatus("失败: " + err.message);
  } finally {
    state.busy = false;
  }
}

function paint(data) {
  const holder = $("preview");
  holder.innerHTML = "";
  data.pages.forEach((page) => {
    const img = document.createElement("img");
    img.src = page.url;
    img.alt = "第 " + page.url.split("/").pop();
    img.loading = "lazy";
    holder.appendChild(img);
  });
  $("page-info").textContent = `${data.pages.length} 页 · ${data.font.split("/").pop()}`;
  const pdf = $("download-pdf");
  pdf.href = data.pdf;
  pdf.classList.remove("disabled");
  paintCoverage(data.coverage);
  if (data.warnings && data.warnings.length) {
    setStatus(`${data.pages.length} 页 · ${data.warnings.length} 个警告`);
    console.warn(data.warnings);
  }
}

function paintCoverage(report) {
  const box = $("coverage");
  if (!report) { box.textContent = ""; return; }
  if (!report.missing.length) {
    box.innerHTML = `字形覆盖 <b>100%</b> · ${report.total} 个字符全部命中`;
    return;
  }
  const chars = report.missing.map((m) => m.char).join(" ");
  box.innerHTML =
    `字形覆盖 ${(report.coverage * 100).toFixed(1)}% · 缺 <b>${report.missing.length}</b> 个：` +
    `<span title="${report.missing.map((m) => m.code).join(" ")}">${escapeHtml(chars)}</span>` +
    ` — 到「符号页」生成模板补上。`;
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  ["content", "params", "paper", "symbols"].forEach((key) => {
    $("tab-" + key).classList.toggle("hidden", key !== name);
  });
}

async function boot() {
  const boot = await api("/api/state");
  state.defaults = boot.defaults;

  const select = $("font-select");
  select.innerHTML = "";
  boot.fonts.forEach((font) => {
    const option = document.createElement("option");
    option.value = font.path;
    option.textContent = font.name;
    select.appendChild(option);
  });
  if (boot.defaults.font_path) select.value = boot.defaults.font_path;
  let wanted = new URLSearchParams(window.location.search).get("font");
  if (!wanted) {
    try { wanted = localStorage.getItem("handnote:font"); } catch (err) { wanted = null; }
    if (wanted && ![...select.options].some((option) => option.value === wanted)) wanted = null;
  }
  if (wanted) {
    if (![...select.options].some((option) => option.value === wanted)) {
      const option = document.createElement("option");
      option.value = wanted;
      option.textContent = wanted.split(/[\\/]/).pop();
      select.appendChild(option);
    }
    select.value = wanted;
    try { localStorage.setItem("handnote:font", wanted); } catch (err) { /* optional */ }
  }

  state.presets = boot.paper_presets || [];
  buildPresets();
  applyConfig(boot.defaults);
  try {
    const paper = JSON.parse(localStorage.getItem(PAPER_STORE) || "null");
    if (paper) applyPaper(paper);
  } catch (err) { /* ignore a broken entry */ }
  const saved = localStorage.getItem("handnote:markdown");
  $("markdown").value = saved || boot.sample;
  state.sample = boot.sample;

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
  document.querySelectorAll("#controls input").forEach((el) => {
    el.addEventListener("input", () => { syncOutputs(); schedule(); });
    el.addEventListener("change", () => { syncOutputs(); schedule(100); });
  });
  $("font-select").addEventListener("change", () => {
    try { localStorage.setItem("handnote:font", $("font-select").value); } catch (err) { /* optional */ }
    schedule(100);
  });

  let mdTimer = 0;
  $("markdown").addEventListener("input", () => {
    clearTimeout(mdTimer);
    mdTimer = setTimeout(() => {
      localStorage.setItem("handnote:markdown", $("markdown").value);
      schedule(300);
    }, 200);
  });

  $("render-btn").addEventListener("click", () => render());
  $("reset-btn").addEventListener("click", () => {
    applyConfig(state.defaults);
    savePaper();
    schedule(100);
  });
  $("sample-btn").addEventListener("click", () => { $("markdown").value = state.sample; schedule(100); });
  $("clear-btn").addEventListener("click", () => { $("markdown").value = ""; render(); });

  $("md-upload").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const data = await api("/api/upload?kind=markdown", { method: "POST", body: form });
      $("markdown").value = data.text;
      schedule(100);
    } catch (err) { setStatus("导入失败: " + err.message); }
  });

  $("font-upload").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const data = await api("/api/upload?kind=font", { method: "POST", body: form });
      const option = document.createElement("option");
      option.value = data.path;
      option.textContent = data.name;
      $("font-select").appendChild(option);
      $("font-select").value = data.path;
      schedule(100);
    } catch (err) { setStatus("字体导入失败: " + err.message); }
  });

  bindSymbols();
  bindPaper();
  render();
}

function symbolQuery() {
  const cell = $("sym-cell").value || "24";
  const cols = $("sym-cols").value || "7";
  const rows = $("sym-rows").value || "10";
  const dpi = $("sym-dpi").value || "300";
  const sets = $("sym-sets").value || "1";
  return `?cell_mm=${cell}&cols=${cols}&rows=${rows}&dpi=${dpi}&sets=${sets}`;
}

function bindSymbols() {
  $("sym-pdf").addEventListener("click", () => {
    window.open("/api/symbols/template.pdf" + symbolQuery(), "_blank");
  });
  $("sym-json").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/symbols/template.json" + symbolQuery());
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "handnote-symbols.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    } catch (err) { setStatus("下载失败: " + err.message); }
  });
  $("sym-build").addEventListener("click", async () => {
    const scans = $("sym-scans").files;
    const spec = $("sym-spec").files[0];
    const box = $("sym-result");
    if (!scans.length || !spec) { box.textContent = "请选择扫描图和 spec.json"; return; }
    const form = new FormData();
    form.append("spec", spec);
    if ($("sym-merge").checked) form.append("merge", $("font-select").value);
    if ($("sym-replace").checked) form.append("replace", "1");
    form.append("keep", $("sym-keep").value || "0.3");
    for (const file of scans) form.append("scan", file);
    box.textContent = "构建中…";
    try {
      const data = await api("/api/symbols/build", { method: "POST", body: form });
      box.innerHTML = `构建完成：<b>${data.glyphs}</b> 个字形` +
        (data.sets > 1 ? `，最多 <b>${data.sets}</b> 套` : "") +
        (data.warped_pages ? `，${data.warped_pages} 页已按定位标记校正` : "") +
        (data.missing.length ? `，缺 ${data.missing.length} 个（${escapeHtml(data.missing.join(" "))}）` : "") +
        ` · <a href="${data.url}" download>下载 TTF</a>`;
      const option = document.createElement("option");
      option.value = data.font;
      option.textContent = data.font.split(/[\\/]/).pop();
      $("font-select").appendChild(option);
      $("font-select").value = data.font;
      schedule(100);
    } catch (err) {
      box.textContent = "构建失败: " + err.message;
    }
  });
}

boot().catch((err) => setStatus("启动失败: " + err.message));

// -- paper ----------------------------------------------------------------
function buildPresets() {
  const box = $("paper-presets");
  box.innerHTML = "";
  state.presets.forEach((preset) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.key = preset.key;
    btn.setAttribute("role", "listitem");
    const canvas = document.createElement("canvas");
    canvas.width = 112;
    canvas.height = 152;
    drawThumb(canvas, preset.config);
    const label = document.createElement("span");
    label.textContent = preset.label;
    btn.append(canvas, label);
    btn.addEventListener("click", () => {
      const cfg = { ...preset.config };
      delete cfg.paper_image;
      applyPaper(cfg);
      savePaper();
      schedule(100);
    });
    box.appendChild(btn);
  });
}

function drawThumb(canvas, cfg) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const line = (x0, y0, x1, y1) => {
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
  };
  ctx.fillStyle = cfg.paper_color || "#fafafa";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = cfg.rule_color;
  ctx.fillStyle = cfg.rule_color;
  ctx.globalAlpha = Math.min(1, cfg.rule_alpha / 200);
  ctx.lineWidth = 1;
  const style = cfg.paper_style;
  if (style === "lined") {
    for (let y = 18; y < h - 6; y += 10) line(5, y + 0.5, w - 5, y + 0.5);
  } else if (style === "grid") {
    for (let y = 6; y < h - 4; y += 6) line(6, y + 0.5, w - 6, y + 0.5);
    for (let x = 6; x < w - 4; x += 6) line(x + 0.5, 6, x + 0.5, h - 6);
  } else if (style === "dots") {
    for (let y = 6; y < h - 4; y += 7) {
      for (let x = 6; x < w - 4; x += 7) ctx.fillRect(x, y, 1.8, 1.8);
    }
  } else if (style === "essay" || style === "tianzi") {
    const c = 12;
    for (let y = 14; y + c < h - 6; y += c + 5) {
      for (let x = 8; x + c <= w - 8; x += c) {
        ctx.strokeRect(x + 0.5, y + 0.5, c, c);
        if (style === "tianzi") {
          ctx.save();
          ctx.setLineDash([2, 2]);
          ctx.globalAlpha *= 0.6;
          line(x + c / 2 + 0.5, y, x + c / 2 + 0.5, y + c);
          line(x, y + c / 2 + 0.5, x + c, y + c / 2 + 0.5);
          ctx.restore();
        }
      }
    }
  }
  if (cfg.margin_rule) {
    ctx.globalAlpha = 0.8;
    ctx.strokeStyle = "#de7878";
    line(16.5, 0, 16.5, h);
  }
}

function paperChanged(delay = 450) {
  syncPaper();
  syncOutputs();
  savePaper();
  schedule(delay);
}

function bindPaper() {
  $("essay-count").addEventListener("change", () => {
    const value = $("essay-count").value;
    if (value !== "custom") {
      const [cols, rows] = value.split("x").map(Number);
      $("grid_cols").value = cols;
      $("grid_rows").value = rows;
    }
  });
  document.querySelectorAll("#paper-controls input, #paper-controls select").forEach((el) => {
    el.addEventListener("input", () => paperChanged());
    el.addEventListener("change", () => paperChanged(100));
  });
  const scan = { id: null, width: 0, height: 0, detected: null, corners: null };
  const img = $("scan-img");
  const svg = $("scan-svg");

  function drawCorners() {
    if (!scan.corners || !img.clientWidth) return;
    const sx = img.clientWidth / scan.width;
    const sy = img.clientHeight / scan.height;
    const pts = scan.corners.map(([x, y]) => [x * sx, y * sy]);
    svg.setAttribute("viewBox", `0 0 ${img.clientWidth} ${img.clientHeight}`);
    svg.innerHTML = `<polygon points="${pts.map((p) => p.join(",")).join(" ")}"></polygon>` +
      pts.map((p, i) => `<circle r="9" cx="${p[0]}" cy="${p[1]}" data-i="${i}"></circle>`).join("");
  }
  img.addEventListener("load", drawCorners);
  window.addEventListener("resize", drawCorners);

  let drag = -1;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.tagName !== "circle") return;
    drag = Number(event.target.dataset.i);
    svg.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  svg.addEventListener("pointermove", (event) => {
    if (drag < 0) return;
    const rect = img.getBoundingClientRect();
    const w = rect.width || 1;
    const h = rect.height || 1;
    const x = Math.min(Math.max(event.clientX - rect.left, 0), w);
    const y = Math.min(Math.max(event.clientY - rect.top, 0), h);
    scan.corners[drag] = [x * scan.width / w, y * scan.height / h];
    drawCorners();
  });
  const stop = () => { drag = -1; };
  svg.addEventListener("pointerup", stop);
  svg.addEventListener("pointercancel", stop);

  $("paper-scan").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    $("scan-result").textContent = "上传中…";
    try {
      const data = await api("/api/paper/scan", { method: "POST", body: form });
      Object.assign(scan, data, { detected: data.corners.map((p) => [...p]) });
      img.src = data.preview;
      $("scan-editor").classList.remove("hidden");
      $("scan-result").textContent = "拖动四个圆点对准纸张的四个角，然后点“应用为背景”。";
    } catch (err) {
      $("scan-result").textContent = "上传失败: " + err.message;
    }
  });
  $("scan-detected").addEventListener("click", () => {
    if (!scan.detected) return;
    scan.corners = scan.detected.map((p) => [...p]);
    drawCorners();
  });
  $("scan-full").addEventListener("click", () => {
    const w = scan.width - 1;
    const h = scan.height - 1;
    scan.corners = [[0, 0], [w, 0], [w, h], [0, h]];
    drawCorners();
  });
  ["scan-evenness", "scan-whiten"].forEach((id) => $(id).addEventListener("input", syncOutputs));
  $("scan-apply").addEventListener("click", async () => {
    if (!scan.id) return;
    const box = $("scan-result");
    box.textContent = "处理中…";
    try {
      const data = await api("/api/paper/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: scan.id,
          corners: scan.corners,
          width: state.defaults.page_width,
          height: state.defaults.page_height,
          evenness: Number($("scan-evenness").value),
          whiten: Number($("scan-whiten").value),
          gray: $("scan-gray").checked,
        }),
      });
      const rules = data.rules;
      applyPaper({
        paper_style: "image",
        paper_image: data.path,
        paper_snap: true,
        rule_pitch: rules ? rules.pitch : 0,
        rule_top: rules ? rules.top : 0,
      });
      box.innerHTML = (rules
        ? `已应用。检测到 <b>${rules.count}</b> 条横线，行距 <b>${rules.pitch}</b> px，文字已对齐到线上。`
        : "已应用。没有检测到规则的横线，文字按边距自由排版；需要对齐时手动设置“行距”和“首条线位置”。") +
        `<img class="scan-thumb" src="${data.url}" alt="处理后的稿纸">`;
      savePaper();
      schedule(100);
    } catch (err) {
      box.textContent = "处理失败: " + err.message;
    }
  });
  $("scan-clear").addEventListener("click", () => {
    applyPaper({ paper_style: "plain", paper_image: "", rule_pitch: 0, rule_top: 0 });
    $("scan-result").textContent = "";
    savePaper();
    schedule(100);
  });
}
