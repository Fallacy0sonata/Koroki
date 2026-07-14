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
ACTION_TYPES = {"click", "double_click", "right_click", "move_to", "press", "hold",
                "hold_click", "scroll", "wait"}

# REAL-MONEY / OFF-GAME RAIL (2026-07-05): she pursued her "buy stuff" goal
# straight into Universal Paperclips' MERCH link ("T-Shirts: Gift Shop") —
# real-world commerce read as gameplay. Two tiers (owner insight: business
# games legitimately contain "checkout"/"gift shop"/"subscribe" — words alone
# would cripple her in every tycoon):
#   HARD BLOCK — unambiguous real-world payment surfaces. No game needs her
#                clicking these; refused outright, panic-switch tier.
#   VERIFY     — commerce-flavored terms that MIGHT be gameplay. Her eyes get
#                asked, with the actual frame: would this cost real money /
#                leave the game? Only a clear "no" lets the click through.
#                Vision unavailable -> refuse (fail safe).
HARD_BLOCK_TERMS = (
    "paypal", "credit card", "debit card", "patreon", "kickstarter",
    "gift card", "redeem code", "enter code", "billing",
)
VERIFY_TERMS = (
    "gift shop", "t-shirt", "tshirt", "merch", "donate", "checkout",
    "add to cart", "app store", "google play", "steam store", "buy now",
    "subscribe", "premium", "robux", "top up", "top-up", "iphone", "android",
    "shop now", "official site", "www.", ".com",
)


def classify_commerce_risk(target: str) -> str:
    """'block' | 'verify' | 'clear' for a click/move target."""
    t = " ".join(str(target or "").lower().split())
    if any(term in t for term in HARD_BLOCK_TERMS):
        return "block"
    if any(term in t for term in VERIFY_TERMS):
        return "verify"
    return "clear"

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

    async def _vision_money_check(self, target: str) -> bool:
        """Ask her eyes, with the actual frame, whether clicking `target` is safe.

        Returns True only on a clear 'no' (it will NOT cost real money / leave
        the game). Any 'yes', 'unsure', or vision failure refuses — fail safe.
        """
        from stream_watch import capture_window_png

        if self._window_rect(require_foreground=False) is None:
            return False
        png = await asyncio.to_thread(capture_window_png, self._hwnd)
        if png is None:
            return False
        question = (
            f"If someone clicked '{target}' on this screen, would it lead to a "
            "real-money purchase, real merchandise, or a website outside this "
            "game? Answer with only one word: yes, no, or unsure."
        )
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(f"{self.vision_url}/v1/describe", json={
                    "request_id": "money_check",
                    "images_b64": [base64.b64encode(png).decode("ascii")],
                    "question": question,
                    "max_tokens": 8,
                })
                if resp.status_code != 200:
                    return False
                answer = str(resp.json().get("description") or "").strip().lower()
        except httpx.HTTPError:
            return False
        first = (answer.split() or [""])[0].strip(".,!")
        return first == "no"

    async def _landing_text(self, pos: tuple[int, int]) -> Optional[str]:
        """Read what's at the click point: crop around it, ask her eyes.

        Returns the text/label under the crosshair, or None if unreadable
        (None = proceed; the words-tier rail already ran)."""
        from stream_watch import capture_window_png

        rect = self._window_rect(require_foreground=False)
        if rect is None or self._hwnd is None:
            return None
        png = await asyncio.to_thread(capture_window_png, self._hwnd)
        if png is None:
            return None
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            # pos is screen coords; crop is window-relative.
            x = pos[0] - rect[0]
            y = pos[1] - rect[1]
            box = (max(0, x - 140), max(0, y - 45),
                   min(img.width, x + 140), min(img.height, y + 45))
            crop = img.crop(box)
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            crop_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(f"{self.vision_url}/v1/describe", json={
                    "request_id": "landing_check",
                    "images_b64": [crop_b64],
                    "question": "What does the button or link text in this image say? "
                                "Answer with just the text.",
                    "max_tokens": 20,
                })
                if resp.status_code != 200:
                    return None
                return str(resp.json().get("description") or "").strip() or None
        except httpx.HTTPError:
            return None

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

        # Real-money / off-game rail — before any pointing or clicking.
        target = str(action.get("target", ""))
        if kind in ("click", "double_click", "right_click", "move_to", "hold_click"):
            risk = classify_commerce_risk(target)
            if risk == "block":
                self.stats.refused += 1
                logger.warning("REAL-MONEY RAIL: hard-refused %s on %r", kind, target[:60])
                return {"ok": False, "detail": f"refused: {target[:40]!r} is a real-world "
                                               "payment surface — never part of play"}
            if risk == "verify":
                safe = await self._vision_money_check(target)
                if not safe:
                    self.stats.refused += 1
                    logger.warning("REAL-MONEY RAIL: vision-refused %s on %r", kind, target[:60])
                    return {"ok": False, "detail": f"refused: {target[:40]!r} looks like real "
                                                   "commerce or an off-game link on this screen"}
                logger.info("REAL-MONEY RAIL: vision cleared %r as in-game", target[:60])

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

        # LANDING-ZONE RAIL (2026-07-05, merch click #2): she said "click the
        # store" — clean words — but pointing landed on the bright MERCH banner
        # (salience bias). Words are checked above; here we read what's actually
        # under the crosshair and re-classify. Only real clicks pay this tax.
        if kind in ("click", "double_click", "hold_click") and not self.dry_run:
            landing = await self._landing_text(pos)
            if landing:
                l_risk = classify_commerce_risk(landing)
                if l_risk == "block" or (
                    l_risk == "verify" and not await self._vision_money_check(landing)
                ):
                    self.stats.refused += 1
                    logger.warning("LANDING RAIL: refused %s at %s — under cursor: %r",
                                   kind, pos, landing[:60])
                    return {"ok": False, "detail": f"refused: the spot found for "
                                                   f"{target[:30]!r} is {landing[:40]!r} — "
                                                   "real commerce/off-game, not clicking it"}
        # REAL input additionally requires the game to be the foreground window.
        if not self.dry_run and self._window_rect(require_foreground=True) is None:
            self.stats.refused += 1
            return {"ok": False, "detail": "window not foreground — real input refused"}
        x, y = pos

        hold_secs = min(6.0, max(0.2, float(action.get("seconds", 1.5))))

        def _mouse(pdi):
            if kind == "move_to":
                pdi.moveTo(x, y)
            elif kind == "double_click":
                pdi.doubleClick(x, y)
            elif kind == "right_click":
                pdi.rightClick(x, y)
            elif kind == "hold_click":
                # GM2 wave 2: held mouse button on a target ("hold the pump
                # handle for 3 seconds"). Symmetric to key-hold — never leave
                # the button stuck down.
                import time as _t

                pdi.moveTo(x, y)
                pdi.mouseDown(x, y)
                try:
                    _t.sleep(hold_secs)
                finally:
                    pdi.mouseUp(x, y)
            else:
                pdi.click(x, y)

        _detail = (f"{kind} {target!r} {hold_secs:.1f}s @ ({x},{y})"
                   if kind == "hold_click" else f"{kind} {target!r} @ ({x},{y})")
        return await self._do(kind, _detail, _mouse)

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
