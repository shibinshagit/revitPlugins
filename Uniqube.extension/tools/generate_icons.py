# -*- coding: utf-8 -*-
"""Generate 32x32 PNG icons for UNIQUBE pyRevit pushbuttons."""
from __future__ import print_function

import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 32

# Uniqube brand palette
BG = (26, 54, 93)          # navy
BG_ACCENT = (0, 120, 120)  # teal
FG = (255, 255, 255)
FG_DIM = (180, 210, 230)
RED = (220, 60, 60)
GREEN = (80, 180, 100)
AMBER = (240, 180, 60)


def _base(accent=False):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, 30, 30], radius=6, fill=BG_ACCENT if accent else BG)
    return img, d


def _flatten(img):
    """Revit ribbon icons must be opaque RGB — transparent PNGs may not show."""
    out = Image.new("RGB", (SIZE, SIZE), BG)
    if img.mode == "RGBA":
        out.paste(img, (0, 0), img)
    else:
        out.paste(img, (0, 0))
    return out


def _save(rel_path, img):
    folder = os.path.join(ROOT, rel_path)
    os.makedirs(folder, exist_ok=True)
    flat = _flatten(img)
    for name in ("icon.png", "icon32.png"):
        path = os.path.join(folder, name)
        flat.save(path, "PNG")
        print("wrote", path)


def icon_prepare_mep():
    img, d = _base(accent=True)
    d.rectangle([6, 8, 13, 24], outline=FG, width=1)
    d.rectangle([19, 8, 26, 24], outline=FG, width=1)
    d.line([9, 14, 9, 20], fill=GREEN, width=2)
    d.line([22, 14, 22, 20], fill=GREEN, width=2)
    d.line([13, 17, 19, 17], fill=RED, width=2)
    return img


def icon_fill_bimsf():
    img, d = _base()
    d.polygon([(16, 5), (26, 10), (26, 22), (16, 27), (6, 22), (6, 10)], outline=FG, width=1)
    d.line([10, 14, 22, 14], fill=FG_DIM, width=1)
    d.line([10, 18, 20, 18], fill=FG_DIM, width=1)
    d.rectangle([11, 10, 15, 12], fill=AMBER)
    return img


def icon_sync():
    img, d = _base()
    d.arc([7, 7, 19, 19], 200, 340, fill=FG, width=2)
    d.polygon([(18, 8), (22, 6), (20, 12)], fill=FG)
    d.arc([13, 13, 25, 25], 20, 160, fill=FG_DIM, width=2)
    d.polygon([(14, 24), (10, 26), (12, 20)], fill=FG_DIM)
    return img


def icon_schedule_conduit():
    img, d = _base()
    d.rectangle([5, 5, 27, 27], outline=FG, width=1)
    d.line([5, 11, 27, 11], fill=FG_DIM, width=1)
    d.line([5, 17, 27, 17], fill=FG_DIM, width=1)
    d.line([14, 5, 14, 27], fill=FG_DIM, width=1)
    d.ellipse([17, 20, 25, 26], outline=AMBER, width=2)
    return img


def icon_schedule_elec():
    img, d = _base()
    d.rectangle([5, 5, 27, 27], outline=FG, width=1)
    d.line([5, 11, 27, 11], fill=FG_DIM, width=1)
    d.line([5, 17, 27, 17], fill=FG_DIM, width=1)
    d.polygon([(20, 19), (17, 26), (21, 26), (18, 19)], fill=AMBER)
    return img


def icon_schedule_pipe():
    img, d = _base()
    d.rectangle([5, 5, 27, 27], outline=FG, width=1)
    d.line([5, 11, 27, 11], fill=FG_DIM, width=1)
    d.line([5, 17, 27, 17], fill=FG_DIM, width=1)
    d.line([17, 20, 25, 20], fill=GREEN, width=3)
    d.ellipse([15, 18, 19, 22], outline=GREEN, width=1)
    return img


def icon_setup_bimsf():
    img, d = _base()
    d.ellipse([8, 8, 24, 24], outline=FG, width=1)
    d.line([16, 11, 16, 16], fill=FG, width=2)
    d.rectangle([14, 16, 18, 18], fill=AMBER)
    d.line([11, 21, 21, 21], fill=FG_DIM, width=1)
    return img


def icon_ifc_map():
    img, d = _base()
    d.rectangle([5, 10, 14, 22], outline=FG, width=1)
    d.rectangle([18, 10, 27, 22], outline=FG, width=1)
    d.line([14, 16, 18, 16], fill=AMBER, width=2)
    d.polygon([(16, 14), (20, 16), (16, 18)], fill=AMBER)
    return img


def icon_group():
    img, d = _base()
    d.rectangle([6, 7, 14, 15], outline=FG, width=1)
    d.rectangle([18, 7, 26, 15], outline=FG, width=1)
    d.rectangle([6, 17, 14, 25], outline=FG, width=1)
    d.rectangle([18, 17, 26, 25], outline=FG, width=1)
    d.rectangle([4, 5, 28, 27], outline=GREEN, width=1)
    return img


def icon_ungroup():
    img, d = _base()
    d.rectangle([5, 5, 13, 13], outline=FG, width=1)
    d.rectangle([19, 5, 27, 13], outline=FG, width=1)
    d.rectangle([5, 19, 13, 27], outline=FG, width=1)
    d.rectangle([19, 19, 27, 27], outline=FG, width=1)
    d.line([13, 9, 19, 9], fill=RED, width=1)
    d.line([13, 23, 19, 23], fill=RED, width=1)
    return img


def icon_combine_color():
    img, d = _base()
    d.rectangle([5, 14, 11, 20], fill=RED)
    d.rectangle([13, 14, 19, 20], fill=GREEN)
    d.rectangle([21, 14, 27, 20], fill=AMBER)
    d.rectangle([8, 6, 24, 12], outline=FG, width=1)
    d.line([10, 9, 22, 9], fill=FG_DIM, width=1)
    return img


def icon_combine_asm():
    img, d = _base()
    d.polygon([(16, 5), (26, 11), (26, 23), (16, 29), (6, 23), (6, 11)], outline=FG, width=1)
    d.line([16, 11, 16, 23], fill=FG_DIM, width=1)
    d.line([10, 14, 22, 14], fill=FG_DIM, width=1)
    return img


def icon_create_asm():
    img, d = _base()
    d.rectangle([8, 8, 24, 24], outline=FG, width=1)
    d.line([16, 11, 16, 21], fill=GREEN, width=2)
    d.line([11, 16, 21, 16], fill=GREEN, width=2)
    return img


def icon_shop_draw():
    img, d = _base()
    d.rectangle([6, 6, 26, 26], outline=FG, width=1)
    d.rectangle([9, 9, 23, 18], outline=FG_DIM, width=1)
    d.line([9, 21, 20, 21], fill=FG_DIM, width=1)
    d.line([9, 24, 16, 24], fill=FG_DIM, width=1)
    return img


def icon_position_id():
    img, d = _base()
    d.line([8, 11, 8, 18], fill=FG, width=2)
    d.line([8, 11, 12, 11], fill=FG, width=2)
    d.line([8, 14, 11, 14], fill=FG, width=2)
    d.ellipse([13, 11, 17, 18], outline=FG, width=2)
    d.line([19, 18, 19, 11], fill=FG, width=2)
    d.line([19, 11, 23, 11], fill=FG, width=2)
    d.line([19, 14, 22, 14], fill=FG, width=2)
    d.line([19, 18, 23, 18], fill=FG, width=2)
    d.line([6, 22, 26, 22], fill=AMBER, width=2)
    return img


def icon_master_list():
    img, d = _base(accent=True)
    d.rectangle([5, 4, 27, 28], outline=FG, width=2)
    d.line([5, 10, 27, 10], fill=FG, width=2)
    d.line([12, 4, 12, 28], fill=FG_DIM, width=1)
    for y in (14, 18, 23):
        d.line([14, y, 26, y], fill=FG, width=1)
    d.rectangle([7, 6, 11, 9], fill=AMBER)
    return img


def icon_bom():
    img, d = _base()
    d.rectangle([7, 5, 25, 27], outline=FG, width=1)
    d.line([7, 11, 25, 11], fill=FG, width=1)
    d.line([10, 14, 22, 14], fill=FG_DIM, width=1)
    d.line([10, 17, 22, 17], fill=FG_DIM, width=1)
    d.line([10, 20, 18, 20], fill=FG_DIM, width=1)
    d.rectangle([10, 7, 14, 9], fill=AMBER)
    return img


def icon_pulldown_schedules():
    img, d = _base()
    d.rectangle([6, 6, 26, 26], outline=FG, width=1)
    d.line([6, 12, 26, 12], fill=FG_DIM, width=1)
    d.line([6, 18, 26, 18], fill=FG_DIM, width=1)
    d.line([14, 6, 14, 26], fill=FG_DIM, width=1)
    return img


def icon_pulldown_panels():
    img, d = _base(accent=True)
    d.rectangle([7, 7, 25, 25], outline=FG, width=1)
    d.line([7, 13, 25, 13], fill=FG_DIM, width=1)
    d.line([7, 19, 25, 19], fill=FG_DIM, width=1)
    d.line([15, 7, 15, 25], fill=FG_DIM, width=1)
    return img


def icon_pulldown_assembly():
    img, d = _base()
    d.polygon([(16, 6), (25, 12), (25, 22), (16, 28), (7, 22), (7, 12)], outline=FG, width=1)
    d.ellipse([13, 14, 19, 20], outline=GREEN, width=1)
    return img


def icon_pulldown_setup():
    img, d = _base()
    d.rectangle([9, 8, 23, 24], outline=FG, width=1)
    d.line([12, 12, 20, 12], fill=FG_DIM, width=1)
    d.line([12, 16, 20, 16], fill=FG_DIM, width=1)
    d.rectangle([12, 19, 16, 21], fill=AMBER)
    return img


ICONS = {
    "UNIQUBE.tab/MEP.panel/MEPGroupPanels.pushbutton": icon_prepare_mep,
    "UNIQUBE.tab/MEP.panel/FillMEPContainers.pushbutton": icon_fill_bimsf,
    "UNIQUBE.tab/MEP.panel/SyncPanelSelection.pushbutton": icon_sync,
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/ConduitFittingSched.pushbutton": icon_schedule_conduit,
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/ElecFixtureSched.pushbutton": icon_schedule_elec,
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown/PipeFittingSched.pushbutton": icon_schedule_pipe,
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown/SetupBIMSF.pushbutton": icon_setup_bimsf,
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown/IFCPanelMapper.pushbutton": icon_ifc_map,
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/GroupPanels.pushbutton": icon_group,
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/UngroupPanels.pushbutton": icon_ungroup,
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown/PanelCombineColor.pushbutton": icon_combine_color,
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/PanelCombineAssembly.pushbutton": icon_combine_asm,
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/CreateAssemblies.pushbutton": icon_create_asm,
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown/AssemblyShopDrawing.pushbutton": icon_shop_draw,
    "UNIQUBE.tab/Structural.panel/AdvancePositionID.pushbutton": icon_position_id,
    "UNIQUBE.tab/Structural.panel/MasterPanelList.pushbutton": icon_master_list,
    "UNIQUBE.tab/Structural.panel/BOMExtraction.pushbutton": icon_bom,
}


def icon_tab():
    img, d = _base(accent=True)
    d.rectangle([8, 8, 24, 24], outline=FG, width=2)
    d.line([8, 14, 24, 14], fill=FG_DIM, width=1)
    d.line([8, 20, 24, 20], fill=FG_DIM, width=1)
    d.line([16, 8, 16, 24], fill=FG_DIM, width=1)
    return img


PULLDOWN_ICONS = {
    "UNIQUBE.tab/MEP.panel/MEPSchedules.pulldown": icon_pulldown_schedules,
    "UNIQUBE.tab/Structural.panel/PanelSetup.pulldown": icon_pulldown_setup,
    "UNIQUBE.tab/Structural.panel/PanelGrouping.pulldown": icon_pulldown_panels,
    "UNIQUBE.tab/Structural.panel/PanelAssembly.pulldown": icon_pulldown_assembly,
}


def main():
    for rel, fn in ICONS.items():
        _save(rel, fn())
    for rel, fn in PULLDOWN_ICONS.items():
        _save(rel, fn())
    _save("UNIQUBE.tab", icon_tab())


if __name__ == "__main__":
    main()
