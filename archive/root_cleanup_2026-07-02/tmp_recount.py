cases = [
    ('見えそうで見えない秘密は蜜の味', 18,
     'mʲ i e s o ɯ d e mʲ i e n a i ç i mʲ i ts ɯ h a mʲ i ts ɯ n o a dʑ i'),
    ('わけがない これはネタじゃない からこそ許せない', 22,
     'i w a k e ɡ a n a i k o ɾ e h a n e t a dʑ a n a i k a ɾ a k o s o j ɯ ɾ ɯ s e n a i'),
    ('完璧じゃない 君じゃ許せない 自分を許せない', 24,
     'k a ɴ p e c i dʑ a n a i c i mʲ i dʑ a j ɯ ɾ ɯ s e n a i dʑ i b ɯ ɴ w o j ɯ ɾ ɯ s e n a i'),
    ('唯一無二じゃなくちゃイヤイヤ', 14,
     'j ɯ i i ts ɯ m ɯ ɲ i dʑ a n a k ɯ tɕ a i j a i j a'),
]
VOWELS = set('aeiouɯɴ')
for text, exp, ph_str in cases:
    phones = ph_str.split()
    n = sum(1 for p in phones if p in VOWELS)
    diff = abs(n - exp)
    status = 'OK (<=1)' if diff <= 1 else 'STILL OFF'
    print(text[:28].ljust(30), 'exp=%d  vowels=%d  diff=%d  %s' % (exp, n, diff, status))
