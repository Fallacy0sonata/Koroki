"""Multi-day projects — her pastimes gain continuity, days become arcs.

Before this module her activities were moment-scale: she "reads", but never a book she's
halfway through. Now certain activities attach to a persistent project ("reading
'Convenience Store Woman' — day 3", "learning the Racing into the Night bridge"), which:
  - enriches the activity's felt-state line and journal entries with the project name,
  - progresses a little each session and eventually COMPLETES (journaled — "finished it"),
  - gives conversation real continuity ("how's the song coming?" has a true answer).

Captain-in-cabin: the subsystem owns project state and progress; the LLM only ever
reads/voices it. Zero LLM calls here.

Activity-kind mapping: reading→book · singing_practice→song · doodling→art.
State: data/mind/projects.json (survives restarts).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("orchestrator.mind.projects")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _REPO_ROOT / "data" / "mind" / "projects.json"

# Pools of plausible projects per kind. Names are real/realistic so her life reads
# authentic; recently used ones are avoided.
_POOLS: dict[str, list[str]] = {
    "book": [
        "Convenience Store Woman", "Before the Coffee Gets Cold", "Kitchen",
        "The Housekeeper and the Professor", "Norwegian Wood", "Kafka on the Shore",
        "I Want to Eat Your Pancreas", "The Travelling Cat Chronicles",
        "Sweet Bean Paste", "Days at the Morisaki Bookshop",
    ],
    "song": [
        "Idol", "Racing into the Night", "Monster", "Gunjou", "Tabun",
        "Mister", "Haruka", "Loveletter", "Adventure", "Biri-Biri",
    ],
    "art": [
        "the skyline at dusk", "the bird that visits the sill", "her own left hand",
        "the tea mug going cold", "rain on the window glass", "the bedroom at night",
        "clouds she saw yesterday", "a self-portrait she'll never show anyone",
    ],
}

_KIND_VERBS = {"book": "reading", "song": "practicing", "art": "sketching"}

# progress gained per activity session (randomized within range)
_PROGRESS_PER_SESSION = (0.08, 0.2)


@dataclass
class Project:
    kind: str
    name: str
    progress: float = 0.0
    started_ts: float = field(default_factory=time.time)
    last_touched_ts: float = field(default_factory=time.time)
    sessions: int = 0
    done: bool = False


class ProjectManager:
    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state_path = state_path or _STATE_PATH
        self._active: dict[str, Project] = {}     # kind -> current project
        self._history: list[str] = []             # recently used names (avoid repeats)
        self._load()

    # ------------------------------------------------------------------

    def touch(self, kind: str) -> Project | None:
        """An activity session of this kind is happening — attach/advance its project.
        Returns the (possibly newly started, possibly just-completed) project."""
        if kind not in _POOLS:
            return None
        with self._lock:
            proj = self._active.get(kind)
            if proj is None or proj.done:
                proj = self._start_new(kind)
                if proj is None:
                    return None
            proj.sessions += 1
            proj.last_touched_ts = time.time()
            proj.progress = min(1.0, proj.progress + random.uniform(*_PROGRESS_PER_SESSION))
            completed = proj.progress >= 1.0 and not proj.done
            if completed:
                proj.done = True
            self._save()
        if completed:
            self._journal(f"finished {_KIND_VERBS[kind]} \"{proj.name}\" — "
                          f"{proj.sessions} sittings over {self._span_days(proj)} days")
            logger.info("project completed: %s %r", kind, proj.name)
        return proj

    def _start_new(self, kind: str) -> Project | None:
        pool = [n for n in _POOLS[kind] if n not in self._history[-12:]]
        if not pool:
            pool = _POOLS[kind]
        name = random.choice(pool)
        proj = Project(kind=kind, name=name)
        self._active[kind] = proj
        self._history.append(name)
        self._history = self._history[-24:]
        self._journal(f"started {_KIND_VERBS[kind]} \"{name}\"")
        logger.info("project started: %s %r", kind, name)
        return proj

    # ------------------------------------------------------------------

    def current(self, kind: str) -> Project | None:
        with self._lock:
            proj = self._active.get(kind)
            return None if proj is None or proj.done else proj

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {"kind": p.kind, "name": p.name, "progress": round(p.progress, 2),
                 "sessions": p.sessions}
                for p in self._active.values() if not p.done
            ]

    @staticmethod
    def _span_days(proj: Project) -> int:
        return max(1, round((proj.last_touched_ts - proj.started_ts) / 86400))

    def _journal(self, text: str) -> None:
        try:
            from .journal import journal
            journal().log_event("activity", text, meta={"project": True})
        except Exception:
            pass

    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._active = {k: Project(**v) for k, v in data.get("active", {}).items()}
            self._history = list(data.get("history", []))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("projects load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({
                "active": {k: asdict(p) for k, p in self._active.items()},
                "history": self._history,
            }), encoding="utf-8")
        except Exception as exc:
            logger.warning("projects save failed: %s", exc)


# ----------------------------------------------------------------------

_INSTANCE: ProjectManager | None = None
_INSTANCE_LOCK = threading.Lock()


def get_projects() -> ProjectManager:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = ProjectManager()
    return _INSTANCE
