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

from uniqube_text import as_ascii_name, as_net_string, as_unicode, exception_text

try:
    from Autodesk.Revit.DB import DXFExportOptions
    _HAS_DXF = True
except Exception:
    DXFExportOptions = None
    _HAS_DXF = False

try:
    from System.IO import Path as NetPath, Directory as NetDirectory, File as NetFile
except Exception:
    NetPath = None
    NetDirectory = None
    NetFile = None


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
    """Strictly ASCII key. Revit view names may hold characters (U+00E1 and
    friends) that IronPython cannot push through os.path / Revit Export."""
    raw = u"_".join(_safe_str(p) for p in parts)
    raw = re.sub(r"[^A-Za-z0-9\-.]+", "_", as_ascii_name(raw, "drawing"))
    raw = raw.strip("_") or "drawing"
    return raw[:120]


def _safe_str(val):
    if val is None:
        return u""
    try:
        return as_unicode(val)
    except Exception:
        return u""


def _join(folder, name):
    if NetPath is not None:
        try:
            return NetPath.Combine(folder, name)
        except Exception:
            pass
    return os.path.join(as_unicode(folder), as_unicode(name))


def _is_file(path):
    if NetFile is not None:
        try:
            return NetFile.Exists(path)
        except Exception:
            pass
    try:
        return os.path.isfile(path)
    except Exception:
        return False


def _basename(path):
    if NetPath is not None:
        try:
            return as_unicode(NetPath.GetFileName(path))
        except Exception:
            pass
    return os.path.basename(as_unicode(path))


def _list_files(folder, pattern):
    """Directory listing via .NET - os.listdir dies on non-ASCII file names."""
    if NetDirectory is not None:
        try:
            return [as_unicode(p) for p in NetDirectory.GetFiles(folder, pattern)]
        except Exception:
            return []
    try:
        return [
            os.path.join(as_unicode(folder), as_unicode(f))
            for f in os.listdir(folder)
        ]
    except Exception:
        return []


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
    # View/sheet local coords - shared site coords often place geometry
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
    ext = ".dxf" if use_dxf else ".dwg"
    ids = List[ElementId]()
    ids.Add(ElementId(element_id))
    before = set(p.lower() for p in _list_files(folder, "*" + ext))
    try:
        if use_dxf:
            opts = _make_dxf_options()
            if opts is None:
                return None
            doc.Export(as_net_string(folder), as_net_string(base_name), ids, opts)
        else:
            doc.Export(
                as_net_string(folder),
                as_net_string(base_name),
                ids,
                _make_dwg_options(),
            )
    except Exception as ex:
        raise Exception(
            "Export failed for {}: {}".format(base_name, exception_text(ex))
        )

    preferred = _join(folder, base_name + ext)
    if _is_file(preferred):
        return preferred
    new_files = [p for p in _list_files(folder, "*" + ext) if p.lower() not in before]
    if new_files:
        return sorted(new_files)[-1]
    return None


def export_drawings(doc, folder, progress_cb=None):
    """
    Export DXF (+ DWG) for each exportable view/sheet.
    progress_cb(current, total, message) optional.
    Returns (manifest_list, errors_list)
    """
    if NetDirectory is not None:
        try:
            NetDirectory.CreateDirectory(folder)
        except Exception:
            pass
    elif not os.path.isdir(folder):
        os.makedirs(folder)

    items = collect_exportable_drawings(doc)
    total = len(items)
    manifest = []
    errors = []

    for i, item in enumerate(items):
        base = item["stableKey"]
        if progress_cb:
            progress_cb(
                i + 1,
                total,
                "Exporting drawing {}/{}: {}".format(i + 1, total, base),
            )
        dxf_path = None
        dwg_path = None
        try:
            dxf_path = _export_one(doc, folder, base, item["elementId"], use_dxf=True)
        except Exception as ex:
            errors.append("{} DXF: {}".format(base, exception_text(ex)))
        try:
            dwg_path = _export_one(doc, folder, base, item["elementId"], use_dxf=False)
        except Exception as ex:
            errors.append("{} DWG: {}".format(base, exception_text(ex)))

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
            "dxfFileName": _basename(dxf_path) if dxf_path else None,
            "dwgFileName": _basename(dwg_path) if dwg_path else None,
        }
        manifest.append(entry)

    return manifest, errors
