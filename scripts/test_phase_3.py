"""Phase 3 smoke test — relationships + residue + proactive scheduler."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Clean state
for p in [
    REPO_ROOT / "data" / "social" / "relationships.json",
    REPO_ROOT / "data" / "social" / "residue.json",
    REPO_ROOT / "data" / "social" / "presence_state.json",
    REPO_ROOT / "data" / "meta" / "scheduler_state.json",
    REPO_ROOT / "data" / "body" / "endocrine_state.json",
    REPO_ROOT / "data" / "body" / "rpe_state.json",
]:
    if p.exists():
        p.unlink()

from services.orchestrator.body.endocrine import Event, get_endocrine
from services.orchestrator.social.presence import get_presence
from services.orchestrator.social.relationship import (
    get_relationships, OWNER_TRUST_FLOOR, NORMAL_TRUST_CEILING,
)
from services.orchestrator.social.residue import get_residue
from services.orchestrator.meta.scheduler import get_scheduler


OWNER = "test_owner"
FRIEND = "test_friend"


def main() -> None:
    print("=" * 70)
    print("PHASE 3 SMOKE TEST — relationships + residue + scheduler")
    print("=" * 70)

    rels = get_relationships()
    res = get_residue()
    sched = get_scheduler()
    endo = get_endocrine()

    # ── TEST A: relationships — trust dynamics ──
    print("\n[A] Trust dynamics:")
    r_owner = rels.get_or_create(OWNER, is_owner=True)
    r_friend = rels.get_or_create(FRIEND, is_owner=False)
    print(f"  initial: owner trust={r_owner.trust:.3f}  friend trust={r_friend.trust:.3f}")

    # 50 warm interactions on friend
    for _ in range(50):
        rels.note_warm_interaction(FRIEND, is_owner=False)
    print(f"  after 50 warm with friend: trust={rels.get(FRIEND).trust:.3f}  (expect ~0.75)")

    # 5 conflicts with friend
    for _ in range(5):
        rels.note_conflict(FRIEND, is_owner=False)
    print(f"  after 5 conflicts: trust={rels.get(FRIEND).trust:.3f}  (expect ~0.5)")

    # Same conflicts on owner — should bottom out at floor, not zero
    for _ in range(20):
        rels.note_conflict(OWNER, is_owner=True)
    print(f"  after 20 conflicts on OWNER: trust={rels.get(OWNER).trust:.3f}  "
          f"(expect floor {OWNER_TRUST_FLOOR})")

    # ── TEST B: residue decay + application ──
    print("\n[B] Residue dynamics:")
    res.write_residue(FRIEND, "cortisol", 0.15, summary_hint="tense argument")
    res.write_residue(FRIEND, "oxytocin", 0.08, summary_hint="warm makeup")
    initial = res.get_residue_for_user(FRIEND)
    print(f"  initial residue: {initial}")

    # 12h later
    future = time.time() + 12 * 3600
    decayed = res.get_residue_for_user(FRIEND, now_ts=future)
    print(f"  after 12h decay: {decayed}  (expect ~13% of initial — 2 tau halves)")

    # ── TEST C: residue → body event injection ──
    print("\n[C] Residue applied to endocrine on session start:")
    # Reset body to baseline
    for c in endo.components.values():
        c.level = c.baseline
    endo._last_tick_ts = time.time()
    body_before = {n: c.level for n, c in endo.components.items()}
    print(f"  cortisol before: {body_before['cortisol']:.3f}  oxytocin before: {body_before['oxytocin']:.3f}")
    applied = res.apply_residue_to_endocrine(
        FRIEND, endo.ingest_event, Event, is_owner=False
    )
    print(f"  apply returned: {applied}")
    body_after = {n: c.level for n, c in endo.components.items()}
    print(f"  cortisol after:  {body_after['cortisol']:.3f}  (should be higher — tense residue)")
    print(f"  oxytocin after:  {body_after['oxytocin']:.3f}  (should be higher — warm residue)")

    # ── TEST D: scheduler — care drive when owner absent ──
    print("\n[D] Scheduler care drive when owner is absent:")
    pres = get_presence()
    # Note owner activity 90 min ago (already absent)
    now = time.time()
    pres.note_activity(OWNER, is_owner=True, now_ts=now - 90 * 60)
    # Set oxytocin elevated (missing them)
    endo.components["oxytocin"].level = 0.5
    endo._last_tick_ts = now

    # Tick scheduler many times to let care drive accumulate (5-min intervals)
    fired = None
    for i in range(15):  # 15 ticks at 5min each = 75 min
        action = sched.maybe_act(now_ts=now + i * 300)
        if action:
            fired = action
            break
    if fired:
        print(f"  FIRED: drive={fired.drive}  user={fired.user_id}  "
              f"intensity={fired.intensity:.2f}  reason={fired.reason}")
    else:
        print(f"  no action fired after 75 simulated minutes")
        print(f"  drive state: {sched.drive_state()}")

    # ── TEST E: scheduler — sleep block ──
    print("\n[E] Scheduler should NOT fire when asleep:")
    from services.orchestrator.body.sleep import get_sleep, SleepState
    sleep = get_sleep()
    sleep._state.state = SleepState.ASLEEP.value
    # Reset cooldown so it COULD fire if not for sleep
    sched._state.last_action_ts = 0.0
    sched._state.drives = {"care": 1.0, "boredom": 1.0}  # force max drives
    action = sched.maybe_act(now_ts=time.time())
    print(f"  with sleep=ASLEEP and max drives: action = {action}  (should be None)")

    print("\n" + "=" * 70)
    print("PHASE 3 SMOKE TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
