# 冻结的参考分支

本分支 `feat/hanzi-components` 已冻结，**不再更新，也不要合并进 `main`**。

## 内容

手写汉字的旧方案：按偏旁拆分再拼合。

- `handnote/hanzi.py`：`match_strokes`（把手写笔画对到 makemeahanzi 标准笔画）、`harvest`（按 IDS 拆出部件实例）、`Composer`（用部件外框变换拼出没写过的字）、`recommend`（推荐下一批最划算的字）、`build_hanzi_font`。
- `tests/test_hanzi.py`，以及对应的 `webui/hanzi.*` 和 `webui/hanzi_api.py`。
- 分支上的压感、稿纸、.docx 导入三个提交与 `main` 内容相同，不是独有内容。

## 为什么冻结

写约 430 字就能覆盖 GB2312 一级，但部件只做外框变换，拼出的字生硬。`main` 已改用微调后的 SDT 生成模型（PR #1、#2），决策记录见 `main` 分支的 `MILESTONES.md`。

## 以后怎么用

只作参考。需要其中的代码（比如笔画匹配、部件拆分）时，从本分支手动挑选移植到基于最新 `main` 的新分支，不要直接合并：两套实现在 `hanzi_api.py`、`hanzi.js`、`cli.py` 和文档上互相冲突。
