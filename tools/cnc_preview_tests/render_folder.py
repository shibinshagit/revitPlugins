# -*- coding: utf-8 -*-
"""Screenshot every CNC program in a folder through the real preview window.

    netcore_host.exe <engine> render_folder.py <lib> <folder> <out>

For checking a program exported from a model by eye: each file is drawn
whole, then once per sloping member zoomed onto its ends.
"""
import sys

import clr

clr.AddReference(MODULES)
from IronPython.Modules import ResourceMetaPathImporter

sys.meta_path.append(
    ResourceMetaPathImporter(LOADER, "pyRevitLoader.python_2712pr_lib.zip")
)
sys.path.insert(0, LIB)

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Action
from System.IO import Directory, File, FileMode, FileStream, Path
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import BitmapFrame, PngBitmapEncoder, RenderTargetBitmap
from System.Windows.Threading import DispatcherPriority

import uniqube_cnc_preview as preview
import uniqube_cnc_window as cnc_window

paths = sorted(Directory.GetFiles(SAMPLES, "*.csv"))
UNITS = [{"name": Path.GetFileNameWithoutExtension(p), "members": [None], "csv": p}
         for p in paths]


def provider(unit, job_name):
    text = File.ReadAllText(unit["csv"])
    program = preview.parse_program(text)
    return {"filename": Path.GetFileName(unit["csv"]), "text": text,
            "count": len(program["components"])}


window = cnc_window.CncPreviewWindow(UNITS, provider, "", OUT, checked_names=[])
win = window.window


def settle():
    win.Dispatcher.Invoke(DispatcherPriority.ApplicationIdle, Action(lambda: None))


def shot(name):
    settle()
    root = window.viewport
    bitmap = RenderTargetBitmap(int(root.ActualWidth) * 2, int(root.ActualHeight) * 2,
                                192.0, 192.0, PixelFormats.Pbgra32)
    bitmap.Render(root)
    encoder = PngBitmapEncoder()
    encoder.Frames.Add(BitmapFrame.Create(bitmap))
    path = Path.Combine(OUT, name + ".png")
    stream = FileStream(path, FileMode.Create)
    try:
        encoder.Save(stream)
    finally:
        stream.Close()
    print("shot  " + path)


win.Show()
settle()
for number, unit in enumerate(UNITS):
    window.unit_list.SelectedItem = window._row_view(number)
    settle()
    window._fit(rebuild=True)
    stem = "render_" + unit["name"]
    shot(stem)
    components = window._entry_now["program"]["components"]
    sloped = [c for c in components if 0.2 < abs(c["uy"]) < 0.98]
    for comp in sloped[:2]:
        for end, station in (("a", 0.0), ("b", comp["span"])):
            x, y = preview.point_at(comp, station)
            window._fit(rebuild=True)
            settle()
            sx, sy = x * window._scale + window._tx, -y * window._scale + window._ty
            window._zoom_at(sx, sy, 6.0)
            shot("{}_{}_{}".format(stem, comp["label"], end))
        window._select(components.index(comp))
        settle()
        shot("{}_{}_alone".format(stem, comp["label"]))
        window._select(None)
        settle()
win.Close()
FAILURES = 0
