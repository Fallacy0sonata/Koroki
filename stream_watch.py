"""Watch-party engine — Stage 1 of STREAMING & PLAY (docs/master_queue.md).

She watches a window (game/video) and speaks to her VIEWERS about events —
never to herself. Owner design pillars (2026-07-04):
- Addressed speech only: every line targets the viewers or remarks on an event
  breaking normality ("talking to THEMSELVES is psychopath, talking to
  viewers / commenting game events is normal human").
- Anti-yapper: hard cooldown + scene-novelty gate here, plus the brain's own
  [silent] option downstream. Silence is normal — streamers breathe.

Split of responsibilities (captain-in-cabin hygiene):
- This module is her EYES + the event gate: capture → vision describe → decide
  whether something happened → fire on_event(summary).
- The MOUTH (brain line, TTS, voice-channel playback) is wired by the caller
  (discord_bot.py) through the on_event callback.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger("koroki.watch")


# ── S2: CPU frame gate — the VLM only looks when pixels actually changed ──


class FrameGate:
    """Morphological change detection against an EMA baseline (pure CPU, ~1 ms).

    Gaming-eyes ranking S2 (docs/master_queue.md): gray → absdiff vs a smoothed
    baseline of recent frames → median blur → threshold → morphological close →
    changed-pixel ratio. The EMA baseline (instead of prev-frame diff) absorbs
    slow pans/drifts so only abrupt, structural changes wake the VLM. This is
    what buys the fast-look budget: most frames never cost VLM time at all.
    """

    def __init__(
        self,
        change_threshold: float = 0.02,   # fraction of pixels that must change
        ema_alpha: float = 0.25,          # baseline adaptation speed
        downscale_width: int = 480,
    ):
        self.change_threshold = change_threshold
        self.ema_alpha = ema_alpha
        self.downscale_width = downscale_width
        self._baseline = None  # float32 grayscale EMA

    def changed(self, png_bytes: bytes) -> tuple[bool, float]:
        """Returns (frame_is_worth_looking_at, changed_pixel_ratio)."""
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            return True, 1.0  # unreadable frame — let the VLM decide
        if arr.shape[1] > self.downscale_width:
            scale = self.downscale_width / arr.shape[1]
            arr = cv2.resize(arr, (self.downscale_width, int(arr.shape[0] * scale)))
        cur = arr.astype("float32")

        if self._baseline is None or self._baseline.shape != cur.shape:
            self._baseline = cur
            return True, 1.0  # first look of the session

        diff = cv2.absdiff(cur, self._baseline).astype("uint8")
        diff = cv2.medianBlur(diff, 5)
        _, mask = cv2.threshold(diff, 18, 255, cv2.THRESH_BINARY)
        kernel = np.ones((9, 9), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        ratio = float((mask > 0).mean())

        # Baseline always adapts — slow drifts get absorbed, not reported.
        self._baseline = self.ema_alpha * cur + (1.0 - self.ema_alpha) * self._baseline
        return ratio >= self.change_threshold, ratio


# ── S1: rolling state doc — overwrite semantics, never appended logs ──


@dataclass
class WatchState:
    """What she knows about this session so far (gaming-eyes ranking S1).

    Overwrite rule: `current_scene` is REPLACED every look; `recent_events` is
    a small bounded window. Temporal authority beats semantic relevance —
    stale facts get overwritten, never retrieved from a growing log.
    """

    game: Optional[str] = None
    current_scene: str = ""
    recent_events: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=4)
    )

    def note_scene(self, description: str) -> None:
        self.current_scene = description  # overwrite — the only current truth

    def note_event(self, summary: str) -> None:
        self.recent_events.append(
            (time.strftime("%H:%M"), summary[:120])
        )

    def context_block(self) -> str:
        """One compact line of session memory for vision asks + commentary."""
        if not self.recent_events:
            return ""
        events = "; ".join(f"{t} {s}" for t, s in self.recent_events)
        return f"earlier on this stream: {events}"


# ── window capture (Windows) ─────────────────────────────────────────


def find_window(title_substring: str) -> Optional[tuple[int, str]]:
    """Find a visible top-level window whose title contains the substring."""
    import win32gui

    needle = title_substring.lower()
    hits: list[tuple[int, str]] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and needle in title.lower():
                hits.append((hwnd, title))

    win32gui.EnumWindows(_cb, None)
    return hits[0] if hits else None


def capture_window_png(hwnd: int) -> Optional[bytes]:
    """Screenshot the window's on-screen rect as PNG. Window must not be minimized."""
    import mss
    import win32gui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 64 or height < 64:
        return None  # minimized or degenerate
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── the anti-yapper gate ─────────────────────────────────────────────


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


@dataclass
class WatchConfig:
    window_title: str
    game: Optional[str] = None
    tick_seconds: float = 8.0
    cooldown_seconds: float = 25.0
    novelty_threshold: float = 0.5      # 1 - jaccard(prev, cur) must exceed this
    max_describe_tokens: int = 80
    vision_url: str = "http://127.0.0.1:9005"


def evaluate_gate(
    prev_desc: Optional[str],
    cur_desc: str,
    last_spoke_at: float,
    now: float,
    cfg: WatchConfig,
) -> tuple[bool, str]:
    """Addressed-speech gate: only EVENTS earn a line. Pure — unit-tested."""
    if now - last_spoke_at < cfg.cooldown_seconds:
        return False, "cooldown"
    if prev_desc is None:
        return True, "first_look"  # stream just started — greet the room
    novelty = 1.0 - _jaccard(prev_desc, cur_desc)
    if novelty >= cfg.novelty_threshold:
        return True, f"scene_changed({novelty:.2f})"
    return False, f"nothing_new({novelty:.2f})"


# ── the session ──────────────────────────────────────────────────────


@dataclass
class WatchStats:
    ticks: int = 0
    events: int = 0
    gated_frames: int = 0   # frames the CPU gate spared the VLM from
    capture_failures: int = 0
    started_at: float = field(default_factory=time.time)


class WatchSession:
    """Capture→describe→gate loop. on_event(summary, reason) is the only output."""

    def __init__(
        self,
        cfg: WatchConfig,
        on_event: Callable[[str, str], Awaitable[None]],
    ):
        self.cfg = cfg
        self.on_event = on_event
        self.stats = WatchStats()
        self.state = WatchState(game=cfg.game)   # S1 rolling session memory
        self._gate = FrameGate()                  # S2 CPU change gate
        self._hwnd: Optional[int] = None
        self._prev_desc: Optional[str] = None
        self._last_spoke_at = 0.0
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="koroki-watch-session")
        logger.info("watch session started: window~'%s' game=%s tick=%.0fs",
                    self.cfg.window_title, self.cfg.game, self.cfg.tick_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("watch session stopped: %d ticks, %d events", self.stats.ticks, self.stats.events)

    def _resolve_window(self) -> Optional[int]:
        import win32gui

        if self._hwnd and win32gui.IsWindow(self._hwnd) and win32gui.IsWindowVisible(self._hwnd):
            return self._hwnd
        hit = find_window(self.cfg.window_title)
        if hit:
            self._hwnd = hit[0]
            logger.info("watch target window: '%s'", hit[1])
            return self._hwnd
        return None

    async def _describe(self, png: bytes) -> Optional[str]:
        payload = {
            "request_id": f"watch_{self.stats.ticks}",
            "images_b64": [base64.b64encode(png).decode("ascii")],
            "max_tokens": self.cfg.max_describe_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)) as client:
                resp = await client.post(f"{self.cfg.vision_url}/v1/describe", json=payload)
                if resp.status_code != 200:
                    logger.warning("watch describe %d: %s", resp.status_code, resp.text[:150])
                    return None
                return str(resp.json().get("description") or "").strip() or None
        except httpx.HTTPError as exc:
            logger.warning("watch describe failed: %s", exc)
            return None

    async def _tick(self) -> None:
        self.stats.ticks += 1
        hwnd = self._resolve_window()
        if hwnd is None:
            self.stats.capture_failures += 1
            if self.stats.capture_failures % 8 == 1:
                logger.warning("watch: window '%s' not found", self.cfg.window_title)
            return
        png = await asyncio.to_thread(capture_window_png, hwnd)
        if png is None:
            self.stats.capture_failures += 1
            return

        # S2: the CPU gate decides whether this frame is worth VLM time at all.
        pixels_changed, ratio = await asyncio.to_thread(self._gate.changed, png)
        if not pixels_changed:
            self.stats.gated_frames += 1
            logger.debug("frame gate: static (%.3f) — VLM spared", ratio)
            return

        desc = await self._describe(png)
        if desc is None:
            return
        self.state.note_scene(desc)  # S1: overwrite the current truth

        speak, reason = evaluate_gate(
            self._prev_desc, desc, self._last_spoke_at, time.time(), self.cfg
        )
        self._prev_desc = desc
        if not speak:
            logger.debug("watch gate: %s", reason)
            return

        # Optimistically mark — prevents pileup while the mouth is busy.
        self._last_spoke_at = time.time()
        self.stats.events += 1
        self.state.note_event(desc[:120])
        logger.info("watch EVENT (%s, px %.2f): %s", reason, ratio, desc[:120])
        try:
            await self.on_event(desc, reason)
        except Exception:
            logger.error("watch on_event failed", exc_info=True)

    async def _loop(self) -> None:
        while self._running:
            started = time.time()
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("watch tick crashed", exc_info=True)
            elapsed = time.time() - started
            await asyncio.sleep(max(1.0, self.cfg.tick_seconds - elapsed))
