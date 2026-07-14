"""The Game Mind — what turns 'she sees buttons' into 'she plays games'.

Research-grounded (Voyager curriculum, Cradle skill curation, Reflexion
failure-notes, objective-stack persistence — verified refs in master_queue
GAME MIND ARC, 2026-07-05). Pure state module: no I/O, no LLM calls — the
PlaySession owns wiring, this owns memory and prompt blocks.

Components:
  ObjectiveStack  — she PUSHes/POPs her own goals via meta-DOs; stack renders
                    into every decide prompt (depth-first intent that survives
                    distractions).
  OutcomeLog      — (action → observed effect) pairs; the causal memory that
                    teaches what buttons DO, not just that they exist.
  ProgressTracker — numeric metrics (money, level, paperclips) sampled via
                    targeted vision questions; progression made visible.
  SkillLibrary    — named replayable action sequences she chooses to save
                    (save_skill) and reuse (skill <name>). Persisted per game.
  Lessons         — failure-gated reflections, short and few.
"""
from __future__ import annotations

import json
import logging
import re as _re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("koroki.gamemind")

_REPO_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = _REPO_ROOT / "data" / "game" / "skills"
TRAJ_DIR = _REPO_ROOT / "data" / "game" / "trajectories"
RULES_DIR = _REPO_ROOT / "data" / "game" / "rules"

# Genre baselines: the END goal she starts from + which numbers to watch.
# The curriculum is hers — she pushes intermediate goals herself.
# Metrics are dicts since 2026-07-06: "q" is the VLM question (fallback path),
# "labels" are the on-screen label keywords the OCR pass matches (primary path
# — one ~300 ms CPU read returns EVERY metric; the VLM took 1-31 s per question
# and misread numbers).
GENRE_TEMPLATES: dict[str, dict] = {
    "idle_incremental": {
        "final_goal": "reach the game's ending by growing the core number through "
                      "every phase and unlocking each new mechanic as it appears",
        "metrics": [
            {"q": "the main resource count shown on screen",
             "labels": ["paperclips", "clips", "resources", "points", "score"]},
            {"q": "the amount of money/funds shown, if any",
             "labels": ["funds", "money", "cash", "balance"]},
        ],
    },
    "tycoon": {
        "final_goal": "grow the business: increase money, buy every upgrade tier, "
                      "unlock new areas until nothing new is left to unlock",
        "metrics": [
            {"q": "the money/cash amount shown on screen",
             "labels": ["money", "cash", "funds", "balance", "gold", "coins"]},
        ],
    },
    "platformer": {
        "final_goal": "reach the end of each level; finish the game",
        "metrics": [
            {"q": "the level number or name shown, if any",
             "labels": ["level", "stage", "world"]},
        ],
    },
    "puzzle": {
        "final_goal": "solve the current puzzle, then the next; clear all of them",
        "metrics": [
            {"q": "the level or puzzle number shown, if any",
             "labels": ["level", "puzzle", "stage"]},
        ],
    },
    "sandbox": {
        "final_goal": "set your own milestones and complete them; build toward "
                      "the deepest thing the game lets you make",
        "metrics": [],
    },
}


@dataclass
class Goal:
    text: str
    pushed_ts: float = field(default_factory=time.time)


class ObjectiveStack:
    def __init__(self, final_goal: str):
        self.final_goal = final_goal
        self._stack: list[Goal] = []

    def push(self, text: str) -> None:
        self._stack.append(Goal(text[:160]))

    def pop(self) -> str | None:
        return self._stack.pop().text if self._stack else None

    def current(self) -> str | None:
        return self._stack[-1].text if self._stack else None

    def block(self) -> str:
        lines = [f"FINAL GOAL: {self.final_goal}"]
        if self._stack:
            for i, g in enumerate(self._stack):
                marker = "-> NOW" if i == len(self._stack) - 1 else f"  ({i + 1})"
                lines.append(f"{marker} {g.text}")
        else:
            lines.append("(no current goal — push one with: DO: push_goal <goal>)")
        return "\n".join(lines)


class OutcomeLog:
    """Action -> effect pairs. The last one may be 'pending' until the next look."""

    def __init__(self, keep: int = 6):
        self.keep = keep
        self._rows: list[dict] = []
        self._pending: dict | None = None

    def action_taken(self, action_desc: str) -> None:
        self._pending = {"action": action_desc[:100], "effect": None}

    def observe_effect(self, screen_changed: bool, metric_deltas: list[str]) -> None:
        if self._pending is None:
            return
        if metric_deltas:
            effect = "; ".join(metric_deltas)[:120]
        elif screen_changed:
            effect = "screen changed"
        else:
            effect = "NO visible effect"
        self._pending["effect"] = effect
        self._rows.append(self._pending)
        self._rows = self._rows[-self.keep:]
        self._pending = None

    def failure_streak(self) -> int:
        n = 0
        for row in reversed(self._rows):
            if "NO visible effect" in (row["effect"] or "") or row["action"].startswith("FAILED"):
                n += 1
            else:
                break
        return n

    def last_action(self) -> str | None:
        """Most recent action (pending first) — the consequence ledger's suspect
        when a purchase page suddenly appears (GM2 step 4)."""
        if self._pending is not None:
            return self._pending["action"]
        return self._rows[-1]["action"] if self._rows else None

    def block(self) -> str:
        if not self._rows:
            return ""
        lines = ["RECENT ACTIONS AND WHAT THEY DID:"]
        for row in self._rows:
            lines.append(f"- {row['action']} -> {row['effect']}")
        return "\n".join(lines)


class ProgressTracker:
    """Numeric progress from OCR (primary) / vision answers (fallback).

    History is keyed by the metric's question string; deltas come from value
    changes regardless of which eye read them.
    """

    def __init__(self, metrics: list):
        # Accept both shapes: dicts ({"q", "labels"}) and legacy plain strings.
        self.metric_defs: list[dict] = [
            m if isinstance(m, dict) else {"q": str(m), "labels": []} for m in metrics
        ]
        self.questions = [m["q"] for m in self.metric_defs]
        self._history: dict[str, list[str]] = {q: [] for q in self.questions}

    def record(self, question: str, answer: str) -> str | None:
        """Store an answer; return a delta string if the value changed."""
        answer = (answer or "").strip()[:60]
        if not answer:
            return None
        hist = self._history.setdefault(question, [])
        delta = None
        if hist and hist[-1] != answer:
            delta = f"{question[:40]}: {hist[-1]} -> {answer}"
        hist.append(answer)
        del hist[:-8]
        return delta

    def block(self) -> str:
        rows = []
        for q, hist in self._history.items():
            if hist:
                trail = " -> ".join(hist[-3:])
                rows.append(f"- {q[:44]}: {trail}")
        return ("PROGRESS METRICS:\n" + "\n".join(rows)) if rows else ""


def ocr_keywords(ocr_text: str, n: int = 6) -> list[str]:
    """Distinctive on-screen words at skill-save time — the skill's implicit
    precondition (LIMBS wave 1: closed-loop skills, never open-loop macros).
    Numbers are skipped (they change); short tokens are UI noise."""
    words: list[str] = []
    for tok in _re.split(r"[^a-zA-Z0-9']+", ocr_text.lower()):
        if len(tok) >= 3 and not tok.isdigit() and tok not in words:
            words.append(tok)
        if len(words) >= n:
            break
    return words


def precondition_ok(context_words: list[str] | None, ocr_text: str) -> bool:
    """A skill saved on one screen must not fire blind on another. Unknown
    context (old skills, empty OCR at save time) stays permissive."""
    if not context_words:
        return True
    hay = ocr_text.lower()
    return any(w in hay for w in context_words)


class SkillLibrary:
    """Named replayable action sequences, persisted per game.

    She curates it herself: 'DO: save_skill <name>' stores the recent successful
    actions; 'DO: skill <name>' replays one. Cradle's skill-curation idea at
    the scale a 12GB single-PC life needs. Since LIMBS wave 1 a skill also
    remembers the screen it was born on (context_words) — replay on the wrong
    screen refuses instead of clicking into the void.
    """

    def __init__(self, game_slug: str):
        self._path = SKILLS_DIR / f"{game_slug}.json"
        self.skills: dict[str, dict] = {}
        try:
            self.skills = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("skill library load failed: %s", exc)

    def save(self, name: str, steps: list[dict], note: str = "",
             context_words: list[str] | None = None) -> bool:
        name = name.strip().lower().replace(" ", "_")[:40]
        if not name or not steps:
            return False
        self.skills[name] = {"steps": steps[-8:], "note": note[:120],
                             "context_words": (context_words or [])[:6],
                             "saved_ts": time.time(), "uses": 0}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.skills, indent=1), encoding="utf-8")
        except Exception as exc:
            logger.warning("skill library save failed: %s", exc)
        return True

    def get(self, name: str) -> list[dict] | None:
        entry = self.skills.get(name.strip().lower().replace(" ", "_"))
        if entry:
            entry["uses"] = entry.get("uses", 0) + 1
            return list(entry["steps"])
        return None

    def context_words(self, name: str) -> list[str]:
        """Precondition words for a skill; [] for pre-wave-1 entries (permissive)."""
        entry = self.skills.get(name.strip().lower().replace(" ", "_"))
        return list(entry.get("context_words") or []) if entry else []

    def block(self) -> str:
        if not self.skills:
            return ""
        names = ", ".join(sorted(self.skills)[:10])
        return (f"SAVED SKILLS (reuse with 'DO: skill <name>'): {names}")


class RuleBook:
    """Learned hard constraints, persisted per game (GM2 step 4, owner-directed
    2026-07-08: "she pressed this once, next time she knows... no pressing that
    again"). COSTLY/DANGEROUS outcomes become rules that (a) render as a HARD
    RULES prompt block and (b) game_agent enforces IN CODE before the hands
    move — prompt hope is not a guard. Rules graduate into the game card so
    they survive across sessions and models.
    """

    def __init__(self, game_slug: str, game_display: str = ""):
        self._path = RULES_DIR / f"{game_slug}.json"
        self.game_display = game_display or game_slug
        self.rules: list[dict] = []
        try:
            self.rules = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("rulebook load failed: %s", exc)

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                                for c in (text or "").lower()).split())

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.rules, indent=1), encoding="utf-8")
        except Exception as exc:
            logger.warning("rulebook save failed: %s", exc)

    def add(self, target: str, rule_text: str, klass: str = "COSTLY") -> bool:
        pattern = self._norm(target)[:80]
        if not pattern:
            return False
        if any(r["pattern"] == pattern for r in self.rules):
            return True  # already learned
        self.rules.append({"pattern": pattern, "rule": rule_text.strip()[:160],
                           "class": klass, "learned_ts": time.time(), "hits": 0})
        self._save()
        logger.info("RULE LEARNED (%s): %r -> %s", klass, pattern, rule_text[:80])
        try:  # graduate into her permanent save file for this game
            import game_knowledge
            game_knowledge.append_lesson(self.game_display, f"RULE: {rule_text.strip()[:140]}")
        except Exception as exc:
            logger.warning("rule graduation to card failed: %s", exc)
        return True

    def banned(self, action_desc: str) -> str | None:
        """The rule text if this action matches a learned ban, else None."""
        hay = self._norm(action_desc)
        if not hay:
            return None
        for r in self.rules:
            if r["pattern"] and r["pattern"] in hay:
                r["hits"] = r.get("hits", 0) + 1
                self._save()
                return r["rule"]
        return None

    def block(self) -> str:
        if not self.rules:
            return ""
        lines = ["HARD RULES for this game — NEVER violate these:"]
        for r in self.rules[-6:]:
            lines.append(f"- {r['rule']}")
        return "\n".join(lines)


class GameMind:
    """One object per play session, holding all the above + lessons."""

    def __init__(self, game: str, genre: str = "sandbox", final_goal: str | None = None):
        tpl = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["sandbox"])
        self.genre = genre
        self.goals = ObjectiveStack(final_goal or tpl["final_goal"])
        self.outcomes = OutcomeLog()
        self.progress = ProgressTracker(list(tpl["metrics"]))
        slug = "".join(c if c.isalnum() else "_" for c in game.lower())[:40]
        self.skills = SkillLibrary(slug)
        self.rules = RuleBook(slug, game)
        self.lessons: list[str] = []
        self.recent_success_steps: list[dict] = []   # feed for save_skill
        self.strategy: str = ""                      # the optimizer's latest read

    def add_lesson(self, text: str) -> None:
        text = text.strip()[:160]
        if text and text not in self.lessons:
            self.lessons.append(text)
            self.lessons = self.lessons[-4:]

    def note_successful_action(self, action: dict) -> None:
        self.recent_success_steps.append(action)
        self.recent_success_steps = self.recent_success_steps[-8:]

    def lessons_block(self) -> str:
        if not self.lessons:
            return ""
        return "LESSONS FROM EARLIER FAILURES:\n" + "\n".join(f"- {l}" for l in self.lessons)

    def prompt_blocks(self) -> dict[str, str]:
        lessons = self.lessons_block()
        if self.strategy:
            strategy = "CURRENT STRATEGY (your own analysis):\n" + self.strategy
            lessons = f"{strategy}\n{lessons}" if lessons else strategy
        # HARD RULES lead the lessons block — highest-priority learned knowledge
        # (the code-level guard in game_agent enforces them regardless).
        rules = self.rules.block()
        if rules:
            lessons = f"{rules}\n{lessons}" if lessons else rules
        return {
            "goals": self.goals.block(),
            "recent": self.outcomes.block(),
            "metrics": self.progress.block(),
            "lessons": lessons,
            "skills": self.skills.block(),
        }
