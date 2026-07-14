"""Game knowledge cards — her permanent save files, per game (GM2 step 2).

Owner's structure (2026-07-08): platform-grouped cards + a registry with alias
fuzzy-matching, so "sol" / "sol rng" / "sol's rng" all resolve to the same card
and she never re-learns a known game. Unknown game -> a skeleton card is born
and learning starts.

Layout:
    data/game/knowledge/registry.json
    data/game/knowledge/<platform>/<slug>.md

Card sections (markdown):
    ## For her          <- the ONLY part injected into prompts (4B law: short)
    ## Notes            <- owner/Claude long-form, never injected
    ## Lessons          <- appended by the consequence ledger (GM2 step 4);
                           distilled lessons graduate into "## For her"

Used by: game_agent.py (decide `knowledge` field), discord_bot watch loop
(scene bracket), future consequence ledger.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_KNOW_DIR = _ROOT / "data" / "game" / "knowledge"
_REGISTRY = _KNOW_DIR / "registry.json"
_LOCK = threading.Lock()

_FOR_HER_RE = re.compile(r"##\s*For her\s*\n(.*?)(?=\n##\s|\Z)", re.S | re.I)
_LESSONS_RE = re.compile(r"(##\s*Lessons\s*\n)", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "game").lower()).strip("_") or "game"


def _load_registry() -> dict:
    try:
        return json.loads(_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return {"games": []}


def _save_registry(reg: dict) -> None:
    _KNOW_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve(name: str) -> dict | None:
    """Name/alias -> registry entry. Exact alias, then containment, then fuzzy."""
    q = _norm(name)
    if not q:
        return None
    games = _load_registry().get("games", [])
    for g in games:  # exact normalized alias/display/slug
        candidates = {_norm(g.get("display", ""))} | {_norm(a) for a in g.get("aliases", [])}
        candidates.add(_norm(g.get("slug", "")))
        if q in candidates:
            return g
    for g in games:  # containment either way ("sol" in "sol s rng")
        for c in {_norm(g.get("display", ""))} | {_norm(a) for a in g.get("aliases", [])}:
            if c and (q in c or c in q):
                return g
    best, best_score = None, 0.0
    for g in games:  # fuzzy last
        for c in {_norm(g.get("display", ""))} | {_norm(a) for a in g.get("aliases", [])}:
            score = difflib.SequenceMatcher(None, q, c).ratio() if c else 0.0
            if score > best_score:
                best, best_score = g, score
    return best if best_score >= 0.75 else None


def card_path(entry: dict) -> Path:
    return _KNOW_DIR / entry.get("platform", "misc") / f"{entry['slug']}.md"


def ensure_card(name: str, platform: str = "misc") -> dict:
    """Resolve; if unknown, register + create a skeleton card (learning starts)."""
    with _LOCK:
        hit = resolve(name)
        if hit:
            return hit
        entry = {
            "slug": _slugify(name),
            "platform": platform,
            "display": name.strip() or "Unknown Game",
            "aliases": [name.strip().lower()],
        }
        reg = _load_registry()
        reg.setdefault("games", []).append(entry)
        _save_registry(reg)
        p = card_path(entry)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(
                f"# {entry['display']}\n\n"
                "## For her\n"
                f"{entry['display']}: unknown game — first time seeing it. "
                "Watch carefully, act cautiously, never touch anything that "
                "mentions real money, robux, or purchases.\n\n"
                "## Notes\n(new card — owner/Claude fill in what this game is)\n\n"
                "## Lessons\n",
                encoding="utf-8",
            )
        return entry


def prompt_summary(name: str, limit: int = 600) -> str:
    """The '## For her' block for a game name, or '' if unknown. Prompt-sized."""
    entry = resolve(name)
    if not entry:
        return ""
    try:
        text = card_path(entry).read_text(encoding="utf-8")
    except Exception:
        return ""
    m = _FOR_HER_RE.search(text)
    if not m:
        return ""
    summary = re.sub(r"\s+", " ", m.group(1)).strip()
    return summary[:limit]


def append_lesson(name: str, lesson: str) -> bool:
    """Persist a learned lesson into the card (consequence ledger writes here)."""
    entry = resolve(name) or ensure_card(name)
    p = card_path(entry)
    with _LOCK:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return False
        line = f"- {lesson.strip()}\n"
        if line in text:
            return True  # already learned
        if _LESSONS_RE.search(text):
            text = _LESSONS_RE.sub(r"\g<1>" + line, text, count=1)
        else:
            text += f"\n## Lessons\n{line}"
        p.write_text(text, encoding="utf-8")
        return True
