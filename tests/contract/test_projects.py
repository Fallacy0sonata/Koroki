"""Contract tests for multi-day projects (mind/projects.py) + activity integration."""

import time

from services.orchestrator.mind import journal as journal_mod
from services.orchestrator.mind.activities import ActivityEngine, ActivityDef
from services.orchestrator.mind.projects import ProjectManager


def test_touch_starts_and_advances_project(tmp_path) -> None:
    pm = ProjectManager(state_path=tmp_path / "projects.json")
    p1 = pm.touch("book")
    assert p1 is not None and p1.kind == "book" and p1.sessions == 1
    name = p1.name
    p2 = pm.touch("book")
    assert p2.name == name              # same ongoing project
    assert p2.progress > p1.progress or p2.sessions == 2


def test_project_completes_and_starts_fresh(tmp_path) -> None:
    pm = ProjectManager(state_path=tmp_path / "projects.json")
    first = pm.touch("song").name
    for _ in range(30):                  # enough sessions to guarantee completion
        proj = pm.touch("song")
    assert proj is not None
    # after completion, a later touch starts a NEW project
    nxt = pm.touch("song")
    assert nxt.name != first or not nxt.done


def test_completion_is_journaled(tmp_path) -> None:
    events = journal_mod._INSTANCE.read_day(journal_mod._local_day())
    before = len(events)
    pm = ProjectManager(state_path=tmp_path / "projects.json")
    for _ in range(30):
        pm.touch("art")
    events = journal_mod._INSTANCE.read_day(journal_mod._local_day())
    texts = [e["text"] for e in events[before:]]
    assert any(t.startswith("started sketching") for t in texts)
    assert any(t.startswith("finished sketching") for t in texts)


def test_persistence_roundtrip(tmp_path) -> None:
    pm = ProjectManager(state_path=tmp_path / "projects.json")
    name = pm.touch("book").name
    pm2 = ProjectManager(state_path=tmp_path / "projects.json")
    assert pm2.current("book").name == name


def test_snapshot_shape(tmp_path) -> None:
    pm = ProjectManager(state_path=tmp_path / "projects.json")
    pm.touch("book")
    snap = pm.snapshot()
    assert snap and set(snap[0]) == {"kind", "name", "progress", "sessions"}


def test_activity_transition_attaches_project(tmp_path, monkeypatch) -> None:
    import services.orchestrator.mind.projects as proj_mod
    monkeypatch.setattr(proj_mod, "_INSTANCE",
                        ProjectManager(state_path=tmp_path / "projects.json"))
    eng = ActivityEngine(state_path=tmp_path / "activity_state.json")
    reading = next(a for a in __import__(
        "services.orchestrator.mind.activities", fromlist=["CATALOG"]).CATALOG
        if a.name == "reading")
    eng._transition(reading, time.time())
    cur = eng.current()
    assert cur["name"] == "reading"
    assert "—" in cur["doing"] and '"' in cur["doing"]   # project title attached
    frag = eng.prompt_fragment()
    assert '"' in frag                                    # reaches the felt-state line
