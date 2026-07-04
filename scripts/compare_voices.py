"""
Voice similarity experiment — Koroki vs Ikura (YOASOBI).

Goal: answer "are they actually similar mathematically, or just to my ear?"

Method:
  1. Speaker embeddings (Resemblyzer) — fixed-length vectors that capture
     speaker identity independently of what's said. Cosine similarity tells
     us "do these voices belong to the same person, mathematically."
  2. F0 distribution comparison — where does each voice's pitch naturally sit?
  3. Spectrogram side-by-side — visual sanity check.

Baselines we include so the number is INTERPRETABLE:
  - Koroki vs herself across samples → self-similarity baseline (upper bound)
  - Ikura vs herself across songs → self-similarity baseline
  - Koroki vs Ikura → the answer

Interpretation guide for Resemblyzer cosine similarity:
  - > 0.85 → almost certainly same speaker
  - 0.75 - 0.85 → very similar (could pass for same speaker)
  - 0.65 - 0.75 → similar timbre but distinguishable
  - 0.50 - 0.65 → similar voice type (e.g. both young female) but distinct
  - < 0.50 → different speakers / different voice types

Run from Koroki root:
    .venv\\Scripts\\python.exe scripts\\compare_voices.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import librosa
import matplotlib.pyplot as plt
from resemblyzer import VoiceEncoder, preprocess_wav

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "voice_analysis"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Sample selection ─────────────────────────────────────────────────────
# Koroki references — only the unnumbered EN_sample (per user note: the
# numbered EN samples are same person but very different tones, so they'd
# contaminate the average). JP_sample1 included because Ikura sings in
# Japanese and language affects formants — JP-vs-JP is most acoustically fair.
KOROKI_SAMPLES = [
    REPO_ROOT / "voice_samples" / "EN_sample.wav",
    REPO_ROOT / "voice_samples" / "JP_sample1.wav",
]

# Ikura singing — random sample of YOASOBI segments to average out
# song-specific quirks.
def select_ikura_samples(n: int = 8) -> list[Path]:
    yoasobi_dir = REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi" / "wavs"
    all_wavs = sorted(yoasobi_dir.glob("*.wav"))
    # Stride through the corpus to get variety across songs
    step = max(1, len(all_wavs) // n)
    return all_wavs[::step][:n]


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed(encoder: VoiceEncoder, wav_path: Path) -> np.ndarray:
    """Resemblyzer embedding: 256-dim vector representing speaker identity."""
    wav = preprocess_wav(str(wav_path))
    return encoder.embed_utterance(wav)


def pitch_stats(wav_path: Path) -> tuple[float, float, np.ndarray]:
    """Return (median Hz, std Hz, F0 contour Hz)."""
    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    # Use pyin for clean F0
    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=80, fmax=600, sr=sr, frame_length=2048,
    )
    f0_clean = f0[voiced_flag & ~np.isnan(f0)]
    if len(f0_clean) == 0:
        return 0.0, 0.0, np.array([])
    return float(np.median(f0_clean)), float(np.std(f0_clean)), f0_clean


def main() -> None:
    print("=" * 75)
    print("VOICE SIMILARITY EXPERIMENT — Koroki vs Ikura (YOASOBI)")
    print("=" * 75)

    # Verify samples exist
    for p in KOROKI_SAMPLES:
        if not p.exists():
            print(f"MISSING: {p}")
            sys.exit(1)

    ikura_samples = select_ikura_samples(n=8)
    if not ikura_samples:
        print("MISSING: no YOASOBI samples found")
        sys.exit(1)

    print(f"\nKoroki samples ({len(KOROKI_SAMPLES)}):")
    for p in KOROKI_SAMPLES:
        print(f"  {p.name}")
    print(f"\nIkura samples ({len(ikura_samples)}):")
    for p in ikura_samples:
        print(f"  {p.name}")

    # ── Step 1: speaker embeddings ──
    print("\n" + "=" * 75)
    print("STEP 1: Computing speaker embeddings (Resemblyzer)")
    print("=" * 75)
    encoder = VoiceEncoder()

    koroki_embs = {}
    for p in KOROKI_SAMPLES:
        koroki_embs[p.name] = embed(encoder, p)
        print(f"  embedded: {p.name}")

    ikura_embs = {}
    for p in ikura_samples:
        ikura_embs[p.name] = embed(encoder, p)
        print(f"  embedded: {p.name}")

    # ── Step 2: similarity calculations ──
    print("\n" + "=" * 75)
    print("STEP 2: Cosine similarity analysis")
    print("=" * 75)

    # Average embeddings — the "centroid" voice fingerprint of each speaker
    koroki_centroid = np.mean(list(koroki_embs.values()), axis=0)
    ikura_centroid = np.mean(list(ikura_embs.values()), axis=0)

    # Baseline: Koroki sample-vs-sample (within-speaker similarity)
    koroki_names = list(koroki_embs.keys())
    koroki_self_sims = []
    for i in range(len(koroki_names)):
        for j in range(i + 1, len(koroki_names)):
            s = cosine_sim(koroki_embs[koroki_names[i]], koroki_embs[koroki_names[j]])
            koroki_self_sims.append(s)
            print(f"  Koroki self: {koroki_names[i]} vs {koroki_names[j]} = {s:.3f}")

    # Baseline: Ikura sample-vs-sample
    ikura_names = list(ikura_embs.keys())
    ikura_self_sims = []
    for i in range(len(ikura_names)):
        for j in range(i + 1, len(ikura_names)):
            s = cosine_sim(ikura_embs[ikura_names[i]], ikura_embs[ikura_names[j]])
            ikura_self_sims.append(s)

    # The answer: Koroki vs Ikura (every pairing)
    cross_sims = []
    for kn, ke in koroki_embs.items():
        for inm, ie in ikura_embs.items():
            s = cosine_sim(ke, ie)
            cross_sims.append((kn, inm, s))

    # ── Step 3: report ──
    print("\n" + "=" * 75)
    print("RESULTS")
    print("=" * 75)

    print("\nBASELINES (so the answer below has context):")
    if koroki_self_sims:
        print(f"  Koroki vs Koroki (within-speaker): mean={np.mean(koroki_self_sims):.3f}  "
              f"min={np.min(koroki_self_sims):.3f}  max={np.max(koroki_self_sims):.3f}")
    print(f"  Ikura vs Ikura (within-speaker): mean={np.mean(ikura_self_sims):.3f}  "
          f"min={np.min(ikura_self_sims):.3f}  max={np.max(ikura_self_sims):.3f}")

    print("\n>>> KOROKI vs IKURA <<<")
    cross_scores = [s for _, _, s in cross_sims]
    print(f"  mean similarity: {np.mean(cross_scores):.3f}")
    print(f"  min:             {np.min(cross_scores):.3f}")
    print(f"  max:             {np.max(cross_scores):.3f}")
    print(f"  centroid-vs-centroid: {cosine_sim(koroki_centroid, ikura_centroid):.3f}")

    # Per-Koroki-sample breakdown
    print("\nPer Koroki sample (each averaged over all Ikura samples):")
    for kn in koroki_embs:
        kn_sims = [s for k, _, s in cross_sims if k == kn]
        print(f"  {kn}: mean={np.mean(kn_sims):.3f}")

    # Interpretation
    mean_cross = float(np.mean(cross_scores))
    print("\nINTERPRETATION:")
    if mean_cross > 0.85:
        verdict = "ALMOST CERTAINLY SAME SPEAKER (>0.85) — voices are nearly identical"
    elif mean_cross > 0.75:
        verdict = "VERY SIMILAR (0.75-0.85) — could pass for same speaker in many contexts"
    elif mean_cross > 0.65:
        verdict = "SIMILAR TIMBRE (0.65-0.75) — clearly related voice type but distinguishable"
    elif mean_cross > 0.50:
        verdict = "SIMILAR VOICE TYPE (0.50-0.65) — same category (e.g. young female) but distinct identities"
    else:
        verdict = f"DIFFERENT VOICES ({mean_cross:.2f}) — your ear is being optimistic"
    print(f"  {verdict}")

    # Caveat about speech vs singing
    print("\nCAVEAT: We're comparing Koroki speech vs Ikura SINGING. Singing")
    print("changes formants and pitch contour, so this is a conservative lower")
    print("bound — the real speech-vs-speech similarity is likely HIGHER.")

    # ── Step 4: F0 distribution ──
    print("\n" + "=" * 75)
    print("STEP 3: F0 (pitch) distribution analysis")
    print("=" * 75)
    print("(Where each voice naturally sits — relevant for the 'transpose, don't SVC' idea)")

    koroki_f0s = []
    for p in KOROKI_SAMPLES:
        med, std, contour = pitch_stats(p)
        print(f"  {p.name}: median={med:.1f} Hz, std={std:.1f} Hz")
        koroki_f0s.append(contour)

    ikura_f0s = []
    for p in ikura_samples[:4]:  # sample for speed
        med, std, contour = pitch_stats(p)
        print(f"  {p.name}: median={med:.1f} Hz, std={std:.1f} Hz")
        ikura_f0s.append(contour)

    koroki_all_f0 = np.concatenate(koroki_f0s) if koroki_f0s else np.array([])
    ikura_all_f0 = np.concatenate(ikura_f0s) if ikura_f0s else np.array([])

    print(f"\nKoroki pooled: median={np.median(koroki_all_f0):.1f} Hz "
          f"(IQR {np.percentile(koroki_all_f0, 25):.0f}-{np.percentile(koroki_all_f0, 75):.0f})")
    print(f"Ikura  pooled: median={np.median(ikura_all_f0):.1f} Hz "
          f"(IQR {np.percentile(ikura_all_f0, 25):.0f}-{np.percentile(ikura_all_f0, 75):.0f})")

    semitone_shift = 12 * np.log2(np.median(ikura_all_f0) / np.median(koroki_all_f0))
    print(f"Semitone shift to align medians: {semitone_shift:+.2f} semitones "
          f"(Ikura is {'above' if semitone_shift > 0 else 'below'} Koroki)")

    # ── Step 5: F0 distribution plot ──
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hist(koroki_all_f0, bins=50, alpha=0.6, label="Koroki", color="C0", density=True)
    axes[0].hist(ikura_all_f0, bins=50, alpha=0.6, label="Ikura", color="C1", density=True)
    axes[0].set_xlabel("F0 (Hz)")
    axes[0].set_ylabel("density")
    axes[0].set_title("F0 distribution — where each voice naturally sits")
    axes[0].legend()
    axes[0].set_xlim(80, 600)

    # Cross-similarity heatmap
    sim_matrix = np.zeros((len(koroki_embs), len(ikura_embs)))
    for i, ke in enumerate(koroki_embs.values()):
        for j, ie in enumerate(ikura_embs.values()):
            sim_matrix[i, j] = cosine_sim(ke, ie)
    im = axes[1].imshow(sim_matrix, cmap="RdYlGn", vmin=0.4, vmax=1.0, aspect="auto")
    axes[1].set_xticks(range(len(ikura_embs)))
    axes[1].set_xticklabels([n[-12:] for n in ikura_embs.keys()], rotation=45, ha="right", fontsize=7)
    axes[1].set_yticks(range(len(koroki_embs)))
    axes[1].set_yticklabels(list(koroki_embs.keys()))
    axes[1].set_title("Speaker similarity heatmap (cosine)")
    plt.colorbar(im, ax=axes[1])

    plt.tight_layout()
    plot_path = OUT_DIR / "koroki_vs_ikura.png"
    plt.savefig(plot_path, dpi=120, bbox_inches="tight")
    print(f"\nPlot saved: {plot_path}")

    print("\n" + "=" * 75)
    print("DONE")
    print("=" * 75)


if __name__ == "__main__":
    main()
