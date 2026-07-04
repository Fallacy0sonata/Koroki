"""Smoke test for Phase 1 endocrine + interoception loop.

Run from Koroki root:
    .venv\\Scripts\\python.exe scripts\\test_endocrine.py

Walks through a few realistic event sequences and prints the resulting
felt-state at each step, so we can see whether the causal chain produces
sensible language. Not a unit test — a sanity check.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.orchestrator.body.endocrine import Event, get_endocrine
from services.orchestrator.body.interoception import get_felt_state


def show(label: str) -> None:
    fs = get_felt_state()
    raw = fs._raw or {}
    print(f"\n=== {label} ===")
    print(f"  RAW: cortisol={raw.get('cortisol', 0):.2f}  "
          f"dopa_tonic={raw.get('dopamine_tonic', 0):.2f}  "
          f"dopa_phasic={raw.get('dopamine_phasic', 0):+.2f}  "
          f"oxytocin={raw.get('oxytocin', 0):.2f}")
    block = fs.to_prompt_block()
    if block:
        print(block)
    else:
        print("  (felt-state is neutral — no fragments above thresholds)")


def main() -> None:
    engine = get_endocrine()

    # Reset to known baseline for the demo.
    for comp in engine.components.values():
        comp.level = comp.baseline
    engine._last_tick_ts = time.time()

    show("Baseline (just woke up)")

    # Owner sends a warm message.
    print("\n>>> EVENT: Owner sends warm affectionate message.")
    engine.ingest_event(Event(
        type="owner_warm_message",
        source="koro",
        valence=0.7,
        intensity=0.8,
        tags=["affectionate", "owner_present"],
    ))
    show("After warm owner message")

    # Some idle time passes (simulated 10 minutes).
    print("\n>>> 10 minutes pass with no events...")
    time.sleep(0.1)  # real time, but force tick with explicit dt
    engine.tick(force_dt=600.0)
    show("After 10 minutes of silence")

    # A conflict event.
    print("\n>>> EVENT: Conflict with non-owner user.")
    engine.ingest_event(Event(
        type="conflict_message",
        source="stranger_123",
        valence=-0.6,
        intensity=0.7,
        tags=["conflict"],
    ))
    show("After conflict")

    # Threat resolves — relief.
    print("\n>>> EVENT: Conflict resolved cleanly (relief).")
    engine.ingest_event(Event(
        type="conflict_resolved",
        source="stranger_123",
        valence=0.3,
        intensity=0.5,
        tags=["trust_signal"],
    ))
    show("After resolution")

    # Owner returns after long absence.
    print("\n>>> 30 minutes pass, then owner returns warmly.")
    engine.tick(force_dt=1800.0)
    engine.ingest_event(Event(
        type="owner_returned",
        source="koro",
        valence=0.6,
        intensity=0.9,
        tags=["affectionate", "owner_present", "sustained_presence"],
    ))
    show("After owner returns")

    # An expected reward DIDN'T happen — disappointment.
    # First we set expectation high by giving repeated reward in a state.
    print("\n>>> Building expectation of reward in 'owner_morning_greet' state...")
    for _ in range(5):
        engine.ingest_event(Event(
            type="owner_morning_greet",
            source="koro",
            valence=0.5,
            intensity=0.7,
            tags=["affectionate"],
        ))
    show("After expectation building")

    # Now the expected event doesn't happen — neutral state instead.
    print("\n>>> EVENT: owner_morning_greet expected, neutral state instead.")
    engine.ingest_event(Event(
        type="owner_morning_greet",
        source="koro",
        valence=0.0,  # no reward this time
        intensity=0.5,
    ))
    show("After expected reward didn't come")

    engine.save()
    print("\n=== State saved. Run again to see persistence work. ===")


if __name__ == "__main__":
    main()
