"""One-shot script to remove chess contamination from user memory files.
Run this AFTER stopping the orchestrator. It is safe to run multiple times.
"""

import json
import pathlib
import sys

MEMORY_DIR = pathlib.Path(__file__).parent.parent / "data" / "memory"

CHESS_EPISODE_PHRASES = [
    "glances up, then back to the board",
    "glances over, then back to your chess board",
    "leans in slightly",
    "leans back, watching you",
    "You're quiet. Not like you.",
    "You're still quiet. Maybe I should just",
    "I'm here. Always.",
    "here if you need to talk",
    "[chess]",
]

CHESS_BELIEF_TOPICS = {"chess, tilts", "chess, glances", "chess, board"}


def is_chess_episode(episode: dict) -> bool:
    summary = episode.get("summary", "")
    user_part = summary.split("->")[0] if "->" in summary else summary
    if "[chess]" in user_part:
        return True
    ai_part = summary.split("->")[1] if "->" in summary else ""
    for phrase in CHESS_EPISODE_PHRASES:
        if phrase in ai_part:
            return True
    return False


def is_chess_belief(belief: dict) -> bool:
    topic = belief.get("topic", "")
    return topic in CHESS_BELIEF_TOPICS


def is_chess_turn(turn: dict) -> bool:
    content = turn.get("content", "")
    return (
        "[chess]" in content
        or "not the type to quit" in content
        or "still quiet" in content
        or "I'm here. Always." in content
        or "here if you need to talk" in content
        or "back to the board" in content
        or "back to your chess board" in content
        or "Let's get this over with" in content
        or "taking the initiative" in content.lower()
        or "exchange on e4" in content
        or "pawn break" in content
        or "knight to f3" in content
        or "pawn to d5" in content
    )


def clean_memory_file(path: pathlib.Path) -> tuple[bool, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, f"read error: {e}"

    changed = False

    # Clean episodic_memory
    episodes_before = len(data.get("episodic_memory", []))
    data["episodic_memory"] = [
        ep for ep in data.get("episodic_memory", []) if not is_chess_episode(ep)
    ]
    episodes_after = len(data["episodic_memory"])
    if episodes_before != episodes_after:
        changed = True
        removed = episodes_before - episodes_after
        msg_episodes = f"removed {removed} chess episodes"
    else:
        msg_episodes = "no chess episodes"

    # Clean beliefs
    beliefs_before = len(data.get("beliefs", []))
    data["beliefs"] = [
        b for b in data.get("beliefs", []) if not is_chess_belief(b)
    ]
    beliefs_after = len(data["beliefs"])
    if beliefs_before != beliefs_after:
        changed = True

    # Clean recent_turns
    turns_before = len(data.get("recent_turns", []))
    clean_turns = []
    for turn in data.get("recent_turns", []):
        if is_chess_turn(turn):
            changed = True
            continue
        clean_turns.append(turn)
    data["recent_turns"] = clean_turns
    turns_after = len(data["recent_turns"])

    # Clean intrinsic_interests — chess action words that dominate
    CHESS_INTEREST_WORDS = {
        "tilts", "glances", "then", "over", "head", "leans",
        "slightly", "smiles", "doing", "back", "board",
    }
    interests = data.get("intrinsic_interests", {})
    clean_interests = {k: v for k, v in interests.items() if k not in CHESS_INTEREST_WORDS}
    if len(clean_interests) != len(interests):
        changed = True
        data["intrinsic_interests"] = clean_interests

    # Remove curiosity_restless_topic if it's a chess word
    restless = data.get("curiosity_restless_topic")
    if restless in CHESS_INTEREST_WORDS:
        del data["curiosity_restless_topic"]
        changed = True

    # Update retention_state counts
    data.setdefault("retention_state", {})
    data["retention_state"]["episode_count"] = len(data["episodic_memory"])
    data["retention_state"]["belief_count"] = len(data["beliefs"])

    if not changed:
        return False, "already clean"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    summary = (
        f"{msg_episodes}, "
        f"{beliefs_before - beliefs_after} chess beliefs, "
        f"{turns_before - turns_after} chess turns"
    )
    return True, summary


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else None

    if targets:
        paths = [MEMORY_DIR / t if not t.endswith(".json") else MEMORY_DIR / t for t in targets]
        # Also accept bare IDs
        paths = [MEMORY_DIR / (t if t.endswith(".json") else t + ".json") for t in targets]
    else:
        paths = sorted(MEMORY_DIR.glob("*.json"))

    if not paths:
        print(f"No memory files found in {MEMORY_DIR}")
        return

    any_changed = False
    for p in paths:
        if not p.exists():
            print(f"  MISSING  {p.name}")
            continue
        changed, detail = clean_memory_file(p)
        status = "CLEANED" if changed else "ok     "
        print(f"  {status}  {p.name}  ({detail})")
        if changed:
            any_changed = True

    if any_changed:
        print("\nDone. Restart the orchestrator.")
    else:
        print("\nAll files were already clean.")


if __name__ == "__main__":
    main()
