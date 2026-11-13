# -*- coding: utf-8 -*-
"""Build UniQube plugin logos from the product mark.

Light: black wordmark + orange mark on transparent.
Dark / header: solid white wordmark + orange mark on transparent.
"""
from __future__ import print_function

import os

from PIL import Image

SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "uniqube-software", "uniqube", "public", "UniQube_Logo.png",
)
OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Uniqube.extension", "lib", "ui",
)


def is_paper(r, g, b, a):
    """Near-white / empty pixel that should become transparent."""
    return a < 8 or (r > 240 and g > 240 and b > 240)


def is_ink(r, g, b):
    """Near-black wordmark."""
    return r < 50 and g < 50 and b < 50


def is_orange(r, g, b):
    return r > 180 and 70 < g < 210 and b < 130


def trim(im):
    pixels = im.load()
    w, h = im.size
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if not is_paper(r, g, b, a):
                xs.append(x)
                ys.append(y)
    pad = 2
    box = (
        max(0, min(xs) - pad),
        max(0, min(ys) - pad),
        min(w, max(xs) + pad + 1),
        min(h, max(ys) + pad + 1),
    )
    return im.crop(box)


def rebuild(im, wordmark=(0, 0, 0, 255)):
    """Transparent canvas; orange mark kept; wordmark recolored solid."""
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    src, dst = im.load(), out.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            r, g, b, a = src[x, y]
            if is_paper(r, g, b, a):
                continue
            if is_orange(r, g, b):
                dst[x, y] = (r, g, b, 255)
            elif is_ink(r, g, b):
                dst[x, y] = wordmark
            else:
                # faint anti-alias on the mark / letter edges
                if r > 180 and g > 100 and b < 160:
                    dst[x, y] = (r, g, b, a if a else 255)
                else:
                    # blend ink AA toward the requested wordmark colour
                    wr, wg, wb, wa = wordmark
                    dst[x, y] = (wr, wg, wb, max(a, 180))
    return out


def save_scaled(im, name, width):
    ratio = width / float(im.size[0])
    size = (width, max(1, int(round(im.size[1] * ratio))))
    out = im.resize(size, Image.LANCZOS)
    path = os.path.join(OUT, name)
    out.save(path, "PNG")
    # prove corners are transparent
    px = out.getpixel((0, 0))
    print(name, out.size, "corner", px)


def main():
    raw = trim(Image.open(SRC).convert("RGBA"))
    light = rebuild(raw, wordmark=(29, 29, 31, 255))  # product ink
    dark = rebuild(raw, wordmark=(255, 255, 255, 255))
    save_scaled(light, "logo.png", 360)
    save_scaled(dark, "logo.dark.png", 360)
    save_scaled(dark, "logo.header.png", 200)
    save_scaled(light, "logo.header.light.png", 200)
    # Wider header mark so the stacked wordmark stays readable at ~36px height.
    save_scaled(dark, "logo.header.wide.png", 280)


if __name__ == "__main__":
    main()
