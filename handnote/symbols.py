"""Printable symbol template + scanner -> TTF builder.

Workflow: :func:`build_template` writes a grid PDF where every cell holds a
light grey guide glyph; the writer traces each cell with a dark pen;
:func:`build_font` slices the scan by the template's JSON geometry, traces
the ink into TrueType contours and writes a font carrying those code points.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image, ImageDraw, ImageFont

from .coverage import font_codepoints
from .opentype import add_alternates, read_alternates

#: Upper bound on handwritten sets per character.
MAX_SETS = 8

#: Symbol groups written on the template, in page order.
SYMBOL_GROUPS: dict[str, str] = {
    "拉丁大写": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "拉丁小写": "abcdefghijklmnopqrstuvwxyz",
    "数字": "0123456789",
    "ASCII 标点": "()[]{}<>.,:;!?/\\|*&%#@$^_~'\"+-=",
    "运算": "−×÷±∓√∑∏∫∂∇∞∝⋅·∗",
    "关系": "≠≈≡≤≥≪≫∼≅",
    "集合": "∈∉∋⊂⊃⊆⊇⊊⊋∪∩∖∅",
    "逻辑": "∧∨¬∀∃∴∵",
    "箭头": "→←↔⇒⇐⇔↦↑↓",
    "希腊小写": "αβγδεζηθικλμνξοπρστυφχψω",
    "希腊大写": "ΓΔΘΛΞΠΣΥΦΨΩ",
    "括号角标": "⟨⟩⌈⌉⌊⌋°′″²³ⁿ½¼¾",
    "黑板体": "ℝℕℤℚℂ",
}

GUIDE_RGB = (200, 200, 200)
GRID_RGB = (230, 225, 225)
LABEL_RGB = (236, 236, 236)
HEAD_RGB = (90, 90, 90)


def all_symbols() -> str:
    """Every template character, de-duplicated, in group order."""
    seen: dict[str, None] = {}
    for text in SYMBOL_GROUPS.values():
        for ch in text:
            seen.setdefault(ch, None)
    return "".join(seen)


def group_of(ch: str) -> str:
    """Group label for a character (empty string when unknown)."""
    for name, text in SYMBOL_GROUPS.items():
        if ch in text:
            return name
    return ""


@dataclass(frozen=True)
class Cell:
    index: int
    page: int
    row: int
    col: int
    char: str
    bbox: tuple[int, int, int, int]


@dataclass
class Template:
    """Grid geometry and cells for one printable template."""

    cells: list[Cell]
    page_size: tuple[int, int]
    origin: tuple[int, int]
    cell_px: int
    cols: int
    rows: int
    dpi: int
    pages: int
    guide_missing: list[str]
    sets: int = 1

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "sets": self.sets,
            "dpi": self.dpi,
            "cell_px": self.cell_px,
            "cell_mm": round(self.cell_px / self.dpi * 25.4, 3),
            "cols": self.cols,
            "rows": self.rows,
            "pages": self.pages,
            "page_size_px": list(self.page_size),
            "origin_px": list(self.origin),
            "guide_missing": self.guide_missing,
            "cells": [
                {
                    "index": c.index,
                    "page": c.page,
                    "row": c.row,
                    "col": c.col,
                    "char": c.char,
                    "code": ord(c.char),
                    "bbox": list(c.bbox),
                }
                for c in self.cells
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Template:
        cells = [
            Cell(c["index"], c["page"], c["row"], c["col"], c["char"],
                 tuple(c["bbox"]))
            for c in data["cells"]
        ]
        return cls(
            cells=cells,
            page_size=tuple(data["page_size_px"]),
            origin=tuple(data["origin_px"]),
            cell_px=int(data["cell_px"]),
            cols=int(data["cols"]),
            rows=int(data["rows"]),
            dpi=int(data["dpi"]),
            pages=int(data["pages"]),
            guide_missing=list(data.get("guide_missing", [])),
            sets=int(data.get("sets", 1) or 1),
        )


def _guide_fonts() -> list[Path]:
    from .fonts import CACHE_DIR, SOURCES

    fonts: list[Path] = []
    for key in ("noto", "zhimang"):
        path = CACHE_DIR / SOURCES[key][0]
        if path.is_file():
            fonts.append(path)
    import matplotlib

    dejavu = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    if dejavu.is_file():
        fonts.append(dejavu)
    return fonts


def _pick_font(fonts: Sequence[Path], caches: dict[Path, set[int]], ch: str) -> Path | None:
    for path in fonts:
        if path not in caches:
            try:
                caches[path] = font_codepoints(path)
            except Exception:  # noqa: BLE001 - corrupt font is skippable
                caches[path] = set()
        if ord(ch) in caches[path]:
            return path
    return None


def build_template(
    chars: str | None = None,
    *,
    cell_mm: float = 24.0,
    cols: int = 7,
    rows: int = 10,
    dpi: int = 300,
    margin_mm: float = 15.0,
    header_mm: float = 12.0,
    title: str = "手写数学符号模板",
    sets: int = 1,
) -> tuple[list[Image.Image], Template]:
    """Render the printable grid; returns ``(pages, template)``.

    With ``sets > 1`` the whole grid is repeated once per handwritten set
    (pages are set-major); ``template.pages`` still counts one set.
    """
    text = chars if chars is not None else all_symbols()
    sets = int(min(max(sets, 1), MAX_SETS))
    px_per_mm = dpi / 25.4
    page_w = int(round(210 * px_per_mm))
    page_h = int(round(297 * px_per_mm))
    cell_px = int(round(cell_mm * px_per_mm))
    grid_w = cols * cell_px
    grid_h = rows * cell_px
    if grid_w > page_w - 2 * int(margin_mm * px_per_mm):
        raise ValueError("cell_mm too large for the page width")
    if grid_h + header_mm * px_per_mm > page_h - 2 * margin_mm * px_per_mm:
        raise ValueError("rows * cell_mm too large for the page height")
    left = (page_w - grid_w) // 2
    top = int(round((margin_mm + header_mm) * px_per_mm))

    per_page = cols * rows
    pages_total = max(1, math.ceil(len(text) / per_page))
    fonts = _guide_fonts()
    cmap_cache: dict[Path, set[int]] = {}
    guide_font: dict[str, Path | None] = {}
    missing: list[str] = []
    for ch in text:
        path = _pick_font(fonts, cmap_cache, ch)
        guide_font[ch] = path
        if path is None:
            missing.append(ch)

    cells: list[Cell] = []
    for index, ch in enumerate(text):
        page, slot = divmod(index, per_page)
        row, col = divmod(slot, cols)
        x0 = left + col * cell_px
        y0 = top + row * cell_px
        cells.append(Cell(index, page, row, col, ch, (x0, y0, x0 + cell_px, y0 + cell_px)))

    guide_size = int(cell_px * 0.72)
    head_size = max(12, int(5.2 * px_per_mm))
    small_size = max(9, int(3.2 * px_per_mm))
    line_w = max(1, dpi // 150)
    head_font = _pick_font(fonts, cmap_cache, "手") or (fonts[0] if fonts else None)

    def load(path: Path | None, size: int) -> ImageFont.FreeTypeFont | None:
        if path is None:
            return None
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return None

    head_font_obj = load(head_font, head_size) if head_font else None
    small_font_obj = load(head_font, small_size) if head_font else None

    pages: list[Image.Image] = []
    for set_no, page in ((k, pg) for k in range(sets) for pg in range(pages_total)):
        img = Image.new("RGB", (page_w, page_h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        first = page * per_page + 1
        last = min(len(text), (page + 1) * per_page)
        if head_font_obj:
            heading = f"{title}   {page + 1}/{pages_total}"
            if sets > 1:
                heading += f"   第 {set_no + 1}/{sets} 套"
            draw.text((left, int(top * 0.42)), heading, font=head_font_obj, fill=HEAD_RGB)
            note = f"字符 {first}–{last} · 用黑色笔描写浅灰字 · 每格一字"
            draw.text((left, int(top * 0.68)), note, font=small_font_obj, fill=HEAD_RGB)
        for cell in cells:
            if cell.page != page:
                continue
            x0, y0, x1, y1 = cell.bbox
            draw.rectangle([x0, y0, x1, y1], outline=GRID_RGB, width=line_w)
            glyph_font = load(guide_font.get(cell.char), guide_size)
            if glyph_font:
                draw.text(((x0 + x1) / 2, (y0 + y1) / 2), cell.char,
                          font=glyph_font, fill=GUIDE_RGB, anchor="mm")
            label = f"{cell.index + 1}  U+{ord(cell.char):04X}"
            draw.text((x0 + 4, y0 + 3), label, font=small_font_obj, fill=LABEL_RGB)
        footer = f"cell {cell_mm:g}mm · {cols}×{rows} · {dpi}dpi · handnote"
        draw.text((left, page_h - int(margin_mm * px_per_mm * 0.6)), footer,
                  font=small_font_obj, fill=HEAD_RGB)
        pages.append(img)

    template = Template(cells, (page_w, page_h), (left, top), cell_px,
                        cols, rows, dpi, pages_total, missing, sets)
    return pages, template


def write_template(out_pdf: str | Path, out_json: str | Path | None = None, **kwargs) -> Template:
    """Build the template, save a multi-page PDF, optionally save the spec."""
    pages, template = build_template(**kwargs)
    pdf_path = Path(out_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(pdf_path, format="PDF", save_all=True, append_images=pages[1:],
                  resolution=float(template.dpi))
    if out_json:
        Path(out_json).write_text(
            json.dumps(template.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return template


def _signed_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) / 2.0)


def _emit_contour(pen: TTGlyphPen, points: np.ndarray, corner_deg: float = 62.0) -> None:
    """Append one closed contour, smoothed through edge midpoints."""
    pts = [(float(px), float(py)) for px, py in points]
    n = len(pts)
    if n < 3:
        return
    mids = [((pts[i][0] + pts[(i + 1) % n][0]) / 2.0,
             (pts[i][1] + pts[(i + 1) % n][1]) / 2.0) for i in range(n)]
    pen.moveTo((round(mids[-1][0]), round(mids[-1][1])))
    for i in range(n):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1 = (cur[0] - prev[0], cur[1] - prev[1])
        v2 = (nxt[0] - cur[0], nxt[1] - cur[1])
        n1 = math.hypot(*v1) or 1e-6
        n2 = math.hypot(*v2) or 1e-6
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        turn = 180.0 - math.degrees(math.acos(cos))
        if turn > corner_deg:
            pen.lineTo((round(cur[0]), round(cur[1])))
        else:
            pen.qCurveTo((round(cur[0]), round(cur[1])),
                         (round(mids[i][0]), round(mids[i][1])))
    pen.closePath()


def _cell_glyph(crop: np.ndarray, upem: int, threshold: int) -> tuple[TTGlyphPen, int, int] | None:
    """Trace one cell crop into a glyph; returns (pen, advance, lsb)."""
    ink = (crop < threshold).astype(np.uint8)
    if ink.sum() < 12:
        return None
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    contours, hierarchy = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    h, w = crop.shape
    scale = upem / float(max(h, w))
    cx = w / 2.0
    polys: list[tuple[np.ndarray, bool]] = []
    for i, contour in enumerate(contours):
        if len(contour) < 3:
            continue
        approx = cv2.approxPolyDP(contour, 0.9, True).reshape(-1, 2).astype(np.float64)
        if len(approx) < 3:
            continue
        mapped = np.empty_like(approx)
        mapped[:, 0] = (approx[:, 0] - cx) * scale + upem / 2
        mapped[:, 1] = (0.9 * h - approx[:, 1]) * scale
        is_hole = hierarchy is not None and hierarchy[0][i][3] != -1
        polys.append((mapped, is_hole))
    if not polys:
        return None

    all_pts = np.vstack([p for p, _ in polys])
    dx = 60 - float(all_pts[:, 0].min())
    dy = 60 - float(all_pts[:, 1].min())
    pen = TTGlyphPen(None)
    for points, is_hole in polys:
        points = points + np.array([dx, dy])
        area = _signed_area(points)
        want_positive = not is_hole
        if (area > 0) != want_positive:
            points = points[::-1]
        _emit_contour(pen, points)
    width = float((all_pts[:, 0] + dx).max())
    advance = int(round(width + 60))
    return pen, advance, 60


def natural_key(path: str | Path) -> list:
    """Sort key that orders ``scan-2`` before ``scan-10``."""
    name = Path(path).name.lower()
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", name)]


def _crops_from_scans(scans: Sequence[str | Path], spec: Template,
                      first_page: int = 0) -> dict[int, np.ndarray]:
    """Crop every template cell out of the scanned pages of one set."""
    pages = list(scans)
    images: dict[int, Image.Image] = {}
    try:
        for idx, path in enumerate(pages[first_page:first_page + spec.pages]):
            images[idx] = Image.open(path).convert("L")
        crops: dict[int, np.ndarray] = {}
        for cell in spec.cells:
            page = images.get(cell.page)
            if page is None:
                continue
            x0, y0, x1, y1 = cell.bbox
            if x1 > page.width or y1 > page.height:
                continue
            crops[cell.index] = np.asarray(page.crop((x0, y0, x1, y1)))
        return crops
    finally:
        for image in images.values():
            image.close()


def scan_sets(scans: Sequence[str | Path], spec: Template) -> list[dict[int, np.ndarray]]:
    """Group scans into handwritten sets: every ``spec.pages`` pages is one set."""
    pages = sorted((Path(p) for p in scans), key=natural_key)
    count = min(MAX_SETS, max(1, math.ceil(len(pages) / max(1, spec.pages))))
    return [_crops_from_scans(pages, spec, k * spec.pages) for k in range(count)]


def assemble_font(
    crops: dict[int, np.ndarray],
    template: Template | dict,
    out_path: str | Path,
    *,
    threshold: int = 128,
    upem: int = 1000,
    family: str = "HandNote Symbols",
    extra_sets: Sequence[dict[int, np.ndarray]] = (),
) -> dict:
    """Trace per-cell ink crops into a TTF; returns a build report.

    ``extra_sets`` holds further handwritten sets of the same template. For
    each character the first traced shape becomes the cmap glyph and the
    rest become ``uniXXXX.sN`` alternates reachable through ``rand``/``calt``.
    """
    spec = template if isinstance(template, Template) else Template.from_dict(template)
    glyphs: dict[str, object] = {}
    metrics: dict[str, tuple[int, int]] = {}
    cmap: dict[int, str] = {}
    alternates: dict[str, list[str]] = {}
    built: list[str] = []
    missing: list[str] = []
    all_sets = [crops, *extra_sets][:MAX_SETS]

    for cell in spec.cells:
        shapes = []
        for set_crops in all_sets:
            crop = set_crops.get(cell.index)
            traced = _cell_glyph(crop, upem, threshold) if crop is not None else None
            if traced is not None:
                shapes.append(traced)
        if not shapes:
            missing.append(cell.char)
            continue
        name = f"uni{ord(cell.char):04X}"
        for n, (pen, advance, lsb) in enumerate(shapes):
            glyph_name = name if n == 0 else f"{name}.s{n}"
            glyphs[glyph_name] = pen.glyph()
            metrics[glyph_name] = (advance, lsb)
        if len(shapes) > 1:
            alternates[name] = [f"{name}.s{n}" for n in range(1, len(shapes))]
        cmap[ord(cell.char)] = name
        built.append(cell.char)

    def order_key(glyph_name: str) -> tuple[int, int]:
        base, _, suffix = glyph_name.partition(".s")
        return int(suffix or 0), int(base[3:], 16)

    order = [".notdef"] + sorted(glyphs, key=order_key)
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    notdef = TTGlyphPen(None)
    fb.setupGlyf({".notdef": notdef.glyph(), **glyphs})
    fb.setupHorizontalMetrics({".notdef": (500, 0), **metrics})
    fb.setupHorizontalHeader(ascent=880, descent=-120)
    fb.setupNameTable({
        "familyName": family,
        "styleName": "Regular",
        "uniqueFontIdentifier": f"{family.replace(' ', '')}-Regular",
        "psName": f"{family.replace(' ', '')}-Regular",
        "fullName": family,
        "version": "Version 1.0",
    })
    fb.setupOS2(sTypoAscender=880, sTypoDescender=-120,
                usWinAscent=880, usWinDescent=120)
    fb.setupPost()
    add_alternates(fb.font, alternates)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out))
    return {
        "font": str(out),
        "glyphs": len(built),
        "missing": missing,
        "chars": "".join(built),
        "sets": max([1] + [1 + len(alts) for alts in alternates.values()]),
        "alternates": sum(len(alts) for alts in alternates.values()),
    }


def build_font(
    scans: Sequence[str | Path],
    template: Template | dict,
    out_path: str | Path,
    *,
    threshold: int = 128,
    upem: int = 1000,
    family: str = "HandNote Symbols",
) -> dict:
    """Trace scanned template pages into a TTF; returns a build report.

    Scans are taken in natural filename order; every ``spec.pages`` pages
    form one handwritten set.
    """
    spec = template if isinstance(template, Template) else Template.from_dict(template)
    sets = scan_sets(scans, spec)
    report = assemble_font(sets[0], spec, out_path, threshold=threshold, upem=upem,
                           family=family, extra_sets=sets[1:])
    report["scanned_sets"] = len(sets)
    return report


def simulate_scan(
    pages: Sequence[Image.Image],
    template: Template,
    font_path: str | Path,
    out_paths: Sequence[str | Path],
    *,
    jitter_px: float = 2.0,
    seed: int = 7,
) -> list[Path]:
    """Fill a template with real glyphs to exercise the scan pipeline.

    Writes one JPEG per page and returns their paths.
    """
    rng = np.random.default_rng(seed)
    font = ImageFont.truetype(str(font_path), int(template.cell_px * 0.62))
    written: list[Path] = []
    by_page: dict[int, list[Cell]] = {}
    for cell in template.cells:
        by_page.setdefault(cell.page, []).append(cell)
    for page_idx, image in enumerate(pages):
        img = image.convert("RGB")
        draw = ImageDraw.Draw(img)
        for cell in by_page.get(page_idx, []):
            x0, y0, x1, y1 = cell.bbox
            cx = (x0 + x1) / 2 + rng.normal(0, jitter_px)
            cy = (y0 + y1) / 2 + rng.normal(0, jitter_px)
            draw.text((cx, cy), cell.char, font=font, fill=(20, 20, 26), anchor="mm")
        arr = np.asarray(img).astype(np.float32)
        arr = arr * 0.93 + rng.normal(0, 3.0, arr.shape)
        out = Path(out_paths[page_idx])
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out, quality=92)
        written.append(out)
    return written


def coverage_of(font_path: str | Path, chars: Iterable[str]) -> dict[str, list[str]]:
    """Split ``chars`` into covered and missing for one font."""
    cmap = font_codepoints(font_path)
    have, miss = [], []
    for ch in chars:
        (have if ord(ch) in cmap else miss).append(ch)
    return {"have": have, "missing": miss}


def merge_fonts(
    base_path: str | Path,
    overlay_path: str | Path,
    out_path: str | Path,
    *,
    family_suffix: str = " Math",
) -> dict:
    """Merge overlay glyphs into a base font, keeping base glyphs on conflict."""
    from copy import deepcopy

    from fontTools.ttLib import TTFont

    base = TTFont(str(base_path))
    extra = TTFont(str(overlay_path))
    if "glyf" not in base or "glyf" not in extra:
        raise ValueError("merge requires TrueType (glyf) fonts for both inputs")
    order = list(base.getGlyphOrder())
    base_cmap = base.getBestCmap() or {}
    extra_cmap = extra.getBestCmap() or {}
    glyf = base["glyf"]
    hmtx = base["hmtx"]
    vmtx = base["vmtx"] if "vmtx" in base else None
    extra_alts = read_alternates(extra)
    taken = set(order)
    added: list[str] = []
    skipped: list[str] = []
    mapping: dict[int, str] = {}
    new_alts: dict[str, list[str]] = {}

    def copy_glyph(src_name: str, want: str) -> str:
        new_name = want
        n = 1
        while new_name in taken:
            new_name = f"{want}_{n}"
            n += 1
        glyf.glyphs[new_name] = deepcopy(extra["glyf"][src_name])
        hmtx.metrics[new_name] = extra["hmtx"][src_name]
        if vmtx is not None:
            head = base["head"]
            vmtx.metrics[new_name] = (head.unitsPerEm, 0)
        order.append(new_name)
        taken.add(new_name)
        return new_name

    for code, name in sorted(extra_cmap.items()):
        if code in base_cmap:
            skipped.append(chr(code))
            continue
        new_name = copy_glyph(name, name if name not in taken else f"hn{code:04X}")
        mapping[code] = new_name
        added.append(chr(code))
        alts = extra_alts.get(name, [])
        if alts:
            new_alts[new_name] = [copy_glyph(alt, f"{new_name}.s{n}")
                                  for n, alt in enumerate(alts, 1)]
    base.setGlyphOrder(order)
    base["maxp"].numGlyphs = len(order)
    for subtable in base["cmap"].tables:
        if not subtable.isUnicode():
            continue
        for code, name in mapping.items():
            if code <= 0xFFFF or subtable.format in (12, 13):
                subtable.cmap[code] = name
    add_alternates(base, new_alts)
    if "post" in base:
        base["post"].formatType = 3.0
    base["name"].setName(
        f"{base['name'].getDebugName(1)}{family_suffix}", 1, 3, 1, 0x409
    )
    base["name"].setName(
        f"{base['name'].getDebugName(4)} {family_suffix}".strip(), 4, 3, 1, 0x409
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    base.save(str(out))
    base.close()
    extra.close()
    return {"font": str(out), "added": len(added), "skipped": len(skipped),
            "chars": "".join(added), "kept": "".join(skipped),
            "alternates": sum(len(alts) for alts in new_alts.values())}
