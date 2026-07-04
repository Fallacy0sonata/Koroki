from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import pipeline


def _resample(audio: np.ndarray, src_sr: int, target_sr: int = 16000) -> np.ndarray:
    if src_sr == target_sr:
        return audio
    try:
        import librosa

        return librosa.resample(audio, orig_sr=src_sr, target_sr=target_sr)
    except Exception:
        import torchaudio.functional as F

        wav = torch.from_numpy(audio).unsqueeze(0)
        wav = F.resample(wav, src_sr, target_sr)
        return wav.squeeze(0).cpu().numpy()


def transcribe_dir(audio_dir: Path, output_path: Path, model_id: str, limit: int | None = None) -> None:
    wav_files = sorted(audio_dir.glob("*.wav"))
    if limit is not None:
        wav_files = wav_files[:limit]

    if not wav_files:
        raise FileNotFoundError(f"No .wav files found in {audio_dir}")

    device = 0 if torch.cuda.is_available() else -1
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32

    asr = pipeline(
        task="automatic-speech-recognition",
        model=model_id,
        device=device,
        torch_dtype=dtype,
    )

    rows: list[dict] = []
    for wav_path in wav_files:
        audio, sr = sf.read(str(wav_path), dtype="float32")
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        audio = _resample(audio, sr, 16000)

        result = asr({"array": audio, "sampling_rate": 16000})
        text = " ".join(str(result.get("text", "")).split())

        rows.append(
            {
                "file": wav_path.name,
                "path": str(wav_path.as_posix()),
                "sample_rate": int(sr),
                "transcript": text,
            }
        )
        print(f"{wav_path.name}: {text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(rows)} transcripts -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe Koroki voice samples with Whisper")
    parser.add_argument("--audio-dir", default="voice_samples", help="Folder with .wav samples")
    parser.add_argument("--output", default="data/voice_transcripts.json", help="Output JSON path")
    parser.add_argument("--model", default="openai/whisper-small.en", help="Whisper model id")
    parser.add_argument("--limit", type=int, default=None, help="Optional file limit for quick test")
    args = parser.parse_args()

    transcribe_dir(Path(args.audio_dir), Path(args.output), args.model, args.limit)
