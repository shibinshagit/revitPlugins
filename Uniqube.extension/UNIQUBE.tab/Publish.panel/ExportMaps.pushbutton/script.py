# -*- coding: utf-8 -*-
"""Export BIMSF + colour maps as JSON. No IFC, no drawings, no upload."""
from __future__ import print_function

__title__ = "Export Maps"
__doc__ = (
    "Write BIMSF_Container and colour maps to JSON on disk.\n\n"
    "Use this when a full Publish is too slow (AnyDesk / large models).\n"
    "Send the JSON files to Uniqube — they merge onto an already-uploaded project.\n\n"
    "MEP: link Structure, run Fill BIMSF, then export from the MEP file."
)
__author__ = "Uniqube"

import uniqube_sidecar_export

if __name__ == "__main__":
    uniqube_sidecar_export.run()
