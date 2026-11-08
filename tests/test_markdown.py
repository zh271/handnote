import pytest

from handnote.markdown import ParseError, parse


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
