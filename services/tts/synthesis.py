"""
TTS synthesis engine.

Uses the official qwen-tts package (pip install qwen-tts), primarily with
Qwen3-TTS-12Hz-0.6B-Base voice-clone flow.

Phase 7 emergency fix:
- The old ProcessPoolExecutor design created a second CUDA context on Windows.
- On a single 12GB GPU that caused TTS to deadlock while Brain was resident.
- TTS now stays in the main process and runs its blocking work via asyncio.to_thread(),
  which keeps a single CUDA context alive while preserving FastAPI responsiveness.

When qwen-tts or GPU is unavailable, falls back to a 1-second WAV of
silence so the rest of the pipeline can be tested without a GPU.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import logging
import os
import subprocess
import shutil
import struct
import threading
import time
import tempfile
import wave
from pathlib import Path

from shared.utils.config import get_settings
from .voice_profiles import build_voice_style

logger = logging.getLogger("tts.synthesis")


def _ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 1)


def _shape0(value: object) -> int | None:
    shape = getattr(value, "shape", None)
    if not shape:
        return None
    try:
        return int(shape[0])
    except Exception:
        return None


def _is_cuda_graph_capture_error(exc: Exception) -> bool:
    text = str(exc).lower()
    needles = (
        "cudaerrorstreamcaptureinvalidated",
        "offset increment outside graph capture",
        "operation failed due to a previous error during capture",
        "capture",
        "cuda graph",
    )
    return any(needle in text for needle in needles)


def _configure_sox(tts_settings: dict, repo_root: Path) -> str | None:
    """Configure optional SoX path so qwen-tts can use native binaries when present."""
    configured = str(tts_settings.get("sox_path", "")).strip()
    sox_path: str | None = None

    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        candidate = candidate.resolve()
        if candidate.exists() and candidate.is_file():
            sox_path = str(candidate)
            # Some libraries read SOX_PATH directly.
            os.environ["SOX_PATH"] = sox_path
            # Ensure the containing folder is also available via PATH.
            os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{os.environ.get('PATH', '')}"
            logger.info("TTS SoX configured via settings: %s", sox_path)
        else:
            logger.warning("Configured sox_path does not exist: %s", candidate)

    detected = shutil.which("sox")
    if detected:
        logger.info("TTS SoX detected on PATH: %s", detected)
        return sox_path or detected

    logger.warning("TTS SoX not detected. qwen-tts may use slower fallback audio processing.")
    return sox_path

def _generate_silence_wav(sample_rate: int, duration_seconds: float) -> bytes:
    num_samples = int(sample_rate * duration_seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
    return buf.getvalue()
class TTSSynthesizer:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._sample_rate: int = self._settings["models"]["tts"].get("sample_rate", 24000)
        self._model_id: str = self._settings["models"]["tts"]["name"]
        self._tts_settings: dict = self._settings["models"]["tts"]
        self._backend: str = str(self._tts_settings.get("backend", "faster_qwen3")).strip().lower()
        self._attn_implementation: str = str(
            os.environ.get(
                "TTS_ATTN_IMPLEMENTATION_OVERRIDE",
                self._tts_settings.get("attn_implementation", "flash_attention_2"),
            )
        ).strip() or "flash_attention_2"
        self._max_seq_len: int = int(
            os.environ.get(
                "TTS_MAX_SEQ_LEN_OVERRIDE",
                self._tts_settings.get("max_seq_len", 1536),
            )
        )
        self._xvec_only_override = self._tts_settings.get("xvec_only_override", None)
        self._max_new_tokens: int = int(self._tts_settings.get("max_new_tokens", 512))
        self._do_sample: bool = bool(self._tts_settings.get("do_sample", True))
        self._temperature: float = float(self._tts_settings.get("temperature", 0.65))
        self._top_p: float = float(self._tts_settings.get("top_p", 0.9))
        self._top_k: int = int(self._tts_settings.get("top_k", 40))
        self._repetition_penalty: float = float(self._tts_settings.get("repetition_penalty", 1.08))
        self._sampling_seed = self._tts_settings.get("sampling_seed", None)
        self._non_streaming_mode: bool = bool(self._tts_settings.get("non_streaming_mode", True))
        self._output_gain_db: float = float(self._tts_settings.get("output_gain_db", 3.0))
        self._output_gain_linear: float = pow(10.0, self._output_gain_db / 20.0)
        self._output_speed: float = float(self._tts_settings.get("output_speed", 1.0))
        if self._output_speed <= 0:
            self._output_speed = 1.0
        self._output_speed_preserve_pitch: bool = bool(
            self._tts_settings.get("output_speed_preserve_pitch", True)
        )
        self._empty_cache_after_synthesize: bool = bool(
            self._tts_settings.get("empty_cache_after_synthesize", False)
        )
        self._clone_profiles: dict[str, dict] = self._settings["models"]["tts"].get(
            "clone_profiles",
            {},
        )
        self._fallback_voice: dict = self._settings["models"]["tts"].get(
            "fallback_voice",
            {"speaker": "Serena", "instruct": "Speak with elegant confidence and warm softness."},
        )
        self._repo_root = str(Path(__file__).resolve().parents[2])
        self._model = None
        self._clone_prompts: dict[str, object] = {}
        self._clone_profile_runtime: dict[str, dict[str, object]] = {}
        self._default_profile_name: str | None = None
        self._fallback_speaker = str(self._fallback_voice.get("speaker", "Serena"))
        self._fallback_instruct = str(
            self._fallback_voice.get(
                "instruct",
                "Speak with elegant confidence and warm softness.",
            )
        )
        self._lock = threading.Lock()
        self._ready = False
        self._sox_path: str | None = None
        self._supports_clone_instruct = False
        self._logged_clone_instruct_unsupported = False
        self._graph_fallback_applied = False

    def _time_stretch_audio(self, audio_wave: object, speed: float) -> object:
        """Simple speed control via linear resampling (keeps integration dependency-free)."""
        if abs(speed - 1.0) < 1e-6:
            return audio_wave
        try:
            import numpy as np

            wave = np.asarray(audio_wave, dtype=np.float32)
            if wave.size < 2:
                return wave

            # speed < 1.0 => slower/longer; speed > 1.0 => faster/shorter
            target_len = max(1, int(round(wave.shape[0] / speed)))
            src_idx = np.linspace(0.0, 1.0, num=wave.shape[0], endpoint=True)
            dst_idx = np.linspace(0.0, 1.0, num=target_len, endpoint=True)
            stretched = np.interp(dst_idx, src_idx, wave).astype(np.float32)
            return np.clip(stretched, -0.99, 0.99)
        except Exception:
            return audio_wave

    def _time_stretch_audio_preserve_pitch(
        self,
        audio_wave: object,
        sample_rate: int,
        speed: float,
    ) -> object:
        """Pitch-preserving speed control using SoX tempo effect."""
        if abs(speed - 1.0) < 1e-6:
            return audio_wave
        if not self._sox_path:
            return audio_wave

        try:
            import numpy as np
            import soundfile as sf

            wave = np.asarray(audio_wave, dtype=np.float32)
            if wave.size < 2:
                return wave

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f:
                in_path = in_f.name
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
                out_path = out_f.name

            try:
                sf.write(in_path, wave, sample_rate, format="WAV", subtype="PCM_16")

                cmd = [
                    self._sox_path,
                    in_path,
                    out_path,
                    "tempo",
                    "-s",
                    f"{speed:.4f}",
                ]
                proc = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    logger.warning(
                        "SoX tempo processing failed (code=%s): %s",
                        proc.returncode,
                        (proc.stderr or proc.stdout or "").strip(),
                    )
                    return audio_wave

                stretched, sr = sf.read(out_path, dtype="float32", always_2d=False)
                if sr != sample_rate:
                    logger.warning(
                        "SoX returned unexpected sample rate %s (expected %s); keeping original audio",
                        sr,
                        sample_rate,
                    )
                    return audio_wave
                if getattr(stretched, "ndim", 1) > 1:
                    stretched = stretched.mean(axis=1)
                return np.clip(np.asarray(stretched, dtype=np.float32), -0.99, 0.99)
            finally:
                try:
                    os.remove(in_path)
                except Exception:
                    pass
                try:
                    os.remove(out_path)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Pitch-preserving speed processing failed; using original audio: %s", exc)
            return audio_wave

    def _prepare_clone_profiles(self, root: Path) -> None:
        self._clone_profile_runtime = {}
        for profile_name, cfg in self._clone_profiles.items():
            raw_ref_audio = str(cfg.get("ref_audio", "")).strip()
            if not raw_ref_audio:
                continue

            ref_path = Path(raw_ref_audio)
            if not ref_path.is_absolute():
                ref_path = root / ref_path
            if not ref_path.exists():
                logger.warning("Clone ref audio missing for '%s': %s", profile_name, ref_path)
                continue

            ref_text = str(cfg.get("ref_text", "")).strip()
            x_vector_only = bool(cfg.get("x_vector_only_mode", False))
            if not ref_text:
                x_vector_only = True

            if isinstance(self._xvec_only_override, bool):
                x_vector_only = self._xvec_only_override

            self._clone_profile_runtime[profile_name] = {
                "ref_audio": str(ref_path),
                "ref_text": ref_text,
                "xvec_only": x_vector_only,
                "instruct": str(cfg.get("instruct", "")).strip(),
            }
            logger.info(
                "Prepared clone profile '%s' (xvec_only=%s override=%s) from %s",
                profile_name,
                x_vector_only,
                self._xvec_only_override,
                ref_path,
            )

        self._default_profile_name = next(iter(self._clone_profile_runtime), None)

    def _load_model_backend(self, device: str, dtype: object) -> None:
        self._model = None
        self._clone_prompts = {}
        self._supports_clone_instruct = False

        if self._backend == "faster_qwen3" and str(device).startswith("cuda"):
            from faster_qwen3_tts import FasterQwen3TTS

            logger.info("Loading FasterQwen3 backend")
            self._model = FasterQwen3TTS.from_pretrained(
                self._model_id,
                device=device,
                dtype=dtype,
                attn_implementation=self._attn_implementation,
                max_seq_len=self._max_seq_len,
            )
            try:
                sig = inspect.signature(self._model.generate_voice_clone)
                self._supports_clone_instruct = "instruct" in sig.parameters
            except Exception:
                self._supports_clone_instruct = False
            logger.info(
                "FasterQwen3 clone-instruct support: %s",
                self._supports_clone_instruct,
            )
            return

        from qwen_tts import Qwen3TTSModel

        if self._backend == "faster_qwen3" and not str(device).startswith("cuda"):
            logger.warning("FasterQwen requires CUDA; falling back to qwen_tts backend")

        self._backend = "qwen_tts"
        self._supports_clone_instruct = True
        self._model = Qwen3TTSModel.from_pretrained(
            self._model_id,
            device_map=device,
            dtype=dtype,
            attn_implementation=self._attn_implementation,
            low_cpu_mem_usage=True,
        )

        for profile_name, profile_cfg in self._clone_profile_runtime.items():
            prompt_kwargs: dict = {"ref_audio": profile_cfg["ref_audio"]}
            if profile_cfg["ref_text"]:
                prompt_kwargs["ref_text"] = profile_cfg["ref_text"]
            else:
                prompt_kwargs["x_vector_only_mode"] = bool(profile_cfg["xvec_only"])

            try:
                prompt_item = self._model.create_voice_clone_prompt(**prompt_kwargs)
                self._clone_prompts[profile_name] = prompt_item
                logger.info("Loaded clone prompt for '%s'", profile_name)
            except Exception as exc:
                logger.warning("Failed clone prompt for '%s': %s", profile_name, exc)

    def _fallback_to_safe_tts_backend(self, reason: Exception | str) -> bool:
        if self._graph_fallback_applied:
            return False
        if self._backend != "faster_qwen3":
            return False

        logger.warning(
            "TTS runtime fallback triggered due to graph/capture failure: %s. Falling back to qwen_tts + sdpa.",
            reason,
        )
        try:
            import torch

            self._graph_fallback_applied = True
            self._backend = "qwen_tts"
            self._attn_implementation = "sdpa"
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            self._load_model_backend(device=device, dtype=dtype)
            self._ready = self._model is not None
            logger.info(
                "TTS fallback backend ready: backend=%s attn_implementation=%s",
                self._backend,
                self._attn_implementation,
            )
            return self._ready
        except Exception as fallback_exc:
            logger.error("TTS fallback backend load failed: %s", fallback_exc)
            return False

    def load(self) -> None:
        """Load model and precompute clone prompts in the main process."""
        try:
            import torch

            root = Path(self._repo_root)
            self._sox_path = _configure_sox(self._tts_settings, root)

            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

            self._prepare_clone_profiles(root)
            self._load_model_backend(device=device, dtype=dtype)

            self._ready = self._model is not None
            if self._ready:
                logger.info(
                    "TTS ready: model=%s backend=%s clone_profiles=%s",
                    self._model_id,
                    self._backend,
                    sorted(self._clone_profile_runtime.keys()),
                )
                logger.info(
                    "TTS runtime config: attn_implementation=%s max_seq_len=%s sox=%s",
                    self._attn_implementation,
                    self._max_seq_len,
                    bool(self._sox_path),
                )
                _warmup_profile = self._default_profile_name
                logger.info("TTS: Running startup warmup synthesis (profile=%s)...", _warmup_profile)
                warmup_audio = self._synthesize_blocking("Hello.", _warmup_profile, request_id="tts_warmup")
                if warmup_audio:
                    logger.info("TTS: Warmup complete — runtime path is usable.")
        except Exception as exc:
            logger.error("TTS model load failed: %s — stub mode active", exc)
            self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _synthesize_blocking(
        self,
        text: str,
        voice_profile_name: str,
        request_id: str | None = None,
        emotion: str | None = None,
        emotion_intensity: int = 50,
        emotion_variant: str | None = None,
        _retry_after_fallback: bool = True,
    ) -> bytes:
        request_label = request_id or "tts_local"
        request_started = time.perf_counter()
        if self._model is None:
            logger.info(
                "[%s] TTS PROFILE request_entry_ms=0.0 mode=stub silence_fallback=true total_ms=0.0",
                request_label,
            )
            return _generate_silence_wav(self._sample_rate, max(1.0, len(text) * 0.05))

        try:
            import soundfile as sf
            import torch

            voice_style = build_voice_style(
                request_id=request_label,
                emotion=emotion,
                intensity=emotion_intensity,
                variant=emotion_variant,
            )
            effective_temperature = max(0.15, min(1.2, self._temperature + voice_style.temperature_delta))
            effective_top_p = max(0.5, min(1.0, self._top_p + voice_style.top_p_delta))
            effective_repetition_penalty = max(
                1.0,
                min(1.3, self._repetition_penalty + voice_style.repetition_penalty_delta),
            )
            effective_output_speed = max(0.82, min(1.12, self._output_speed + voice_style.speed_delta))

            lock_wait_started = time.perf_counter()
            with self._lock:
                lock_wait_ms = round((time.perf_counter() - lock_wait_started) * 1000.0, 1)
                internal_metrics: dict[str, object] = {
                    "generate_ms": None,
                    "decode_ms": None,
                    "generated_code_lengths": None,
                    "decoded_sample_lengths": None,
                    "decode_sample_rate": None,
                }

                prompt_started = time.perf_counter()
                prompt_item = self._clone_prompts.get(voice_profile_name)
                prompt_lookup_ms = _ms_since(prompt_started)
                profile_cfg = self._clone_profile_runtime.get(voice_profile_name)
                if profile_cfg is None and self._default_profile_name is not None:
                    profile_cfg = self._clone_profile_runtime.get(self._default_profile_name)

                inner_model = getattr(self._model, "model", None)
                speech_tokenizer = getattr(inner_model, "speech_tokenizer", None) if inner_model is not None else None
                original_generate = getattr(inner_model, "generate", None) if inner_model is not None else None
                original_decode = getattr(speech_tokenizer, "decode", None) if speech_tokenizer is not None else None

                def timed_generate(*args, **kwargs):
                    started = time.perf_counter()
                    result = original_generate(*args, **kwargs)
                    internal_metrics["generate_ms"] = _ms_since(started)
                    try:
                        talker_codes_list = result[0] if isinstance(result, tuple) else result
                        internal_metrics["generated_code_lengths"] = [
                            _shape0(item) for item in talker_codes_list
                        ]
                    except Exception:
                        internal_metrics["generated_code_lengths"] = None
                    return result

                def timed_decode(*args, **kwargs):
                    started = time.perf_counter()
                    result = original_decode(*args, **kwargs)
                    internal_metrics["decode_ms"] = _ms_since(started)
                    try:
                        wavs, sample_rate = result
                        internal_metrics["decoded_sample_lengths"] = [len(wav) for wav in wavs]
                        internal_metrics["decode_sample_rate"] = int(sample_rate)
                    except Exception:
                        internal_metrics["decoded_sample_lengths"] = None
                        internal_metrics["decode_sample_rate"] = None
                    return result

                if original_generate is not None:
                    inner_model.generate = timed_generate
                if original_decode is not None:
                    speech_tokenizer.decode = timed_decode

                if isinstance(self._sampling_seed, int):
                    torch.manual_seed(self._sampling_seed)
                    if torch.cuda.is_available():
                        torch.cuda.manual_seed_all(self._sampling_seed)
                effective_seed = int(torch.initial_seed())
                text_hash = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
                profile_ref_audio = str(profile_cfg["ref_audio"]) if profile_cfg is not None else ""
                profile_ref_text_chars = len(str(profile_cfg["ref_text"])) if profile_cfg is not None else 0
                profile_xvec_only = bool(profile_cfg["xvec_only"]) if profile_cfg is not None else False

                inference_started = time.perf_counter()
                try:
                    if self._backend == "faster_qwen3" and profile_cfg is not None:
                        instruct_parts = [
                            str(profile_cfg.get("instruct", "")).strip(),
                            voice_style.instruct_suffix,
                        ]
                        merged_instruct = " ".join(part for part in instruct_parts if part).strip()
                        _clone_kwargs: dict = dict(
                            text=text,
                            language="English",
                            ref_audio=str(profile_cfg["ref_audio"]),
                            ref_text=str(profile_cfg["ref_text"]),
                            max_new_tokens=self._max_new_tokens,
                            do_sample=self._do_sample,
                            temperature=effective_temperature,
                            top_p=effective_top_p,
                            top_k=self._top_k,
                            repetition_penalty=effective_repetition_penalty,
                            non_streaming_mode=self._non_streaming_mode,
                            xvec_only=bool(profile_cfg["xvec_only"]),
                        )
                        if merged_instruct and self._supports_clone_instruct:
                            _clone_kwargs["instruct"] = merged_instruct
                        elif merged_instruct and not self._logged_clone_instruct_unsupported:
                            logger.info(
                                "TTS backend '%s' ignores clone-profile instruct in voice_clone mode; using ref audio/text only.",
                                self._backend,
                            )
                            self._logged_clone_instruct_unsupported = True
                        wavs, sr = self._model.generate_voice_clone(**_clone_kwargs)
                        voice_mode = "voice_clone_faster"
                    elif prompt_item is not None:
                        wavs, sr = self._model.generate_voice_clone(
                            text=text,
                            language="English",
                            voice_clone_prompt=prompt_item,
                            non_streaming_mode=self._non_streaming_mode,
                            max_new_tokens=self._max_new_tokens,
                            do_sample=self._do_sample,
                            temperature=effective_temperature,
                            top_p=effective_top_p,
                            top_k=self._top_k,
                            repetition_penalty=effective_repetition_penalty,
                        )
                        voice_mode = "voice_clone"
                    else:
                        fallback_instruct = " ".join(
                            part
                            for part in (self._fallback_instruct, voice_style.instruct_suffix)
                            if part
                        ).strip()
                        wavs, sr = self._model.generate_custom_voice(
                            text=text,
                            language="English",
                            speaker=self._fallback_speaker,
                            instruct=fallback_instruct,
                            max_new_tokens=self._max_new_tokens,
                            do_sample=self._do_sample,
                            temperature=effective_temperature,
                            top_p=effective_top_p,
                            top_k=self._top_k,
                            repetition_penalty=effective_repetition_penalty,
                        )
                        voice_mode = "custom_voice"
                finally:
                    if original_generate is not None:
                        inner_model.generate = original_generate
                    if original_decode is not None:
                        speech_tokenizer.decode = original_decode
                model_inference_ms = _ms_since(inference_started)

                wav_encode_started = time.perf_counter()
                audio_wave = wavs[0]
                if self._output_gain_linear != 1.0:
                    try:
                        import numpy as np

                        audio_wave = np.asarray(audio_wave, dtype=np.float32) * self._output_gain_linear
                        audio_wave = np.clip(audio_wave, -0.99, 0.99)
                    except Exception:
                        # If gain processing fails for any reason, keep original waveform.
                        audio_wave = wavs[0]
                if abs(effective_output_speed - 1.0) > 1e-6:
                    if self._output_speed_preserve_pitch:
                        adjusted = self._time_stretch_audio_preserve_pitch(audio_wave, sr, effective_output_speed)
                        if adjusted is audio_wave and not self._sox_path:
                            logger.warning(
                                "output_speed_preserve_pitch=true but SoX unavailable; keeping original speed to avoid pitch shift."
                            )
                        else:
                            audio_wave = adjusted
                    else:
                        audio_wave = self._time_stretch_audio(audio_wave, effective_output_speed)
                buf = io.BytesIO()
                sf.write(buf, audio_wave, sr, format="WAV", subtype="PCM_16")
                buf.seek(0)
                audio_bytes = buf.read()
                wav_encoding_ms = _ms_since(wav_encode_started)

            cuda_cleanup_ms = 0.0
            if torch.cuda.is_available() and self._empty_cache_after_synthesize:
                cuda_cleanup_started = time.perf_counter()
                torch.cuda.empty_cache()
                cuda_cleanup_ms = _ms_since(cuda_cleanup_started)

            total_ms = _ms_since(request_started)
            logger.info(
                "[%s] TTS PROFILE request_entry_ms=0.0 lock_wait_ms=%s voice_clone_generation_ms=%s "
                "model_inference_ms=%s wav_encoding_ms=%s cuda_cleanup_ms=%s total_ms=%s "
                "mode=%s text_chars=%d cached_prompt=%s non_streaming_mode=%s max_new_tokens=%d "
                "internal_generate_ms=%s internal_decode_ms=%s generated_code_lengths=%s "
                "decoded_sample_lengths=%s decode_sample_rate=%s backend=%s output_gain_db=%s "
                "output_speed=%s output_speed_preserve_pitch=%s clone_instruct_supported=%s do_sample=%s temperature=%s top_p=%s top_k=%s repetition_penalty=%s sampling_seed=%s "
                "effective_seed=%s text_hash=%s profile_xvec_only=%s ref_text_chars=%d ref_audio=%s emotion=%s emotion_intensity=%s emotion_variant=%s",
                request_label,
                lock_wait_ms,
                prompt_lookup_ms,
                model_inference_ms,
                wav_encoding_ms,
                cuda_cleanup_ms,
                total_ms,
                voice_mode,
                len(text),
                prompt_item is not None,
                self._non_streaming_mode,
                self._max_new_tokens,
                internal_metrics["generate_ms"],
                internal_metrics["decode_ms"],
                internal_metrics["generated_code_lengths"],
                internal_metrics["decoded_sample_lengths"],
                internal_metrics["decode_sample_rate"],
                self._backend,
                self._output_gain_db,
                effective_output_speed,
                self._output_speed_preserve_pitch,
                self._supports_clone_instruct,
                self._do_sample,
                effective_temperature,
                effective_top_p,
                self._top_k,
                effective_repetition_penalty,
                self._sampling_seed,
                effective_seed,
                text_hash,
                profile_xvec_only,
                profile_ref_text_chars,
                profile_ref_audio,
                voice_style.emotion,
                voice_style.intensity,
                voice_style.variant,
            )

            return audio_bytes
        except Exception as exc:
            if _retry_after_fallback and _is_cuda_graph_capture_error(exc):
                if self._fallback_to_safe_tts_backend(exc):
                    return self._synthesize_blocking(
                        text,
                        voice_profile_name,
                        request_id,
                        emotion,
                        emotion_intensity,
                        emotion_variant,
                        _retry_after_fallback=False,
                    )
            logger.error(
                "[%s] Synthesis failed after %sms: %s — returning silence",
                request_label,
                _ms_since(request_started),
                exc,
            )
            return _generate_silence_wav(self._sample_rate, max(1.0, len(text) * 0.05))

    async def synthesize(
        self,
        text: str,
        voice_profile_name: str,
        request_id: str | None = None,
        emotion: str | None = None,
        emotion_intensity: int = 50,
        emotion_variant: str | None = None,
    ) -> bytes:
        """Synthesize text to WAV bytes without blocking the event loop."""
        if self._model is None or not self._ready:
            return _generate_silence_wav(self._sample_rate, max(1.0, len(text) * 0.05))
        request_label = request_id or "tts_local"
        async_started = time.perf_counter()
        audio_bytes = await asyncio.to_thread(
            self._synthesize_blocking,
            text,
            voice_profile_name,
            request_id,
            emotion,
            emotion_intensity,
            emotion_variant,
        )
        logger.info(
            "[%s] TTS ASYNC request_entry_ms=0.0 thread_handoff_total_ms=%s text_chars=%d voice=%s",
            request_label,
            _ms_since(async_started),
            len(text),
            voice_profile_name,
        )
        return audio_bytes
