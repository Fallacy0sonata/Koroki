from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/Shinn/Desktop/Koroki")
BACKUP = ROOT / "Koroki - Copy"
OUT_DIR = ROOT / "data" / "training" / "lora"


def _tier_from_meta(meta: dict | None) -> str:
    meta = meta or {}
    mode = str(meta.get("mode", "")).lower()
    rel = str(meta.get("relationship", "")).lower()
    text = f"{mode} {rel}"

    if any(k in text for k in ["owner", "girlfriend", "boyfriend", "wife", "husband", "master"]):
        return "owner"
    if any(k in text for k in ["tsundere", "friend", "close", "companion", "confidant", "beloved", "darling", "dearest", "soulmate"]):
        return "tsundere"
    return "peasant"


def _clean(s: str | None) -> str:
    return " ".join((s or "").split()).strip()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_from_supervised(path: Path):
    data = _read_json(path)
    approved = data.get("approved", []) if isinstance(data, dict) else []
    rejected = data.get("rejected", []) if isinstance(data, dict) else []

    good: list[dict] = []
    bad_by_user: defaultdict[str, list[str]] = defaultdict(list)

    for row in approved:
        td = row.get("trainingData", {}) if isinstance(row, dict) else {}
        user = _clean(td.get("user") or row.get("userInput"))
        assistant = _clean(td.get("assistant") or row.get("aiResponse"))
        if not user or not assistant:
            continue
        meta = row.get("metadata", {}) if isinstance(row, dict) else {}
        tier = _tier_from_meta(meta)
        good.append({"tier": tier, "user": user, "assistant": assistant, "source": "supervised_approved"})

    for row in rejected:
        if not isinstance(row, dict):
            continue
        user = _clean(row.get("userInput"))
        bad = _clean(row.get("aiResponse"))
        if user and bad:
            bad_by_user[user].append(bad)

    return good, bad_by_user


def _extract_from_slm(path: Path):
    data = _read_json(path)
    out: list[dict] = []
    if not isinstance(data, list):
        return out

    for row in data:
        if not isinstance(row, dict):
            continue
        msgs = row.get("messages", [])
        if len(msgs) < 2:
            continue
        user = _clean(msgs[0].get("content") if isinstance(msgs[0], dict) else "")
        assistant = _clean(msgs[1].get("content") if isinstance(msgs[1], dict) else "")
        if not user or not assistant:
            continue
        tier = _tier_from_meta(row.get("metadata", {}))
        out.append({"tier": tier, "user": user, "assistant": assistant, "source": "slm_simulated"})
    return out


def _extract_from_simulated(path: Path):
    data = _read_json(path)
    approved = data.get("approved", []) if isinstance(data, dict) else []
    rejected = data.get("rejected", []) if isinstance(data, dict) else []

    good: list[dict] = []
    bad_by_user: defaultdict[str, list[str]] = defaultdict(list)

    for row in approved:
        if not isinstance(row, dict):
            continue
        user = _clean(row.get("input"))
        assistant = _clean(row.get("response"))
        if not user or not assistant:
            continue
        meta = {
            "mode": row.get("mode", ""),
            "relationship": row.get("relationship", ""),
        }
        tier = _tier_from_meta(meta)
        good.append({"tier": tier, "user": user, "assistant": assistant, "source": "simulated_approved"})

    for row in rejected:
        if not isinstance(row, dict):
            continue
        user = _clean(row.get("input"))
        bad = _clean(row.get("response"))
        if user and bad:
            bad_by_user[user].append(bad)

    return good, bad_by_user


def _extract_from_koroki_training_data(path: Path):
    data = _read_json(path)
    rows = data.get("bestResponses", []) if isinstance(data, dict) else []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("rating", 0)) < 4:
            continue
        user = _clean(row.get("user"))
        assistant = _clean(row.get("koroki") or row.get("text"))
        if not user or not assistant:
            continue
        # Legacy bestResponses are mostly intimate Koroki style; treat as owner-tier supervision.
        out.append({"tier": "owner", "user": user, "assistant": assistant, "source": "best_responses"})
    return out


def _manual_contrastive_seed() -> list[dict]:
    return [
        {
            "tier": "peasant",
            "user": "How can I help you today?",
            "bad": "I'd be happy to assist you today.",
            "good": "You're standing in my light. Move, or make yourself useful and get me some tea.",
            "source": "manual_seed",
        },
        {
            "tier": "tsundere",
            "user": "Can I help you with anything?",
            "bad": "Thank you for your assistance.",
            "good": "Hmph, I didn't ask for rescue. Stay nearby if you want, baka.",
            "source": "manual_seed",
        },
        {
            "tier": "owner",
            "user": "Need help?",
            "bad": "Yes, I would appreciate your assistance.",
            "good": "For you? Maybe. Come closer and I'll tell you what I need.",
            "source": "manual_seed",
        },
    ]


def build() -> dict:
    supervised_path = BACKUP / "supervised_training.json"
    simulated_path = BACKUP / "simulated_training.json"
    slm_path = BACKUP / "slm_training_simulated.json"
    best_resp_path = BACKUP / "koroki_training_data.json"

    good_supervised, bad_by_user = _extract_from_supervised(supervised_path)
    good_simulated, bad_simulated = _extract_from_simulated(simulated_path)
    good_slm = _extract_from_slm(slm_path)
    good_best = _extract_from_koroki_training_data(best_resp_path)

    all_good = good_supervised + good_simulated + good_slm + good_best

    for user, bads in bad_simulated.items():
        bad_by_user[user].extend(bads)

    # De-duplicate by (tier,user,assistant)
    dedup = {}
    for row in all_good:
        dedup[(row["tier"], row["user"], row["assistant"])] = row
    all_good = list(dedup.values())

    # Build contrastive pairs where a rejected answer exists for same user prompt.
    contrastive: list[dict] = []
    paired_users: set[tuple[str, str]] = set()
    for row in all_good:
        bads = bad_by_user.get(row["user"], [])
        for bad in bads[:2]:
            contrastive.append(
                {
                    "tier": row["tier"],
                    "user": row["user"],
                    "bad": bad,
                    "good": row["assistant"],
                    "source": f"{row['source']}_paired_rejected",
                }
            )
            paired_users.add((row["tier"], row["user"]))

    # Ensure each good sample has at least one contrastive counterpart.
    fallback_bad = {
        "owner": "I'd be happy to assist you with that request.",
        "tsundere": "I can certainly help you with your request today.",
        "peasant": "How may I assist you today?",
    }
    for row in all_good:
        key = (row["tier"], row["user"])
        if key in paired_users:
            continue
        contrastive.append(
            {
                "tier": row["tier"],
                "user": row["user"],
                "bad": fallback_bad.get(row["tier"], "How may I assist you today?"),
                "good": row["assistant"],
                "source": f"{row['source']}_synthetic_bad",
            }
        )

    contrastive.extend(_manual_contrastive_seed())

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Tier SFT files
    tier_counts = Counter()
    for tier in ("owner", "tsundere", "peasant"):
        tier_rows = [r for r in all_good if r["tier"] == tier]
        tier_counts[tier] = len(tier_rows)
        out_path = OUT_DIR / f"{tier}_sft.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in tier_rows:
                rec = {
                    "messages": [
                        {"role": "user", "content": r["user"]},
                        {"role": "assistant", "content": r["assistant"]},
                    ],
                    "metadata": {"tier": tier, "source": r["source"]},
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Combined contrastive file
    contrastive_path = OUT_DIR / "contrastive_bad_good.jsonl"
    with contrastive_path.open("w", encoding="utf-8") as f:
        for r in contrastive:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {
        "tier_sft_counts": dict(tier_counts),
        "total_sft_pairs": sum(tier_counts.values()),
        "contrastive_pairs": len(contrastive),
        "files": {
            "owner": str((OUT_DIR / "owner_sft.jsonl").as_posix()),
            "tsundere": str((OUT_DIR / "tsundere_sft.jsonl").as_posix()),
            "peasant": str((OUT_DIR / "peasant_sft.jsonl").as_posix()),
            "contrastive": str(contrastive_path.as_posix()),
        },
    }
    (OUT_DIR / "dataset_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    stats = build()
    print(json.dumps(stats, indent=2))
