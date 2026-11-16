import pytest

from handnote.markdown import ParseError, parse, split_attrs


def kinds(blocks):
    return [b.kind for b in blocks]


def test_headings_and_paragraph():
    blocks = parse("# 一\n\n## 二\n### 三\n\n正文一行")
    assert kinds(blocks) == ["h1", "h2", "h3", "p"]


def test_inline_math_split():
    blocks = parse("设 $A$ 与 $B$ 相交")
    kinds_ = [k for k, _ in blocks[0].segments]
    assert kinds_ == ["t", "m", "t", "m", "t"]


def test_escaped_dollar():
    blocks = parse(r"价格 \$5 与 $x$")
    assert blocks[0].segments[0][1].startswith("价格 $5")
    assert blocks[0].segments[1] == ("m", "x")


def test_unclosed_dollar_is_text():
    blocks = parse("只有 $ 一个")
    assert all(k == "t" for k, _ in blocks[0].segments)


def test_display_math_single_and_multi_line():
    single = parse("$$a+b$$")
    assert single[0].kind == "display" and single[0].segments[0][1] == "a+b"
    multi = parse("$$\na+b\nc+d\n$$")
    assert multi[0].segments[0][1] == "a+b\nc+d"


def test_unterminated_display_raises():
    with pytest.raises(ParseError):
        parse("$$\na+b")


def test_matrix_rows_padded():
    block = parse(r"$$M = \begin{bmatrix} 1 & 0 \\ 1 \end{bmatrix}$$")[0]
    assert block.kind == "matrix"
    assert block.payload["prefix"] == "M ="
    assert block.payload["rows"] == [["1", "0"], ["1", ""]]


def test_matrix_env_kept():
    block = parse(r"$$\begin{pmatrix} 1 \\ 2 \end{pmatrix}$$")[0]
    assert block.payload["env"] == "pmatrix"


def test_lists_and_numbers():
    blocks = parse("- 甲\n- 乙\n\n1. 丙\n2) 丁")
    assert kinds(blocks) == ["bullet", "bullet", "num", "num"]
    assert blocks[2].segments[0][1].startswith("1.")
    assert blocks[3].segments[0][1].startswith("2.")


def test_pagebreak():
    blocks = parse("甲\n\n---\n\n乙")
    assert kinds(blocks) == ["p", "pagebreak", "p"]


def test_emphasis_markers_removed():
    blocks = parse("这是 **重点** 与 `code`")
    assert blocks[0].segments[0][1] == "这是 重点 与 code"


def test_paragraph_lines_joined_for_cjk():
    blocks = parse("第一行\n第二行")
    assert blocks[0].text == "第一行第二行"


def test_paragraph_lines_joined_for_latin():
    blocks = parse("first line\nsecond line")
    assert blocks[0].text == "first line second line"


def test_code_fence_kept_verbatim():
    blocks = parse("```\n$a$ **b**\n```")
    assert blocks[0].kind == "p"
    assert blocks[0].text == "$a$ **b**"


INLINE_MATRIX = r"\begin{bmatrix} 0 & 1 & 1 \\ 0 & 1 & 1 \end{bmatrix}"


def test_inline_matrix_stays_inline():
    block = parse(f"关系矩阵 ${INLINE_MATRIX}$ 如上")[0]
    assert block.kind == "p"
    assert block.segments[1] == ("m", INLINE_MATRIX)


def test_lone_inline_matrix_becomes_matrix_block():
    block = parse(f"${INLINE_MATRIX}$")[0]
    assert block.kind == "matrix"
    assert block.payload["rows"] == [["0", "1", "1"], ["0", "1", "1"]]


def test_single_backslash_row_separator():
    block = parse(r"$$\begin{bmatrix} 0 & 1 \ 1 & 0 \end{bmatrix}$$")[0]
    assert block.payload["rows"] == [["0", "1"], ["1", "0"]]


def test_row_spacing_option_dropped():
    block = parse(r"$$\begin{bmatrix} a \\[2pt] b \end{bmatrix}$$")[0]
    assert block.payload["rows"] == [["a"], ["b"]]


def test_text_after_matrix_is_display_not_error():
    tex = r"A = \begin{bmatrix} 1 \\ 2 \end{bmatrix}, B = \begin{pmatrix} 3 \end{pmatrix}"
    block = parse(f"$${tex}$$")[0]
    assert block.kind == "display"
    assert block.segments == (("m", tex),)


# -- layout attributes ----------------------------------------------------------
@pytest.mark.parametrize("line, text, payload", [
    ("居中 {.center}", "居中", {"align": "center"}),
    ("靠右 {.right}", "靠右", {"align": "right"}),
    ("左 {.left}", "左", {"align": "left"}),
    ("缩进 {indent=2}", "缩进", {"indent": 2.0}),
    ("首行 {first=2}", "首行", {"first": 2.0}),
    ("悬挂 {first=-1.5}", "悬挂", {"first": -1.5}),
    ("组合 { .center  indent=2 first=1 }", "组合", {"align": "center", "indent": 2.0,
                                                 "first": 1.0}),
    ("贴着{.right}", "贴着", {"align": "right"}),
])
def test_split_attrs_keys_and_combinations(line, text, payload):
    assert split_attrs(line) == (text, payload)


def test_split_attrs_clamps_and_resets():
    assert split_attrs("x {indent=99}") == ("x", {"indent": 20.0})
    assert split_attrs("x {first=-99}") == ("x", {"first": -20.0})
    assert split_attrs("x {first=99}") == ("x", {"first": 20.0})
    assert split_attrs("x {indent=-3}") == ("x", {}), "indent is never negative"
    assert split_attrs("x {indent=0 first=0}") == ("x", {})
    # The last alignment wins; .left is kept so formulas can tell it from the default.
    assert split_attrs("x {.center .left}") == ("x", {"align": "left"})
    assert split_attrs("x {.left .right}") == ("x", {"align": "right"})


@pytest.mark.parametrize("line", [
    "集合 {x}", "未知 {.middle}", "没有值 {indent=}", "太大 {indent=999}",
    r"公式 $\{a\}$", "中间 {.center} 还有字", "{ }",
])
def test_malformed_attrs_are_left_alone(line):
    assert split_attrs(line) == (line, {})


def test_math_braces_survive_parse():
    blocks = parse(r"集合 $A=\{a\}$")
    assert blocks[0].payload == {}
    assert blocks[0].segments[-1] == ("m", r"A=\{a\}")


def test_attrs_on_headings_bullets_and_numbers():
    blocks = parse("# 标题 {.center}\n\n- 项目 {indent=2}\n\n3. 步骤 {.right first=1}\n\n"
                   "普通段落")
    assert [(b.kind, b.text, b.payload) for b in blocks] == [
        ("h1", "标题", {"align": "center"}),
        ("bullet", "• 项目", {"indent": 2.0}),
        ("num", "3.  步骤", {"align": "right", "first": 1.0}),
        ("p", "普通段落", {}),
    ]


def test_attrs_on_any_line_of_a_paragraph():
    blocks = parse("第一行\n第二行 {.center}")
    assert len(blocks) == 1
    assert blocks[0].text == "第一行第二行" and blocks[0].payload == {"align": "center"}
    assert parse("{.center}") == [], "a lone attribute list makes no block"


def test_wide_space_lead_is_first_line_indent():
    (block,) = parse("　　首行缩进两字")
    assert block.text == "首行缩进两字" and block.payload == {"first": 2.0}
    (block,) = parse("　　首行 {first=4}")
    assert block.payload == {"first": 4.0}, "an explicit first= wins over the lead"
    (block,) = parse("　　首行 {.center}")
    assert block.payload == {"align": "center", "first": 2.0}
    (block,) = parse("  普通空格不算")
    assert block.payload == {}


@pytest.mark.parametrize("mark", ["&nbsp;", "<br>", "<br/>", "<BR />"])
def test_blank_lines(mark):
    blocks = parse(f"甲\n{mark}\n乙")
    assert [b.kind for b in blocks] == ["p", "blank", "p"]
    assert [b.kind for b in parse(f"甲\n\n{mark}\n\n{mark}\n\n乙")] == [
        "p", "blank", "blank", "p"]
    assert parse("正文里的 &nbsp; 不算")[0].kind == "p"


# -- layout marks on display formulas -------------------------------------------
def test_mark_after_single_line_display():
    blocks = parse("前文\n\n$$x^2$$ {.left}\n\n后文")
    assert [(b.kind, b.payload) for b in blocks] == [
        ("p", {}), ("display", {"align": "left"}), ("p", {})]
    assert blocks[1].segments == (("m", "x^2"),)
    (block,) = parse(r"$$\{a\}$$ {.right}")
    assert block.segments == (("m", r"\{a\}"),) and block.payload == {"align": "right"}


def test_mark_on_closing_line_of_multiline_display():
    (block,) = parse("$$\na+b\n=c\n$$ {.right indent=2}")
    assert block.kind == "display"
    assert block.segments == (("m", "a+b\n=c"),)
    assert block.payload == {"align": "right", "indent": 2.0}
    (block,) = parse("$$a+b\n=c$$ {.left}")
    assert block.segments == (("m", "a+b\n=c"),) and block.payload == {"align": "left"}


def test_display_keeps_only_align_and_indent():
    (block,) = parse("$$x$$ {.center indent=2 first=3}")
    assert block.payload == {"align": "center", "indent": 2.0}
    (block,) = parse("$$x$$ {first=3}")
    assert block.payload == {}
    (block,) = parse("$$x$$")
    assert block.payload == {}


def test_marks_on_matrices_merge_with_rows():
    (block,) = parse(r"$$M=\begin{bmatrix}1&0\\0&1\end{bmatrix}$$ {.right first=1}")
    assert block.kind == "matrix"
    assert block.payload == {"prefix": "M=", "rows": [["1", "0"], ["0", "1"]],
                             "env": "bmatrix", "align": "right"}
    (block,) = parse(r"$\begin{pmatrix}a&b\\c&d\end{pmatrix}$ {.left indent=1}")
    assert block.kind == "matrix"
    assert block.payload == {"prefix": "", "rows": [["a", "b"], ["c", "d"]],
                             "env": "pmatrix", "align": "left", "indent": 1.0}
    (block,) = parse(r"$$\begin{bmatrix}1\end{bmatrix}$$")
    assert set(block.payload) == {"prefix", "rows", "env"}


def test_unterminated_display_still_raises_with_marks():
    with pytest.raises(ParseError):
        parse("$$\na+b {.left}\n")
