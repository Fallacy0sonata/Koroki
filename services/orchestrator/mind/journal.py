"""Koroki's experience journal — the autobiographical layer her life was evaporating without.

Before this module (2026-07-02): thoughts pruned at 24h, sleep consolidation only logged and
decayed importances, self_history.md was static. She could not truthfully answer "what did you
do yesterday" — and roadmap Phase 5 (retrain on lived experience) had no data accumulating.

Design (captain-in-cabin: subsystems record, the LLM only voices):
  - An append-only experience stream: one JSONL file per local calendar day at
    data/koroki/journal/YYYY-MM-DD.jsonl. Events are small typed records —
    activity transitions, internal thoughts, mood samples, notable interactions,
    sleep/wake markers. Writers call `journal().log_event(...)`; it is cheap and lock-safe.
  - Nightly consolidation: `consolidate_pending()` compiles every finished day (jsonl without
    a matching .md) into a compact human-readable day entry YYYY-MM-DD.md — deterministic
    template text (no LLM on the critical path; an optional voicing pass can rewrite entries
    in her own words later). Hooked from meta/sleep_cycle.consolidate() and self-healing on
    date rollover: the first event of a new day triggers consolidation of prior days.
  - Recall: `recent_entries(n)` returns the last n day entries for prompt injection
    (identity/introspective contexts, alongside self_history), and `today_line()` gives a
    one-line "so far today" for lightweight prompt use.

This stream is ALSO the Phase 5/6 training corpus, accumulating from day one.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("orchestrator.mind.journal")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_JOURNAL_DIR = _REPO_ROOT / "data" / "koroki" / "journal"

# Event kinds with dedicated rendering in the day entry. Unknown kinds still log fine.
KIND_ACTIVITY = "activity"
KIND_THOUGHT = "thought"
KIND_MOOD = "mood"
KIND_INTERACTION = "interaction"
KIND_SLEEP = "sleep"
KIND_SING = "sing"
KIND_WORLD = "world_event"
KIND_DREAM = "dream"


def _local_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d")


def _local_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class Journal:
    """Append-only daily experience stream + day-entry consolidation."""

    def __init__(self, journal_dir: Path | None = None):
        self._dir = journal_dir or _JOURNAL_DIR
        self._lock = threading.Lock()
        self._known_day = _local_day()

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    def log_event(self, kind: str, text: str, meta: dict | None = None,
                  ts: float | None = None) -> None:
        """Append one experience event. Cheap; never raises to callers."""
        try:
            ts = ts or time.time()
            day = _local_day(ts)
            record = {"ts": ts, "kind": kind, "text": text}
            if meta:
                record["meta"] = meta
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with open(self._dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                # rollover only counts FORWARD (ISO dates compare lexically) — a backdated
                # write must not trigger consolidation of a day still receiving events
                rolled = day > self._known_day
                self._known_day = max(self._known_day, day)
            if rolled:
                # date rollover — consolidate finished days in the background
                threading.Thread(target=self.consolidate_pending, daemon=True).start()
        except Exception as exc:  # journaling must never break the caller
            logger.warning("journal write failed: %s", exc)

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def read_day(self, day: str) -> list[dict]:
        path = self._dir / f"{day}.jsonl"
        events: list[dict] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("journal read failed for %s: %s", day, exc)
        return events

    def today_line(self) -> str:
        """One short line about her day so far — for lightweight prompt injection."""
        events = self.read_day(_local_day())
        if not events:
            return ""
        acts = [e for e in events if e.get("kind") == KIND_ACTIVITY]
        thoughts = [e for e in events if e.get("kind") == KIND_THOUGHT]
        parts = []
        if acts:
            recent = acts[-3:]
            names = [a.get("meta", {}).get("name") or a.get("text", "") for a in recent]
            names = [n for n in names if n]
            if names:
                parts.append("today so far: " + ", ".join(names))
        if thoughts:
            parts.append(f'a thought that stuck: "{thoughts[-1].get("text", "")}"')
        return " | ".join(parts)

    def recent_entries(self, n: int = 3) -> list[tuple[str, str]]:
        """Last n consolidated day entries as (day, markdown text), newest first.
        Prefers the LLM-voiced version (her own diary voice) when one exists —
        the template .md stays canonical."""
        out: list[tuple[str, str]] = []
        try:
            mds = sorted((p for p in self._dir.glob("*.md")
                          if not p.name.endswith(".voiced.md")), reverse=True)
            for p in mds[:n]:
                voiced = self._dir / f"{p.stem}.voiced.md"
                src = voiced if voiced.exists() else p
                out.append((p.stem, src.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("journal recall failed: %s", exc)
        return out

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def consolidate_pending(self) -> int:
        """Compile every finished day (jsonl older than today, no .md yet) into a day entry.
        Returns the number of days consolidated. Deterministic template — no LLM."""
        done = 0
        today = _local_day()
        try:
            for p in sorted(self._dir.glob("*.jsonl")):
                day = p.stem
                if day >= today:
                    continue
                md = self._dir / f"{day}.md"
                # rebuild when missing OR when the day file gained events after the
                # entry was written (late/backdated writes)
                if md.exists() and md.stat().st_mtime >= p.stat().st_mtime:
                    continue
                entry = self._build_day_entry(day, self.read_day(day))
                if entry:
                    (self._dir / f"{day}.md").write_text(entry, encoding="utf-8")
                    done += 1
                    logger.info("journal: consolidated %s", day)
                    # optional voicing pass runs in the background — consolidation can be
                    # triggered from the chat path and must never wait on the Brain
                    threading.Thread(target=self._voice_entry, args=(day, entry),
                                     daemon=True, name=f"journal-voice-{day}").start()
        except Exception as exc:
            logger.warning("journal consolidation failed: %s", exc)
        return done

    def _voice_entry(self, day: str, template_entry: str) -> None:
        """Rewrite the template day entry in her own diary voice via one Brain call.
        Writes <day>.voiced.md; the template .md remains the canonical record.
        Silently skips when disabled or the Brain is unreachable."""
        try:
            from shared.utils.config import get_settings
            settings = get_settings()
            if not bool(settings.get("mind", {}).get("journal", {}).get("llm_voice", True)):
                return
            import httpx
            url = settings["services"]["brain"]["url"] + "/v1/generate"
            payload = {
                "request_id": f"journal_voice_{day}",
                "message": (
                    "[internal] Below is the factual log of your day. Rewrite it as YOUR "
                    "diary entry — first person, your voice, keep every fact, keep it under "
                    "150 words, no headings, no preamble.\n\n" + template_entry
                ),
                "user_context": {
                    "user_id": "__journal_voice__",
                    "relationship_score": 100,
                    "is_owner": False,
                    "mode": "auto",
                    "platform": "desktop",
                    "core_facts": ["koroki_mode=journal_voicing"],
                },
                "max_new_tokens": 260,
                "enable_thinking": False,
                "forbidden_phrases": [],
            }
            with httpx.Client(timeout=60.0) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                text = (r.json().get("text") or "").strip()
            if len(text) < 40:
                return
            (self._dir / f"{day}.voiced.md").write_text(
                f"# {day} — in her words\n\n{text}\n", encoding="utf-8")
            logger.info("journal: voiced %s", day)
        except Exception as exc:
            logger.info("journal voicing skipped for %s (%s)", day, exc)

    def _build_day_entry(self, day: str, events: list[dict]) -> str:
        if not events:
            return ""
        # activities: merge consecutive same-name segments into spans with durations
        spans: list[tuple[str, float, float]] = []  # (name, start, end)
        for e in events:
            if e.get("kind") != KIND_ACTIVITY:
                continue
            name = e.get("meta", {}).get("name") or e.get("text", "?")
            ts = float(e.get("ts", 0))
            if spans and spans[-1][0] == name:
                spans[-1] = (name, spans[-1][1], ts)
            else:
                if spans:
                    spans[-1] = (spans[-1][0], spans[-1][1], ts)
                spans.append((name, ts, ts))

        thoughts = [e for e in events if e.get("kind") == KIND_THOUGHT]
        moods = [e for e in events if e.get("kind") == KIND_MOOD]
        interactions = [e for e in events if e.get("kind") == KIND_INTERACTION]
        sleeps = [e for e in events if e.get("kind") == KIND_SLEEP]
        sings = [e for e in events if e.get("kind") == KIND_SING]
        world_evs = [e for e in events if e.get("kind") == KIND_WORLD]
        dreams = [e for e in events if e.get("kind") == KIND_DREAM]

        lines = [f"# {day}", ""]
        if dreams:
            lines.append("## Dreamt")
            for e in dreams:
                lines.append(f"- {e.get('text', '')}")
            lines.append("")
        if spans:
            lines.append("## The day")
            for name, start, _end in spans:
                lines.append(f"- {_local_hm(start)} — {name.replace('_', ' ')}")
            lines.append("")
        if moods:
            lines.append("## Mood arc")
            step = max(1, len(moods) // 6)  # at most ~6 samples in the entry
            for e in moods[::step]:
                lines.append(f"- {_local_hm(float(e['ts']))} — {e.get('text', '')}")
            lines.append("")
        if thoughts:
            lines.append("## Thoughts that surfaced")
            for e in thoughts:
                lines.append(f"- {_local_hm(float(e['ts']))} — {e.get('text', '')}")
            lines.append("")
        if interactions:
            lines.append("## People")
            for e in interactions[:12]:
                lines.append(f"- {_local_hm(float(e['ts']))} — {e.get('text', '')}")
            lines.append("")
        if sings:
            lines.append("## Singing")
            for e in sings:
                lines.append(f"- {_local_hm(float(e['ts']))} — {e.get('text', '')}")
            lines.append("")
        if world_evs:
            lines.append("## The world outside")
            for e in world_evs[:10]:
                lines.append(f"- {_local_hm(float(e['ts']))} — {e.get('text', '')}")
            lines.append("")
        if sleeps:
            marks = ", ".join(f"{_local_hm(float(e['ts']))} {e.get('text', '')}" for e in sleeps)
            lines.append(f"_Sleep: {marks}_")
            lines.append("")
        return "\n".join(lines).strip() + "\n"


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------

_INSTANCE: Journal | None = None
_INSTANCE_LOCK = threading.Lock()


def _boot_consolidation(j: Journal) -> None:
    """Consolidate finished days shortly after boot. Without this, a day whose midnight
    passed while the stack was DOWN never consolidates until she next sleeps (found
    2026-07-03: she stayed awake all night, so yesterday's diary never got written).
    Delayed 90 s so the Brain has time to come up — the voicing pass needs it."""
    time.sleep(90)
    try:
        j.consolidate_pending()
    except Exception:
        logger.warning("boot consolidation failed", exc_info=True)


def journal() -> Journal:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = Journal()
                threading.Thread(target=_boot_consolidation, args=(_INSTANCE,),
                                 daemon=True, name="journal-boot-consolidation").start()
    return _INSTANCE


def yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
