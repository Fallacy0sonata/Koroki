import json

with open("data/diffsinger_work/yoasobi_idol/transcript.json") as f:
    tr = json.load(f)
segs = tr["segments"]
print("First 14 transcript lines:")
for i, s in enumerate(segs[:14]):
    print("[%02d] %.2f-%.2f: %s" % (i, s["start"], s["end"], s["text"]))

print()
print("First 5 segments.ds entries:")
with open("data/diffsinger_work/yoasobi_idol/segments.ds") as f:
    ds = json.load(f)
for i, seg in enumerate(ds[:5]):
    phs = seg["ph_seq"].split()
    durs = [float(d)*1000 for d in seg["ph_dur"].split()]
    pairs = " ".join("%s(%d)" % (p, d) for p,d in zip(phs, durs))
    print("[%02d] offset=%.2f: %s" % (i, seg["offset"], pairs[:120]))
    print()
