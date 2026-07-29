# -*- coding: utf-8 -*-
"""Export Revit views/sheets to DXF (+ DWG) for Uniqube CAD viewer."""
from __future__ import print_function

import os
import re

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    View,
    ViewSheet,
    ViewType,
    ElementId,
    DWGExportOptions,
)
from System.Collections.Generic import List

try:
    from Autodesk.Revit.DB import DXFExportOptions
    _HAS_DXF = True
except Exception:
    DXFExportOptions = None
    _HAS_DXF = False


# View types we export as CAD drawings
EXPORTABLE_VIEW_TYPES = set([
    "FloorPlan",
    "CeilingPlan",
    "Elevation",
    "Section",
    "EngineeringPlan",
    "Detail",
    "AreaPlan",
    "DraftingView",
])


def _safe_key(parts):
    raw = "_".join(_safe_str(p) for p in parts)
    raw = re.sub(r"[^\w\-.]+", "_", raw, flags=re.UNICODE)
    raw = raw.strip("_") or "drawing"
    return raw[:180]


def _safe_str(val):
    if val is None:
        return ""
    try:
        return str(val)
    except Exception:
        return ""


def _view_type_name(view):
    try:
        return str(view.ViewType)
    except Exception:
        return "Unknown"


def collect_exportable_drawings(doc):
    """
    Return list of dicts: kind, elementId, name, sheetNumber, viewType, stableKey
    """
    items = []

    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate:
            continue
        if isinstance(v, ViewSheet):
            continue
        vt = _view_type_name(v)
        if vt not in EXPORTABLE_VIEW_TYPES:
            continue
        name = _safe_str(v.Name)
        eid = v.Id.IntegerValue
        key = _safe_key([vt, name, eid])
        items.append({
            "kind": "VIEW",
            "elementId": eid,
            "name": name,
            "sheetNumber": None,
            "viewType": vt,
            "stableKey": key,
        })

    for s in FilteredElementCollector(doc).OfClass(ViewSheet):
        eid = s.Id.IntegerValue
        number = _safe_str(s.SheetNumber)
        name = _safe_str(s.Name)
        key = _safe_key(["Sheet", number, name, eid])
        items.append({
            "kind": "SHEET",
            "elementId": eid,
            "name": name,
            "sheetNumber": number,
            "viewType": "DrawingSheet",
            "stableKey": key,
        })

    return items


def _make_dxf_options():
    if not _HAS_DXF or DXFExportOptions is None:
        return None
    opts = DXFExportOptions()
    # View/sheet local coords — shared site coords often place geometry
    # far from origin and confuse the web CAD viewer camera.
    try:
        opts.SharedCoords = False
    except Exception:
        pass
    return opts


def _make_dwg_options():
    opts = DWGExportOptions()
    try:
        opts.SharedCoords = False
    except Exception:
        pass
    return opts


def _export_one(doc, folder, base_name, element_id, use_dxf=True):
    """Export a single view/sheet. Returns absolute path or None."""
    if use_dxf and not _HAS_DXF:
        return None
    ids = List[ElementId]()
    ids.Add(ElementId(element_id))
    before = set(os.listdir(folder))
    ok = False
    try:
        if use_dxf:
            opts = _make_dxf_options()
            if opts is None:
                return None
            ok = doc.Export(folder, base_name, ids, opts)
        else:
            ok = doc.Export(folder, base_name, ids, _make_dwg_options())
    except Exception as ex:
        raise Exception("Export failed for {}: {}".format(base_name, ex))

    ext = ".dxf" if use_dxf else ".dwg"
    preferred = os.path.join(folder, base_name + ext)
    if os.path.isfile(preferred):
        return preferred
    after = set(os.listdir(folder))
    new_files = [f for f in (after - before) if f.lower().endswith(ext)]
    if new_files:
        return os.path.join(folder, sorted(new_files)[-1])
    if not ok:
        return None
    return preferred if os.path.isfile(preferred) else None


def export_drawings(doc, folder, progress_cb=None):
    """
    Export DXF (+ DWG) for each exportable view/sheet.
    progress_cb(current, total, message) optional.
    Returns (manifest_list, errors_list)
    """
    if not os.path.isdir(folder):
        os.makedirs(folder)

    items = collect_exportable_drawings(doc)
    total = len(items)
    manifest = []
    errors = []

    for i, item in enumerate(items):
        if progress_cb:
            progress_cb(
                i + 1,
                total,
                "Exporting drawing {}/{}: {}".format(i + 1, total, item["name"]),
            )
        base = item["stableKey"]
        dxf_path = None
        dwg_path = None
        try:
            dxf_path = _export_one(doc, folder, base, item["elementId"], use_dxf=True)
        except Exception as ex:
            errors.append("{} DXF: {}".format(base, ex))
        try:
            dwg_path = _export_one(doc, folder, base, item["elementId"], use_dxf=False)
        except Exception as ex:
            errors.append("{} DWG: {}".format(base, ex))

        if not dxf_path and not dwg_path:
            continue

        entry = {
            "key": item["stableKey"],
            "revitElementId": item["elementId"],
            "kind": item["kind"],
            "name": item["name"],
            "sheetNumber": item["sheetNumber"],
            "viewType": item["viewType"],
            "dxfPath": dxf_path,
            "dwgPath": dwg_path,
            "dxfFileName": os.path.basename(dxf_path) if dxf_path else None,
            "dwgFileName": os.path.basename(dwg_path) if dwg_path else None,
        }
        manifest.append(entry)

    return manifest, errors
