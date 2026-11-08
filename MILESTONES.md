# Milestones

| # | 内容 | 状态 | 证据 |
|---|---|---|---|
| M1 | 渲染核心：Markdown 解析、逐字抖动、纸张、分页 | [VERIFIED] | `tests/test_markdown.py`, `tests/test_render.py` |
| M2 | 字形覆盖：LaTeX → Unicode 映射，缺字报告 | [VERIFIED] | `tests/test_render.py::test_coverage_flags_missing_symbol` |
| M3 | 符号页：模板 PDF + spec.json，扫描描摹，字体合并 | [VERIFIED] | `tests/test_symbols.py` |
| M4 | Web UI：参数面板、实时预览、导入与符号页工作流 | [VERIFIED] | `tests/test_webui.py` |
| M5 | 字形变体：高频字多字形 + OpenType `rand`/`calt` | [PLANNED] | TODO.md |
| M6 | 扫描鲁棒性：透视校正、自动页序识别 | [PLANNED] | TODO.md |
| M7 | 浏览器手写采集：笔画 schema、光栅化、采集页 | [VERIFIED] | `tests/test_capture.py`, `tests/test_webui.py::test_capture_endpoint` |

## 决策记录

- 输出位图而非字体。逐字抖动依赖渲染期随机器，字体格式只能编码固定字形；两者目标不同，先做渲染器。
- 符号补字走"打印模板 + 手写 + 扫描"，不用 AI 补字。202 个符号手写约 30 分钟，AI 方案需要 GPU 与逐字目检。
- 合并字体时保留基础字体全部字形，只补缺失码位；冲突时保留原字形，避免覆盖汉字。
- 模板采用固定网格与 JSON 几何，而不是二维码定位。扫描件由同一模板生成，坐标可直接复用；倾斜校正留到 M6。
- 采集用 cell-local 归一化坐标而不是页面像素。页面缩放、设备像素比、屏幕尺寸都不影响生成结果，同一份 JSON 可在 128–1024px 任意分辨率光栅化。
