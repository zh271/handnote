"""Corner markers: perspective correction and automatic page order."""

import cv2
import numpy as np
from PIL import Image

from handnote.symbols import (
    Template,
    build_font,
    build_template,
    load_sheets,
    simulate_scan,
)

CHARS = "0123456789ABCDEF∈⊆√"


def _distort(src, dst, *, tilt, scale, flip=False):
    """Mimic a phone photo: perspective skew, rotation, other resolution."""
    img = np.asarray(Image.open(src).convert("L"))
    h, w = img.shape
    out_w, out_h = int(w * scale) + 160, int(h * scale) + 160
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    c, s = np.cos(np.radians(tilt)), np.sin(np.radians(tilt))
    centre = np.float32([w, h]) / 2
    moved = (corners - centre) @ np.float32([[c, s], [-s, c]]) * scale
    moved += np.float32([out_w, out_h]) / 2
    moved[1] += [-40, 25]  # keystone: top edge narrower than the bottom
    moved[0] += [35, 20]
    if flip:
        moved = moved[[2, 3, 0, 1]]
    matrix = cv2.getPerspectiveTransform(corners, moved)
    photo = cv2.warpPerspective(img, matrix, (out_w, out_h), borderValue=210)
    Image.fromarray(photo).save(dst, quality=90)
    return dst


def test_template_has_unique_corner_markers():
    _, spec = build_template(CHARS, sets=2)
    ids = [m["id"] for m in spec.markers]
    assert len(ids) == 4 * spec.pages * spec.sets == len(set(ids))
    w, h = spec.page_size
    for marker in spec.markers:
        for x, y in marker["points"]:
            assert 0 <= x <= w and 0 <= y <= h
    # Markers survive the JSON round trip.
    assert Template.from_dict(spec.to_dict()).markers == spec.markers


def test_distorted_shuffled_scans_build_the_same_font(dejavu_path, tmp_path):
    pages, spec = build_template(CHARS, sets=2)
    # One page per set; fill each printed sheet (with its own markers).
    clean = [simulate_scan([page], spec, dejavu_path, [tmp_path / f"clean-{i}.jpg"],
                           seed=i)[0] for i, page in enumerate(pages)]
    # Second set first, upside down and skewed: file order no longer helps.
    photos = [
        _distort(clean[1], tmp_path / "a.jpg", tilt=4, scale=0.7, flip=True),
        _distort(clean[0], tmp_path / "b.jpg", tilt=-3, scale=0.85),
    ]
    sheets, warped = load_sheets(photos, spec)
    assert warped == 2 and sorted(sheets) == [0, 1]

    reference = build_font(clean[:1], spec, tmp_path / "ref.ttf")
    report = build_font(photos, spec, tmp_path / "fixed.ttf")
    assert report["warped_pages"] == 2
    assert report["scanned_sets"] == 2
    assert report["chars"] == reference["chars"] == CHARS
    assert report["alternates"] == len(CHARS)


def test_template_without_markers_keeps_file_order(dejavu_path, tmp_path):
    pages, spec = build_template(CHARS)
    scans = simulate_scan(pages, spec, dejavu_path, [tmp_path / "scan-1.jpg"])
    legacy = Template.from_dict({**spec.to_dict(), "markers": []})
    report = build_font(scans, legacy, tmp_path / "legacy.ttf")
    assert report["warped_pages"] == 0
    assert report["chars"] == CHARS
