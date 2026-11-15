#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画「小朋友」的图标：深底 + 一只话筒 + 两侧声波。
产物 mac/AppIcon.icns（以及 iconset/ 里的原图，方便改）。
"""
import os
import subprocess
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# 圆角底：竖向渐变
grad = Image.new("RGB", (1, S))
top, bot = (44, 52, 82), (17, 18, 27)
for y in range(S):
    t = y / (S - 1)
    grad.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
grad = grad.resize((S, S))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=228, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
W = (243, 245, 255, 255)
BLUE = (111, 141, 255, 220)

# 话筒头
d.rounded_rectangle([404, 250, 620, 610], radius=108, fill=W)
# 托架（U 形）
d.arc([286, 372, 738, 800], start=20, end=160, fill=W, width=46)
# 竖杆 + 底座
d.rounded_rectangle([492, 790, 532, 872], radius=20, fill=W)
d.rounded_rectangle([378, 866, 646, 908], radius=21, fill=W)
# 两侧声波
d.arc([120, 300, 470, 760], start=112, end=248, fill=BLUE, width=34)
d.arc([554, 300, 904, 760], start=292, end=68, fill=BLUE, width=34)

icon = img.resize((S, S), Image.LANCZOS)

iconset = os.path.join(HERE, "iconset")
os.makedirs(iconset, exist_ok=True)
pairs = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
         (256, 1), (256, 2), (512, 1), (512, 2)]
for size, scale in pairs:
    px = size * scale
    name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
    icon.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))

icon.save(os.path.join(HERE, "icon-1024.png"))

# 直接写 icns（本机 iconutil 认不出这套 iconset，自己拼更稳）
def png_bytes(size):
    import io
    b = io.BytesIO()
    icon.resize((size, size), Image.LANCZOS).save(b, format="PNG")
    return b.getvalue()

chunks = [(b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
          (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512)]
body = b""
for tag, size in chunks:
    data = png_bytes(size)
    body += tag + (len(data) + 8).to_bytes(4, "big") + data
out = os.path.join(HERE, "AppIcon.icns")
with open(out, "wb") as f:
    f.write(b"icns" + (len(body) + 8).to_bytes(4, "big") + body)
print("ok:", out, os.path.getsize(out), "bytes")
