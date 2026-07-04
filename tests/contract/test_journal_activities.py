"""Contract tests for the experience journal + activity engine (mind/journal, mind/activities)."""

import time

from services.orchestrator.mind import journal as journal_mod
from services.orchestrator.mind.activities import ActivityEngine
from services.orchestrator.mind.journal import Journal


def _seed_two_days(j: Journal) -> None:
    # anchor at NOON two days ago so +N-hour offsets never cross midnight
    # (anchoring at `now - 2d` made the test flake when run late in the evening)
    from datetime import datetime, timedelta
    noon = (datetime.now() - timedelta(days=2)).replace(hour=12, minute=0, second=0)
    day_old = noon.timestamp()
    j.log_event("activity", "curled up with a book",
                meta={"name": "reading", "spot": "bed"}, ts=day_old)
    j.log_event("thought", "the city hums differently after midnight", ts=day_old + 1800)
    j.log_event("mood", "warm, a little drowsy", ts=day_old + 3600)
    j.log_event("sleep", "fell asleep", ts=day_old + 9000)


def test_journal_consolidates_finished_days(tmp_path) -> None:
    j = Journal(journal_dir=tmp_path / "journal")
    _seed_two_days(j)
    n = j.consolidate_pending()
    assert n >= 1
    entries = j.recent_entries(3)
    assert entries
    day, text = entries[-1]  # oldest
    assert "reading" in text
    assert "Thoughts that surfaced" in text
    assert "Mood arc" in text


def test_journal_backdated_write_does_not_trigger_rollover(tmp_path) -> None:
    j = Journal(journal_dir=tmp_path / "journal")
    # a backdated event must not race-consolidate the current day
    j.log_event("thought", "old thought", ts=time.time() - 86400 * 3)
    assert j._known_day == journal_mod._local_day()


def test_journal_reconsolidates_when_day_gains_events(tmp_path) -> None:
    j = Journal(journal_dir=tmp_path / "journal")
    old = time.time() - 86400 * 2
    j.log_event("thought", "first", ts=old)
    assert j.consolidate_pending() >= 1
    time.sleep(0.05)
    j.log_event("thought", "late addition", ts=old + 60)
    assert j.consolidate_pending() >= 1
    day = journal_mod._local_day(old)
    text = (tmp_path / "journal" / f"{day}.md").read_text(encoding="utf-8")
    assert "late addition" in text


def test_journal_today_line(tmp_path) -> None:
    j = Journal(journal_dir=tmp_path / "journal")
    j.log_event("activity", "doodling in a sketchbook", meta={"name": "doodling", "spot": "desk"})
    j.log_event("thought", "why do humans keep aquariums?")
    line = j.today_line()
    assert "doodling" in line
    assert "aquariums" in line


def test_activity_engine_picks_and_persists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(journal_mod, "_INSTANCE", Journal(journal_dir=tmp_path / "journal"))
    eng = ActivityEngine(state_path=tmp_path / "activity_state.json")
    picks = set()
    for _ in range(10):
        eng._transition(eng._pick(time.time()), time.time())
        picks.add(eng.current()["name"])
    assert len(picks) >= 3, f"picker looks stuck: {picks}"

    frag = eng.prompt_fragment()
    assert frag.startswith("right now she's ")

    # journal received activity events
    events = journal_mod._INSTANCE.read_day(journal_mod._local_day())
    assert any(e["kind"] == "activity" for e in events)

    # state survives a restart
    eng2 = ActivityEngine(state_path=tmp_path / "activity_state.json")
    assert eng2.current()["name"] == eng.current()["name"]


def test_activity_current_snapshot_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(journal_mod, "_INSTANCE", Journal(journal_dir=tmp_path / "journal"))
    eng = ActivityEngine(state_path=tmp_path / "activity_state.json")
    cur = eng.current()
    for key in ("name", "doing", "spot", "since_ts", "minutes"):
        assert key in cur
