"""
Chess engine: python-chess board state + Stockfish move generation.
LLM is NOT used for moves — only for streamer commentary (see commentary.py).

Stockfish path resolution order:
  1. games.chess.stockfish_path in settings.yaml
  2. Common Windows install locations
  3. PATH lookup ("stockfish" / "stockfish.exe")
  4. Fallback: first legal move (random-ish, still valid chess)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("orchestrator.games.chess")

try:
    import chess
    import chess.engine
    HAS_CHESS = True
except ImportError:
    logger.warning("python-chess not installed — chess mode unavailable (pip install chess)")
    HAS_CHESS = False


class ChessSession:
    def __init__(self, game_id: str, user_id: str, user_color_str: str = "white") -> None:
        self.game_id = game_id
        self.user_id = user_id
        self.user_color: int = chess.WHITE if user_color_str.lower() != "black" else chess.BLACK
        self.difficulty: int = 10
        self.move_history: list[str] = []
        self.status: str = "active"  # active | user_won | koroki_won | draw | resigned
        self.board: chess.Board = chess.Board()
        # Her recent commentary lines — game turns are never persisted to memory,
        # so without this she can't see her own last line and repeats it verbatim.
        self.recent_lines: list[str] = []

    @property
    def is_users_turn(self) -> bool:
        return bool(self.board.turn == self.user_color)


def _determine_outcome(board: chess.Board, user_color: int) -> str:
    if board.is_checkmate():
        # board.turn is the side that is NOW in checkmate (lost)
        if board.turn == user_color:
            return "koroki_won"
        return "user_won"
    if board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves():
        return "draw"
    return "active"


def describe_move(board_before: chess.Board, move: chess.Move) -> str:
    """Neutral human phrase for a move ("knight takes bishop on d5", "castles short").

    Used both in the Discord header (so the human always sees WHAT happened, not
    just SAN) and in her commentary context (so she reacts to the actual move
    instead of improvising from nothing — owner, 2026-07-04).
    """
    if board_before.is_castling(move):
        return "castles short" if chess.square_file(move.to_square) == 6 else "castles long"
    piece = board_before.piece_at(move.from_square)
    name = chess.piece_name(piece.piece_type) if piece else "piece"
    to_name = chess.square_name(move.to_square)
    if board_before.is_en_passant(move):
        base = f"pawn takes pawn en passant on {to_name}"
    else:
        captured = board_before.piece_at(move.to_square)
        if captured is not None:
            base = f"{name} takes {chess.piece_name(captured.piece_type)} on {to_name}"
        else:
            base = f"{name} to {to_name}"
    if move.promotion:
        base += f", promotes to {chess.piece_name(move.promotion)}"
    return base


_PIECE_VALUE = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9}  # pawn/knight/bishop/rook/queen


def biggest_real_threat(board_after: chess.Board, moved_to: int, mover_color: bool) -> str | None:
    """Most valuable enemy piece the just-moved piece genuinely threatens.

    "Genuinely" = the victim is undefended, or worth more than the attacker —
    attacking a defended pawn with a queen is not a threat, and teasing about it
    would be the same fabrication problem as the "checkmate in two" bluff.
    Only minor pieces and up are worth teasing about.
    """
    attacker = board_after.piece_at(moved_to)
    if attacker is None:
        return None
    attacker_value = _PIECE_VALUE.get(attacker.piece_type, 0)
    best: tuple[int, int] | None = None  # (value, square)
    for sq in board_after.attacks(moved_to):
        victim = board_after.piece_at(sq)
        if victim is None or victim.color == mover_color or victim.piece_type == chess.KING:
            continue
        value = _PIECE_VALUE.get(victim.piece_type, 0)
        if value < 3:
            continue
        defended = board_after.is_attacked_by(not mover_color, sq)
        if value > attacker_value or not defended:
            if best is None or value > best[0]:
                best = (value, sq)
    if best is None:
        return None
    victim = board_after.piece_at(best[1])
    return f"their {chess.piece_name(victim.piece_type)} on {chess.square_name(best[1])}"


def material_note(board: chess.Board, color: bool) -> str:
    """Material balance from `color`'s perspective, in chess-natural points."""
    diff = sum(
        value * (len(board.pieces(pt, color)) - len(board.pieces(pt, not color)))
        for pt, value in _PIECE_VALUE.items()
    )
    if diff == 0:
        return "material is even"
    side = "up" if diff > 0 else "down"
    points = abs(diff)
    return f"you are {side} {points} point{'s' if points > 1 else ''} of material"


def render_board(board: chess.Board, user_color: int) -> str:
    """Render board as labelled ASCII, oriented from the user's side."""
    ranks = range(7, -1, -1) if user_color == chess.WHITE else range(8)
    files = range(8) if user_color == chess.WHITE else range(7, -1, -1)
    lines: list[str] = []
    for rank in ranks:
        row = f"{rank + 1} "
        for file in files:
            piece = board.piece_at(chess.square(file, rank))
            row += (piece.symbol() if piece else ".") + " "
        lines.append(row)
    file_labels = "  " + " ".join("abcdefgh"[f] for f in files)
    lines.append(file_labels)
    return "\n".join(lines)


class ChessEngine:
    def __init__(self) -> None:
        self._sessions: dict[str, ChessSession] = {}
        self._stockfish_path: str | None = None

    def _resolve_stockfish(self) -> str | None:
        if self._stockfish_path:
            return self._stockfish_path
        import os
        koroki_root = Path(os.environ.get("KOROKI_ROOT", "."))
        try:
            from shared.utils.config import get_settings
            cfg_path = str(get_settings().get("games", {}).get("chess", {}).get("stockfish_path", "")).strip()
        except Exception:
            cfg_path = ""

        candidates = [cfg_path] if cfg_path else []
        candidates += [
            "stockfish",
            "stockfish.exe",
            "C:/stockfish/stockfish.exe",
            "C:/stockfish/stockfish-windows-x86-64-avx2.exe",
            "C:/Program Files/Stockfish/stockfish.exe",
        ]
        for c in candidates:
            if not c:
                continue
            p = Path(c)
            if not p.is_absolute():
                p = koroki_root / c  # resolve relative paths from repo root
            if p.exists():
                self._stockfish_path = str(p)
                logger.info("Stockfish found at: %s", self._stockfish_path)
                return self._stockfish_path
        logger.warning("Stockfish not found — chess moves will use first-legal-move fallback")
        return None

    async def _stockfish_move(self, board: chess.Board, depth: int) -> Optional[chess.Move]:
        path = self._resolve_stockfish()
        if not path:
            moves = list(board.legal_moves)
            return moves[0] if moves else None
        try:
            def _sync() -> chess.Move | None:
                with chess.engine.SimpleEngine.popen_uci(path) as engine:
                    result = engine.play(board, chess.engine.Limit(depth=depth))
                    return result.move
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            logger.warning("Stockfish error (%s) — falling back to first legal move", exc)
            moves = list(board.legal_moves)
            return moves[0] if moves else None

    def start_game(self, user_id: str, user_color: str = "white") -> ChessSession:
        session = ChessSession(
            game_id=f"chess_{user_id}",
            user_id=user_id,
            user_color_str=user_color,
        )
        self._sessions[user_id] = session
        return session

    def get_session(self, user_id: str) -> ChessSession | None:
        return self._sessions.get(user_id)

    def end_session(self, user_id: str) -> None:
        self._sessions.pop(user_id, None)

    async def make_koroki_move(self, session: ChessSession) -> dict:
        """Make Koroki's move (call only when it's Koroki's turn)."""
        move = await self._stockfish_move(session.board, session.difficulty)
        if not move:
            session.status = "draw"
            return {"koroki_move": None, "status": "draw", "game_over": True, "in_check": False}

        san = session.board.san(move)
        desc = describe_move(session.board, move)
        notable = bool(
            session.board.is_capture(move)
            or session.board.is_castling(move)
            or move.promotion is not None
        )
        koroki_color = not session.user_color
        session.board.push(move)
        session.move_history.append(f"Koroki:{san}")

        if session.board.is_game_over():
            session.status = _determine_outcome(session.board, session.user_color)

        return {
            "koroki_move": san,
            "koroki_move_desc": desc,
            "koroki_move_notable": notable,
            # Engine truths she can safely tease with — real threats + material only.
            "koroki_threat": biggest_real_threat(session.board, move.to_square, koroki_color),
            "material_note": material_note(session.board, koroki_color),
            "status": session.status,
            "game_over": session.board.is_game_over(),
            "in_check": session.board.is_check(),
        }

    async def apply_user_move(self, user_id: str, move_str: str) -> dict:
        """Validate + apply user move, then make Koroki's response. Returns full move result."""
        session = self._sessions.get(user_id)
        if not session:
            return {"error": "no_active_game"}
        if session.status != "active":
            return {"error": "game_over", "status": session.status}
        if not session.is_users_turn:
            return {"error": "not_your_turn"}

        move: chess.Move | None = None
        try:
            candidate = chess.Move.from_uci(move_str)
            if candidate in session.board.legal_moves:
                move = candidate
        except Exception:
            pass
        if move is None:
            try:
                move = session.board.parse_san(move_str)
            except Exception:
                return {"error": "illegal_move", "move": move_str}
        if move not in session.board.legal_moves:
            return {"error": "illegal_move", "move": move_str}

        user_san = session.board.san(move)
        user_desc = describe_move(session.board, move)
        session.board.push(move)
        session.move_history.append(f"User:{user_san}")

        if session.board.is_game_over():
            session.status = _determine_outcome(session.board, session.user_color)
            return {
                "user_move": user_san,
                "user_move_desc": user_desc,
                "koroki_move": None,
                "board_ascii": render_board(session.board, session.user_color),
                "board_fen": session.board.fen(),
                "move_number": len(session.move_history),
                "status": session.status,
                "game_over": True,
                "in_check": False,
            }

        koroki = await self.make_koroki_move(session)
        return {
            "user_move": user_san,
            "user_move_desc": user_desc,
            "koroki_move": koroki.get("koroki_move"),
            "koroki_move_desc": koroki.get("koroki_move_desc"),
            "koroki_move_notable": koroki.get("koroki_move_notable", False),
            "koroki_threat": koroki.get("koroki_threat"),
            "material_note": koroki.get("material_note"),
            "board_ascii": render_board(session.board, session.user_color),
            "board_fen": session.board.fen(),
            "move_number": len(session.move_history),
            "status": session.status,
            "game_over": session.board.is_game_over(),
            "in_check": koroki.get("in_check", False),
        }

    def resign(self, user_id: str) -> dict:
        session = self._sessions.get(user_id)
        if not session:
            return {"error": "no_active_game"}
        session.status = "koroki_won"
        board_ascii = render_board(session.board, session.user_color)
        return {"status": "resigned", "board_ascii": board_ascii}


chess_engine = ChessEngine()
