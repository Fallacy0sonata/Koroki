"""Gameplay footage harvester — Limbs Stage 2 material (2026-07-09).

Banks external gameplay videos for the VPT-style pipeline: once the Stage-1
IDM exists (trained on the owner's demo_recorder sessions), this footage gets
pseudo-labeled with actions and becomes behavior-cloning corpus
(docs/game_limbs_verdict_2026-07-09.md, addendum).

Uses the .venv_diffsinger yt-dlp (same install the singing pipeline uses —
one production path). Video-only at <=720p: the IDM trains on downscaled
frames anyway (VPT used 128px), audio is dead weight.

Facecam/overlay rejection is deliberately NOT done here — that is a visual
filter pass at IDM time. This stage prefers popular "no commentary" gameplay
via the search query and banks metadata for later pruning.

Usage:
  .venv\\Scripts\\python.exe scripts\\harvest_gameplay.py --game "sols rng" --max 8
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YTDLP = ROOT / ".venv_diffsinger" / "Scripts" / "yt-dlp.exe"
STORAGE_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_youtube")

# search-result title rejects: not real gameplay footage. Montage/edited videos
# are worse than bad play — jump cuts teach the model teleportation (batch 1
# grabbed "In A Nutshell" + "EVERY UPDATE" compilations before these were added).
# Video-level heuristics stay best-effort; the REAL gate is segment-level
# filtering at IDM time (motion stats, cut detection, UI match).
TITLE_BLACKLIST = ("#shorts", "trailer", "reaction", "tier list", "tierlist",
                   "how to get", "codes", "vs ", " memes", "edit)",
                   "nutshell", "every update", "compilation", "montage",
                   "funny moments", "luckiest", "best moments", "recap",
                   # 2026-07-09 footage-filter lesson: 9/10 sols_rng videos were
                   # MONTAGE-REJECTs — the POPULAR Roblox format is the edited
                   # progression movie. First-person narrative titles = edited.
                   "noob to", "full movie", " hours", "i spent", "i went",
                   "i played", "omg", "insane", "crazy")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def title_ok(title: str) -> bool:
    low = title.lower()
    return not any(b in low for b in TITLE_BLACKLIST)


def game_tokens(game: str) -> list[str]:
    """Significant words of the game name — the relevance anchor."""
    return [t for t in slugify(game).split("_") if len(t) >= 3]


# Generic game words that a WRONG game can share. A title matching ONLY these is
# not proof it's the right game — "RollerCoaster Tycoon 2" slipped the old
# any-token gate on "tycoon" alone (2026-07-11). A distinctive token must match.
GENERIC_GAME_WORDS = {
    "tycoon", "simulator", "sim", "game", "games", "roblox", "obby", "rpg",
    "online", "world", "adventure", "story", "clicker", "legends", "world",
}


def relevance_ok(title: str, tokens: list[str]) -> bool:
    """A title is on-game only if it shares a DISTINCTIVE (non-generic) game
    token — not just a generic word like 'tycoon' every tycoon shares."""
    if not tokens:
        return True
    low = title.lower()
    matched = [t for t in tokens if t in low]
    if not matched:
        return False
    distinctive = [t for t in tokens if t not in GENERIC_GAME_WORDS]
    # if the game name has any distinctive word, one of THOSE must appear;
    # a game whose whole name is generic falls back to any match.
    if distinctive and not any(t in low for t in distinctive):
        return False
    return True


def pick_candidates(entries: list[dict], *, min_s: float, max_s: float, max_n: int,
                    already: set[str], tokens: list[str] | None = None) -> list[dict]:
    """Filter search entries to plausible gameplay OF THIS GAME, most-viewed first.

    The relevance gate exists because a drifted query ("sols rng longplay full
    session", 2026-07-09) returned Famicom retro longplays and 5 of them got
    banked into the sols_rng corpus — wrong-game footage is worse than bad
    gameplay. A title that names the game is the cheapest honest anchor.
    """
    good = []
    for e in entries:
        if not e or e.get("id") in already:
            continue
        dur = e.get("duration") or 0
        if not (min_s <= dur <= max_s):
            continue
        title = e.get("title") or ""
        if not title_ok(title):
            continue
        if tokens and not relevance_ok(title, tokens):
            continue  # search drift or wrong game (generic-word-only match)
        good.append(e)
    # Duration ranking, NOT view count (2026-07-09 lesson): popularity selects
    # for edited progression movies; raw longplays are long and unglamorous.
    good.sort(key=lambda e: e.get("duration") or 0, reverse=True)
    return good[:max_n]


def search(query: str, pool: int) -> list[dict]:
    out = subprocess.run(
        [str(YTDLP), f"ytsearch{pool}:{query}", "--flat-playlist", "-J", "--no-warnings"],
        capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        print(f"[harvest] search failed: {out.stderr.strip()[:400]}")
        return []
    data = json.loads(out.stdout)
    return data.get("entries") or []


def download(video_id: str, dest_dir: Path) -> bool:
    url = f"https://www.youtube.com/watch?v={video_id}"
    out = subprocess.run(
        [str(YTDLP), url,
         "-f", "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]",
         "-o", str(dest_dir / "%(id)s.%(ext)s"),
         "--no-warnings", "--no-playlist"],
        capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        print(f"[harvest] download {video_id} failed: {out.stderr.strip()[:300]}")
        return False
    return True


def verdict_at_ingest(video_id: str, dest_dir: Path) -> str:
    """Quality-gate the video NOW, while it's hot in the Drive cache.

    Cold re-reads from Drive cost 10-18 min per video (live-measured
    2026-07-10); analyzing at download time costs seconds. Verdict appends to
    the same quality.jsonl the training assembler reads. Fail-soft: a verdict
    failure never blocks banking."""
    try:
        sys.path.insert(0, str(ROOT / "experiments" / "limbs"))
        import time as _time

        from footage_filter import analyze_video

        vid_path = next(dest_dir.glob(f"{video_id}.*"), None)
        if vid_path is None or vid_path.suffix == ".jsonl":
            return "?"
        t0 = _time.perf_counter()
        rep = analyze_video(vid_path)
        if rep is None:
            return "?"
        rep = {"id": video_id, **rep, "analyzed_s": round(_time.perf_counter() - t0, 1)}
        with open(dest_dir / "quality.jsonl", "a", encoding="utf-8") as qf:
            qf.write(json.dumps(rep, separators=(",", ":")) + "\n")
        return ("MONTAGE-REJECT" if rep["montage"]
                else f"usable {rep['usable_ratio']:.0%}")
    except Exception as exc:
        print(f"  [verdict] failed ({exc}) — banked without verdict")
        return "?"


def load_meta_ids(meta_path: Path) -> set[str]:
    if not meta_path.exists():
        return set()
    ids = set()
    for line in meta_path.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(json.loads(line)["id"])
        except Exception:
            continue
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description="Bank external gameplay footage to Koroki Storage.")
    ap.add_argument("--game", required=True, help='game name, e.g. "sols rng"')
    ap.add_argument("--query", default=None,
                    help="override search query (default: 'roblox <game> gameplay no commentary')")
    ap.add_argument("--max", type=int, default=8, help="videos to download this run")
    ap.add_argument("--min-minutes", type=float, default=5.0)
    ap.add_argument("--max-minutes", type=float, default=45.0)
    ap.add_argument("--no-roblox", action="store_true",
                    help="don't force 'roblox' into the default query (for non-Roblox games)")
    args = ap.parse_args()

    if not YTDLP.exists():
        print(f"[harvest] yt-dlp not found at {YTDLP}")
        return 1
    slug = slugify(args.game)
    dest = STORAGE_ROOT / slug
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[harvest] cannot reach Koroki Storage ({exc}) — is the Drive client running?")
        return 1

    meta_path = dest / "meta.jsonl"
    already = load_meta_ids(meta_path)
    # Bias search toward Roblox by default: games with common-word names ("grow a
    # garden", "doors") otherwise pull non-Roblox lookalikes (Animal Crossing
    # gardening, RollerCoaster Tycoon) that the title-token gate can't tell apart
    # (2026-07-11). --query still fully overrides; --no-roblox for non-Roblox games.
    prefix = "" if args.no_roblox else "roblox "
    query = args.query or f"{prefix}{args.game} gameplay no commentary"
    print(f"[harvest] searching: {query!r} (have {len(already)} banked)")

    entries = search(query, pool=max(args.max * 4, 20))
    picks = pick_candidates(entries, min_s=args.min_minutes * 60, max_s=args.max_minutes * 60,
                            max_n=args.max, already=already, tokens=game_tokens(args.game))
    if not picks:
        print("[harvest] nothing new matched the filters")
        return 0

    got = 0
    with open(meta_path, "a", encoding="utf-8") as mf:
        for e in picks:
            vid = e["id"]
            title = (e.get("title") or "")[:90]
            print(f"[harvest] {vid}  {e.get('duration', 0) / 60:.0f}min  {title}")
            if not download(vid, dest):
                continue
            print(f"  [verdict] {verdict_at_ingest(vid, dest)}")
            mf.write(json.dumps({
                "id": vid, "title": e.get("title"), "duration": e.get("duration"),
                "channel": e.get("channel") or e.get("uploader"),
                "view_count": e.get("view_count"), "query": query,
                "banked_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            mf.flush()
            got += 1
            time.sleep(3)  # politeness gap between downloads

    print(f"[harvest] banked {got}/{len(picks)} new videos -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
