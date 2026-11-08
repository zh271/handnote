# handnote

把 Markdown 排成手写页面：逐字抖动、手绘矩阵、mathtext 公式，输出 PNG 或 PDF；附带一个本地 Web UI 调参和实时预览。

## 快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .

python -m handnote render notes.md -o out/notes.pdf   # 默认字体为字迹体
python -m handnote serve                              # http://127.0.0.1:8765/
```

首次运行会从 Google Fonts 下载 ZhiMangXing（OFL）到 `~/.cache/handnote/fonts/`。也可以 `--font /path/to/font.ttf` 指定任意字体。

## 输入语法

| 写法 | 输出 |
|---|---|
| `#` `##` `###` | 三级标题 |
| `- 条目` / `1. 条目` | 项目符号 / 编号 |
| `$x^2$` | 行内公式，与正文同一字体 |
| `$$...$$` | 居中公式 |
| `$$M=\begin{bmatrix}...\end{bmatrix}$$` | 手绘括号矩阵（`pmatrix` 为圆括号） |
| `---` | 强制分页 |
| ` ``` ` 代码块 | 原样输出文字 |

矩阵单元格超过一个字符时自动按公式渲染。

## 数学符号页

默认字迹体没有 `∈ ∪ ⊆ ⊊ ∨ √ ∑ ∫` 等符号，mathtext 会静默回退到 STIX。补字流程三步：

```bash
# 1. 生成可打印模板（A4，24mm 格，7×10，共 3 页 202 个字符）
python -m handnote symbols template -o out/math-symbols.pdf

# 2. 用黑笔逐格描写浅灰字，扫描成图片，按页顺序传入
python -m handnote symbols build scan-1.png scan-2.png scan-3.png \
    -m out/math-symbols.json -o out/my-symbols.ttf

# 3. 合并进字迹体，保留原有汉字
python -m handnote symbols build scan-*.png -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf
```

模板约定：描红字 `RGB(200,200,200)`、格线 `RGB(230,225,225)`、阈值取 128，因此必须用深色笔。每格左上角的 `U+XXXX` 是字符编码，颜色 236，扫描后不可见。

## 浏览器手写采集

打开 `http://127.0.0.1:8765/capture`，页面按模板 spec 画出 202 个格子，鼠标、触控笔或手指直接写，提笔即存 localStorage，刷新不丢。

采集数据是一份可直接喂给 CLI 的 JSON：

```json
{"version": 1, "source": "browser", "width": 0.05,
 "cells": [{"index": 12, "strokes": [[[0.31, 0.18, 0.62], [0.33, 0.25, 0.58]]]}]}
```

| 字段 | 含义 |
|---|---|
| `width` | 笔宽，占格子边长的比例，范围 0.01–0.2 |
| `cells[].index` | 模板格子序号，与 spec.json 一致 |
| `strokes` | 一笔一个数组，点按书写顺序排列 |
| 点 | `[u, v]` 或 `[u, v, pressure]`；u/v 归一化到单格，原点在左上角，y 向下 |

服务端每格按 256px 光栅化：笔宽 × 256，压力把线宽调制到 0.7–1.3 倍，然后走与扫描件完全相同的描摹器。

```bash
python -m handnote symbols capture capture.json -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf
```

网页上的“构建字体”按钮等价于这条命令，默认合并到默认字体。

## 参数

`--set key=value` 可覆盖任意字段，Web UI 里是滑块。

| 字段 | 默认 | 作用 |
|---|---|---|
| `body_size` / `line_height` | 25 / 1.82 | 正文字号与行距 |
| `jitter_rot` | 0.9 | 单字旋转标准差（度） |
| `jitter_dx` / `jitter_dy` | 0.03 / 0.035 | 单字位移标准差（em 比例） |
| `size_wobble` | 1.0 | 字号随机幅度（px） |
| `ink_alpha_min` / `max` | 238 / 255 | 墨色深浅范围 |
| `math_scale` / `display_size` | 1.12 / 36 | 行内公式比例、独立公式字号 |
| `paper_noise` / `paper_gradient` | 1.6 / 3.0 | 纸张噪声与纵向渐变 |
| `seed` | 11 | 随机种子，同种子输出逐字节一致 |
| `dpi` | 150 | PDF 物理尺寸 |

## Web UI

`python -m handnote serve` 启动单进程服务，只监听 `127.0.0.1`，无鉴权，适合本机使用。

| 路由 | 作用 |
|---|---|
| `GET /` | 单页界面 |
| `POST /api/render` | `{markdown, config}` → 预览 URL、PDF URL、字形覆盖报告 |
| `POST /api/analyze` | 只返回缺字报告 |
| `POST /api/upload?kind=font\|markdown` | 导入字体或 Markdown |
| `GET /api/symbols/template.pdf` | 下载符号模板 |
| `POST /api/symbols/build` | 上传扫描图 + spec.json，可选合并到当前字体 |
| `GET /capture` | 浏览器手写采集页 |
| `GET /api/guide-font` | 采集页用的描红字体（按字符子集化） |
| `POST /api/symbols/capture` | 提交笔画 JSON，返回构建好的字体 |

界面分三栏：内容、参数、符号页；覆盖报告会列出缺字并跳到符号页。

## 测试

```bash
pip install -e ".[test]"
python -m pytest -q
```

68 个测试覆盖解析、渲染、字形覆盖、模板几何、扫描建字体、笔画光栅化、字体合并与 HTTP 接口。渲染测试断言同种子逐字节一致，扫描测试用 DejaVu 模拟手写填格后端到端构建字体。

## 边界

- 输出是位图，不是字体文件；要在 Word 里打字仍需要 wefont 那条线。
- Word 的公式走 OMML，不经过本渲染器，逐字抖动无法带入。
- 一个字只有一个字形，重复字仍完全一致；变体方案见 TODO。
- 扫描件需整页平放、无透视；倾斜过大的页面会切格错位。

## 状态

| 能力 | 状态 | 证据 |
|---|---|---|
| Markdown → 手写页面（抖动、公式、矩阵、分页） | [VERIFIED] | `tests/test_render.py` |
| 字形覆盖分析与缺字报告 | [VERIFIED] | `tests/test_render.py::test_coverage_flags_missing_symbol` |
| 符号模板生成（202 字符 / 3 页） | [VERIFIED] | `tests/test_symbols.py::test_every_symbol_has_a_cell` |
| 扫描 → TTF → 合并字体 | [VERIFIED] | `tests/test_symbols.py::test_scan_round_trip` |
| 浏览器笔画 → TTF → 合并 | [VERIFIED] | `tests/test_capture.py` |
| 本地 Web UI 与 API | [VERIFIED] | `tests/test_webui.py` |
| 字形变体（OpenType `rand` / `calt`） | [PLANNED] | TODO.md |
| 透视校正的扫描切格 | [PLANNED] | TODO.md |
