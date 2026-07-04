"""Verify phoneme sequences vs expected morae. Strict per-segment window."""
import json
import pykakasi

kks = pykakasi.kakasi()

_WORD_OVERRIDES = {
    "君":"きみ","彼女":"かのじょ","彼":"かれ","何":"なに","誰":"だれ",
    "心":"こころ","夢":"ゆめ","涙":"なみだ","僕":"ぼく","俺":"おれ",
}
_TEXT_PRE_FIXES = [
    ("愛されたことも","あいされたことも"),("愛したこともない","あいしたこともない"),
    ("愛したこと","あいしたこと"),("愛してるで","あいしてるで"),
    ("愛してる","あいしてる"),("愛してた","あいしてた"),
    ("愛して","あいして"),("愛する","あいする"),("愛した","あいした"),
    ("愛だ","あいだ"),("愛の","あいの"),("愛を","あいを"),
    ("愛が","あいが"),("愛は","あいは"),("愛と","あいと"),
]

def kakasi_to_hira(text):
    for k, v in _TEXT_PRE_FIXES: text = text.replace(k, v)
    exp = []
    for ch in text:
        if ch == "々" and exp:
            j = len(exp)-1
            while j >= 0 and exp[j] == "々": j -= 1
            if j >= 0: exp.append(exp[j]); continue
        exp.append(ch)
    text = "".join(exp)
    parts = []
    for item in kks.convert(text):
        orig = item.get("orig","")
        parts.append(_WORD_OVERRIDES.get(orig) or item.get("hira") or item.get("orig") or "")
    return "".join(parts)

_SMALL_HIRA = frozenset("ぁぃぅぇぉゃゅょゎ")
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
    lyrics = json.load(f)["segments"]
with open("data/diffsinger_work/yoasobi_idol/alignment_segs.json") as f:
    intervals = json.load(f)

_SILENCE = {"AP","SP"}
_VOWELS = set("aeiouɯɴ")
_AFFRICATES = {"ts","tɕ","dʑ"}  # count as vowel-bearing (they precede a vowel)

ok_count = sp_count = off_count = 0

for i, lyr in enumerate(lyrics):
    text = lyr["text"].strip()
    t_start, t_end = lyr["start"], lyr["end"]

    hira = kakasi_to_hira(text)
    morae = [m for m in split_morae(hira) if m != "ー"]
    n_morae = len(morae)

    # Strict window: interval start must be within [t_start, t_end)
    line_phones = [ph for s,e,ph in intervals
                   if s >= t_start - 0.02 and s < t_end and ph not in _SILENCE]

    if not line_phones:
        sp_count += 1
        print(f"[{i:02d}] SP   | {text}")
        continue

    # Count "syllable nuclei" — vowels + affricates (one per mora)
    n_nuclei = sum(1 for p in line_phones if p in _VOWELS or p in _AFFRICATES)
    diff = abs(n_nuclei - n_morae)

    if diff <= 1:
        ok_count += 1
        status = "OK "
    else:
        off_count += 1
        status = "OFF"

    print(f"[{i:02d}] {status} | exp={n_morae}  got≈{n_nuclei}  | {text}")
    if diff > 1:
        print(f"       hira:   {hira}")
        print(f"       ph:     {' '.join(line_phones)[:100]}")

print(f"\nSummary: OK={ok_count}  OFF={off_count}  SP={sp_count}  (total=64)")
