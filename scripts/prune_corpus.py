"""Prune flagged videos from the limbs corpus (files + meta + quality entries).

Wrong-game slips get flagged NON-destructively into a review manifest
(`_wrong_game_review.jsonl` at the corpus root); this removes them only when the
owner confirms. Dry-run by default — nothing is deleted without --confirm.

  # see what would be removed:
  .venv\\Scripts\\python.exe scripts\\prune_corpus.py
  # actually remove (owner-confirmed destructive op):
  .venv\\Scripts\\python.exe scripts\\prune_corpus.py --confirm
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CORPUS_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_youtube")
MANIFEST = CORPUS_ROOT / "_wrong_game_review.jsonl"


def _rewrite_without(path: Path, drop_ids: set[str]) -> int:
    """Rewrite a jsonl file dropping lines whose 'id' is in drop_ids. Returns
    lines removed. No-op if the file is missing."""
    if not path.exists():
        return 0
    kept, removed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("id") in drop_ids:
                removed += 1
                continue
        except Exception:
            pass
        kept.append(line)
    if removed:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune flagged wrong-game videos from the corpus.")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--confirm", action="store_true", help="actually delete (else dry-run)")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"[prune] no manifest at {manifest} — nothing flagged")
        return 0
    entries = [json.loads(l) for l in manifest.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not entries:
        print("[prune] manifest empty")
        return 0

    print(f"[prune] {'DELETING' if args.confirm else 'DRY-RUN'} {len(entries)} flagged videos:")
    for e in entries:
        folder = CORPUS_ROOT / e["folder"]
        vid = e["id"]
        vfile = next((p for p in folder.glob(f"{vid}.*") if p.suffix != ".jsonl"), None)
        print(f"  {e['folder']}/{vid}  {e.get('title','')[:55]}")
        if not args.confirm:
            continue
        if vfile and vfile.exists():
            vfile.unlink()
        _rewrite_without(folder / "meta.jsonl", {vid})
        _rewrite_without(folder / "quality.jsonl", {vid})
    if args.confirm:
        manifest.unlink()
        print("[prune] done — manifest cleared.")
    else:
        print("[prune] dry-run only. Re-run with --confirm to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
