"""Composite Koroki's EXISTING (SDXL clean-anime) sprite onto Sketch Pad (painterly) rooms to judge
the style clash. This decides whether she needs restyling to live in this world.
"""
import os
from PIL import Image
ROOT=r"C:/Users/Shinn/Desktop/Koroki"
SPR=os.path.join(ROOT,"clients","web","assets","koroki_sprites")
FARM=os.path.join(ROOT,"assets","flux_style_farm")
PREV=r"C:/Users/Shinn/Desktop/Koroki/data/art_previews"

def place(room_png, sprite_png, hfrac=0.60, cx=0.62, foot=0.965):
    room=Image.open(os.path.join(FARM,room_png)).convert("RGBA"); W,H=room.size
    spr=Image.open(os.path.join(SPR,sprite_png)).convert("RGBA")
    bb=spr.getbbox();  spr=spr.crop(bb) if bb else spr
    sh=int(H*hfrac); sw=int(spr.width*sh/spr.height)
    spr=spr.resize((sw,sh),Image.LANCZOS)
    x=int(W*cx-sw/2); y=int(H*foot-sh)
    out=room.copy(); out.alpha_composite(spr,(x,y)); return out.convert("RGB")

jobs=[
 ("bedroom + stand_neutral","P_bedroom_night.png","stand_neutral.png",0.62,0.66,0.97),
 ("bedroom + koroki_neutral","P_bedroom_night.png","koroki_neutral.png",0.60,0.64,0.985),
 ("lounge + stand_neutral","P_lounge_evening.png","stand_neutral.png",0.60,0.60,0.97),
 ("study + stand_smug","P_study_rain.png","stand_smug.png",0.58,0.58,0.97),
]
imgs=[]
for lab,room,spr,hf,cx,ft in jobs:
    try: imgs.append((lab,place(room,spr,hf,cx,ft)))
    except Exception as e: print("skip",lab,repr(e)[:80],flush=True)
cols=2; import math; rows=math.ceil(len(imgs)/cols); cw=600; ch=int(cw*832/1216)
from PIL import ImageDraw
sheet=Image.new("RGB",(cols*cw,rows*(ch+22)+6),(24,22,26)); dr=ImageDraw.Draw(sheet)
for k,(lab,im) in enumerate(imgs):
    t=im.copy(); t.thumbnail((cw-8,ch)); r,c=divmod(k,cols); ox=c*cw+4; oy=r*(ch+22)+20
    dr.text((ox,oy-16),lab,fill=(235,210,160)); sheet.paste(t,(ox,oy))
sheet.save(os.path.join(PREV,"koroki_match_sheet.jpg"),quality=90)
print("MATCH TEST DONE",flush=True)
