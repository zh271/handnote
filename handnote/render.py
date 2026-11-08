"""Handwriting renderer: Markdown in, page images out.

Layout is split in three passes: :meth:`Renderer.layout` measures blocks into
lines, :meth:`Renderer.paginate` groups lines into pages, and
:meth:`Renderer.render_page` draws one page with per-glyph jitter.
"""

from __future__ import annotations

import io
import logging
import math
import random
import threading
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.textpath import TextPath, text_to_path  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from .config import RenderConfig  # noqa: E402
from .coverage import analyze  # noqa: E402
from .fonts import resolve_font  # noqa: E402
from .markdown import MATRIX_RE, Block, matrix_rows, parse  # noqa: E402
from .variants import (  # noqa: E402
    GlyphSets,
    SetPicker,
    handwritten_outline,
    handwritten_sets,
    make_warp,
    warp_points,
)

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_RESAMPLE = Image.Resampling.BICUBIC
_LOCK = threading.RLock()
_REGISTERED: dict[str, str] = {}

#: Characters that must not start a line in Chinese typesetting.
_NO_LINE_START = set("，。、；：？！）》”’】」』%")

_ATOMIC_ASCII = set("._-/'()")

#: Full-width punctuation whose glyph sits inside a mostly empty em box.
#: It is re-spaced from its ink instead of its advance width.
_OPENING_PUNCT = set("（《〈「『【〔“‘")
_CLOSING_PUNCT = set("，。、；：？！）》〉」』】〕”’")
_SQUEEZE_PUNCT = _OPENING_PUNCT | _CLOSING_PUNCT | set("…—～·")
#: Padding (em) kept around squeezed punctuation: ``(before, after)``.
_PUNCT_PAD = {"opening": (0.16, 0.06), "closing": (0.06, 0.16), "middle": (0.08, 0.08)}

_CLOSEPOLY = MplPath.CLOSEPOLY


def _register(path: Path) -> str:
    key = str(path)
    if key in _REGISTERED:
        return _REGISTERED[key]
    fm.fontManager.addfont(key)
    family = fm.FontProperties(fname=key).get_name()
    _REGISTERED[key] = family
    return family


def _tokenize(segments: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Split segments into atomic drawable tokens for line breaking."""
    toks: list[tuple[str, str]] = []
    for kind, value in segments:
        if kind == "m":
            toks.append(("m", value))
            continue
        i = 0
        while i < len(value):
            ch = value[i]
            if ch.isascii() and (ch.isalnum() or ch in _ATOMIC_ASCII):
                j = i
                while j < len(value) and value[j].isascii() and (
                    value[j].isalnum() or value[j] in _ATOMIC_ASCII
                ):
                    j += 1
                toks.append(("t", value[i:j]))
                i = j
            else:
                toks.append(("t", ch))
                i += 1
    return toks


def _drop_math_spaces(toks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop spaces typed around inline math when the other side is CJK.

    ``设集合 $A$ 上`` would otherwise get a word space on top of the formula's
    own gap. Spaces between a formula and Latin text (``where $x$ is``) stay.
    """
    out: list[tuple[str, str]] = []
    for i, tok in enumerate(toks):
        if tok[0] == "t" and tok[1].isspace():
            prev = toks[i - 1] if i > 0 else None
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if (prev and prev[0] == "m") or (nxt and nxt[0] == "m"):
                other = nxt if prev and prev[0] == "m" else prev
                latin = (other is not None and other[0] == "t" and other[1]
                         and other[1][0 if other is nxt else -1].isascii()
                         and other[1][0 if other is nxt else -1].isalnum())
                if not latin:
                    continue
        out.append(tok)
    return out


@dataclass
class _State:
    rng: random.Random
    np_rng: np.random.Generator
    picker: SetPicker | None = None


class Renderer:
    """Draws a block list onto page images using one handwriting font."""

    def __init__(self, cfg: RenderConfig):
        cfg.validate()
        self.cfg = cfg
        self.font_path = resolve_font(cfg.font_path)
        self.family = _register(self.font_path)
        self._pil_fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._math_cache: dict[tuple[str, int], tuple[Image.Image, float]] = {}
        self._glyph_paths: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self._own_fonts: dict[str, bool] = {}
        self._ink_cache: dict[tuple[str, int], tuple[int, int] | None] = {}
        self._set_width_cache: dict[tuple[str, int], float] = {}
        self._math_ink: dict[tuple[str, int], tuple[int, int]] = {}
        self._sets: GlyphSets | None = None
        self.warnings: list[str] = []
        self._apply_mathtext()

    # -- setup ------------------------------------------------------------
    def _apply_mathtext(self) -> None:
        fam = self.family
        matplotlib.rcParams.update(
            {
                "mathtext.fontset": "custom",
                "mathtext.rm": fam,
                "mathtext.it": f"{fam}:italic",
                "mathtext.bf": f"{fam}:bold",
                "mathtext.fallback": "stix",
            }
        )

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def font(self, size: float) -> ImageFont.FreeTypeFont:
        px = max(1, int(round(size)))
        if px not in self._pil_fonts:
            self._pil_fonts[px] = ImageFont.truetype(str(self.font_path), px)
        return self._pil_fonts[px]

    # -- math -------------------------------------------------------------
    def math_image(self, tex: str, px: int) -> tuple[Image.Image, float]:
        """Rasterise one LaTeX snippet; returns (image, baseline offset)."""
        key = (tex, px)
        if key in self._math_cache:
            return self._math_cache[key]
        try:
            result = self._render_math(tex, px)
        except Exception as exc:  # noqa: BLE001 - user LaTeX is untrusted
            self.warn(f"formula fallback: {tex[:48]!r} ({type(exc).__name__})")
            result = self._render_math_plain(tex, px)
        self._math_cache[key] = result
        return result

    def _render_math(self, tex: str, px: int) -> tuple[Image.Image, float]:
        if MATRIX_RE.search(tex):
            return self._compose_math(tex, px)
        ss = 3
        size = max(1, px) * ss
        body = tex.strip()
        if not (body.startswith("$") and body.endswith("$")):
            body = f"${body}$"
        try:
            path = self._math_path(body, size, tex)
        except (AttributeError, TypeError, IndexError, KeyError):
            # matplotlib internals moved; fall back to the plain glyph path.
            path = TextPath((0, 0), body, size=size, prop=fm.FontProperties(size=size))
        bb = path.get_extents()
        if bb.width <= 0 or bb.height <= 0:
            raise ValueError("empty math bbox")
        pad = 4 * ss
        w = int(np.ceil(bb.width + 2 * pad))
        h = int(np.ceil(bb.height + 2 * pad))
        fig = Figure(figsize=(w / 100, h / 100), dpi=100)
        canvas = FigureCanvasAgg(fig)
        fig.patch.set_alpha(0)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, w)
        ax.set_ylim(0, h)
        ax.axis("off")
        ax.patch.set_alpha(0)
        ax.add_patch(
            PathPatch(
                path,
                transform=Affine2D().translate(-bb.x0 + pad, -bb.y0 + pad),
                fc="black",
                ec="black",
                lw=1.0,
            )
        )
        canvas.draw()
        arr = np.asarray(canvas.buffer_rgba()).copy()
        img = Image.fromarray(arr).resize(
            (max(1, round(w / ss)), max(1, round(h / ss))), _RESAMPLE
        )
        baseline = (h - (pad - bb.y0)) / ss
        tint = Image.new("RGBA", img.size, self.cfg.ink_rgb + (255,))
        tint.putalpha(img.getchannel("A"))
        return tint, baseline

    def _math_path(self, body: str, size: float, key: str) -> MplPath:
        """Build the formula outline, swapping each glyph for one of the sets.

        Mirrors ``TextToPath.get_text_path`` for mathtext, but warps every
        glyph with the set chosen for that occurrence.
        """
        unit = text_to_path.FONT_SCALE
        prop = fm.FontProperties(size=unit)
        parsed = text_to_path.mathtext_parser.parse(body, text_to_path.DPI, prop)
        glyphs, rects = parsed[3], parsed[4]
        cfg = self.cfg
        picker = SetPicker(cfg.glyph_sets, random.Random(f"{cfg.seed}|math|{key}"))
        verts: list[np.ndarray] = []
        codes: list[np.ndarray] = []
        items = [g if len(g) == 6 else (g[0], g[1], g[2], g[0].get_char_index(g[2]), g[3], g[4])
                 for g in glyphs]
        # Inside formulas digit_spacing goes between two adjacent digits and
        # symbol_spacing after a comma or semicolon, so "{1,2,3}" opens up
        # behind each comma instead of between a digit and its comma.
        ordered = sorted(items, key=lambda g: g[4])
        gaps: list[tuple[float, float]] = []
        for cur, nxt in zip(ordered, ordered[1:], strict=False):
            ch, after = chr(cur[2]), chr(nxt[2])
            if ch.isdigit() and after.isdigit():
                gaps.append((cur[4], cfg.digit_spacing * cur[1]))
            elif ch in ",;":
                gaps.append((cur[4], cfg.symbol_spacing * cur[1]))

        def shift(x: float) -> float:
            return sum(extra for gx, extra in gaps if gx < x - 1e-6)

        for font, fontsize, ccode, index, ox, oy in items:
            hand = (handwritten_sets(self.font_path, ccode)
                    if cfg.glyph_sets > 1 and self._is_own_font(font.fname) else 0)
            k = picker.pick(ccode, hand or None)
            v, c = self._math_glyph(font, index, ccode, k, hand > 0)
            if len(v) == 0:
                continue
            verts.append(v * (fontsize / unit) + (ox + shift(ox), oy))
            codes.append(c)
        for ox, oy, w, h in rects:
            ox += shift(ox)
            verts.append(np.array([(ox, oy), (ox, oy + h), (ox + w, oy + h),
                                   (ox + w, oy), (ox, oy), (0, 0)], dtype=float))
            codes.append(np.array([MplPath.MOVETO] + [MplPath.LINETO] * 4
                                  + [MplPath.CLOSEPOLY], dtype=MplPath.code_type))
        if not verts:
            return MplPath(np.empty((0, 2)))
        return MplPath(np.concatenate(verts) * (size / unit), np.concatenate(codes))

    def _is_own_font(self, fname: str) -> bool:
        hit = self._own_fonts.get(fname)
        if hit is None:
            try:
                hit = Path(fname).resolve() == self.font_path.resolve()
            except OSError:
                hit = False
            self._own_fonts[fname] = hit
        return hit

    def _math_glyph(self, font, index: int, ccode: int, k: int,
                    handwritten: bool = False) -> tuple[np.ndarray, np.ndarray]:
        """Outline of one mathtext glyph at ``FONT_SCALE`` for set ``k``.

        Handwritten sets come from the font's alternates; other sets warp
        the base outline.
        """
        cfg = self.cfg
        key = (font.fname, index, ccode, k, handwritten, cfg.seed, cfg.glyph_strength)
        hit = self._glyph_paths.get(key)
        if hit is not None:
            return hit
        outline = (handwritten_outline(self.font_path, ccode, k, text_to_path.FONT_SCALE)
                   if handwritten and k > 0 else None)
        if outline is not None:
            result = outline
        elif k == 0 or handwritten:
            from matplotlib import ft2font

            flags = getattr(getattr(ft2font, "LoadFlags", None), "NO_HINTING", None)
            if flags is None:
                flags = ft2font.LOAD_NO_HINTING
            font.clear()
            font.set_size(text_to_path.FONT_SCALE, text_to_path.DPI)
            font.load_glyph(index, flags=flags)
            verts, codes = font.get_path()
            result = (np.asarray(verts, dtype=float), np.asarray(codes))
        else:
            verts, codes = self._math_glyph(font, index, ccode, 0)
            keep = codes != _CLOSEPOLY
            warped = verts.copy()
            if keep.any():
                warped[keep] = warp_points(
                    verts[keep], make_warp(ccode, k, cfg.seed, cfg.glyph_strength),
                    float(text_to_path.FONT_SCALE),
                )
            result = (warped, codes)
        self._glyph_paths[key] = result
        return result

    def _compose_math(self, tex: str, px: int) -> tuple[Image.Image, float]:
        """Lay out a formula that contains matrices, left to right."""
        body = tex.strip().strip("$").strip()
        pieces: list[tuple[Image.Image, float]] = []
        pos = 0
        for m in MATRIX_RE.finditer(body):
            before = body[pos:m.start()].strip()
            if before:
                pieces.append(self.math_image(before, px))
            pieces.append(self._matrix_image(matrix_rows(m.group(2)), m.group(1), px,
                                             m.group(0)))
            pos = m.end()
        after = body[pos:].strip()
        if after:
            pieces.append(self.math_image(after, px))
        gap = max(2, round(px * 0.18))
        ascent = max(base for _, base in pieces)
        descent = max(img.height - base for img, base in pieces)
        width = sum(img.width for img, _ in pieces) + gap * (len(pieces) - 1)
        height = int(math.ceil(ascent + descent))
        out = Image.new("RGBA", (max(1, int(width)), max(1, height)), (0, 0, 0, 0))
        x = 0
        for img, base in pieces:
            out.alpha_composite(img, (int(x), int(round(ascent - base))))
            x += img.width + gap
        return out, ascent

    def _matrix_image(self, rows: list[list[str]], env: str, px: int,
                      key: str) -> tuple[Image.Image, float]:
        """Draw a matrix (cells via mathtext) into its own image."""
        cfg = self.cfg
        rows = rows or [[""]]
        cell_px = max(8, round(px * 0.92))
        imgs = [[self.math_image(cell, cell_px) if cell else None for cell in row]
                for row in rows]
        ncols = max(len(row) for row in rows)
        col_w = []
        for c in range(ncols):
            widths = [row[c][0].width for row in imgs if c < len(row) and row[c]]
            col_w.append(max(widths, default=int(cell_px * 0.6)))
        row_asc, row_desc = [], []
        for row in imgs:
            items = [it for it in row if it]
            row_asc.append(max([b for _, b in items] + [cell_px * 0.95]))
            row_desc.append(max([im.height - b for im, b in items] + [cell_px * 0.3]))
        side = px * 0.42
        col_gap = px * 0.5
        pad_y = px * 0.12
        width = int(math.ceil(2 * side + sum(col_w) + col_gap * (ncols - 1)))
        height = int(math.ceil(sum(row_asc) + sum(row_desc) + 2 * pad_y))
        out = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        y = pad_y
        for r, row in enumerate(imgs):
            baseline = y + row_asc[r]
            x = side
            for c in range(ncols):
                item = row[c] if c < len(row) else None
                if item:
                    img, base = item
                    out.alpha_composite(img, (int(x + (col_w[c] - img.width) / 2),
                                              int(round(baseline - base))))
                x += col_w[c] + col_gap
            y = baseline + row_desc[r]
        draw = ImageDraw.Draw(out)
        rng = random.Random(f"{cfg.seed}|matrix|{key}|{px}")
        color = cfg.ink_rgb + (min(255, cfg.ink_alpha_max - 20),)
        stroke = max(1, round(px / 13))
        for s in (0, 1):
            bx = side * 0.45 if s == 0 else width - side * 0.45
            self._bracket(draw, bx, pad_y * 0.5, height - pad_y * 0.5, s, env,
                          px * 0.8, rng, color, stroke)
        return out, height / 2 + px * 0.3

    def _bracket(self, draw: ImageDraw.ImageDraw, bx: float, ty: float, by: float,
                 side: int, env: str, size: float, rng: random.Random,
                 color: tuple[int, int, int, int], width: int) -> None:
        """Draw one hand-drawn matrix delimiter (left when ``side`` is 0)."""
        if env == "matrix":
            return
        if env == "pmatrix":
            tick = size * 0.30
            pts = []
            for k in range(11):
                frac = k / 10
                offset = math.sin(frac * math.pi) * tick * (1 if side else -1)
                pts.append((bx + offset + rng.gauss(0, 0.35), ty + (by - ty) * frac))
            draw.line(pts, fill=color, width=width, joint="curve")
            return
        tick = size * 0.34
        curvature = 0.0
        pts: list[tuple[float, float]] = []
        for yy in np.linspace(ty, by, 11):
            curvature = 0.72 * curvature + rng.gauss(0, 0.55)
            pts.append((bx + curvature, yy))
        draw.line(pts, fill=color, width=width, joint="curve")
        if env == "bmatrix":
            direction = 1 if side == 0 else -1
            draw.line([(bx, ty), (bx + direction * tick, ty + rng.gauss(0, 0.7))],
                      fill=color, width=width)
            draw.line([(bx, by), (bx + direction * tick, by + rng.gauss(0, 0.7))],
                      fill=color, width=width)

    def _render_math_plain(self, tex: str, px: int) -> tuple[Image.Image, float]:
        text = tex.replace("\\", "").replace("$", "")
        font = self.font(px)
        width = max(1, int(font.getlength(text)) + 8)
        ascent, descent = font.getmetrics()
        height = ascent + descent + 8
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((4, 4 + ascent), text, font=font,
                                 fill=self.cfg.ink_rgb + (255,), anchor="ls")
        return img, ascent + 4

    # -- layout -----------------------------------------------------------
    def heading_size(self, kind: str) -> int:
        return {"h1": self.cfg.h1_size, "h2": self.cfg.h2_size, "h3": self.cfg.h3_size}[kind]

    def matrix_metrics(self, rows: list[list[str]]) -> tuple[float, float, float, float]:
        """Return ``(cell_w, row_h, matrix_w, matrix_h)`` for a matrix."""
        size = self.cfg.body_size
        cell_w = size * 2.0
        row_h = size * 1.8
        ncols = len(rows[0]) if rows else 1
        return cell_w, row_h, cell_w * ncols + size * 1.3, row_h * len(rows)

    def layout(self, blocks: list[Block]) -> list[dict]:
        """Measure blocks into drawable lines."""
        cfg = self.cfg
        lines: list[dict] = []
        for block in blocks:
            kind = block.kind
            if kind == "pagebreak":
                lines.append({"kind": "pagebreak", "h": 0})
                continue
            if kind == "display":
                tex = block.segments[0][1] if block.segments else ""
                img, _ = self.math_image(tex, cfg.display_size)
                lines.append({"kind": "display", "img": img, "h": img.height + 26})
                continue
            if kind == "matrix":
                rows = block.payload.get("rows") or [[""]]
                _, _, _, mat_h = self.matrix_metrics(rows)
                lines.append({
                    "kind": "matrix",
                    "prefix": block.payload.get("prefix", ""),
                    "rows": rows,
                    "env": block.payload.get("env", "bmatrix"),
                    "h": mat_h + 2 * cfg.body_size,
                })
                continue

            size = self.heading_size(kind) if kind in ("h1", "h2", "h3") else cfg.body_size
            indent = 16 if kind == "bullet" else 0
            avail = cfg.page_width - 2 * cfg.margin_x - indent
            toks = _drop_math_spaces(_tokenize(block.segments))
            cur: list[tuple[str, str]] = []
            x = 0.0
            for tok in toks:
                if tok[0] == "m":
                    w = self.math_advance(tok[1], size)[1]
                else:
                    w = sum(self.char_metrics(ch, size)[1] for ch in tok[1])
                if x + w > avail and cur:
                    if tok[0] == "t" and tok[1] and tok[1][0] in _NO_LINE_START:
                        cur.append(tok)
                        x += w
                        continue
                    lines.append(self._text_line(kind, size, indent, cur))
                    cur = []
                    x = 0.0
                cur.append(tok)
                x += w
            if cur:
                lines.append(self._text_line(kind, size, indent, cur))
            if kind in ("h1", "h2"):
                lines.append({"kind": "gap", "h": 6})
        return lines

    # -- spacing ----------------------------------------------------------
    def _ink(self, ch: str, px: int) -> tuple[int, int] | None:
        """Horizontal ink extent of ``ch`` relative to its origin, in pixels."""
        key = (ch, px)
        if key not in self._ink_cache:
            side = px * 3
            img = Image.new("L", (side, side), 0)
            ImageDraw.Draw(img).text((px, px * 2), ch, font=self.font(px), fill=255,
                                     anchor="ls")
            box = img.getbbox()
            self._ink_cache[key] = None if box is None else (box[0] - px, box[2] - px)
        return self._ink_cache[key]

    def _glyph_advance(self, ch: str, size: int, k: int | None) -> float:
        """Advance of ``ch``: set ``k``'s glyph, or the widest set for layout.

        Handwritten sets of one character can differ a lot in width, so the
        pen follows the set actually drawn and line breaking plans for the
        widest one.
        """
        sets = self._sets
        if sets is None or not sets.has(ch):
            return self.font(size).getlength(ch)
        if k is not None:
            return sets.font(k, size).getlength(ch)
        key = (ch, size)
        cache = self._set_width_cache
        if key not in cache:
            cache[key] = max(sets.font(n, size).getlength(ch)
                             for n in range(sets.choices(ch)))
        return cache[key]

    def char_metrics(self, ch: str, size: int, k: int | None = None) -> tuple[float, float]:
        """Return ``(shift, advance)`` for one character at ``size``.

        ``shift`` moves the glyph right of the pen position; ``advance`` is
        how far the pen moves. ``k`` is the glyph set being drawn (``None``
        while measuring). CJK glyphs get ``char_spacing``, digits
        ``digit_spacing``, other symbols ``symbol_spacing`` and Latin letters
        a quarter of ``char_spacing``; full-width punctuation is re-spaced
        from its ink, blended by ``punct_squeeze``.
        """
        cfg = self.cfg
        if ch.isspace():
            return 0.0, self.font(size).getlength(ch)
        advance = self._glyph_advance(ch, size, k)
        if ch in _SQUEEZE_PUNCT and cfg.punct_squeeze > 0:
            ink = self._ink(ch, size)
            if ink is not None:
                left, right = ink
                role = ("opening" if ch in _OPENING_PUNCT
                        else "closing" if ch in _CLOSING_PUNCT else "middle")
                pad_before, pad_after = (p * size for p in _PUNCT_PAD[role])
                t = cfg.punct_squeeze
                before = (1 - t) * left + t * pad_before
                after = (1 - t) * (advance - right) + t * pad_after
                return before - left, before + (right - left) + after
        if ch.isdigit():
            em = cfg.digit_spacing
        elif ch.isascii() and ch.isalpha():
            em = cfg.char_spacing * 0.25
        elif ch.isascii() or unicodedata.category(ch).startswith("S"):
            em = cfg.symbol_spacing
        else:
            em = cfg.char_spacing
        return 0.0, advance + em * size

    def math_advance(self, tex: str, size: int) -> tuple[float, float]:
        """Return ``(shift, advance)`` for an inline formula.

        The formula is placed by its ink, with ``math_gap`` em on each side.
        """
        px = round(size * self.cfg.math_scale)
        key = (tex, px)
        if key not in self._math_ink:
            img, _ = self.math_image(tex, px)
            box = img.getchannel("A").getbbox() or (0, 0, img.width, img.height)
            self._math_ink[key] = (box[0], box[2])
        left, right = self._math_ink[key]
        gap = self.cfg.math_gap * size
        return gap - left, gap + (right - left) + gap

    def _text_line(self, kind: str, size: int, indent: int,
                   toks: list[tuple[str, str]]) -> dict:
        """One text line; grows when an inline formula (e.g. a matrix) is tall."""
        cfg = self.cfg
        h = round(size * cfg.line_height)
        top = size * 1.28
        pad_top = pad_bottom = 0.0
        for tok_kind, value in toks:
            if tok_kind != "m":
                continue
            img, base = self.math_image(value, round(size * cfg.math_scale))
            pad_top = max(pad_top, base + 2 - top)
            pad_bottom = max(pad_bottom, img.height - base + 2 - (h - top))
        pad = math.ceil(pad_top)
        return {"kind": kind, "size": size, "indent": indent, "toks": toks,
                "pad": pad, "h": h + pad + math.ceil(pad_bottom)}

    def paginate(self, lines: list[dict]) -> list[list[dict]]:
        """Split lines into pages, honouring page breaks and keep-with-next."""
        cfg = self.cfg
        pages: list[list[dict]] = []
        cur: list[dict] = []
        y = cfg.margin_top
        limit = cfg.page_height - cfg.margin_bottom
        i = 0
        while i < len(lines):
            line = lines[i]
            if line["kind"] == "pagebreak":
                if cur:
                    pages.append(cur)
                    cur = []
                y = cfg.margin_top
                i += 1
                continue
            need = line["h"]
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if (nxt and line["kind"] in ("p", "bullet", "num")
                    and nxt["kind"] in ("display", "matrix")):
                need += nxt["h"]
            if nxt and line["kind"] in ("h1", "h2", "h3"):
                need += min(nxt["h"], 46)
            if y + need > limit and cur:
                pages.append(cur)
                cur = []
                y = cfg.margin_top
            cur.append(line)
            y += line["h"]
            i += 1
        if cur:
            pages.append(cur)
        return pages

    # -- drawing ----------------------------------------------------------
    def paper(self, state: _State) -> Image.Image:
        cfg = self.cfg
        w, h = cfg.page_width, cfg.page_height
        if not cfg.paper:
            return Image.new("RGBA", (w, h), (255, 255, 255, 255))
        rng = state.np_rng
        field = np.full((h, w, 3), cfg.paper_base + 0.5, dtype=np.float32)
        field += rng.normal(0, max(cfg.paper_noise, 1e-6), (h, w, 1)).astype(np.float32)
        gradient = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
        field -= cfg.paper_gradient * gradient
        return Image.fromarray(np.clip(field, 0, 255).astype(np.uint8)).convert("RGBA")

    def draw_char(
        self,
        canvas: Image.Image,
        x: float,
        baseline: float,
        ch: str,
        size: int,
        state: _State,
        rot: float | None = None,
        dx: float | None = None,
        dy: float | None = None,
    ) -> float:
        """Draw one glyph with jitter; returns its advance width."""
        cfg = self.cfg
        rng = state.rng
        if ch.isspace():
            return self.char_metrics(ch, size)[1]
        k = None
        if self._sets is not None and state.picker is not None and self._sets.has(ch):
            k = state.picker.pick(ord(ch), self._sets.choices(ch))
        shift, pen_advance = self.char_metrics(ch, size, k)
        x += shift
        wobble = int(round(cfg.size_wobble))
        fs = size + (rng.choice([-wobble, 0, 0, 0, wobble]) if wobble > 0 else 0)
        fs = max(1, fs)
        font = self.font(fs) if k is None else self._sets.font(k, fs)
        advance = font.getlength(ch)
        side = max(8, int(size * 2.5))
        tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        alpha = rng.randint(cfg.ink_alpha_min, cfg.ink_alpha_max)
        ImageDraw.Draw(tile).text(
            (side * 0.25, side * 0.80), ch, font=font,
            fill=cfg.ink_rgb + (alpha,), anchor="ls",
        )
        sigma = cfg.jitter_rot if rot is None else rot
        angle = rng.gauss(0, sigma)
        if ch in "，。、；：？！）》”’":
            angle *= 0.15
        if abs(angle) > 0.01:
            tile = tile.rotate(
                angle,
                resample=_RESAMPLE,
                center=(side * 0.25 + advance / 2, side * 0.80 - fs * 0.35),
            )
        ox = rng.gauss(0, (cfg.jitter_dx if dx is None else dx) * size)
        oy = rng.gauss(0, (cfg.jitter_dy if dy is None else dy) * size)
        canvas.alpha_composite(
            tile, (int(x - side * 0.25 + ox), int(baseline - side * 0.80 + oy))
        )
        return pen_advance

    def draw_matrix(
        self,
        canvas: Image.Image,
        prefix: str,
        rows: list[list[str]],
        env: str,
        x_center: float,
        top: float,
        state: _State,
    ) -> None:
        cfg = self.cfg
        size = cfg.body_size
        cell_w, row_h, mat_w, mat_h = self.matrix_metrics(rows)
        gap = size * cfg.matrix_gap
        pre_img, pre_base = (None, 0.0)
        pre_w = 0.0
        if prefix:
            pre_img, pre_base = self.math_image(prefix, size + 10)
            pre_w = pre_img.width + gap
        total = pre_w + mat_w
        x0 = x_center - total / 2
        group_base = top + mat_h / 2 + size * 0.30
        if pre_img is not None:
            canvas.alpha_composite(pre_img, (int(x0), int(group_base - pre_base)))
        mx = x0 + pre_w
        draw = ImageDraw.Draw(canvas)
        rng = state.rng
        color = cfg.ink_rgb + (min(255, cfg.ink_alpha_max - 20),)
        for side in (0, 1):
            bx = mx + (size * 0.30 if side == 0 else mat_w - size * 0.30)
            self._bracket(draw, bx, top - size * 0.15, top + mat_h + size * 0.1, side,
                          env, size, rng, color, 3)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                cx = mx + size * 0.65 + c * cell_w + cell_w / 2
                base = top + r * row_h + row_h * 0.72
                if len(cell) > 1:
                    img, img_base = self.math_image(cell, round(size * 0.95))
                    canvas.alpha_composite(img, (int(cx - img.width / 2), int(base - img_base)))
                elif cell:
                    advance = self.font(size).getlength(cell)
                    self.draw_char(canvas, cx - advance / 2, base, cell, size, state,
                                   rot=cfg.jitter_rot * 0.78, dx=cfg.jitter_dx * 0.7,
                                   dy=cfg.jitter_dy * 0.7)

    def render_page(
        self, lines: list[dict], page_no: int, total: int, state: _State
    ) -> Image.Image:
        cfg = self.cfg
        canvas = self.paper(state)
        y = float(cfg.margin_top)
        for line in lines:
            kind = line["kind"]
            if kind == "gap":
                y += line["h"]
                continue
            if kind == "display":
                img = line["img"]
                canvas.alpha_composite(img, (int((cfg.page_width - img.width) / 2), int(y + 14)))
                y += line["h"]
                continue
            if kind == "matrix":
                self.draw_matrix(canvas, line["prefix"], line["rows"], line["env"],
                                 cfg.page_width / 2, y + 8, state)
                y += line["h"]
                continue
            size = line["size"]
            x = float(cfg.margin_x + line["indent"])
            baseline = y + line.get("pad", 0) + size * 1.28
            rot_scale = 0.55 if kind == "h1" else 1.0
            for tok_kind, value in line["toks"]:
                if tok_kind == "m":
                    img, base = self.math_image(value, round(size * cfg.math_scale))
                    shift, advance = self.math_advance(value, size)
                    canvas.alpha_composite(img, (int(round(x + shift)),
                                                 int(baseline - base)))
                    x += advance
                else:
                    for ch in value:
                        x += self.draw_char(canvas, x, baseline, ch, size, state,
                                            rot=cfg.jitter_rot * rot_scale)
            y += line["h"]
        if cfg.page_numbers:
            label = str(page_no)
            self.draw_char(
                canvas,
                cfg.page_width - cfg.margin_x - 10,
                cfg.page_height - cfg.margin_bottom + 26,
                label,
                max(12, int(cfg.body_size * 0.8)),
                state,
                rot=cfg.jitter_rot * 0.45,
            )
        return canvas

    # -- public API -------------------------------------------------------
    def render(self, blocks: list[Block]) -> list[Image.Image]:
        """Render blocks into page images (RGB, one per page)."""
        with _LOCK:
            self._apply_mathtext()
            self._prepare_sets(blocks)
            lines = self.layout(blocks)
            pages = self.paginate(lines)
            state = _State(random.Random(self.cfg.seed),
                           np.random.default_rng(self.cfg.seed))
            if self._sets is not None:
                state.picker = SetPicker(self.cfg.glyph_sets, state.rng)
            return [self.render_page(page, i + 1, len(pages), state)
                    for i, page in enumerate(pages)]

    def _prepare_sets(self, blocks: list[Block]) -> None:
        """Build per-set fonts for every character ``draw_char`` will draw.

        Runs before layout so line breaking can measure each set's width.
        """
        cfg = self.cfg
        self._set_width_cache = {}
        if cfg.glyph_sets <= 1:
            self._sets = None
            return
        chars: set[str] = set()
        for block in blocks:
            for kind, value in block.segments:
                if kind == "t":
                    chars.update(value)
            for row in block.payload.get("rows", ()):
                chars.update(cell for cell in row if len(cell) == 1)
        if cfg.page_numbers:
            chars.update("0123456789")
        chars = {ch for ch in chars if not ch.isspace()}
        current = self._sets
        if current is not None and {ord(ch) for ch in chars} <= current.codes | current.absent:
            return
        try:
            self._sets = GlyphSets(self.font_path, chars, cfg.glyph_sets,
                                   cfg.glyph_strength, cfg.seed)
        except Exception as exc:  # noqa: BLE001 - odd fonts fall back to one set
            self._sets = None
            self.warn(f"glyph sets disabled: {type(exc).__name__}: {exc}")

    def render_markdown(self, text: str) -> list[Image.Image]:
        return self.render(parse(text))

    def to_pdf(self, images: list[Image.Image]) -> bytes:
        """Encode page images as a PDF at the configured DPI."""
        if not images:
            raise ValueError("no pages to export")
        pages = [im.convert("RGB") for im in images]
        buf = io.BytesIO()
        pages[0].save(
            buf, format="PDF", save_all=True, append_images=pages[1:],
            resolution=float(self.cfg.dpi),
        )
        return buf.getvalue()

    def coverage(self, blocks: list[Block]) -> dict:
        """Glyph coverage report for a document."""
        return analyze(self.font_path, blocks).to_dict()


def render_markdown(text: str, cfg: RenderConfig) -> tuple[list[Image.Image], list[str]]:
    """Convenience wrapper. Returns ``(pages, warnings)``."""
    renderer = Renderer(cfg)
    return renderer.render_markdown(text), renderer.warnings
