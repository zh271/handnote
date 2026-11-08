import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from handnote.symbols import build_template, simulate_scan


def _request(base, path, data=None, headers=None, method=None):
    req = urllib.request.Request(base + path, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _multipart(fields, files):
    boundary = "----handnotetest"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    for name, filename, data, ctype in files:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
            + data + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from handnote.webui import server as module

    uploads = tmp_path_factory.mktemp("uploads")
    original = module.UPLOAD_DIR
    module.UPLOAD_DIR = uploads
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    module.UPLOAD_DIR = original


def test_index(server):
    status, headers, body = _request(server, "/")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert b"handnote" in body


def test_static_assets(server):
    for name in ("app.js", "style.css"):
        status, _headers, body = _request(server, f"/static/{name}")
        assert status == 200 and body


def test_state(server):
    status, _headers, body = _request(server, "/api/state")
    assert status == 200
    data = json.loads(body)
    assert data["defaults"]["body_size"] == 25
    assert data["fonts"], "at least one bundled font should be listed"
    assert "∈" in data["symbols"]


def test_render_and_preview(server):
    payload = json.dumps({"markdown": "## 标题\n\n正文 $x=1$\n\n$$a^2+b^2=c^2$$"}).encode()
    status, _headers, body = _request(server, "/api/render", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status == 200, body
    data = json.loads(body)
    assert len(data["pages"]) == 1
    assert data["coverage"]["coverage"] > 0.9

    status, headers, png = _request(server, data["pages"][0]["url"])
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert png.startswith(b"\x89PNG")

    status, headers, pdf = _request(server, data["pdf"])
    assert status == 200 and pdf.startswith(b"%PDF")


def test_render_rejects_bad_config(server):
    payload = json.dumps({"markdown": "x", "config": {"font_path": "/nope.ttf"}}).encode()
    status, _headers, body = _request(server, "/api/render", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status in (400, 500)
    assert b"error" in body


def test_analyze(server):
    payload = json.dumps({"markdown": "设 $x\\in A$"}).encode()
    status, _headers, body = _request(server, "/api/analyze", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status == 200
    report = json.loads(body)
    assert "∈" in [item["char"] for item in report["missing"]]


def test_upload_markdown(server):
    body, ctype = _multipart({}, [("file", "note.md", "# 标题".encode(), "text/markdown")])
    status, _headers, raw = _request(server, "/api/upload?kind=markdown", data=body,
                                     headers={"Content-Type": ctype})
    assert status == 200
    assert json.loads(raw)["text"] == "# 标题"


def test_symbol_template_json(server):
    status, _headers, body = _request(
        server, "/api/symbols/template.json?cell_mm=12&dpi=150&cols=7&rows=10")
    assert status == 200
    spec = json.loads(body)
    assert spec["cell_px"] > 20
    assert len(spec["cells"]) > 100


def test_symbol_build_endpoint(server):
    from handnote.fonts import default_font

    pages, spec = build_template(cell_mm=12, dpi=150)
    scans = simulate_scan(pages, spec, default_font(),
                          [f"/tmp/hn-web-{i}.jpg" for i in range(len(pages))])
    files = [("spec", "spec.json", json.dumps(spec.to_dict()).encode(), "application/json")]
    for i, path in enumerate(scans):
        files.append(("scan", f"scan-{i}.jpg", path.read_bytes(), "image/jpeg"))
    body, ctype = _multipart({}, files)
    status, _headers, raw = _request(server, "/api/symbols/build", data=body,
                                     headers={"Content-Type": ctype})
    assert status == 200, raw
    report = json.loads(raw)
    assert report["glyphs"] > 0
    status, _headers, font = _request(server, report["url"])
    assert status == 200 and font[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")


def test_capture_page(server):
    status, headers, body = _request(server, "/capture")
    assert status == 200
    assert "手写采集" in body.decode("utf-8")
    assert b"/static/capture.js" in body


def test_guide_font(server):
    import io

    from fontTools.ttLib import TTFont

    status, headers, body = _request(server, "/api/guide-font")
    assert status == 200
    assert headers["Content-Type"] == "font/otf"
    assert len(body) > 2000
    cmap = TTFont(io.BytesIO(body)).getBestCmap()
    for ch in "∈∪⊆⊊∨√∑∫απ0A":
        assert ord(ch) in cmap, f"guide font misses {ch}"


def test_capture_endpoint(server):
    spec = json.loads(_request(server, "/api/symbols/template.json?cell_mm=12&dpi=150")[2])
    by_char = {cell["char"]: cell["index"] for cell in spec["cells"]}
    capture = {
        "version": 1,
        "width": 0.05,
        "cells": [
            {"index": by_char["∈"], "strokes": [[[0.1, 0.4], [0.9, 0.6]]]},
            {"index": by_char["∪"], "strokes": [[[0.5, 0.1], [0.5, 0.9]]]},
        ],
    }
    payload = json.dumps({"spec": spec, "capture": capture}).encode()
    status, _headers, raw = _request(server, "/api/symbols/capture", data=payload,
                                     headers={"Content-Type": "application/json"})
    assert status == 200, raw
    report = json.loads(raw)
    assert report["glyphs"] == 2
    assert report["merged_added"] == 2
    status, _headers, font = _request(server, report["url"])
    assert status == 200
    assert font[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")
