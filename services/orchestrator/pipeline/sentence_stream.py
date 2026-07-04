"""Sentence-streaming Brain→TTS — synthesize sentence N while the Brain writes N+1.

The classic path waits for the full reply, shapes it, then makes ONE TTS call: total
latency ≈ brain_time + tts(full reply). This pipeline overlaps them: each sentence is
dispatched to TTS the moment the Brain finishes streaming it, so the final wait is
≈ brain_time + tts(last sentence). Perceived latency drops 30-50% on multi-sentence
replies.

Honesty guarantees (why this can ship flag-gated without breaking anything):
  - The audio is only used when the streamed text SURVIVED post-processing unchanged
    (think-leak strip / repetition trim / crutch retry all rewrite text). Any mismatch →
    the caller falls back to the classic one-call TTS. Optimism costs nothing on miss.
  - Any synthesis error, wav-format mismatch between segments, or cancellation → None →
    classic fallback.

Inter-sentence spacing (owner directive 2026-07-02): pauses are EARNED BY THE TEXT,
never randomized — "some sentences are meant to be spaced and some are meant to be in
a row." Deterministic pause model:
  - trailing "…"/"..." (she trails off)            → long pause
  - paragraph break in the source text             → longest pause
  - next sentence starts with a run-on connective
    (and/but/so/… , lowercase continuation)        → near-zero gap
  - "!" / "?"                                      → medium pause
  - plain "."                                      → short pause
TTS already breathes naturally inside a sentence; these gaps only govern the seams.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger("orchestrator.pipeline.sentence_stream")

# ── sentence assembly ────────────────────────────────────────────────────────

_TERMINALS = ".!?…。！？"
# don't split right after these (decimals handled separately)
_ABBREVIATIONS = ("mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.")
_RUN_ON_STARTERS = (
    "and", "but", "so", "or", "also", "then", "plus", "because", "which", "though",
    "でも", "それで", "だから", "けど", "ね、",
)

_PAUSE_TRAIL_OFF_MS = 420
_PAUSE_PARAGRAPH_MS = 500
_PAUSE_EXCLAIM_MS = 200
_PAUSE_DEFAULT_MS = 150
_PAUSE_RUN_ON_MS = 40


@dataclass
class Sentence:
    text: str                 # raw sentence text as streamed
    paragraph_end: bool = False  # a blank line followed this sentence in the source


class SentenceAssembler:
    """Feed streamed text chunks, get completed sentences out.

    Splits at terminal punctuation (EN + JP), keeping closing quotes/brackets with the
    sentence. Guards decimals ("v2.5"), common abbreviations, and ellipses runs.
    """

    def __init__(self, min_chars: int = 4):
        self._buf = ""
        self._min_chars = min_chars

    def feed(self, chunk: str) -> list[Sentence]:
        self._buf += chunk
        out: list[Sentence] = []
        while True:
            cut = self._find_cut(self._buf)
            if cut is None:
                break
            raw, rest = self._buf[:cut], self._buf[cut:]
            para = bool(re.match(r"\s*\n\s*\n", rest))
            text = raw.strip()
            self._buf = rest.lstrip()
            if text:
                out.append(Sentence(text=text, paragraph_end=para))
        return out

    def flush(self) -> list[Sentence]:
        text = self._buf.strip()
        self._buf = ""
        return [Sentence(text=text)] if text else []

    def _find_cut(self, buf: str) -> int | None:
        for i, ch in enumerate(buf):
            if ch not in _TERMINALS:
                continue
            # need at least one following char to know the boundary is real
            # (more terminal chars may still be streaming in: "...", "?!")
            j = i + 1
            if j >= len(buf):
                return None
            if buf[j] in _TERMINALS:
                continue  # inside a run like "..." or "?!" — cut at its end
            # decimals: "2.5"
            if ch == "." and i > 0 and buf[i - 1].isdigit() and buf[j].isdigit():
                continue
            # abbreviations: "dr. koro"
            if ch == ".":
                tail = buf[max(0, i - 6):i + 1].lower()
                if any(tail.endswith(a) for a in _ABBREVIATIONS):
                    continue
            # mid-sentence ellipsis: "Well... maybe" — an ellipsis run followed by a
            # lowercase continuation is a pause INSIDE the sentence, not a boundary
            # (also keeps true run-ons in one TTS segment: zero artificial gap)
            is_ellipsis = ch == "…" or (ch == "." and i > 0 and buf[i - 1] == ".")
            if is_ellipsis:
                k = j
                while k < len(buf) and buf[k] in " \t":
                    k += 1
                if k >= len(buf):
                    return None  # can't judge yet — wait for more stream
                if buf[k].isalpha() and buf[k].islower():
                    continue
            # keep closing quotes/brackets with the sentence
            while j < len(buf) and buf[j] in "\"'」』)]”’":
                j += 1
            if j >= len(buf):
                return None
            candidate = buf[:j].strip()
            if len(candidate) < self._min_chars:
                continue
            return j
        return None


# ── pause model ──────────────────────────────────────────────────────────────

def pause_after_ms(cur: Sentence, nxt: Sentence | None) -> int:
    """Deterministic inter-sentence gap — derived from the text, never random."""
    if nxt is None:
        return 0
    if cur.paragraph_end:
        return _PAUSE_PARAGRAPH_MS
    body = cur.text.rstrip("\"'」』)]”’")
    if body.endswith(("…", "...")):
        return _PAUSE_TRAIL_OFF_MS
    nxt_head = nxt.text.lstrip("\"'「『([“‘")
    first_word = re.split(r"[\s、,]", nxt_head, maxsplit=1)[0].lower()
    if first_word in _RUN_ON_STARTERS or (nxt_head[:1].islower() and nxt_head[:1].isalpha()):
        return _PAUSE_RUN_ON_MS
    if body.endswith(("!", "?", "！", "？")):
        return _PAUSE_EXCLAIM_MS
    return _PAUSE_DEFAULT_MS


# ── wav concatenation ────────────────────────────────────────────────────────

def concat_wav(segments: list[bytes], pauses_ms: list[int]) -> bytes | None:
    """Concatenate wav segments with silence gaps. All segments must share params.
    Returns None on any format mismatch (caller falls back to one-call TTS)."""
    if not segments:
        return None
    try:
        frames: list[bytes] = []
        params = None
        for k, seg in enumerate(segments):
            with wave.open(io.BytesIO(seg), "rb") as w:
                p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if params is None:
                    params = p
                elif p != params:
                    logger.warning("wav param mismatch %s vs %s — abort concat", p, params)
                    return None
                frames.append(w.readframes(w.getnframes()))
            if k < len(pauses_ms) and pauses_ms[k] > 0:
                nch, sw, sr = params
                n_silence = int(sr * pauses_ms[k] / 1000)
                frames.append(b"\x00" * (n_silence * nch * sw))
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(params[0])
            w.setsampwidth(params[1])
            w.setframerate(params[2])
            w.writeframes(b"".join(frames))
        return out.getvalue()
    except Exception as exc:
        logger.warning("wav concat failed: %s", exc)
        return None


# ── streaming orchestrator ───────────────────────────────────────────────────

@dataclass
class _Segment:
    sentence: Sentence
    shaped: str
    task: asyncio.Task | None = None


@dataclass
class StreamStats:
    sentences: int = 0
    synthesized: int = 0
    reason: str = ""
    chars_budgeted: int = 0


class StreamingSpeech:
    """Drives per-sentence TTS during Brain streaming.

    synth(shaped_text) -> wav bytes | None   (async; one call per sentence)
    shaper(raw_text, is_last) -> shaped text ("" to skip the sentence)
    max_chars: synthesis budget over SHAPED text (mirrors the classic clip)
    """

    def __init__(
        self,
        synth: Callable[[str], Awaitable[bytes | None]],
        shaper: Callable[[str, bool], str],
        max_chars: int = 0,
        min_sentence_chars: int = 4,
    ):
        self._synth = synth
        self._shaper = shaper
        self._max_chars = max_chars
        self._assembler = SentenceAssembler(min_chars=min_sentence_chars)
        self._segments: list[_Segment] = []
        self._raw_parts: list[str] = []
        self._budget_used = 0
        self._budget_hit = False
        self.stats = StreamStats()

    # -- streaming side --

    def feed(self, chunk: str) -> None:
        self._raw_parts.append(chunk)
        for sent in self._assembler.feed(chunk):
            self._submit(sent, is_last=False)

    def _submit(self, sent: Sentence, is_last: bool) -> None:
        self.stats.sentences += 1
        if self._budget_hit:
            return
        try:
            shaped = self._shaper(sent.text, is_last)
        except Exception as exc:
            logger.warning("shaper failed on sentence: %s", exc)
            shaped = ""
        if not shaped:
            return
        if self._max_chars > 0 and self._budget_used + len(shaped) > self._max_chars:
            self._budget_hit = True
            logger.info("sentence-stream: char budget reached (%d) — later sentences skipped",
                        self._max_chars)
            return
        self._budget_used += len(shaped)
        seg = _Segment(sentence=sent, shaped=shaped)
        seg.task = asyncio.create_task(self._synth(shaped))
        self._segments.append(seg)

    def cancel(self) -> None:
        for seg in self._segments:
            if seg.task is not None and not seg.task.done():
                seg.task.cancel()

    # -- finalize side --

    def streamed_raw_text(self) -> str:
        return "".join(self._raw_parts)

    async def finalize(self, final_text: str) -> tuple[bytes | None, StreamStats]:
        """Await all segments and assemble the reply audio.

        `final_text` is the post-repair response text. If it no longer matches what was
        streamed (crutch retry, repetition trim, think-leak strip), the streamed audio
        is invalid → (None, stats) and the caller uses the classic one-call path.
        """
        for sent in self._assembler.flush():
            self._submit(sent, is_last=True)
        self.stats.chars_budgeted = self._budget_used

        def _norm(t: str) -> str:
            return re.sub(r"\s+", " ", t).strip()

        if _norm(final_text) != _norm(self.streamed_raw_text()):
            self.stats.reason = "text_changed_after_stream"
            self.cancel()
            return None, self.stats

        if not self._segments:
            self.stats.reason = "no_sentences"
            return None, self.stats

        wavs: list[bytes] = []
        for seg in self._segments:
            try:
                audio = await seg.task if seg.task is not None else None
            except asyncio.CancelledError:
                audio = None
            except Exception as exc:
                logger.warning("segment synthesis failed: %s", exc)
                audio = None
            if audio is None:
                self.stats.reason = "segment_failed"
                self.cancel()
                return None, self.stats
            wavs.append(audio)
            self.stats.synthesized += 1

        pauses = [
            pause_after_ms(self._segments[k].sentence,
                           self._segments[k + 1].sentence if k + 1 < len(self._segments) else None)
            for k in range(len(self._segments))
        ]
        audio = concat_wav(wavs, pauses)
        if audio is None:
            self.stats.reason = "concat_failed"
            return None, self.stats
        self.stats.reason = "ok"
        return audio, self.stats
