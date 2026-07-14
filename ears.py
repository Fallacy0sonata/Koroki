"""Koroki's ears — voice-channel hearing (THE FIVE #1, v1 built 2026-07-05).

Chain: discord-ext-voice-recv sink (per-speaker 48 kHz stereo PCM)
  → phrase segmentation by packet gaps (Discord clients only transmit while
    the speaker's own VAD is open, so a ~700 ms packet gap IS the phrase end
    — no streaming VAD needed on our side)
  → Silero batch VAD as a noise filter (open-mic breath/keyboard guard)
  → faster-whisper STT (CPU int8, model from the LOCAL HF cache — resolve the
    snapshot path first: HF_HUB_OFFLINE=1 in .env makes hub-id loads raise
    even with a complete cache, the exact bug that silently degraded memory
    recall until 2026-07-05)
  → async callback into the bot (same query_orchestrator path as typed chat).

v1 scope: listens to the owner only (ears.listen_all flips that). Tone is
measured light (rms / duration / speech rate) and LOGGED ONLY — phase 2 wires
acoustics into the endocrine path (causal-chain honest), never into prompt
text as labels.

Runs inside the bot process (voice receive lives on the gateway connection).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger("koroki.ears")

_INPUT_RATE = 48_000   # discord decoded PCM: 48 kHz 16-bit stereo
_TARGET_RATE = 16_000  # whisper / silero rate (exact /3 decimation)


@dataclass
class _Stream:
    """Per-speaker accumulation state. Touched from the receive thread."""

    display_name: str
    chunks: list[bytes] = field(default_factory=list)
    last_packet: float = 0.0
    started: float = 0.0


@dataclass
class HeardPhrase:
    user_id: int
    display_name: str
    text: str
    duration_s: float
    rms: float
    chars_per_s: float
    # Continuous dimensional tone (audeering wav2vec2 dim-SER, ONNX CPU).
    # None when ears.tone_dims_enabled is off or the model is unavailable.
    arousal: Optional[float] = None
    dominance: Optional[float] = None
    valence: Optional[float] = None


class ToneDims:
    """Continuous arousal/dominance/valence from raw phrase audio (CPU ONNX).

    Model: audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim exported to
    tools/models/ser_dim_onnx (research arc 2026-07). This is the causal-chain
    honest signal for phase 2: acoustics -> endocrine, never labels into her
    prompt. v1 logs only; wiring into the body comes with the owner.
    """

    _OUTPUT_ORDER = ("arousal", "dominance", "valence")  # per audeering model card

    def __init__(self, model_dir: str) -> None:
        self._dir = model_dir
        self._session = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._session is None:
            import onnxruntime as ort

            path = str(Path(self._dir) / "model.onnx")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            self._session = ort.InferenceSession(
                path, providers=["CPUExecutionProvider"], sess_options=opts
            )
        return self._session

    def measure(self, audio16k: "np.ndarray") -> Optional[tuple[float, float, float]]:
        """audio16k: float32 mono 16 kHz in [-1, 1]. Returns (A, D, V) or None."""
        try:
            sess = self._ensure()
            x = audio16k[: 10 * 16_000].astype("float32")[None, :]  # cap 10 s
            out = sess.run(None, {sess.get_inputs()[0].name: x})
            vals = [float(v) for v in list(out[-1].reshape(-1))[:3]]
            if len(vals) < 3:
                return None
            return vals[0], vals[1], vals[2]
        except Exception:
            logger.debug("tone dims failed", exc_info=True)
            return None


class EarsSession:
    """One listening session bound to a VoiceRecvClient.

    Wiring: create, then `vc.listen(session.sink)`. Call close() to stop.
    on_phrase runs on the bot's event loop.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_phrase: Callable[[HeardPhrase], Awaitable[None]],
        *,
        owner_id: int,
        listen_all: bool = False,
        stt_model: str = "base",
        language: str = "",
        phrase_gap_ms: int = 700,
        min_phrase_ms: int = 350,
        max_phrase_s: float = 30.0,
        tone_dims_enabled: bool = False,
        tone_model_dir: str = "tools/models/ser_dim_onnx",
    ) -> None:
        # DAVE E2EE decrypt shim (2026-07-10): must patch voice-recv BEFORE the
        # bot calls vc.listen(); without it E2EE servers throw OpusError and she
        # hears nothing. Idempotent, no-op if davey is absent. See ears_dave_shim.
        try:
            from ears_dave_shim import install_dave_shim

            install_dave_shim()
        except Exception:
            logger.warning("DAVE shim install failed — E2EE listening may be deaf", exc_info=True)
        self._loop = loop
        self._on_phrase = on_phrase
        self._owner_id = int(owner_id)
        self._listen_all = bool(listen_all)
        self._stt_model_name = stt_model
        self._language = language.strip() or None
        self._phrase_gap_s = phrase_gap_ms / 1000.0
        self._min_phrase_s = min_phrase_ms / 1000.0
        self._max_phrase_s = float(max_phrase_s)

        self._streams: dict[int, _Stream] = {}
        self._streams_lock = threading.Lock()
        self._whisper = None
        self._whisper_lock = threading.Lock()
        self._tone: Optional[ToneDims] = None
        if tone_dims_enabled:
            tm = Path(tone_model_dir)
            if not tm.is_absolute():
                tm = Path(__file__).resolve().parent / tone_model_dir
            if (tm / "model.onnx").exists():
                self._tone = ToneDims(str(tm))
            else:
                logger.warning("tone dims enabled but model missing: %s", tm)
        self._closed = False
        self._watcher: Optional[asyncio.Task] = None
        self.sink = self._make_sink()
        self.phrases_heard = 0

    # ------------------------------------------------------------------
    # Sink (receive thread — keep write() minimal)
    # ------------------------------------------------------------------

    def _make_sink(self):
        from discord.ext import voice_recv

        session = self

        class _PhraseSink(voice_recv.AudioSink):
            def wants_opus(self) -> bool:
                return False  # decoded PCM

            def write(self, user, data) -> None:
                if session._closed or user is None or getattr(user, "bot", False):
                    return
                uid = int(user.id)
                if not session._listen_all and uid != session._owner_id:
                    return
                now = time.monotonic()
                with session._streams_lock:
                    st = session._streams.get(uid)
                    if st is None:
                        st = _Stream(display_name=getattr(user, "display_name", str(uid)))
                        st.started = now
                        session._streams[uid] = st
                    if not st.chunks:
                        st.started = now
                    st.chunks.append(data.pcm)
                    st.last_packet = now

            def cleanup(self) -> None:
                pass

        return _PhraseSink()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._watcher is None:
            self._watcher = self._loop.create_task(self._watch_loop(), name="koroki-ears")
        logger.info(
            "ears listening: model=%s lang=%s listen_all=%s gap=%.0fms",
            self._stt_model_name,
            self._language or "auto",
            self._listen_all,
            self._phrase_gap_s * 1000,
        )

    def close(self) -> None:
        self._closed = True
        if self._watcher is not None:
            self._watcher.cancel()
            self._watcher = None
        logger.info("ears closed (%d phrases heard)", self.phrases_heard)

    def warm(self) -> None:
        """Load whisper off the hot path (call via to_thread at ears_start)."""
        self._ensure_whisper()

    # ------------------------------------------------------------------
    # Phrase watcher (bot loop)
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            ready: list[tuple[int, _Stream, bytes]] = []
            with self._streams_lock:
                for uid, st in self._streams.items():
                    if not st.chunks:
                        continue
                    elapsed = now - st.started
                    gap = now - st.last_packet
                    if gap >= self._phrase_gap_s or elapsed >= self._max_phrase_s:
                        pcm = b"".join(st.chunks)
                        st.chunks = []
                        ready.append((uid, st, pcm))
            for uid, st, pcm in ready:
                duration = len(pcm) / (_INPUT_RATE * 2 * 2)  # 16-bit stereo
                if duration < self._min_phrase_s:
                    continue
                asyncio.get_running_loop().run_in_executor(
                    None, self._process_phrase, uid, st.display_name, pcm
                )

    # ------------------------------------------------------------------
    # STT worker (thread pool)
    # ------------------------------------------------------------------

    def _ensure_whisper(self):
        if self._whisper is not None:
            return self._whisper
        with self._whisper_lock:
            if self._whisper is not None:
                return self._whisper
            from faster_whisper import WhisperModel

            repo = f"Systran/faster-whisper-{self._stt_model_name}"
            path = repo
            try:
                from huggingface_hub import snapshot_download

                path = snapshot_download(repo, local_files_only=True)
            except Exception:
                logger.info("%s not in local HF cache — trying hub download", repo)
            t0 = time.time()
            self._whisper = WhisperModel(path, device="cpu", compute_type="int8")
            logger.info(
                "whisper %s ready in %.1fs (cpu int8)", self._stt_model_name, time.time() - t0
            )
            return self._whisper

    def _process_phrase(self, uid: int, name: str, pcm: bytes) -> None:
        try:
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size < 4:
                return
            audio = audio.reshape(-1, 2).mean(axis=1)          # stereo -> mono
            n = (audio.size // 3) * 3
            audio16 = audio[:n].reshape(-1, 3).mean(axis=1)    # 48k -> 16k (box)
            duration = audio16.size / _TARGET_RATE
            rms = float(np.sqrt(np.mean(np.square(audio16))) + 1e-12)

            # Noise gate: Discord's client VAD already gated transmission, but
            # open-mic setups leak breaths/keys. Batch Silero over the phrase.
            try:
                from faster_whisper.vad import VadOptions, get_speech_timestamps

                speech = get_speech_timestamps(
                    audio16, VadOptions(min_speech_duration_ms=200)
                )
                if not speech:
                    logger.debug("ears: dropped non-speech phrase (%.2fs)", duration)
                    return
            except Exception:
                pass  # filter is best-effort; never block hearing on it

            model = self._ensure_whisper()
            with self._whisper_lock:
                t0 = time.time()
                segments, info = model.transcribe(
                    audio16,
                    language=self._language,
                    beam_size=1,
                    without_timestamps=True,
                    condition_on_previous_text=False,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                stt_s = time.time() - t0

            if not text:
                return
            phrase = HeardPhrase(
                user_id=uid,
                display_name=name,
                text=text,
                duration_s=round(duration, 2),
                rms=round(rms, 4),
                chars_per_s=round(len(text) / max(duration, 0.1), 1),
            )
            if self._tone is not None:
                dims = self._tone.measure(audio16)
                if dims:
                    phrase.arousal, phrase.dominance, phrase.valence = (
                        round(dims[0], 3),
                        round(dims[1], 3),
                        round(dims[2], 3),
                    )
            self.phrases_heard += 1
            # Tone telemetry — phase-2 feeds acoustics into endocrine.
            logger.info(
                "ears: [%s] %.2fs stt=%.2fs rms=%.3f rate=%.1fc/s A/D/V=%s/%s/%s: %s",
                name,
                duration,
                stt_s,
                phrase.rms,
                phrase.chars_per_s,
                phrase.arousal,
                phrase.dominance,
                phrase.valence,
                text[:120],
            )
            asyncio.run_coroutine_threadsafe(self._on_phrase(phrase), self._loop)
        except Exception:
            logger.exception("ears: phrase processing failed")
