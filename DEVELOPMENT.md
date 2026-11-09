# 开发说明

给想修改或扩展 handnote 的人看。使用方法见 [README.md](README.md)，数据格式见 [docs/formats.md](docs/formats.md)，更细的工程不变量见 [CLAUDE.md](CLAUDE.md)（AI 编程助手也读这份）。

## 环境

需要 Python 3.11 或更高版本。CI 在 3.11 / 3.12 / 3.13 上测试。

```bash
git clone https://github.com/zh271/handnote.git
cd handnote
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[test]" ruff
python -m handnote fonts fetch zhimang # 测试需要默认字体
```

日常命令：

```bash
python -m pytest -q                    # 全部测试，约 240 个
python -m pytest tests/test_render.py -q
ruff check handnote tests              # 必须干净
python -m handnote serve               # 改了 Python 代码要重启；改 webui 静态文件刷新页面即可
```

Linux 上 opencv 需要 `libglib2.0-0`（CI 里用 apt 安装）。

## 目录结构

```
handnote/
  config.py       RenderConfig：全部参数、类型转换、取值钳制
  markdown.py     Markdown 子集 -> Block；$..$ / $$..$$、矩阵提取、分页符
  coverage.py     LaTeX -> Unicode 映射，对比字体 cmap 报缺字
  render.py       Renderer：layout -> paginate -> render_page / render_vector
  vector.py       矢量页面记录与无依赖的 PDF 写出器
  variants.py     多套字形：确定性轮廓变形、SetPicker、按套小字体
  opentype.py     GSUB 读写：rand / calt 变体
  symbols.py      模板生成、cv2 描摹、assemble_font、merge_fonts
  capture.py      浏览器笔画 JSON 校验、光栅化、build_capture_font
  paper.py        稿纸：预设、行线网格 Grid、画线、扫描纸校正与行距检测
  fonts.py        字体缓存目录与下载源
  cli.py          命令行入口（python -m handnote）
  webui/          stdlib HTTP 服务 + 原生 JS 页面（无构建步骤）
skills/
  concise-answer/ 答题 agent 的 skill，输出受 handnote 渲染能力约束
examples/         可直接渲染的示例 Markdown
docs/formats.md   spec.json / capture.json / 构建报告字段
tests/            pytest；不联网，只用缓存字体
start.bat         Windows 一键启动脚本
```

## 架构

### 渲染链路

```
Markdown ──parse──▶ Block[] ──layout──▶ 行（测量宽度、断行）──paginate──▶ 页 ──render_page──┬▶ PIL Image ──▶ PNG / 位图 PDF
                                                                                          └▶ VectorPage ──▶ 矢量 PDF
```

- **layout** 只测量不绘制。正文按字符、行内公式按整体作为原子 token 断行。宽度只有两个来源：`Renderer.char_metrics`（单字）和 `Renderer.math_advance`（行内公式），绘制时用同样的函数，保证测量和绘制一致。
- **公式** 由 matplotlib mathtext 解析出逐字形的位置，再用对应套的轮廓逐个重画，所以 STIX 回退的符号也会被变形。矩阵不走 mathtext，由 `_compose_math` / `_matrix_image` 拼出单元格并手绘括号。
- **正文** 每个字单独绘制成小块，带旋转、位移、字号和墨色抖动后合成到纸张上。多套字形时，每套生成一份只含本页字符的小字体，仍由 FreeType 光栅化。
- **随机性** 全部来自以 `seed` 初始化的 `random.Random`。同一 `seed` 输出逐字节一致，这是测试断言的一部分。
- **矢量 PDF** 走同一个 `render_page`：`render_vector` 关掉光栅绘制（`_raster=False`），打开记录（`_rec`），`draw_char`、`_place_math`、`_bracket` 在原位置把同样的东西记成轮廓。随机数的消耗顺序和光栅完全相同，所以两种输出逐字对齐。
  - 正文字形的轮廓取自基础字体或该套的小字体（`GlyphSets.outline`），矩阵 = 平移 · 绕 `Image.rotate` 同一中心旋转 · 字号缩放。
  - 公式在 `_math_path` 里按字形拆成 `parts`，每个字形一个共享轮廓，另加与光栅一致的 1pt 描边。
  - `vector.py` 把每个不同的轮廓写成一个 Form XObject，页面里只放 `cm` 矩阵和引用，墨色深浅用 ExtGState。没有轮廓可用的极少数情况（公式解析失败的纯文本回退、字体缺字）嵌入为图片。
  - 改动绘制逻辑时，光栅和记录两边要一起改，并跑 `tests/test_vector.py`。

### 补字链路

```
symbols template ──▶ PDF + spec.json
                          │
          ┌───────────────┴───────────────┐
     纸笔 + 扫描                     浏览器笔画 JSON
   symbols.scan_sets             capture.normalize_sets
          └───────────────┬───────────────┘
                 每格灰度图 ──cv2 描摹──▶ 轮廓
                          │
             assemble_font（多套 → GSUB rand/calt）
                          │
             merge_fonts（只增；--replace 时重映射 cmap）
                          │
                     合并后的 TTF
```

- 扫描件先经 `register_page`：检测四角 ArUco 标记（id = 页号 × 4 + 角号），用单应矩阵拉正到模板像素坐标，并按标记确定页号；没有标记时按文件名顺序。
- 扫描件和浏览器笔画最终都变成同样的格子位图，走同一个描摹器。
- 格子几何（缩放、基线）在两条输入之间共享。改动会同时影响扫描件、浏览器笔画和已有字体，见 CLAUDE.md 不变量 1。
- 对齐：每格的手写墨迹框向描红字的墨迹框和基线靠拢，`keep` 控制保留多少原始偏差（`symbols.guide_box`、`_cell_glyph`）。
- 合并默认只补基础字体缺的码位。`replace=True` 只把手写过的码位指向手写字形，不删旧字形，并把手写字形缩放到旧字形的框里。
- 手写多套写成 `uniXXXX.sN` 字形 + `rand`（AlternateSubst）+ `calt`（ChainContextSubst）。合并后 `post` 表降为 3.0，字形名不可靠，渲染器只从 `rand` 读取变体。

### Web 服务

`handnote/webui/server.py` 基于 `http.server`，单进程，只监听 `127.0.0.1`。matplotlib 的 rcParams 是全局状态，渲染入口用模块级锁串行化。

| 路由 | 作用 |
|---|---|
| `GET /`、`GET /capture` | 渲染器页面、手写采集页 |
| `GET /static/<file>` | 页面的 JS / CSS |
| `GET /api/state` | 默认配置、字体列表 |
| `POST /api/render` | `{markdown, config}` → 预览图 URL、PDF URL、缺字报告 |
| `POST /api/analyze` | 只返回缺字报告 |
| `POST /api/upload?kind=font\|markdown` | 导入字体或 Markdown |
| `GET /api/preview/<key>/<i>.png`、`GET /api/pdf/<key>.pdf` | 渲染结果 |
| `GET /api/symbols/template.pdf`、`template.json` | 符号模板与 spec（`?sets=N` 多套） |
| `POST /api/symbols/build` | 上传扫描图 + spec，构建并可选合并、替换 |
| `GET /api/guide-font` | 采集页用的描红字体（按字符子集化） |
| `POST /api/symbols/capture` | 提交笔画 JSON，构建并可选合并、替换 |
| `GET /api/font-file/<name>` | 下载构建好的字体 |

| `POST /api/paper/scan`、`GET /api/paper/raw/<id>` | 上传纸张照片，返回自动识别的四角；角点编辑用的缩略图 |
| `POST /api/paper/apply`、`GET /api/paper/file/<name>` | 按四角校正、去阴影，存为 `paper-*.png`，并检测行距 |

稿纸相关路由在 `webui/paper_api.py`，`server.py` 只做转发。`/api/render` 只接受 `var/uploads/paper-*.png` 作为 `paper_image`。

上传和构建产物写到当前目录的 `var/uploads/`（已在 `.gitignore`）。

## 常见扩展

**新增一个渲染参数**

1. `config.py`：在 `RenderConfig` 加字段和默认值，在 `_LIMITS` 加取值范围；`from_mapping` 按字段的类型标注做转换。
2. 在 `render.py` 里使用它。影响宽度的参数必须放进 `char_metrics` 或 `math_advance`，否则断行和绘制会对不上。
3. Web 界面：`webui/index.html` 加滑块，`webui/app.js` 的 `NUMERIC` 列表加上字段名。
4. README 的参数表加一行；`tests/test_config.py` 或相关测试补断言。

**新增一个 API 路由**

在 `server.py` 的 `do_GET` / `do_POST` 分发里加分支，并在 `tests/test_webui.py` 加用例（测试会在端口 0 上起真实服务）。

**新增下载字体**

在 `fonts.py` 的 `SOURCES` 里加 `key: (文件名, URL)`，只收许可允许再分发的字体，并在 [CREDITS.md](CREDITS.md) 记录作者和许可。

**扩充符号模板**

模板字符分组在 `symbols.py`。`cells[].index` 是采集 JSON 和扫描件共用的编号，在中间插入字符会让已有的采集数据错位；新字符请追加到末尾，或同时写迁移（CLAUDE.md 不变量 7）。

**修改答题 skill**

`skills/concise-answer/SKILL.md` 里的公式白名单和示例由 `tests/test_answer_skill.py` 逐条实际渲染：白名单公式不能产生警告，示例里 `$` 外的字符必须在默认字体里。改完跑一遍：

```bash
python -m pytest tests/test_answer_skill.py -q
```

渲染器新支持了某个命令，再把它加进白名单；反之，渲染器去掉支持时这组测试会失败提醒。

## 测试约定

- 测试不联网，字体从缓存读；缓存缺失时 `conftest.py` 下载一次。
- 渲染相关断言用结构（页数、尺寸、确定性、缺字报告），不比对像素快照。
- 描摹质量用“墨量大于阈值”这类不脆弱的断言，不断言轮廓点数。
- 同一 `seed` 逐字节一致必须保持（`tests/test_render.py::test_deterministic_for_same_seed`）。
- 新增 API 路由必须加 `tests/test_webui.py` 用例。

## 代码风格

- `ruff check` 必须干净，配置在 `pyproject.toml`（行宽 100）。
- Google 风格 docstring：一行摘要，空行后展开。
- 公共函数带类型标注，内部辅助函数以 `_` 开头。
- 依赖保持精简：Web 层只用标准库，图像层只用 pillow / numpy / matplotlib / fonttools / opencv-headless。新增依赖前先讨论。
- 前端是原生 JS，不引入构建工具。

## 提交与发布

- 提交信息用 Conventional Commits，小写前缀：`feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`ci:`、`chore:`。
- 推到 `main` 和每个 Pull Request 都会跑 [CI](.github/workflows/tests.yml)：ruff + 三个 Python 版本的 pytest。
- 版本号在 `pyproject.toml` 和 `handnote/__init__.py` 两处，发布时同步修改。

## 进度与待办

- [MILESTONES.md](MILESTONES.md)：已完成的里程碑和设计决策记录。
- [TODO.md](TODO.md)：已知问题与计划，欢迎从这里挑任务。

| 能力 | 状态 | 测试 |
|---|---|---|
| Markdown → 手写页面（抖动、公式、矩阵、分页） | 完成 | `tests/test_render.py` |
| 矢量 PDF（与位图逐字对齐、字节确定） | 完成 | `tests/test_vector.py` |
| 字距、标点收紧、公式留白 | 完成 | `tests/test_spacing.py` |
| 缺字报告 | 完成 | `tests/test_render.py::test_coverage_flags_missing_symbol` |
| 符号模板、扫描 → TTF → 合并 | 完成 | `tests/test_symbols.py` |
| 浏览器笔画 → TTF | 完成 | `tests/test_capture.py` |
| 描红对齐、手写替换 | 完成 | `tests/test_align.py`、`tests/test_replace.py` |
| 合成多套、手写多套（`rand` / `calt`） | 完成 | `tests/test_variants.py`、`tests/test_sets.py` |
| 答题 skill 白名单可渲染 | 完成 | `tests/test_answer_skill.py` |
| Web 界面与 API | 完成 | `tests/test_webui.py` |
| 扫描件透视校正、自动页序 | 完成 | `tests/test_perspective.py` |
