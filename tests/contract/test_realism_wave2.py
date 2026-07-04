"""Contract tests for realism wave 2: world events, dreams, interest drift, journal voicing."""

import random
import time

from services.orchestrator.mind import journal as journal_mod
from services.orchestrator.mind.dreams import _template_dream
from services.orchestrator.mind.interest_drift import InterestDrift
from services.orchestrator.mind.journal import Journal
from services.orchestrator.world.events import (
    CATALOG,
    WorldEventEngine,
    _hour_in_band,
)


# ── world events ─────────────────────────────────────────────────────────────

class _ForcedRng(random.Random):
    """random() always below any probability → every eligible event fires."""

    def random(self):  # noqa: D102
        return 0.0


def _engine(tmp_path, monkeypatch, weather="clear", hour=12.0, awake=True,
            rng=None) -> WorldEventEngine:
    eng = WorldEventEngine(state_path=tmp_path / "events_state.json",
                           rng=rng or _ForcedRng())
    monkeypatch.setattr(eng, "_safe_weather", lambda: weather)
    monkeypatch.setattr(eng, "_safe_awake", lambda: awake)
    import services.orchestrator.world.events as ev_mod
    monkeypatch.setattr(ev_mod.clock, "hour_of_day", lambda: hour)
    return eng


def test_events_fire_and_reach_journal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(journal_mod, "_INSTANCE", Journal(journal_dir=tmp_path / "journal"))
    eng = _engine(tmp_path, monkeypatch, weather="clear", hour=12.0)
    fired = eng.tick(60.0)
    assert fired, "nothing fired with forced rng"
    names = {f["name"] for f in fired}
    assert "thunderclap" not in names            # needs storm weather
    assert "sunset_glow" not in names            # needs 17-19h
    events = journal_mod._INSTANCE.read_day(journal_mod._local_day())
    assert any(e["kind"] == "world_event" for e in events)


def test_thunderclap_requires_storm_and_ignores_sleep(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path, monkeypatch, weather="storm", hour=3.0, awake=False)
    fired = {f["name"] for f in eng.tick(60.0)}
    assert "thunderclap" in fired                # storms don't care that she sleeps
    assert "neighbor_noise" not in fired         # awake_only + hour band


def test_event_cooldown_persists_across_restart(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path, monkeypatch, weather="storm", hour=22.0)
    assert any(f["name"] == "thunderclap" for f in eng.tick(60.0))
    eng2 = _engine(tmp_path, monkeypatch, weather="storm", hour=22.0)
    assert not any(f["name"] == "thunderclap" for f in eng2.tick(60.0))  # cooldown loaded


def test_weather_transition_emits_event(tmp_path, monkeypatch) -> None:
    class _NeverRng(random.Random):
        def random(self):
            return 1.0  # regular events never fire — isolates the transition event

    eng = _engine(tmp_path, monkeypatch, weather="clear", hour=12.0, rng=_NeverRng())
    eng.tick(60.0)  # records weather baseline
    monkeypatch.setattr(eng, "_safe_weather", lambda: "rain")
    fired = eng.tick(60.0)
    assert any(f["name"] == "weather_rain" for f in fired)


def test_recent_fragment_expires(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path, monkeypatch, weather="storm", hour=22.0)
    eng.tick(60.0)
    assert eng.recent_fragment().startswith("just now, ")
    assert eng.recent_fragment(window_s=0.0) == ""


def test_hour_band_wraps_midnight() -> None:
    assert _hour_in_band(23.0, (21, 26))
    assert _hour_in_band(1.5, (21, 26))
    assert not _hour_in_band(12.0, (21, 26))


def test_catalog_valences_in_range() -> None:
    for ev in CATALOG:
        assert -1.0 <= ev.valence <= 1.0
        assert 0.0 < ev.intensity[0] <= ev.intensity[1] <= 1.0


# ── dreams (template fallback path — LLM path needs the live Brain) ─────────

def test_template_dream_uses_real_fragments() -> None:
    d = _template_dream(["the thunderclap rattled the window", "she practiced a song"])
    assert "thunderclap" in d and "song" in d
    assert _template_dream([]) == ""


# ── interest drift ───────────────────────────────────────────────────────────

def test_drift_reinforces_and_caps(tmp_path) -> None:
    d = InterestDrift(state_path=tmp_path / "drift.json")
    for _ in range(50):
        d.reinforce("games", base=38, importance=1.0)
    w = d.effective_weight("games", 38)
    assert 38 < w <= 38 + 18  # capped at MAX_DELTA


def test_drift_decays_toward_base(tmp_path) -> None:
    d = InterestDrift(state_path=tmp_path / "drift.json")
    d.reinforce("books", base=52, importance=1.0)
    # simulate 10 weeks of no reinforcement
    d._deltas["books"]["ts"] = time.time() - 70 * 86400
    assert d.effective_weight("books", 52) <= 53


def test_drift_persists_across_restart(tmp_path) -> None:
    d = InterestDrift(state_path=tmp_path / "drift.json")
    d.reinforce("music", base=90, importance=0.8)
    d2 = InterestDrift(state_path=tmp_path / "drift.json")
    assert d2.effective_weight("music", 90) >= 91


def test_analyze_message_uses_drifted_weight(tmp_path, monkeypatch) -> None:
    import services.orchestrator.mind.interest_drift as drift_mod
    import services.orchestrator.topic_interests as ti

    d = InterestDrift(state_path=tmp_path / "drift.json")
    monkeypatch.setattr(drift_mod, "_INSTANCE", d)
    name, w0, val = ti.analyze_message("did you play any games lately?")
    assert name == "games" and val == "positive"
    for _ in range(10):
        d.reinforce("games", base=38, importance=1.0)
    _, w1, _ = ti.analyze_message("did you play any games lately?")
    assert w1 > w0


def test_aversions_never_drift() -> None:
    import services.orchestrator.topic_interests as ti
    name, w, val = ti.analyze_message("someone brought a weapon")
    assert val == "negative"
    assert w == ti.base_weight(name)  # negative categories use base weight, always


# ── journal voicing ──────────────────────────────────────────────────────────

def test_recent_entries_prefer_voiced_version(tmp_path) -> None:
    j = Journal(journal_dir=tmp_path / "journal")
    (tmp_path / "journal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "journal" / "2026-07-01.md").write_text("# template", encoding="utf-8")
    (tmp_path / "journal" / "2026-07-01.voiced.md").write_text("# her words", encoding="utf-8")
    (tmp_path / "journal" / "2026-06-30.md").write_text("# only template", encoding="utf-8")
    entries = j.recent_entries(2)
    assert entries[0] == ("2026-07-01", "# her words")
    assert entries[1] == ("2026-06-30", "# only template")
