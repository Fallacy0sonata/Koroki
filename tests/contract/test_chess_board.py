"""Chess board rendering + move description + commentary gating (2026-07-04).

Owner complaints driving these: ASCII board showed nothing about the last move
("i BET you dont even know which move she just did"), and commentary was blind
vibes because the agent prompt profile filters chess_event= facts — the context
now rides the [system] message instead.
"""
import chess

from services.orchestrator.games.board_render import render_board_png, HAS_RENDER
from services.orchestrator.games.chess import biggest_real_threat, describe_move, material_note
from services.orchestrator.games.commentary import (
    build_chess_move_context,
    build_grounding_retry_context,
    should_comment_on_move,
    ungrounded_chess_claims,
)


# ── describe_move ──


def test_describe_quiet_and_pawn_moves() -> None:
    board = chess.Board()
    assert describe_move(board, board.parse_san("e4")) == "pawn to e4"
    assert describe_move(board, board.parse_san("Nf3")) == "knight to f3"


def test_describe_capture() -> None:
    board = chess.Board()
    for san in ("e4", "d5"):
        board.push_san(san)
    assert describe_move(board, board.parse_san("exd5")) == "pawn takes pawn on d5"


def test_describe_castling() -> None:
    board = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1")
    assert describe_move(board, board.parse_san("O-O")) == "castles short"
    assert describe_move(board, board.parse_san("O-O-O")) == "castles long"


def test_describe_promotion() -> None:
    board = chess.Board("8/P7/8/8/8/8/7k/K7 w - - 0 1")
    assert describe_move(board, board.parse_san("a8=Q")) == "pawn to a8, promotes to queen"


def test_describe_en_passant() -> None:
    board = chess.Board()
    for san in ("e4", "a6", "e5", "d5"):
        board.push_san(san)
    assert describe_move(board, board.parse_san("exd6")) == "pawn takes pawn en passant on d6"


# ── commentary gating ──


def test_notable_moves_always_comment() -> None:
    for _ in range(20):
        assert should_comment_on_move("active", False, 20, notable=True)


def test_endgame_and_check_always_comment() -> None:
    assert should_comment_on_move("koroki_won", False, 30)
    assert should_comment_on_move("active", True, 30)


def test_move_context_carries_description() -> None:
    ctx = build_chess_move_context(
        user_move="d5",
        koroki_move="exd5",
        move_number=4,
        in_check=False,
        status="active",
        koroki_move_desc="pawn takes pawn on d5",
    )
    assert "you_did=pawn takes pawn on d5" in ctx
    assert "opponent_played=d5" in ctx
    assert ctx.startswith("chess_event=")


# ── teasing facts: threats + material (must be engine-true, never flattering) ──


def test_threat_detects_hanging_piece() -> None:
    # White knight on e5 attacks an UNDEFENDED black rook on d7.
    board = chess.Board("7k/3r4/8/4N3/8/8/8/7K b - - 0 1")
    assert biggest_real_threat(board, chess.E5, chess.WHITE) == "their rook on d7"


def test_threat_ignores_defended_lesser_piece() -> None:
    # White queen attacks a black knight that is defended by a pawn — queen takes
    # knight loses material, so it's not a real threat and she must not tease it.
    board = chess.Board("7k/2p5/3n4/3Q4/8/8/8/7K b - - 0 1")
    assert biggest_real_threat(board, chess.D5, chess.WHITE) is None


def test_threat_reports_more_valuable_target_even_if_defended() -> None:
    # Knight attacks a DEFENDED queen — still a real threat (value 9 > 3).
    board = chess.Board("3r3k/8/3q4/8/4N3/8/8/7K b - - 0 1")
    assert biggest_real_threat(board, chess.E4, chess.WHITE) == "their queen on d6"


def test_material_note_balance() -> None:
    assert material_note(chess.Board(), chess.WHITE) == "material is even"
    # White missing a knight → Black up 3
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/R1BQKB1R w KQkq - 0 1")
    assert material_note(board, chess.BLACK) == "you are up 6 points of material"
    assert material_note(board, chess.WHITE) == "you are down 6 points of material"


def test_capture_and_threat_context_assembly() -> None:
    ctx = build_chess_move_context(
        user_move="d4",
        koroki_move="Nxe4",
        move_number=6,
        in_check=False,
        status="active",
        koroki_move_desc="knight takes pawn on e4",
        user_move_desc="pawn to d4",
        threat="their queen on d1",
        material="you are up 1 point of material",
    )
    assert "opponent_did=pawn to d4" in ctx
    assert "you_now_threaten=their queen on d1" in ctx
    assert "material=you are up 1 point of material" in ctx
    # capture takes seed priority over threat
    assert "took a piece" in ctx or "captured" in ctx or "yours now" in ctx


# ── grounding verifier (she announced "checkmate in two" on move 1, 2026-07-04) ──


def test_fabricated_mate_claim_is_flagged() -> None:
    assert ungrounded_chess_claims("checkmate in two", in_check=False, status="active") == ["checkmate"]
    assert "checkmate" in ungrounded_chess_claims("mate in 3, sorry", in_check=False, status="active")
    assert "checkmate" in ungrounded_chess_claims("that's mate", in_check=True, status="active")


def test_fabricated_check_claim_is_flagged() -> None:
    assert ungrounded_chess_claims("check.", in_check=False, status="active") == ["check"]
    assert ungrounded_chess_claims("you're in check now", in_check=False, status="active") == ["check"]
    # real check → allowed
    assert ungrounded_chess_claims("check.", in_check=True, status="active") == []


def test_check_as_verb_is_not_flagged() -> None:
    # Live false positive from the first night: "check" used as a verb.
    assert ungrounded_chess_claims(
        "they didn't check queen capture. i wanted to force material loss",
        in_check=False, status="active",
    ) == []
    assert ungrounded_chess_claims("check the corner squares sometime", in_check=False, status="active") == []


def test_real_mate_and_trash_talk_pass() -> None:
    assert ungrounded_chess_claims("checkmate. i win", in_check=True, status="koroki_won") == []
    assert ungrounded_chess_claims("smart. c2-c3 next probably", in_check=False, status="active") == []
    assert ungrounded_chess_claims(
        "the knight feels bold there. how did you like it?", in_check=False, status="active"
    ) == []
    # "checking" must not trip the \bcheck\b guard
    assert ungrounded_chess_claims("just checking your patience", in_check=False, status="active") == []


def test_resignation_is_not_a_mate() -> None:
    assert "checkmate" in ungrounded_chess_claims("checkmate, basically", in_check=False, status="resigned")


def test_retry_context_names_the_lie() -> None:
    retry = build_grounding_retry_context("chess_event=move_exchange | ...", ["checkmate"])
    assert "falsely claimed checkmate" in retry
    assert retry.startswith("chess_event=move_exchange")


# ── board rendering ──


def test_render_returns_png_with_last_move() -> None:
    assert HAS_RENDER
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nf6"):
        board.push_san(san)
    png = render_board_png(board, chess.WHITE)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000  # a real image, not a stub


def test_render_black_orientation_and_empty_stack() -> None:
    # Fresh board (no move to highlight) from Black's side must not crash.
    png = render_board_png(chess.Board(), chess.BLACK)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
