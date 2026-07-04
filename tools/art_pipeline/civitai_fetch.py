"""Resolve Civitai model metadata (version id, downloadUrl, filename, size, base model, trigger words)
for the two rough-sketch FLUX LoRAs, then attempt download to ComfyUI loras. If a token is required,
report that clearly instead of saving a login/HTML blob.
"""
import os, ssl, json, urllib.request
ssl._create_default_https_context = ssl._create_unverified_context
LORADIR=r"C:/Users/Shinn/Desktop/Koroki/tools/ComfyUI/models/loras"
os.makedirs(LORADIR,exist_ok=True)
# optional token from env/.env
TOK=os.environ.get("CIVITAI_TOKEN")
try:
    for line in open(r"C:/Users/Shinn/Desktop/Koroki/.env",encoding="utf-8",errors="ignore"):
        if line.strip().startswith("CIVITAI"):
            TOK=line.split("=",1)[1].strip().strip('"').strip("'")
except Exception: pass
print("civitai token present:", bool(TOK), flush=True)

MODELS={"chaotic_lineart":1278849,"sketchpad_concept":1433827}
def api(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req,timeout=40).read())

def pick_flux_version(meta):
    for v in meta.get("modelVersions",[]):
        if "flux" in (v.get("baseModel","").lower()):
            return v
    return meta.get("modelVersions",[None])[0]

def download(url,dest):
    if TOK:
        url=url+(("&" if "?" in url else "?")+"token="+TOK)
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    r=urllib.request.urlopen(req,timeout=120)
    ct=r.headers.get("Content-Type","")
    if "text/html" in ct or "json" in ct:
        head=r.read(400)
        return False, f"needs-auth/redirect (Content-Type={ct}) {head[:120]!r}"
    total=0
    with open(dest,"wb") as f:
        while True:
            chunk=r.read(1<<20)
            if not chunk: break
            f.write(chunk); total+=len(chunk)
    return True, f"{total/1e6:.1f} MB"

for name,mid in MODELS.items():
    try:
        meta=api(f"https://civitai.com/api/v1/models/{mid}")
    except Exception as e:
        print(name,"API FAIL",repr(e)[:120],flush=True); continue
    v=pick_flux_version(meta)
    if not v: print(name,"no version",flush=True); continue
    files=v.get("files",[])
    f0=next((f for f in files if f.get("type")=="Model"),files[0] if files else None)
    words=v.get("trainedWords",[])
    print(f"\n== {name} (model {mid}) ==",flush=True)
    print("  version:",v.get("id"),v.get("name"),"| base:",v.get("baseModel"),flush=True)
    print("  trigger words:",words,flush=True)
    if not f0: print("  no file",flush=True); continue
    print(f"  file: {f0.get('name')}  ~{f0.get('sizeKB',0)/1024:.1f} MB",flush=True)
    dl=f0.get("downloadUrl") or f"https://civitai.com/api/download/models/{v.get('id')}"
    dest=os.path.join(LORADIR,f"sketch_{name}.safetensors")
    try:
        ok,msg=download(dl,dest)
        print("  DOWNLOAD",("OK "+msg) if ok else ("FAILED: "+msg),flush=True)
        if not ok and os.path.exists(dest) and os.path.getsize(dest)<100000: os.remove(dest)
    except Exception as e:
        print("  DOWNLOAD EXC",repr(e)[:160],flush=True)
print("\nDONE",flush=True)
