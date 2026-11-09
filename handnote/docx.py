"""Word (.docx) -> the Markdown subset handnote renders.

Standard library only: a .docx is a zip of XML parts. What carries over:

- Heading styles (``Heading 1``/``标题 1``, ``Title``, outline levels) -> ``#``..``###``;
  a short paragraph that is bold throughout counts as a ``###`` heading.
- Word's automatic numbering: decimal ``1.`` / ``1)`` -> numbered items,
  bullets -> ``-`` items, other formats (``一、``, ``(1)``, ``a.``, ``①``)
  keep their marker as text. Counters follow Word's levels and restarts.
- Page breaks (``w:br type=page``, "page break before") -> ``---``.
- Soft line breaks start a new paragraph, so lines stay where they were.
- Equations (OMML) -> LaTeX: fractions, scripts, radicals, n-ary operators,
  delimiters, functions, accents, matrices (``\\begin{bmatrix}``) and
  equation arrays (one display formula per row).
- Tables become one paragraph per row (cells separated by wide spaces);
  images, text boxes and headers/footers are dropped. Both are reported
  in ``warnings``.

Literal ``$`` in the text is escaped so it is not read as math.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from .coverage import LATEX_SYMBOLS

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MAX_BYTES = 50 * 1024 * 1024
_SENTENCE_END = ("。", "．", ".", "：", ":")
WIDE_SPACE = "\u3000"


class DocxError(ValueError):
    """The file is not a readable .docx document."""


@dataclass
class DocxResult:
    markdown: str
    warnings: list[str] = field(default_factory=list)


def docx_to_markdown(data: bytes) -> DocxResult:
    """Convert the bytes of a .docx file; see the module docstring."""
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise DocxError("这是旧版 .doc 文件，请在 Word 里“另存为” .docx 后再导入")
    if len(data) > MAX_BYTES:
        raise DocxError("文件太大（超过 50MB）")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        document = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise DocxError("不是有效的 .docx 文件") from exc
    styles = _Styles(_part(archive, "word/styles.xml"))
    numbering = _Numbering(_part(archive, "word/numbering.xml"))
    body = document.find(f"{W}body")
    conv = _Converter(styles, numbering)
    if body is not None:
        conv.block_container(body)
    return DocxResult(conv.markdown(), conv.warnings())


def _part(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(name))
    except (KeyError, ET.ParseError):
        return None


def _val(el: ET.Element | None, tag: str, attr: str = "val") -> str | None:
    if el is None:
        return None
    child = el.find(tag)
    if child is None:
        return None
    return child.get(f"{W}{attr}") if tag.startswith(W) else child.get(f"{M}{attr}")


def _on(el: ET.Element | None, tag: str) -> bool:
    """A Word toggle property (``<w:b/>``, ``<w:b w:val="0"/>``)."""
    if el is None:
        return False
    child = el.find(tag)
    if child is None:
        return False
    return child.get(f"{W}val", "true") not in ("0", "false", "off")


# -- styles and numbering --------------------------------------------------------------
class _Styles:
    def __init__(self, root: ET.Element | None):
        self.styles: dict[str, ET.Element] = {}
        if root is not None:
            for st in root.iter(f"{W}style"):
                sid = st.get(f"{W}styleId")
                if sid:
                    self.styles[sid] = st

    def _chain(self, sid: str | None):
        seen = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            st = self.styles[sid]
            yield st
            sid = _val(st, f"{W}basedOn")

    def heading_level(self, sid: str | None) -> int | None:
        for st in self._chain(sid):
            name = (_val(st, f"{W}name") or "").strip().lower()
            if name in ("title", "标题"):
                return 1
            if name in ("subtitle", "副标题"):
                return 2
            m = re.fullmatch(r"(?:heading|标题)\s*(\d)", name)
            if m:
                return int(m.group(1))
            lvl = _val(st.find(f"{W}pPr"), f"{W}outlineLvl")
            if lvl is not None and lvl.isdigit() and int(lvl) < 9:
                return int(lvl) + 1
        return None

    def numbering(self, sid: str | None) -> tuple[str, int] | None:
        for st in self._chain(sid):
            num = st.find(f"{W}pPr/{W}numPr")
            if num is not None:
                num_id = _val(num, f"{W}numId")
                if num_id:
                    return num_id, int(_val(num, f"{W}ilvl") or 0)
        return None


_CN_DIGITS = "零一二三四五六七八九"


def _chinese(n: int) -> str:
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
                       (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"),
                       (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(97 + rem) + out
    return out


def _format(n: int, fmt: str) -> str:
    if fmt in ("chineseCounting", "chineseCountingThousand", "ideographTraditional",
               "japaneseCounting", "chineseLegalSimplified"):
        return _chinese(n)
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalEnclosedCircle" and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class _Numbering:
    def __init__(self, root: ET.Element | None):
        self.abstract: dict[str, dict[int, tuple[str, str, int]]] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self.counters: dict[str, list[int]] = {}
        if root is None:
            return
        for ab in root.iter(f"{W}abstractNum"):
            levels = {}
            for lvl in ab.iter(f"{W}lvl"):
                ilvl = int(lvl.get(f"{W}ilvl", "0"))
                levels[ilvl] = (_val(lvl, f"{W}numFmt") or "decimal",
                                _val(lvl, f"{W}lvlText") or f"%{ilvl + 1}.",
                                int(_val(lvl, f"{W}start") or 1))
            self.abstract[ab.get(f"{W}abstractNumId", "")] = levels
        for num in root.iter(f"{W}num"):
            overrides = {}
            for ov in num.iter(f"{W}lvlOverride"):
                start = _val(ov, f"{W}startOverride")
                if start is not None:
                    overrides[int(ov.get(f"{W}ilvl", "0"))] = int(start)
            self.nums[num.get(f"{W}numId", "")] = (_val(num, f"{W}abstractNumId") or "",
                                                   overrides)

    def marker(self, num_id: str, ilvl: int) -> tuple[str, str] | None:
        """``(kind, marker)``: kind is ``bullet``, ``num`` (Markdown ``N.``) or ``text``."""
        if num_id == "0" or num_id not in self.nums:
            return None
        abstract_id, overrides = self.nums[num_id]
        levels = self.abstract.get(abstract_id, {})
        fmt, text, _start = levels.get(ilvl, ("decimal", f"%{ilvl + 1}.", 1))
        if fmt == "none":
            return None
        if fmt == "bullet":
            return "bullet", ""
        key = abstract_id or num_id
        counts = self.counters.setdefault(key, [0] * 9)
        if counts[ilvl] == 0:
            counts[ilvl] = overrides.get(ilvl, levels.get(ilvl, (0, 0, 1))[2])
        else:
            counts[ilvl] += 1
        for deeper in range(ilvl + 1, 9):
            counts[deeper] = 0

        def fill(match: re.Match) -> str:
            level = int(match.group(1)) - 1
            lfmt = levels.get(level, ("decimal", "", 1))[0]
            value = counts[level] or levels.get(level, (0, 0, 1))[2]
            return _format(value, lfmt)

        label = re.sub(r"%(\d)", fill, text).strip()
        if fmt in ("decimal", "decimalZero") and re.fullmatch(r"\d{1,3}[.)]", label):
            return "num", label
        return "text", label


# -- equations ---------------------------------------------------------------------------
_REVERSE: dict[str, str] = {}
for _cmd, _ch in LATEX_SYMBOLS.items():
    if _cmd not in ("sqrt", "vert", "mid"):
        _REVERSE.setdefault(_ch, "\\" + _cmd)
_REVERSE.update({"ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
                 "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "−": "-", "∙": r"\cdot",
                 "·": r"\cdot", "′": "'", "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#",
                 "$": r"\$", "\u2061": "", "\u2062": "", "\u200b": "",
                 # mathtext knows \leq/\geq but not \le/\ge
                 "≤": r"\leq", "≥": r"\geq"})
_NARY = {"∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∬": r"\iint", "∮": r"\oint",
         "⋃": r"\bigcup", "⋂": r"\bigcap", "∐": r"\coprod"}
_ACCENT = {"\u0302": r"\hat", "\u0303": r"\tilde", "\u0307": r"\dot", "\u0308": r"\ddot",
           "\u20d7": r"\vec", "\u0304": r"\bar", "\u0305": r"\overline", "\u030c": r"\check"}
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max",
          "min", "sup", "inf", "det", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
          "deg", "dim", "ker", "gcd", "arg"}
_MATRIX_ENV = {("[", "]"): "bmatrix", ("(", ")"): "pmatrix", ("|", "|"): "vmatrix"}


class _Math:
    """OMML element -> LaTeX."""

    def __init__(self):
        self.unknown: set[str] = set()

    def convert(self, el: ET.Element) -> str:
        return "".join(self.node(child) for child in el).strip()

    def group(self, el: ET.Element | None) -> str:
        return "" if el is None else self.convert(el)

    def braced(self, el: ET.Element | None) -> str:
        text = self.group(el)
        return text if len(text) == 1 else "{" + text + "}"

    def text(self, raw: str, plain: bool) -> str:
        if plain and re.search(r"[\u4e00-\u9fff]|[A-Za-z]{2,}", raw):
            if raw.strip() in _FUNCS:
                return "\\" + raw.strip() + " "
            return r"\mathrm{" + raw.replace(" ", r"\ ") + "}"
        out = []
        for ch in raw:
            rep = _REVERSE.get(ch)
            if rep is not None:
                out.append(rep + (" " if rep[-1:].isalpha() else ""))
            elif "\u4e00" <= ch <= "\u9fff":
                out.append(r"\mathrm{" + ch + "}")
            else:
                out.append(ch)
        return "".join(out)

    def node(self, el: ET.Element) -> str:
        tag = el.tag.replace(M, "m:").replace(W, "w:")
        handler = getattr(self, "_" + tag[2:], None) if tag.startswith("m:") else None
        if tag.startswith("m:") and tag.endswith("Pr"):
            return ""
        if handler is not None:
            return handler(el)
        if tag in ("w:r", "w:ins", "w:smartTag"):
            return "".join(self.node(c) for c in el)
        if tag == "w:t":
            return self.text(el.text or "", False)
        if tag.startswith("m:"):
            return self.convert(el)
        return ""

    def _r(self, el: ET.Element) -> str:
        rpr = el.find(f"{M}rPr")
        plain = rpr is not None and (rpr.find(f"{M}nor") is not None
                                     or _val(rpr, f"{M}sty") == "p")
        raw = "".join(t.text or "" for t in el.iter(f"{M}t"))
        if not raw:
            raw = "".join(t.text or "" for t in el.iter(f"{W}t"))
        return self.text(raw, plain)

    def _f(self, el: ET.Element) -> str:
        num, den = self.group(el.find(f"{M}num")), self.group(el.find(f"{M}den"))
        if _val(el.find(f"{M}fPr"), f"{M}type") == "lin":
            return f"{num}/{den}"
        return rf"\frac{{{num}}}{{{den}}}"

    def _sSup(self, el: ET.Element) -> str:
        return self.braced(el.find(f"{M}e")) + "^" + self.braced(el.find(f"{M}sup"))

    def _sSub(self, el: ET.Element) -> str:
        return self.braced(el.find(f"{M}e")) + "_" + self.braced(el.find(f"{M}sub"))

    def _sSubSup(self, el: ET.Element) -> str:
        return (self.braced(el.find(f"{M}e")) + "_" + self.braced(el.find(f"{M}sub"))
                + "^" + self.braced(el.find(f"{M}sup")))

    def _sPre(self, el: ET.Element) -> str:
        return ("{}_" + self.braced(el.find(f"{M}sub")) + "^" + self.braced(el.find(f"{M}sup"))
                + self.braced(el.find(f"{M}e")))

    def _rad(self, el: ET.Element) -> str:
        deg = self.group(el.find(f"{M}deg"))
        body = self.group(el.find(f"{M}e"))
        hide = _val(el.find(f"{M}radPr"), f"{M}degHide") in ("1", "on", "true")
        return rf"\sqrt[{deg}]{{{body}}}" if deg and not hide else rf"\sqrt{{{body}}}"

    def _nary(self, el: ET.Element) -> str:
        pr = el.find(f"{M}naryPr")
        op = _NARY.get(_val(pr, f"{M}chr") or "∫", r"\int")
        out = op
        sub, sup = self.group(el.find(f"{M}sub")), self.group(el.find(f"{M}sup"))
        if sub and _val(pr, f"{M}subHide") not in ("1", "on", "true"):
            out += "_{" + sub + "}"
        if sup and _val(pr, f"{M}supHide") not in ("1", "on", "true"):
            out += "^{" + sup + "}"
        return out + " " + self.group(el.find(f"{M}e"))

    def _d(self, el: ET.Element) -> str:
        pr = el.find(f"{M}dPr")
        beg = _val(pr, f"{M}begChr")
        end = _val(pr, f"{M}endChr")
        beg = "(" if beg is None else beg
        end = ")" if end is None else end
        sep = _val(pr, f"{M}sepChr") or "|"
        parts = el.findall(f"{M}e")
        if len(parts) == 1:
            mat = [c for c in parts[0] if c.tag == f"{M}m"]
            if len(mat) == 1 and len(list(parts[0])) == len(mat) + sum(
                    1 for c in parts[0] if c.tag.endswith("Pr")):
                env = _MATRIX_ENV.get((beg, end))
                if env:
                    return self.matrix(mat[0], env)
        inner = (" " + self.text(sep, False) + " ").join(self.group(p) for p in parts)
        return self.text(beg, False) + inner + self.text(end, False)

    def matrix(self, el: ET.Element, env: str) -> str:
        rows = [" & ".join(self.group(e) for e in mr.findall(f"{M}e"))
                for mr in el.findall(f"{M}mr")]
        return rf"\begin{{{env}}}" + r" \\ ".join(rows) + rf"\end{{{env}}}"

    def _m(self, el: ET.Element) -> str:
        return self.matrix(el, "matrix")

    def _func(self, el: ET.Element) -> str:
        name = self.group(el.find(f"{M}fName"))
        plain = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", name).strip()
        if plain in _FUNCS:
            name = "\\" + plain
        elif "_" in name and name.split("_")[0].strip() in {"lim", r"\mathrm{lim}"}:
            name = r"\lim_" + name.split("_", 1)[1]
        return name + " " + self.group(el.find(f"{M}e"))

    def _limLow(self, el: ET.Element) -> str:
        base = self.group(el.find(f"{M}e"))
        base = r"\lim" if base in ("lim", r"\mathrm{lim}") else base
        return base + "_{" + self.group(el.find(f"{M}lim")) + "}"

    def _limUpp(self, el: ET.Element) -> str:
        return self.braced(el.find(f"{M}e")) + "^{" + self.group(el.find(f"{M}lim")) + "}"

    def _acc(self, el: ET.Element) -> str:
        chr_ = _val(el.find(f"{M}accPr"), f"{M}chr") or "\u0302"
        return _ACCENT.get(chr_, r"\hat") + "{" + self.group(el.find(f"{M}e")) + "}"

    def _bar(self, el: ET.Element) -> str:
        if _val(el.find(f"{M}barPr"), f"{M}pos") == "bot":
            return r"\underline{" + self.group(el.find(f"{M}e")) + "}"
        return r"\overline{" + self.group(el.find(f"{M}e")) + "}"

    def _eqArr(self, el: ET.Element) -> str:
        return r",\quad ".join(self.group(e) for e in el.findall(f"{M}e"))

    def rows(self, omath: ET.Element) -> list[str]:
        """Display equation -> one LaTeX string per row (equation arrays split)."""
        kids = [c for c in omath if not c.tag.endswith("Pr")]
        if len(kids) == 1 and kids[0].tag == f"{M}eqArr":
            return [self.group(e) for e in kids[0].findall(f"{M}e")]
        return [self.convert(omath)]


def _tidy_math(tex: str) -> str:
    return re.sub(r"\s{2,}", " ", tex).strip()


# -- body -------------------------------------------------------------------------
class _Converter:
    def __init__(self, styles: _Styles, numbering: _Numbering):
        self.styles, self.numbering, self.math = styles, numbering, _Math()
        self.out: list[str] = []
        self.images = 0
        self.tables = 0
        self.textboxes = 0

    def warnings(self) -> list[str]:
        notes = []
        if self.tables:
            notes.append(f"{self.tables} 个表格按行转成了段落")
        if self.images:
            notes.append(f"{self.images} 张图片没有导入")
        if self.textboxes:
            notes.append(f"{self.textboxes} 个文本框没有导入")
        return notes

    def markdown(self) -> str:
        blocks, last = [], None
        for block in self.out:
            if block == "---" and (last in (None, "---")):
                continue  # no leading or doubled page breaks
            blocks.append(block)
            last = block
        while blocks and blocks[-1] == "---":
            blocks.pop()
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def block_container(self, el: ET.Element) -> None:
        for child in el:
            if child.tag == f"{W}p":
                self.paragraph(child)
            elif child.tag == f"{W}tbl":
                self.table(child)
            elif child.tag in (f"{W}sdt", f"{W}customXml"):
                content = child.find(f"{W}sdtContent")
                self.block_container(content if content is not None else child)
            elif child.tag == f"{W}sectPr":
                continue

    def table(self, tbl: ET.Element) -> None:
        self.tables += 1
        for tr in tbl.findall(f"{W}tr"):
            cells = []
            for tc in tr.findall(f"{W}tc"):
                texts = []
                for p in tc.iter(f"{W}p"):
                    lines, _ = self.runs(p)
                    texts.append(" ".join(line for line in lines
                                          if isinstance(line, str) and line.strip()))
                cells.append(" ".join(t for t in texts if t))
            row = (WIDE_SPACE * 2).join(c for c in cells if c)
            if row.strip():
                self.out.append(row)

    def paragraph(self, p: ET.Element) -> None:
        ppr = p.find(f"{W}pPr")
        if _on(ppr, f"{W}pageBreakBefore"):
            self.out.append("---")
        style = _val(ppr, f"{W}pStyle")
        level = self.styles.heading_level(style)
        outline = _val(ppr, f"{W}outlineLvl")
        if level is None and outline is not None and outline.isdigit() and int(outline) < 9:
            level = int(outline) + 1

        num = None
        num_pr = ppr.find(f"{W}numPr") if ppr is not None else None
        if num_pr is not None:
            num_id = _val(num_pr, f"{W}numId")
            if num_id:
                num = (num_id, int(_val(num_pr, f"{W}ilvl") or 0))
        elif level is None:
            num = self.styles.numbering(style)
        marker = self.numbering.marker(*num) if num else None

        lines, bold = self.runs(p)
        # runs() records display equations and page breaks as their own items.
        first = True
        for item in lines:
            if isinstance(item, tuple):
                self.out.append(item[1])
                first = True
                continue
            text = item.strip()
            if not text:
                continue
            if first and level is not None:
                text = "#" * min(level, 3) + " " + text
            elif first and marker is not None:
                kind, label = marker
                if kind == "bullet":
                    text = "- " + text
                elif kind == "num" or not label.endswith(("、", "．")):
                    text = f"{label} {text}"
                else:
                    text = label + text
            elif first and bold and len(text) <= 40 and not text.endswith(_SENTENCE_END):
                text = "### " + text
            self.out.append(text)
            first = False

    def runs(self, p: ET.Element) -> tuple[list, bool]:
        """Paragraph content as lines (soft breaks split them) and display items.

        Returns ``(items, all_bold)``; an item is a text line or
        ``("block", markdown)`` for display math and page breaks.
        """
        items: list = []
        buf: list[str] = []
        state = {"bold": True, "text": False}

        def flush() -> None:
            items.append("".join(buf))
            buf.clear()

        def walk(el: ET.Element) -> None:
            for child in el:
                tag = child.tag
                if tag == f"{W}r":
                    run(child)
                elif tag in (f"{W}hyperlink", f"{W}ins", f"{W}smartTag", f"{W}fldSimple",
                             f"{W}customXml", f"{W}bdo", f"{W}dir"):
                    walk(child)
                elif tag == f"{W}sdt":
                    content = child.find(f"{W}sdtContent")
                    if content is not None:
                        walk(content)
                elif tag == f"{M}oMath":
                    tex = _tidy_math(self.math.convert(child))
                    if tex:
                        buf.append(f"${tex}$")
                        state["text"] = True
                elif tag == f"{M}oMathPara":
                    flush()
                    for omath in child.iter(f"{M}oMath"):
                        for row in self.math.rows(omath):
                            row = _tidy_math(row)
                            if row:
                                items.append(("block", f"$${row}$$"))

        def run(r: ET.Element) -> None:
            rpr = r.find(f"{W}rPr")
            bold = _on(rpr, f"{W}b") or _on(rpr, f"{W}bCs")
            for child in r:
                tag = child.tag
                if tag == f"{W}t":
                    text = (child.text or "").replace("$", r"\$")
                    if text.strip():
                        state["text"] = True
                        if not bold:
                            state["bold"] = False
                    buf.append(text)
                elif tag == f"{W}tab":
                    buf.append(WIDE_SPACE)
                elif tag in (f"{W}br", f"{W}cr"):
                    if child.get(f"{W}type") == "page":
                        flush()
                        items.append(("block", "---"))
                    else:
                        flush()
                elif tag in (f"{W}drawing", f"{W}pict", f"{W}object"):
                    if child.find(f".//{W}txbxContent") is not None:
                        self.textboxes += 1
                    else:
                        self.images += 1
                elif tag == f"{W}noBreakHyphen":
                    buf.append("-")
                elif tag == f"{W}sym":
                    char = child.get(f"{W}char", "")
                    try:
                        buf.append(chr(int(char, 16)) if char else "")
                    except ValueError:
                        pass

        walk(p)
        flush()
        return items, state["bold"] and state["text"]
