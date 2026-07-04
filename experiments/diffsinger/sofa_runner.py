"""
Thin CLI wrapper around SOFA (Singing-Oriented Forced Aligner).
Called by sing_song.py via .venv_diffsinger python.
Prints the output TextGrid path as the last line of stdout.

The SOFA Japanese dictionary expects mora-level Romaji tokens in the .lab file
(e.g., "ka no jo wa" for かのじょは). This script converts kana/kanji input to
that format using pykakasi before calling SOFA.

Setup (one-time):
    1. git clone https://github.com/qiuqiao/SOFA experiments/diffsinger/SOFA
    2. .venv_diffsinger\\Scripts\\pip install torch torchaudio librosa einops pykakasi
    3. Download into experiments/diffsinger/SOFA/checkpoints/japanese/:
         step.100000.ckpt           (51 MB)
         japanese-extension-sofa.txt
         hparams.yaml
       from: https://github.com/Greenleaf2001/SOFA_Models/releases/tag/JPN_Test2_Plus
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_SELF_DIR = Path(__file__).parent
_SOFA_DIR = _SELF_DIR / "SOFA"
_CKPT = _SOFA_DIR / "checkpoints" / "japanese" / "step.100000.ckpt"
_DICT = _SOFA_DIR / "checkpoints" / "japanese" / "japanese-extension-sofa.txt"

# ---------------------------------------------------------------------------
# Kana → SOFA romaji mora converter
# ---------------------------------------------------------------------------

# Digraphs must be checked before single chars (longest match first)
_DIGRAPH: dict[str, str] = {
    # きゃ行
    'きゃ': 'kya', 'きぃ': 'kyi', 'きゅ': 'kyu', 'きぇ': 'kye', 'きょ': 'kyo',
    'キャ': 'kya', 'キィ': 'kyi', 'キュ': 'kyu', 'キェ': 'kye', 'キョ': 'kyo',
    # くぁ行
    'くぁ': 'kwa', 'くぃ': 'kwi', 'くぅ': 'kwu', 'くぇ': 'kwe', 'くぉ': 'kwo',
    'クァ': 'kwa', 'クィ': 'kwi', 'クゥ': 'kwu', 'クェ': 'kwe', 'クォ': 'kwo',
    # ぎゃ行
    'ぎゃ': 'gya', 'ぎぃ': 'gyi', 'ぎゅ': 'gyu', 'ぎぇ': 'gye', 'ぎょ': 'gyo',
    'ギャ': 'gya', 'ギィ': 'gyi', 'ギュ': 'gyu', 'ギェ': 'gye', 'ギョ': 'gyo',
    # ぐぁ行
    'ぐぁ': 'gwa', 'ぐぃ': 'gwi', 'ぐぅ': 'gwu', 'ぐぇ': 'gwe', 'ぐぉ': 'gwo',
    'グァ': 'gwa', 'グィ': 'gwi', 'グゥ': 'gwu', 'グェ': 'gwe', 'グォ': 'gwo',
    # しゃ行
    'しゃ': 'sha', 'しぃ': 'shi', 'しゅ': 'shu', 'しぇ': 'she', 'しょ': 'sho',
    'シャ': 'sha', 'シィ': 'shi', 'シュ': 'shu', 'シェ': 'she', 'ショ': 'sho',
    # じゃ行
    'じゃ': 'ja',  'じぃ': 'ji',  'じゅ': 'ju',  'じぇ': 'je',  'じょ': 'jo',
    'ジャ': 'ja',  'ジィ': 'ji',  'ジュ': 'ju',  'ジェ': 'je',  'ジョ': 'jo',
    # ちゃ行
    'ちゃ': 'cha', 'ちぃ': 'chi', 'ちゅ': 'chu', 'ちぇ': 'che', 'ちょ': 'cho',
    'チャ': 'cha', 'チィ': 'chi', 'チュ': 'chu', 'チェ': 'che', 'チョ': 'cho',
    # つぁ行
    'つぁ': 'tsa', 'つぃ': 'tsi', 'つぇ': 'tse', 'つぉ': 'tso',
    'ツァ': 'tsa', 'ツィ': 'tsi', 'ツェ': 'tse', 'ツォ': 'tso',
    # てぃ行
    'てぃ': 'ti', 'てゅ': 'tyu',
    'ティ': 'ti', 'テュ': 'tyu',
    # とぅ
    'とぅ': 'tu',
    'トゥ': 'tu',
    # にゃ行
    'にゃ': 'nya', 'にぃ': 'nyi', 'にゅ': 'nyu', 'にぇ': 'nye', 'にょ': 'nyo',
    'ニャ': 'nya', 'ニィ': 'nyi', 'ニュ': 'nyu', 'ニェ': 'nye', 'ニョ': 'nyo',
    # ひゃ行
    'ひゃ': 'hya', 'ひぃ': 'hyi', 'ひゅ': 'hyu', 'ひぇ': 'hye', 'ひょ': 'hyo',
    'ヒャ': 'hya', 'ヒィ': 'hyi', 'ヒュ': 'hyu', 'ヒェ': 'hye', 'ヒョ': 'hyo',
    # ふぁ行
    'ふぁ': 'fa',  'ふぃ': 'fi',  'ふぅ': 'fu',  'ふぇ': 'fe',  'ふぉ': 'fo',
    'ファ': 'fa',  'フィ': 'fi',  'フゥ': 'fu',  'フェ': 'fe',  'フォ': 'fo',
    # みゃ行
    'みゃ': 'mya', 'みぃ': 'myi', 'みゅ': 'myu', 'みぇ': 'mye', 'みょ': 'myo',
    'ミャ': 'mya', 'ミィ': 'myi', 'ミュ': 'myu', 'ミェ': 'mye', 'ミョ': 'myo',
    # りゃ行
    'りゃ': 'rya', 'りぃ': 'ryi', 'りゅ': 'ryu', 'りぇ': 'rye', 'りょ': 'ryo',
    'リャ': 'rya', 'リィ': 'ryi', 'リュ': 'ryu', 'リェ': 'rye', 'リョ': 'ryo',
    # びゃ行
    'びゃ': 'bya', 'びぃ': 'byi', 'びゅ': 'byu', 'びぇ': 'bye', 'びょ': 'byo',
    'ビャ': 'bya', 'ビィ': 'byi', 'ビュ': 'byu', 'ビェ': 'bye', 'ビョ': 'byo',
    # ぴゃ行
    'ぴゃ': 'pya', 'ぴぃ': 'pyi', 'ぴゅ': 'pyu', 'ぴぇ': 'pye', 'ぴょ': 'pyo',
    'ピャ': 'pya', 'ピィ': 'pyi', 'ピュ': 'pyu', 'ピェ': 'pye', 'ピョ': 'pyo',
    # でぃ行
    'でぃ': 'di', 'でゅ': 'dyu',
    'ディ': 'di', 'デュ': 'dyu',
    # どぅ
    'どぅ': 'du',
    'ドゥ': 'du',
    # ゔぁ行 (labiodental v)
    'ヴぁ': 'va', 'ヴぃ': 'vi', 'ヴぇ': 've', 'ヴぉ': 'vo',
    'ヴァ': 'va', 'ヴィ': 'vi', 'ヴェ': 've', 'ヴォ': 'vo',
}

_SINGLE: dict[str, str | None] = {
    # Hiragana vowels
    'あ': 'a',  'い': 'i',  'う': 'u',  'え': 'e',  'お': 'o',
    # Katakana vowels
    'ア': 'a',  'イ': 'i',  'ウ': 'u',  'エ': 'e',  'オ': 'o',
    # か行
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'カ': 'ka', 'キ': 'ki', 'ク': 'ku', 'ケ': 'ke', 'コ': 'ko',
    # が行
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ガ': 'ga', 'ギ': 'gi', 'グ': 'gu', 'ゲ': 'ge', 'ゴ': 'go',
    # さ行
    'さ': 'sa', 'し': 'shi','す': 'su', 'せ': 'se', 'そ': 'so',
    'サ': 'sa', 'シ': 'shi','ス': 'su', 'セ': 'se', 'ソ': 'so',
    # ざ行
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'ザ': 'za', 'ジ': 'ji', 'ズ': 'zu', 'ゼ': 'ze', 'ゾ': 'zo',
    # た行
    'た': 'ta', 'ち': 'chi','つ': 'tsu','て': 'te', 'と': 'to',
    'タ': 'ta', 'チ': 'chi','ツ': 'tsu','テ': 'te', 'ト': 'to',
    # だ行
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ダ': 'da', 'ヂ': 'ji', 'ヅ': 'zu', 'デ': 'de', 'ド': 'do',
    # な行
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'ナ': 'na', 'ニ': 'ni', 'ヌ': 'nu', 'ネ': 'ne', 'ノ': 'no',
    # は行
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ハ': 'ha', 'ヒ': 'hi', 'フ': 'fu', 'ヘ': 'he', 'ホ': 'ho',
    # ば行
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'バ': 'ba', 'ビ': 'bi', 'ブ': 'bu', 'ベ': 'be', 'ボ': 'bo',
    # ぱ行
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'パ': 'pa', 'ピ': 'pi', 'プ': 'pu', 'ペ': 'pe', 'ポ': 'po',
    # ま行
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'マ': 'ma', 'ミ': 'mi', 'ム': 'mu', 'メ': 'me', 'モ': 'mo',
    # や行
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ヤ': 'ya', 'ユ': 'yu', 'ヨ': 'yo',
    # ら行
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'ラ': 'ra', 'リ': 'ri', 'ル': 'ru', 'レ': 're', 'ロ': 'ro',
    # わ行
    'わ': 'wa', 'ゐ': 'wi', 'ゑ': 'we', 'を': 'wo',
    'ワ': 'wa', 'ヰ': 'wi', 'ヱ': 'we', 'ヲ': 'wo',
    'ゔ': 'vu', 'ヴ': 'vu',
    # special
    'ん': 'N',  'ン': 'N',
    'っ': 'cl', 'ッ': 'cl',
    'ー': None,  # long vowel — handled below (repeat prev vowel)
    '〜': None,  # wave dash used as long vowel in lyrics
    # small vowels (standalone use in loan words)
    'ぁ': 'a',  'ぃ': 'i',  'ぅ': 'u',  'ぇ': 'e',  'ぉ': 'o',
    'ァ': 'a',  'ィ': 'i',  'ゥ': 'u',  'ェ': 'e',  'ォ': 'o',
}

# Characters that are silently skipped (punctuation, spaces, etc.)
_SKIP = set(' \t\n　。、！？…「」『』（）・—–―""''')


def _kana_to_sofa_lab(text: str) -> str:
    """
    Convert a Japanese kana/kanji string to a space-separated SOFA romaji token sequence.
    Kanji is first converted to hiragana via pykakasi.
    Returns a string like: "a i shi te ru yo".
    """
    from pykakasi import kakasi
    kks = kakasi()

    # pykakasi converts each chunk, giving us the hiragana reading
    kana_text = "".join(item["hira"] for item in kks.convert(text))

    tokens: list[str] = []
    prev_vowel = "a"
    i = 0
    while i < len(kana_text):
        ch2 = kana_text[i:i + 2]
        if ch2 in _DIGRAPH:
            mora = _DIGRAPH[ch2]
            tokens.append(mora)
            prev_vowel = mora[-1]
            i += 2
            continue
        ch = kana_text[i]
        if ch in _SINGLE:
            mora = _SINGLE[ch]
            if mora is None:
                # Long vowel marker — repeat the previous vowel
                tokens.append(prev_vowel)
            else:
                tokens.append(mora)
                if ch not in ('ん', 'ン', 'っ', 'ッ'):
                    prev_vowel = mora[-1]
            i += 1
            continue
        if ch in _SKIP:
            i += 1
            continue
        # Unknown character (e.g. ASCII from failed kanji conversion) — skip
        i += 1

    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import json as _json
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", help="Input vocals WAV file")
    parser.add_argument("--transcript", required=True, help="Japanese lyrics (kana/kanji)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--segments-json", default=None,
        help='JSON array of {"start", "end", "text"} dicts from Whisper — '
             'used to insert SP tokens at phrase boundaries in the lab file',
    )
    args = parser.parse_args()

    if not _SOFA_DIR.exists():
        sys.exit(
            f"SOFA not found at {_SOFA_DIR}\n"
            "Clone it: git clone https://github.com/qiuqiao/SOFA experiments/diffsinger/SOFA"
        )
    if not _CKPT.exists():
        sys.exit(
            f"SOFA checkpoint not found: {_CKPT}\n"
            "Download from: https://github.com/Greenleaf2001/SOFA_Models/releases/tag/JPN_Test2_Plus"
        )
    if not _DICT.exists():
        sys.exit(f"SOFA dictionary not found: {_DICT}")

    # Build the lab content: if Whisper segment info is available, insert SP tokens
    # between segments wherever there is a meaningful gap in the audio.  Without this,
    # SOFA has no phrase-boundary information and stretches the final vowel of each
    # phrase to fill the silence, producing unnatural long holds.
    _SP_GAP = 0.0  # always insert SP at every Whisper segment boundary
    if args.segments_json:
        segs = _json.loads(args.segments_json)
        parts: list[str] = []
        prev_end: float | None = None
        for seg in segs:
            text = seg["text"].strip()
            if not text:
                prev_end = seg["end"]
                continue
            lab = _kana_to_sofa_lab(text)
            if lab.strip():
                # Only insert SP boundary if this segment has phoneme content.
                # Inserting SP for English/empty-lab segments would produce an
                # all-SP lab when all lines are non-Japanese, blocking the
                # transcript fallback below.
                if prev_end is not None and seg["start"] - prev_end >= _SP_GAP:
                    parts.append("SP")
                parts.append(lab.strip())
            prev_end = seg["end"]
        lab_text = " ".join(parts) if parts else _kana_to_sofa_lab(args.transcript)
    else:
        lab_text = _kana_to_sofa_lab(args.transcript)

    if not lab_text.strip():
        sys.exit("Kana→romaji conversion produced empty output — check transcript")
    print(f"  SOFA lab: {lab_text[:100]}{'...' if len(lab_text) > 100 else ''}", file=sys.stderr)

    # Build the segments folder SOFA expects
    segments_dir = Path(args.output_dir) / "sofa_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.wav, str(segments_dir / "song.wav"))
    (segments_dir / "song.lab").write_text(lab_text, encoding="utf-8")

    # Run SOFA infer.py
    cmd = [
        sys.executable, str(_SOFA_DIR / "infer.py"),
        "--ckpt", str(_CKPT),
        "--folder", str(segments_dir),
        "--dictionary", str(_DICT),
        "--out_formats", "TextGrid",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_SOFA_DIR))
    if result.returncode != 0:
        sys.exit(
            f"SOFA inference failed (exit {result.returncode}):\n"
            f"{result.stdout[-300:]}\n{result.stderr[-600:]}"
        )

    # SOFA writes TextGrid/ next to the input wav
    tg = segments_dir / "TextGrid" / "song.TextGrid"
    if not tg.exists():
        candidates = sorted(segments_dir.rglob("*.TextGrid"))
        if not candidates:
            sys.exit(
                f"SOFA produced no TextGrid.\nstdout: {result.stdout[-400:]}\nstderr: {result.stderr[-400:]}"
            )
        tg = candidates[0]

    print(str(tg))


if __name__ == "__main__":
    main()
