"""
Builds game-event context strings injected into core_facts via game_event_context.
The LLM reads these and generates in-character streamer commentary.

Seeds deliberately vary in tone so commentary doesn't feel repetitive across a match.
"""
from __future__ import annotations

import random
import re


_GAME_START_SEEDS = [
    "chess_event=game_start | React naturally to starting a chess match. Confident, mildly amused, or quietly focused — your call. One or two sentences, in your natural voice.",
    "chess_event=game_start | You agreed to play chess. Brief in-character reaction. You can be cocky, indifferent, or unexpectedly sharp about it.",
    "chess_event=game_start | Chess match is starting. Say something real about it — not helpful narration, just your actual reaction. Keep it short.",
]

# The Discord header already announces her move mechanically ("played Nf6 — knight
# to f6"), so her line is pure reaction — but it must be ABOUT the move. The 4B
# obeys concrete format demands and deflects open "react" asks (LEGACY 2026-07-04):
# every seed demands she reference the actual piece, square, or their last move.
_MOVE_SEEDS = [
    "chess_event=move_exchange | you just played what's in you_did, answering their opponent_did. one short line to your opponent about WHY that square or what you're setting up — name the piece or square, no vague filler.",
    "chess_event=move_exchange | your move is in you_did. one line: your read on their opponent_did — was it smart, slow, or asking for trouble? reference the actual piece or square.",
    "chess_event=move_exchange | you played you_did. tell your opponent what you think of the position now, in one concrete line — mention a piece or a square, dry or teasing.",
    "chess_event=move_exchange | you answered opponent_did with you_did. one line about the exchange — what they missed, or what you want next. concrete, no 'interesting move' filler.",
    "chess_event=move_exchange | your move: you_did. one short line — either needle them about opponent_did or hint at your plan. name something real on the board.",
]

# She just ate something — gloat, dry (owner 2026-07-04: teasing "but prob not that cheery").
_CAPTURE_SEEDS = [
    "chess_event=move_exchange | you just took a piece — you_did. one short dry line to your opponent about the piece you took. gloating but understated, like it was inevitable.",
    "chess_event=move_exchange | you captured: you_did. tease them about losing that piece in one line — quiet, a little mean, not cheery.",
    "chess_event=move_exchange | you_did — that piece is yours now. one line: let them feel it. dry satisfaction, name the piece.",
]

# Her move creates a real threat (engine-verified) — quiet menace about the target.
_THREAT_SEEDS = [
    "chess_event=move_exchange | you played you_did and now you're eyeing you_now_threaten. one line of quiet menace at your opponent about that exact piece — like you already know how this ends.",
    "chess_event=move_exchange | your you_did puts you_now_threaten in your sights. warn them about it in one dry teasing line — name the piece, understated, not cheery.",
    "chess_event=move_exchange | after you_did, you_now_threaten has a problem. one short line telling your opponent to look at it. calm, a little smug.",
]

_CHECK_SEEDS = [
    "chess_event=check | you played you_did and now they are in check. one line to them — satisfied, smug, or understated. reference the piece giving check.",
    "chess_event=check | your you_did puts them in check. react in one natural line — no rule explanations, just you.",
]

_CHECKMATE_KOROKI_WON = [
    "chess_event=checkmate_win | You won by checkmate. Brief, natural reaction. Could be smug, quietly satisfied, or nonchalant. No speech.",
    "chess_event=checkmate_win | Checkmate. You won. React in one or two sentences — don't explain the game, just be yourself.",
    "chess_event=checkmate_win | You just won the chess match. Brief reaction — satisfaction, cool indifference, or a small dig. Keep it short.",
]

_CHECKMATE_USER_WON = [
    "chess_event=checkmate_loss | Opponent checkmated you. React honestly — salty, impressed, or composed. Not whiny, not over-the-top. Brief.",
    "chess_event=checkmate_loss | You lost the chess match. One or two sentences — real reaction, no excuses needed.",
    "chess_event=checkmate_loss | You were checkmated. Respond in your natural voice — could be dismissive, could be genuine. Brief.",
]

_DRAW_SEEDS = [
    "chess_event=draw | Game ended in a draw. Brief reaction — amused, disappointed, or just matter-of-fact about it.",
    "chess_event=draw | Draw. React in a sentence — whatever feels natural.",
]

_RESIGN_SEEDS = [
    "chess_event=opponent_resigned | Your opponent resigned. Brief comment — gracious, cool, or a small note of satisfaction.",
    "chess_event=opponent_resigned | They resigned. React naturally in a sentence — understated is fine.",
]


def should_comment_on_move(
    status: str, in_check: bool, move_number: int, notable: bool = False
) -> bool:
    """
    Returns True if this move warrants commentary. Not every move does, but chess
    is addressed speech (she's playing WITH someone), so she talks more here than
    the anti-yapper stream gate allows (owner raised the rate 2026-07-04).
    """
    # Game-ending moves: always comment
    if status in ("koroki_won", "user_won", "checkmate", "stalemate", "draw"):
        return True
    # Check: always comment
    if in_check:
        return True
    # Captures, castling, promotions: something actually happened — always comment
    if notable:
        return True
    # Opening moves (1-4): she has opinions early
    if move_number <= 4:
        return random.random() < 0.75
    # Regular moves: a bit over half
    return random.random() < 0.55


def build_chess_game_start_context(user_color: str) -> str:
    seed = random.choice(_GAME_START_SEEDS)
    return f"{seed} | opponent_plays_as={user_color}"


def build_chess_move_context(
    user_move: str | None,
    koroki_move: str | None,
    move_number: int,
    in_check: bool,
    status: str,
    koroki_move_desc: str | None = None,
    user_move_desc: str | None = None,
    threat: str | None = None,
    material: str | None = None,
    avoid_lines: list[str] | None = None,
) -> str:
    # Seed choice follows what actually happened: game end > check > her capture >
    # a real threat > quiet move. Captures and threats are her teasing material —
    # engine-verified facts she can be menacing about without bluffing.
    _captured = bool(koroki_move_desc and "takes" in koroki_move_desc)
    if status == "koroki_won":
        seed = random.choice(_CHECKMATE_KOROKI_WON)
    elif status == "user_won":
        seed = random.choice(_CHECKMATE_USER_WON)
    elif status == "draw":
        seed = random.choice(_DRAW_SEEDS)
    elif in_check:
        seed = random.choice(_CHECK_SEEDS)
    elif _captured:
        seed = random.choice(_CAPTURE_SEEDS)
    elif threat:
        seed = random.choice(_THREAT_SEEDS)
    else:
        seed = random.choice(_MOVE_SEEDS)

    parts = [seed, f"move_number={move_number}"]
    if user_move:
        parts.append(f"opponent_played={user_move}")
    if user_move_desc:
        parts.append(f"opponent_did={user_move_desc}")
    if koroki_move:
        parts.append(f"you_played={koroki_move}")
    if koroki_move_desc:
        parts.append(f"you_did={koroki_move_desc}")
    if threat:
        parts.append(f"you_now_threaten={threat}")
    if material:
        parts.append(f"material={material}")
    if avoid_lines:
        _avoid = " / ".join(str(line)[:60] for line in avoid_lines[-3:])
        parts.append(f'you already said: "{_avoid}" — say something DIFFERENT this time')
    # Grounding rail: with an open "be concrete" ask the 4B reaches for dramatic
    # chess words that aren't true (said "check." on a quiet move, 2026-07-04).
    parts.append(
        "ONLY claim what this context states — never say check, mate, or that "
        "something was captured unless it is written here"
    )
    return " | ".join(parts)


def build_chess_resign_context() -> str:
    return random.choice(_RESIGN_SEEDS)


def ungrounded_chess_claims(text: str, *, in_check: bool, status: str) -> list[str]:
    """Chess facts she claimed that the board disproves.

    The prompt rail reduces fabrication but can't guarantee it — she announced
    "checkmate in two" on move 1 (2026-07-04). Check/mate/stalemate claims are
    engine-verifiable, so verify them: callers retry once with a correction, then
    drop the line (the mechanical move header keeps the human informed either way).
    This is factual grounding against engine truth, not an output-style filter —
    trash talk, predictions about squares, and attitude all pass untouched.
    """
    t = (text or "").lower()
    claims: list[str] = []
    if re.search(r"check\s?-?\s?mate|\bmate\s+in\b|\bmate\b", t):
        if status not in ("koroki_won", "user_won", "checkmate"):
            claims.append("checkmate")
    # Claim-shaped "check" only: "in check", terminal "check." / "check!", or the
    # whole line. Verb uses pass — "they didn't check queen capture" was wrongly
    # flagged on the first night (2026-07-04).
    if re.search(r"\bin\s+check\b|\bcheck\s*[.!,~]|\bcheck\b\s*$", t) and not in_check:
        claims.append("check")
    if re.search(r"\bstalemate\b", t) and status not in ("draw", "stalemate"):
        claims.append("stalemate")
    return claims


def build_grounding_retry_context(base_context: str, false_claims: list[str]) -> str:
    return (
        f"{base_context} | RETRY: your previous line falsely claimed "
        f"{', '.join(false_claims)} — that is NOT true on this board. one line about "
        "what actually happened instead; do not mention check or mate at all"
    )
