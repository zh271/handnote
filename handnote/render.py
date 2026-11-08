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
from matplotlib.textpath import TextPath  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from .config import RenderConfig  # noqa: E402
from .coverage import analyze  # noqa: E402
from .fonts import resolve_font  # noqa: E402
from .markdown import Block, parse  # noqa: E402

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_RESAMPLE = Image.Resampling.BICUBIC
_LOCK = threading.RLock()
_REGISTERED: dict[str, str] = {}

#: Characters that must not start a line in Chinese typesetting.
_NO_LINE_START = set("，。、；：？！）》”’】」』%")

_ATOMIC_ASCII = set("._-/'()")


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


@dataclass
class _State:
    rng: random.Random
    np_rng: np.random.Generator


class Renderer:
    """Draws a block list onto page images using one handwriting font."""

    def __init__(self, cfg: RenderConfig):
        cfg.validate()
        self.cfg = cfg
        self.font_path = resolve_font(cfg.font_path)
        self.family = _register(self.font_path)
        self._pil_fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._math_cache: dict[tuple[str, int], tuple[Image.Image, float]] = {}
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
        ss = 3
        size = max(1, px) * ss
        body = tex.strip()
        if not (body.startswith("$") and body.endswith("$")):
            body = f"${body}$"
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
            toks = _tokenize(block.segments)
            cur: list[tuple[str, str]] = []
            x = 0.0
            for tok in toks:
                if tok[0] == "m":
                    img, _ = self.math_image(tok[1], round(size * cfg.math_scale))
                    w = img.width + 12
                else:
                    w = self.font(size).getlength(tok[1])
                if x + w > avail and cur:
                    if tok[0] == "t" and tok[1] and tok[1][0] in _NO_LINE_START:
                        cur.append(tok)
                        x += w
                        continue
                    lines.append({"kind": kind, "size": size, "indent": indent,
                                  "toks": cur, "h": round(size * cfg.line_height)})
                    cur = []
                    x = 0.0
                cur.append(tok)
                x += w
            if cur:
                lines.append({"kind": kind, "size": size, "indent": indent,
                              "toks": cur, "h": round(size * cfg.line_height)})
            if kind in ("h1", "h2"):
                lines.append({"kind": "gap", "h": 6})
        return lines

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
            return self.font(size).getlength(ch)
        wobble = int(round(cfg.size_wobble))
        fs = size + (rng.choice([-wobble, 0, 0, 0, wobble]) if wobble > 0 else 0)
        fs = max(1, fs)
        font = self.font(fs)
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
        return advance

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
        for side in (0, 1):
            bx = mx + (size * 0.30 if side == 0 else mat_w - size * 0.30)
            ty = top - size * 0.15
            by = top + mat_h + size * 0.1
            color = cfg.ink_rgb + (min(255, cfg.ink_alpha_max - 20),)
            tick = size * 0.30
            if env == "pmatrix":
                pts = []
                for k in range(11):
                    frac = k / 10
                    offset = math.sin(frac * math.pi) * tick * (1 if side else -1)
                    pts.append((bx + offset + rng.gauss(0, 0.35), ty + (by - ty) * frac))
                draw.line(pts, fill=color, width=3, joint="curve")
                continue
            tick = size * 0.34
            curvature = 0.0
            pts: list[tuple[float, float]] = []
            for yy in np.linspace(ty, by, 11):
                curvature = 0.72 * curvature + rng.gauss(0, 0.55)
                pts.append((bx + curvature, yy))
            draw.line(pts, fill=color, width=3, joint="curve")
            if env == "bmatrix":
                direction = 1 if side == 0 else -1
                draw.line([(bx, ty), (bx + direction * tick, ty + rng.gauss(0, 0.7))],
                          fill=color, width=3)
                draw.line([(bx, by), (bx + direction * tick, by + rng.gauss(0, 0.7))],
                          fill=color, width=3)
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
            baseline = y + size * 1.28
            rot_scale = 0.55 if kind == "h1" else 1.0
            for tok_kind, value in line["toks"]:
                if tok_kind == "m":
                    img, base = self.math_image(value, round(size * cfg.math_scale))
                    canvas.alpha_composite(img, (int(x + 6), int(baseline - base)))
                    x += img.width + 12
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
            lines = self.layout(blocks)
            pages = self.paginate(lines)
            state = _State(random.Random(self.cfg.seed),
                           np.random.default_rng(self.cfg.seed))
            return [self.render_page(page, i + 1, len(pages), state)
                    for i, page in enumerate(pages)]

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
