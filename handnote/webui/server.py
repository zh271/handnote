"""Local web UI server (stdlib only)."""

from __future__ import annotations

import email.parser
import email.policy
import io
import json
import re
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..capture import build_capture_font
from ..config import RenderConfig
from ..fonts import list_fonts, resolve_font
from ..markdown import ParseError, parse
from ..render import Renderer
from ..symbols import SYMBOL_GROUPS, Template, all_symbols, build_font, build_template, merge_fonts

WEBUI_DIR = Path(__file__).parent
UPLOAD_DIR = Path.cwd() / "var" / "uploads"
MAX_BODY = 48 * 1024 * 1024
CACHE_LIMIT = 8

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class Store:
    """In-memory cache for rendered previews and generated templates."""

    def __init__(self) -> None:
        self.previews: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.templates: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def put_preview(self, entry: dict[str, Any]) -> str:
        key = uuid.uuid4().hex
        self.previews[key] = entry
        while len(self.previews) > CACHE_LIMIT:
            self.previews.popitem(last=False)
        return key

    def put_template(self, key: str, entry: dict[str, Any]) -> None:
        self.templates[key] = entry
        while len(self.templates) > 2:
            self.templates.popitem(last=False)


STORE = Store()


class Handler(BaseHTTPRequestHandler):
    server_version = "handnote/0.1"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        pass

    def _send(self, status: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        return self.rfile.read(length) if length else b""

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEBUI_DIR / "index.html").read_bytes(),
                                  _MIME[".html"])
            if path in ("/capture", "/capture.html"):
                return self._send(200, (WEBUI_DIR / "capture.html").read_bytes(),
                                  _MIME[".html"])
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/guide-font":
                return self._guide_font(query)
            if path == "/api/state":
                return self._json(self._state())
            if path.startswith("/api/preview/"):
                return self._preview(path)
            if path.startswith("/api/pdf/"):
                return self._pdf(path)
            if path == "/api/symbols/template.pdf":
                return self._template_pdf(query, as_json=False)
            if path == "/api/symbols/template.json":
                return self._template_pdf(query, as_json=True)
            if path.startswith("/api/font-file/"):
                return self._font_file(path)
            return self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001 - surface message to the UI
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def _static(self, name: str) -> None:
        target = (WEBUI_DIR / name).resolve()
        if WEBUI_DIR.resolve() not in target.parents or not target.is_file():
            return self._error("not found", 404)
        ctype = _MIME.get(target.suffix, "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)

    def _font_file(self, path: str) -> None:
        name = path.rsplit("/", 1)[-1]
        target = (UPLOAD_DIR / name).resolve()
        if UPLOAD_DIR.resolve() != target.parent or not target.is_file():
            return self._error("not found", 404)
        return self._send(200, target.read_bytes(), "font/ttf",
                          {"Content-Disposition": f'attachment; filename="{name}"'})

    def _guide_font(self, query: dict[str, list[str]]) -> None:
        chars = query.get("chars", [""])[0] or all_symbols()
        if len(chars) > 600:
            return self._error("too many characters requested", 400)
        data = guide_font(chars)
        return self._send(200, data, "font/otf",
                          {"Cache-Control": "public, max-age=86400"})

    def _state(self) -> dict[str, Any]:
        cfg = RenderConfig()
        try:
            cfg.font_path = str(resolve_font(""))
        except FileNotFoundError:
            cfg.font_path = ""
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        return {
            "defaults": cfg.as_dict(),
            "fonts": list_fonts([UPLOAD_DIR]),
            "groups": SYMBOL_GROUPS,
            "symbols": all_symbols(),
            "sample": SAMPLE_MARKDOWN,
        }

    def _preview(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            return self._error("bad preview path", 404)
        entry = STORE.previews.get(parts[2])
        if not entry:
            return self._error("preview expired", 404)
        try:
            index = int(parts[3].split(".")[0])
            image = entry["images"][index]
        except (ValueError, IndexError):
            return self._error("bad page index", 404)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return self._send(200, buffer.getvalue(), "image/png")

    def _pdf(self, path: str) -> None:
        key = path.strip("/").split("/")[-1].split(".")[0]
        entry = STORE.previews.get(key)
        if not entry:
            return self._error("preview expired", 404)
        if not entry.get("pdf"):
            renderer = Renderer(entry["config"])
            entry["pdf"] = renderer.pdf(entry["blocks"], entry["images"])
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(entry["pdf"])))
        self.send_header("Content-Disposition", f'attachment; filename="handnote-{key[:8]}.pdf"')
        self.end_headers()
        self.wfile.write(entry["pdf"])

    def _template_pdf(self, query: dict[str, list[str]], as_json: bool) -> None:
        cell = float(query.get("cell_mm", ["24"])[0])
        cols = int(query.get("cols", ["7"])[0])
        rows = int(query.get("rows", ["10"])[0])
        dpi = int(query.get("dpi", ["300"])[0])
        sets = int(query.get("sets", ["1"])[0])
        chars = query.get("chars", [""])[0] or None
        if chars and len(chars) > 600:
            return self._error("too many characters requested", 400)
        key = f"{cell}-{cols}-{rows}-{dpi}-{sets}-{chars or ''}"
        entry = STORE.templates.get(key)
        if entry is None:
            pages, spec = build_template(chars, cell_mm=cell, cols=cols,
                                         rows=rows, dpi=dpi, sets=sets)
            buffer = io.BytesIO()
            pages[0].save(buffer, format="PDF", save_all=True,
                          append_images=pages[1:], resolution=float(dpi))
            for page in pages:
                page.close()
            entry = {"pdf": buffer.getvalue(), "spec": spec.to_dict()}
            STORE.put_template(key, entry)
        if as_json:
            body = json.dumps(entry["spec"], ensure_ascii=False).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        return self._send(200, entry["pdf"], "application/pdf")

    # -- POST -------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/render":
                return self._render()
            if path == "/api/analyze":
                return self._analyze()
            if path == "/api/upload":
                return self._upload(query)
            if path == "/api/symbols/build":
                return self._symbol_build()
            if path == "/api/symbols/capture":
                return self._symbol_capture()
            return self._error("not found", 404)
        except (ValueError, ParseError) as exc:
            return self._error(f"{type(exc).__name__}: {exc}", 400)
        except Exception as exc:  # noqa: BLE001
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def _payload(self) -> dict[str, Any]:
        raw = self._body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _config(self, payload: dict[str, Any]) -> RenderConfig:
        data = dict(payload.get("config") or {})
        if payload.get("font"):
            data["font_path"] = payload["font"]
        data.setdefault("font_path", "")
        if not data["font_path"]:
            data["font_path"] = str(resolve_font(""))
        return RenderConfig.from_mapping(data)

    def _render(self) -> None:
        payload = self._payload()
        markdown = payload.get("markdown") or ""
        cfg = self._config(payload)
        blocks = parse(markdown)
        started = time.time()
        renderer = Renderer(cfg)
        images = renderer.render(blocks)
        elapsed = time.time() - started
        pdf = renderer.pdf(blocks, images) if payload.get("pdf") else None
        key = STORE.put_preview({"images": images, "blocks": blocks, "pdf": pdf,
                                 "config": cfg})
        report = renderer.coverage(blocks)
        return self._json({
            "id": key,
            "pages": [{"url": f"/api/preview/{key}/{i}.png", "width": im.width,
                       "height": im.height} for i, im in enumerate(images)],
            "pdf": f"/api/pdf/{key}.pdf",
            "warnings": renderer.warnings,
            "coverage": report,
            "elapsed": round(elapsed, 2),
            "font": str(renderer.font_path),
        })

    def _analyze(self) -> None:
        payload = self._payload()
        blocks = parse(payload.get("markdown") or "")
        renderer = Renderer(self._config(payload))
        return self._json(renderer.coverage(blocks))

    def _upload(self, query: dict[str, list[str]]) -> None:
        kind = (query.get("kind") or ["font"])[0]
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._error("expected multipart/form-data")
        parts = parse_multipart(self._body(), ctype)
        if not parts:
            return self._error("no file in request")
        part = parts[0]
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        if kind == "markdown":
            text = part.data.decode("utf-8", errors="replace")
            return self._json({"text": text, "filename": part.filename})
        suffix = Path(part.filename or "upload.bin").suffix.lower()
        allowed = ({".ttf", ".otf", ".ttc"} if kind == "font"
                   else {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".json", ".pdf"})
        if suffix not in allowed:
            return self._error(f"unsupported file type {suffix}")
        name = f"{uuid.uuid4().hex[:12]}{suffix}"
        target = UPLOAD_DIR / name
        target.write_bytes(part.data)
        return self._json({"path": str(target), "name": name, "size": len(part.data)})

    def _symbol_build(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._error("expected multipart/form-data")
        parts = parse_multipart(self._body(), ctype)
        spec_text = next((p.data.decode("utf-8") for p in parts if p.name == "spec"), None)
        scans = [p for p in parts if p.name == "scan"]
        if not spec_text or not scans:
            return self._error("need a spec json and at least one scan")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        def natural(part: Part) -> list:
            name = part.filename or ""
            return [int(tok) if tok.isdigit() else tok.lower()
                    for tok in re.split(r"(\d+)", name)]

        batch = uuid.uuid4().hex[:8]
        for i, part in enumerate(sorted(scans, key=natural)):
            suffix = Path(part.filename or ".png").suffix
            # One batch prefix + zero-padded page number keeps page order on disk.
            target = UPLOAD_DIR / f"scan-{batch}-{i:03d}{suffix}"
            target.write_bytes(part.data)
            paths.append(target)
        spec = Template.from_dict(json.loads(spec_text))
        out = UPLOAD_DIR / f"symbols-{uuid.uuid4().hex[:8]}.ttf"
        keep = next((float(p.data.decode("utf-8")) for p in parts
                     if p.name == "keep" and p.data.strip()), 0.3)
        report = build_font(paths, spec, out, keep=keep)
        merge_into = next((p.data.decode("utf-8").strip() for p in parts
                           if p.name == "merge" and p.data), "")
        replace = next((p.data.decode("utf-8").strip().lower() in ("1", "true", "on")
                        for p in parts if p.name == "replace"), False)
        if merge_into and report["glyphs"] and Path(merge_into).is_file():
            merged = UPLOAD_DIR / f"merged-{uuid.uuid4().hex[:8]}.ttf"
            result = merge_fonts(merge_into, out, merged, replace=replace)
            report["font"] = str(merged)
            report["merged_into"] = merge_into
            report["merged_added"] = result["added"]
            report["merged_replaced"] = result["replaced_chars"]
            report["merged_alternates"] = result["alternates"]
        report["url"] = "/api/font-file/" + Path(report["font"]).name
        return self._json(report)


    def _symbol_capture(self) -> None:
        payload = self._payload()
        spec_data = payload.get("spec")
        capture = payload.get("capture") or payload
        if not spec_data:
            return self._error("need the template spec")
        spec = Template.from_dict(spec_data)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        out = UPLOAD_DIR / f"capture-{uuid.uuid4().hex[:8]}.ttf"
        keep = payload.get("keep")
        pressure = payload.get("pressure")
        report = build_capture_font(capture, spec, out,
                                    keep=0.3 if keep is None else float(keep),
                                    pressure=1.0 if pressure is None else float(pressure))
        merge_into = payload.get("merge")
        if merge_into is False:
            merge_into = ""
        if merge_into is None or merge_into is True:
            try:
                merge_into = str(resolve_font(""))
            except FileNotFoundError:
                merge_into = ""
        if merge_into and report["glyphs"] and Path(str(merge_into)).is_file():
            merged = UPLOAD_DIR / f"merged-{uuid.uuid4().hex[:8]}.ttf"
            result = merge_fonts(str(merge_into), out, merged,
                                 replace=bool(payload.get("replace")))
            report["font"] = str(merged)
            report["merged_into"] = str(merge_into)
            report["merged_added"] = result["added"]
            report["merged_replaced"] = result["replaced_chars"]
            report["merged_alternates"] = result["alternates"]
        report["url"] = "/api/font-file/" + Path(report["font"]).name
        return self._json(report)


class Part:
    def __init__(self, name: str | None, filename: str | None, data: bytes):
        self.name = name
        self.filename = filename
        self.data = data


def parse_multipart(body: bytes, content_type: str) -> list[Part]:
    """Minimal multipart/form-data parser (file + text fields)."""
    header = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n"
    )
    boundary = header.get_boundary()
    if not boundary:
        return []
    delimiter = b"--" + boundary.encode()
    chunks = body.split(delimiter)
    parts: list[Part] = []
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk.lstrip(b"\r\n")
        head, _, data = chunk.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        meta = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            head + b"\r\n\r\n"
        )
        parts.append(Part(meta.get_param("name", header="content-disposition"),
                          meta.get_filename(), data))
    return parts


_GUIDE_CACHE: dict[str, bytes] = {}


def guide_font(chars: str) -> bytes:
    """Subset the guide font down to ``chars`` so the capture page can load it."""
    cached = _GUIDE_CACHE.get(chars)
    if cached:
        return cached
    from fontTools import subset

    from ..fonts import CACHE_DIR, SOURCES

    path = CACHE_DIR / SOURCES["noto"][0]
    if not path.is_file():
        import matplotlib

        path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    options = subset.Options()
    options.drop_tables += ["DSIG"]
    font = subset.load_font(str(path), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes={ord(ch) for ch in chars})
    subsetter.subset(font)
    buffer = io.BytesIO()
    font.save(buffer)
    font.close()
    data = buffer.getvalue()
    _GUIDE_CACHE[chars] = data
    return data


SAMPLE_MARKDOWN = """## 例题：自反闭包的求解

设集合 $A=\\{1,2,3\\}$，定义二元关系：

$$R=\\{(1,2),(2,2),(3,1)\\}$$

1. 求 $R$ 的自反闭包 $r(R)$。
2. 写出关系矩阵 $M_R$。

$$M_R = \\begin{bmatrix} 0 & 1 & 0 \\\\ 0 & 1 & 0 \\\\ 1 & 0 & 0 \\end{bmatrix}$$
"""


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the blocking HTTP server."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"handnote web UI: http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
