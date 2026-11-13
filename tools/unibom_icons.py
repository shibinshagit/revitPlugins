# -*- coding: utf-8 -*-
"""Draw the UNIBOM ribbon icons in the UNIQUBE illustration style.

Flat colour, thick dark outline, teal / orange / coral palette, 512 px PNG
with 96 DPI metadata (pyRevit scales it for the ribbon). Original artwork,
drawn in code so it can be regenerated or restyled.

    pip install pillow
    python3 tools/unibom_icons.py
"""
from __future__ import division, print_function

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Uniqube.extension")
TAB = os.path.join(ROOT, "UNIBOM.tab")

S = 1024                 # draw at 2x, downsample to 512 for clean edges
OUT = 512
INK = (24, 26, 30, 255)
LINE = 30
TEAL = (64, 178, 160, 255)
TEAL_DARK = (0, 150, 128, 255)
MINT = (220, 242, 240, 255)
ORANGE = (247, 150, 30, 255)
AMBER = (250, 184, 48, 255)
CORAL = (240, 96, 84, 255)
PAPER = (250, 250, 248, 255)
GREY = (200, 204, 210, 255)


def canvas():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def rrect(d, box, r, fill, width=LINE):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=INK, width=width)


def poly(d, pts, fill, width=LINE):
    d.polygon(pts, fill=fill)
    d.line(list(pts) + [pts[0], pts[1]], fill=INK, width=width, joint="curve")


def thick(d, a, b, width, fill=INK):
    d.line([a, b], fill=fill, width=width)
    for x, y in (a, b):
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=fill)


def circle(d, cx, cy, r, fill, width=LINE):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=INK, width=width)


def check(d, cx, cy, size, fill=INK, width=44):
    a = (cx - size * 0.55, cy + size * 0.02)
    b = (cx - size * 0.12, cy + size * 0.42)
    c = (cx + size * 0.6, cy - size * 0.38)
    thick(d, a, b, width, fill)
    thick(d, b, c, width, fill)


def gear(d, cx, cy, r, teeth, fill):
    pts = []
    for i in range(teeth * 2):
        ang0 = math.pi * i / teeth
        rad = r if i % 2 == 0 else r * 0.78
        for k in (-0.32, 0.32):
            ang = ang0 + k * math.pi / teeth
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    poly(d, pts, fill)
    circle(d, cx, cy, r * 0.32, PAPER)


def save(img, rel):
    small = img.resize((OUT, OUT), Image.LANCZOS)
    path = os.path.join(TAB, rel, "icon.png")
    small.save(path, "PNG", dpi=(96, 96))
    print("wrote", os.path.relpath(path, ROOT))
    return small


# --- icons ----------------------------------------------------------------------
def run_boq():
    """BOQ sheet with a table, and an orange run badge."""
    img, d = canvas()
    rrect(d, [150, 90, 760, 900], 56, PAPER)
    d.rounded_rectangle([150 + LINE // 2, 90 + LINE // 2, 760 - LINE // 2, 250], radius=40, fill=TEAL)
    d.rectangle([150 + LINE // 2, 200, 760 - LINE // 2, 250], fill=TEAL)
    d.line([(150, 250), (760, 250)], fill=INK, width=LINE)
    for i, y in enumerate((340, 450, 560, 670)):
        thick(d, (230, y), (300, y), 34, CORAL if i == 0 else TEAL_DARK)
        thick(d, (370, y), (680, y), 34, GREY)
    circle(d, 740, 760, 190, AMBER)
    tri = [(690, 660), (690, 860), (850, 760)]
    d.polygon(tri, fill=INK)
    return save(img, "Takeoff.panel/RunBOQ.pushbutton")


def check_model():
    """Steel frame panel under a magnifier with a check."""
    img, d = canvas()
    rrect(d, [110, 170, 650, 860], 40, MINT)
    for x in (245, 380, 515):
        d.line([(x, 185), (x, 845)], fill=TEAL_DARK, width=34)
    d.line([(125, 400), (635, 400)], fill=TEAL_DARK, width=26)
    d.line([(125, 630), (635, 630)], fill=TEAL_DARK, width=26)
    d.rounded_rectangle([110, 170, 650, 860], radius=40, outline=INK, width=LINE)
    thick(d, (760, 760), (930, 930), 96)
    thick(d, (760, 760), (930, 930), 50, CORAL)
    circle(d, 650, 560, 250, PAPER, width=48)
    d.ellipse([430, 340, 870, 780], outline=AMBER, width=26)
    check(d, 650, 560, 190, TEAL_DARK, 56)
    return save(img, "Setup.panel/CheckModel.pushbutton")


def rules_folder():
    """Orange folder, rule sheet, teal gear."""
    img, d = canvas()
    poly(d, [(90, 300), (90, 860), (880, 860), (880, 360), (470, 360), (400, 260), (130, 260)], ORANGE)
    rrect(d, [220, 150, 700, 620], 40, PAPER)
    for y in (260, 350, 440):
        thick(d, (300, y), (620, y), 30, GREY)
    poly(d, [(90, 450), (880, 450), (880, 860), (90, 860)], AMBER)
    gear(d, 700, 680, 200, 8, TEAL)
    return save(img, "Setup.panel/RulesFolder.pushbutton")


def reports_folder():
    """Report sheet with a bar chart in front of a folder."""
    img, d = canvas()
    poly(d, [(80, 280), (80, 860), (900, 860), (900, 340), (480, 340), (410, 240), (120, 240)], ORANGE)
    rrect(d, [250, 140, 790, 820], 44, PAPER)
    base = 700
    for x, h, c in ((340, 220, TEAL), (450, 360, AMBER), (560, 160, CORAL), (670, 290, TEAL_DARK)):
        rrect(d, [x - 40, base - h, x + 40, base], 14, c, width=22)
    d.line([(300, base + 12), (740, base + 12)], fill=INK, width=24)
    thick(d, (320, 250), (560, 250), 30, GREY)
    return save(img, "Output.panel/ReportsFolder.pushbutton")


if __name__ == "__main__":
    sheet = Image.new("RGBA", (4 * 280, 280), (40, 42, 48, 255))
    sheet_light = Image.new("RGBA", (4 * 280, 280), (240, 240, 240, 255))
    for i, fn in enumerate((check_model, rules_folder, run_boq, reports_folder)):
        icon = fn().resize((256, 256), Image.LANCZOS)
        sheet.paste(icon, (i * 280 + 12, 12), icon)
        sheet_light.paste(icon, (i * 280 + 12, 12), icon)
    preview = Image.new("RGBA", (4 * 280, 560))
    preview.paste(sheet_light, (0, 0))
    preview.paste(sheet, (0, 280))
    preview.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "unibom_icons_preview.png"))
