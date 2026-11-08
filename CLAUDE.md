# handnote — development notes

给接手开发的 agent 或人看的工程说明。用户文档在 `README.md`，数据格式在 `docs/formats.md`。

## 项目是什么

两条链路，共用一套几何：

1. **渲染**：Markdown → 逐字抖动的手写页面 → PNG/PDF。
2. **补字**：符号模板 PDF →（手写扫描 | 浏览器笔画）→ 描摹成 TTF → 合并进中文字体，补掉 mathtext 回退。

纯 CPU，无模型权重，无 GPU 依赖。Python ≥ 3.11，依赖 pillow / numpy / matplotlib / fonttools / opencv-headless。

## 常用命令

```bash
pip install -e ".[test]"
python -m handnote fonts fetch zhimang          # 下载默认字体（OFL）
python -m handnote render examples/math-symbols.md -o out/demo.pdf
python -m handnote analyze examples/math-symbols.md
python -m handnote serve                         # Web UI :8765
python -m pytest -q
ruff check handnote tests
```

## 模块地图

| 文件 | 职责 |
|---|---|
| `handnote/config.py` | `RenderConfig`，所有可调参数；`from_mapping` 做类型转换、钳制与校验 |
| `handnote/markdown.py` | Markdown 子集 → `Block` 列表；处理 `$..$`、`$$..$$`、矩阵提取、分页 |
| `handnote/coverage.py` | LaTeX → Unicode 映射；对比字体 cmap 报缺字 |
| `handnote/render.py` | `Renderer`：layout（测量）→ paginate（分页）→ render_page（绘制） |
| `handnote/symbols.py` | 模板生成、cv2 描摹、`assemble_font`、`merge_fonts` |
| `handnote/capture.py` | 笔画 JSON 校验、光栅化、`build_capture_font` |
| `handnote/fonts.py` | 字体缓存目录与下载 |
| `handnote/cli.py` | 命令行入口 |
| `handnote/webui/server.py` | stdlib HTTP 服务与 JSON API |
| `handnote/webui/*.html/js/css` | 渲染器界面 + 采集页（原生 JS，无构建步骤） |

## 不变量（改代码前先读）

1. **格子几何**。每格按 `upem / max(h, w)` 缩放；`x` 以格心映射到 `upem/2`，`y` 以 `0.9h` 为基线映射到 0。改动这里会同时影响扫描件、浏览器笔画、已有字体，必须先跑 `tests/test_symbols.py` 与 `tests/test_capture.py`。
2. **渲染确定性**。同一 `seed` 必须逐字节一致（`tests/test_render.py::test_deterministic_for_same_seed`）。不要在渲染路径里引入 `random` 之外的时间/哈希随机源。
3. **matplotlib rcParams 是全局状态**。`Renderer._apply_mathtext()` 在每次 `render()` 内调用，并用模块级 `_LOCK` 串行化。新增渲染入口时也必须拿锁。
4. **字体合并只增不改**。`merge_fonts` 跳过基础字体已有的码位，冲突时保留原字形；不要改成覆盖，否则会破坏汉字。
5. **缺字是静默的**。mathtext 缺字形会回退 STIX 而不报错，唯一可靠信号是 `coverage.analyze()`；渲染后把 `renderer.warnings` 透传给调用方。
6. **数字格式稳定**。`spec.json` 的 `cells[].index` 与 `capture.json` 的 `index` 是同一套编号，改动需同步迁移脚本。

## 测试约定

- 测试不联网：`tests/conftest.py` 用缓存字体，缺失时下载一次。
- 涉及渲染的断言优先用结构（页数、尺寸、确定性、覆盖率），不要比对像素快照。
- 描摹质量用"墨量 > 阈值"这类低脆弱断言；不要断言具体轮廓点数。
- 新增 API 路由必须同时加 `tests/test_webui.py` 用例（服务在测试内起在端口 0）。

## 代码风格

- Google 风格 docstring，一行摘要，空行后展开。
- 公共函数带类型标注；内部辅助函数前缀 `_`。
- 不加不必要依赖：Web 层坚持 stdlib，图像层只用已列出的五个包。
- `ruff check` 必须干净（配置在 `pyproject.toml`）。

## 已知限制

见 `TODO.md`。影响面最大的三个：单字形无变体、扫描件无透视校正、公式不支持 `cases/aligned`。
