# -*- coding: utf-8 -*-
"""Generate UNIQUBE pyRevit ribbon icons from Fluent UI (via Iconify API).

Icons: Microsoft Fluent UI System Icons (MIT)
https://github.com/microsoft/fluentui-system-icons

Revit / pyRevit require PNG icons with 96 DPI metadata. Without it, buttons
fall back to text initials (AP, BOM, ML, etc.).

Usage:
    pip install -r tools/requirements-icons.txt
    python3 tools/generate_icons.py
"""
from __future__ import print_function

import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
from svg.path import parse_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 96
GLYPH = 64
PAD = (SIZE - GLYPH) // 2
VIEW = 24.0
DPI = (96, 96)

BG = (26, 54, 93)
BG_ACCENT = (0, 120, 120)
ICONIFY = "https://api.iconify.design/fluent/{name}.svg?color=%23ffffff"
CACHE = os.path.join(os.path.dirname(__file__), "icon_cache")

ICON_MAP = {
    "UNIQUBE.tab/MEP.panel/MEPGroupPanels.pushbutton": (
        "apps-list-24-regular", True
    ),
    "UNIQUBE.tab/MEP.panel/FillMEPContainers.pushbutton": (
        "tag-24-regular", False
    ),
    "UNIQUBE.tab/MEP.panel/SyncPanelSelection.pushbutton": (
        "arrow-sync-24-regular", False
    ),
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/ConduitFittingSched.pushbutton": (
        "plug-connected-24-regular", False
    ),
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/ElecFixtureSched.pushbutton": (
        "lightbulb-24-regular", False
    ),
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/PipeFittingSched.pushbutton": (
        "pipeline-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown/SetupBIMSF.pushbutton": (
        "settings-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown/IFCPanelMapper.pushbutton": (
        "map-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/GroupPanels.pushbutton": (
        "group-list-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/UngroupPanels.pushbutton": (
        "group-dismiss-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/PanelCombineColor.pushbutton": (
        "color-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/PanelCombineAssembly.pushbutton": (
        "cube-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/CreateAssemblies.pushbutton": (
        "stack-add-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/AssemblyShopDrawing.pushbutton": (
        "print-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/AdvancePositionID.pushbutton": (
        "number-symbol-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/MasterPanelList.pushbutton": (
        "table-24-regular", True
    ),
    "UNIQUBE.tab/Structural.panel/BOMExtraction.pushbutton": (
        "document-table-24-regular", False
    ),
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown": (
        "table-multiple-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown": (
        "settings-24-regular", False
    ),
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown": (
        "group-list-24-regular", True
    ),
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown": (
        "cube-24-regular", False
    ),
    "UNIQUBE.tab": ("grid-24-regular", True),
}


def _fetch_svg(slug):
    os.makedirs(CACHE, exist_ok=True)
    cache_path = os.path.join(CACHE, slug + ".svg")
    if os.path.isfile(cache_path):
        with open(cache_path, "rb") as handle:
            return handle.read()

    url = ICONIFY.format(name=slug)
    req = urllib.request.Request(
        url, headers={"User-Agent": "UniqubeIconGenerator/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as ex:
        raise RuntimeError("Icon not found: {} ({})".format(slug, ex))

    with open(cache_path, "wb") as handle:
        handle.write(data)
    return data


def _path_polygons(svg_bytes):
    root = ET.fromstring(svg_bytes)
    polygons = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag != "path":
            continue
        d_attr = elem.get("d")
        if not d_attr:
            continue
        parsed = parse_path(d_attr)
        flat = []
        for seg in parsed:
            length = max(getattr(seg, "length", lambda: 1.0)(), 0.01)
            steps = max(int(length * 3), 6)
            for i in range(steps + 1):
                pt = seg.point(i / float(steps))
                flat.append((pt.real, pt.imag))
        if len(flat) >= 3:
            polygons.append(flat)
    return polygons


def _render_glyph(svg_bytes):
    scale = GLYPH / VIEW
    img = Image.new("RGBA", (GLYPH, GLYPH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for poly in _path_polygons(svg_bytes):
        scaled = [(x * scale, y * scale) for x, y in poly]
        draw.polygon(scaled, fill=(255, 255, 255, 255))
    return img


def _compose(svg_bytes, accent=False):
    bg = BG_ACCENT if accent else BG
    canvas = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    draw = ImageDraw.Draw(canvas)
    radius = SIZE // 5
    draw.rounded_rectangle(
        [2, 2, SIZE - 3, SIZE - 3], radius=radius, fill=bg + (255,)
    )
    glyph = _render_glyph(svg_bytes)
    canvas.paste(glyph, (PAD, PAD), glyph)
    rgb = Image.new("RGB", (SIZE, SIZE), bg)
    rgb.paste(canvas, mask=canvas.split()[3])
    return rgb


def _save(rel_path, img):
    folder = os.path.join(ROOT, rel_path)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "icon.png")
    img.save(path, "PNG", dpi=DPI)
    print("wrote", path, img.size, "dpi=96")


def main():
    errors = []
    for rel, (slug, accent) in ICON_MAP.items():
        try:
            svg = _fetch_svg(slug)
            _save(rel, _compose(svg, accent=accent))
        except Exception as ex:
            errors.append("{}: {}".format(rel, ex))
            print("ERROR", rel, ex)

    if errors:
        print("\n{} icon(s) failed.".format(len(errors)))
        raise SystemExit(1)
    print("\nDone — {} Fluent UI icons at 96x96 / 96 DPI.".format(len(ICON_MAP)))


if __name__ == "__main__":
    main()
