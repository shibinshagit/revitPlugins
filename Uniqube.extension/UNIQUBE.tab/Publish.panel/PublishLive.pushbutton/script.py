# -*- coding: utf-8 -*-
"""Publish to live / production Uniqube (ECS)."""
from __future__ import print_function

__title__ = "Publish Live"
__doc__ = (
    "Publish THIS Revit file to the LIVE Uniqube project API "
    "(https://api.uniqube3d.co).\n\n"
    "Typical Bathroom Pod flow:\n"
    "  - Structure file -> Publish as Structure\n"
    "  - MEP file: link Structure -> Prepare MEP Panels -> remove link -> "
    "Publish as MEP\n"
    "BIMSF_Container ids join Structure + MEP in the Uniqube 3D tree."
)
__author__ = "Uniqube"

import os
import sys

_lib = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.append(_lib)

print("Publish Live starting...")

if __name__ == "__main__":
    import uniqube_publish

    uniqube_publish.run_publish("live")
