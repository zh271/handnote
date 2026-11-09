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
    assert b"/Subtype /Form" in pdf  # vector by default


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


def test_upload_word(server):
    from test_docx import _docx, _p

    table = "<w:tbl><w:tr><w:tc>" + _p("格") + "</w:tc></w:tr></w:tbl>"
    word = _docx(_p("标题", "Heading1") + table)
    body, ctype = _multipart({}, [("file", "note.docx", word, "application/octet-stream")])
    status, _headers, raw = _request(server, "/api/upload?kind=markdown", data=body,
                                     headers={"Content-Type": ctype})
    assert status == 200, raw
    data = json.loads(raw)
    assert data["text"] == "# 标题\n\n格\n" and data["warnings"] == ["1 个表格按行转成了段落"]
    body, ctype = _multipart({}, [("file", "old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
                                   "application/msword")])
    status, _headers, raw = _request(server, "/api/upload?kind=markdown", data=body,
                                     headers={"Content-Type": ctype})
    assert status == 400 and "另存为" in json.loads(raw)["error"]


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
    assert "手写字体" in body.decode("utf-8")
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
    payload = json.dumps({"spec": spec, "capture": capture, "pressure": 1.5}).encode()
    status, _headers, raw = _request(server, "/api/symbols/capture", data=payload,
                                     headers={"Content-Type": "application/json"})
    assert status == 200, raw
    report = json.loads(raw)
    assert report["glyphs"] == 2
    assert report["pressure"] == 1.5
    assert report["merged_added"] == 2
    status, _headers, font = _request(server, report["url"])
    assert status == 200
    assert font[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")


def test_capture_endpoint_with_sets(server):
    spec = json.loads(_request(server, "/api/symbols/template.json?cell_mm=12&dpi=150")[2])
    index = next(cell["index"] for cell in spec["cells"] if cell["char"] == "∈")
    capture = {"cells": [
        {"index": index, "set": 0, "strokes": [[[0.1, 0.4], [0.9, 0.6]]]},
        {"index": index, "set": 1, "strokes": [[[0.1, 0.6], [0.9, 0.4]]]},
    ]}
    # The capture page sends the merge checkbox as a boolean.
    payload = json.dumps({"spec": spec, "capture": capture, "merge": True}).encode()
    status, _headers, raw = _request(server, "/api/symbols/capture", data=payload,
                                     headers={"Content-Type": "application/json"})
    assert status == 200, raw
    report = json.loads(raw)
    assert report["sets"] == 2
    assert report["merged_alternates"] == 1

    payload = json.dumps({"spec": spec, "capture": capture, "merge": False}).encode()
    report = json.loads(_request(server, "/api/symbols/capture", data=payload,
                                 headers={"Content-Type": "application/json"})[2])
    assert "merged_into" not in report


def test_capture_replace_and_font_listed(server):
    spec = json.loads(_request(server, "/api/symbols/template.json?cell_mm=12&dpi=150")[2])
    index = next(cell["index"] for cell in spec["cells"] if cell["char"] == "0")
    capture = {"cells": [{"index": index, "strokes": [[[0.5, 0.1], [0.2, 0.5], [0.5, 0.9],
                                                       [0.8, 0.5], [0.5, 0.1]]]}]}
    payload = json.dumps({"spec": spec, "capture": capture, "merge": True,
                          "replace": True}).encode()
    status, _headers, raw = _request(server, "/api/symbols/capture", data=payload,
                                     headers={"Content-Type": "application/json"})
    assert status == 200, raw
    report = json.loads(raw)
    assert report["merged_replaced"] == "0"
    fonts = json.loads(_request(server, "/api/state")[2])["fonts"]
    assert report["font"] in [font["path"] for font in fonts]


def test_template_json_with_sets(server):
    status, _headers, body = _request(
        server, "/api/symbols/template.json?cell_mm=12&dpi=150&sets=2")
    assert status == 200
    assert json.loads(body)["sets"] == 2


def test_bind_falls_back_when_port_taken():
    from handnote.webui.server import bind

    blocker = bind("127.0.0.1", 0)
    taken = blocker.server_address[1]
    try:
        for tries in (10, 1):  # next free port, then the OS-assigned fallback
            httpd = bind("127.0.0.1", taken, tries=tries)
            try:
                assert httpd.server_address[1] != taken
            finally:
                httpd.server_close()
    finally:
        blocker.server_close()


def test_serve_opens_browser_on_bound_port(monkeypatch, capsys):
    from handnote.webui import server as module

    blocker = module.bind("127.0.0.1", 0)
    taken = blocker.server_address[1]
    opened = []
    monkeypatch.setattr(module.webbrowser, "open", opened.append)

    def stop(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(module._Server, "serve_forever", stop)
    try:
        module.serve("127.0.0.1", taken, open_browser=True)
    finally:
        blocker.server_close()
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")
    assert opened[0] != f"http://127.0.0.1:{taken}/"
    assert opened[0] in capsys.readouterr().out


def test_paper_scan_api(server):
    import io

    from test_paper import _photo

    status, _headers, body = _request(server, "/api/state")
    assert any(p["key"] == "tianzi" for p in json.loads(body)["paper_presets"])

    photo, _ = _photo()
    buffer = io.BytesIO()
    photo.save(buffer, format="PNG")
    data, ctype = _multipart({}, [("file", "sheet.png", buffer.getvalue(), "image/png")])
    status, _headers, body = _request(server, "/api/paper/scan", data=data,
                                      headers={"Content-Type": ctype})
    assert status == 200, body
    scan = json.loads(body)
    assert len(scan["corners"]) == 4
    status, headers, raw = _request(server, scan["preview"])
    assert status == 200 and headers["Content-Type"] == "image/jpeg"

    payload = json.dumps({"id": scan["id"], "corners": scan["corners"],
                          "width": 600, "height": 840}).encode()
    status, _headers, body = _request(server, "/api/paper/apply", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status == 200, body
    applied = json.loads(body)
    assert applied["rules"]["pitch"] > 25
    status, _headers, png = _request(server, applied["url"])
    assert status == 200 and png.startswith(b"\x89PNG")

    config = {"paper_style": "image", "paper_image": applied["path"],
              "rule_pitch": applied["rules"]["pitch"], "rule_top": applied["rules"]["top"]}
    payload = json.dumps({"markdown": "第一行\n\n第二行", "config": config}).encode()
    status, _headers, body = _request(server, "/api/render", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status == 200, body
    assert not json.loads(body)["warnings"]

    config["paper_image"] = __file__
    payload = json.dumps({"markdown": "字", "config": config}).encode()
    status, _headers, body = _request(server, "/api/render", data=payload,
                                      headers={"Content-Type": "application/json"})
    assert status == 400
    assert _request(server, "/api/paper/raw/../../etc")[0] == 404
    assert _request(server, "/api/paper/file/secret.png")[0] == 404


@pytest.fixture
def toy_hanzi():
    from test_hanzi_font import toy_data

    from handnote import hanzi_data

    original = hanzi_data._LOADED
    hanzi_data.set_default(toy_data())
    yield hanzi_data._LOADED
    hanzi_data.set_default(original)


def _post(server, path, body):
    status, _h, raw = _request(server, path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return status, json.loads(raw)


def _wait(server, job):
    import time

    for _ in range(300):
        status = json.loads(_request(server, f"/api/hanzi/job/{job}")[2])
        if status["state"] != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_hanzi_page(server):
    status, _headers, body = _request(server, "/hanzi")
    assert status == 200
    assert b"/static/hanzi.js" in body and b"/static/pad.js" in body
    for name in ("hanzi.js", "pad.js", "pen.js", "hanzi.css"):
        assert _request(server, f"/static/{name}")[0] == 200
    assert b'href="/hanzi"' in _request(server, "/")[2]
    assert b'href="/hanzi"' in _request(server, "/capture")[2]


def test_hanzi_guide_and_check(server, toy_hanzi):
    from test_hanzi_font import capture_of

    status = json.loads(_request(server, "/api/hanzi/status")[2])
    assert status["available"] and len(status["recommended"]) == 100
    info = json.loads(_request(server, "/api/hanzi/char?c=%E6%B1%9F")[2])["江"]
    assert info["strokes"] == 6 and len(info["medians"]) == 6
    first = capture_of(toy_hanzi, "江")["chars"][0]
    code, result = _post(server, "/api/hanzi/check", {"char": "江", "strokes": first["strokes"]})
    assert code == 200 and result["ok"]
    code, result = _post(server, "/api/hanzi/check",
                         {"char": "江", "strokes": first["strokes"][:2]})
    assert not result["ok"] and "标准 6 笔" in result["reason"]


def test_hanzi_model_status_without_torch(server, monkeypatch):
    from handnote.webui import hanzi_api

    monkeypatch.setattr(hanzi_api, "_torch", lambda: False)
    code, status = _post(server, "/api/hanzi/model/status", {"capture": None})
    assert code == 200 and status["torch"] is False and status["model"] is None
    code, err = _post(server, "/api/hanzi/model/train",
                      {"capture": {"chars": [{"char": "江", "strokes": [[[0, 0], [1, 1]]]}]}})
    assert code == 400 and "pip install" in err["error"]


def test_hanzi_model_jobs(server, toy_hanzi, monkeypatch, tmp_path):
    """Train and generate through the API with the model calls stubbed out."""
    pytest.importorskip("torch")
    from test_hanzi_font import capture_of

    from handnote.webui import hanzi_api

    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))
    monkeypatch.setattr(hanzi_api, "_torch", lambda: True)
    capture = capture_of(toy_hanzi, "清工")
    calls = {}

    def fake_train(cap, target, iters, progress):
        progress({"iter": iters, "iters": iters, "loss": 0.0, "val": 0.0, "eta": 0})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"weights")
        target.with_suffix(".json").write_text(json.dumps({"chars": 2, "iters": iters}))
        calls["train"] = iters
        return {"model": str(target), "chars": 2, "seconds": 0.1, "log": [], "warnings": []}

    def fake_generate(cap, model, targets, sets, progress):
        calls["generate"] = (targets, sets)
        progress({"done": 1, "total": 1, "set": 0, "seconds": 0.1})
        extra = capture_of(toy_hanzi, "江")["chars"]
        return {"capture": {**cap, "chars": cap["chars"] + extra},
                "report": {"generated": 1, "written": 2, "skipped": "", "sets": sets}}

    monkeypatch.setattr(hanzi_api, "train_model", fake_train)
    monkeypatch.setattr(hanzi_api, "generate_chars", fake_generate)

    code, err = _post(server, "/api/hanzi/model/generate", {"capture": capture, "targets": "江"})
    assert code == 400 and "训练" in err["error"]

    code, started = _post(server, "/api/hanzi/model/train", {"capture": capture, "iters": 50})
    assert code == 200
    done = _wait(server, started["job"])
    assert done["state"] == "done", done
    assert "log" not in done["report"] and calls["train"] == 50
    code, again = _post(server, "/api/hanzi/model/train", {"capture": capture})
    assert again == {"job": None, "cached": True}
    code, status = _post(server, "/api/hanzi/model/status", {"capture": capture})
    assert status["model"]["chars"] == 2

    code, started = _post(server, "/api/hanzi/model/generate",
                          {"capture": capture, "targets": "江", "sets": 2, "pressure": 0})
    assert code == 200
    done = _wait(server, started["job"])
    assert done["state"] == "done", done
    report = done["report"]
    assert calls["generate"] == ("江", 2)
    assert report["glyphs"] == 3 and report["merged_replaced"] >= 3
    assert _request(server, report["url"])[0] == 200
    code, err = _post(server, "/api/hanzi/model/generate", {"capture": capture, "targets": "abc"})
    assert code == 400
