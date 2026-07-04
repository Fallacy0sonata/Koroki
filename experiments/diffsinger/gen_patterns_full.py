"""
Generate CosyVoice speech patterns covering ALL 63 phonemes in ja_ipa_dict.txt.

Purpose: DiffSinger's 160k base checkpoint was trained with 63 phonemes + AP + SP + PAD = 65
vocab slots. Fine-tuning from that checkpoint requires the same vocab size, meaning every
phoneme in ja_ipa_dict.txt must appear at least once in the training data so the txt_embed
weights stay valid. This script synthesizes targeted patterns to cover all 63 phonemes,
especially the ones missing from the 26-phoneme koroki_v2 dataset:
  - Long vowels: aː eː iː oː ɯː
  - Geminate consonants: bː cː dː kː mː nː pː pʲː sː tɕː tː ɕː ɡː ɲː
  - Palatalized: bʲ dʲ mʲ pʲ tʲ ɾʲ
  - Special allophones: ç ŋ ɟ ɰ̃ ɲ ʑ dz
  - Rare/devoiced: i̥ ɯ̥ ɨ ɨː ɨ̥

Post-processing pipeline converts SOFA's simple romaji output into richer IPA:
  1. Long vowel merging (consecutive same vowels → long form)
  2. Geminate conversion (c + consonant → geminate phoneme)
  3. ɴ allophone selection (ŋ before velars, ɰ̃ before vowels)
  4. Japanese devoicing (ɯ/i between voiceless consonants → ɯ̥/i̥)
  5. ʑ allophone (dʑ between vowels → ʑ)
  6. dz detection (z after SP/AP = word-initial → dz)

Usage (from Koroki root, .venv_diffsinger):
    # 1. Start CosyVoice adapter first (separate terminal):
    .\\scripts\\easy_start_cosyvoice_adapter.ps1

    # 2. Run synthesis + alignment:
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\gen_patterns_full.py

    # 3. Skip synthesis if WAVs already exist:
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\gen_patterns_full.py --skip-synthesis

    # 4. Override output dir:
    .venv_diffsinger\\Scripts\\python.exe experiments\\diffsinger\\gen_patterns_full.py --out-dir data/diffsinger_raw/patterns_full

Output: data/diffsinger_raw/patterns_full/
    wavs/cv_full_XXXX.wav
    transcriptions.csv
    phonemes.txt          (all 63 phonemes + AP + SP, one per line, sorted)
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

_OUT_DIR   = _REPO_ROOT / "data" / "diffsinger_raw" / "patterns_full"
_WAVS_DIR  = _OUT_DIR / "wavs"
_SOFA_TMP  = _OUT_DIR / "_sofa_tmp"

_COSYVOICE_URL = "http://127.0.0.1:9004/synthesize"
_TARGET_SR = 44100
_TIMEOUT   = 60

# ---------------------------------------------------------------------------
# SOFA romaji → IPA mapping (extended for 63-phoneme coverage)
# ---------------------------------------------------------------------------
# Key changes vs gen_patterns.py:
#   ny → ɲ  (palatal nasal, was n)
#   hy → ç  (palatal fricative, was h)
#   my → mʲ (labialized nasal, was m)
#   ry → ɾʲ (palatalized flap, was ɾ)
#   by → bʲ (palatalized bilabial, was b)
#   py → pʲ (palatalized bilabial stop, was p)
#   gy → ɟ  (palatal voiced stop — gy syllables → ɟ allophone)
#   dy → dʲ (palatalized d, てぃ row)

_SOFA_TO_IPA: dict[str, str] = {
    "a": "a",   "i": "i",   "u": "ɯ",   "e": "e",   "o": "o",
    "k": "k",   "g": "ɡ",   "s": "s",   "z": "z",
    "t": "t",   "d": "d",   "n": "n",   "h": "h",
    "b": "b",   "p": "p",   "m": "m",   "y": "j",   "r": "ɾ",
    "w": "w",   "N": "ɴ",   "cl": "c",
    "sh": "ɕ",  "ch": "tɕ", "ts": "ts",
    "f": "ɸ",   "j": "dʑ",  "v": "v",
    # Palatalized / compound onsets — specific mappings to 63-phoneme targets
    "ky":  "k",    # ki → k + i (SOFA emits 'ky' for き)
    "gy":  "ɟ",    # gi → ɟ + i (voiced palatal stop allophone)
    "ny":  "ɲ",    # ni → ɲ + i (palatal nasal)
    "hy":  "ç",    # hi → ç + i (palatal fricative)
    "my":  "mʲ",   # mi → mʲ + i (palatalized nasal)
    "ry":  "ɾʲ",   # ri → ɾʲ + i (palatalized flap)
    "by":  "bʲ",   # bi → bʲ + i (palatalized bilabial voiced stop)
    "py":  "pʲ",   # pi → pʲ + i (palatalized bilabial voiceless stop)
    "dy":  "dʲ",   # di (ディ) → dʲ + i
    "ty":  "tʲ",   # ti (ティ) → tʲ + i
    # Silence/boundary tokens
    "AP":  "AP",  "SP":  "SP",  "br":  "AP",
    "pau": "SP",  "sil": "SP",  "EP":  "SP",  "GS":  "SP",
}

# ---------------------------------------------------------------------------
# Geminate consonant table: preceding 'c' + following IPA → geminate IPA
# ---------------------------------------------------------------------------
_GEMINATE: dict[str, str] = {
    "k":   "kː",
    "ɡ":   "ɡː",
    "s":   "sː",
    "t":   "tː",
    "d":   "dː",
    "b":   "bː",
    "p":   "pː",
    "m":   "mː",
    "n":   "nː",
    "tɕ":  "tɕː",
    "ɕ":   "ɕː",
    "ɲ":   "ɲː",
    "pʲ":  "pʲː",
    "mʲ":  "mː",    # っみ → mː (double nasal, not pʲː)
    "bʲ":  "bː",    # っび → bː (geminate b, palatalisation absorbed)
    "ɾ":   "ɾ",     # っr is unusual, keep as-is
}

# Short vowels eligible for long-vowel merging
_SHORT_TO_LONG: dict[str, str] = {
    "a": "aː",
    "i": "iː",
    "ɯ": "ɯː",
    "e": "eː",
    "o": "oː",
}

# Voiceless consonant set for devoicing rule
_VOICELESS: frozenset[str] = frozenset({
    "k", "kː", "s", "sː", "t", "tː", "ts", "tɕ", "tɕː",
    "ɕ", "ɕː", "ɸ", "ç", "p", "pː", "pʲ", "pʲː", "h",
    "tʲ",
})

# Velar consonants that trigger ŋ allophone for ɴ
_VELAR: frozenset[str] = frozenset({"k", "kː", "ɡ", "ɡː"})

# Vowels that trigger ɰ̃ allophone for ɴ
_VOWEL_SET: frozenset[str] = frozenset({"a", "i", "ɯ", "e", "o", "aː", "iː", "ɯː", "eː", "oː"})

# ---------------------------------------------------------------------------
# All 63 phonemes declared in ja_ipa_dict.txt
# ---------------------------------------------------------------------------
_ALL_PHONEMES: list[str] = [
    "a", "aː", "b", "bʲ", "bː", "c", "cː", "d", "dː", "dz", "dʑ", "dʲ",
    "e", "eː", "h", "i", "iː", "i̥", "j", "k", "kː", "m", "mʲ", "mː",
    "n", "nː", "o", "oː", "p", "pʲ", "pʲː", "pː", "s", "sː", "t", "tʲ",
    "ts", "tɕ", "tɕː", "tː", "w", "z", "ç", "ŋ", "ɕ", "ɕː", "ɟ", "ɡ",
    "ɡː", "ɨ", "ɨː", "ɨ̥", "ɯ", "ɯː", "ɯ̥", "ɰ̃", "ɲ", "ɲː", "ɴ", "ɸ",
    "ɾ", "ɾʲ", "ʑ", "ʔ",
]

# ---------------------------------------------------------------------------
# Pattern list — 420+ targeted texts for full 63-phoneme coverage
# ---------------------------------------------------------------------------

_PATTERNS: list[str] = [
    # ── LONG VOWELS: aː ──────────────────────────────────────────────────────
    # ー after a-final mora produces consecutive 'a' tokens → post-process → aː
    "おかあさんおかあさん",
    "おじさんおばさん",
    "大丈夫大丈夫",
    "はあはあ、つかれた",
    "ああそうかそうか",
    "かあさん待ってて",
    "ああああ、なんで",
    "ばあちゃんのうち",
    "おかあさんありがとう",
    "ああもう、やだなあ",

    # ── LONG VOWELS: iː ──────────────────────────────────────────────────────
    "お兄さんが来た",
    "いいね、それいい",
    "おにいさんかわいい",
    "ちいさいこえで",
    "にいにいうるさい",
    "いい気分だよ",
    "きいてきいて",
    "いいよいいよ、どうぞ",
    "毎日練習してる",
    "ちいさなひとつの光",

    # ── LONG VOWELS: ɯː ──────────────────────────────────────────────────────
    "空気が読めない",
    "勇気を出して",
    "うーんもう少し",
    "くうきよめない",
    "ゆうきをだして",
    "ふうん、そうなんだ",
    "うう、さむい",
    "うーんなるほど",
    "むうっとした顔で",
    "くうくう眠れない",

    # ── LONG VOWELS: eː ──────────────────────────────────────────────────────
    "先生ありがとう",
    "姉さん待って",
    "ねえねえきいて",
    "せんせいだいすき",
    "おねえさんどこ",
    "えええ、まじで",
    "ねえ、そっちいっていい",
    "へえそうなんだ",
    "えーっとね",
    "ねえねえ、きいてよ",

    # ── LONG VOWELS: oː ──────────────────────────────────────────────────────
    "学校楽しかった",
    "王様みたいな人",
    "おうさまのくに",
    "がっこうたのしい",
    "おおきなこえで",
    "おおそうかそうか",
    "こうこうせいのとき",
    "どうぞよろしく",
    "もうもうもう",
    "そうそうそうそう",

    # ── PALATALIZED: ɲ / ɲː (ny → ɲ mapping) ────────────────────────────────
    "にゃんにゃん鳴いてる",
    "にゅうがくおめでとう",
    "にょっきにょっき",
    "ねこがにゃあにゃあ",
    "にゃーってなく",
    "にゅうもんしたばかり",
    "にゃんこのきもち",
    "入院してからずっと",
    "ぬいぐるみにゃんこ",
    "にゅるにゅるしてる",

    # ── PALATALIZED: ç (hy → ç mapping) ─────────────────────────────────────
    "ひゃあ、びっくりした",
    "ひゅーっと風が吹いた",
    "ひょっとして好き",
    "ひゃくえんショップ",
    "ひゅうひゅうさむい",
    "ひょいっとよけた",
    "ひゃあこわかった",
    "ひゅんっていった",
    "ひょっこり現れた",
    "ひゃあそれはすごい",

    # ── PALATALIZED: mʲ (my → mʲ mapping) ───────────────────────────────────
    "みゃあって鳴く猫",
    "みゅうじっくすきだよ",
    "みょうにしずかだな",
    "みゃーみゃーないてる",
    "みゅーじかるみたい",
    "みょうじなんていうの",
    "みゃあと返事した",
    "みゅうみゅういう",
    "みょうにあたたかい",
    "みゃーと呼んでみた",

    # ── PALATALIZED: ɾʲ (ry → ɾʲ mapping) ───────────────────────────────────
    "りゃくしてよんで",
    "りゅうきょうのたび",
    "りょうりがすき",
    "りゃあ、やられた",
    "りゅうがくしたい",
    "りょこうにいきたい",
    "料理うまくなりたい",
    "りゃくごをつかう",
    "りゅうそうするよ",
    "りょうてをあげて",

    # ── PALATALIZED: bʲ (by → bʲ mapping) ───────────────────────────────────
    "びゃあ、こわい",
    "びゅんびゅんはしる",
    "びょうきになった",
    "びゃあってさけんだ",
    "びゅっとすぎた",
    "びょうどうにあつかって",
    "びゃーそれはやばい",
    "びゅんとよこぎった",
    "病院行かなきゃ",
    "びゃあ、おちた",

    # ── PALATALIZED: pʲ (py → pʲ mapping) ───────────────────────────────────
    "ぴゃあ、かわいい",
    "ぴゅっとにげた",
    "ぴょんぴょんとぶ",
    "ぴゃーなにそれ",
    "ぴゅっとはねた",
    "ぴょこぴょこ歩く",
    "ぴゃあ、びっくり",
    "ぴゅーんとんでった",
    "ぴょいっとよけた",
    "ぴゃあまじかよ",

    # ── PALATALIZED: ɟ (gy → ɟ mapping) ─────────────────────────────────────
    "ぎゃあ、いたい",
    "ぎゅうぎゅうづめ",
    "ぎょっとした顔",
    "ぎゃあってさけんだ",
    "ぎゅっとだきしめて",
    "ぎょうざたべたい",
    "ぎゃあもうやだ",
    "ぎゅうにゅうのんで",
    "ぎょっとしてしまった",
    "ぎゃあぎゃあないてる",

    # ── PALATALIZED: tʲ (ty → tʲ, ティ row) ─────────────────────────────────
    "ティアラをかぶって",
    "ティッシュもってきて",
    "てぃあらみたいな",
    "ティータイムしよう",
    "ティーンのきもち",
    "てぃんっとなった",
    "ティーポット用意して",
    "ティアラかわいいね",
    "ティーンだったあのころ",
    "てぃんてぃんきこえる",

    # ── PALATALIZED: dʲ (dy → dʲ, ディ row) ─────────────────────────────────
    "ディズニーいきたい",
    "ディスコみたいな夜",
    "ディテールにこだわって",
    "ディナーよやくした",
    "ディスプレイこわれた",
    "でぃすこにいきたい",
    "ディストーションかける",
    "ディープなはなし",
    "ディスコダンスをおどる",
    "ディフェンスがんばる",

    # ── GEMINATES: kː ────────────────────────────────────────────────────────
    "はっきり言って",
    "びっくりした",
    "ゆっくりしてね",
    "きっとうまくいく",
    "がっこうたのしい",
    "さっかーしようよ",
    "ぴっかぴかだよ",
    "もっかいいって",
    "うっかりわすれた",
    "ざっくりいうと",

    # ── GEMINATES: tː ────────────────────────────────────────────────────────
    "ちょっと待って",
    "もっと話して",
    "やっと会えた",
    "ぴったりだね",
    "ざっとみただけ",
    "ぎゅっとだいて",
    "きっとそうだよ",
    "はっとした",
    "さっと消えた",
    "きつっと縛って",

    # ── GEMINATES: pː ────────────────────────────────────────────────────────
    "いっぱい食べた",
    "コップを割った",
    "きっぱり断って",
    "ぱっとしない",
    "ひっぱってみて",
    "ざっぱくだけど",
    "すっぱいレモン",
    "はっぴーな気分",
    "おっぱいじゃない",
    "いっぽんみち",

    # ── GEMINATES: sː ────────────────────────────────────────────────────────
    "まっすぐ行く",
    "ざっさと行け",
    "さっさとしろよ",
    "どっさりある",
    "ねっしんにやる",
    "もっさりしてる",
    "どっさり積んだ",
    "ざっさっとやる",
    "まっさきにいった",
    "ちっさいけど",

    # ── GEMINATES: tɕː (っち) ─────────────────────────────────────────────────
    "どっちが好き",
    "こっちにきて",
    "そっちじゃない",
    "あっちいって",
    "うっちゃりした",
    "とっちめてやる",
    "そっちのほうが",
    "あっちこっちで",
    "こっちむいてよ",
    "どっちでもいい",

    # ── GEMINATES: ɕː (っし) ─────────────────────────────────────────────────
    "まっしろにそめて",
    "いっしょにいこう",
    "もっとはっしゃして",
    "ざっしをよんで",
    "いっしゅんのはなし",
    "きっしょうてんにょ",
    "ますまっしゅ",
    "もっしゃもっしゃたべた",
    "まっしぐらにはしる",
    "いっしゅうかん",

    # ── GEMINATES: ɡː ────────────────────────────────────────────────────────
    "でっかいゆめを",
    "おっとっとっと",
    # ɡː is very rare in native Japanese; use loan words or special contexts
    "ドッグランいった",
    "ビッグになりたい",
    "エッグサンドイッチ",
    "フォッグがかかった",
    "バッグをわすれた",
    "レッグウォーマー",
    "ドッグタグつけてる",
    "ビッグサプライズだ",

    # ── GEMINATES: bː ─────────────────────────────────────────────────────────
    # Very rare in native Japanese; use loan words or expressives
    "ラッビットはやい",
    "ホッビットみたいな",
    "ラビびっくりした",
    "クラッブのこうら",
    "グラッブするよ",
    "スタッブきたい",
    "ウェッブさいと",
    "クラッブアップル",
    "ダッブルですね",
    "フリッブフロップ",

    # ── GEMINATES: dː ─────────────────────────────────────────────────────────
    # dː as geminate stop is rare; covered by っd sequences in loan words
    "ベッドでねる",
    "アッドしてね",
    "オッドアイだよ",
    "レッドカーペット",
    "アッドオンする",
    "ウェッドするよ",
    "ヘッドフォンかけて",
    "グッドモーニング",
    "バッドエンドやだ",
    "レッドフラッグ",

    # ── GEMINATES: mː ─────────────────────────────────────────────────────────
    # Natural mː from っm (though rare in Japanese)
    "はんまあ、すごい",
    "んまい、おいしい",
    "うっまいこといった",
    "なんまんだぶ",
    "うまうまたべた",
    "んまっ、これは",
    "いっまに覚えよう",
    # loan word coverage
    "グラマーだよね",
    "サマーフェスたのしみ",
    "ハマーになりたい",

    # ── GEMINATES: nː ─────────────────────────────────────────────────────────
    "うんめいってなに",
    "おんなのひとたち",
    "こんなにすきなのに",
    "あんなところに",
    "そんなにないてない",
    "んんん、わからない",
    "うんうんそうだね",
    "あんなひとがいた",
    "んーなるほどね",
    "こんなにうれしいとは",

    # ── GEMINATES: ɲː ─────────────────────────────────────────────────────────
    # ɲː from っに/ɲ sequences (uncommon; use expressive speech)
    "にゃっにゃっにゃっ",
    "にゅっにゅっとした",
    "なんにゃーって",
    "にゅにゅにゅっと",
    "うんにゃそうじゃない",
    "うんにゃ、ちがう",
    "んにゃーってなく",
    "にゃにゃにゃっ",
    "うんにゃうんにゃ",
    "にゅっとそっと",

    # ── GEMINATES: pʲː (っぴ) ─────────────────────────────────────────────────
    "いっぴきのねこが",
    "いっぴょう投じる",
    "ぴっぴっとなる",
    "きっぴしゃりと",
    "はっぴいえんどう",
    "いっぴょうずつ",
    "ぴっぴっぴっと",
    "とっぴなはなし",
    "いっぴきいぬがいた",
    "はっぴえんどう",

    # ── ŋ: ɴ before velars ───────────────────────────────────────────────────
    "なんか違う気がする",
    "たんごをおぼえる",
    "えんがわにすわる",
    "せんげつのはなし",
    "こんかいはちがう",
    "ぱんくしてしまった",
    "はんがくセール",
    "こんきょがない",
    "あんきしてみた",
    "えんぎがうまい",
    "てんかいがはやい",
    "ほんごのつかいかた",
    "さんかくけい",
    "こんごともよろしく",

    # ── ɰ̃: ɴ before vowels ────────────────────────────────────────────────────
    "ぜんいんあつまって",
    "こんあいだのこと",
    "そんな暗い夜に",
    "あんいつもの場所で",
    "てんいんさんに",
    "かんいたいおう",
    "さんいんちほう",
    "あんおくのひかり",
    "でんおんがいい",
    "かんあんなことで",
    "あいたんいてくれて",
    "あんえいするよ",
    "てんいってなんだ",

    # ── ʑ: dʑ between vowels → ʑ ────────────────────────────────────────────
    "あじがするよね",
    "ふじさんのぼる",
    "かじをとれ",
    "うじうじしてないで",
    "しあわせにあじわう",
    "あじわいがある",
    "ゆうじはどこに",
    "つうじないよ",
    "ただしいあじを",
    "やさしいあじわい",
    "おうじさまみたいな",
    "まじかよそれ",
    "あじさいがさく",
    "かぜのなかのあじ",

    # ── dz: z word-initial / after SP ────────────────────────────────────────
    "ずっとそこにいた",
    "ずれてしまった",
    "ずるいなあ",
    "ずっといっしょに",
    "ずっとあなたのそばで",
    "ずっとずっとすきだよ",
    "ずばりいうわよ",
    "ずけずけいうな",
    "ずっこけそうになった",
    "ずっとまってたよ",
    "ずーっとここにいる",
    "ずらっとならんで",

    # ── devoiced ɯ̥: ɯ between voiceless consonants ───────────────────────────
    "すきなひとに伝えたい",
    "つくえのしたに",
    "くすりをのんだ",
    "すぐにきてください",
    "つくったりこわしたり",
    "すっきりした気分",
    "ふしぎなできごと",
    "はなしかけたかった",
    "すてきなひとだよ",
    "つきのひかりに",
    "かすかなきおくで",
    "ふつうにはなして",
    "つくり笑いをして",
    "なんとなくすきだよ",

    # ── devoiced i̥: i between voiceless consonants ────────────────────────────
    "きしゃにのりたい",
    "としのせいかな",
    "ひとりぼっちで",
    "きつねうどんたべたい",
    "ちかくにいるよ",
    "しきたりをまもる",
    "くちびるがかわく",
    "とちゅうでやめた",
    "ひたすらはしった",
    "しちごさんのひ",
    "はしきんぐしよう",
    "ひそかにおもってた",
    "たちきれなかった",
    "ちいさいこえで",

    # ── ɨ / ɨː coverage (Slavic-origin or rare analyses) ────────────────────
    # ɨ in Japanese linguistics: sometimes used for the /ɯ/ allophone before
    # dental consonants. Create dense patterns with す、つ、ず row:
    "すっとはいってく",
    "つっこみをいれる",
    "すてきなえがおで",
    "ついつい笑ってしまう",
    "つどいのばしょで",
    "すたすたあるいた",
    "つらいときこそ",
    "すいすいおよぐ",
    "ついにきたんだね",
    "すくなくともそれは",

    # ── ʔ: glottal stop (っ at phrase end or in expressives) ─────────────────
    "えっ、まじで",
    "あっ、そっか",
    "うっ、つらい",
    "あっ、わかった",
    "えっえっえっ",
    "あっ、ごめん",
    "うっ、しまった",
    "おっ、いいね",
    "あっ、いたい",
    "えっ、うそ",

    # ── YOASOBI song phrases (natural coverage + singing feel) ───────────────
    "君が笑えばいいと思ってた",
    "ただ側にいたいと願うだけで",
    "さよならだけが唯一の言葉で",
    "君を思い出せなくなる前に",
    "花のように散ってしまえたら",
    "会いに行くよひとりで",
    "生きていくのはつらいよと",
    "うまくいかない日々も続けてきたから",
    "正しいとか正しくないとかじゃなくて",
    "怖くて怖くて怖くて",
    "それでも前に進もうとした",
    "好きなものを好きだと言える",
    "飛び出してみよう青い空に",
    "あなたとなら何でもできそう",
    "無敵の笑顔で荒らすメディア",
    "天才的なアイドル様",
    "完璧で嘘つきな君は",
    "君は完璧で究極のアイドル",
    "その笑顔で愛してるで誰も彼も虜にしていく",
    "嘘でもそれは完全なアイ",

    # ── GENERAL CONVERSATIONAL (phoneme coverage via naturalistic speech) ─────
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
    "なんでなんでなんでこんなに苦しいの",
    "どうしてどうしてどうして好きなのに",
    "ないないない",
    "ダメダメダメ",
    "いやいやいや",
    "はいはいはい",
    "やだやだやだ",
    "まだまだまだ",
    "ぜんぜんぜんぜん",
    "ぜったいぜったいぜったい",
    "やっぱりやっぱりやっぱり",
    "どんどんどんどん",
    "きらきらきらきら",
    "どきどきどきどき",
    "わくわくわくわく",
    "ふわふわふわふわ",
    "ぐるぐるぐるぐる",
    "ぽかぽかぽかぽか",
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
            from math import gcd
            from scipy.signal import resample_poly
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


# ---------------------------------------------------------------------------
# Post-processing pipeline
# ---------------------------------------------------------------------------

def _post_process(ph_seq: list[str], ph_dur: list[float]) -> tuple[list[str], list[float]]:
    """
    Apply IPA post-processing to enrich SOFA's basic output with 63-phoneme allophones.

    Steps applied in order:
      1. Long vowel merging
      2. Geminate consonant conversion (c + consonant → geminate)
      3. ɴ allophone selection (ŋ before velars, ɰ̃ before vowels)
      4. Japanese devoicing (ɯ/i between voiceless consonants → ɯ̥/i̥)
      5. ʑ allophone (dʑ between vowels → ʑ)
      6. dz detection (z following SP/AP → dz)
    """
    if not ph_seq:
        return ph_seq, ph_dur

    # -- Step 1: Long vowel merging ------------------------------------------
    # Scan left-to-right. When ph[i] == ph[i+1] and both are short vowels,
    # merge: remove ph[i], change ph[i+1] to long form, combine durations.
    seq: list[str]   = list(ph_seq)
    dur: list[float] = list(ph_dur)
    i = 0
    while i < len(seq) - 1:
        curr = seq[i]
        nxt  = seq[i + 1]
        if curr == nxt and curr in _SHORT_TO_LONG:
            seq[i + 1] = _SHORT_TO_LONG[curr]
            dur[i + 1] = round(dur[i] + dur[i + 1], 4)
            seq.pop(i)
            dur.pop(i)
            # Don't advance i — check the merged token against the next one
        else:
            i += 1

    # -- Step 2: Geminate conversion -----------------------------------------
    # When ph[i] == 'c' and ph[i+1] is a known consonant, replace with geminate.
    # The 'c' closure is consumed and ph[i+1] becomes the geminate phoneme.
    i = 0
    while i < len(seq) - 1:
        if seq[i] == "c":
            nxt = seq[i + 1]
            if nxt in _GEMINATE:
                # Merge: combine durations into the geminate phoneme at i+1
                seq[i + 1] = _GEMINATE[nxt]
                dur[i + 1] = round(dur[i] + dur[i + 1], 4)
                seq.pop(i)
                dur.pop(i)
                # Don't advance — re-check position i in case of double geminate
                continue
        i += 1

    # -- Step 3: ɴ allophone selection ----------------------------------------
    for i in range(len(seq)):
        if seq[i] != "ɴ":
            continue
        nxt = seq[i + 1] if i + 1 < len(seq) else "SP"
        # Strip length markers for category lookup
        nxt_base = nxt.rstrip("ː") if nxt not in ("SP", "AP") else nxt
        if nxt_base in _VELAR or nxt in _VELAR:
            seq[i] = "ŋ"
        elif nxt_base in _VOWEL_SET or nxt in _VOWEL_SET:
            seq[i] = "ɰ̃"
        # Else: keep as ɴ (before dentals, labials, nasals, SP, etc.)

    # -- Step 4: Japanese devoicing -------------------------------------------
    # ɯ or i between two voiceless consonants → ɯ̥ or i̥
    # Also devoice ɯ/i at end of phrase (before SP/AP) when preceded by voiceless
    for i in range(len(seq)):
        if seq[i] not in ("ɯ", "i"):
            continue
        prev_ph = seq[i - 1] if i > 0 else "SP"
        next_ph = seq[i + 1] if i + 1 < len(seq) else "SP"
        prev_voiceless = prev_ph in _VOICELESS
        next_voiceless = next_ph in _VOICELESS or next_ph in ("SP", "AP")
        if prev_voiceless and next_voiceless:
            seq[i] = "ɯ̥" if seq[i] == "ɯ" else "i̥"

    # -- Step 5: ʑ allophone --------------------------------------------------
    # dʑ between two vowels (or vowel + AP/SP boundary) → ʑ (voiced fricative)
    for i in range(len(seq)):
        if seq[i] != "dʑ":
            continue
        prev_ph = seq[i - 1] if i > 0 else "SP"
        next_ph = seq[i + 1] if i + 1 < len(seq) else "SP"
        prev_is_vowel = prev_ph.rstrip("ː") in _VOWEL_SET or prev_ph in _VOWEL_SET
        next_is_vowel = next_ph.rstrip("ː") in _VOWEL_SET or next_ph in _VOWEL_SET
        if prev_is_vowel and next_is_vowel:
            seq[i] = "ʑ"

    # -- Step 6: dz detection -------------------------------------------------
    # z immediately following SP/AP (word-initial position) → dz
    for i in range(len(seq)):
        if seq[i] != "z":
            continue
        prev_ph = seq[i - 1] if i > 0 else "SP"
        if prev_ph in ("SP", "AP"):
            seq[i] = "dz"

    return seq, dur


def _textgrid_to_row(name: str, tg_path: Path) -> tuple[str, str] | None:
    intervals = _parse_textgrid(tg_path)
    if not intervals:
        return None

    ph_seq: list[str]   = []
    ph_dur: list[float] = []
    for xmin, xmax, label in intervals:
        if xmax <= xmin:
            continue
        dur = round(xmax - xmin, 4)
        ipa = _SOFA_TO_IPA.get(label, label)
        if not ipa:
            continue
        ph_seq.append(ipa)
        ph_dur.append(dur)

    if not ph_seq:
        return None

    # Apply full post-processing pipeline
    ph_seq, ph_dur = _post_process(ph_seq, ph_dur)

    if not ph_seq:
        return None
    return " ".join(ph_seq), " ".join(str(d) for d in ph_dur)


# ---------------------------------------------------------------------------
# Manual sample helper — for phonemes unreachable via SOFA post-processing
# ---------------------------------------------------------------------------

def _make_manual_row(
    name: str,
    ph_seq_list: list[str],
    wav_path: Path,
) -> tuple[str, str] | None:
    """
    Build a transcription row without SOFA by estimating durations from WAV length.

    Distributes total WAV duration proportionally using phoneme-class weights:
      - Long vowels: 1.4x
      - Short vowels: 1.0x
      - Consonants:   0.6x
      - AP/SP:        0.8x
    """
    if not wav_path.exists():
        return None
    try:
        info = sf.info(str(wav_path))
        total_dur = info.duration
    except Exception:
        return None

    weights: dict[str, float] = {}
    for ph in ph_seq_list:
        if ph in ("AP", "SP"):
            weights[ph] = 0.8
        elif ph.endswith("ː"):
            weights[ph] = 1.4
        elif ph.rstrip("ː") in _VOWEL_SET or ph in _VOWEL_SET:
            weights[ph] = 1.0
        else:
            weights[ph] = 0.6

    total_weight = sum(weights[ph] for ph in ph_seq_list)
    if total_weight == 0:
        return None

    ph_dur = [
        round(weights[ph] / total_weight * total_dur, 4)
        for ph in ph_seq_list
    ]
    return " ".join(ph_seq_list), " ".join(str(d) for d in ph_dur)


# ---------------------------------------------------------------------------
# SOFA batch runner
# ---------------------------------------------------------------------------

def _run_sofa_batch(batch_dir: Path) -> bool:
    cmd = [
        sys.executable, str(_SOFA_DIR / "infer.py"),
        "--ckpt",       str(_SOFA_CKPT),
        "--folder",     str(batch_dir),
        "--dictionary", str(_SOFA_DICT),
        "--out_formats", "TextGrid",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(_SOFA_DIR), timeout=3600,
    )
    for line in result.stderr.splitlines():
        if line.strip() and "warning" not in line.lower():
            print(f"  [sofa] {line.strip()}")
    if result.returncode != 0:
        print(f"SOFA failed:\n{result.stderr[-800:]}")
        return False
    return True


# ---------------------------------------------------------------------------
# Manual coverage samples
# ---------------------------------------------------------------------------

# Each entry: (text_for_synthesis, manual_ph_seq, name_suffix)
# These use SOFA-unreachable phonemes and will be manually tagged.
_MANUAL_SAMPLES: list[tuple[str, list[str], str]] = [
    # ɨ: rare vowel, used as allophone of ɯ in some transcription systems
    # Manually assign to す before dental (phonetically near ɨ in some analyses)
    (
        "すっとながれていく",
        ["SP", "s", "ɨ", "t", "t", "o", "n", "a", "ɡ", "a", "ɾ", "e", "t", "e", "i", "k", "ɯ", "SP"],
        "manual_ɨ_01",
    ),
    # ɨː: lengthened version
    (
        "すーっとながれる",
        ["SP", "s", "ɨː", "t", "t", "o", "n", "a", "ɡ", "a", "ɾ", "e", "ɾ", "ɯ", "SP"],
        "manual_ɨː_01",
    ),
    # ɨ̥: devoiced ɨ (voiceless context)
    (
        "すっかりつかれた",
        ["SP", "s", "ɨ̥", "k", "k", "a", "ɾ", "i", "t", "s", "ɨ̥", "k", "a", "ɾ", "e", "t", "a", "SP"],
        "manual_ɨ̥_01",
    ),
    # cː: geminate closure mark (the 'c' phoneme itself, not as closure before consonant)
    # In the 63-phoneme dict, cː is distinct from kː. Use っっ-like expressive.
    (
        "あっっとおどろく",
        ["SP", "a", "cː", "t", "o", "o", "d", "o", "ɾ", "o", "k", "ɯ", "SP"],
        "manual_cː_01",
    ),
    # dz: word-initial ず (the post-process catches most; this is a clean manual sample)
    (
        "ずばりそうです",
        ["SP", "dz", "ɯ", "b", "a", "ɾ", "i", "s", "oː", "d", "e", "s", "ɯ̥", "SP"],
        "manual_dz_01",
    ),
    # dʲ: palatalized d — loan word ディ (di) sound as in ディスコ
    (
        "ディスコで踊ろう",
        ["SP", "dʲ", "i", "s", "ɯ", "k", "o", "d", "e", "o", "d", "o", "ɾ", "oː", "SP"],
        "manual_dʲ_01",
    ),
    # nː: geminate n — さん + ねん → nː (N mora before n-initial syllable)
    (
        "三年間がんばった",
        ["SP", "s", "a", "nː", "e", "n", "k", "a", "ŋ", "ɡ", "a", "m", "b", "a", "t", "t", "a", "SP"],
        "manual_nː_01",
    ),
    # tʲ: palatalized t — loan word ティ (ti) sound as in パーティー
    (
        "パーティーへ行こう",
        ["SP", "p", "aː", "tʲ", "iː", "e", "i", "k", "oː", "SP"],
        "manual_tʲ_01",
    ),
    # ʔ: glottal stop — sentence-final っ in expressive Japanese (あっ exclamation)
    (
        "あっ、そうか",
        ["SP", "a", "ʔ", "s", "oː", "k", "a", "SP"],
        "manual_ʔ_01",
    ),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate full 63-phoneme DiffSinger training patterns via CosyVoice + SOFA"
    )
    parser.add_argument(
        "--skip-synthesis", action="store_true",
        help="Skip CosyVoice synthesis — use existing WAVs in output wavs/ dir",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=_OUT_DIR,
        help="Output directory (default: data/diffsinger_raw/patterns_full)",
    )
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
    print(f"Manual samples:       {len(_MANUAL_SAMPLES)}")
    print(f"Output dir:           {out_dir}")

    # ── Step 1: Synthesize ───────────────────────────────────────────────────
    if not args.skip_synthesis:
        print("\n[1/4] Synthesizing with CosyVoice...")
        ok = fail = skip = 0

        # Synthesize regular patterns
        for idx, text in enumerate(patterns):
            name    = f"cv_full_{idx:04d}"
            wav_out = wavs_dir / f"{name}.wav"
            if wav_out.exists() and wav_out.stat().st_size > 0:
                skip += 1
                continue
            success = _synthesize(text, wav_out)
            if success:
                ok += 1
                print(f"  [{idx + 1}/{len(patterns)}] OK  {name}: {text[:50]}")
            else:
                fail += 1
            time.sleep(0.1)

        # Synthesize manual samples
        for text, _ph_seq, suffix in _MANUAL_SAMPLES:
            name    = f"cv_full_{suffix}"
            wav_out = wavs_dir / f"{name}.wav"
            if wav_out.exists() and wav_out.stat().st_size > 0:
                skip += 1
                continue
            success = _synthesize(text, wav_out)
            if success:
                ok += 1
                print(f"  [manual] OK  {name}: {text[:50]}")
            else:
                fail += 1
            time.sleep(0.1)

        print(f"  Synthesized: {ok} OK, {fail} failed, {skip} skipped")
    else:
        print("\n[1/4] Skipping synthesis (--skip-synthesis)")

    # ── Step 2: Prepare SOFA batch folder ───────────────────────────────────
    print("\n[2/4] Preparing SOFA alignment...")
    prepared = 0
    for idx, text in enumerate(patterns):
        name    = f"cv_full_{idx:04d}"
        wav_src = wavs_dir / f"{name}.wav"
        if not wav_src.exists():
            continue

        lab_text = _kana_to_sofa_lab(text)
        if not lab_text.strip():
            print(f"  WARN: empty lab for '{text[:40]}' — skipping")
            continue

        shutil.copy2(wav_src, sofa_tmp / f"{name}.wav")
        (sofa_tmp / f"{name}.lab").write_text(lab_text, encoding="utf-8")
        prepared += 1

    print(f"  Prepared {prepared} clips for SOFA alignment")

    if prepared == 0:
        sys.exit("No WAVs to align — check synthesis step")

    # ── Step 3: Run SOFA batch alignment ────────────────────────────────────
    print("\n[3/4] Running SOFA batch alignment...")
    ok = _run_sofa_batch(sofa_tmp)
    if not ok:
        print("  WARN: SOFA returned non-zero exit; some TextGrids may be missing")

    # ── Step 4: Parse TextGrids + manual rows → transcriptions.csv ──────────
    print("\n[4/4] Building transcriptions.csv...")
    rows: list[tuple[str, str, str]] = [("name", "ph_seq", "ph_dur")]
    missing = success_count = 0
    tg_dir = sofa_tmp / "TextGrid"

    # Regular SOFA-aligned patterns
    for idx, text in enumerate(patterns):
        name    = f"cv_full_{idx:04d}"
        tg_path = tg_dir / f"{name}.TextGrid"
        if not tg_path.exists():
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

        ph_seq_str, ph_dur_str = result
        rows.append((name, ph_seq_str, ph_dur_str))
        success_count += 1

    # Manual samples — use estimated durations, no SOFA
    for text, ph_seq_list, suffix in _MANUAL_SAMPLES:
        name    = f"cv_full_{suffix}"
        wav_path = wavs_dir / f"{name}.wav"
        result = _make_manual_row(name, ph_seq_list, wav_path)
        if result is None:
            print(f"  WARN: manual sample {name} WAV not found — skipping")
            missing += 1
            continue
        ph_seq_str, ph_dur_str = result
        rows.append((name, ph_seq_str, ph_dur_str))
        success_count += 1
        print(f"  [manual] {name}: {ph_seq_str[:60]}")

    # Write CSV
    csv_path = out_dir / "transcriptions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"  Wrote {success_count} rows ({missing} missing) → {csv_path}")

    # ── Write phonemes.txt ───────────────────────────────────────────────────
    ph_path = out_dir / "phonemes.txt"
    # All 63 phonemes + AP + SP, sorted
    all_ph = sorted(set(_ALL_PHONEMES) | {"AP", "SP"})
    ph_path.write_text("\n".join(all_ph) + "\n", encoding="utf-8")
    print(f"  Wrote {len(all_ph)} phonemes → {ph_path}")

    # ── Validation: check coverage ───────────────────────────────────────────
    print("\n[Validation] Checking phoneme coverage...")
    covered: set[str] = set()
    for row in rows[1:]:  # skip header
        ph_seq_str = row[1]
        covered.update(ph_seq_str.split())

    all_target = set(_ALL_PHONEMES)
    missing_ph = all_target - covered
    extra_ph   = covered - all_target - {"AP", "SP"}

    if missing_ph:
        print(f"  WARNING: {len(missing_ph)} phonemes NOT covered:")
        for ph in sorted(missing_ph):
            print(f"    - {ph}")
        print("  Consider adding more targeted patterns or manual samples.")
    else:
        print("  All 63 phonemes covered.")

    if extra_ph:
        print(f"  INFO: {len(extra_ph)} phonemes in output not in 63-dict (check mapping):")
        for ph in sorted(extra_ph):
            print(f"    ? {ph}")

    covered_count = len(all_target & covered)
    print(f"  Coverage: {covered_count}/{len(all_target)} phonemes")

    print(f"\nDone. Dataset written to: {out_dir}")
    print("Next steps:")
    print("  1. Copy phonemes.txt to any co-trained dataset if needed")
    print("  2. Binarize: python DiffSinger/scripts/binarize.py --config DiffSinger/configs/koroki_v3.yaml")
    print("  3. Train:    python DiffSinger/scripts/train.py acoustic --config DiffSinger/configs/koroki_v3.yaml --exp koroki_v3")


if __name__ == "__main__":
    main()
