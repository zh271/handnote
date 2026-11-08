# 致谢与第三方许可

handnote 本身的代码采用 [MIT 许可](LICENSE)。下面列出它用到的开源项目和字体，感谢这些项目的作者和贡献者。仓库里不包含任何第三方代码或字体文件：Python 依赖由 pip 安装，字体在首次使用时从上游下载。

## Python 依赖

运行时依赖，见 `pyproject.toml`：

| 项目 | 用途 | 作者 | 许可 | 地址 |
|---|---|---|---|---|
| Pillow | 图像绘制、字体光栅化、PDF 输出 | Jeffrey A. Clark (Alex) 及贡献者；源自 Fredrik Lundh 的 PIL | MIT-CMU | <https://github.com/python-pillow/Pillow> |
| NumPy | 位图与轮廓的数值计算 | Travis E. Oliphant 及 NumPy 开发者 | BSD-3-Clause | <https://github.com/numpy/numpy> |
| Matplotlib | mathtext 公式排版与字形路径 | John D. Hunter、Michael Droettboom 及 Matplotlib 开发团队 | Matplotlib License（基于 PSF） | <https://github.com/matplotlib/matplotlib> |
| fontTools | TTF 读写、字体合并、OpenType GSUB 变体 | Just van Rossum、Behdad Esfahbod、Cosimo Lupo 及贡献者 | MIT | <https://github.com/fonttools/fonttools> |
| OpenCV | 扫描件与笔画的二值化、轮廓描摹 | OpenCV 团队 | Apache-2.0 | <https://github.com/opencv/opencv> |
| opencv-python-headless | OpenCV 的 Python wheel 打包 | Olli-Pekka Heinisuo 及贡献者 | MIT（打包脚本）/ Apache-2.0（OpenCV） | <https://github.com/opencv/opencv-python> |

Pillow 和 Matplotlib 的 wheel 内置了 [FreeType](https://freetype.org/)（David Turner、Robert Wilhelm、Werner Lemberg，FreeType License / GPLv2），handnote 的字形光栅化依赖它。

开发与测试工具：

| 项目 | 用途 | 作者 | 许可 | 地址 |
|---|---|---|---|---|
| pytest | 测试框架 | Holger Krekel 及 pytest-dev | MIT | <https://github.com/pytest-dev/pytest> |
| Ruff | 代码检查 | Astral | MIT | <https://github.com/astral-sh/ruff> |
| actions/checkout、actions/setup-python | GitHub Actions CI | GitHub | MIT | <https://github.com/actions/checkout>、<https://github.com/actions/setup-python> |

## 字体

`python -m handnote fonts fetch <名称>` 从下列地址下载到本机缓存，不随仓库分发：

| 名称 | 字体 | 设计者 | 许可 | 地址 |
|---|---|---|---|---|
| `zhimang`（默认） | 志莽行书 Zhi Mang Xing | Wei Zhimang | SIL OFL 1.1 | <https://fonts.google.com/specimen/Zhi+Mang+Xing>、<https://github.com/google/fonts/tree/main/ofl/zhimangxing> |
| `mashanzheng` | 马善政毛笔楷书 Ma Shan Zheng | Ma ShanZheng | SIL OFL 1.1 | <https://fonts.google.com/specimen/Ma+Shan+Zheng>、<https://github.com/googlefonts/mashanzheng> |
| `patrick` | Patrick Hand | Patrick Wagesreiter | SIL OFL 1.1 | <https://fonts.google.com/specimen/Patrick+Hand>、<https://github.com/googlefonts/patrickhand> |
| `caveat` | Caveat | Impallari Type | SIL OFL 1.1 | <https://fonts.google.com/specimen/Caveat>、<https://github.com/googlefonts/caveat> |
| `noto` | Noto Sans SC（思源黑体） | Google 与 Adobe | SIL OFL 1.1 | <https://github.com/notofonts/noto-cjk> |
| `xkcd` | xkcd Script | 由 Randall Munroe 的手写样本生成，ipython/xkcd-font 项目维护 | **CC BY-NC 3.0（不可商用）** | <https://github.com/ipython/xkcd-font> |

随 Matplotlib 安装、被 handnote 间接使用的字体：

| 字体 | 用途 | 版权方 | 许可 | 地址 |
|---|---|---|---|---|
| STIX | 手写字体缺符号时 mathtext 的回退字形；handnote 会把它变形后再画 | STI Pub Companies | STIX Font License | <https://www.stixfonts.org/> |
| DejaVu Sans | 测试中模拟“手写”填格、采集页描红字 | Bitstream（Vera）与 DejaVu 贡献者 | Bitstream Vera License，DejaVu 修改部分为公有领域 | <https://dejavu-fonts.github.io/> |

## 生成的字体

- 不加 `--merge` 构建出的符号字体，字形全部来自你自己的笔迹。
- 加 `--merge` 的合并字体包含基础字体的字形，属于该字体的修改版本。以上述 OFL 字体为基础时，再分发合并字体必须继续采用 SIL OFL 1.1、附带许可文本，且不得单独出售字体。只在本机渲染图片不受此限制。
- 以 `xkcd` 为基础字体时，产物同样受 CC BY-NC 3.0 约束。

许可条款以各项目仓库中的原文为准。如果发现这里有遗漏或错误，欢迎提 issue。
