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
- 复制 `glyf` 轮廓与 `hmtx` 度量，写入 base 的 cmap 所有 Unicode 子表。
- `post` 表降为 3.0（无字形名），避免名字表冲突。
- 家族名追加后缀（默认 `" Math"`），方便与 base 同时安装。

- 新增码位若在 overlay 的 `rand` 里有变体，变体字形一并复制，并在 base 的 GSUB 末尾追加 `rand` / `calt` lookup；base 原有的 lookup、特性、脚本不动，只是 FeatureList 按 tag 重排并同步各 LangSys 的索引。

因此合并是单向的：符号不会覆盖汉字，重复执行结果一致。

## 5. 字体内的多套

| 名字 | 含义 |
|---|---|
| `uniXXXX` | 第一套，cmap 指向它 |
| `uniXXXX.sN` | 第 N+1 套（合并后可能改名，靠 GSUB 而不是名字识别） |
| `rand` | 一个 AlternateSubst：`base -> [base, .s1, .s2, ...]` |
| `calt` | 链式替换：前一个字是默认字形则当前字换 `.s1`，前一个是 `.s1` 则换 `.s2`……没有下一套时回到默认 |

渲染器通过 `rand` 读出各字符的变体（`opentype.read_alternates`），因此第三方带 `rand` 的字体也能直接用。

## 6. 手写汉字

### 采集 JSON

`/hanzi` 页面“导出 JSON”的格式，也是 `hanzi train`、`hanzi generate`、`hanzi build` 的输入；`hanzi generate` 输出同样的格式：

```json
{"version": 1, "kind": "hanzi", "width": 0.05,
 "chars": [{"char": "清", "set": 0,
            "strokes": [{"points": [[0.21, 0.18, 0.5], [0.24, 0.25, 0.5]]}]}]}
```

| 字段 | 含义 |
|---|---|
| `chars[].char` | 一个字 |
| `chars[].set` | 第几套，0–7，可省略；同一个字每套一条。构建时第 0 套是默认字形，其余写成 `rand` 变体 |
| `strokes` | 按书写顺序排列；每笔是点数组，或 `{"points": [...], "width": 0.05}` |
| 点 | `[u, v]` 或 `[u, v, pressure]`，与符号采集相同的单格归一化坐标；压力规则同第 2 节 |

格子与 makemeahanzi 的 1024 字框一一对应：`u = x / 1024`，`v = (900 - y) / 1024`。描红、笔顺校验和生成字的摆放都用这个映射。

**校验**（`hanzi_font.match_strokes`，只用于页面提示）：笔数等于标准笔数；书写和标准各自按外框归一化后逐笔比较起点和终点，平均偏差超过 0.22 或单笔超过 0.5 判为笔顺不对。不合格的字照样参与训练。

### 模型

模型文件放在 `~/.cache/handnote/sdt/`：

| 文件 | 内容 |
|---|---|
| `checkpoint-iter199999.pth` | SDT 作者发布的中文权重，sha256 校验 |
| `content.npz` + `chars.txt` | GB2312 6763 字的 64×64 标准字形（模型的“写哪个字”输入），从作者数据集 zip 里只读出两个成员，用只允许 numpy 数组的 unpickler 转存 |
| `models/<采集哈希>.pth` + `.json` | 网页训练出的模型；`.json` 记录训练日志、写了多少字和压感模型 |
| `models/<采集哈希>-partial.json` | 生成的进度缓存：每个 `套号:字` 的原始笔画和少几笔，中断后续跑 |

**训练**（`model.train.finetune`）：每个写过的字转成 SDT 的笔迹序列：按墨迹中心和散布归一化，每笔做 RDP 简化（容差 0.06），得到相对位移和落笔、抬笔、结束三种状态。风格图是等宽细线、贴左上角的 64×64 图。只用笔迹损失；内容编码器冻结；留出 8 个字算验证损失；默认 300 步，学习率 5e-5，批大小 12，并加轻微的缩放、错切、旋转。

**生成**（`model.generate.generate`）：第 k 套用按 k 选出的 15 张风格图，逐点取最可能的输出，所以结果是确定的。笔数比标准少的字换一组风格图重写，最多 3 次，取最接近的一次；最终少 1 笔接受（通常是连写），少 2 笔以上不收入。笔画平滑后按标准字形外框摆放：中心对齐，面积相等，再乘书写大小系数（写过的字与标准外框面积比的平方根的中位数），不出格。

**压感**（`model.pressure`）：

- 采集里有压力，并且标准差不小于 0.02 时，按弧长每 0.012 重采样，用岭回归拟合压力。特征包括：笔画位置的帽函数（节点 0、0.1、0.25、0.5、0.75、0.9、1）、拐角、整笔方向（8 向）、笔长。
- 没有压力时用通用规则：按模型原始点距估计速度，`clamp(0.8 - 0.3·相对点距, 0.2, 0.8)`，起笔和收笔渐轻。
- 生成报告的 `pressure` 字段写明用的是哪一种（`learned` / `rule`）。

**生成报告**：

| 字段 | 含义 |
|---|---|
| `generated` / `written` | 模型写的字数、你亲手写的字数 |
| `skipped` | 少 2 笔以上、没收入的字 |
| `one_short` | 少 1 笔、已收入的字数 |
| `unsupported` | 不在 SDT 字表（GB2312 6763 字）里的字 |
| `pressure` | `learned` 或 `rule` |
| `writer_scale` | 书写大小系数 |
