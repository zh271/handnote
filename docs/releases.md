# 发布说明

每个版本对应一个 `vX.Y.Z` 标签和一个 GitHub Release，正文取自本文件。

## v2（开发中，`2.0.0.dev0`）

调研用 [DiffBrush](https://github.com/dailenson/DiffBrush)（ICCV 2025）整行生成替换现有手写方案。第一步是可选的推理原型（`python -m handnote line …`，只支持英文），不改变 v1.1.0 的任何功能。

## v1.1.0：手写汉字改用 SDT 模型

标签打在 `main` 的 `57007be`（PR #5 合并）。

- **手写汉字**：在 `/hanzi` 页写 60–100 字，在 CPU 上微调 [SDT](https://github.com/dailenson/SDT)（CVPR 2023）约 300 步，生成其余汉字（可到 GB2312 一级 3755 字），再描摹成字体合并进中文手写体。少笔画的字自动重写；压感从书写者的笔迹学习。命令行：`hanzi model-fetch / train / generate / build`。
- 导入 Word `.docx`：标题、编号、分页、软换行，公式（OMML）转 LaTeX。
- 稿纸：横线、方格、作文格、田字格等预设，文字对齐行线，可选一字一格；扫描自己的纸（四角校正、去阴影、测行距）。
- 压感：笔画宽度逐点随压力变化。
- 矢量 PDF 输出。
- Windows 一键启动脚本 `start.bat`，启动时自动快进更新。

模型是可选依赖（`pip install -e ".[model]"`）。SDT 权重用仅限研究用途的 CASIA-OLHWDB 训练，运行时下载、不随仓库分发；生成的字体请只作个人使用。

## v1.0.0：手写汉字按偏旁拼合（已冻结）

标签打在 `feat/hanzi-components` 的 `fc6555d`。这条分支不再更新，只留作参考。

- **手写汉字**：写约 430 个字覆盖 GB2312 一级常用部件，按偏旁拆分再拼合出其余汉字（`hanzi.py`：笔画匹配、部件采集、`Composer`、推荐写字顺序）。部件只做外框变换，拼出的字比较生硬，v1.1.0 改用 SDT 生成。
- 与 v1.1.0 共有：Markdown → 手写页面（逐字抖动、公式、矩阵、分页）、符号模板扫描与浏览器手写补字、多套字形与 OpenType 变体、透视校正、导入 Word、稿纸、压感、矢量 PDF。
