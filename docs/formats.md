# 数据格式

三份 JSON 串起整条链路：`spec.json` 描述模板几何，`capture.json` 记录笔画，构建报告描述产物。三者的 `index` 必须一致。

## 1. 模板 spec

由 `python -m handnote symbols template` 生成，与 PDF 同目录。

```json
{
  "version": 1,
  "sets": 1,
  "dpi": 300,
  "cell_px": 283,
  "cell_mm": 24.0,
  "cols": 7,
  "rows": 10,
  "pages": 3,
  "page_size_px": [2480, 3508],
  "origin_px": [293, 318],
  "guide_missing": [],
  "markers": [
    {"id": 0, "sheet": 0, "corner": 0, "points": [[77, 77], [183, 77], [183, 183], [77, 183]]}
  ],
  "cells": [
    {"index": 0, "page": 0, "row": 0, "col": 0,
     "char": "A", "code": 65, "bbox": [293, 318, 576, 601]}
  ]
}
```

| 字段 | 含义 |
|---|---|
| `sets` | 模板打印了几套（`--sets`），PDF 页数 = `pages × sets`；旧 spec 缺省为 1 |
| `pages` | 一套的页数 |
| `dpi` | PDF 物理尺寸的基准，`page_size_px / dpi * 25.4` 等于毫米 |
| `cell_px` / `cell_mm` | 格子边长，像素与毫米两种写法 |
| `page_size_px` | 整页像素尺寸，A4 在 300dpi 下为 2480×3508 |
| `origin_px` | 网格左上角，`[x, y]` |
| `guide_missing` | 描红字体缺的字符；非空说明模板里有空格子 |
| `markers` | 每张打印页四角的 ArUco 标记（`DICT_4X4_1000`）。`sheet` 为打印页序号（按套优先：第 k 套第 p 页 = `k × pages + p`），`corner` 0–3 依次为左上、右上、右下、左下，`id = sheet × 4 + corner`，`points` 为标记四角像素坐标（顺时针，从左上起）。旧 spec 没有此字段，构建时按文件名顺序且不做校正 |
| `cells[].bbox` | 该格的绝对像素包围盒 `[x0, y0, x1, y1]` |
| `cells[].index` | 全局序号，采集与构建都靠它对位 |

几何约定：网格从 `origin_px` 起，第 `r` 行 `c` 列的格子左上角是
`origin + (c * cell_px, r * cell_px)`。`bbox` 是冗余字段，方便直接切图。

## 2. 采集数据

浏览器采集页导出，也是 `handnote symbols capture` 的输入。

```json
{
  "version": 1,
  "source": "browser",
  "width": 0.05,
  "cells": [
    {"index": 12, "set": 0,
     "strokes": [
       [[0.31, 0.18, 0.62], [0.33, 0.25, 0.58], [0.36, 0.31, 0.55]],
       [[0.52, 0.20], [0.50, 0.44], [0.49, 0.71]]
     ]}
  ]
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| `width` | float | 笔宽占格子边长的比例，0.01–0.2，缺省 0.05 |
| `cells[].index` | int | 0 ≤ index < `len(spec.cells)` |
| `cells[].set` | int | 0 ≤ set < 8，缺省 0；同一 `index` 可在不同套各出现一次 |
| `strokes` | array | 一笔一个数组，最多 64 笔/格 |
| 点 | `[u, v]` 或 `[u, v, pressure]` | `u, v ∈ [0,1]`，原点在格子左上角，y 向下；`pressure ∈ [0,1]` 缺省 1 |

服务端处理：

1. 每格按 `size`（默认 256）光栅化。线宽逐点计算：`width * size * max(0.3, 1 + gain * 0.6 * (p - 0.5))`，`p` 是该点压力（先做三点滑动平均），`gain` 是压感强度（API `"pressure"`、CLI `--pressure`，0–2，默认 1）。每段画成两端圆盘之间的四边形，所以笔画沿途粗细渐变。压力恒为 0.5 时与 `gain` 无关；默认 `gain=1` 时压力 0 → 0.7 倍、1 → 1.3 倍，与旧版按平均压力调制的结果在压力恒定时一致。
2. 圆头圆角，端点补圆点。
3. 送入 `_cell_glyph()`，与扫描件共用描摹与平滑。
4. 描摹后按模板描红字的墨迹框和基线对齐：大小取 `(描红/手写)^(1-keep)`，中心取 `描红中心 + keep × (手写中心 − 描红中心)`；`keep=1` 时退回旧行为（字底统一放在 y=60）。

越界坐标会被钳到 `[0,1]`；`index` 越界、NaN、非数字坐标直接抛 `CaptureError`（HTTP 400）。

## 3. 构建报告

`symbols build` / `symbols capture` / `POST /api/symbols/capture` 的返回值。

```json
{
  "font": "out/handnote-symbols.ttf",
  "glyphs": 202,
  "missing": [],
  "chars": "ABCDEFG...",
  "captured": 202,
  "size": 256,
  "merged_into": "/home/me/.cache/handnote/fonts/ZhiMangXing-Regular.ttf",
  "sets": 3,
  "alternates": 404,
  "captured_sets": 3,
  "merged_added": 103,
  "merged_alternates": 206
}
```

| 字段 | 含义 |
|---|---|
| `glyphs` | 成功描摹的字形数 |
| `missing` | 没写或描摹失败、未进入字体的字符 |
| `chars` | 成功字符，顺序与 spec 一致 |
| `sets` / `alternates` | 单个字符最多几套；变体字形总数（不含默认字形） |
| `captured_sets` / `scanned_sets` | 采集或扫描输入里有几套 |
| `merged_added` | 合并进基础字体时新增的码位；已有码位不计入 |
| `merged_alternates` | 随新增码位一起带进基础字体的变体数 |
| `font` | 最终产物；未合并时是纯符号字体 |

## 4. 字体合并规则

`merge_fonts(base, overlay, out)`：

- 只处理 TrueType（`glyf`）字体，CFF 会报错。
- 遍历 overlay 的 cmap，默认跳过 base 已覆盖的码位。
- `replace=True`（CLI `--replace`，API `"replace": true`）时，overlay 带的码位改指到手写字形：按旧字形的包围盒缩放（高度足够时按高度，扁平字符按宽度，比例钳在 0.4–2.5），垂直中心对齐、沿用旧字形的左右留白；旧字形不删除。报告里 `replaced_chars` 列出被替换的字符。
- CJK 表意字（U+3400–9FFF 等）不逐字缩放：取两边各自汉字墨迹框的中位数，用同一个缩放和垂直平移把手写字放进 base 的字身，水平居中于旧字形的步进。这样保留你写的字与字之间的大小差异。
- 复制 `glyf` 轮廓与 `hmtx` 度量，写入 base 的 cmap 所有 Unicode 子表。
- `post` 表降为 3.0（无字形名），避免名字表冲突。
- 家族名追加后缀（默认 `" Math"`），方便与 base 同时安装。

- 新增码位若在 overlay 的 `rand` 里有变体，变体字形一并复制，并在 base 的 GSUB 末尾追加 `rand` / `calt` lookup；base 原有的 lookup、特性、脚本不动，只是 FeatureList 按 tag 重排并同步各 LangSys 的索引。

因此合并是单向的：overlay 里没有的码位（包括没写也没拼出来的汉字）不受影响，重复执行结果一致。

## 5. 字体内的多套

| 名字 | 含义 |
|---|---|
| `uniXXXX` | 第一套，cmap 指向它 |
| `uniXXXX.sN` | 第 N+1 套（合并后可能改名，靠 GSUB 而不是名字识别） |
| `rand` | 一个 AlternateSubst：`base -> [base, .s1, .s2, ...]` |
| `calt` | 链式替换：前一个字是默认字形则当前字换 `.s1`，前一个是 `.s1` 则换 `.s2`……没有下一套时回到默认 |

渲染器通过 `rand` 读出各字符的变体（`opentype.read_alternates`），因此第三方带 `rand` 的字体也能直接用。

## 6. 手写汉字采集

`/hanzi` 页面“导出 JSON”的格式，也是 `python -m handnote hanzi plan|build` 的输入：

```json
{"version": 1, "kind": "hanzi", "width": 0.05,
 "chars": [{"char": "清", "set": 0,
            "strokes": [{"points": [[0.21, 0.18, 0.5], [0.24, 0.25, 0.5]]}]}]}
```

| 字段 | 含义 |
|---|---|
| `chars[].char` | 一个字 |
| `chars[].set` | 第几套，0–7，可省略；同一个字每套一条 |
| `strokes` | 按书写顺序排列；每笔是点数组，或 `{"points": [...], "width": 0.05}` |
| 点 | `[u, v]` 或 `[u, v, pressure]`，与符号采集相同的单格归一化坐标；压力规则同上 |

格子与 makemeahanzi 的 1024 字框一一对应：`u = x / 1024`，`v = (900 - y) / 1024`。描红就是按这个映射画的，所以照着描红写，第 i 笔就落在标准第 i 笔上。

**校验**（`hanzi.match_strokes`）：笔数必须等于标准笔数；书写和标准各自按外框归一化后，逐笔比较起点和终点，平均偏差超过 0.22 或单笔超过 0.5 判为笔顺不对。不合格的字整字使用，不拆部件。

**拆分与拼合**（`hanzi.harvest` / `hanzi.Composer`）：

- makemeahanzi 的 `matches` 给出每一笔属于 IDS 树的哪个节点，例如“清 = ⿰氵青”的前 3 笔属于 `[0]`（氵）。合格的字按节点切出部件实例，记下它在源字里的标准外框和位置（IDC + 第几个子节点）。
- 拼目标字时，对 IDS 的每个子节点取目标字里对应笔画的标准外框，挑一个实例，把它从源外框映射到目标外框：x、y 分别缩放，长宽比变化超过 1.4 倍的部分改为等比；缩放超出 0.35–2.5 的实例不用；笔宽不缩放。
- 优先位置相同的实例，其次外框长宽比、面积接近的；分数相近的实例按套号轮换，生成不同变体。
- 某个子节点没有实例时，用它自己的 IDS 再拆，最多 3 层。
- 目标字有笔画不属于任何子节点（标准数据里 `matches` 为 `null`），或任一子节点拼不出，就放弃，回退为基础字体。
- 结果只由采集数据决定，与 JSON 里的顺序无关。

**数据缓存**：`hanzi fetch` 把 `dictionary.txt` 与 `graphics.txt` 下载到 `~/.cache/handnote/hanzi/`，再压缩成 `hanzi-gb1.json`（GB2312 一级字及其用到的部件：IDS、`matches`、笔画中线）。

**构建报告**（`hanzi build` / `GET /api/hanzi/build/<id>` 的 `report`）：

| 字段 | 含义 |
|---|---|
| `glyphs` | 字体里的汉字数 |
| `written` / `composed` | 其中手写的、拼合的 |
| `alternates` | 变体字形数 |
| `unsplit` | 校验没通过的字及原因 |
| `merged_replaced` / `merged_added` | 合并时替换、新增的码位数 |
