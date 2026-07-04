"""Phase 2B smoke test — presence + memory + body causal chain.

Verifies the full chain documented in docs/phase_2b_prediction_log.md final section:
  user spends time online → sustained_presence → oxytocin rises → felt-state
  warms → memory written with high importance → recall → body echo.

Run from Koroki root:
    .venv\\Scripts\\python.exe scripts\\test_phase_2b.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Reset state for a clean test
for p in [
    REPO_ROOT / "data" / "body" / "endocrine_state.json",
    REPO_ROOT / "data" / "body" / "rpe_state.json",
    REPO_ROOT / "data" / "social" / "presence_state.json",
    REPO_ROOT / "data" / "mind" / "memory_stream.jsonl",
]:
    if p.exists():
        p.unlink()

from services.orchestrator.body.endocrine import Event, get_endocrine
from services.orchestrator.body.interoception import get_felt_state
from services.orchestrator.social.presence import get_presence
from services.orchestrator.mind.memory import get_memory


def reset_body():
    eng = get_endocrine()
    for c in eng.components.values():
        c.level = c.baseline
        c.receptor_sensitivity = 1.0
    eng._last_tick_ts = time.time()


def show_body(label: str) -> None:
    fs = get_felt_state()
    raw = fs._raw
    print(f"\n[{label}]")
    print(f"  cort={raw['cortisol']:.2f}  oxy={raw['oxytocin']:.2f}  sero={raw['serotonin']:.2f}")
    print(f"  ne={raw['norepinephrine']:.2f}  dopa_p={raw['dopamine_phasic']:+.2f}  mel={raw['melatonin']:.2f}")
    if fs.to_prompt_block():
        for line in fs.to_prompt_block().splitlines():
            print(f"  {line}")


def main() -> None:
    print("=" * 75)
    print("PHASE 2B SMOKE TEST — full causal chain")
    print("=" * 75)

    presence = get_presence()
    memory = get_memory()
    endocrine = get_endocrine()
    reset_body()

    OWNER_ID = "koro_test"
    now = time.time()

    # ── TEST A: Presence sustained_presence over a long owner session ──
    print("\n" + "=" * 75)
    print("TEST A: Sustained-presence drives oxytocin baseline rise over time")
    print("=" * 75)

    show_body("Baseline (no presence yet)")

    print("\n>>> Simulating: owner sends a message at t=0")
    presence.note_activity(OWNER_ID, is_owner=True, now_ts=now)
    endocrine.ingest_event(Event(
        type="user_message:warm", source=OWNER_ID,
        valence=0.5, intensity=0.6,
        tags=["affectionate", "owner_present"],
    ))
    events = presence.maybe_emit_sustained_events(now_ts=now)
    print(f"  sustained_presence events emitted at t=0: {len(events)}")
    show_body("After 1st message, no session yet")

    for elapsed_min in [2, 6, 22, 65]:
        sim_ts = now + elapsed_min * 60
        print(f"\n>>> t = {elapsed_min} min into session; owner sends another message")
        presence.note_activity(OWNER_ID, is_owner=True, now_ts=sim_ts)
        endocrine.tick(force_dt=elapsed_min * 60 if elapsed_min == 2 else (elapsed_min - prev_min) * 60)
        prev_min = elapsed_min
        events = presence.maybe_emit_sustained_events(now_ts=sim_ts)
        print(f"  sustained_presence events emitted: {len(events)}")
        for e in events:
            print(f"    → threshold={e.seconds_into_session}s intensity={e.intensity:.2f}")
            # Ingest into endocrine like chat.py does
            endocrine.ingest_event(Event(
                type="sustained_presence",
                source=e.user_id,
                valence=0.4,
                intensity=e.intensity,
                tags=["sustained_presence", "owner_present", "affectionate"],
            ))
        show_body(f"At t={elapsed_min}m")
    prev_min = 65  # reset for next test

    # ── TEST B: Session resets after idle gap ──
    print("\n" + "=" * 75)
    print("TEST B: Idle gap > 5min resets session")
    print("=" * 75)
    print(f"\n>>> User idle for 7 minutes")
    far_future = now + (65 + 7) * 60
    presence.note_activity(OWNER_ID, is_owner=True, now_ts=far_future)
    state = presence._state[OWNER_ID]
    seconds_in = far_future - state.session_start_ts
    print(f"  After re-activity: session_start moved to now? session_seconds_in={seconds_in:.1f}")
    print(f"  fired_thresholds reset? {state.fired_thresholds_seconds}")

    # ── TEST C: Memory write happens on important moments ──
    print("\n" + "=" * 75)
    print("TEST C: Important moments get memorized; trivial ones don't")
    print("=" * 75)

    reset_body()
    print("\n>>> Trivial message: user says 'mm'")
    body_now = {n: c.level for n, c in endocrine.components.items()}
    written = memory.remember(
        content="user said 'mm'",
        body_state=body_now,
        source_user_id="koro_test",
    )
    print(f"  Written? {written is not None} (expect: None — too trivial)")

    print("\n>>> Important moment: simulate conflict spiking cortisol")
    endocrine.ingest_event(Event(
        type="conflict_event", source="koro_test",
        valence=-0.7, intensity=0.9,
        tags=["conflict"],
    ))
    body_now = {n: c.level for n, c in endocrine.components.items()}
    written = memory.remember(
        content="had a conflict — voice raised, felt unfair",
        body_state=body_now,
        source_user_id="koro_test",
        tags=["conflict"],
    )
    print(f"  Written? {written is not None}")
    if written:
        print(f"  Importance: {written.importance:.3f}  (cortisol at write: {body_now['cortisol']:.2f})")

    print("\n>>> Important moment: warm exchange spiking oxytocin")
    reset_body()
    for _ in range(3):
        endocrine.ingest_event(Event(
            type="warm_message", source="koro_test",
            valence=0.6, intensity=0.8,
            tags=["affectionate", "owner_present"],
        ))
    body_now = {n: c.level for n, c in endocrine.components.items()}
    written = memory.remember(
        content="Koro shared that Yorushika song that hit the same way",
        body_state=body_now,
        source_user_id="koro_test",
        tags=["affectionate", "music"],
    )
    print(f"  Written? {written is not None}")
    if written:
        print(f"  Importance: {written.importance:.3f}  (oxytocin at write: {body_now['oxytocin']:.2f})")

    # ── TEST D: Recall feedback — warm memory echo ──
    print("\n" + "=" * 75)
    print("TEST D: Recall fires body feedback (memory echo)")
    print("=" * 75)
    reset_body()
    show_body("Reset baseline — should be neutral")

    print("\n>>> Trigger memory recall via query")
    results = memory.retrieve("yorushika", top_k=3)
    print(f"  Retrieved {len(results)} memories.")
    for r in results:
        print(f"    score={r.score_total:.3f}  importance={r.score_importance:.2f}  "
              f"recency={r.score_recency:.2f}  relevance={r.score_relevance:.2f}")
        print(f"    → {r.node.content[:80]}")

    print("\n>>> Apply recall feedback")
    for r in results:
        fired = memory.apply_recall_feedback(r.node, endocrine.ingest_event, Event)
        print(f"    Feedback fired for memory id={r.node.id[:8]}? {fired}")

    show_body("After recall feedback (should show warmth/etc.)")

    # ── TEST E: Recall cooldown prevents oscillation ──
    print("\n" + "=" * 75)
    print("TEST E: Recall cooldown blocks rapid re-feedback")
    print("=" * 75)
    if results:
        r0 = results[0].node
        print("\n>>> Try to fire feedback again immediately on same memory")
        fired = memory.apply_recall_feedback(r0, endocrine.ingest_event, Event)
        print(f"  Feedback fired? {fired} (expect: False due to cooldown)")

    print("\n" + "=" * 75)
    print("PHASE 2B SMOKE TEST COMPLETE")
    print("=" * 75)


if __name__ == "__main__":
    main()
