"""OCR metric extraction (services/vision/ocr.py) + ProgressTracker metric defs."""

from game_goals import GENRE_TEMPLATES, ProgressTracker
from services.vision.ocr import extract_metric


def _line(text, x0=0, y0=0, x1=100, y1=20, conf=0.99):
    return {"text": text, "conf": conf, "box": [x0, y0, x1, y1]}


def test_value_in_same_line_after_label():
    lines = [_line("Available Funds: $25.71")]
    assert extract_metric(lines, ["funds"]) == "$25.71"


def test_percent_value():
    lines = [_line("Public Demand: 32%")]
    assert extract_metric(lines, ["demand"]) == "32%"


def test_thousands_separator():
    lines = [_line("Paperclips: 3,847")]
    assert extract_metric(lines, ["paperclips"]) == "3,847"


def test_case_insensitive_label():
    lines = [_line("MONEY 1200")]
    assert extract_metric(lines, ["money"]) == "1200"


def test_label_only_line_value_to_the_right():
    lines = [
        _line("Wire", x0=0, y0=100, x1=60, y1=120),
        _line("750 inches", x0=70, y0=100, x1=180, y1=120),
    ]
    assert extract_metric(lines, ["wire"]) == "750"


def test_no_label_hit_returns_none():
    lines = [_line("Make Paperclip"), _line("Business")]
    assert extract_metric(lines, ["funds", "money"]) is None


def test_no_number_anywhere_returns_none():
    lines = [_line("Funds: pending")]
    assert extract_metric(lines, ["funds"]) is None


def test_value_row_alignment_rejects_distant_rows():
    lines = [
        _line("Score", x0=0, y0=0, x1=50, y1=20),
        _line("999", x0=60, y0=400, x1=100, y1=420),  # different row entirely
    ]
    assert extract_metric(lines, ["score"]) is None


def test_progress_tracker_accepts_dict_metrics():
    tpl = GENRE_TEMPLATES["tycoon"]
    pt = ProgressTracker(list(tpl["metrics"]))
    assert pt.questions == ["the money/cash amount shown on screen"]
    assert pt.metric_defs[0]["labels"][0] == "money"
    assert pt.record(pt.questions[0], "$10") is None      # first sample: no delta
    delta = pt.record(pt.questions[0], "$25")
    assert delta and "$10 -> $25" in delta


def test_progress_tracker_legacy_string_metrics():
    pt = ProgressTracker(["the score shown"])
    assert pt.questions == ["the score shown"]
    assert pt.metric_defs[0]["labels"] == []


def test_all_genre_templates_have_dict_metrics():
    for genre, tpl in GENRE_TEMPLATES.items():
        for m in tpl["metrics"]:
            assert isinstance(m, dict) and "q" in m and "labels" in m, genre
