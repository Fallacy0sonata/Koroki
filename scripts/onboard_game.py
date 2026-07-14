"""Auto-onboarding v1 — game wiki → draft knowledge card (LIMBS W1.4, 2026-07-09).

"Whenever she boots a game, the system researches it first" (owner, game-limbs
arc). Pipeline: Fandom/MediaWiki search → page plaintext → schema-locked
extraction on the local brain (/v1/plan; the W1.2 mechanism reused — a
malformed extraction cannot be emitted) → PROGRAMMATIC quote-grounding (every
fact must share content words with its source chunk, else dropped — the
anti-hallucination gate lives in code, not in prompt hope) → merged DRAFT.

The draft is written as `<slug>.research.md` NEXT TO her live game card, never
merged automatically: 4B distillation is the weakest link in this chain
(docs/game_limbs_verdict_2026-07-09.md), so a human/Claude review stands
between research and what she believes.

Requires the brain running (solo is fine):
  .venv\\Scripts\\python.exe scripts\\onboard_game.py --game "sols rng"
  # --wiki overrides the <slug-with-hyphens>.fandom.com guess
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / "data" / "game" / "knowledge"
BRAIN_PLAN_URL = "http://127.0.0.1:9881/v1/plan"

SEARCH_TOPICS = ("beginner guide", "getting started", "mechanics", "controls",
                 "currency", "shop")
MAX_PAGES = 6
CHUNK_CHARS = 2800
MAX_CHUNKS = 10
GROUND_THRESHOLD = 0.55

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {"type": "array", "maxItems": 4,
                  "items": {"type": "string", "maxLength": 100}},
        "mechanics": {"type": "array", "maxItems": 6,
                      "items": {"type": "string", "maxLength": 120}},
        "dangers": {"type": "array", "maxItems": 4,
                    "items": {"type": "string", "maxLength": 100}},
        "controls": {"type": "array", "maxItems": 4,
                     "items": {"type": "string", "maxLength": 80}},
        "glossary": {"type": "array", "maxItems": 6,
                     "items": {"type": "string", "maxLength": 80}},
    },
    "required": ["goals", "mechanics", "dangers", "controls", "glossary"],
}

_EXTRACT_SYSTEM = (
    "You extract game knowledge from wiki text. Output ONLY facts stated in "
    "the text — never invent, never generalize from other games. Empty arrays "
    "are correct when the text says nothing about a category. goals = what "
    "players work toward; mechanics = how core systems work; dangers = what "
    "loses progress or costs premium/real currency; controls = keys/buttons; "
    "glossary = game-specific terms, each as 'term: meaning'."
)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def strip_html(raw: str) -> str:
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def grounded(fact: str, source_lower: str, threshold: float = GROUND_THRESHOLD) -> bool:
    """Every content word of the fact should appear in its source chunk."""
    words = [w for w in re.split(r"[^a-z0-9']+", fact.lower()) if len(w) >= 3]
    if not words:
        return False
    hits = sum(1 for w in words if w in source_lower)
    return hits / len(words) >= threshold


class FandomWiki:
    def __init__(self, subdomain: str):
        self.api = f"https://{subdomain}.fandom.com/api.php"
        # follow_redirects: Fandom 301s subdomain variants (sols-rng -> sol-rng)
        self._client = httpx.Client(timeout=20.0, follow_redirects=True,
                                    headers={"User-Agent": "koroki-onboard/1.0"})

    def exists(self) -> bool:
        try:
            r = self._client.get(self.api, params={"action": "query", "meta": "siteinfo",
                                                   "format": "json"})
            return r.status_code == 200 and "query" in r.json()
        except Exception:
            return False

    def search_titles(self, query: str, limit: int = 3) -> list[str]:
        try:
            r = self._client.get(self.api, params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": limit, "format": "json"})
            return [hit["title"] for hit in r.json()["query"]["search"]]
        except Exception:
            return []

    def page_text(self, title: str) -> str:
        try:
            r = self._client.get(self.api, params={
                "action": "parse", "page": title, "prop": "text",
                "disablelimitreport": 1, "format": "json"})
            raw = r.json()["parse"]["text"]["*"]
            return strip_html(raw)
        except Exception:
            return ""


def extract_chunk(chunk: str, game: str) -> dict | None:
    """One schema-locked extraction call against the local brain."""
    message = f"game: {game}\nwiki text:\n{chunk}\n\nextract the knowledge."
    try:
        r = httpx.post(BRAIN_PLAN_URL, json={
            "request_id": "onboard",
            "system": _EXTRACT_SYSTEM,
            "message": message[:4000],
            "json_schema": CARD_SCHEMA,
            "max_new_tokens": 400,
        }, timeout=90.0)
        if r.status_code != 200:
            print(f"  [extract] brain {r.status_code}: {r.text[:120]}")
            return None
        return r.json().get("plan")
    except httpx.HTTPError as exc:
        print(f"  [extract] failed: {exc}")
        return None


def merge_grounded(results: list[tuple[dict, str]]) -> tuple[dict, int, int]:
    """Merge per-chunk extractions, keeping only quote-grounded facts."""
    card: dict[str, list[str]] = {k: [] for k in CARD_SCHEMA["properties"]}
    kept = dropped = 0
    for extraction, source in results:
        src_lower = source.lower()
        for key in card:
            for fact in extraction.get(key) or []:
                fact = str(fact).strip()
                if not fact:
                    continue
                if not grounded(fact, src_lower):
                    dropped += 1
                    continue
                if any(grounded(fact, old.lower(), 0.8) for old in card[key]):
                    continue  # near-duplicate of something already kept
                card[key].append(fact)
                kept += 1
    caps = {"goals": 5, "mechanics": 8, "dangers": 5, "controls": 5, "glossary": 8}
    for key, cap in caps.items():
        card[key] = card[key][:cap]
    return card, kept, dropped


def main() -> int:
    ap = argparse.ArgumentParser(description="Research a game's wiki into a draft knowledge card.")
    ap.add_argument("--game", required=True)
    ap.add_argument("--wiki", default=None, help="fandom subdomain (default: game name hyphenated)")
    ap.add_argument("--platform", default="roblox")
    args = ap.parse_args()

    # Fandom subdomain conventions vary (sols-rng vs growagarden vs tpt2) —
    # try hyphenated, concatenated, and initials before giving up.
    slug = slugify(args.game)
    words = slug.split("_")
    candidates = [args.wiki] if args.wiki else [
        slug.replace("_", "-"),
        slug.replace("_", ""),
        "".join(w[0] if w.isalpha() else w for w in words) if len(words) > 2 else None,
    ]
    wiki = None
    sub = ""
    for cand in candidates:
        if not cand:
            continue
        probe = FandomWiki(cand)
        if probe.exists():
            wiki, sub = probe, cand
            break
    if wiki is None:
        print(f"[onboard] no wiki found (tried {[c for c in candidates if c]}) — pass --wiki")
        return 1
    print(f"[onboard] wiki: {sub}.fandom.com")

    titles: list[str] = []
    for topic in SEARCH_TOPICS:
        for t in wiki.search_titles(topic):
            if t not in titles:
                titles.append(t)
    titles = titles[:MAX_PAGES]
    if not titles:
        print("[onboard] wiki search returned nothing")
        return 1
    print(f"[onboard] pages: {titles}")

    chunks: list[str] = []
    for title in titles:
        text = wiki.page_text(title)
        if len(text) < 200:
            continue
        for i in range(0, len(text), CHUNK_CHARS):
            chunks.append(f"[from wiki page: {title}]\n{text[i:i + CHUNK_CHARS]}")
            if len(chunks) >= MAX_CHUNKS:
                break
        if len(chunks) >= MAX_CHUNKS:
            break
    print(f"[onboard] {len(chunks)} chunks to distill")

    results: list[tuple[dict, str]] = []
    for n, chunk in enumerate(chunks):
        t0 = time.perf_counter()
        extraction = extract_chunk(chunk, args.game)
        if extraction:
            results.append((extraction, chunk))
            facts = sum(len(extraction.get(k) or []) for k in CARD_SCHEMA["properties"])
            print(f"  chunk {n + 1}/{len(chunks)}: {facts} facts ({time.perf_counter() - t0:.1f}s)")
    if not results:
        print("[onboard] extraction produced nothing — is the brain up?")
        return 1

    card, kept, dropped = merge_grounded(results)
    print(f"[onboard] grounding gate: kept {kept}, dropped {dropped} ungrounded")

    slug = slugify(args.game)
    out_dir = KNOWLEDGE_DIR / args.platform
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}.research.md"
    lines = [f"# {args.game} — auto-research DRAFT ({time.strftime('%Y-%m-%d')})",
             "",
             f"Source: {sub}.fandom.com ({len(chunks)} chunks, {kept} grounded facts, "
             f"{dropped} dropped by the grounding gate).",
             "REVIEW BEFORE MERGING into the live card — 4B distillation draft.",
             ""]
    section_names = {"goals": "Goals", "mechanics": "Mechanics", "dangers": "Dangers",
                     "controls": "Controls", "glossary": "Glossary"}
    for key, heading in section_names.items():
        if card[key]:
            lines.append(f"## {heading}")
            lines += [f"- {fact}" for fact in card[key]]
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[onboard] draft written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
