"""
Generate targeted DiffSinger training samples using CosyVoice.

Synthesizes ~250 Japanese patterns with CosyVoice (port 9004) targeting
patterns the model struggles with: rapid repeated syllables (ないないない),
fast staccato bursts, dense syllable runs, and rap-style delivery.

After synthesis, runs SOFA force-alignment to produce phoneme timings.

NOTE: CosyVoice outputs are SPEECH, not singing. These samples teach DiffSinger
correct phoneme sequencing for difficult patterns. For best quality, mix with
the singing-only datasets (yoasobi, staging_aligned) which teach singing dynamics.

Usage (from Koroki root, using .venv_diffsinger):
    # 1. Start CosyVoice adapter first (in a separate terminal):
    .\\scripts\\easy_start_cosyvoice_adapter.ps1

    # 2. Run generation + alignment:
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\gen_patterns.py

    # 3. If WAVs already exist, skip CosyVoice synthesis:
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\gen_patterns.py --skip-synthesis

Output: data/diffsinger_raw/patterns/
    wavs/cv_pattern_XXXX.wav
    transcriptions.csv
    phonemes.txt          (copied from yoasobi dataset)
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

_SELF_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SELF_DIR.parents[1]
_SOFA_DIR  = _SELF_DIR / "SOFA"
_SOFA_CKPT = _SOFA_DIR / "checkpoints" / "japanese" / "step.100000.ckpt"
_SOFA_DICT = _SOFA_DIR / "checkpoints" / "japanese" / "japanese-extension-sofa.txt"

_OUT_DIR   = _REPO_ROOT / "data" / "diffsinger_raw" / "patterns"
_WAVS_DIR  = _OUT_DIR / "wavs"
_SOFA_TMP  = _OUT_DIR / "_sofa_tmp"  # staging folder for SOFA batch run

_COSYVOICE_URL = "http://127.0.0.1:9004/synthesize"
_TARGET_SR = 44100
_TIMEOUT = 60  # seconds per synthesis call

# SOFA romaji → DiffSinger IPA.
# "cl" (っ) → "c" matches the DiffSinger yoasobi/patterns phoneme inventory.
_SOFA_TO_IPA: dict[str, str] = {
    "a": "a",   "i": "i",   "u": "ɯ",   "e": "e",   "o": "o",
    "k": "k",   "g": "ɡ",   "s": "s",   "z": "z",
    "t": "t",   "d": "d",   "n": "n",   "h": "h",
    "b": "b",   "p": "p",   "m": "m",   "y": "j",   "r": "ɾ",
    "w": "w",   "N": "ɴ",   "cl": "c",
    "sh": "ɕ",  "ch": "tɕ", "ts": "ts",
    "f": "ɸ",   "j": "dʑ",   "v": "v",
    "ky": "k",  "gy": "ɡ",  "ny": "n",  "hy": "h",
    "my": "m",  "ry": "ɾ",  "by": "b",  "py": "p",
    "AP": "AP", "SP": "SP", "br": "AP",
    "pau": "SP", "sil": "SP", "EP": "SP", "GS": "SP",
}

# ---------------------------------------------------------------------------
# Pattern list — 250+ targeted patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[str] = [
    # ── RAPID REPETITION (primary target) ────────────────────────────────────
    # ない: DiffSinger attention collapses on rapid same-token repetition.
    # Dense coverage trains attention to stay on track.
    "ないないない",
    "ないないないない",
    "ないないないないない",
    "あれもないないない",
    "これもないないない",
    "もうないないない",
    "なんにもないないない",
    "あれもないないないこれもないないない",
    "ないよないよないよ",
    "そんなのないないない",
    "ぜんぜんないないない",
    "もうなんにもない",

    # ダメ/だめ variants
    "ダメダメダメ",
    "だめだめだめ",
    "ダメダメダメダメ",
    "だめだめだめだめ",
    "それはダメダメ",
    "もうだめだめだめ",
    "ぜったいダメダメ",
    "こんなのダメダメダメ",

    # いや variants
    "いやいやいや",
    "イヤイヤイヤ",
    "いやいやいやいや",
    "そんないやいやいや",
    "もうイヤイヤイヤ",
    "絶対イヤイヤ",

    # はい variants
    "はいはいはい",
    "はいはいはいはい",
    "はいはいはいはいはい",
    "うんうんうん",
    "うんうんうんうん",

    # その他の繰り返しパターン
    "やだやだやだ",
    "やだやだやだやだ",
    "まだまだまだ",
    "まだまだまだまだ",
    "もうもうもう",
    "もうもうもうもう",
    "ちがうちがうちがう",
    "ちがうちがうちがうちがう",
    "すきすきすき",
    "きらいきらいきらい",
    "うそうそうそ",
    "ほんとほんとほんと",
    "ねえねえねえ",
    "ねえねえねえねえ",
    "なんでなんでなんで",
    "どうしてどうしてどうして",
    "むりむりむり",
    "むりむりむりむり",
    "なんかなんかなんか",
    "しってるしってるしってる",
    "わかるわかるわかる",
    "わからないわからない",
    "やばいやばいやばい",
    "かわいいかわいいかわいい",
    "さびしいさびしいさびしい",
    "うれしいうれしいうれしい",
    "かなしいかなしいかなしい",
    "こわいこわいこわい",
    "くやしいくやしいくやしい",
    "もったいないもったいない",
    "しかたないしかたない",

    # オノマトペ系繰り返し
    "どんどんどんどん",
    "たんたんたんたん",
    "きらきらきらきら",
    "どきどきどきどき",
    "わくわくわくわく",
    "ふわふわふわふわ",
    "さらさらさらさら",
    "ぱっぱっぱっぱっ",
    "ふんふんふんふん",
    "ランランランラン",
    "たらたらたらたら",
    "ぐるぐるぐるぐる",
    "ぼーっとぼーっとぼーっと",
    "ぽかぽかぽかぽか",
    "ぴかぴかぴかぴか",

    # ── STACCATO / SHORT PUNCHY ──────────────────────────────────────────────
    "いまいまいま",
    "はやくはやくはやく",
    "きてきてきて",
    "みてみてみて",
    "おいでおいでおいで",
    "ちょっとまってまってまって",
    "そうそうそうそう",
    "ちがうちがう、そうじゃない",
    "ねえ、きいて",
    "あのねあのね",
    "へえへえへえ",
    "えっ、えっ、ほんとに？",
    "あ、そっかそっか",
    "うわあびっくりした",
    "おおすごいすごい",
    "かっこいいかっこいい",
    "さいこうさいこうさいこう",
    "たすけてたすけて",
    "まってまってまって",
    "こっちこっちこっちだよ",
    "あれあれあれ",
    "これこれこれだよ",
    "なにもかもなにもかも",
    "ぜんぜんぜんぜん",
    "まったくまったく",
    "ほんとにもうほんとに",
    "もうだめもうだめ",
    "ぜったいぜったいぜったい",
    "きっときっときっと",
    "やっぱりやっぱりやっぱり",

    # ── DENSE SYLLABLE RUNS (rap-style) ──────────────────────────────────────
    # From アイドル — the specific song patterns that broke
    "あれもないないないこれもないないない好きなタイプは",
    "知りたいその秘密ミステリアス",
    "誰もが目を奪われていく",
    "まさに最強で無敵のアイドル",
    "誰もが信じ崇めてる",
    "わけがないこれはネタじゃない",
    "からこそ許せない",
    "完璧じゃない君じゃ許せない",
    "自分を許せない",
    "誰よりも強い君以外は認めない",
    "弱いとこなんて見せちゃダメダメ",
    "唯一無二じゃなくちゃイヤイヤ",
    "それこそ本物のアイ",
    "金輪際現れない一番星の生まれ変わり",

    # Fast dense patterns
    "もうこんなにすきになってしまって",
    "きみのことがすきなのにきみはわかってくれない",
    "なんでなんでなんでなんでなんで",
    "どうしてどうしてどうしてこんなに",
    "あいしてあいしてあいしてあいして",
    "ねえきいてよきいてよきいてよ",
    "わかってるわかってるけどでもでも",
    "そんなことわかってるのに",
    "なにもかもなにもかもなにもかも",
    "まだまだまだまだあきらめてない",
    "すきだよすきだよすきだよきみが",
    "どんなにどんなに願っても届かない",
    "ずっとずっとずっとそばにいて",
    "この気持ちを伝えたいけど言えない",
    "言葉が見つからない見つからない",
    "会いたい会いたい会いたいよ",
    "忘れられない忘れられない",
    "思い出すたびに胸が痛くなる",
    "泣きたくないのに涙が出てくる",
    "いつでもいつでもいつでも思い出す",
    "もう二度と会えないのかなって",
    "それでもそれでもそれでも好きだよ",

    # Fast triple-repeat + continuation
    "なんでなんでなんでこんなに苦しいの",
    "どうしてどうしてどうして好きなのに",
    "だれかだれかだれか助けてよ",
    "ねえねえねえちゃんと聞いてよ",

    # ── YOASOBI SONG PHRASES ─────────────────────────────────────────────────
    # アイドル
    "無敵の笑顔で荒らすメディア",
    "天才的なアイドル様",
    "完璧で嘘つきな君は",
    "君は完璧で究極のアイドル",
    "その笑顔で愛してるで誰も彼も虜にしていく",
    "嘘でもそれは完全なアイ",
    "得意の笑顔で沸かすメディア",
    "愛してるって嘘で積むキャリア",
    "そう嘘はとびきりの愛だ",
    "これは絶対嘘じゃない愛してる",
    "はいはいあの子は特別です",
    "誰もが目を奪われていく",
    "また好きにさせる",
    "見えそうで見えない秘密は蜜の味",

    # 夜に駆ける
    "君が笑えばいいと思ってた",
    "ただ側にいたいと願うだけで",
    "こんな世界嫌だと泣いた夜もある",
    "さよならだけが唯一の言葉で",

    # ハルジオン
    "君を思い出せなくなる前に",
    "花のように散ってしまえたら",
    "会いに行くよひとりで",
    "生きていくのはつらいよと",

    # 怪物
    "うまくいかない日々も続けてきたから",
    "正しいとか正しくないとかじゃなくて",
    "怖くて怖くて怖くて",
    "それでも前に進もうとした",

    # 群青
    "好きなものを好きだと言える",
    "飛び出してみよう青い空に",
    "あなたとなら何でもできそう",

    # ── GENERAL J-POP MELODIC ─────────────────────────────────────────────────
    "あなたのことが好きです",
    "きみのことを忘れないよ",
    "ずっとそばにいてほしい",
    "毎日毎日同じことの繰り返し",
    "何も変わらない日々が続く",
    "きっとうまくいくよ信じてれば",
    "夢を諦めないで前に進もう",
    "悲しいけど笑えるよ",
    "涙をこらえて笑った",
    "会いたくて会いたくて震える",
    "こんな気持ちになったのは初めて",
    "時間よ止まれと思った",
    "君の笑顔が見たくて",
    "春の風が吹いている",
    "夏の夜に星を見た",
    "秋の空は高いな",
    "桜が散ってしまう前に",
    "あの日のことを覚えてる",
    "もう戻れないとわかってても",
    "大丈夫きっとなんとかなる",
    "ひとりじゃないよって言って",
    "そばにいるから安心して",
    "何があっても一緒にいるよ",
    "君が泣いてるならぼくも泣くよ",
    "笑顔でいてほしいただそれだけ",
    "幸せになってほしいな",
    "また明日ね、さよなら",
    "おかえり、ただいま",
    "ここにいるよここにいるよ",
    "消えないでここにいて",
    "好きって言えばよかった",
    "あの時に戻れるなら",
    "もう一度だけ会いたい",
    "ありがとうって言えなかった",
    "ごめんなさいって言えなかった",

    # ── VOWEL / SUSTAINED ─────────────────────────────────────────────────────
    "ああああああ",
    "うーんもう少し",
    "えーっとね",
    "あーそういうことか",
    "うわあびっくりした",
    "おおそうかそうか",
    "いーよいーよ",
    "そーかそーか",
]


# ---------------------------------------------------------------------------
# CosyVoice synthesis
# ---------------------------------------------------------------------------

def _synthesize(text: str, out_path: Path) -> bool:
    try:
        resp = requests.post(
            _COSYVOICE_URL,
            json={"text": text, "emotion": "neutral", "relationship_score": 50},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        wav_bytes = base64.b64decode(resp.json()["wav_base64"])
        audio, sr = sf.read(io.BytesIO(wav_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)
        if sr != _TARGET_SR:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(sr, _TARGET_SR)
            audio = resample_poly(audio, _TARGET_SR // g, sr // g)
        sf.write(str(out_path), audio, _TARGET_SR, subtype="PCM_16")
        return True
    except Exception as e:
        print(f"  WARN: synthesis failed for '{text[:40]}': {e}")
        return False


# ---------------------------------------------------------------------------
# TextGrid parsing
# ---------------------------------------------------------------------------

def _parse_textgrid(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tier_blocks = re.split(r'item\s*\[\d+\]\s*:', text)
    for block in tier_blocks:
        if 'name = "phones"' not in block:
            continue
        intervals = []
        for ib in re.split(r'intervals\s*\[\d+\]\s*:', block)[1:]:
            xm = re.search(r'xmin\s*=\s*([\d.eE+\-]+)', ib)
            xM = re.search(r'xmax\s*=\s*([\d.eE+\-]+)', ib)
            tm = re.search(r'text\s*=\s*"([^"]*)"', ib)
            if xm and xM and tm:
                label = tm.group(1).strip()
                intervals.append((float(xm.group(1)), float(xM.group(1)), label))
        return intervals
    return []


def _textgrid_to_row(name: str, tg_path: Path) -> tuple[str, str] | None:
    intervals = _parse_textgrid(tg_path)
    if not intervals:
        return None

    ph_seq, ph_dur = [], []
    for xmin, xmax, label in intervals:
        if xmax <= xmin:
            continue
        dur = round(xmax - xmin, 4)
        ipa = _SOFA_TO_IPA.get(label, label)
        if not ipa:
            continue
        ph_seq.append(ipa)
        ph_dur.append(str(dur))

    if not ph_seq:
        return None
    return " ".join(ph_seq), " ".join(ph_dur)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-synthesis", action="store_true",
                        help="Skip CosyVoice synthesis — use existing WAVs in output dir")
    parser.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = parser.parse_args()

    out_dir  = args.out_dir
    wavs_dir = out_dir / "wavs"
    sofa_tmp = out_dir / "_sofa_tmp"
    wavs_dir.mkdir(parents=True, exist_ok=True)
    sofa_tmp.mkdir(parents=True, exist_ok=True)
    (sofa_tmp / "TextGrid").mkdir(exist_ok=True)

    if not _SOFA_CKPT.exists():
        sys.exit(f"SOFA checkpoint not found: {_SOFA_CKPT}")
    if not _SOFA_DICT.exists():
        sys.exit(f"SOFA dictionary not found: {_SOFA_DICT}")

    # Load kana→SOFA lab converter from sofa_runner (same directory)
    sys.path.insert(0, str(_SELF_DIR))
    from sofa_runner import _kana_to_sofa_lab  # type: ignore

    patterns = _PATTERNS
    print(f"Patterns to generate: {len(patterns)}")

    # ── Step 1: Synthesize ──────────────────────────────────────────────────
    if not args.skip_synthesis:
        print("\n[1/3] Synthesizing with CosyVoice...")
        ok = fail = skip = 0
        for idx, text in enumerate(patterns):
            name    = f"cv_pattern_{idx:04d}"
            wav_out = wavs_dir / f"{name}.wav"
            if wav_out.exists() and wav_out.stat().st_size > 0:
                skip += 1
                continue
            success = _synthesize(text, wav_out)
            if success:
                ok += 1
                print(f"  [{idx+1}/{len(patterns)}] OK  {name}: {text[:50]}")
            else:
                fail += 1
            time.sleep(0.1)  # brief pause to avoid overwhelming CosyVoice
        print(f"  Synthesized: {ok} OK, {fail} failed, {skip} skipped")
    else:
        print("\n[1/3] Skipping synthesis (--skip-synthesis)")

    # ── Step 2: Build SOFA batch folder ─────────────────────────────────────
    print("\n[2/3] Preparing SOFA alignment...")
    prepared = 0
    for idx, text in enumerate(patterns):
        name    = f"cv_pattern_{idx:04d}"
        wav_src = wavs_dir / f"{name}.wav"
        if not wav_src.exists():
            continue

        lab_text = _kana_to_sofa_lab(text)
        if not lab_text.strip():
            print(f"  WARN: empty lab for '{text[:40]}' — skipping")
            continue

        # SOFA expects WAV + matching .lab in the same folder
        shutil.copy2(wav_src, sofa_tmp / f"{name}.wav")
        (sofa_tmp / f"{name}.lab").write_text(lab_text, encoding="utf-8")
        prepared += 1

    print(f"  Prepared {prepared} clips for SOFA")

    if prepared == 0:
        sys.exit("No WAVs to align — check synthesis step")

    # Run SOFA batch inference
    print("  Running SOFA batch alignment...")
    cmd = [
        sys.executable, str(_SOFA_DIR / "infer.py"),
        "--ckpt",    str(_SOFA_CKPT),
        "--folder",  str(sofa_tmp),
        "--dictionary", str(_SOFA_DICT),
        "--out_formats", "TextGrid",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=str(_SOFA_DIR), timeout=1800)
    for line in result.stderr.splitlines():
        if line.strip() and "warning" not in line.lower():
            print(f"  [sofa] {line.strip()}")
    if result.returncode != 0:
        sys.exit(f"SOFA failed:\n{result.stderr[-600:]}")

    # ── Step 3: Parse TextGrids → transcriptions.csv ─────────────────────────
    print("\n[3/3] Building transcriptions.csv...")
    rows = [("name", "ph_seq", "ph_dur")]
    missing = ok = 0
    tg_dir = sofa_tmp / "TextGrid"

    for idx, text in enumerate(patterns):
        name   = f"cv_pattern_{idx:04d}"
        tg_path = tg_dir / f"{name}.TextGrid"
        if not tg_path.exists():
            # SOFA sometimes writes without leading zeros or with variant names
            candidates = sorted(tg_dir.glob(f"{name}*.TextGrid"))
            if candidates:
                tg_path = candidates[0]
            else:
                missing += 1
                continue

        result = _textgrid_to_row(name, tg_path)
        if result is None:
            missing += 1
            continue

        ph_seq, ph_dur = result
        rows.append((name, ph_seq, ph_dur))
        ok += 1

    csv_path = out_dir / "transcriptions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"  Wrote {ok} rows to {csv_path} ({missing} missing TextGrids)")

    # Copy phonemes.txt from yoasobi dataset (same phoneme set)
    ph_src = _REPO_ROOT / "data" / "diffsinger_raw" / "yoasobi" / "phonemes.txt"
    ph_dst = out_dir / "phonemes.txt"
    if ph_src.exists():
        shutil.copy2(ph_src, ph_dst)
        print(f"  Copied phonemes.txt from yoasobi dataset")
    else:
        print(f"  WARN: phonemes.txt not found at {ph_src} — create it manually")

    print(f"\nDone. Dataset written to: {out_dir}")
    print("Next steps:")
    print("  1. Binarize:  python DiffSinger/scripts/binarize.py --config DiffSinger/configs/koroki_v2.yaml")
    print("  2. Train:     python DiffSinger/scripts/train.py acoustic --config DiffSinger/configs/koroki_v2.yaml --exp koroki_v2")


if __name__ == "__main__":
    main()
