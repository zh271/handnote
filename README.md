# handnote

[![tests](https://github.com/zh271/handnote/actions/workflows/tests.yml/badge.svg)](https://github.com/zh271/handnote/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](pyproject.toml)

把 Markdown 笔记排成手写页面，输出 PNG 或 PDF。

- **像手写**：每个字随机旋转、位移、深浅不一；同一个字反复出现时字形也不完全一样。
- **能写数学**：行内公式、居中公式、手绘括号的矩阵，和正文用同一种手写字体。
- **用你自己的笔迹**：在浏览器里（或打印纸上）把数学符号、数字、字母写一遍，生成字体后合并进中文手写体。
- **配合 AI 答题**：附带一份 agent skill，让 AI 只输出题号和精简推导，交给 handnote 直接排成手写答卷。
- **纯本地**：只用 CPU，不需要显卡和模型；附带本机 Web 界面，调参实时预览。

> 本文是使用说明。参与开发请看 [DEVELOPMENT.md](DEVELOPMENT.md)，用到的开源项目见 [CREDITS.md](CREDITS.md)。

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [Markdown 写法](#markdown-写法)
- [用自己的笔迹](#用自己的笔迹)
- [多套字形](#多套字形)
- [参数](#参数)
- [配合 AI 答题 agent](#配合-ai-答题-agent)
- [常见问题](#常见问题)
- [限制](#限制)
- [许可](#许可)

## 安装

需要 Python 3.11 或更高版本。

### Windows：一键启动

克隆或下载仓库后，双击根目录的 `start.bat`。它会依次创建虚拟环境、安装依赖、下载默认字体，然后打开 `http://127.0.0.1:8765/`。之后每次双击都会直接启动，`pyproject.toml` 变化时才重装依赖。

### 手动安装

```bash
git clone https://github.com/zh271/handnote.git
cd handnote
python -m venv .venv
```

激活虚拟环境（macOS / Linux）：

```bash
source .venv/bin/activate
```

激活虚拟环境（Windows PowerShell）：

```powershell
.venv\Scripts\Activate.ps1
```

安装并下载默认字体：

```bash
pip install -e .
python -m handnote fonts fetch zhimang
```

默认字体是志莽行书（Zhi Mang Xing，SIL OFL 1.1），下载到 `~/.cache/handnote/fonts/`（可用环境变量 `HANDNOTE_CACHE` 改位置）。仓库本身不含任何字体文件。也可以下载其他字体，或用 `--font` 指定本机任意 TTF/OTF：

| 名称 | 字体 | 说明 |
|---|---|---|
| `zhimang` | 志莽行书 | 默认，中文行书 |
| `mashanzheng` | 马善政毛笔楷书 | 中文楷书 |
| `noto` | Noto Sans SC | 非手写，用作对照 |
| `patrick` / `caveat` | Patrick Hand / Caveat | 只有拉丁字母 |
| `xkcd` | xkcd Script | 只有拉丁字母；CC BY-NC 3.0，**不可商用** |

```bash
python -m handnote fonts list
python -m handnote fonts fetch mashanzheng
```

## 快速上手

### 命令行

```bash
python -m handnote render examples/basics.md -o out/basics.pdf
python -m handnote render notes.md -o out/notes.png        # 只有一页时输出单张 PNG
python -m handnote render notes.md -o out/pages             # 每页一张 page-N.png
python -m handnote render notes.md -o out/a.pdf --set body_size=28 --set seed=3
```

`examples/` 里有三份可以直接渲染的示例。

渲染前想知道字体缺哪些字，用 `analyze`：

```bash
python -m handnote analyze notes.md
```

公式里字体没有的符号会被 matplotlib 静默换成印刷体（STIX），看起来不像手写；`analyze` 会把这些字列出来，补字方法见[用自己的笔迹](#用自己的笔迹)。

### Web 界面

```bash
python -m handnote serve            # http://127.0.0.1:8765/
```

界面分三栏：

| 栏 | 用途 |
|---|---|
| 内容 | 粘贴或导入 Markdown，右侧实时预览，可下载 PDF |
| 参数 | 字号、行距、字距、抖动、纸张等滑块；可切换字体，包括你合并出的手写字体 |
| 符号页 | 下载打印模板、上传扫描件构建字体 |

缺字报告会直接列在预览旁边，点一下就跳到符号页。浏览器手写采集页在 `http://127.0.0.1:8765/capture`。

服务只监听本机 `127.0.0.1`，没有登录鉴权，不要改成对外开放。上传和构建的文件保存在启动目录下的 `var/uploads/`。

## Markdown 写法

handnote 支持 Markdown 的一个子集：

| 写法 | 效果 |
|---|---|
| `#` `##` `###` | 三级标题 |
| `- 条目` / `1. 条目` | 项目符号 / 编号 |
| `$x^2$` | 行内公式 |
| `$$...$$`（写在同一行） | 居中公式 |
| `$$M=\begin{bmatrix}1&0\\0&1\end{bmatrix}$$` | 手绘括号矩阵；`pmatrix` 圆括号，`vmatrix` 竖线，`matrix` 无括号 |
| 单独一行的 `$\begin{bmatrix}...\end{bmatrix}$` | 同上，居中 |
| 正文中的 `$A=\begin{bmatrix}...\end{bmatrix}$` | 行内小矩阵，所在行自动加高 |
| `---` | 强制分页 |
| ` ``` ` 代码块 | 原样输出文字 |

- 相邻两行不空行会拼成一段；要分段请空一行。
- 公式由 matplotlib mathtext 排版，支持常见的上下标、分式、根式、希腊字母、求和积分等；不支持 `cases`、`aligned`、`array` 环境。
- 汉字和公式之间不用加空格，加了也会自动去掉；英文单词和公式之间的空格会保留。
- 矩阵行用 `\\` 分隔（被复制成单个 `\` 也能识别），单元格用 `&`。

## 用自己的笔迹

默认字体缺很多数学符号（`∈ ∪ ⊆ √ ∑ ∫` 等），数字和字母也未必合你心意（比如志莽行书的 0 中间有一道）。补字的流程是：把模板里的 202 个符号、数字、字母写一遍，handnote 描摹成字体，再合并进中文手写体。汉字不需要写。

### 方法一：浏览器手写（推荐）

1. 启动 `python -m handnote serve`，打开 `http://127.0.0.1:8765/capture`。
2. 用鼠标、触控笔或手指在每个格子里照着浅灰描红字写。写完一笔就自动保存在浏览器里，刷新也不会丢。顶栏可以调笔宽、撤销。
3. 点“构建字体”。默认勾选了：
   - **合并到默认字体**：把手写符号并进志莽行书，汉字保留。
   - **手写覆盖已有字符**：你写过的数字、字母替换掉字体原来的字形；没写的字符不变。
   - **保留书写误差**（滑块，默认 0.3）：每个字的大小和位置会向描红字对齐，只保留这个比例的个人偏差。0 为完全对齐，1 为原样。
4. 构建完成后点“在渲染器中使用”。以后在“参数”栏的字体下拉框里选“手写合并 月-日 时:分”即可。

想把笔迹带到别的电脑上，点“导出 JSON”，再用命令行构建：

```bash
python -m handnote symbols template -o out/math-symbols.pdf        # 同时生成 out/math-symbols.json
python -m handnote symbols capture capture.json -m out/math-symbols.json \
    -o out/merged.ttf --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf --replace
```

### 方法二：纸笔扫描

```bash
# 1. 生成 A4 模板（3 页，每格 24mm）和格子坐标 out/math-symbols.json
python -m handnote symbols template -o out/math-symbols.pdf

# 2. 打印，用黑色或深色笔照着浅灰字逐格写，扫描或用手机拍照
# 3. 描摹并合并
python -m handnote symbols build scan-1.png scan-2.png scan-3.png \
    -m out/math-symbols.json -o out/merged.ttf \
    --merge ~/.cache/handnote/fonts/ZhiMangXing-Regular.ttf --replace
```

- 描红字是浅灰色，扫描阈值会把它滤掉，所以必须用深色笔。
- 模板四角各有一个黑白方块（ArUco 定位标记）。构建时会找到它们，把倾斜、透视变形、上下颠倒、分辨率不同的扫描件或照片拉正，并按标记识别是哪一套的第几页，所以文件名和上传顺序都无所谓。
- 拍照时四个角的标记要完整入镜，至少两个能看清；光线均匀、不要有强烈阴影。
- 找不到标记的图片（比如用旧模板打印的）按文件名自然排序补到空缺的页上，不做校正，这时需要平放扫描。
- 不加 `--merge` 只生成符号字体；不加 `--replace` 只补基础字体缺的字，已有的数字字母保持原样。
- `--keep` 与网页上的“保留书写误差”相同。

Web 界面的“符号页”栏可以完成同样的操作：下载模板、上传扫描图、勾选合并与替换。

## 多套字形

真人写同一个字，每次都不一样。handnote 每画一个字就随机选一套字形，同一个字不会连续两次用同一套。字形来源有两种：

1. **手写多套**（优先）：同一张模板写几遍，每遍是一套。写过多套的字符就在这几套之间轮换，不做任何变形。
   - 浏览器：顶栏“当前套”切到第 2–8 套，每套分开保存，构建时一起提交。
   - 纸笔：`symbols template --sets 3` 打印 3 套（页眉标“第 k/3 套”），扫描后全部传给 `build`，按文件名自然排序，每 3 页算一套。
2. **合成多套**：其余字符（包括全部汉字）默认 4 套。第 0 套是原字形，其余几套是轻微、平滑的变形。

```bash
python -m handnote render notes.md -o out/a.pdf --set glyph_sets=6 --set glyph_strength=1.4
python -m handnote render notes.md -o out/a.pdf --set glyph_sets=1     # 关闭多套
```

手写多套会以 OpenType 变体写进构建出的字体，装进系统后在别的软件里也会轮换：

| 特性 | 生效的软件 | 效果 |
|---|---|---|
| `rand` | 基于 HarfBuzz 的软件（浏览器、LibreOffice 等），默认开启 | 每个字随机取一套 |
| `calt` | Word，需在“字体 → 高级”里勾选“上下文替换” | 连续出现的字依次轮换 |

## 参数

命令行用 `--set 字段=值` 覆盖，Web 界面里是滑块。em 指当前字号。

| 字段 | 默认 | 作用 |
|---|---|---|
| `body_size` / `line_height` | 25 / 1.82 | 正文字号（px）与行距倍数 |
| `char_spacing` | 0.12 | 汉字之间的字距（em）；英文字母只加四分之一，单词不会散开 |
| `digit_spacing` / `symbol_spacing` | 0.06 / 0.08 | 数字之间、符号之后的额外间距（em），公式内同样生效 |
| `punct_squeeze` | 1.0 | 全角标点收紧：1 让标点贴着前一个字，0 为字体原宽 |
| `math_gap` | 0.25 | 行内公式左右留白（em） |
| `jitter_rot` | 0.9 | 单字旋转幅度（度） |
| `jitter_dx` / `jitter_dy` | 0.03 / 0.035 | 单字位移幅度（em） |
| `size_wobble` | 1.0 | 字号随机幅度（px） |
| `ink_alpha_min` / `ink_alpha_max` | 238 / 255 | 墨色深浅范围 |
| `math_scale` / `display_size` | 1.12 / 36 | 行内公式相对正文的比例、居中公式字号 |
| `paper_noise` / `paper_gradient` | 1.6 / 3.0 | 纸张颗粒与纵向渐变 |
| `glyph_sets` / `glyph_strength` | 4 / 1.0 | 字形套数（1 为关闭）、合成套之间的差异幅度 |
| `seed` | 11 | 随机种子；同一种子、同一输入的输出逐字节一致 |
| `dpi` | 150 | PDF 的物理尺寸换算 |

完整字段与取值范围见 [`handnote/config.py`](handnote/config.py)。

## 配合 AI 答题 agent

[`skills/concise-answer/SKILL.md`](skills/concise-answer/SKILL.md) 是一份 agent skill。装上以后，把题目（文字或照片）发给 AI，它只输出题号和最精简的推导，格式和公式都限制在 handnote 能渲染的范围内，可以直接排成手写答卷。

它规定了：

- 照片里有多道题而你没说做哪几题时，先只回一行“图中有 3、4、5 题，答哪几题？”。
- 题号照抄原题，不重新编号；看不清的题写“题干不全”，不猜。
- 只写题号和作答，不写思路和客套话；计算题保留每一步“式子 ⇒ 结果”。
- 公式只用一份白名单里的写法，避开 handnote 不支持的命令（如 `\le`、`cases`）。

### 安装到 Claude Code

skill 是一个文件夹，放进 Claude Code 的 skills 目录即可。

只给自己用（所有项目都生效），macOS / Linux：

```bash
mkdir -p ~/.claude/skills
cp -r skills/concise-answer ~/.claude/skills/
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse skills\concise-answer "$HOME\.claude\skills\"
```

只在某个项目里用：复制到那个项目的 `.claude/skills/concise-answer/`。

装好后新开一个会话。发题目时 Claude 会自动套用这份 skill，也可以输入 `/concise-answer` 手动调用。想跟随仓库更新，可以用符号链接代替复制。

### 安装到 Claude 网页版 / 桌面版

1. 把 `skills/concise-answer` 文件夹压缩成 zip，zip 根目录就是这个文件夹。
2. 在 Claude 的设置里找到 Skills（位于“功能 / Capabilities”一类的页面），上传这个 zip 并启用。
3. 新开对话，直接发题目。

### 用在其他 agent

其他 AI 工具（ChatGPT、Cursor、Codex 等）不认识 skill 格式时，把 `SKILL.md` 正文（去掉开头 `---` 之间的几行）粘贴到系统提示词、自定义指令或 `AGENTS.md` 里，效果相同。

### 从答案到手写答卷

1. 把题目发给装好 skill 的 agent，得到 Markdown 答案。
2. 保存成 `answer.md`，或者直接粘贴进 Web 界面的“内容”栏。
3. 渲染：

```bash
python -m handnote render answer.md -o out/answer.pdf
```

先按[用自己的笔迹](#用自己的笔迹)构建好手写字体，并在渲染时选用它，答卷里的数字和符号就是你自己的字。

如果你修改了 skill 里的公式白名单，运行 `python -m pytest tests/test_answer_skill.py`，它会把白名单里的每条公式和每个示例都实际渲染一遍，确认没有渲染失败或缺字。

## 常见问题

**公式里有个符号像印刷体，不像手写。**
字体缺这个符号，matplotlib 换成了 STIX。用 `analyze` 或 Web 界面的缺字报告找到它，再按[用自己的笔迹](#用自己的笔迹)补上。

**我写了手写数字，但渲染出来还是原来的字形。**
构建时要勾选“手写覆盖已有字符”（命令行 `--replace`），并且渲染时选用新构建的合并字体。如果更新过代码，记得重启 `serve`。

**字太挤 / 标点离字太远 / 公式两边空太多。**
分别调 `char_spacing`、`punct_squeeze`、`math_gap`。

**整条公式变成了原样的源码文字。**
mathtext 解析失败，通常是用了不支持的命令（`\le`、`\iff`、`cases`、`\boxed` 等）。可用的写法参考 [SKILL.md 的白名单](skills/concise-answer/SKILL.md#4-公式白名单)。

**每次渲染结果都一样。**
这是有意的：同一 `seed` 输出逐字节一致。想换一种“笔迹”，改 `seed`。

## 限制

- 输出是位图（PNG，或由位图组成的 PDF），不是可编辑文字；放大后有锯齿。
- 手写多套只覆盖写过的字符；汉字只有合成的多套。
- 透视校正只处理平面变形；纸张弯曲、褶皱无法拉平。
- 公式不支持 `cases`、`aligned`、`array` 等环境。
- Word 里的公式（OMML）不经过 handnote，无法带上手写抖动。

更多计划见 [TODO.md](TODO.md)。

## 文档

| 文件 | 内容 |
|---|---|
| [DEVELOPMENT.md](DEVELOPMENT.md) | 开发说明：环境、架构、测试、扩展方式 |
| [docs/formats.md](docs/formats.md) | 模板 spec、采集 JSON、构建报告的字段定义 |
| [CREDITS.md](CREDITS.md) | 依赖的开源项目、字体及其作者与许可 |
| [CLAUDE.md](CLAUDE.md) | 给 AI 编程助手的工程约定与不变量 |
| [MILESTONES.md](MILESTONES.md) / [TODO.md](TODO.md) | 进度、设计决策与待办 |

## 许可

代码使用 MIT 许可，见 [LICENSE](LICENSE)。

字体不在仓库里，使用时从上游下载，各自遵循原许可（见 [CREDITS.md](CREDITS.md)）：

- 只含你手写符号的字体（不加 `--merge`），字形来自你自己的笔迹。
- 合并字体（`--merge`）包含基础字体的字形，属于该字体的修改版。以志莽行书为基础时，**分发**合并字体须继续遵循 SIL Open Font License 1.1：附上许可，且不能单独出售字体。自己用来渲染图片不受影响。
- `xkcd` 字体为 CC BY-NC 3.0，不可用于商业用途。
