"""Unit tests for demo_recorder pure helpers (Limbs Stage 0)."""

from demo_recorder import DeltaAggregator, MoveThrottle, build_manifest, even_rect, serialize_key


def test_even_rect_floors_odd_dimensions():
    r = even_rect(10, 20, 1281, 721)
    assert r == {"left": 10, "top": 20, "width": 1280, "height": 720}


def test_even_rect_keeps_even_dimensions():
    r = even_rect(0, 0, 1920, 1080)
    assert r["width"] == 1920 and r["height"] == 1080


class _KeyChar:
    char = "w"


class _KeyNamed:
    char = None
    name = "space"


class _KeyVkOnly:
    char = None
    name = None
    vk = 255


def test_serialize_key_char_name_vk():
    assert serialize_key(_KeyChar()) == "w"
    assert serialize_key(_KeyNamed()) == "space"
    assert serialize_key(_KeyVkOnly()) == "vk_255"


def test_move_throttle_limits_rate():
    th = MoveThrottle(min_interval=0.02)
    assert th.accept(100.000) is True
    assert th.accept(100.005) is False  # inside the window
    assert th.accept(100.021) is True
    assert th.accept(100.030) is False


def test_delta_aggregator_sums_and_flushes_per_window():
    agg = DeltaAggregator(window=0.01)
    agg._last_flush = 100.0  # anchor the window
    assert agg.add(3, -1, 100.002) is None      # inside window: accumulate
    assert agg.add(2, 2, 100.005) is None
    ev = agg.add(1, 0, 100.011)                 # window elapsed: flush the sum
    assert ev == {"t": 100.011, "e": "mr", "dx": 6, "dy": 1}
    assert agg.flush(100.020) is None           # nothing accumulated after flush


def test_delta_aggregator_tail_flush():
    agg = DeltaAggregator(window=0.01)
    agg._last_flush = 100.0
    agg.add(5, 5, 100.001)
    ev = agg.flush(100.002)  # session stop: remainder must not be lost
    assert ev is not None and ev["dx"] == 5 and ev["dy"] == 5


def test_build_manifest_shape():
    m = build_manifest(
        game="sols_rng", window_title="Roblox", rect={"left": 0, "top": 0, "width": 1920, "height": 1080},
        fps=10, codec="h264_nvenc", started=1000.0, ended=1065.5,
        frames=650, events=1200, paused_seconds=5.25,
    )
    assert m["schema"] == 1
    assert m["game"] == "sols_rng"
    assert m["wall_seconds"] == 65.5
    assert m["paused_seconds"] == 5.2
    assert m["frames"] == 650 and m["events"] == 1200
    assert "rect" in m and m["rect"]["width"] == 1920
