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

from game_goals import TRAJ_DIR, GameMind, ocr_keywords, precondition_ok
from game_hands import GameHands
from stream_watch import FrameGate, WatchState, capture_window_png, find_window

logger = logging.getLogger("koroki.play")


@dataclass
class PlayConfig:
    window_title: str
    game: str
    objective: str = "explore, have fun, react to what happens"
    knowledge: str = ""
    genre: str = "sandbox"         # picks the Game Mind's goal/metric template
    tick_seconds: float = 10.0
    force_look_every: int = 3      # gated ticks before she looks anyway
    metric_every: int = 3          # looks between metric samplings
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
        # Game card = her permanent save file for this game (GM2 step 2). If no
        # knowledge was passed explicitly, the card's "For her" block fills it —
        # known games are never re-learned; unknown games get a skeleton card.
        if not cfg.knowledge:
            try:
                import game_knowledge

                entry = game_knowledge.ensure_card(cfg.game, platform="roblox"
                                                   if "roblox" in cfg.game.lower()
                                                   or "tycoon" in cfg.game.lower()
                                                   else "misc")
                cfg.game = entry["display"]  # canonical name everywhere downstream
                cfg.knowledge = game_knowledge.prompt_summary(entry["display"])
                if cfg.knowledge:
                    logger.info("play: game card loaded for %r (%s/%s, %d chars)",
                                cfg.game, entry["platform"], entry["slug"],
                                len(cfg.knowledge))
            except Exception as exc:
                logger.warning("play: game card load failed: %s", exc)
        self.stats = PlayStats()
        self._purchase_active = False  # a real-money page is on screen right now
        self.state = WatchState(game=cfg.game)
        self.mind = GameMind(game=cfg.game, genre=cfg.genre,
                             final_goal=cfg.objective if cfg.objective else None)
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
        # SFT-ready trajectory log: prompt-side and her raw completion kept
        # separate so masked-loss training needs no untangling later.
        self._traj_path = TRAJ_DIR / f"{int(time.time())}_{cfg.game[:24].replace(' ', '_')}.jsonl"

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
        # GM2 wave 2: what she learned this session graduates into her save
        # file for this game — next session starts already knowing it.
        try:
            import game_knowledge

            for lesson in self.mind.lessons:
                game_knowledge.append_lesson(self.cfg.game, lesson)
            if self.mind.lessons:
                logger.info("play: %d session lesson(s) graduated into the %r card",
                            len(self.mind.lessons), self.cfg.game)
        except Exception as exc:
            logger.warning("play: lesson graduation failed: %s", exc)
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

    async def _ask_vision(self, png: bytes, question: str) -> Optional[str]:
        """Targeted question about the frame (metric sampling)."""
        payload = {
            "request_id": f"metric_{self.stats.cycles}",
            "images_b64": [base64.b64encode(png).decode("ascii")],
            "question": question,
            "max_tokens": 24,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=45.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(f"{self.cfg.vision_url}/v1/describe", json=payload)
                if resp.status_code != 200:
                    return None
                return str(resp.json().get("description") or "").strip() or None
        except httpx.HTTPError:
            return None

    # Purchase-UI signatures (GM2 step 4): any of these on screen right after an
    # action = that action opened a real-money page. Live 2026-07-08: "auto buy"
    # and "jetpack" were Robux gamepasses; she pressed both.
    _PURCHASE_SIGNS = (
        "robux", "r$", "buy now", "purchase", "get robux", "buy for",
        "gamepass", "game pass", "premium", "buy 1", "cancel or buy",
        "buy item", "confirm purchase",
    )

    def _detect_purchase(self, scene: Optional[str], ocr_lines: Optional[list[dict]]) -> bool:
        hay = (scene or "").lower()
        if ocr_lines:
            hay += " " + " ".join(str(l.get("text", "")).lower() for l in ocr_lines)
        return any(sig in hay for sig in self._PURCHASE_SIGNS)

    def _handle_purchase_page(self) -> None:
        """A real-money page appeared: ban the action that opened it, forever."""
        suspect = self.mind.outcomes.last_action()
        if suspect and not suspect.startswith(("FAILED", "REFUSED")):
            self.mind.rules.add(
                suspect,
                f"never press {suspect[:60]!r} — it opens a real-money/Robux "
                "purchase page",
                klass="DANGEROUS",
            )
        self.mind.add_lesson("a Robux/purchase page opened — close it, buy NOTHING")
        self.state.note_event("PURCHASE PAGE appeared — real money, backing out")

    async def _ocr_lines(self, png: bytes) -> Optional[list[dict]]:
        """One full-frame OCR pass (~300 ms CPU, no VRAM) — all metrics at once."""
        payload = {
            "request_id": f"ocr_{self.stats.cycles}",
            "image_b64": base64.b64encode(png).decode("ascii"),
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=20.0, write=15.0, pool=5.0)
            ) as client:
                resp = await client.post(f"{self.cfg.vision_url}/v1/ocr", json=payload)
                if resp.status_code != 200:
                    logger.warning("play ocr %d", resp.status_code)
                    return None
                return resp.json().get("lines") or []
        except httpx.HTTPError as exc:
            logger.warning("play ocr failed: %s", exc)
            return None

    def _metrics_from_lines(self, lines: Optional[list[dict]]) -> tuple[list[str], list[dict]]:
        """OCR half of metric sampling (GM2 wave 2: runs EVERY tick — ~300ms
        CPU, zero VRAM). Returns (deltas, unresolved metric defs)."""
        deltas: list[str] = []
        defs = self.mind.progress.metric_defs
        if not defs:
            return deltas, []
        if lines is None:
            return deltas, list(defs)
        from services.vision.ocr import extract_metric

        unresolved: list[dict] = []
        for m in defs:
            val = extract_metric(lines, m.get("labels") or []) if m.get("labels") else None
            if val:
                d = self.mind.progress.record(m["q"], val)
                if d:
                    deltas.append(d)
            else:
                unresolved.append(m)
        return deltas, unresolved

    async def _metrics_vlm_fallback(self, png: bytes, unresolved: list[dict]) -> list[str]:
        """VLM half — expensive, only for label-less metrics, only on looks."""
        deltas: list[str] = []
        for m in unresolved:
            ans = await self._ask_vision(png, f"Answer with just the value: {m['q']}")
            if ans:
                d = self.mind.progress.record(m["q"], ans)
                if d:
                    deltas.append(d)
        return deltas

    async def _decide(self, scene: str, mode: str = "play") -> Optional[dict]:
        blocks = self.mind.prompt_blocks()
        payload = {
            "request_id": f"play_{self.stats.cycles}",
            "game": self.cfg.game,
            "objective": self.cfg.objective,
            "knowledge": self.cfg.knowledge,
            "state_doc": (self.state.context_block() or "")[:1100],
            "scene": scene[:880],
            # exact on-screen words (buttons, menus, numbers) — the captain
            # grounds click targets on these, not on VLM prose
            "ocr_block": self.state.ocr_text[:380],
            "goals_block": blocks["goals"][:680],
            "recent_block": blocks["recent"][:880],
            "metrics_block": blocks["metrics"][:580],
            "lessons_block": blocks["lessons"][:680],
            "skills_block": blocks["skills"][:380],
            "mode": mode,
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
        if changed:
            self.state.note_activity()  # GM2 world-model: the world moved

        # GM2 wave 2 — OCR-fast lane: EVERY tick reads the whole frame (~300ms
        # CPU, zero VRAM). Metrics update and purchase pages get caught at tick
        # rate, even while the VLM lane sits out static frames.
        ocr_lines = await self._ocr_lines(png)
        self.state.note_ocr(ocr_lines)  # blackboard exact-text layer (LIMBS wave 1)
        metric_deltas, unresolved = self._metrics_from_lines(ocr_lines)
        purchase_now = self._detect_purchase(None, ocr_lines)
        if purchase_now and not self._purchase_active:
            self._handle_purchase_page()
        self._purchase_active = purchase_now

        # Causal closure at tick rate — but only when there's real evidence
        # (change or deltas). A static tick right after an action stays pending
        # so slow animations aren't misjudged as "NO visible effect".
        if changed or metric_deltas:
            self.mind.outcomes.observe_effect(screen_changed=changed, metric_deltas=metric_deltas)

        if not changed:
            self._gated_streak += 1
            # a live purchase page overrides the gate — she must SEE it to close it
            if not purchase_now and self._gated_streak < self.cfg.force_look_every:
                self.stats.gated += 1
                return
            # Static screen but she must still act (menus don't move themselves).
            logger.debug("play: forced look after %d static ticks", self._gated_streak)
        self._gated_streak = 0

        self.stats.looks += 1

        # VLM-slow lane: label-less metrics only, and only on scheduled looks.
        if unresolved and self.stats.looks % max(1, self.cfg.metric_every) == 1:
            metric_deltas += await self._metrics_vlm_fallback(png, unresolved)
        # Forced-look case: close a still-pending action as no-effect (no-op if
        # the tick-rate closure above already ran).
        self.mind.outcomes.observe_effect(screen_changed=changed, metric_deltas=metric_deltas)

        # Failure-gated reflection (Reflexion, cheap form): three actions with
        # no visible effect -> one lesson written, not a full critique loop.
        if self.mind.outcomes.failure_streak() >= 3:
            self.mind.add_lesson(
                f"the last approach isn't working near: {(self.state.context_block() or '')[-100:]}"
                " — try a different element or step back"
            )

        scene = await self._describe(png)
        if scene is None:
            return
        self.state.note_scene(scene)

        # Consequence ledger (GM2 step 4): purchase pages caught by the OCR-fast
        # lane above, or by the scene text here (OCR can miss stylized fonts).
        if not self._purchase_active and self._detect_purchase(scene, None):
            self._handle_purchase_page()
            self._purchase_active = True
        if self._purchase_active:
            scene = (
                "A REAL-MONEY PURCHASE PAGE IS OPEN (Robux). Do NOT buy anything. "
                "Close it now — press the X or Escape. | " + scene
            )

        # Strategist (the optimizer's eye): every 8 looks, or after a failure
        # streak, she studies the WHOLE position — numbers she's ignoring,
        # inefficiencies, a concrete plan — and it persists in her prompt.
        if self.stats.looks % 8 == 2 or self.mind.outcomes.failure_streak() >= 3:
            strat = await self._decide(scene, mode="strategy")
            if strat and strat.get("strategy"):
                self.mind.strategy = strat["strategy"][:400]
                logger.info("play strategy: %r", self.mind.strategy[:100])

        # Curriculum review (Voyager pattern): meta-cognition gets its own call —
        # pre-verified that the 4B never pushes/pops goals inline. Fires when the
        # stack is empty, or every 6 looks as a done-yet check.
        if self.mind.goals.current() is None or self.stats.looks % 6 == 0:
            review = await self._decide(scene, mode="curriculum")
            if review:
                ga, goal = review.get("goal_action", "keep"), review.get("goal", "")
                if ga in ("pop", "pop_then_push") and self.mind.goals.current():
                    done = self.mind.goals.pop()
                    self.state.note_event(f"goal done: {done[:60]}")
                if ga in ("push", "pop_then_push") and goal:
                    self.mind.goals.push(goal)
                    self.state.note_event(f"new goal: {goal[:60]}")
                    logger.info("play curriculum: %s -> %r", ga, goal[:80])

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

        await self._execute(action)
        self._log_trajectory(scene, decision, metric_deltas)

    async def _sequence_postcondition(self, label: str, ocr_before: str) -> None:
        """Fresh OCR after a completed multi-step sequence (skill replay or
        motor plan): identical on-screen text means it did nothing visible —
        record the lesson immediately instead of waiting for a no-effect
        streak."""
        if self._hwnd is None:
            return
        png = await asyncio.to_thread(capture_window_png, self._hwnd)
        if png is None:
            return
        lines = await self._ocr_lines(png)
        if lines is None:
            return
        self.state.note_ocr(lines)
        if self.state.ocr_text == ocr_before:
            self.mind.add_lesson(
                f"{label} ran fully but nothing on screen changed — "
                "it may be broken or wrong for this situation"
            )
            self.state.note_event(f"{label} had no visible effect")
            logger.info("play: %s postcondition failed (no OCR change)", label)

    async def _fetch_plan(self, intent: str) -> Optional[list[dict]]:
        """Ask the motor planner for steps. Returns the step list, [] on planner
        error/empty, or None on transport failure. Split out so the closed-loop
        executor is testable without a live orchestrator."""
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{self.cfg.orchestrator_url}/v1/games/plan",
                    json={
                        "request_id": f"plan_{self.stats.cycles}",
                        "game": self.cfg.game,
                        "intent": intent[:200],
                        "ocr_block": self.state.ocr_text[:380],
                        "knowledge": self.cfg.knowledge[:700],
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("play plan request failed: %s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("play plan %d: %s", resp.status_code, resp.text[:120])
            return []
        return resp.json().get("steps") or []

    async def _execute_plan(self, intent: str) -> None:
        """Motor-planner delegation (LIMBS W1.2): captain intent → schema-locked
        steps from /v1/games/plan, executed CLOSED-LOOP — rule guard per step,
        fresh OCR + purchase tripwire between steps, postcondition at the end."""
        if not intent:
            return
        steps = await self._fetch_plan(intent)
        if steps is None:
            return
        if not steps:
            self.mind.outcomes.action_taken(f"FAILED plan '{intent[:50]}' (no steps)")
            return

        self.mind.outcomes.action_taken(f"plan '{intent[:50]}' ({len(steps)} steps)")
        self.state.note_event(f"planned: {intent[:60]} ({len(steps)} steps)")
        ocr_before = self.state.ocr_text
        completed = True
        for step in steps:
            desc = " ".join(str(v) for v in step.values())[:80]
            rule = self.mind.rules.banned(desc)
            if rule:  # learned rules are CODE — they bind inside plans too
                self.stats.refused += 1
                self.state.note_event(f"plan step refused by rule: {rule[:60]}")
                completed = False
                break
            result = await self.hands.act(step)
            if not result.get("ok"):
                completed = False
                break
            await asyncio.sleep(0.8)
            # Closed loop between steps: fresh eyes + the purchase tripwire.
            # ~300 ms OCR per step is cheap insurance against a mid-plan popup.
            if self._hwnd is not None:
                png = await asyncio.to_thread(capture_window_png, self._hwnd)
                if png:
                    lines = await self._ocr_lines(png)
                    if lines is not None:
                        self.state.note_ocr(lines)
                        if self._detect_purchase(None, lines):
                            self._handle_purchase_page()
                            self._purchase_active = True
                            completed = False
                            break
        if completed and ocr_before:
            await self._sequence_postcondition(f"plan '{intent[:40]}'", ocr_before)

    async def _execute(self, action: dict) -> None:
        """Run one decided action — hands for the physical, mind for the meta."""
        kind = action.get("type")
        if kind in (None, "look"):
            return

        # Meta-actions: her own goal/skill management. No hands involved.
        if kind == "push_goal":
            self.mind.goals.push(action.get("goal", ""))
            self.state.note_event(f"new goal: {action.get('goal', '')[:60]}")
            return
        if kind == "pop_goal":
            done = self.mind.goals.pop()
            if done:
                self.state.note_event(f"goal done: {done[:60]}")
            return
        if kind == "save_skill":
            # LIMBS wave 1: the skill remembers the screen it was born on —
            # the blackboard's exact-text words become its precondition.
            ok = self.mind.skills.save(
                action.get("name", ""), self.mind.recent_success_steps,
                context_words=ocr_keywords(self.state.ocr_text),
            )
            self.state.note_event(f"skill saved: {action.get('name', '')}" if ok
                                  else "skill save failed (nothing to save)")
            return
        if kind == "skill":
            name = action.get("name", "")
            # Precondition (LIMBS wave 1): a skill saved on one screen refuses
            # to fire blind on another — closed-loop, never open-loop.
            ctx_words = self.mind.skills.context_words(name)
            if not precondition_ok(ctx_words, self.state.ocr_text):
                self.stats.refused += 1
                self.mind.outcomes.action_taken(
                    f"REFUSED skill '{name}' (wrong screen — it expects "
                    f"{', '.join(ctx_words[:3])})"
                )
                self.mind.outcomes.observe_effect(screen_changed=False, metric_deltas=[])
                self.state.note_event(f"skill {name} refused: wrong screen for it")
                return
            steps = self.mind.skills.get(name)
            if not steps:
                self.mind.outcomes.action_taken(f"FAILED skill '{name}' (unknown)")
                return
            self.mind.outcomes.action_taken(f"skill {name} ({len(steps)} steps)")
            ocr_before = self.state.ocr_text
            completed = True
            for step in steps:
                _step_desc = " ".join(str(v) for v in step.values())[:80]
                _rule = self.mind.rules.banned(_step_desc)
                if _rule:  # a saved skill can contain a later-banned action
                    self.stats.refused += 1
                    self.state.note_event(f"skill step refused by rule: {_rule[:60]}")
                    completed = False
                    break
                result = await self.hands.act(step)
                if not result.get("ok"):
                    completed = False
                    break
                await asyncio.sleep(0.8)
            # Postcondition (LIMBS wave 1): a completed skill must leave a
            # visible trace. OCR-only compare — the FrameGate baseline stays
            # untouched so next tick's causal closure still judges honestly.
            if completed and ocr_before:
                await self._sequence_postcondition(f"skill '{name}'", ocr_before)
            return
        if kind == "plan":
            await self._execute_plan(str(action.get("intent") or ""))
            return

        # Physical action through the hands (rails intact).
        desc = " ".join(str(v) for v in action.values())[:80]
        # GM2 step 4: learned rules are CODE, not prompt hope — a banned target
        # never reaches the hands ("pressed it once, never again").
        rule = self.mind.rules.banned(desc)
        if rule:
            self.stats.refused += 1
            self.mind.outcomes.action_taken(f"REFUSED (learned rule): {desc[:60]}")
            self.mind.outcomes.observe_effect(screen_changed=False, metric_deltas=[])
            self.state.note_event(f"refused by learned rule: {rule[:60]}")
            logger.info("play: action refused by rulebook: %r (%s)", desc[:60], rule[:60])
            return
        result = await self.hands.act(action)
        if result.get("ok"):
            self.stats.actions_done += 1
            self.mind.outcomes.action_taken(desc)
            self.mind.note_successful_action(action)
            self.state.note_event(f"did: {result.get('detail', '')[:80]}")
        else:
            self.stats.refused += 1
            self.mind.outcomes.action_taken(f"FAILED {desc}")
            self.state.note_event(f"failed: {result.get('detail', '')[:80]}")

    def _log_trajectory(self, scene: str, decision: dict, metric_deltas: list[str]) -> None:
        """One SFT-ready line per cycle (prompt side and completion kept separate)."""
        import json as _json
        try:
            self._traj_path.parent.mkdir(parents=True, exist_ok=True)
            with self._traj_path.open("a", encoding="utf-8") as f:
                f.write(_json.dumps({
                    "ts": time.time(),
                    "cycle": self.stats.cycles,
                    "game": self.cfg.game,
                    "genre": self.cfg.genre,
                    "blocks": self.mind.prompt_blocks(),
                    "scene": scene[:880],
                    "completion_raw": decision.get("raw", ""),
                    "parsed": {"task_state": decision.get("task_state"),
                               "action": decision.get("action"),
                               "say": decision.get("say")},
                    "metric_deltas": metric_deltas,
                }, ensure_ascii=False) + "\n")
        except Exception:
            # WARNING, not debug — invisible failures cost a day of sleep
            # debugging once already (failure signals must be visible).
            logger.warning("trajectory log failed", exc_info=True)

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
