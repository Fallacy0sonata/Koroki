"""Chess board → PNG for Discord display.

ASCII boards read like debug output and show nothing about what just happened
(owner, 2026-07-04: "i BET you dont even know which move she just did"). This
renders a lichess-style board with the last move highlighted (from + to squares)
and the king tinted red when in check, oriented from the user's side.

Pillow + Segoe UI Symbol (ships with Windows 10) — no new dependencies. Rendering
must never break the game: any failure returns None and callers fall back to ASCII.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger("orchestrator.games.board_render")

try:
    import chess
    from PIL import Image, ImageDraw, ImageFont
    HAS_RENDER = True
except ImportError:
    HAS_RENDER = False

SQ = 72          # square edge in px
MARGIN = 26      # coordinate gutter
LIGHT = (240, 217, 181)
DARK = (181, 136, 99)
HILITE = (246, 246, 105, 130)   # last-move from/to overlay
CHECK = (235, 97, 80, 170)      # king square when in check
FRAME = (38, 36, 33)            # outer chrome
LABEL = (196, 189, 180)

# One filled glyph per piece type; color is painted, not encoded in the glyph —
# outlined "white" glyphs (♘) render too thin at Discord sizes.
_GLYPHS = {"p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚"}
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguisym.ttf",  # Segoe UI Symbol — full chess set
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_font_cache: dict[int, "ImageFont.FreeTypeFont"] = {}


def _font(size: int) -> "ImageFont.FreeTypeFont | None":
    if size not in _font_cache:
        for path in _FONT_CANDIDATES:
            try:
                _font_cache[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    return _font_cache.get(size)


def _draw_piece(draw: "ImageDraw.ImageDraw", cx: int, cy: int, symbol: str) -> None:
    font = _font(int(SQ * 0.82))
    if font is None:
        return
    glyph = _GLYPHS[symbol.lower()]
    white = symbol.isupper()
    fill = (250, 250, 250) if white else (43, 43, 43)
    outline = (46, 44, 40) if white else (232, 230, 226)
    # Poor man's outline: glyph stamped at 8 offsets in the outline color first.
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)):
        draw.text((cx + dx, cy + dy), glyph, font=font, fill=outline, anchor="mm")
    draw.text((cx, cy), glyph, font=font, fill=fill, anchor="mm")


def render_board_png(board: "chess.Board", user_color: int) -> bytes | None:
    """PNG of the position from the user's side; None if rendering unavailable."""
    if not HAS_RENDER:
        return None
    try:
        ranks = range(7, -1, -1) if user_color == chess.WHITE else range(8)
        files = range(8) if user_color == chess.WHITE else range(7, -1, -1)

        size = 8 * SQ + 2 * MARGIN
        img = Image.new("RGB", (size, size), FRAME)
        overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        odraw = ImageDraw.Draw(overlay)

        last = board.peek() if board.move_stack else None
        hilite_squares = {last.from_square, last.to_square} if last else set()
        check_square = board.king(board.turn) if board.is_check() else None

        label_font = _font(15)
        for row, rank in enumerate(ranks):
            for col, file in enumerate(files):
                x0 = MARGIN + col * SQ
                y0 = MARGIN + row * SQ
                sq = chess.square(file, rank)
                base = LIGHT if (file + rank) % 2 else DARK
                draw.rectangle([x0, y0, x0 + SQ, y0 + SQ], fill=base)
                if sq in hilite_squares:
                    odraw.rectangle([x0, y0, x0 + SQ, y0 + SQ], fill=HILITE)
                if sq == check_square:
                    odraw.rectangle([x0, y0, x0 + SQ, y0 + SQ], fill=CHECK)

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        for row, rank in enumerate(ranks):
            for col, file in enumerate(files):
                piece = board.piece_at(chess.square(file, rank))
                if piece:
                    cx = MARGIN + col * SQ + SQ // 2
                    cy = MARGIN + row * SQ + SQ // 2
                    _draw_piece(draw, cx, cy, piece.symbol())

        if label_font:
            for row, rank in enumerate(ranks):
                y = MARGIN + row * SQ + SQ // 2
                draw.text((MARGIN // 2, y), str(rank + 1), font=label_font, fill=LABEL, anchor="mm")
            for col, file in enumerate(files):
                x = MARGIN + col * SQ + SQ // 2
                draw.text((x, size - MARGIN // 2), "abcdefgh"[file], font=label_font,
                          fill=LABEL, anchor="mm")

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        logger.warning("board render failed — falling back to ASCII", exc_info=True)
        return None
