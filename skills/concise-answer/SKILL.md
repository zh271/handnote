---
name: concise-answer
description: 用户发来题目（文字或照片，可附补充要求）时使用。直接给出题号和最精简的作答，不写思路和解释；输出是 handnote 能渲染成手写页的 Markdown，公式只用本文白名单里的写法。
---

# 极简作答

用户发来的内容就是题目或对题目的补充要求，不用确认意图，直接作答。输出会交给 handnote 渲染成手写页，所以排版和公式必须落在下面的限制内。

## 1. 确定要答哪些题

1. 照片里只有一道题，或者用文字发来题目：直接作答。
2. 照片里有多道题，补充要求也没有指定题号或范围：先确认，不作答。整次输出只写一行，列出图中印刷的题号，例如 `图中有 3、4、5 题，答哪几题？`。照片里有被裁掉一半的题，也一并列出。
3. 补充要求指定了题号或范围（如"只做 3、5""全做"），或者用户回复了确认：只答指定的题。题号照抄原题，不要自己重新编号。
4. 某题题干被裁切或看不清，缺少作答所需的条件：该题只写 `N. 题干不全`，不要猜。
5. 补充要求（如"用向量法""只要答案""写详细点"）优先于本文的默认做法。

## 2. 写什么

只写题号和作答。不写思路、解释、题干复述、开场白、总结，也不写"答："。

- 选择题：`1. B`；多选写 `2. ABD`。
- 填空题：`3. $\frac{\sqrt{3}}{2}$`；有多个空时用全角分号隔开：`4. $2$；$-1$`。
- 判断题：`5. 对` / `5. 错`。不要用 √、✓。
- 解答题、证明题：只写推导链，能用符号就不用汉字，最后一行是结论。

推导链的写法：

- 因果关系用 `$\because$` `$\therefore$` `$\Rightarrow$` `$\Leftrightarrow$`。
- 汉字只用这些连接词：设、令、由、得、即、又、则、故、联立、解得、或、且、舍、同理、综上、当…时、取、记、证毕。
- 只保留得分步：关键条件、关键变形、结果。省掉一眼可见的代数化简。
- 每一步只写一个推出关系，不要把三步塞进一行。
- 证明题最后一行写 `$\therefore$ 原式成立` 或 `证毕`。

## 3. 排版

handnote 只支持 Markdown 的一个子集。下面的规则不是风格建议，违反会导致缺字、拼行或渲染失败。

**题号与分行**

- 大题题号写在行首：`7. ` 后接第一步，题号行不要单独成行。
- 小问另起一段，写成 `(1) `、`(2) `。不要用 ①、⑴、Ⅰ，字体里没有这些字。
- 每一步单独一段，**步骤之间空一行**。不空行的相邻行会被拼成同一段。
- 不要用标题 `#`、表格、引用 `>`、代码块、`---`（会分页）。`**加粗**` 会被丢掉，不要用。

**公式**

- 以行内 `$...$` 为主。只有叠了两层以上的分式、求和、积分，或整行公式本来就很长时，才用独占一行的 `$$...$$`。`$$` 必须写在同一行内，开头和结尾都在这一行。
- **所有数学符号都写在 `$` 里**，包括 ∵ ∴ ⇒ ≤ ≥ ≠ ∈ √ π。正文字体没有这些字，写在 `$` 外会变成方框。
- 汉字尽量写在 `$` 外：`由 $a>0$ 得`。必须放在公式里时用 `\text{}`：`$x=1\text{或}x=2$`。
- 一个 `$...$` 不要超过约 40 个源码字符。行内公式不会折行，过长会伸出页边。长等式链在 `=` 或 `\Rightarrow` 处拆成两步。
- 汉字和公式之间不需要加空格，渲染器会自动处理。
- 中文部分用全角标点 `，。；：`，公式内部用半角。

## 4. 公式白名单

只用下面列出的写法。白名单之外的命令，没有把握就换成白名单里的等价写法。

**关系与逻辑**

- 不等号：`$x\leq 1$` `$x\geq 2$` `$x\leqslant 1$` `$x\geqslant 2$` `$x\neq 1$` `$a<b$` `$a>b$`
- 近似、恒等与相似：`$\approx 3.14$` `$a\equiv b$` `$\sim$` `$\cong$`
- 推理：`$\because$` `$\therefore$` `$\Rightarrow$` `$\Leftrightarrow$` `$\to$` `$\rightarrow$` `$\leftarrow$`
- 量词与连接词：`$\forall x$` `$\exists x$` `$\neg p$` `$p\wedge q$` `$p\vee q$`
- 集合：`$x\in A$` `$x\notin A$` `$A\subseteq B$` `$A\subsetneq B$` `$A\cap B$` `$A\cup B$` `$A\setminus B$` `$\varnothing$` `$\complement_U A$` `$\{x\mid x>0\}$`
- 数集：`$\mathbb{R}$` `$\mathbb{N}$` `$\mathbb{Z}$` `$\mathbb{Q}$` `$\mathbb{C}$`
- 几何：`$AB\parallel CD$` `$AB\perp CD$` `$\angle ABC=90^\circ$` `$\triangle ABC\cong\triangle DEF$` `$S_{\triangle ABC}$`

**运算与结构**

- 分式与根式：`$\frac{a}{b}$` `$\dfrac{a}{b}$` `$\sqrt{2}$` `$\sqrt[3]{x}$`
- 上下标：`$x^{2}$` `$a_{n+1}$` `$f'(x)$` `$f''(x)$`
- 常用运算符：`$\pm$` `$\times$` `$\div$` `$a\cdot b$` `$10\%$`
- 括号：`$\left(\frac{1}{2}\right)$` `$\left|x\right|$` `$(-\infty,1]\cup[2,+\infty)$` `$\lfloor x\rfloor$`
- 函数：`$\sin^2\alpha+\cos^2\alpha=1$` `$\tan\theta$` `$\arcsin x$` `$\ln x$` `$\lg x$` `$\log_2 x$` `$\mathrm{e}^x$` `$\max\{a,b\}$` `$\min f(x)$`
- 极限与求和：`$\lim_{x\to 0}\frac{\sin x}{x}=1$` `$\sum_{i=1}^{n} i$` `$\prod_{k=1}^{n} k$`
- 微积分：`$\int_{0}^{1} x\mathrm{d}x$` `$\frac{\mathrm{d}y}{\mathrm{d}x}$` `$\partial f$` `$\infty$`
- 排列组合与概率：`$\binom{n}{k}$` `$C_n^k$` `$A_n^k$` `$P(A|B)$`
- 向量与修饰：`$\overrightarrow{AB}$` `$\vec{a}$` `$\overline{AB}$` `$\bar{x}$` `$\hat{y}$` `$\widehat{AB}$`
- 定义与模运算：`$\Delta=b^2-4ac$` `$\overset{\Delta}{=}$` `$a\equiv b\ (\mathrm{mod}\ 3)$`
- 间距：`$x\,y$` `$x\ y$` `$x\quad y$`
- 汉字：`$x=1\text{或}x=2$`
- 矩阵与行列式（行用 `\\` 分隔，单元用 `&`）：`$\begin{bmatrix}1&0\\0&1\end{bmatrix}$` `$\begin{vmatrix}1&2\\3&4\end{vmatrix}$`
- 方程组：用 `matrix` 上下对齐，不加大括号，前面写"联立"：`$\begin{matrix}x+y=3\\x-y=1\end{matrix}$`

### 禁用

这些写法在 handnote 里会解析失败，整条公式退化成纯文本：

| 不要写 | 改成 |
|---|---|
| `\le` `\ge` | `\leq` `\geq` |
| `\iff` `\implies` `\gets` | `\Leftrightarrow` `\Rightarrow` `\leftarrow` |
| `\land` `\lor` | `\wedge` `\vee` |
| `\R` `\N` 等简写 | `\mathbb{R}` |
| `\begin{cases}` `aligned` `array` | 分段函数每段一行：`当 $x>0$ 时，$f(x)=1$`；方程组用 `matrix` |
| `\left\{ ... \right.` 包住矩阵 | 不加括号的 `matrix` |
| `\tfrac` `\cfrac` | `\frac` / `\dfrac` |
| `\lim\limits` `\displaystyle` `\mathop` | 直接写 `\lim_{x\to 0}` |
| `\boxed` `\color` `\underbrace` `\stackrel` | 不要框、不要颜色；`\stackrel` 改 `\overset` |
| `\pmod{3}` | `\ (\mathrm{mod}\ 3)` |
| `$$` 内换行、`$$` 内的 `\\`（矩阵之外） | 拆成多个 `$$` 段 |

## 5. 示例

用户：一张照片，里面有第 1、2 题（选择、填空）和第 17 题（解答，两小问），没有补充要求。

输出：

```markdown
图中有 1、2、17 题，答哪几题？
```

用户：全做。

输出：

```markdown
1. C

2. $(-\infty,1]\cup[2,+\infty)$

17. (1) $\because a_{n+1}-a_n=2$

$\therefore \{a_n\}$ 为等差数列，$a_n=2n-1$

(2) $b_n=\frac{1}{a_n a_{n+1}}=\frac{1}{2}\left(\frac{1}{2n-1}-\frac{1}{2n+1}\right)$

$\therefore T_n=\frac{1}{2}\left(1-\frac{1}{2n+1}\right)=\frac{n}{2n+1}$
```

用户：解方程组 x+y=3, x−y=1。

输出：

```markdown
1. 联立 $\begin{matrix}x+y=3\\x-y=1\end{matrix}$

解得 $x=2$，$y=1$
```

用户：证明 $a>0$ 时 $a+\frac{1}{a}\geq 2$。

输出：

```markdown
1. $\because a>0$

$\therefore a+\frac{1}{a}-2=\frac{(a-1)^2}{a}\geq 0$

$\therefore a+\frac{1}{a}\geq 2$，当且仅当 $a=1$ 时取等
```

## 6. 输出前自查

- [ ] 照片里有多道题而用户没指定时，只输出了一行确认。
- [ ] 只有题号和作答，没有思路、解释或客套话。
- [ ] 每步之间空了一行，小问写成 `(1)`。
- [ ] ∵ ∴ ⇒ ≤ 等符号都在 `$` 里。
- [ ] 没有用到"禁用"表里的写法，`\le`/`\ge` 已写成 `\leq`/`\geq`。
- [ ] 没有任何一个 `$...$` 长到会伸出页边。
