"""
Genius lyrics fetching + CTC forced alignment for sing_song.py.

Replaces Whisper ASR for songs where Whisper fails to transcribe all vocal
sections (heavy production music, repeated chorus sections).

Pipeline:
  1. Fetch complete lyrics from Genius API (all verses + all chorus repeats)
  2. Convert lyrics lines to hiragana (wav2vec2 Japanese model uses kana vocab)
  3. Run demucsed vocals through wav2vec2 → CTC log-probabilities
  4. ctc-segmentation DP → optimal per-line timestamps for the full song

Output format matches syncedlyrics/Whisper: list of {"start", "end", "text"} dicts.
The per-line SOFA segmented path in sing_song.py handles both sources identically.

Dependencies (install in .venv_diffsinger):
  pip install lyricsgenius ctc-segmentation transformers torch

Environment:
  GENIUS_API_TOKEN — free API token from genius.com/api-clients
"""

from __future__ import annotations

import os
import re
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf


_CTC_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-japanese"
_TARGET_SR = 16000  # wav2vec2 requires 16kHz input


def _to_hiragana(text: str) -> str:
    """Convert Japanese text to hiragana. wav2vec2 Japanese vocab is kana-based."""
    try:
        import pykakasi
        kks = pykakasi.kakasi()
        result = kks.convert(text)
        return "".join(item["hira"] or item["orig"] for item in result)
    except ImportError:
        return text


def _read_genius_token() -> str | None:
    """Get GENIUS_API_TOKEN from environment or .env file (whichever is set)."""
    token = os.environ.get("GENIUS_API_TOKEN")
    if token:
        return token
    # sing_song.py runs as a standalone script and doesn't load .env automatically.
    # Read the token directly from the .env file at the repo root.
    dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    if not dotenv_path.exists():
        return None
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GENIUS_API_TOKEN="):
            value = line[len("GENIUS_API_TOKEN="):].strip()
            return value if value else None
    return None


def _has_japanese(text: str) -> bool:
    """Return True if text contains hiragana, katakana, or CJK characters."""
    return bool(re.search(r'[ぁ-鿿]', text))


def fetch_genius_lyrics(query: str) -> list[str] | None:
    """
    Fetch complete song lyrics from Genius API.

    Unlike syncedlyrics (which often deduplicates repeated chorus sections),
    Genius returns the full lyrics as performed — all verses and all repeats.

    Requires GENIUS_API_TOKEN in .env (free from genius.com/api-clients).
    Returns list of non-empty Japanese-containing lines, or None if unavailable.

    Genius sometimes returns an English translation page instead of the original
    Japanese — we detect this and reject the result so Whisper is used instead.
    """
    token = _read_genius_token()
    if not token:
        print("  [genius] GENIUS_API_TOKEN not set in .env — skipping Genius")
        print("  To enable: add GENIUS_API_TOKEN=<token> to .env (free at genius.com/api-clients)")
        return None

    try:
        import lyricsgenius
    except ImportError:
        print("  [genius] lyricsgenius not installed — skipping")
        print("  Install: .venv_diffsinger\\Scripts\\pip install lyricsgenius")
        return None

    try:
        genius = lyricsgenius.Genius(token)
        genius.remove_section_headers = True
        genius.retries = 2
        genius.timeout = 10
        # Windows certstore mismatch — disable cert verification on the genius
        # session. Genius lyrics traffic is non-sensitive (public data).
        try:
            import urllib3
            urllib3.disable_warnings()
            if hasattr(genius, "_session"):
                genius._session.verify = False
        except Exception:
            pass
        print(f"  [genius] searching '{query}'...")
        song = genius.search_song(query)
        if not song:
            print("  [genius] song not found")
            return None

        lines = []
        for line in song.lyrics.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Genius appends section headers [Verse 1], [Chorus], etc.
            if re.match(r"^\[.*\]$", line):
                continue
            # Genius appends contributor/translation metadata at the end
            if any(kw in line for kw in ("Contributors", "Translations", "Embed")):
                continue
            lines.append(line)

        if not lines:
            print("  [genius] lyrics parsed but empty after filtering")
            return None

        def _parse_lyrics(raw_lyrics: str) -> list[str]:
            result = []
            for line in raw_lyrics.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if re.match(r"^\[.*\]$", line):
                    continue
                if any(kw in line for kw in ("Contributors", "Translations", "Embed")):
                    continue
                result.append(line)
            return result

        # Check if Genius returned a non-Japanese page (e.g. English translation).
        # SOFA can only align Japanese text — if < 30% of lines contain Japanese
        # characters, the lyrics are not usable for forced alignment.
        japanese_lines = [l for l in lines if _has_japanese(l)]
        japanese_ratio = len(japanese_lines) / len(lines) if lines else 0

        if japanese_ratio < 0.30:
            print(f"  [genius] lyrics are {100*(1-japanese_ratio):.0f}% non-Japanese "
                  f"({len(lines) - len(japanese_lines)}/{len(lines)} lines have no Japanese text)")
            print("  [genius] likely an English translation page — retrying with 'original'...")
            # Genius often has a separate "original" Japanese page. Try adding "original"
            # or "Japanese" to the query so the Japanese page ranks above the translation.
            for retry_suffix in (" original", " Japanese", " 歌詞"):
                retry_song = genius.search_song(query + retry_suffix)
                if retry_song and retry_song.url != song.url:
                    retry_lines = _parse_lyrics(retry_song.lyrics)
                    retry_ja = [l for l in retry_lines if _has_japanese(l)]
                    if len(retry_ja) / max(len(retry_lines), 1) >= 0.30:
                        print(f"  [genius] retry '{retry_suffix}' found Japanese lyrics "
                              f"({len(retry_ja)} lines)")
                        lines = retry_ja
                        japanese_lines = retry_ja
                        break
            else:
                print("  [genius] all retries returned non-Japanese — skipping")
                print("  [genius] Tip: pass the Japanese song title directly, e.g. 'アイドル YOASOBI'")
                return None

        # Drop individual English-only lines (ad-libs like '(Hey! Hey!)' that SOFA
        # cannot convert to phonemes — they would make per-line SOFA skip those clips).
        dropped = len(lines) - len(japanese_lines)
        if dropped:
            print(f"  [genius] removed {dropped} non-Japanese line(s) (English ad-libs)")
        lines = japanese_lines

        print(f"  [genius] {len(lines)} Japanese lyric lines (full song including repeats)")
        return lines
    except Exception as e:
        print(f"  [genius] failed: {e}")
        return None


def fetch_utanet_lyrics(query: str) -> list[str] | None:
    """
    Fetch Japanese lyrics from uta-net.com — Japan's largest lyrics database.

    Always returns original Japanese text (no English translation risk).
    Searches by song title + artist, returns full lyrics split into lines.
    Falls back gracefully if the song isn't found or the site is unreachable.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    try:
        # uta-net search: POST to search endpoint
        search_url = "https://www.uta-net.com/search/"
        params = {"target": "ST", "type": "in", "query": query}
        r = requests.get(search_url, params=params, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        # First search result
        first = soup.select_one("table.search-table tbody tr td.song-title a")
        if not first:
            return None

        song_url = "https://www.uta-net.com" + first["href"]
        r2 = requests.get(song_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r2.status_code != 200:
            return None

        soup2 = BeautifulSoup(r2.text, "html.parser")
        lyric_div = soup2.select_one("div#kashi_area")
        if not lyric_div:
            return None

        # uta-net uses <br> tags for line breaks inside the div
        for br in lyric_div.find_all("br"):
            br.replace_with("\n")
        raw = lyric_div.get_text("\n")

        lines = []
        for line in raw.split("\n"):
            line = line.strip()
            if line and _has_japanese(line):
                lines.append(line)

        if not lines:
            return None

        print(f"  [uta-net] {len(lines)} lyric lines")
        return lines
    except Exception as e:
        print(f"  [uta-net] failed: {e}")
        return None


def rms_silence_align(
    vocals_path: str,
    lyrics_lines: list[str],
    silence_thresh_ratio: float = 0.08,
    min_silence_s: float = 0.15,
    audio_start_s: float = 0.0,
) -> list[dict]:
    """
    Align lyrics to vocal audio by detecting RMS silence gaps between sung phrases.

    Finds natural phrase boundaries without ASR — only the vocal audio and correct
    text are needed. Silence gaps in the demucsed vocal correspond to the singer
    pausing between lyric lines.

    silence_thresh_ratio: silence = RMS < this fraction of the median voiced RMS.
    min_silence_s: minimum gap duration to count as a phrase boundary.

    Returns list of {"start", "end", "text"} dicts, one per lyric line.
    """
    audio, sr = sf.read(vocals_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    # Trim to the region we're aligning (e.g. only the second half of the song)
    if audio_start_s > 0:
        start_sample = min(int(audio_start_s * sr), len(audio))
        audio = audio[start_sample:]
    total_duration = len(audio) / sr

    n_lines = len(lyrics_lines)
    if n_lines <= 1:
        return [{"start": 0.0, "end": round(total_duration, 3),
                 "text": lyrics_lines[0] if lyrics_lines else ""}]

    # Short-time RMS via numpy strides — 10ms hop, 50ms window
    hop_s, win_s = 0.010, 0.050
    hop, win = int(hop_s * sr), int(win_s * sr)
    n_frames = max(1, (len(audio) - win) // hop + 1)
    # Clip to avoid out-of-bounds in stride_tricks
    audio_clipped = audio[: n_frames * hop + win]
    frames = np.lib.stride_tricks.as_strided(
        audio_clipped,
        shape=(n_frames, win),
        strides=(audio_clipped.strides[0] * hop, audio_clipped.strides[0]),
    )
    rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Adaptive silence threshold relative to the median voiced RMS
    silence_thresh = float(np.median(rms)) * silence_thresh_ratio
    is_silence = rms < silence_thresh
    min_frames = max(1, int(min_silence_s / hop_s))

    # Vectorised silence-region detection
    padded = np.concatenate([[False], is_silence, [False]])
    sil_starts = np.where(~padded[:-1] & padded[1:])[0]
    sil_ends   = np.where( padded[:-1] & ~padded[1:])[0]
    sil_durs   = sil_ends - sil_starts

    valid     = sil_durs >= min_frames
    sil_starts, sil_ends, sil_durs = sil_starts[valid], sil_ends[valid], sil_durs[valid]

    print(f"  [rms-align] {len(sil_starts)} silence gaps detected in {total_duration:.0f}s vocal")

    n_bounds_needed = n_lines - 1

    if len(sil_starts) >= n_bounds_needed:
        # Take the N-1 longest (strongest) silence gaps as phrase boundaries
        top_idx = np.argsort(sil_durs)[::-1][:n_bounds_needed]
        top_idx = np.sort(top_idx)
        boundaries = [(float(sil_starts[i]) + float(sil_ends[i])) / 2 * hop_s
                      for i in top_idx]
    else:
        # Fewer gaps than needed — use all we have, then bisect the longest voiced segments
        boundaries = sorted(
            (float(sil_starts[i]) + float(sil_ends[i])) / 2 * hop_s
            for i in range(len(sil_starts))
        )
        all_pts = [0.0] + boundaries + [total_duration]
        while len(boundaries) < n_bounds_needed:
            gaps = [(all_pts[i + 1] - all_pts[i], i) for i in range(len(all_pts) - 1)]
            _, lg_idx = max(gaps)
            split = (all_pts[lg_idx] + all_pts[lg_idx + 1]) / 2
            boundaries.append(split)
            all_pts = sorted(all_pts + [split])
        boundaries = sorted(boundaries)

    starts_t = [0.0] + boundaries
    ends_t   = boundaries + [total_duration]

    result = [
        {"start": round(s + audio_start_s, 3), "end": round(e + audio_start_s, 3), "text": line}
        for s, e, line in zip(starts_t, ends_t, lyrics_lines)
    ]
    print(f"  [rms-align] {len(result)} segments mapped "
          f"(avg {total_duration / n_lines:.1f}s/line"
          + (f", starting at {audio_start_s:.0f}s)" if audio_start_s > 0 else ")"))
    return result


def pace_align_lyrics(
    genius_lines: list[str],
    audio_duration: float,
    synced_segs: list[dict] | None = None,
) -> list[dict]:
    """
    Distribute Genius lyrics across the song using a pace derived from the audio.

    When syncedlyrics partially covers the song, we derive the per-line pace from
    its timing (which reflects the actual song rhythm). Otherwise we divide the
    audio duration uniformly.

    This completely bypasses Whisper — the correct Genius text is used directly,
    and SOFA handles per-phoneme timing within each line window.

    Returns list of {"start", "end", "text"} dicts covering the full audio duration.
    """
    n = len(genius_lines)
    if n == 0:
        return []

    if synced_segs and len(synced_segs) >= 3:
        # Derive pace from syncedlyrics: covers the unique-section portion of the song.
        # Scale the per-line pace so all genius lines fill the full audio duration.
        synced_dur = synced_segs[-1]["end"]
        synced_n = len(synced_segs)
        raw_pace = synced_dur / synced_n
        # Scale to fit all genius lines into the actual audio duration
        pace = audio_duration / n
        print(f"  [pace-align] {n} Genius lines across {audio_duration:.0f}s "
              f"(syncedlyrics pace: {raw_pace:.2f}s/line → scaled: {pace:.2f}s/line)")
    else:
        pace = audio_duration / n
        print(f"  [pace-align] {n} Genius lines across {audio_duration:.0f}s "
              f"({pace:.2f}s/line, uniform)")

    return [
        {
            "start": round(i * pace, 3),
            "end": round(min((i + 1) * pace, audio_duration), 3),
            "text": line,
        }
        for i, line in enumerate(genius_lines)
    ]


def ctc_align_lyrics(
    vocals_path: str,
    lyrics_lines: list[str],
) -> list[dict] | None:
    """
    Force-align lyrics lines to vocal audio via CTC segmentation.

    Uses wav2vec2-large-xlsr-53-japanese for per-frame character probabilities,
    then ctc-segmentation dynamic programming for globally optimal line timestamps.
    First run downloads the model (~1.2GB) to HuggingFace cache.

    Does NOT do ASR — only alignment of KNOWN text. Works on demucsed vocals
    even with residual instrumental bleed that defeats Whisper transcription.

    Returns list of {"start": float, "end": float, "text": str} dicts
    in the same format as syncedlyrics/Whisper output, or None on failure.
    """
    try:
        import torch
        from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC
        import ctc_segmentation
    except ImportError as e:
        print(f"  [ctc-align] missing dependency: {e}")
        print("  Install: .venv_diffsinger\\Scripts\\pip install ctc-segmentation transformers")
        return None

    import torch.nn.functional as F

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load audio at 16kHz
    audio, sr = sf.read(vocals_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if sr != _TARGET_SR:
        from scipy.signal import resample_poly
        g = gcd(sr, _TARGET_SR)
        audio = resample_poly(audio, _TARGET_SR // g, sr // g).astype(np.float32)

    total_duration = len(audio) / _TARGET_SR
    print(f"  [ctc-align] audio: {total_duration:.0f}s, {len(lyrics_lines)} lines to align")

    # Load model (cached after first download)
    print(f"  [ctc-align] loading {_CTC_MODEL_ID} (first run: ~1.2GB download)...")
    processor = Wav2Vec2Processor.from_pretrained(_CTC_MODEL_ID)
    model = Wav2Vec2ForCTC.from_pretrained(_CTC_MODEL_ID).to(device)
    model.eval()

    # CTC log-probabilities: [time_frames, vocab_size]
    print(f"  [ctc-align] encoding audio on {device}...")
    inputs = processor(audio, sampling_rate=_TARGET_SR, return_tensors="pt", padding=True)
    with torch.no_grad():
        logits = model(inputs.input_values.to(device)).logits[0]
    log_probs = F.log_softmax(logits, dim=-1).cpu().numpy()

    # Free GPU memory before DiffSinger runs later
    del model, logits, inputs
    if device == "cuda":
        torch.cuda.empty_cache()

    # Build char_list sized to the model's ACTUAL output dimension (log_probs.shape[1]).
    # The vocab dict can have entries with indices >= model output size (e.g. special
    # tokens at high indices). Using len(vocab) instead of log_probs.shape[1] causes
    # "index N is out of bounds for axis 0 with size N" inside ctc-segmentation.
    vocab = processor.tokenizer.get_vocab()
    actual_vocab_size = log_probs.shape[1]
    char_list: list[str] = [""] * actual_vocab_size
    for char, idx in vocab.items():
        if 0 <= idx < actual_vocab_size:
            char_list[idx] = char
    # Only keep characters whose vocab index is within the model's output range —
    # characters with idx >= actual_vocab_size cannot appear in log_probs.
    vocab_set = {char for char, idx in vocab.items() if idx < actual_vocab_size}

    # Convert lyrics to hiragana, keep only characters in model vocab.
    # Kanji, romaji, and punctuation not in vocab are dropped — the aligner
    # works on the phonetic shape of the line, so partial character sequences
    # are still enough to place each line correctly in the audio timeline.
    processed_lines: list[str] = []
    for line in lyrics_lines:
        hira = _to_hiragana(line)
        cleaned = "".join(c for c in hira if c in vocab_set)
        processed_lines.append(cleaned if cleaned.strip() else " ")

    # CTC segmentation — globally optimal DP alignment
    config = ctc_segmentation.CtcSegmentationParameters(char_list=char_list)
    config.index_duration = total_duration / log_probs.shape[0]
    config.blank = 0

    # --- diagnostic ---
    print(f"  [ctc-align] log_probs.shape={log_probs.shape}  "
          f"char_list_len={len(char_list)}  blank={config.blank}")
    max_proc_idx = max(
        (vocab.get(c, -1) for line in processed_lines for c in line), default=-1
    )
    print(f"  [ctc-align] max char index in processed_lines={max_proc_idx}  "
          f"space_idx={vocab.get(' ', 'N/A')}  pipe_idx={vocab.get('|', 'N/A')}")
    # ------------------

    print(f"  [ctc-align] running CTC segmentation...")
    try:
        ground_truth_mat, utt_begin_indices = ctc_segmentation.prepare_text(
            config, processed_lines
        )
        print(f"  [ctc-align] ground_truth_mat.shape={ground_truth_mat.shape}")
    except Exception as e:
        print(f"  [ctc-align] prepare_text failed: {e}")
        return None

    try:
        timings, char_probs, _ = ctc_segmentation.ctc_segmentation(
            config, log_probs, ground_truth_mat
        )
    except Exception as e:
        print(f"  [ctc-align] ctc_segmentation failed: {e}")
        return None

    try:
        raw_segments = ctc_segmentation.determine_utterance_segments(
            config, utt_begin_indices, char_probs, timings, processed_lines
        )
    except Exception as e:
        print(f"  [ctc-align] determine_utterance_segments failed: {e}")
        return None

    # Build output — use original (non-hiragana) text so downstream SOFA
    # alignment gets the actual Japanese text to convert to phonemes
    result: list[dict] = []
    for (start, end, _conf), orig_line in zip(raw_segments, lyrics_lines):
        if end - start < 0.05:
            continue
        result.append({
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "text": orig_line,
        })

    if not result:
        print("  [ctc-align] no segments produced")
        return None

    coverage = result[-1]["end"] / total_duration if total_duration > 0 else 0
    print(f"  [ctc-align] {len(result)} segments aligned, "
          f"coverage {coverage*100:.0f}% ({result[-1]['end']:.0f}s / {total_duration:.0f}s)")
    return result
