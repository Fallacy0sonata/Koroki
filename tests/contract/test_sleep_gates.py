"""Sleep state-machine gates (first live night findings, 2026-07-05).

That night: energy refilled to 0.85 by 2 AM -> she woke -> melatonin 0.67
pulled her straight back down -> WAKE/FALLING_ASLEEP flapping until 4:30,
wake callbacks firing mid-night. Fixes under test:
  1. refill-wake is melatonin-gated (full energy at 2 AM keeps sleeping)
  2. fresh WAKE holds MIN_WAKE_DWELL before it can slide back to sleep
"""
from __future__ import annotations

import time

import pytest

import services.orchestrator.body.sleep as sleep_mod
from services.orchestrator.body.sleep import SleepState, SleepSystem


class _FakeEnergy:
    def __init__(self, level: float):
        self._level = level

    def level(self) -> float:
        return self._level

    def tick_awake(self, dt: float, now_ts: float) -> None:
        pass

    def tick_asleep(self, dt: float, now_ts: float) -> None:
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """SleepSystem with injectable energy level + melatonin."""
    state = {"energy": 0.5, "mel": 0.0}
    monkeypatch.setattr(sleep_mod, "get_energy", lambda: _FakeEnergy(state["energy"]))
    monkeypatch.setattr(sleep_mod, "melatonin_circadian", lambda: state["mel"])
    system = SleepSystem(state_path=tmp_path / "sleep.json")
    return system, state


def _force_state(system: SleepSystem, st: SleepState, since_s: float, now: float) -> None:
    system._state.state = st.value
    system._state.state_started_ts = now - since_s
    system._state.last_tick_ts = now


def test_full_energy_at_night_keeps_sleeping(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.9, 0.65          # 2 AM: rested but dark
    _force_state(system, SleepState.ASLEEP, since_s=3 * 3600, now=now)
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.ASLEEP  # no 2 AM wake


def test_nap_still_ends_on_refill(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.9, 0.05          # afternoon nap, mel ~0
    _force_state(system, SleepState.ASLEEP, since_s=45 * 60, now=now)
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.WAKING


def test_morning_wake_on_low_melatonin(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.7, 0.1           # morning, slept all night
    _force_state(system, SleepState.ASLEEP, since_s=7 * 3600, now=now)
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.WAKING


def test_fresh_wake_holds_against_high_melatonin(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.6, 0.75          # just woke mid-night somehow
    _force_state(system, SleepState.WAKE, since_s=120, now=now)  # awake 2 min
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.WAKE   # dwell holds — no flap


def test_wake_dwell_expires_then_sleep_pull_works(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.6, 0.75
    _force_state(system, SleepState.WAKE, since_s=20 * 60, now=now)  # awake 20 min
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.FALLING_ASLEEP


def test_critical_energy_overrides_dwell(rig) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.1, 0.0           # completely drained
    _force_state(system, SleepState.WAKE, since_s=60, now=now)   # just woke
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.FALLING_ASLEEP


def test_on_waking_hook_fires(rig) -> None:
    """VRAM offload reloads her voice at ASLEEP->WAKING — the hook must fire there."""
    system, state = rig
    fired = []
    system.on_waking(lambda: fired.append(True))
    now = time.time()
    state["energy"], state["mel"] = 0.9, 0.05          # nap over: refill + no melatonin
    _force_state(system, SleepState.ASLEEP, since_s=45 * 60, now=now)
    system.tick(now_ts=now)
    assert system.current_state() == SleepState.WAKING
    assert fired, "on_waking callback did not fire"


def test_sleep_state_persists_across_restart(rig, tmp_path) -> None:
    system, state = rig
    now = time.time()
    state["energy"], state["mel"] = 0.5, 0.8
    _force_state(system, SleepState.ASLEEP, since_s=3600, now=now)
    system._state.sleep_debt_hours = 2.5
    system.save()
    reborn = SleepSystem(state_path=system._state_path)
    assert reborn.current_state() == SleepState.ASLEEP   # not reset to WAKE
    assert abs(reborn.sleep_debt_hours() - 2.5) < 1e-6
