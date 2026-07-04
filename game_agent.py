"""Her play loop — Stage 2 of STREAMING & PLAY: she plays the game.

One cycle every tick_seconds:
  capture window → FrameGate (S2: static frames cost nothing, but a forced look
  fires every N gated ticks — menus are static and still need decisions) →
  vision describe → WatchState overwrite (S1) → /v1/games/decide (S3: the
  captain returns STATE/DO/SAY, colored by her felt body) → SAY to the room
  (anti-yapper rules live in the bot) → DO through GameHands (dry-run default,
  panic switch, window confinement — the rails never come off).

The captain never sees a pixel coordinate and never emits one: it names things
("click the store button"), moondream's pointing turns names into pixels.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx

from game_hands import GameHands
from stream_watch import FrameGate, WatchState, capture_window_png, find_window

logger = logging.getLogger("koroki.play")


@dataclass
class PlayConfig:
    window_title: str
    game: str
    objective: str = "explore, have fun, react to what happens"
    knowledge: str = ""
    tick_seconds: float = 10.0
    force_look_every: int = 3      # gated ticks before she looks anyway
    dry_run: bool = True
    vision_url: str = "http://127.0.0.1:9005"
    orchestrator_url: str = "http://127.0.0.1:9882"
    max_describe_tokens: int = 100


@dataclass
class PlayStats:
    cycles: int = 0
    gated: int = 0
    looks: int = 0
    decisions: int = 0
    actions_done: int = 0
    refused: int = 0
    said: int = 0
    started_at: float = field(default_factory=time.time)


class PlaySession:
    def __init__(
        self,
        cfg: PlayConfig,
        on_say: Callable[[str], Awaitable[None]],
    ):
        self.cfg = cfg
        self.on_say = on_say
        self.stats = PlayStats()
        self.state = WatchState(game=cfg.game)
        self.hands = GameHands(
            window_title=cfg.window_title,
            vision_url=cfg.vision_url,
            dry_run=cfg.dry_run,
        )
        self._gate = FrameGate()
        self._gated_streak = 0
        self._hwnd: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="koroki-play-session")
        logger.info("play session started: game=%s window~'%s' dry_run=%s tick=%.0fs",
                    self.cfg.game, self.cfg.window_title, self.cfg.dry_run,
                    self.cfg.tick_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("play session stopped: %s", self.stats)

    # ── perception ───────────────────────────────────────────────────

    def _resolve_window(self) -> Optional[int]:
        import win32gui

        if self._hwnd and win32gui.IsWindow(self._hwnd) and win32gui.IsWindowVisible(self._hwnd):
            return self._hwnd
        hit = find_window(self.cfg.window_title)
        if hit:
            self._hwnd = hit[0]
            return self._hwnd
        return None

    async def _describe(self, png: bytes) -> Optional[str]:
        payload = {
            "request_id": f"play_{self.stats.cycles}",
            "images_b64": [base64.b64encode(png).decode("ascii")],
            "max_tokens": self.cfg.max_describe_tokens,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(f"{self.cfg.vision_url}/v1/describe", json=payload)
                if resp.status_code != 200:
                    logger.warning("play describe %d", resp.status_code)
                    return None
                return str(resp.json().get("description") or "").strip() or None
        except httpx.HTTPError as exc:
            logger.warning("play describe failed: %s", exc)
            return None

    async def _decide(self, scene: str) -> Optional[dict]:
        payload = {
            "request_id": f"play_{self.stats.cycles}",
            "game": self.cfg.game,
            "objective": self.cfg.objective,
            "knowledge": self.cfg.knowledge,
            "state_doc": (self.state.context_block() or "")[:1400],
            "scene": scene[:880],
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.cfg.orchestrator_url}/v1/games/decide", json=payload
                )
                if resp.status_code != 200:
                    logger.warning("play decide %d: %s", resp.status_code, resp.text[:150])
                    return None
                return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("play decide failed: %s", exc)
            return None

    # ── the cycle ────────────────────────────────────────────────────

    async def _cycle(self) -> None:
        self.stats.cycles += 1
        if self.hands.panic_active():
            logger.warning("play: PANIC active — cycle skipped")
            return
        hwnd = self._resolve_window()
        if hwnd is None:
            logger.warning("play: window '%s' not found", self.cfg.window_title)
            return
        png = await asyncio.to_thread(capture_window_png, hwnd)
        if png is None:
            return

        changed, ratio = await asyncio.to_thread(self._gate.changed, png)
        if not changed:
            self._gated_streak += 1
            if self._gated_streak < self.cfg.force_look_every:
                self.stats.gated += 1
                return
            # Static screen but she must still act (menus don't move themselves).
            logger.debug("play: forced look after %d static ticks", self._gated_streak)
        self._gated_streak = 0

        self.stats.looks += 1
        scene = await self._describe(png)
        if scene is None:
            return
        self.state.note_scene(scene)

        decision = await self._decide(scene)
        if decision is None:
            return
        self.stats.decisions += 1
        action = decision.get("action") or {"type": "look"}
        say = (decision.get("say") or "").strip()
        task_state = decision.get("task_state", "?")

        logger.info("play cycle %d: state=%s action=%s say=%r",
                    self.stats.cycles, task_state, action, say[:60])

        if say:
            self.stats.said += 1
            try:
                await self.on_say(say)
            except Exception:
                logger.error("play on_say failed", exc_info=True)

        if action.get("type") not in (None, "look"):
            result = await self.hands.act(action)
            if result.get("ok"):
                self.stats.actions_done += 1
                self.state.note_event(f"did: {result.get('detail', '')[:80]}")
            else:
                self.stats.refused += 1
                self.state.note_event(f"failed: {result.get('detail', '')[:80]}")

    async def _loop(self) -> None:
        while self._running:
            started = time.time()
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("play cycle crashed", exc_info=True)
            elapsed = time.time() - started
            await asyncio.sleep(max(1.0, self.cfg.tick_seconds - elapsed))
