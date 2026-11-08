# 数据格式

三份 JSON 串起整条链路：`spec.json` 描述模板几何，`capture.json` 记录笔画，构建报告描述产物。三者的 `index` 必须一致。

## 1. 模板 spec

由 `python -m handnote symbols template` 生成，与 PDF 同目录。

```json
{
  "version": 1,
  "dpi": 300,
  "cell_px": 283,
  "cell_mm": 24.0,
  "cols": 7,
  "rows": 10,
  "pages": 3,
  "page_size_px": [2480, 3508],
  "origin_px": [293, 318],
  "guide_missing": [],
  "cells": [
    {"index": 0, "page": 0, "row": 0, "col": 0,
     "char": "A", "code": 65, "bbox": [293, 318, 576, 601]}
  ]
}
```

| 字段 | 含义 |
|---|---|
| `dpi` | PDF 物理尺寸的基准，`page_size_px / dpi * 25.4` 等于毫米 |
| `cell_px` / `cell_mm` | 格子边长，像素与毫米两种写法 |
| `page_size_px` | 整页像素尺寸，A4 在 300dpi 下为 2480×3508 |
| `origin_px` | 网格左上角，`[x, y]` |
| `guide_missing` | 描红字体缺的字符；非空说明模板里有空格子 |
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
    {"index": 12,
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
| `strokes` | array | 一笔一个数组，最多 64 笔/格 |
| 点 | `[u, v]` 或 `[u, v, pressure]` | `u, v ∈ [0,1]`，原点在格子左上角，y 向下；`pressure ∈ [0,1]` 缺省 1 |

服务端处理：

1. 每格按 `size`（默认 256）光栅化，线宽 `width * size * (0.7 + 0.6 * 平均压力)`。
2. 圆头圆角，端点补圆点。
3. 送入 `_cell_glyph()`，与扫描件共用描摹与平滑。

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
  "merged_added": 103
}
```

| 字段 | 含义 |
|---|---|
| `glyphs` | 成功描摹的字形数 |
| `missing` | 没写或描摹失败、未进入字体的字符 |
| `chars` | 成功字符，顺序与 spec 一致 |
| `merged_added` | 合并进基础字体时新增的码位；已有码位不计入 |
| `font` | 最终产物；未合并时是纯符号字体 |

## 4. 字体合并规则

`merge_fonts(base, overlay, out)`：

- 只处理 TrueType（`glyf`）字体，CFF 会报错。
- 遍历 overlay 的 cmap，跳过 base 已覆盖的码位。
- 复制 `glyf` 轮廓与 `hmtx` 度量，写入 base 的 cmap 所有 Unicode 子表。
- `post` 表降为 3.0（无字形名），避免名字表冲突。
- 家族名追加后缀（默认 `" Math"`），方便与 base 同时安装。

因此合并是单向的：符号不会覆盖汉字，重复执行结果一致。
