# Milestones

| # | 内容 | 状态 | 证据 |
|---|---|---|---|
| M1 | 渲染核心：Markdown 解析、逐字抖动、纸张、分页 | [VERIFIED] | `tests/test_markdown.py`, `tests/test_render.py` |
| M2 | 字形覆盖：LaTeX → Unicode 映射，缺字报告 | [VERIFIED] | `tests/test_render.py::test_coverage_flags_missing_symbol` |
| M3 | 符号页：模板 PDF + spec.json，扫描描摹，字体合并 | [VERIFIED] | `tests/test_symbols.py` |
| M4 | Web UI：参数面板、实时预览、导入与符号页工作流 | [VERIFIED] | `tests/test_webui.py` |
| M5a | 多套字形：渲染期合成 N 套，公式逐字形、正文按套小字体 | [VERIFIED] | `tests/test_variants.py`, `tests/test_render.py` |
| M5b | 手写多套：采集页 / 多份扫描 → 字体变体 + OpenType `rand`/`calt` → 渲染 | [VERIFIED] | `tests/test_sets.py` |
| M6 | 扫描鲁棒性：透视校正、自动页序识别 | [VERIFIED] | `tests/test_perspective.py` |
| M7 | 浏览器手写采集：笔画 schema、光栅化、采集页 | [VERIFIED] | `tests/test_capture.py`, `tests/test_webui.py::test_capture_endpoint` |
| M8 | 手写汉字：写 60–100 字 → 微调 SDT → 生成 GB2312 字 → 字体 | [VERIFIED] | `tests/test_model.py`, `tests/test_model_numpy.py`, `tests/test_hanzi_font.py`, `tests/test_webui.py::test_hanzi_model_jobs` |
| M9 | v2 调研：DiffBrush 整行生成原型（英文，CLI + 测速） | [IN PROGRESS] | `tests/test_linegen.py`, `tests/test_linegen_numpy.py` |

## 版本

| 版本 | 内容 | 提交 |
|---|---|---|
| v1.0.0 | 手写汉字按偏旁拆分再拼合（冻结分支 `feat/hanzi-components`） | `fc6555d` |
| v1.1.0 | 手写汉字改用微调 SDT 生成（`main`，PR #5） | `57007be` |
| v2（开发中） | 调研整行生成（DiffBrush），决定是否替换字体链路 | `2.0.0.dev0` |

## 决策记录

- 输出位图而非字体。逐字抖动依赖渲染期随机器，字体格式只能编码固定字形；两者目标不同，先做渲染器。
- 符号补字走"打印模板 + 手写 + 扫描"，不用 AI 补字。202 个符号手写约 30 分钟，AI 方案需要 GPU 与逐字目检。
- 合并字体时保留基础字体全部字形，默认只补缺失码位；冲突时保留原字形，避免覆盖汉字。后来加了可选的 `replace`：只重映射手写过的码位（模板不含汉字），并按旧字形的框对齐，解决基础字体数字、字母不合心意（如带斜线的 0）的问题。
- 模板采用固定网格与 JSON 几何，而不是二维码定位。扫描件由同一模板生成，坐标可直接复用；倾斜校正留到 M6。
- M6 用 ArUco 标记（参考 OMRChecker 的四角定位块思路）而不是找纸张外轮廓：背景杂乱、纸边被裁时仍可用，标记编号顺带给出页序，且 opencv 已内置，不加依赖。两个及以上标记即可求单应矩阵（RANSAC），只有一个时只识别页号。
- 多套字形先在渲染期合成，不写进字体。mathtext 只能用一个字体家族，无法逐字选 OpenType 变体；逐字形替换 matplotlib 轮廓是唯一能让公式里重复符号不同的办法，正文复用同一套变形参数，保证同一套在公式和正文里一致。
- 手写多套以 OpenType 变体写进字体，而不是拆成多个字体文件：一份 TTF 既能给渲染器用，装进系统后在 HarfBuzz（`rand`）和 Word（`calt`）里也会轮换。渲染器读 `rand` 而不认字形名，因为合并后 `post` 降为 3.0，名字不保留。
- 采集用 cell-local 归一化坐标而不是页面像素。页面缩放、设备像素比、屏幕尺寸都不影响生成结果，同一份 JSON 可在 128–1024px 任意分辨率光栅化。
- 手写汉字先试了"按偏旁拆分再拼合"（分支 `feat/hanzi-components`，冻结留作参考）：写约 430 字就能覆盖 GB2312 一级，但部件只做外框变换，拼出的字生硬。改用生成模型 SDT（CVPR 2023，MIT 代码，CASIA-OLHWDB 预训练）。预训练模型直接用效果差：用户的工整楷书被写成潦草连笔，换预处理、换样本数都无效。用用户自己的笔画轨迹在 CPU 上微调约 300 步后，没写过的字也工整、结构正确、风格接近本人。Inko 等在线服务不考虑：闭源、收费、强制加 AI 标识，与本地优先冲突。
- 模型是可选依赖，权重运行时下载、不分发：CASIA 数据只限研究用途。核心渲染与补字仍然不需要任何模型。
- v2 先做 DiffBrush 原型而不是直接替换：公开的只有英文 IAM 权重，没有中文权重和训练代码；输出是整行图片，接不进"字体 + 逐字排版"。原型只回答三件事：效果（会不会写错字、像不像）、速度（CPU / RX 9070 XT / RTX 5060）、中文要多少代价（自己补训练代码 + 数据 + 算力）。现有 SDT 链路不动。
- 原型实测：4 线程 CPU 50 步每行约 17 秒，风格模仿明显好于逐字拼排，但偶尔漏字母、重复单词；短于约 35 字符的行会被重复内容填满（训练时短行被拼接到 512px 以上），所以按长度均衡换行。风格编码只在每行开始算一次（与时间步无关），上游每步都重算。
