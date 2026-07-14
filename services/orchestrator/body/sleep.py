"""
Sleep state machine — wake/falling-asleep/asleep/waking with sleep debt.

Phase 2C component. Per docs/koroki_subsystem_atlas.md §3.3:
  - States: WAKE, FALLING_ASLEEP, ASLEEP, WAKING
  - Sleep debt accumulates with wake time vs target (~8h sleep per 24h)
  - Cortisol baseline rises with sleep deprivation
  - Memory consolidation happens during ASLEEP state (see sleep_cycle.py)

This is a simplified Phase 2C MVP. Phase 3 expands to full NREM/REM architecture
with stage cycling (~90 min cycles), dream replay during REM, etc. For now
we treat ASLEEP as a single state.

State transitions:
  WAKE → FALLING_ASLEEP: energy < 0.25 OR melatonin > 0.7 OR explicit `try_sleep()`
  FALLING_ASLEEP → ASLEEP: 5-10 min of being in falling-asleep state
  ASLEEP → WAKING: energy ≥ 0.85 OR melatonin < 0.2 (morning) OR explicit `wake()`
  WAKING → WAKE: 2-3 min of being in waking state

Why not have her instantly wake when energy refills?
  Real bodies have momentum. You don't go from REM to fully alert in 1 second.
  The WAKING state captures "morning fog" — eyes open but mind not fully online.

═══════════════════════════════════════════════════════════════════════════════
🐛 PREDICTED BUGS / WATCH-FOR-WHEN-LIVE-TESTING:

SL1. "She gets stuck in FALLING_ASLEEP forever"
     Look at: FALLING_ASLEEP_DURATION_SECONDS + tick() transition logic. The
     state should advance to ASLEEP after the duration even if energy doesn't
     drop further. If transition condition relies on energy continuing to
     drop, she'll never sleep.

SL2. "She never wakes up even after 8 hours of sleep"
     Look at: ASLEEP → WAKING conditions. Energy threshold (0.85) + melatonin
     fall + time-based fallback. If melatonin circadian doesn't fall in the
     simulation (e.g. time-warp issue), she might never wake. Time-based
     fallback should kick in.

SL3. "She wakes up the moment user sends a message"
     Look at: try_wake() / external_arousal_event() handling. User shouldn't
     be able to force-wake her instantly — there's a brief WAKING transition
     even from external stimuli. Otherwise we lose the "she's not fully here yet"
     texture in morning interactions.

SL4. "Sleep debt never accumulates / always reads 0"
     Look at: sleep_debt_hours() calculation + the tick that updates
     last_full_sleep_ts. Check that "full sleep" is correctly recorded when
     she goes through ASLEEP → WAKING after sufficient duration.

SL5. "Cortisol baseline isn't affected by sleep deprivation"
     Look at: the cortisol baseline modification in tick() that applies
     a multiplier based on sleep_debt. Verify endocrine engine reads this OR
     that we're writing back to cortisol.baseline (and that the engine doesn't
     overwrite it from the static default each tick).

SL6. "Brain restart loses sleep state — she's wide awake even if she was asleep"
     Look at: persistence in data/body/sleep_state.json. Save/load logic.
     Probably want to persist state + sleep_debt_hours + last_full_sleep_ts.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import enum
import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .energy import get_energy
from ..world.clock import melatonin_circadian, now

logger = logging.getLogger("orchestrator.body.sleep")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "body" / "sleep_state.json"


class SleepState(str, enum.Enum):
    WAKE = "wake"
    FALLING_ASLEEP = "falling_asleep"
    ASLEEP = "asleep"
    WAKING = "waking"


# ── Tuning constants ──────────────────────────────────────────────────────
# How long it takes to transition between states.
FALLING_ASLEEP_DURATION_SECONDS = 5 * 60   # 5 minutes nodding off
WAKING_DURATION_SECONDS = 3 * 60            # 3 minutes morning fog

# Energy thresholds for sleep state transitions.
SLEEP_INITIATION_ENERGY = 0.25  # below this, sleep starts being pulled
WAKE_FROM_REFILL_ENERGY = 0.85  # above this, she naturally wakes

# Melatonin thresholds (driven by world clock).
SLEEP_INITIATION_MELATONIN = 0.7  # high melatonin pulls toward sleep
WAKE_MELATONIN_CEILING = 0.2       # low melatonin in morning helps wake
# Night gate for the energy-refill wake (first live night, 2026-07-05): she
# refilled to 0.85 by 2 AM, woke, and high melatonin (0.67) immediately pulled
# her back down — WAKE↔FALLING_ASLEEP flapping until ~4:30, wake callbacks
# firing mid-night (a dream logged at 02:15). A body full of energy at 2 AM
# keeps sleeping; refill only wakes her when melatonin says it isn't night.
# Daytime naps unaffected (midday melatonin ≈ 0).
WAKE_REFILL_MELATONIN_CEILING = 0.45
# Hysteresis: once fully awake, don't slide back into FALLING_ASLEEP within
# this window unless energy is critically gone — kills residual flip-flopping
# at threshold boundaries (same night: sleeping→reading→sleeping every 2 min).
MIN_WAKE_DWELL_SECONDS = 15 * 60
CRITICAL_ENERGY = 0.15  # below this she can drop off regardless of dwell
# BUT melatonin-wake only applies after she's been asleep long enough.
# Otherwise "low melatonin" of daytime would prevent any nap — she'd wake
# the instant ASLEEP began because mel is already 0 at noon.
# This is the bug caught in Phase 2C smoke test (SL7 in prediction log).
MIN_SLEEP_FOR_MELATONIN_WAKE_SECONDS = 4 * 3600  # 4h — only substantial sleep gets a morning wake

# Sleep debt target. 24h - 8h sleep = 16h max wake.
TARGET_WAKE_SECONDS = 16 * 3600  # 16h
SLEEP_DEBT_CORTISOL_MULT_PER_HOUR = 0.05  # +5% cortisol baseline per hour of debt

# Downtime is SUSPENSION, not wakefulness. A tick gap far beyond the 60s
# heartbeat means the stack was off — that time must not count as sleepless
# hours (live 2026-07-08: a day of services-off read as 20.5h sleep debt and
# she collapsed asleep at boot, offloading her own voice mid-rehearsal).
# Normal restarts (< this) still count, preserving the 2026-07-05 rule that
# restarts must not silently reset debt.
SUSPEND_GAP_SECONDS = 600.0


@dataclass
class SleepStateData:
    state: str = SleepState.WAKE.value
    state_started_ts: float = 0.0
    last_full_sleep_ts: float = 0.0           # last time she completed a sleep cycle
    sleep_debt_hours: float = 0.0             # accumulated debt
    last_tick_ts: float = 0.0


class SleepSystem:
    """Sleep state machine + debt tracker."""

    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state = SleepStateData(
            state=SleepState.WAKE.value,
            state_started_ts=time.time(),
            last_full_sleep_ts=time.time(),  # assume fresh on first init
            last_tick_ts=time.time(),
        )
        self._state_path = state_path or _STATE_PATH
        self._load()
        # Notify listeners (set by sleep_cycle.py for consolidation hooks)
        self._on_sleep_callbacks: list = []
        self._on_wake_callbacks: list = []
        # Fired at ASLEEP→WAKING (start of the 3-min morning fog) — early enough
        # for the VRAM offloader to reload her voice before she can speak.
        self._on_waking_callbacks: list = []

    # ------------------------------------------------------------------
    # State machine tick
    # ------------------------------------------------------------------

    def tick(self, now_ts: float | None = None) -> None:
        """Advance the sleep state machine + drive energy + accumulate debt."""
        ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            dt = max(0.0, ts - self._state.last_tick_ts)
            self._state.last_tick_ts = ts
            if dt > SUSPEND_GAP_SECONDS:
                # Stack was down: shift the wake-ledger anchor past the gap and
                # swallow the dt so energy doesn't drain a day in one tick.
                self._state.last_full_sleep_ts += dt
                dt = 0.0

        energy = get_energy()
        mel = melatonin_circadian()

        # Drive energy based on current sleep state (outside lock — they have their own).
        if self._state.state in (SleepState.WAKE.value, SleepState.WAKING.value):
            energy.tick_awake(dt=dt, now_ts=ts)
        else:
            energy.tick_asleep(dt=dt, now_ts=ts)

        # Sleep debt: only accumulates while awake.
        with self._lock:
            if self._state.state == SleepState.WAKE.value:
                # Accumulate wake time relative to last full sleep
                wake_seconds = ts - self._state.last_full_sleep_ts
                if wake_seconds > TARGET_WAKE_SECONDS:
                    extra_hours = (wake_seconds - TARGET_WAKE_SECONDS) / 3600.0
                    self._state.sleep_debt_hours = extra_hours

            # State transitions
            state = self._state.state
            in_state_for = ts - self._state.state_started_ts
            cur_energy = energy.level()

            if state == SleepState.WAKE.value:
                # Pull toward sleep if energy is low OR melatonin is high —
                # but a fresh wake holds for MIN_WAKE_DWELL (hysteresis) unless
                # energy is critically gone.
                wants_sleep = (
                    cur_energy < SLEEP_INITIATION_ENERGY or mel > SLEEP_INITIATION_MELATONIN
                )
                dwell_ok = (
                    in_state_for >= MIN_WAKE_DWELL_SECONDS or cur_energy < CRITICAL_ENERGY
                )
                if wants_sleep and dwell_ok:
                    self._transition_to(SleepState.FALLING_ASLEEP, ts)

            elif state == SleepState.FALLING_ASLEEP.value:
                # After duration, fully asleep
                if in_state_for >= FALLING_ASLEEP_DURATION_SECONDS:
                    self._transition_to(SleepState.ASLEEP, ts)
                # Or — user might "wake up" before fully asleep if external arousal
                # (handled separately via external_arousal_event)

            elif state == SleepState.ASLEEP.value:
                # Wake conditions:
                #   (a) Energy refilled — primary wake signal (works for naps)
                #   (b) Morning melatonin fall — ONLY if she's been asleep long enough.
                #       Without the duration gate, daytime mel=0 would wake her
                #       instantly from any nap. See SL7 in prediction log.
                slept_long_enough_for_morning_wake = (
                    in_state_for >= MIN_SLEEP_FOR_MELATONIN_WAKE_SECONDS
                )
                # Refill-wake is gated on melatonin: full energy at 2 AM keeps
                # sleeping (see WAKE_REFILL_MELATONIN_CEILING). Naps still end
                # on refill because daytime melatonin ≈ 0.
                wake_from_refill = (
                    cur_energy >= WAKE_FROM_REFILL_ENERGY
                    and mel < WAKE_REFILL_MELATONIN_CEILING
                )
                wake_from_morning = (
                    mel < WAKE_MELATONIN_CEILING and slept_long_enough_for_morning_wake
                )
                if wake_from_refill or wake_from_morning:
                    # Mark as "full sleep" reset before waking
                    self._state.last_full_sleep_ts = ts
                    self._state.sleep_debt_hours = 0.0
                    self._transition_to(SleepState.WAKING, ts)

            elif state == SleepState.WAKING.value:
                # After duration, fully awake
                if in_state_for >= WAKING_DURATION_SECONDS:
                    self._transition_to(SleepState.WAKE, ts)

        # Throttled persistence — save() existed but was never called (energy.py
        # precedent, fixed 2026-07-05): restarts silently reset sleep debt and
        # state timing. Transitions also save immediately (see _transition_to).
        if ts - getattr(self, "_last_save_ts", 0.0) >= 60.0:
            self._last_save_ts = ts
            self.save()

    def _transition_to(self, new_state: SleepState, ts: float) -> None:
        """Internal — must hold _lock."""
        old_state = self._state.state
        self._state.state = new_state.value
        self._state.state_started_ts = ts
        logger.info("Sleep state: %s → %s", old_state, new_state.value)
        # Fire callbacks (outside this lock would be ideal but for Phase 2C MVP this is ok)
        if new_state == SleepState.ASLEEP:
            for cb in self._on_sleep_callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.warning("on_sleep callback failed: %s", exc)
        elif new_state == SleepState.WAKE and old_state == SleepState.WAKING.value:
            for cb in self._on_wake_callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.warning("on_wake callback failed: %s", exc)
        elif new_state == SleepState.WAKING:
            for cb in self._on_waking_callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.warning("on_waking callback failed: %s", exc)
        # State changes persist immediately — a restart mid-night must resume
        # from the right state, not reset to WAKE.
        try:
            threading.Thread(target=self.save, daemon=True).start()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def current_state(self) -> SleepState:
        with self._lock:
            return SleepState(self._state.state)

    def sleep_debt_hours(self) -> float:
        with self._lock:
            return self._state.sleep_debt_hours

    def is_awake(self) -> bool:
        """User-facing: she's basically awake (could be wake or waking)."""
        return self.current_state() in (SleepState.WAKE, SleepState.WAKING)

    def is_asleep(self) -> bool:
        """User-facing: she's basically asleep (could be falling-asleep or asleep)."""
        return self.current_state() in (SleepState.FALLING_ASLEEP, SleepState.ASLEEP)

    def cortisol_baseline_multiplier(self) -> float:
        """Cortisol baseline boost from sleep debt.

        For each hour of debt, baseline rises 5%. After 4h debt: 1.2x baseline.
        After 12h debt: 1.6x baseline. Capped at 2.0x.
        Used by endocrine integration to modify cortisol baseline.
        """
        debt = self.sleep_debt_hours()
        return min(2.0, 1.0 + debt * SLEEP_DEBT_CORTISOL_MULT_PER_HOUR)

    # ------------------------------------------------------------------
    # Public write API (intentional sleep/wake control)
    # ------------------------------------------------------------------

    def try_sleep(self) -> None:
        """Intentionally try to fall asleep (overrides energy gate)."""
        ts = time.time()
        with self._lock:
            if self._state.state == SleepState.WAKE.value:
                self._transition_to(SleepState.FALLING_ASLEEP, ts)

    def external_arousal_event(self) -> None:
        """External stimulus (e.g., user message, alarm) — may wake her.

        If she's in FALLING_ASLEEP, this aborts → back to WAKE.
        If ASLEEP, it nudges toward WAKING but doesn't force it (deep sleep is
        sticky — she can sleep through messages).
        """
        ts = time.time()
        with self._lock:
            if self._state.state == SleepState.FALLING_ASLEEP.value:
                self._transition_to(SleepState.WAKE, ts)
            elif self._state.state == SleepState.ASLEEP.value:
                # Heavy sleeper — only wake if asleep < 1 hour (light sleep)
                in_state_for = ts - self._state.state_started_ts
                if in_state_for < 3600:
                    self._transition_to(SleepState.WAKING, ts)

    # ------------------------------------------------------------------
    # Hooks (for sleep_cycle.py memory consolidation)
    # ------------------------------------------------------------------

    def on_sleep(self, callback) -> None:
        """Register a callback fired when she enters ASLEEP state."""
        self._on_sleep_callbacks.append(callback)

    def on_wake(self, callback) -> None:
        """Register a callback fired when she fully wakes (WAKING → WAKE)."""
        self._on_wake_callbacks.append(callback)

    def on_waking(self, callback) -> None:
        """Register a callback fired at ASLEEP → WAKING (morning fog begins)."""
        self._on_waking_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Felt-state contribution
    # ------------------------------------------------------------------

    def contribute_to_felt_state(self, out: dict[str, list[str]]) -> None:
        s = self.current_state()
        if s == SleepState.FALLING_ASLEEP:
            out["body"].append("eyes wanting to close, slipping under")
            out["mind"].append("thoughts trailing off")
        elif s == SleepState.ASLEEP:
            out["body"].append("deep stillness")
            out["mind"].append("not really here")
        elif s == SleepState.WAKING:
            out["body"].append("just-awake fog")
            out["mind"].append("not all the way back yet")
        # Sleep debt context
        debt = self.sleep_debt_hours()
        if debt > 4:
            out["body"].append("a worn quality, slept badly recently")
            out["mood"].append("running on fumes")
        elif debt > 2:
            out["mind"].append("a slight haze, sleep was thin")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state.state = data.get("state", SleepState.WAKE.value)
            self._state.state_started_ts = float(data.get("state_started_ts", time.time()))
            self._state.last_full_sleep_ts = float(data.get("last_full_sleep_ts", time.time()))
            self._state.sleep_debt_hours = float(data.get("sleep_debt_hours", 0.0))
            self._state.last_tick_ts = float(data.get("last_tick_ts", time.time()))
            logger.info("Sleep state loaded: %s (debt=%.1fh)",
                         self._state.state, self._state.sleep_debt_hours)
        except Exception as exc:
            logger.warning("Sleep load failed: %s", exc)

    def save(self) -> None:
        with self._lock:
            payload = asdict(self._state)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Sleep save failed: %s", exc)


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────

_INSTANCE: SleepSystem | None = None
_INSTANCE_LOCK = threading.Lock()


def get_sleep() -> SleepSystem:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = SleepSystem()
    return _INSTANCE
