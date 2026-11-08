import pytest

from handnote.config import RenderConfig


def test_defaults_are_valid(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path)})
    assert cfg.body_size == 25
    assert cfg.ink_rgb == (30, 31, 44)


def test_unknown_keys_ignored(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path), "nope": 1})
    assert not hasattr(cfg, "nope")


def test_values_are_clamped(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path), "body_size": 999,
                                     "jitter_rot": -4})
    assert cfg.body_size == 120
    assert cfg.jitter_rot == 0


def test_alpha_bounds_are_swapped(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path),
                                     "ink_alpha_min": 250, "ink_alpha_max": 200})
    assert cfg.ink_alpha_min == 200 and cfg.ink_alpha_max == 250


def test_bad_ink_rejected(font_path):
    with pytest.raises(ValueError):
        RenderConfig.from_mapping({"font_path": str(font_path), "ink": "black"})


def test_missing_font_rejected():
    with pytest.raises(ValueError):
        RenderConfig.from_mapping({"font_path": ""})


def test_round_trip(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path), "seed": 42})
    again = RenderConfig.from_mapping(cfg.as_dict())
    assert again.as_dict() == cfg.as_dict()
