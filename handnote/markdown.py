"""A small Markdown subset -> block list converter.

Supported syntax: ``#``/``##``/``###`` headings, paragraphs, ``-``/``*``
bullets, ``1.`` numbered items, ``$$..$$`` display math, ``$..$`` inline
math, ``\\begin{bmatrix}`` matrices, ``---`` page breaks and fenced code
blocks (rendered as plain text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBER_RE = re.compile(r"^(\d{1,3})[.)]\s+(.*)$")
_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_MATRIX_RE = re.compile(r"\\begin\{(bmatrix|pmatrix|vmatrix)\}(.*?)\\end\{\1\}", re.S)
_FENCE_RE = re.compile(r"^```")


@dataclass(frozen=True)
class Block:
    """One layout unit.

    ``segments`` is a tuple of ``("t", text)`` and ``("m", latex)`` pairs.
    ``payload`` carries matrix data (``prefix``, ``rows``, ``env``).
    """

    kind: str
    segments: tuple[tuple[str, str], ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Plain text of all text segments, used for coverage checks."""
        return "".join(value for kind, value in self.segments if kind == "t")


class ParseError(ValueError):
    """Raised when the Markdown input cannot be parsed."""


def parse(text: str) -> list[Block]:
    """Convert Markdown source into a list of :class:`Block`."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)
    in_fence = False
    fence_buf: list[str] = []

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if _FENCE_RE.match(stripped):
            if in_fence:
                blocks.append(Block("p", (("t", "\n".join(fence_buf)),)))
                fence_buf = []
                in_fence = False
            else:
                in_fence = True
            i += 1
            continue
        if in_fence:
            fence_buf.append(raw)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if _RULE_RE.match(stripped):
            blocks.append(Block("pagebreak"))
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            level = min(len(m.group(1)), 3)
            blocks.append(Block(f"h{level}", parse_inline(m.group(2).strip())))
            i += 1
            continue

        if stripped.startswith("$$"):
            math, i = _read_display_math(lines, i)
            blocks.extend(_display_blocks(math))
            continue

        m = _BULLET_RE.match(stripped)
        if m:
            blocks.append(Block("bullet", (("t", "• "),) + parse_inline(m.group(1).strip())))
            i += 1
            continue

        m = _NUMBER_RE.match(stripped)
        if m:
            marker = f"{m.group(1)}.  "
            blocks.append(Block("num", (("t", marker),) + parse_inline(m.group(2).strip())))
            i += 1
            continue

        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()
            if not stripped:
                i += 1
                continue

        para = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if not nxt or _is_special(nxt):
                break
            para.append(nxt)
            i += 1
        blocks.append(Block("p", parse_inline(_join_lines(para))))

    if in_fence and fence_buf:
        blocks.append(Block("p", (("t", "\n".join(fence_buf)),)))
    return blocks


def _is_special(line: str) -> bool:
    return bool(
        _HEADING_RE.match(line)
        or _BULLET_RE.match(line)
        or _NUMBER_RE.match(line)
        or _RULE_RE.match(line)
        or _FENCE_RE.match(line)
        or line.startswith("$$")
        or line.startswith(">")
    )


def _join_lines(lines: Iterable[str]) -> str:
    out = ""
    for line in lines:
        if out and out[-1].isascii() and out[-1].isalnum() and line[0].isascii() and line[0].isalnum():
            out += " "
        out += line
    return out


def _read_display_math(lines: list[str], i: int) -> tuple[str, int]:
    """Read a ``$$`` block that may span lines. Returns (math, next index)."""
    first = lines[i].strip()
    if len(first) > 4 and first.endswith("$$"):
        return first[2:-2].strip(), i + 1
    body = [first[2:]]
    i += 1
    while i < len(lines):
        line = lines[i]
        if line.strip().endswith("$$"):
            body.append(line.strip()[:-2])
            return "\n".join(body).strip(), i + 1
        body.append(line)
        i += 1
    raise ParseError("unterminated $$ math block starting at line %d" % (i - len(body)))


def _display_blocks(math: str) -> list[Block]:
    """Turn one display-math payload into display and/or matrix blocks."""
    m = _MATRIX_RE.search(math)
    if not m:
        return [Block("display", (("m", math),))]
    prefix = math[: m.start()].strip()
    suffix = math[m.end():].strip()
    if suffix:
        raise ParseError(f"unexpected text after matrix: {suffix!r}")
    rows: list[list[str]] = []
    for row in re.split(r"\\\\", m.group(2)):
        cells = [cell.strip() for cell in row.split("&")]
        if any(cells):
            rows.append(cells)
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return [Block("matrix", (("m", prefix),) if prefix else (),
                  {"prefix": prefix, "rows": rows, "env": m.group(1)})]


def parse_inline(text: str) -> tuple[tuple[str, str], ...]:
    """Split text into text and inline-math segments.

    ``$...$`` becomes a math segment, ``\\$`` a literal dollar sign, and
    ``**bold**``/``*em*``/```code``` markers are dropped.
    """
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    i = 0
    n = len(text)

    def flush() -> None:
        if buf:
            segments.append(("t", _strip_emphasis("".join(buf))))
            buf.clear()

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] == "$":
            buf.append("$")
            i += 2
            continue
        if ch == "$":
            end = _find_closing_dollar(text, i + 1)
            if end == -1:
                buf.append(ch)
                i += 1
                continue
            flush()
            math = text[i + 1:end].strip()
            if math:
                segments.append(("m", math))
            i = end + 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return tuple(segments) if segments else (("t", ""),)


def _find_closing_dollar(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "$":
            return i
        i += 1
    return -1


def _strip_emphasis(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    return text


def load(path: str | Path) -> list[Block]:
    """Read a Markdown file and parse it."""
    return parse(Path(path).read_text(encoding="utf-8"))
