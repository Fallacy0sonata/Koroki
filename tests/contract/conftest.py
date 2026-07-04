"""Contract-suite hermeticity: tests must NEVER touch Koroki's real persistent state.

Root cause of the 2026-07-03 "duplicate journal writes" bug: tests exercised engines
(world events, interest drift, memory) whose code paths reach the module-level
journal()/endocrine singletons — and nobody patched those, so pytest runs wrote 100+
artifact entries into data/koroki/journal/ (her actual diary) and could touch
data/body/endocrine_state.json.

This autouse fixture points every stateful singleton at pytest tmp storage for every
test. Tests that construct their own instances with explicit tmp paths are unaffected.
Manual one-off scripts are NOT covered — they must patch the journal singleton
themselves before calling anything that might log experience events.
"""

import pytest


@pytest.fixture(autouse=True)
def _hermetic_koroki_state(tmp_path, monkeypatch):
    # journal → tmp (the big one: her diary)
    from services.orchestrator.mind import journal as journal_mod
    monkeypatch.setattr(journal_mod, "_INSTANCE",
                        journal_mod.Journal(journal_dir=tmp_path / "journal"))

    # endocrine → tmp state file + fresh engine per test
    from services.orchestrator.body import endocrine as endo_mod
    monkeypatch.setattr(endo_mod, "_STATE_PATH", tmp_path / "endocrine_state.json")
    monkeypatch.setattr(endo_mod, "_INSTANCE", None)

    # interest drift → tmp
    from services.orchestrator.mind import interest_drift as drift_mod
    monkeypatch.setattr(drift_mod, "_INSTANCE",
                        drift_mod.InterestDrift(state_path=tmp_path / "drift.json"))

    # projects → tmp (added after activity-engine tests wrote real projects, 2026-07-03)
    from services.orchestrator.mind import projects as projects_mod
    monkeypatch.setattr(projects_mod, "_INSTANCE",
                        projects_mod.ProjectManager(state_path=tmp_path / "projects.json"))

    yield
