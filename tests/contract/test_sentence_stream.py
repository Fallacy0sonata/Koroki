"""Contract tests for sentence-streaming TTS (pipeline/sentence_stream.py)."""

import asyncio
import io
import wave

from services.orchestrator.pipeline.sentence_stream import (
    Sentence,
    SentenceAssembler,
    StreamingSpeech,
    concat_wav,
    pause_after_ms,
)


def _drip(assembler: SentenceAssembler, text: str, chunk: int = 3):
    """Feed text in tiny chunks like a token stream; return completed sentences."""
    out = []
    for i in range(0, len(text), chunk):
        out.extend(assembler.feed(text[i:i + chunk]))
    return out


# ── assembler ────────────────────────────────────────────────────────────────

def test_assembler_splits_basic_sentences() -> None:
    a = SentenceAssembler()
    sents = _drip(a, "Hello there. How are you today? I missed you! ")
    sents += a.flush()
    assert [s.text for s in sents] == ["Hello there.", "How are you today?", "I missed you!"]


def test_assembler_keeps_ellipsis_and_decimals_together() -> None:
    a = SentenceAssembler()
    sents = _drip(a, "Well... maybe. Version 2.5 is out. ")
    sents += a.flush()
    texts = [s.text for s in sents]
    assert texts == ["Well... maybe.", "Version 2.5 is out."]


def test_assembler_japanese_terminals() -> None:
    a = SentenceAssembler()
    sents = _drip(a, "おはよう。今日は雨だね！散歩する？ ")
    sents += a.flush()
    assert [s.text for s in sents] == ["おはよう。", "今日は雨だね！", "散歩する？"]


def test_assembler_keeps_closing_quotes() -> None:
    a = SentenceAssembler()
    sents = _drip(a, 'She said "hi." Then left. ')
    sents += a.flush()
    assert sents[0].text == 'She said "hi."'


def test_assembler_abbreviation_guard() -> None:
    a = SentenceAssembler()
    sents = _drip(a, "Ask Dr. Koro about it. Fine. ")
    sents += a.flush()
    assert sents[0].text == "Ask Dr. Koro about it."


# ── pause model (deterministic, text-derived — never random) ─────────────────

def test_pause_trail_off_is_long() -> None:
    assert pause_after_ms(Sentence("I wonder..."), Sentence("Anyway.")) == 420


def test_pause_run_on_is_minimal() -> None:
    assert pause_after_ms(Sentence("It rained."), Sentence("and then it stopped.")) == 40
    assert pause_after_ms(Sentence("It rained."), Sentence("But whatever!")) == 40


def test_pause_paragraph_is_longest() -> None:
    assert pause_after_ms(Sentence("Done.", paragraph_end=True), Sentence("New topic.")) == 500


def test_pause_defaults() -> None:
    assert pause_after_ms(Sentence("Really?"), Sentence("Yes.")) == 200
    assert pause_after_ms(Sentence("Fine."), Sentence("Okay.")) == 150
    assert pause_after_ms(Sentence("Last one."), None) == 0


def test_pause_is_deterministic() -> None:
    pair = (Sentence("Hm..."), Sentence("Okay."))
    assert len({pause_after_ms(*pair) for _ in range(20)}) == 1


# ── wav concat ───────────────────────────────────────────────────────────────

def _tone_wav(n_frames: int = 800, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * n_frames)
    return buf.getvalue()


def test_concat_inserts_silence_gaps() -> None:
    seg = _tone_wav(800)
    out = concat_wav([seg, seg], pauses_ms=[100, 0])
    with wave.open(io.BytesIO(out), "rb") as w:
        assert w.getnframes() == 800 + int(22050 * 0.1) + 800


def test_concat_rejects_mismatched_params() -> None:
    assert concat_wav([_tone_wav(rate=22050), _tone_wav(rate=16000)], [0, 0]) is None


# ── streaming orchestrator (async, run via asyncio.run — pytest-asyncio not a dep) ──

def _make_stream(calls: list[str], fail_on: str | None = None) -> StreamingSpeech:
    async def synth(shaped: str) -> bytes | None:
        calls.append(shaped)
        if fail_on and fail_on in shaped:
            return None
        await asyncio.sleep(0)
        return _tone_wav()

    return StreamingSpeech(synth=synth, shaper=lambda s, is_last: s, max_chars=0)


def test_streaming_happy_path_assembles_audio() -> None:
    async def _run() -> None:
        calls: list[str] = []
        ss = _make_stream(calls)
        text = "First sentence. Second one! And the tail"
        for i in range(0, len(text), 4):
            ss.feed(text[i:i + 4])
        audio, stats = await ss.finalize(text)
        assert audio is not None
        assert stats.reason == "ok"
        assert stats.synthesized == 3
        assert calls == ["First sentence.", "Second one!", "And the tail"]
        with wave.open(io.BytesIO(audio), "rb") as w:
            assert w.getnframes() > 800 * 3  # 3 segments + at least one gap

    asyncio.run(_run())


def test_streaming_falls_back_when_text_repaired() -> None:
    async def _run() -> None:
        ss = _make_stream([])
        text = "Something streamed. More text here."
        for i in range(0, len(text), 5):
            ss.feed(text[i:i + 5])
        audio, stats = await ss.finalize("Completely different final text.")
        assert audio is None
        assert stats.reason == "text_changed_after_stream"

    asyncio.run(_run())


def test_streaming_falls_back_on_segment_failure() -> None:
    async def _run() -> None:
        ss = _make_stream([], fail_on="Second")
        text = "First sentence. Second one fails. "
        for i in range(0, len(text), 5):
            ss.feed(text[i:i + 5])
        audio, stats = await ss.finalize(text)
        assert audio is None
        assert stats.reason == "segment_failed"

    asyncio.run(_run())


def test_streaming_respects_char_budget() -> None:
    async def _run() -> None:
        calls: list[str] = []

        async def synth(shaped: str) -> bytes | None:
            calls.append(shaped)
            return _tone_wav()

        ss = StreamingSpeech(synth=synth, shaper=lambda s, _l: s, max_chars=20)
        text = "Short one. This second sentence is far too long for the budget. "
        for i in range(0, len(text), 6):
            ss.feed(text[i:i + 6])
        audio, stats = await ss.finalize(text)
        assert calls == ["Short one."]          # second sentence skipped by budget
        assert audio is not None                # first segment still ships
        assert stats.chars_budgeted <= 20

    asyncio.run(_run())


def test_streaming_shaper_can_drop_sentences() -> None:
    async def _run() -> None:
        calls: list[str] = []

        async def synth(shaped: str) -> bytes | None:
            calls.append(shaped)
            return _tone_wav()

        # shaper empties everything → nothing synthesized → graceful None
        ss = StreamingSpeech(synth=synth, shaper=lambda s, _l: "", max_chars=0)
        text = "Hello there. Bye. "
        for i in range(0, len(text), 4):
            ss.feed(text[i:i + 4])
        audio, stats = await ss.finalize(text)
        assert audio is None
        assert stats.reason == "no_sentences"
        assert calls == []

    asyncio.run(_run())
