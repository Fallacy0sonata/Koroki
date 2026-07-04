"""Autonomic heartbeat — her body ticks whether or not anyone is talking to her.

THE overnight bug of 2026-07-03 (her first sleep attempt): every body tick was
driven by get_felt_state(), which only runs inside chat requests. No chats
overnight → sleep state machine frozen mid-"falling_asleep", activities kept
daydreaming, energy DRAINED all night via stale-state routing, no sleep
session → no dream. Her body only existed when spoken to.

This loop is the fix and the philosophy: a living thing runs continuously.
Every BEAT_SECONDS it advances sleep (which routes energy drain/refill) and
the endocrine engine (decay + circadian + interactions). Room subsystems tick
inside felt-state reads as before — they're perception-priced, not life-priced.

Also emits a vitals log line every LOG_EVERY beats — the observability that
would have caught all of this on night one (melatonin/energy/sleep/activity).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("orchestrator.heartbeat")

BEAT_SECONDS = 60.0
LOG_EVERY = 10  # one vitals line every ~10 minutes


async def run_heartbeat_loop() -> None:
    await asyncio.sleep(15)  # let subsystems construct first
    beat = 0
    logger.info("autonomic heartbeat started (%.0fs)", BEAT_SECONDS)
    while True:
        try:
            from .endocrine import get_endocrine
            from .energy import get_energy
            from .sleep import get_sleep

            sleep = get_sleep()
            sleep.tick()          # advances state machine + routes energy drain/refill
            get_endocrine().tick()  # decay + circadian forcing + interactions

            beat += 1
            if beat % LOG_EVERY == 0:
                try:
                    eng = get_endocrine()
                    mel = eng.components["melatonin"].level
                    mel_eff = eng.components["melatonin"].effective_level()
                    st = sleep.current_state()  # public accessor — .state doesn't exist
                    logger.info(
                        "vitals: sleep=%s energy=%.3f melatonin=%.3f (eff %.3f) cortisol=%.3f",
                        getattr(st, "value", st), get_energy().level(), mel, mel_eff,
                        eng.components["cortisol"].level,
                    )
                except Exception:
                    # WARNING, not debug — a dead vitals line hid the .state bug
                    # for 5 hours (2026-07-04). Failure signals must be visible.
                    logger.warning("vitals log failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("heartbeat tick failed", exc_info=True)
        await asyncio.sleep(BEAT_SECONDS)
