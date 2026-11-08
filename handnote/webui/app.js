const NUMERIC = [
  "body_size", "line_height", "display_size", "math_scale", "jitter_rot",
  "jitter_dx", "jitter_dy", "ink_alpha_min", "paper_noise", "seed",
];

const state = {
  defaults: {},
  timer: 0,
  seq: 0,
  busy: false,
  lastId: null,
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
  cfg.font_path = $("font-select").value;
  return cfg;
}

function applyConfig(cfg) {
  for (const key of NUMERIC) {
    const el = $(key);
    if (el && cfg[key] !== undefined) el.value = cfg[key];
  }
  $("page_numbers").checked = cfg.page_numbers !== false;
  $("paper").checked = cfg.paper !== false;
  syncOutputs();
}

function syncOutputs() {
  document.querySelectorAll("[data-out]").forEach((node) => {
    const el = $(node.dataset.out);
    if (!el) return;
    const value = Number(el.value);
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
  ["content", "params", "symbols"].forEach((key) => {
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

  applyConfig(boot.defaults);
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
  $("font-select").addEventListener("change", () => schedule(100));

  let mdTimer = 0;
  $("markdown").addEventListener("input", () => {
    clearTimeout(mdTimer);
    mdTimer = setTimeout(() => {
      localStorage.setItem("handnote:markdown", $("markdown").value);
      schedule(300);
    }, 200);
  });

  $("render-btn").addEventListener("click", () => render());
  $("reset-btn").addEventListener("click", () => { applyConfig(state.defaults); schedule(100); });
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
  render();
}

function symbolQuery() {
  const cell = $("sym-cell").value || "24";
  const cols = $("sym-cols").value || "7";
  const rows = $("sym-rows").value || "10";
  const dpi = $("sym-dpi").value || "300";
  return `?cell_mm=${cell}&cols=${cols}&rows=${rows}&dpi=${dpi}`;
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
    for (const file of scans) form.append("scan", file);
    box.textContent = "构建中…";
    try {
      const data = await api("/api/symbols/build", { method: "POST", body: form });
      box.innerHTML = `构建完成：<b>${data.glyphs}</b> 个字形` +
        (data.missing.length ? `，缺 ${data.missing.length} 个（${escapeHtml(data.missing.join(" "))}）` : "") +
        ` · <a href="${data.url}" download>下载 TTF</a>`;
      const option = document.createElement("option");
      option.value = data.font;
      option.textContent = data.font.split("/").pop();
      $("font-select").appendChild(option);
      $("font-select").value = data.font;
      schedule(100);
    } catch (err) {
      box.textContent = "构建失败: " + err.message;
    }
  });
}

boot().catch((err) => setStatus("启动失败: " + err.message));
