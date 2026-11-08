#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「局域网互传」品牌图标的所有位图。

用法（仓库根目录）:
  python assets/branding/make_icons.py

输出:
  assets/branding/icon-1024.png   1024×1024 PNG 源图
  assets/branding/icon.ico        16/32/48/64/128/256 多尺寸 ICO
  assets/branding/favicon.ico     16/32/48 网页 favicon
  assets/branding/favicon-32.png
  assets/branding/logo-96.png     网页标题 logo（2x/3x 屏用）
  assets/branding/logo-192.png
  assets/branding/brand-256.png   Tk iconphoto 用
  desktop-client/icon.ico         PyInstaller --icon
  desktop-client/static/*.png|ico|svg
  android-app/app/src/main/res/mipmap-*/ic_launcher.png

配色与几何都集中在下面几个常量里，改配色只改这里 + icon.svg。
"""

import os
import shutil
import struct
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DESKTOP = os.path.join(ROOT, "desktop-client")
STATIC = os.path.join(DESKTOP, "static")
ANDROID_RES = os.path.join(ROOT, "android-app", "app", "src", "main", "res")

# --------------------------- 配色 --------------------------- #
BG_TOP_LEFT = (28, 39, 71)
BG_TOP_RIGHT = (22, 32, 59)
BG_BOTTOM_LEFT = (16, 23, 40)
BG_BOTTOM_RIGHT = (11, 16, 32)
DEVICE_BODY = (234, 240, 255)
DEVICE_SCREEN = (18, 26, 49)
ACCENT = (91, 140, 255)          # #5B8CFF
ACCENT_LIGHT = (126, 165, 255)   # #7EA5FF
ACCENT2 = (49, 214, 176)         # #31D6B0
ACCENT2_LIGHT = (78, 230, 194)   # #4EE6C2
BORDER = (255, 255, 255, 26)

MASTER = 2048          # 以 1024 坐标系绘制，放大 2 倍再降采样，保证小尺寸不糊
SCALE = MASTER / 1024.0


def _s(v):
    return int(round(v * SCALE))


def gradient(size):
    """对角线性渐变（2×2 拉伸，够平滑且无需 numpy）。"""
    base = Image.new("RGB", (2, 2))
    base.putpixel((0, 0), BG_TOP_LEFT)
    base.putpixel((1, 0), BG_TOP_RIGHT)
    base.putpixel((0, 1), BG_BOTTOM_LEFT)
    base.putpixel((1, 1), BG_BOTTOM_RIGHT)
    return base.resize((size, size), Image.BICUBIC)


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=int(radius * SCALE), fill=255)
    return mask


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle([_s(box[0]), _s(box[1]), _s(box[2]), _s(box[3])],
                           radius=_s(radius), fill=fill)


def arrow(draw, direction, shaft_from, shaft_to, center_y,
          shaft_half=42, head_half=112, head_len=76):
    """画一个带尖头的粗箭头。direction=1 指向右，-1 指向左。"""
    tip_x = shaft_to + direction * head_len
    base_x = shaft_to
    pts = [
        (shaft_from, center_y - shaft_half),
        (base_x, center_y - shaft_half),
        (base_x, center_y - head_half),
        (tip_x, center_y),
        (base_x, center_y + head_half),
        (base_x, center_y + shaft_half),
        (shaft_from, center_y + shaft_half),
    ]
    draw.polygon([(_s(x), _s(y)) for x, y in pts], fill=ACCENT if direction > 0 else ACCENT2)


def render_master():
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    img.paste(gradient(MASTER), (0, 0), rounded_mask(MASTER, 210))
    draw = ImageDraw.Draw(img)

    # 内描边
    draw.rounded_rectangle([_s(6), _s(6), _s(1018), _s(1018)],
                           radius=_s(204), outline=BORDER, width=_s(8))

    # 左：手机
    rounded_rect(draw, (128, 296, 244, 728), 42, DEVICE_BODY)
    rounded_rect(draw, (144, 320, 228, 676), 26, DEVICE_SCREEN)
    draw.rounded_rectangle([_s(170), _s(690), _s(202), _s(700)],
                           radius=_s(5), fill=ACCENT)

    # 右：平板
    rounded_rect(draw, (764, 340, 896, 684), 44, DEVICE_BODY)
    rounded_rect(draw, (782, 364, 878, 632), 26, DEVICE_SCREEN)
    draw.ellipse([_s(821), _s(645), _s(839), _s(663)], fill=ACCENT2)

    # 双向箭头
    arrow(draw, +1, 292, 640, 412)
    arrow(draw, -1, 716, 368, 612)
    return img


def save(img, path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = img.resize((size, size), Image.LANCZOS)
    out.save(path)


def render_simplified():
    """小尺寸（16/24px）专用：去掉两台设备，只保留更粗的双向箭头。

    16px 下设备只剩 2px 宽，和箭头挤在一起会糊成一团，因此单独出一版。
    """
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    img.paste(gradient(MASTER), (0, 0), rounded_mask(MASTER, 210))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([_s(6), _s(6), _s(1018), _s(1018)],
                           radius=_s(204), outline=BORDER, width=_s(8))
    arrow(draw, +1, 118, 560, 398, shaft_half=62, head_half=150, head_len=112)
    arrow(draw, -1, 906, 464, 626, shaft_half=62, head_half=150, head_len=112)
    return img


def write_ico(path, entries):
    """写多尺寸 ICO（32 位 BMP 条目），entries=[(size, PIL.Image)]。

    自己写容器而不是用 Pillow 的 sizes=，是为了让 16/24 用简化图形、
    32 以上用完整图形——同一个 ICO 里混排不同设计。
    """
    dir_entries = []
    payload = []
    offset = 6 + 16 * len(entries)
    for size, image in entries:
        rgba = image.convert("RGBA").resize((size, size), Image.LANCZOS)
        pixels = rgba.load()
        xor = bytearray()
        for y in range(size - 1, -1, -1):
            for x in range(size):
                r, g, b, a = pixels[x, y]
                xor += bytes((b, g, r, a))
        mask_row = ((size + 31) // 32) * 4
        and_mask = bytes(mask_row * size)
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                             len(xor), 0, 0, 0, 0)
        blob = header + bytes(xor) + and_mask
        dir_entries.append(struct.pack("<BBBBHHII",
                                       0 if size >= 256 else size,
                                       0 if size >= 256 else size,
                                       0, 0, 1, 32, len(blob), offset))
        payload.append(blob)
        offset += len(blob)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(entries)))
        fh.write(b"".join(dir_entries))
        for blob in payload:
            fh.write(blob)


def main():
    master = render_master()
    small = render_simplified()

    # 1024 PNG 源图
    save(master, os.path.join(HERE, "icon-1024.png"), 1024)

    # ICO：16/24 用简化图形，32 以上用完整图形
    ico_path = os.path.join(HERE, "icon.ico")
    write_ico(ico_path, [(16, small), (24, small), (32, master), (48, master),
                         (64, master), (128, master), (256, master)])

    # 网页 favicon / logo
    save(master, os.path.join(HERE, "favicon-32.png"), 32)
    save(master, os.path.join(HERE, "logo-96.png"), 96)
    save(master, os.path.join(HERE, "logo-192.png"), 192)
    save(master, os.path.join(HERE, "logo-48.png"), 48)
    save(master, os.path.join(HERE, "logo-64.png"), 64)
    save(master, os.path.join(HERE, "brand-256.png"), 256)
    write_ico(os.path.join(HERE, "favicon.ico"),
              [(16, small), (32, master), (48, master)])

    # 桌面端：PyInstaller 图标 + 静态资源
    shutil.copyfile(ico_path, os.path.join(DESKTOP, "icon.ico"))
    for name in ("favicon.ico", "favicon-32.png", "logo-96.png"):
        shutil.copyfile(os.path.join(HERE, name), os.path.join(STATIC, name))
    save(master, os.path.join(STATIC, "brand-48.png"), 48)
    save(master, os.path.join(STATIC, "brand-256.png"), 256)
    save(master, os.path.join(STATIC, "logo-192.png"), 192)
    svg_src = os.path.join(HERE, "icon.svg")
    if os.path.isfile(svg_src):
        shutil.copyfile(svg_src, os.path.join(STATIC, "favicon.svg"))

    # 安卓各密度 launcher icon（48/72/96/144/192）
    for folder, size in (("mipmap-mdpi", 48), ("mipmap-hdpi", 72),
                         ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144),
                         ("mipmap-xxxhdpi", 192)):
        target = os.path.join(ANDROID_RES, folder, "ic_launcher.png")
        if not os.path.isdir(os.path.dirname(target)):
            continue
        save(master, target, size)
        print("android:", os.path.relpath(target, ROOT), size)

    print("ok:", ico_path)


if __name__ == "__main__":
    main()
