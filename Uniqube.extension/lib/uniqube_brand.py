# -*- coding: utf-8 -*-
"""UniQube product look for Revit plugin windows.

Matches the marketing site and software: charcoal header, orange accent,
navy actions, and the stacked UNI / QUBE mark (never plain \"UNIQUBE\" text).
"""
from __future__ import print_function

import os

import clr

clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

# Product colours (web-uniqube globals.css + Revit dark chrome).
ORANGE = "#FFF7902C"
ORANGE_HOVER = "#FFFFA94D"
ORANGE_PRESSED = "#FFD97B0C"
NAVY = "#FF03061E"
INK = "#FF1D1D1F"
HEADER = "#FF16171B"
HEADER_SURFACE = "#FF25282F"
MUTE = "#FF86868B"
PAPER = "#FFFFFFFF"

LOGO_LIGHT = os.path.join(UI_DIR, "logo.png")
LOGO_DARK = os.path.join(UI_DIR, "logo.dark.png")
LOGO_HEADER = os.path.join(UI_DIR, "logo.header.png")
LOGO_HEADER_LIGHT = os.path.join(UI_DIR, "logo.header.light.png")


def logo_path(dark=True, header=True):
    """Absolute path to the UniQube mark for a given surface."""
    if header:
        path = LOGO_HEADER if dark else LOGO_HEADER_LIGHT
    else:
        path = LOGO_DARK if dark else LOGO_LIGHT
    if os.path.isfile(path):
        return path
    fallback = LOGO_DARK if dark else LOGO_LIGHT
    return fallback if os.path.isfile(fallback) else path


def load_logo(image, dark=True, header=True):
    """Put the UniQube mark into a WPF Image. Returns True when it loaded."""
    if image is None:
        return False
    path = logo_path(dark=dark, header=header)
    if not os.path.isfile(path):
        return False
    stream = None
    try:
        from System.IO import FileMode, FileStream, FileAccess

        stream = FileStream(path, FileMode.Open, FileAccess.Read)
        bmp = BitmapImage()
        bmp.BeginInit()
        bmp.StreamSource = stream
        bmp.CacheOption = BitmapCacheOption.OnLoad
        bmp.EndInit()
        try:
            bmp.Freeze()
        except Exception:
            pass
        image.Source = bmp
        return True
    except Exception:
        return False
    finally:
        if stream is not None:
            try:
                stream.Close()
            except Exception:
                pass
