"""Inspect segments.ds: ph_dur distribution, note_seq presence, f0 presence."""
import json, statistics

with open('data/diffsinger_work/yoasobi_idol/segments.ds') as f:
    segs = json.load(f)

print(f"Segments: {len(segs)}")
print()

# Check fields present
first = segs[0]
print("Fields in segment[0]:", list(first.keys()))
print()

# Collect all consonant and vowel durations
VOWELS = set('aeiouɯɴ')
SILENCE = {'AP', 'SP'}
ALL_AFFRICATES = {'ts', 'tɕ', 'dʑ', 'dz'}
con_durs, vow_durs = [], []

for seg in segs:
    phs = seg['ph_seq'].split()
    durs = [float(d) for d in seg['ph_dur'].split()]
    for ph, dur in zip(phs, durs):
        if ph in SILENCE: continue
        if ph in VOWELS or ph == 'ɴ':
            vow_durs.append(dur * 1000)
        else:
            con_durs.append(dur * 1000)

print(f"Consonant durations (ms): n={len(con_durs)}")
print(f"  min={min(con_durs):.0f}  median={statistics.median(con_durs):.0f}  max={max(con_durs):.0f}")
print(f"  <30ms: {sum(1 for d in con_durs if d<30)}  30-40ms: {sum(1 for d in con_durs if 30<=d<41)}  >40ms: {sum(1 for d in con_durs if d>40)}")
print()
print(f"Vowel durations (ms): n={len(vow_durs)}")
print(f"  min={min(vow_durs):.0f}  median={statistics.median(vow_durs):.0f}  max={max(vow_durs):.0f}")
print(f"  <60ms: {sum(1 for d in vow_durs if d<60)}  60-200ms: {sum(1 for d in vow_durs if 60<=d<=200)}  >200ms: {sum(1 for d in vow_durs if d>200)}")
print()

# Check note_seq
has_note = sum(1 for s in segs if s.get('note_seq','').strip())
has_f0 = sum(1 for s in segs if s.get('f0_seq','').strip())
print(f"note_seq populated: {has_note}/{len(segs)}")
print(f"f0_seq populated:   {has_f0}/{len(segs)}")
print()

# Show first segment full detail
s = segs[0]
phs = s['ph_seq'].split()
durs = [float(d) for d in s['ph_dur'].split()]
f0s = [float(v) for v in s['f0_seq'].split()]
print(f"Segment[0] offset={s['offset']}")
print(f"  ph+dur: {' '.join(f'{p}({d*1000:.0f})' for p,d in zip(phs,durs))}")
print(f"  f0 range: {min(f0s):.0f}-{max(f0s):.0f} Hz  ({len(f0s)} frames @ 5ms)")
