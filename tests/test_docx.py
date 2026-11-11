"""Word .docx import (documents assembled by hand, no Word or python-docx needed)."""

import io
import zipfile

import pytest

from handnote.docx import DocxError, docx_to_markdown
from handnote.markdown import parse

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"')

STYLES = f"""<w:styles {NS}>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
  <w:style w:type="paragraph" w:styleId="a5"><w:name w:val="标题 2"/></w:style>
  <w:style w:type="paragraph" w:styleId="Outline"><w:name w:val="Custom"/>
    <w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/>
    <w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>
</w:styles>"""

NUMBERING = f"""<w:numbering {NS}>
  <w:abstractNum w:abstractNumId="10">
    <w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
    <w:lvl w:ilvl="1"><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="20">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="30">
    <w:lvl w:ilvl="0"><w:numFmt w:val="chineseCounting"/><w:lvlText w:val="%1、"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="20"/></w:num>
  <w:num w:numId="3"><w:abstractNumId w:val="30"/></w:num>
</w:numbering>"""


def _docx(body: str, styles: str = STYLES, numbering: str = NUMBERING) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
        if styles:
            z.writestr("word/styles.xml", styles)
        if numbering:
            z.writestr("word/numbering.xml", numbering)
    return buf.getvalue()


def _p(text: str = "", style: str | None = None, num: tuple[int, int] | None = None,
       extra: str = "", bold: bool = False) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num:
        ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    run = f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>' if text else ""
    return f"<w:p><w:pPr>{ppr}</w:pPr>{run}{extra}</w:p>"


def _md(body: str, **kw) -> str:
    return docx_to_markdown(_docx(body, **kw)).markdown


def test_headings_from_styles_and_outline_levels():
    md = _md(_p("总标题", "Title") + _p("一级", "Heading1") + _p("二级", "a5")
             + _p("大纲三级", "Outline") + _p("正文"))
    assert md.splitlines()[::2] == ["# 总标题", "# 一级", "## 二级", "### 大纲三级", "正文"]
    kinds = [b.kind for b in parse(md)]
    assert kinds == ["h1", "h1", "h2", "h3", "p"]


def test_numbering_bullets_and_chinese_markers():
    body = (_p("甲", num=(1, 0)) + _p("子项", num=(1, 1)) + _p("子项二", num=(1, 1))
            + _p("乙", num=(1, 0)) + _p("再子项", num=(1, 1))
            + _p("圆点", num=(2, 0)) + _p("第一部分", num=(3, 0)) + _p("第二部分", num=(3, 0))
            + _p("经样式编号", "ListNumber"))
    lines = _md(body).splitlines()[::2]
    # Nested levels indent two characters per level (``{indent=2}``).
    assert lines == ["1. 甲", "a) 子项 {indent=2}", "b) 子项二 {indent=2}", "2. 乙",
                     "a) 再子项 {indent=2}", "- 圆点",
                     "一、第一部分", "二、第二部分", "3. 经样式编号"]
    kinds = [b.kind for b in parse("\n\n".join(lines))]
    assert kinds[:3] == ["num", "p", "p"] and kinds[5] == "bullet"


def test_breaks_tabs_dollars_and_bold_lines():
    body = ('<w:p><w:r><w:t>第一行</w:t><w:br/><w:t>第二行</w:t><w:tab/>'
            '<w:t>价格 $5</w:t></w:r></w:p>'
            '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            + _p("加粗小标题", bold=True) + _p("这是一个加粗的长句子，以句号结尾。", bold=True)
            + '<w:p><w:pPr><w:pageBreakBefore/></w:pPr><w:r><w:t>新页</w:t></w:r></w:p>')
    md = _md(body)
    assert md.split("\n\n") == ["第一行", "第二行　价格 \\$5", "---", "### 加粗小标题",
                                "这是一个加粗的长句子，以句号结尾。", "---", "新页\n"]
    blocks = parse(md)
    assert blocks[1].text == "第二行　价格 $5"
    assert [b.kind for b in blocks].count("pagebreak") == 2


def test_no_leading_or_doubled_page_breaks():
    body = ('<w:p><w:r><w:br w:type="page"/></w:r></w:p>' + _p("A")
            + '<w:p><w:r><w:br w:type="page"/><w:br w:type="page"/></w:r></w:p>' + _p("B")
            + '<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    assert _md(body) == "A\n\n---\n\nB\n"


def _math(inner: str, display: bool = False) -> str:
    omath = f"<m:oMath>{inner}</m:oMath>"
    return f"<m:oMathPara>{omath}</m:oMathPara>" if display else omath


def _r(text: str) -> str:
    return f"<m:r><m:t>{text}</m:t></m:r>"


def test_inline_equation_to_latex():
    frac = f"<m:f><m:num>{_r('a')}</m:num><m:den>{_r('b+1')}</m:den></m:f>"
    sup = f"<m:sSup><m:e>{_r('x')}</m:e><m:sup>{_r('2')}</m:sup></m:sSup>"
    sub = f"<m:sSub><m:e>{_r('x')}</m:e><m:sub>{_r('ij')}</m:sub></m:sSub>"
    rad = f"<m:rad><m:radPr><m:degHide m:val='1'/></m:radPr><m:deg/><m:e>{_r('y')}</m:e></m:rad>"
    rad3 = f"<m:rad><m:deg>{_r('3')}</m:deg><m:e>{_r('z')}</m:e></m:rad>"
    body = ('<w:p><w:r><w:t xml:space="preserve">设 </w:t></w:r>'
            + _math(sup + _r("+") + frac + _r("≤α∈ℝ") + sub + rad + rad3)
            + '<w:r><w:t>，成立。</w:t></w:r></w:p>')
    md = _md(body)
    assert md.strip() == (r"设 $x^2+\frac{a}{b+1}\leq \alpha \in \mathbb{R}x_{ij}\sqrt{y}"
                          r"\sqrt[3]{z}$，成立。")
    segs = parse(md)[0].segments
    assert segs[0] == ("t", "设 ") and segs[1][0] == "m" and segs[2] == ("t", "，成立。")


def test_display_equations_matrices_and_arrays():
    matrix = ("<m:d><m:dPr><m:begChr m:val='['/><m:endChr m:val=']'/></m:dPr><m:e><m:m>"
              f"<m:mr><m:e>{_r('1')}</m:e><m:e>{_r('0')}</m:e></m:mr>"
              f"<m:mr><m:e>{_r('0')}</m:e><m:e>{_r('1')}</m:e></m:mr></m:m></m:e></m:d>")
    nary = (f"<m:nary><m:naryPr><m:chr m:val='∑'/></m:naryPr><m:sub>{_r('i=1')}</m:sub>"
            f"<m:sup>{_r('n')}</m:sup><m:e>{_r('i')}</m:e></m:nary>")
    integral = (f"<m:nary><m:sub>{_r('0')}</m:sub><m:sup>{_r('1')}</m:sup>"
                f"<m:e>{_r('x')}</m:e></m:nary>")
    func = (f"<m:func><m:fName><m:r><m:rPr><m:sty m:val='p'/></m:rPr><m:t>sin</m:t></m:r>"
            f"</m:fName><m:e>{_r('x')}</m:e></m:func>")
    paren = f"<m:d><m:e>{_r('a+b')}</m:e></m:d>"
    arr = f"<m:eqArr><m:e>{_r('x=1')}</m:e><m:e>{_r('y=2')}</m:e></m:eqArr>"
    body = (f"<w:p>{_math(_r('I=') + matrix, True)}</w:p>"
            f"<w:p>{_math(nary + _r('=') + integral, True)}</w:p>"
            f"<w:p>{_math(func + paren, True)}</w:p>"
            f"<w:p>{_math(arr, True)}</w:p>")
    md = _md(body)
    assert md.split("\n\n") == [
        r"$$I=\begin{bmatrix}1 & 0 \\ 0 & 1\end{bmatrix}$$",
        r"$$\sum_{i=1}^{n} i=\int_{0}^{1} x$$",
        r"$$\sin x(a+b)$$",
        "$$x=1$$", "$$y=2$$\n"]
    kinds = [b.kind for b in parse(md)]
    assert kinds[0] in ("matrix", "display") and kinds.count("display") >= 4


def test_tables_images_and_warnings():
    cell = "<w:tc><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc>"
    table = ("<w:tbl>" + "".join(
        "<w:tr>" + cell.format(a) + cell.format(b) + "</w:tr>"
        for a, b in (("姓名", "成绩"), ("张三", "90"))) + "</w:tbl>")
    image = '<w:p><w:r><w:drawing><wp/></w:drawing></w:r><w:r><w:t>图注</w:t></w:r></w:p>'
    result = docx_to_markdown(_docx(_p("前") + table + image))
    assert result.markdown.split("\n\n") == ["前", "姓名　　成绩", "张三　　90",
                                             "图注\n"]
    assert result.warnings == ["1 个表格按行转成了段落", "1 张图片没有导入"]


def test_minimal_document_without_styles_or_numbering():
    md = _md(_p("只有正文") + _p("编号丢失", num=(1, 0)), styles="", numbering="")
    assert md == "只有正文\n\n编号丢失\n"


def test_rejects_old_doc_and_garbage():
    with pytest.raises(DocxError, match="另存为"):
        docx_to_markdown(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    with pytest.raises(DocxError):
        docx_to_markdown(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("other.xml", "<x/>")
    with pytest.raises(DocxError):
        docx_to_markdown(buf.getvalue())


def test_cli_docx_and_render(tmp_path, capsys):
    from handnote.cli import main

    src = tmp_path / "notes.docx"
    src.write_bytes(_docx(_p("标题", "Heading1") + _p("正文 ") ))
    assert main(["docx", str(src), "-o", str(tmp_path / "notes.md")]) == 0
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "# 标题\n\n正文\n"
    assert main(["analyze", str(src)]) in (0, 2)
    assert '"missing"' in capsys.readouterr().out


def test_every_emitted_command_parses_in_mathtext():
    """Word symbols map only to commands matplotlib's mathtext understands."""
    from matplotlib.mathtext import MathTextParser

    from handnote.docx import _ACCENT, _FUNCS, _NARY, _REVERSE

    parser = MathTextParser("path")
    commands = {v.strip() for v in _REVERSE.values() if v.startswith("\\")}
    commands |= set(_NARY.values()) | {a + "{x}" for a in _ACCENT.values()}
    commands |= {f"\\{f} x" for f in _FUNCS} | {"a&b"}
    for tex in sorted(commands):
        parser.parse(f"${tex}$")


# -- alignment, indents and empty paragraphs ----------------------------------------
def _pp(text: str, ppr: str) -> str:
    """A paragraph with raw ``pPr`` children."""
    return f'<w:p><w:pPr>{ppr}</w:pPr><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


LAYOUT_STYLES = f"""<w:styles {NS}>
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:ind w:firstLine="480"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>
    <w:pPr><w:ind w:left="480"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Base2"><w:name w:val="Base2"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="960" w:firstLineChars="100"/></w:pPr>
    <w:rPr><w:sz w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>
    <w:basedOn w:val="Base2"/><w:pPr><w:jc w:val="center"/></w:pPr></w:style>
</w:styles>"""


def _payloads(md: str) -> list[tuple[str, str, dict]]:
    return [(b.kind, b.text, b.payload) for b in parse(md)]


def test_alignment():
    md = _md(_pp("居中", '<w:jc w:val="center"/>') + _pp("靠右", '<w:jc w:val="right"/>')
             + _pp("末端", '<w:jc w:val="end"/>') + _pp("两端", '<w:jc w:val="both"/>'))
    assert md.split("\n\n") == ["居中 {.center}", "靠右 {.right}", "末端 {.right}", "两端\n"]
    assert _payloads(md) == [("p", "居中", {"align": "center"}),
                             ("p", "靠右", {"align": "right"}),
                             ("p", "末端", {"align": "right"}), ("p", "两端", {})]


def test_heading_keeps_alignment():
    md = _md(_pp("总标题", '<w:pStyle w:val="Title"/><w:jc w:val="center"/>'))
    assert md == "# 总标题 {.center}\n"
    assert _payloads(md) == [("h1", "总标题", {"align": "center"})]


def test_first_line_and_hanging_indents():
    md = _md(_pp("二字", '<w:ind w:firstLine="420"/>')
             + _pp("字符优先", '<w:ind w:firstLineChars="200" w:firstLine="999"/>')
             + _pp("悬挂", '<w:ind w:left="840" w:hanging="420"/>')
             + _pp("悬挂字符", '<w:ind w:hangingChars="150"/>')
             + _pp("微调", '<w:ind w:firstLine="50"/>')
             + _pp("零字符回退", '<w:ind w:leftChars="0" w:left="420"/>'))
    assert md.split("\n\n") == ["二字 {first=2}", "字符优先 {first=2}",
                                "悬挂 {indent=4 first=-2}", "悬挂字符 {first=-1.5}",
                                "微调", "零字符回退 {indent=2}\n"]
    assert [b.payload for b in parse(md)] == [
        {"first": 2.0}, {"first": 2.0}, {"indent": 4.0, "first": -2.0}, {"first": -1.5},
        {}, {"indent": 2.0}]


def test_indents_follow_the_style_chain():
    md = _md(_p("默认") + _p("引文", "Quote")
             + _pp("段落覆盖左缩进", '<w:pStyle w:val="Quote"/><w:ind w:left="0"/>')
             + _pp("段落覆盖首行", '<w:pStyle w:val="Base2"/><w:ind w:firstLine="0"/>'),
             styles=LAYOUT_STYLES)
    assert md.split("\n\n") == [
        # docDefaults first line (480 twips) + default style left (480), 12pt = 240 twips.
        "默认 {indent=2 first=2}",
        # basedOn Base2: left 960 at its 16pt, firstLineChars replaces the default.
        "引文 {.center indent=3 first=1}",
        "段落覆盖左缩进 {.center first=1}",
        "段落覆盖首行 {indent=3}\n"]
    assert _payloads(md) == [
        ("p", "默认", {"indent": 2.0, "first": 2.0}),
        ("p", "引文", {"align": "center", "indent": 3.0, "first": 1.0}),
        ("p", "段落覆盖左缩进", {"align": "center", "first": 1.0}),
        ("p", "段落覆盖首行", {"indent": 3.0})]


def test_list_items_indent_by_level_not_by_ind():
    hanging = '<w:ind w:left="840" w:hanging="420"/>'
    md = _md(_p("一", num=(1, 0))
             + _pp("二", '<w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr>' + hanging)
             + _pp("点", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>' + hanging))
    assert md.split("\n\n") == ["1. 一", "a) 二 {indent=2}", "- 点\n"]
    assert _payloads(md) == [("num", "1.  一", {}), ("p", "a) 二", {"indent": 2.0}),
                             ("bullet", "• 点", {})]


def test_typed_leading_spaces_are_first_line_indent():
    md = _md(_p("　　两个全角") + _p("    四个空格")
             + _pp("　加在样式上", '<w:ind w:firstLine="420"/>'))
    assert md.split("\n\n") == ["两个全角 {first=2}", "四个空格 {first=2}",
                                "加在样式上 {first=3}\n"]
    assert [b.payload for b in parse(md)] == [{"first": 2.0}, {"first": 2.0},
                                              {"first": 3.0}]


def test_empty_paragraphs_become_blank_lines():
    page = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    image = '<w:p><w:r><w:drawing><wp/></w:drawing></w:r></w:p>'
    section = '<w:p><w:pPr><w:sectPr/></w:pPr></w:p>'
    body = (_p() + _p("甲") + _p() + _p() + _p("乙") + _p() + page + _p() + _p("丙")
            + image + _p("丁") + section + _p("戊") + _p() + _p())
    result = docx_to_markdown(_docx(body))
    assert result.markdown.split("\n\n") == [
        "甲", "&nbsp;", "&nbsp;", "乙", "---", "丙", "丁", "戊\n"]
    assert result.warnings == ["1 张图片没有导入"]
    kinds = [b.kind for b in parse(result.markdown)]
    assert kinds == ["p", "blank", "blank", "p", "pagebreak", "p", "p", "p"]


def test_blank_before_page_break_before_is_dropped():
    body = (_p("甲") + _p()
            + '<w:p><w:pPr><w:pageBreakBefore/></w:pPr><w:r><w:t>新页</w:t></w:r></w:p>')
    assert _md(body) == "甲\n\n---\n\n新页\n"
