"""Vector page records and a minimal, deterministic PDF writer.

The renderer records what it draws as :class:`VectorPage` items in page
pixels (origin top left, y down). :func:`write_pdf` turns them into a PDF
where every glyph and formula is a filled outline:

* each distinct outline (one glyph of one set, or one formula) becomes a
  Form XObject and is placed with a ``cm`` matrix, so repeated characters
  cost a few bytes each;
* ink alpha goes through ``ExtGState`` entries;
* anything without an outline (rare fallbacks) is embedded as an image.

No timestamps or random IDs are written, so the same pages give the same
bytes.
"""

from __future__ import annotations

import io
import math
import zlib
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

#: Affine matrix ``(a, b, c, d, e, f)``: ``x' = a x + c y + e``, ``y' = b x + d y + f``.
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# matplotlib path codes, kept local to avoid importing matplotlib here.
_STOP, _MOVETO, _LINETO, _CURVE3, _CURVE4, _CLOSEPOLY = 0, 1, 2, 3, 4, 79


def compose(outer: Matrix, inner: Matrix) -> Matrix:
    """Matrix that applies ``inner`` first, then ``outer``."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def translate(dx: float, dy: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(dx), float(dy))


def rotate_about(angle_deg: float, cx: float, cy: float) -> Matrix:
    """Counter-clockwise rotation as seen on a y-down page (like ``Image.rotate``)."""
    t = math.radians(angle_deg)
    cos, sin = math.cos(t), math.sin(t)
    # x' = cos*dx + sin*dy, y' = -sin*dx + cos*dy around (cx, cy).
    return (cos, -sin, sin, cos, cx - cos * cx - sin * cy, cy + sin * cx - cos * cy)


@dataclass(frozen=True)
class Outline:
    """A filled outline in its own units.

    ``stroke`` outlines are also stroked, with the line width chosen by each
    placement (formula glyphs are reused at different sizes).
    """

    verts: np.ndarray
    codes: np.ndarray
    stroke: bool = False
    #: Decimal places written for coordinates (0 suits 1000+ units per em).
    digits: int = 1


@dataclass
class VectorPage:
    """Everything drawn on one page, in page pixels."""

    width: int
    height: int
    ink: tuple[int, int, int]
    #: ``(top_rgb, bottom_rgb)`` paper shades (0-255), or ``None`` for plain white.
    paper: tuple[tuple[float, ...], tuple[float, ...]] | None = None
    #: ``("path", key, matrix, alpha, line_width)``, ``("stroke", points, matrix, alpha,
    #: width)`` or ``("image", rgba_image, matrix)``. Widths are in the item's own units.
    items: list[tuple] = field(default_factory=list)
    #: Printed lines and dots (:class:`handnote.paper.Rule`), under the ink.
    rules: list = field(default_factory=list)
    #: Scanned sheet drawn full-page under everything (embedded as JPEG).
    background: Image.Image | None = None


def shift_items(items: list[tuple], dx: float, dy: float) -> list[tuple]:
    """Return ``items`` moved by ``(dx, dy)``."""
    move = translate(dx, dy)
    out = []
    for item in items:
        kind = item[0]
        if kind in ("path", "stroke"):
            out.append((kind, item[1], compose(move, item[2]), item[3], item[4]))
        else:
            out.append((kind, item[1], compose(move, item[2])))
    return out


# -- PDF encoding -----------------------------------------------------------
def _fmt(value: float, digits: int = 3) -> str:
    text = f"{value:.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _num(value: float) -> str:
    return _fmt(value, 3)


def _matrix_op(m: Matrix) -> str:
    return " ".join(_num(v) for v in m) + " cm"


def path_ops(verts: np.ndarray, codes: np.ndarray, digits: int = 3) -> str:
    """Translate matplotlib path vertices and codes into PDF path operators."""

    def _num(value: float) -> str:
        return _fmt(value, digits)

    out: list[str] = []
    start = cur = (0.0, 0.0)
    i, n = 0, len(codes)
    while i < n:
        code = int(codes[i])
        x, y = float(verts[i][0]), float(verts[i][1])
        if code == _MOVETO:
            out.append(f"{_num(x)} {_num(y)} m")
            start = cur = (x, y)
            i += 1
        elif code == _LINETO:
            out.append(f"{_num(x)} {_num(y)} l")
            cur = (x, y)
            i += 1
        elif code == _CURVE3 and i + 1 < n:
            qx, qy = x, y
            ex, ey = float(verts[i + 1][0]), float(verts[i + 1][1])
            c1 = (cur[0] + 2 / 3 * (qx - cur[0]), cur[1] + 2 / 3 * (qy - cur[1]))
            c2 = (ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey))
            out.append(f"{_num(c1[0])} {_num(c1[1])} {_num(c2[0])} {_num(c2[1])} "
                       f"{_num(ex)} {_num(ey)} c")
            cur = (ex, ey)
            i += 2
        elif code == _CURVE4 and i + 2 < n:
            pts = [(float(verts[i + j][0]), float(verts[i + j][1])) for j in range(3)]
            out.append(" ".join(f"{_num(px)} {_num(py)}" for px, py in pts) + " c")
            cur = pts[2]
            i += 3
        elif code == _CLOSEPOLY:
            out.append("h")
            cur = start
            i += 1
        else:  # STOP or a truncated curve
            i += 1
    return "\n".join(out)


class _Pdf:
    """Collects numbered objects and writes them with a cross-reference table."""

    def __init__(self) -> None:
        self.objects: list[bytes | None] = []

    def reserve(self) -> int:
        self.objects.append(None)
        return len(self.objects)

    def set(self, num: int, body: bytes) -> None:
        self.objects[num - 1] = body

    def add(self, body: bytes) -> int:
        num = self.reserve()
        self.set(num, body)
        return num

    def stream(self, header: str, data: bytes, compress: bool = True) -> int:
        if compress:
            data = zlib.compress(data, 6)
            header += " /Filter /FlateDecode"
        return self.add(b"<< " + header.encode() + f" /Length {len(data)} >>\nstream\n".encode()
                        + data + b"\nendstream")

    def dump(self, root: int) -> bytes:
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for num, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += f"{num} 0 obj\n".encode() + (body or b"null") + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)


def _image_object(pdf: _Pdf, img: Image.Image) -> int:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    alpha = pdf.stream(f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
                       "/ColorSpace /DeviceGray /BitsPerComponent 8",
                       rgba.getchannel("A").tobytes())
    return pdf.stream(f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
                      f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /SMask {alpha} 0 R",
                      rgba.convert("RGB").tobytes())


def _jpeg_object(pdf: _Pdf, img: Image.Image) -> int:
    buffer = io.BytesIO()
    rgb = img.convert("RGB")
    rgb.save(buffer, format="JPEG", quality=88, optimize=True)
    w, h = rgb.size
    return pdf.stream(f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
                      "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                      buffer.getvalue(), compress=False)


def _rule_ops(rules: list, alpha) -> str:
    """Content-stream operators for printed lines and dots, grouped by style."""
    from .paper import label_path  # paper imports matplotlib; only needed for labels

    ops: list[str] = []
    style = None
    for r in rules:
        if r.kind == "text":
            if style is not None:
                ops.append("S Q")
                style = None
            verts, codes, advance = label_path(r.label, r.width)
            moved = verts + (r.pos - advance * r.end, r.start)
            rgb = " ".join(_num(c / 255) for c in r.rgb)
            ops.append(f"q /{alpha(r.alpha)} gs {rgb} rg {path_ops(moved, codes, 2)} f Q")
            continue
        key = (r.rgb, r.alpha, r.width, r.dash, r.kind == "dot")
        if key != style:
            if style is not None:
                ops.append("S Q")
            rgb = " ".join(_num(c / 255) for c in r.rgb)
            dash = f"[{_num(r.dash[0])} {_num(r.dash[1])}] 0 d " if r.dash else ""
            cap = 1 if r.kind == "dot" else 0
            ops.append(f"q /{alpha(r.alpha)} gs {rgb} RG {_num(r.width)} w {cap} J {dash}")
            style = key
        if r.kind == "h":
            ops.append(f"{_num(r.start)} {_num(r.pos)} m {_num(r.end)} {_num(r.pos)} l")
        elif r.kind == "v":
            ops.append(f"{_num(r.pos)} {_num(r.start)} m {_num(r.pos)} {_num(r.end)} l")
        else:
            ops.append(f"{_num(r.pos)} {_num(r.start)} m {_num(r.pos)} {_num(r.start)} l")
    if style is not None:
        ops.append("S Q")
    return "\n".join(ops)


def write_pdf(pages: list[VectorPage], outlines: dict, dpi: float) -> bytes:
    """Encode recorded pages as a vector PDF.

    Args:
        pages: Pages from :meth:`handnote.render.Renderer.render_vector`.
        outlines: ``key -> Outline`` for every ``"path"`` item.
        dpi: Page pixels per inch; sets the physical page size.
    """
    if not pages:
        raise ValueError("no pages to export")
    pdf = _Pdf()
    catalog = pdf.reserve()
    tree = pdf.reserve()
    forms: dict = {}
    alphas: dict[int, str] = {}
    images: dict[str, int] = {}
    shadings: dict[str, int] = {}
    backgrounds: dict[int, str] = {}
    page_nums: list[int] = []
    scale = 72.0 / float(dpi)

    def form(key) -> str:
        name = forms.get(key)
        if name is None:
            shape: Outline = outlines[key]
            pts = shape.verts[shape.codes != _CLOSEPOLY] if len(shape.codes) else shape.verts
            if len(pts):
                lo, hi = pts.min(axis=0), pts.max(axis=0)
                pad = 0.1 * float((hi - lo).max()) + 2 if shape.stroke else 1.0
                x0, y0 = lo - pad
                x1, y1 = hi + pad
            else:
                x0 = y0 = x1 = y1 = 0.0
            body = path_ops(shape.verts, shape.codes, shape.digits)
            body += "\nB" if shape.stroke else "\nf"
            num = pdf.stream(f"/Type /XObject /Subtype /Form /BBox "
                             f"[{_num(x0)} {_num(y0)} {_num(x1)} {_num(y1)}]",
                             body.encode())
            name = f"P{len(forms)}"
            forms[key] = (name, num)
            return name
        return name[0]

    def alpha(value: int) -> str:
        value = int(min(255, max(0, value)))
        if value not in alphas:
            alphas[value] = f"A{value}"
        return alphas[value]

    for page in pages:
        r, g, b = (c / 255 for c in page.ink)
        ink = f"{_num(r)} {_num(g)} {_num(b)}"
        ops = [f"q {_num(scale)} 0 0 {_num(-scale)} 0 {_num(page.height * scale)} cm"]
        if page.paper is not None:
            top, bottom = (" ".join(_num(min(1.0, max(0.0, v / 255))) for v in shade)
                           for shade in page.paper)
            key = f"{top}-{bottom}-{page.height}"
            if key not in shadings:
                fn = pdf.add(f"<< /FunctionType 2 /Domain [0 1] /C0 [{top}] "
                             f"/C1 [{bottom}] /N 1 >>".encode())
                shadings[key] = pdf.add(f"<< /ShadingType 2 /ColorSpace /DeviceRGB "
                                        f"/Coords [0 0 0 {page.height}] /Function {fn} 0 R "
                                        "/Extend [true true] >>".encode())
            ops.append(f"q 0 0 {page.width} {page.height} re W n /S{shadings[key]} sh Q")
        if page.background is not None:
            name = backgrounds.get(id(page.background))
            if name is None:
                name = f"I{len(images)}"
                images[name] = _jpeg_object(pdf, page.background)
                backgrounds[id(page.background)] = name
            ops.append(f"q {page.width} 0 0 {-page.height} 0 {page.height} cm /{name} Do Q")
        if page.rules:
            ops.append(_rule_ops(page.rules, alpha))
        ops.append(f"{ink} rg {ink} RG 1 J 1 j")
        for item in page.items:
            kind = item[0]
            if kind == "path":
                _, key, m, a, width = item
                lw = f"{_num(width)} w " if width > 0 else ""
                ops.append(f"q /{alpha(a)} gs {_matrix_op(m)} {lw}/{form(key)} Do Q")
            elif kind == "stroke":
                _, pts, m, a, width = item
                if len(pts) < 2:
                    continue
                line = " ".join(f"{_num(x)} {_num(y)} {'m' if i == 0 else 'l'}"
                                for i, (x, y) in enumerate(pts))
                ops.append(f"q /{alpha(a)} gs {_matrix_op(m)} {_num(width)} w {line} S Q")
            elif kind == "image":
                _, img, m = item
                name = f"I{len(images)}"
                images[name] = _image_object(pdf, img)
                w, h = img.size
                ops.append(f"q /A255 gs {_matrix_op(compose(m, (w, 0, 0, -h, 0, h)))} "
                           f"/{name} Do Q")
                alpha(255)
        ops.append("Q")
        content = pdf.stream("", "\n".join(ops).encode())
        page_nums.append((content, page))

    xobjects = " ".join(f"/{name} {num} 0 R" for name, num in forms.values())
    xobjects += " " + " ".join(f"/{name} {num} 0 R" for name, num in images.items())
    states = " ".join(f"/{name} << /Type /ExtGState /ca {_num(v / 255)} /CA {_num(v / 255)} >>"
                      for v, name in sorted(alphas.items()))
    shades = " ".join(f"/S{num} {num} 0 R" for num in shadings.values())
    resources = pdf.add(f"<< /ProcSet [/PDF /ImageC] /XObject << {xobjects} >> "
                        f"/ExtGState << {states} >> /Shading << {shades} >> >>".encode())
    kids = []
    for content, page in page_nums:
        w, h = page.width * scale, page.height * scale
        kids.append(pdf.add(f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {_num(w)} "
                            f"{_num(h)}] /Resources {resources} 0 R /Contents {content} 0 R >>"
                            .encode()))
    pdf.set(tree, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                  f"/Count {len(kids)} >>".encode())
    pdf.set(catalog, f"<< /Type /Catalog /Pages {tree} 0 R >>".encode())
    return pdf.dump(catalog)
