"""Phase 2C smoke test — energy + sleep + consolidation.

Verifies the full causal chain documented in docs/phase_2c_prediction_log.md.
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
    REPO_ROOT / "data" / "body" / "energy_state.json",
    REPO_ROOT / "data" / "body" / "sleep_state.json",
    REPO_ROOT / "data" / "social" / "presence_state.json",
    REPO_ROOT / "data" / "mind" / "memory_stream.jsonl",
]:
    if p.exists():
        p.unlink()

from services.orchestrator.body.endocrine import Event, get_endocrine
from services.orchestrator.body.energy import get_energy
from services.orchestrator.body.sleep import get_sleep, SleepState
from services.orchestrator.body.interoception import get_felt_state
from services.orchestrator.mind.memory import get_memory
from services.orchestrator.meta.sleep_cycle import get_sleep_cycle


def show(label: str) -> None:
    fs = get_felt_state()
    raw = fs._raw
    print(f"\n[{label}]")
    print(f"  energy={get_energy().level():.2f}  sleep={get_sleep().current_state().value}  "
          f"sleep_debt={get_sleep().sleep_debt_hours():.1f}h")
    print(f"  cort={raw['cortisol']:.2f}  oxy={raw['oxytocin']:.2f}  mel={raw['melatonin']:.2f}")
    if fs.to_prompt_block():
        for line in fs.to_prompt_block().splitlines():
            print(f"  {line}")


def main() -> None:
    print("=" * 75)
    print("PHASE 2C SMOKE TEST — energy + sleep + memory consolidation")
    print("=" * 75)

    # Wire sleep cycle (auto-wires on first call)
    sc = get_sleep_cycle()
    print(f"Sleep cycle wired: {sc._wired}")

    energy = get_energy()
    sleep = get_sleep()
    endocrine = get_endocrine()

    # ── TEST A: Energy drains during wake state ──
    print("\n" + "=" * 75)
    print("TEST A: Energy drains while awake")
    print("=" * 75)
    show("Fresh wake")
    print("\n>>> Simulating 6 hours of interaction...")
    for hours_passed in [1, 2, 4, 6]:
        energy.note_interaction()
        sleep.tick(now_ts=time.time() + hours_passed * 3600)
        print(f"  After {hours_passed}h: energy={energy.level():.2f}")

    # ── TEST B: Sleep transitions when energy low ──
    print("\n" + "=" * 75)
    print("TEST B: Sleep transitions (energy low triggers FALLING_ASLEEP)")
    print("=" * 75)
    # Force energy low to trigger
    energy._state.level = 0.15
    print(f"  Forced energy to {energy.level():.2f}")
    sleep.tick(now_ts=time.time() + 6 * 3600 + 60)
    print(f"  State: {sleep.current_state().value} (expect: falling_asleep)")
    # Advance time enough that FALLING_ASLEEP → ASLEEP
    sleep.tick(now_ts=time.time() + 6 * 3600 + 60 + FALLING_DURATION_PLUS_BUFFER)
    print(f"  After {FALLING_DURATION_PLUS_BUFFER}s in falling_asleep: {sleep.current_state().value} (expect: asleep)")
    show("While ASLEEP")

    # ── TEST C: Memory consolidation pending after sleep ──
    print("\n" + "=" * 75)
    print("TEST C: Memory written before sleep gets consolidation")
    print("=" * 75)
    # Write a few memories before
    memory = get_memory()
    body_now = {n: c.level for n, c in endocrine.components.items()}
    body_now["oxytocin"] = 0.85  # important moment
    memory.remember(
        content="great song moment with koro",
        body_state=body_now,
        importance=0.8,
        tags=["affectionate", "music"],
    )
    body_now["oxytocin"] = 0.32
    body_now["cortisol"] = 0.30
    memory.remember(
        content="user said 'k'",
        body_state=body_now,
        importance=0.1,  # bypass auto-importance check
    )
    print(f"  Pre-sleep memory count: {len(memory.all_nodes())}")
    print(f"  Consolidation pending: {sc.is_consolidation_pending()}")

    # ── TEST D: Wake transitions ──
    print("\n" + "=" * 75)
    print("TEST D: Wake transition (energy refills, melatonin falls)")
    print("=" * 75)
    # Refill energy and force wake conditions
    energy._state.level = 0.9
    # Force the time forward so sleep tick can transition
    sleep_at_ts = time.time() + 6 * 3600 + 60 + FALLING_DURATION_PLUS_BUFFER + 4 * 3600
    sleep.tick(now_ts=sleep_at_ts)
    print(f"  After energy refill + 4h: state = {sleep.current_state().value}")
    # Should be WAKING — advance to WAKE
    sleep.tick(now_ts=sleep_at_ts + WAKING_DURATION_PLUS_BUFFER)
    print(f"  After {WAKING_DURATION_PLUS_BUFFER}s in waking: state = {sleep.current_state().value}")
    show("After waking")
    print(f"  Consolidation now pending: {sc.is_consolidation_pending()}")
    print(f"  Memories before consolidation: {[(n.content[:40], n.importance) for n in memory.all_nodes()]}")

    # ── TEST E: Sleep debt accumulation ──
    print("\n" + "=" * 75)
    print("TEST E: Sleep debt accumulates over long wake")
    print("=" * 75)
    # Force long wake state
    sleep._state.last_full_sleep_ts = time.time() - 22 * 3600  # 22h ago
    sleep._state.state = SleepState.WAKE.value
    sleep.tick(now_ts=time.time())
    print(f"  After 22h since last full sleep: debt={sleep.sleep_debt_hours():.1f}h")
    print(f"  Cortisol baseline multiplier: {sleep.cortisol_baseline_multiplier():.2f}x")
    show("Sleep-deprived state")

    print("\n" + "=" * 75)
    print("PHASE 2C SMOKE TEST COMPLETE")
    print("=" * 75)


# Need to import durations from sleep module for test scheduling
from services.orchestrator.body.sleep import FALLING_ASLEEP_DURATION_SECONDS, WAKING_DURATION_SECONDS
FALLING_DURATION_PLUS_BUFFER = FALLING_ASLEEP_DURATION_SECONDS + 10
WAKING_DURATION_PLUS_BUFFER = WAKING_DURATION_SECONDS + 10

if __name__ == "__main__":
    main()
