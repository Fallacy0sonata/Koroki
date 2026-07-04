"""Her hands — Stage 2 of STREAMING & PLAY (docs/master_queue.md).

Constrained-action input executor for game sessions. The captain never emits
raw coordinates or keycodes — it picks from a small action vocabulary
("click <thing>", "press <key>", "scroll", "wait") and this module makes it
physical: described targets are resolved to pixels by the vision service's
/v1/point (moondream's native pointing skill), then executed with
pydirectinput inside the game window only.

Safety rails (owner plan, all three from day one):
1. DRY-RUN default — logs what she WOULD do, touches nothing. Flip per session.
2. Window confinement — every click is clamped to the target window rect;
   input is refused entirely if the window is gone or not in the foreground.
3. Panic switch — hold F9 (checked before every action) or create
   data/game/PANIC (checked every action; delete to release). Freezes hands
   instantly without touching her mind.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("koroki.hands")

_REPO_ROOT = Path(__file__).resolve().parent
PANIC_FILE = _REPO_ROOT / "data" / "game" / "PANIC"

# The whole vocabulary. The captain picks a type; everything else is data.
# "hold" = press-and-hold a key for N seconds — the movement primitive
# (third-person games: hold w to run to the mailbox; owner design 2026-07-04).
ACTION_TYPES = {"click", "double_click", "right_click", "move_to", "press", "hold", "scroll", "wait"}

VK_F9 = 0x78


@dataclass
class HandsStats:
    actions: int = 0
    dry_runs: int = 0
    refused: int = 0
    point_misses: int = 0
    started_at: float = field(default_factory=time.time)


class GameHands:
    def __init__(
        self,
        window_title: str,
        vision_url: str = "http://127.0.0.1:9005",
        dry_run: bool = True,
        min_action_gap_s: float = 0.8,
    ):
        self.window_title = window_title
        self.vision_url = vision_url
        self.dry_run = dry_run
        self.min_action_gap_s = min_action_gap_s
        self.stats = HandsStats()
        self._hwnd: Optional[int] = None
        self._last_action_ts = 0.0

    # ── safety rails ─────────────────────────────────────────────────

    def panic_active(self) -> bool:
        if PANIC_FILE.exists():
            return True
        try:
            import win32api

            if win32api.GetAsyncKeyState(VK_F9) & 0x8000:
                return True
        except Exception:
            pass
        return False

    def _window_rect(self, require_foreground: bool = True) -> Optional[tuple[int, int, int, int]]:
        """Rect of the target window if it exists (and is foreground, when required).

        Foreground is mandatory for REAL input (keystrokes/clicks land in the
        active app). Pointing/dry-run only needs the window visible on screen —
        the owner is usually typing in Discord while testing her aim.
        """
        import win32gui

        from stream_watch import find_window

        if not (self._hwnd and win32gui.IsWindow(self._hwnd)):
            hit = find_window(self.window_title)
            if hit is None:
                return None
            self._hwnd = hit[0]
        if require_foreground and win32gui.GetForegroundWindow() != self._hwnd:
            return None  # refuse: input would land in the wrong app
        left, top, right, bottom = win32gui.GetWindowRect(self._hwnd)
        if right - left < 64 or bottom - top < 64:
            return None
        return left, top, right, bottom

    @staticmethod
    def clamp_to_rect(x: int, y: int, rect: tuple[int, int, int, int],
                      margin: int = 4) -> tuple[int, int]:
        left, top, right, bottom = rect
        return (
            max(left + margin, min(right - margin, x)),
            max(top + margin, min(bottom - margin, y)),
        )

    @staticmethod
    def norm_to_screen(nx: float, ny: float, rect: tuple[int, int, int, int]) -> tuple[int, int]:
        """Vision /v1/point returns 0..1 coords relative to the captured frame,
        which is exactly the window rect — scale straight into screen space."""
        left, top, right, bottom = rect
        return (
            int(left + nx * (right - left)),
            int(top + ny * (bottom - top)),
        )

    # ── target resolution ────────────────────────────────────────────

    async def resolve_target(self, target: str) -> Optional[tuple[int, int]]:
        """Described thing → screen pixel via capture + vision point."""
        from stream_watch import capture_window_png

        rect = self._window_rect(require_foreground=False)
        if rect is None:
            return None
        png = await asyncio.to_thread(capture_window_png, self._hwnd)
        if png is None:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(
                    f"{self.vision_url}/v1/point",
                    json={"image_b64": base64.b64encode(png).decode("ascii"),
                          "target": target},
                )
                resp.raise_for_status()
                points = resp.json().get("points") or []
        except httpx.HTTPError as exc:
            logger.warning("point request failed: %s", exc)
            return None
        if not points:
            self.stats.point_misses += 1
            logger.info("point miss: %r not found on screen", target)
            return None
        # Re-check the rect AFTER the vision round-trip — window may have moved.
        rect = self._window_rect()
        if rect is None:
            return None
        x, y = self.norm_to_screen(points[0]["x"], points[0]["y"], rect)
        return self.clamp_to_rect(x, y, rect)

    # ── the executor ─────────────────────────────────────────────────

    async def act(self, action: dict) -> dict:
        """Execute one constrained action. Returns {ok, detail}."""
        kind = str(action.get("type", "")).lower()
        if kind not in ACTION_TYPES:
            return {"ok": False, "detail": f"unknown action type {kind!r}"}

        if self.panic_active():
            self.stats.refused += 1
            logger.warning("PANIC active — hands frozen (action dropped: %s)", kind)
            return {"ok": False, "detail": "panic switch active"}

        gap = time.time() - self._last_action_ts
        if gap < self.min_action_gap_s:
            await asyncio.sleep(self.min_action_gap_s - gap)

        if kind == "wait":
            secs = min(10.0, max(0.1, float(action.get("seconds", 1.0))))
            await asyncio.sleep(secs)
            self._last_action_ts = time.time()
            return {"ok": True, "detail": f"waited {secs:.1f}s"}

        if kind == "press":
            key = str(action.get("key", "")).lower()[:16]
            if not key:
                return {"ok": False, "detail": "press needs a key"}
            if self._window_rect() is None:
                self.stats.refused += 1
                return {"ok": False, "detail": "window not available/foreground"}
            return await self._do(kind, f"press {key!r}", lambda pdi: pdi.press(key))

        if kind == "hold":
            key = str(action.get("key", "")).lower()[:16]
            secs = min(6.0, max(0.2, float(action.get("seconds", 1.5))))
            if not key:
                return {"ok": False, "detail": "hold needs a key"}
            if self._window_rect() is None:
                self.stats.refused += 1
                return {"ok": False, "detail": "window not available/foreground"}

            def _hold(pdi, _key=key, _secs=secs):
                import time as _t

                pdi.keyDown(_key)
                try:
                    _t.sleep(_secs)
                finally:
                    pdi.keyUp(_key)  # NEVER leave a key stuck down

            return await self._do(kind, f"hold {key!r} {secs:.1f}s", _hold)

        if kind == "scroll":
            amount = int(max(-10, min(10, int(action.get("amount", -3)))))
            if self._window_rect() is None:
                self.stats.refused += 1
                return {"ok": False, "detail": "window not available/foreground"}
            return await self._do(kind, f"scroll {amount}", lambda pdi: pdi.scroll(amount))

        # click / double_click / right_click / move_to — need a resolved target
        target = str(action.get("target", "")).strip()
        if not target:
            return {"ok": False, "detail": f"{kind} needs a target description"}
        pos = await self.resolve_target(target)
        if pos is None:
            self.stats.refused += 1
            return {"ok": False, "detail": f"couldn't locate {target!r} (or window lost)"}
        # REAL input additionally requires the game to be the foreground window.
        if not self.dry_run and self._window_rect(require_foreground=True) is None:
            self.stats.refused += 1
            return {"ok": False, "detail": "window not foreground — real input refused"}
        x, y = pos

        def _mouse(pdi):
            if kind == "move_to":
                pdi.moveTo(x, y)
            elif kind == "double_click":
                pdi.doubleClick(x, y)
            elif kind == "right_click":
                pdi.rightClick(x, y)
            else:
                pdi.click(x, y)

        return await self._do(kind, f"{kind} {target!r} @ ({x},{y})", _mouse)

    async def _do(self, kind: str, detail: str, fn) -> dict:
        if self.dry_run:
            self.stats.dry_runs += 1
            logger.info("[DRY RUN] would %s", detail)
            self._last_action_ts = time.time()
            return {"ok": True, "detail": f"[dry-run] {detail}", "dry_run": True}
        import pydirectinput as pdi

        pdi.FAILSAFE = True  # mouse to screen corner = hard abort (library rail)
        await asyncio.to_thread(fn, pdi)
        self.stats.actions += 1
        self._last_action_ts = time.time()
        logger.info("did %s", detail)
        return {"ok": True, "detail": detail}
