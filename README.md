# handnote

[![tests](https://github.com/zh271/handnote/actions/workflows/tests.yml/badge.svg)](https://github.com/zh271/handnote/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](pyproject.toml)

把 Markdown 排成手写页面：逐字抖动、手绘矩阵、mathtext 公式，输出 PNG 或 PDF；附带一个本地 Web UI 调参和实时预览。

另有一条补字链路：打印符号模板 → 手写（纸笔扫描或浏览器采集）→ 描摹成 TTF → 合并进中文字体，补掉 mathtext 静默回退的数学符号。

## 快速开始

```bash
git clone https://github.com/zh271/handnote.git
cd handnote
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

python -m handnote render notes.md -o out/notes.pdf   # 默认字体为字迹体
python -m handnote serve                              # http://127.0.0.1:8765/
```

首次运行会从 Google Fonts 下载 ZhiMangXing（OFL）到 `~/.cache/handnote/fonts/`。也可以 `--font /path/to/font.ttf` 指定任意字体。

## 项目结构

```
handnote/
  config.py     RenderConfig：全部参数、钳制与校验
  markdown.py   Markdown 子集 -> Block
  coverage.py   LaTeX -> Unicode，缺字报告
  render.py     layout -> paginate -> render_page
  symbols.py    模板、描摹、assemble_font、merge_fonts
  capture.py    笔画 JSON、光栅化、build_capture_font
  fonts.py      字体缓存与下载
  cli.py        命令行
  webui/        stdlib HTTP 服务、渲染界面、采集页
examples/       三份可直接渲染的示例
docs/formats.md spec / capture / 构建报告的字段定义
CLAUDE.md       给接手开发的 agent 的工程约定
```

## 输入语法

| 写法 | 输出 |
|---|---|
| `#` `##` `###` | 三级标题 |
| `- 条目` / `1. 条目` | 项目符号 / 编号 |
| `$x^2$` | 行内公式，与正文同一字体 |
| `$$...$$` | 居中公式 |
| `$$M=\begin{bmatrix}...\end{bmatrix}$$` | 手绘括号矩阵（`pmatrix` 为圆括号，`vmatrix` 竖线，`matrix` 无括号） |
| 单独一行的 `$\begin{bmatrix}...\end{bmatrix}$` | 同上，按独立矩阵居中 |
| 正文里的 `$...\begin{bmatrix}...\end{bmatrix}...$` | 行内小矩阵，所在行自动加高 |
| `---` | 强制分页 |
| ` ``` ` 代码块 | 原样输出文字 |

矩阵单元格超过一个字符时自动按公式渲染。矩阵后面还有内容（如 `A=\begin{bmatrix}..\end{bmatrix}, B=...`）或一条公式里有多个矩阵时，整条按公式排版，矩阵在公式内手绘。行分隔写 `\\`；如果复制时被吞成单个 `\`，也能识别。

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
 "cells": [{"index": 12, "set": 0, "strokes": [[[0.31, 0.18, 0.62], [0.33, 0.25, 0.58]]]}]}
```

| 字段 | 含义 |
|---|---|
| `width` | 笔宽，占格子边长的比例，范围 0.01–0.2 |
| `cells[].index` | 模板格子序号，与 spec.json 一致 |
| `cells[].set` | 第几套，0–7，可省略（默认 0）；同一格每套一条 |
| `strokes` | 一笔一个数组，点按书写顺序排列 |
| 点 | `[u, v]` 或 `[u, v, pressure]`；u/v 归一化到单格，原点在左上角，y 向下 |

服务端每格按 256px 光栅化：笔宽 × 256，压力把线宽调制到 0.7–1.3 倍，然后走与扫描件完全相同的描摹器。

```bash
python -m handnote symbols capture capture.json -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf
```

网页上的“构建字体”按钮等价于这条命令，默认合并到默认字体，并勾选“手写覆盖已有字符”。构建完成后点“在渲染器中使用”即可切换；渲染器的字体下拉框也会列出所有合并过的字体（“手写合并 月-日 时:分”）。

### 对齐描红字，保留部分误差

构建时每个手写字的大小和上下位置向模板里的浅灰描红字对齐，只保留一部分书写偏差，既避免某个字写得过大、过小或偏到格子一角，又留下自然的变化。比例由 `--keep`（采集页“保留书写误差”滑块）控制，默认 0.3：0 完全对齐描红字，1 为原样。用 `--replace` 覆盖已有字符时，各套共用第一套的缩放，套与套之间的大小差异会保留。

### 用手写替换原有字符

默认合并只补基础字体缺的符号，数字、字母这类基础字体已有的字符会保留原字形（比如纸芒行书的 0 中间有一道）。加 `--replace`（网页上是“手写覆盖已有字符”）后，你写过的字符一律换成手写：

```bash
python -m handnote symbols capture capture.json -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf --replace
```

- 只替换采集或扫描里真正写了的字符；没写的字符和全部汉字不变。
- 手写字形按被替换字形的高度、垂直中心和左右留白缩放对齐，所以基线和字号与原字体一致（横线、句点等扁平字符按宽度对齐）。
- 原字形仍留在字体里，只是字符映射改指到手写字形，依赖它的组合字形和排版特性不受影响。
- 写了多套的字符，多套变体同样带过去。

## 多套字形

同一个字反复出现时，手写不会每次一模一样。每次画字随机取一套，同一个字不会连续两次用同一套。字形有两个来源：

1. **手写多套**（优先）：同一张模板写几遍，每遍是一套。字体里带了手写变体的字符，就在这几套之间轮换，不做任何变形。
2. **合成多套**：其余字符默认 4 套，第 0 套是字体原字形，其余几套对每个字形做一次轻微、平滑的变形（缩放、斜切、旋转加低频弯曲）。

`glyph_sets=1` 同时关闭两者。

### 手写多套

浏览器采集页顶栏的“当前套”可以切到第 1–8 套，每套各自保存，构建时一起提交。纸笔方式：

```bash
# 模板打印 3 遍，页眉标“第 k/3 套”
python -m handnote symbols template -o out/math-symbols.pdf --sets 3
# 3 套共 9 页一起传入，按文件名自然排序，每 3 页算一套
python -m handnote symbols build scan-*.png -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf
```

构建出的字体里，第一套是默认字形（cmap），其余是 `uniXXXX.sN` 变体，并写入两个 OpenType 特性，所以装到系统里同样有效：

| 特性 | 谁会用 | 效果 |
|---|---|---|
| `rand` | HarfBuzz（浏览器、LibreOffice 等）默认开启 | 每个字随机取一套 |
| `calt` | Word 需在“字体 → 高级”勾选上下文替代 | 连续出现的字依次轮换各套 |

合并时变体跟随新增的码位；加 `--replace` 时被替换的字符也带上变体。

### 渲染细节

- 公式：matplotlib 排版后逐字形替换轮廓，包括 STIX 回退的符号。
- 矩阵单元格、正文、页码：每套生成一份只含本页所需字符的小字体，仍由 FreeType 光栅化。
- 同一字符在同一套里形状固定，由 `(seed, 码位, 套号)` 决定；同种子输出逐字节一致。

```bash
python -m handnote render notes.md --set glyph_sets=6 --set glyph_strength=1.4
python -m handnote render notes.md --set glyph_sets=1   # 关闭
```

## 参数

`--set key=value` 可覆盖任意字段，Web UI 里是滑块。

| 字段 | 默认 | 作用 |
|---|---|---|
| `body_size` / `line_height` | 25 / 1.82 | 正文字号与行距 |
| `char_spacing` | 0.12 | 字距（em）；ASCII 字母数字只加四分之一，单词不散 |
| `digit_spacing` / `symbol_spacing` | 0.06 / 0.08 | 数字之间、符号（`+ = ( ) ∈` 等）之后的间距（em） |
| `punct_squeeze` | 1.0 | 全角标点按墨迹重排的程度：1 去掉字身空白，只留少量间距；0 为字体原宽 |
| `math_gap` | 0.25 | 行内公式两侧留白（em），按公式墨迹计算；汉字与公式之间手打的空格会被吞掉 |
| `jitter_rot` | 0.9 | 单字旋转标准差（度） |
| `jitter_dx` / `jitter_dy` | 0.03 / 0.035 | 单字位移标准差（em 比例） |
| `size_wobble` | 1.0 | 字号随机幅度（px） |
| `ink_alpha_min` / `max` | 238 / 255 | 墨色深浅范围 |
| `math_scale` / `display_size` | 1.12 / 36 | 行内公式比例、独立公式字号 |
| `paper_noise` / `paper_gradient` | 1.6 / 3.0 | 纸张噪声与纵向渐变 |
| `glyph_sets` | 4 | 字形套数；1 为关闭，每次出现随机取一套且不与上次相同 |
| `glyph_strength` | 1.0 | 各套之间的字形差异幅度，0 等于关闭 |
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

121 个测试覆盖解析、渲染、间距、字形套与手写变体、手写替换、字形覆盖、模板几何、扫描建字体、笔画光栅化、字体合并与 HTTP 接口。渲染测试断言同种子逐字节一致，扫描测试用 DejaVu 模拟手写填格后端到端构建字体。

## 边界

- 输出是位图，不是字体文件；要在 Word 里打字仍需要 wefont 那条线。
- Word 的公式走 OMML，不经过本渲染器，逐字抖动无法带入。
- 手写多套只作用于写过的字符（补进来的符号，或 `--replace` 替换的数字、字母）；汉字仍用合成多套。
- 扫描件需整页平放、无透视；倾斜过大的页面会切格错位。

## 文档

| 文件 | 内容 |
|---|---|
| `docs/formats.md` | spec.json、capture.json、构建报告的字段与几何约定 |
| `CLAUDE.md` | 模块地图、不变量、测试与代码风格约定 |
| `MILESTONES.md` / `TODO.md` | 进度与待办 |

## 状态

| 能力 | 状态 | 证据 |
|---|---|---|
| Markdown → 手写页面（抖动、公式、矩阵、分页） | [VERIFIED] | `tests/test_render.py` |
| 字形覆盖分析与缺字报告 | [VERIFIED] | `tests/test_render.py::test_coverage_flags_missing_symbol` |
| 符号模板生成（202 字符 / 3 页） | [VERIFIED] | `tests/test_symbols.py::test_every_symbol_has_a_cell` |
| 扫描 → TTF → 合并字体 | [VERIFIED] | `tests/test_symbols.py::test_scan_round_trip` |
| 浏览器笔画 → TTF → 合并 | [VERIFIED] | `tests/test_capture.py` |
| 本地 Web UI 与 API | [VERIFIED] | `tests/test_webui.py` |
| 手写多套 → 字体变体（`rand` / `calt`）→ 渲染 | [VERIFIED] | `tests/test_sets.py` |
| 多套字形（公式、矩阵、正文，渲染期合成） | [VERIFIED] | `tests/test_variants.py`, `tests/test_render.py::test_repeated_formula_glyphs_differ` |
| 行内矩阵、公式内多矩阵 | [VERIFIED] | `tests/test_render.py::test_inline_matrix_renders_and_grows_line` |
| 透视校正的扫描切格 | [PLANNED] | TODO.md |

## 许可

MIT，见 [LICENSE](LICENSE)。仓库不包含字体二进制：ZhiMangXing、MaShanZheng、Noto Sans SC 采用 SIL Open Font License 1.1，首次运行时从 Google Fonts 下载到 `~/.cache/handnote/fonts/`；生成的字体只受你输入笔迹的版权约束。
