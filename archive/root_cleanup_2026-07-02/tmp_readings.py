"""Show kakasi hiragana readings for all transcript lines — spot misreadings."""
import json, sys
sys.path.insert(0, "experiments/diffsinger")

# Replicate the exact _kakasi_to_hira + _split_morae logic from sing_song.py
import pykakasi
kks = pykakasi.kakasi()

_WORD_OVERRIDES = {
    "君": "きみ", "僕": "ぼく", "俺": "おれ", "貴方": "あなた",
    "貴女": "あなた", "彼女": "かのじょ", "彼": "かれ",
    "心": "こころ", "夢": "ゆめ", "空": "そら", "星": "ほし",
    "花": "はな", "涙": "なみだ", "声": "こえ",
    "手": "て", "目": "め", "日": "ひ", "今": "いま",
    "何": "なに", "誰": "だれ", "何処": "どこ",
    "此処": "ここ", "何時": "いつ",
}
_TEXT_PRE_FIXES = [
    ("愛されたことも", "あいされたことも"), ("愛したこともない", "あいしたこともない"),
    ("愛したこと", "あいしたこと"), ("愛してるで", "あいしてるで"),
    ("愛してるよ", "あいしてるよ"), ("愛してる", "あいしてる"),
    ("愛してた", "あいしてた"), ("愛してない", "あいしてない"),
    ("愛して", "あいして"), ("愛する", "あいする"), ("愛した", "あいした"),
    ("愛せ", "あいせ"), ("愛だ", "あいだ"), ("愛の", "あいの"),
    ("愛を", "あいを"), ("愛が", "あいが"), ("愛は", "あいは"), ("愛と", "あいと"),
]
_SMALL_HIRA = frozenset("ぁぃぅぇぉゃゅょゎ")

def kakasi_to_hira(text):
    for k, v in _TEXT_PRE_FIXES:
        text = text.replace(k, v)
    expanded = []
    for ch in text:
        if ch == "々" and expanded:
            j = len(expanded) - 1
            while j >= 0 and expanded[j] == "々": j -= 1
            if j >= 0:
                expanded.append(expanded[j]); continue
        expanded.append(ch)
    text = "".join(expanded)
    parts = []
    for item in kks.convert(text):
        orig = item.get("orig", "")
        if orig in _WORD_OVERRIDES:
            parts.append(_WORD_OVERRIDES[orig])
        else:
            parts.append(item.get("hira") or item.get("orig") or "")
    return "".join(parts)

def split_morae(text):
    text = "".join(chr(ord(c)-0x60) if 0x30A1<=ord(c)<=0x30F6 else c for c in text)
    morae, i = [], 0
    while i < len(text):
        c = text[i]
        if i+1 < len(text) and text[i+1] in _SMALL_HIRA:
            morae.append(c+text[i+1]); i += 2
        elif "ぁ" <= c <= "ゟ":
            morae.append(c); i += 1
        else:
            i += 1
    return morae

with open("data/diffsinger_work/yoasobi_idol/transcript.json") as f:
    segs = json.load(f)["segments"]

for i, s in enumerate(segs):
    text = s["text"].strip()
    hira = kakasi_to_hira(text)
    morae = [m for m in split_morae(hira) if m != "ー"]
    print("[%02d] %s" % (i, text))
    print("     %s  (%d)" % (hira, len(morae)))
    print()
