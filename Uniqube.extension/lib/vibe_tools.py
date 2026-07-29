# -*- coding: utf-8 -*-
"""Revit tools for Vibe Modeler (MCP-parity capabilities).

All tool bodies must run on the Revit API thread via ExternalEvent.
"""
from __future__ import print_function

import json
import math
import os
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Color,
    ElementId,
    FilteredElementCollector,
    Level,
    Line,
    OverrideGraphicSettings,
    XYZ,
    View,
    ViewType,
    Transaction,
    StorageType,
    FamilySymbol,
    SaveAsOptions,
    ModelPathUtils,
    OpenOptions,
    DetachFromCentralOption,
    TransactWithCentralOptions,
    SynchronizeWithCentralOptions,
    RelinquishOptions,
    ElementTransformUtils,
)
from Autodesk.Revit.DB.Structure import StructuralType
import Autodesk.Revit.UI as UI
from System import EventHandler
from Autodesk.Revit.UI.Events import IdlingEventArgs
from System.Collections.Generic import List

from vibe_tool_schemas import TOOL_SCHEMAS

MUTATING_TOOLS = frozenset(
    [
        "delete_elements",
        "color_splash",
        "clear_colors",
        "place_family",
        "save_document",
        "sync_with_central",
        "open_document",
        "close_document",
        "execute_revit_code",
    ]
)

# ---------------------------------------------------------------------------
# Selection cache — Revit clears selection when the chat panel takes focus
# ---------------------------------------------------------------------------

_selection_cache = {"count": 0, "elements": [], "fingerprint": None}
_idling_handler = None
_idling_uiapp = None
_last_fingerprint = None

_KEY_PARAMS = (
    "Mark",
    "Type Name",
    "Type Mark",
    "BIMSF_Container",
    "BIMSF_PanelName",
    "Comments",
    "Assembly Name",
)


def _selection_fingerprint(uidoc):
    parts = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            parts.append("H{}".format(eid.IntegerValue))
    except Exception:
        pass
    try:
        for ref in uidoc.Selection.GetReferences():
            le = ref.LinkedElementId
            le_id = le.IntegerValue if le else -1
            parts.append("L{}:{}".format(ref.ElementId.IntegerValue, le_id))
    except Exception:
        pass
    parts.sort()
    return tuple(parts)


def _element_level_name(doc, el):
    try:
        p = el.get_Parameter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)
        if p and p.HasValue and p.AsElementId().IntegerValue > 0:
            lvl = doc.GetElement(p.AsElementId())
            if lvl:
                return lvl.Name
    except Exception:
        pass
    try:
        p = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
        if p and p.HasValue and p.AsElementId().IntegerValue > 0:
            lvl = doc.GetElement(p.AsElementId())
            if lvl:
                return lvl.Name
    except Exception:
        pass
    return None


def _element_key_params(el):
    out = {}
    for name in _KEY_PARAMS:
        p = _get_param(el, name)
        if p is None:
            continue
        val = _param_value(p)
        if val is not None and val != "":
            out[name] = val
    return out


def _describe_host_element(doc, el):
    if el is None:
        return None
    item = {
        "element_id": int(el.Id.IntegerValue),
        "name": _safe_name(el),
        "category": _cat_name(el),
        "host": True,
        "linked": False,
    }
    lvl = _element_level_name(doc, el)
    if lvl:
        item["level"] = lvl
    params = _element_key_params(el)
    if params:
        item["parameters"] = params
    return item


def _describe_reference(doc, ref):
    if ref is None:
        return None
    try:
        le = ref.LinkedElementId
        is_link = le is not None and le != ElementId.InvalidElementId
        if is_link:
            link_inst = doc.GetElement(ref.ElementId)
            link_name = _safe_name(link_inst) if link_inst else None
            link_doc = None
            try:
                if link_inst is not None:
                    link_doc = link_inst.GetLinkDocument()
            except Exception:
                link_doc = None
            el = link_doc.GetElement(le) if link_doc else None
            if el is None:
                return {
                    "element_id": int(le.IntegerValue),
                    "link_instance_id": int(ref.ElementId.IntegerValue),
                    "link_name": link_name,
                    "host": False,
                    "linked": True,
                    "note": "Linked element (could not resolve document)",
                }
            item = _describe_host_element(link_doc, el) or {}
            item["host"] = False
            item["linked"] = True
            item["link_name"] = link_name
            item["link_instance_id"] = int(ref.ElementId.IntegerValue)
            return item

        el = doc.GetElement(ref.ElementId)
        return _describe_host_element(doc, el)
    except Exception:
        return None


def _collect_selection(uidoc, doc):
    items = []
    seen = set()

    if uidoc and doc:
        try:
            for eid in uidoc.Selection.GetElementIds():
                key = "H{}".format(eid.IntegerValue)
                if key in seen:
                    continue
                seen.add(key)
                el = doc.GetElement(eid)
                item = _describe_host_element(doc, el)
                if item:
                    items.append(item)
        except Exception:
            pass

        try:
            for ref in uidoc.Selection.GetReferences():
                le = ref.LinkedElementId
                le_id = le.IntegerValue if le else -1
                key = "L{}:{}".format(ref.ElementId.IntegerValue, le_id)
                if key in seen:
                    continue
                seen.add(key)
                item = _describe_reference(doc, ref)
                if item:
                    items.append(item)
        except Exception:
            pass

    return {"count": len(items), "elements": items[:200]}


def _update_selection_cache(uidoc):
    global _selection_cache
    if not uidoc:
        return
    doc = uidoc.Document
    payload = _collect_selection(uidoc, doc)
    payload["fingerprint"] = _selection_fingerprint(uidoc)
    _selection_cache = payload


def _on_idling(sender, args):
    global _last_fingerprint
    try:
        uidoc = sender.ActiveUIDocument
        if not uidoc:
            return
        fp = _selection_fingerprint(uidoc)
        if not fp:
            return
        if fp != _last_fingerprint:
            _last_fingerprint = fp
            _update_selection_cache(uidoc)
    except Exception:
        pass


def start_selection_watcher(uiapp):
    global _idling_handler, _idling_uiapp
    if uiapp is None:
        return
    if _idling_uiapp is uiapp and _idling_handler is not None:
        return
    try:
        if _idling_uiapp is not None and _idling_handler is not None:
            try:
                _idling_uiapp.Idling -= _idling_handler
            except Exception:
                pass
        _idling_uiapp = uiapp
        _idling_handler = EventHandler[IdlingEventArgs](_on_idling)
        uiapp.Idling += _idling_handler
    except Exception:
        pass


def get_cached_selection():
    return dict(_selection_cache) if _selection_cache else {"count": 0, "elements": []}


def _resolve_host_element_ids(uidoc, doc, args):
    """Resolve host-model ElementIds for delete / modify (not linked)."""
    explicit = args.get("element_ids") or []
    use_selection = True if args.get("use_selection", True) else False
    out = List[ElementId]()
    seen = set()
    skipped_linked = 0

    def _add_int(eid_int):
        if eid_int in seen:
            return
        seen.add(eid_int)
        out.Add(ElementId(int(eid_int)))

    if explicit:
        for raw in explicit:
            try:
                _add_int(int(raw))
            except Exception:
                continue
        return out, skipped_linked

    if not use_selection:
        return out, skipped_linked

    if uidoc and doc:
        try:
            for eid in uidoc.Selection.GetElementIds():
                _add_int(eid.IntegerValue)
        except Exception:
            pass

    if out.Count == 0:
        cached = get_cached_selection()
        for item in cached.get("elements") or []:
            if item.get("linked"):
                skipped_linked += 1
                continue
            eid_int = item.get("element_id")
            if eid_int:
                _add_int(eid_int)

    return out, skipped_linked


def _collector_count(collector):
    try:
        return collector.GetElementCount()
    except Exception:
        try:
            return collector.ToElementIds().Count
        except Exception:
            return len(list(collector.ToElements()))


def _safe_name(el):
    try:
        return el.Name
    except Exception:
        try:
            return el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        except Exception:
            return "Element"


def _cat_name(el):
    try:
        if el.Category:
            return el.Category.Name
    except Exception:
        pass
    return None


def _find_category(doc, category_name):
    target = (category_name or "").strip().lower()
    if not target:
        return None
    for bic in System_Enum_GetValues():
        try:
            cat = doc.Settings.Categories.get_Item(bic)
            if cat and cat.Name and cat.Name.lower() == target:
                return cat
        except Exception:
            continue
    # fallback: scan categories
    try:
        for cat in doc.Settings.Categories:
            if cat and cat.Name and cat.Name.lower() == target:
                return cat
    except Exception:
        pass
    return None


def System_Enum_GetValues():
    # BuiltInCategory values
    try:
        return list(clr.GetClrType(BuiltInCategory).GetEnumValues())
    except Exception:
        vals = []
        for name in dir(BuiltInCategory):
            if name.startswith("OST_"):
                try:
                    vals.append(getattr(BuiltInCategory, name))
                except Exception:
                    pass
        return vals


def _param_value(param):
    if param is None or not param.HasValue:
        return None
    try:
        st = param.StorageType
        if st == StorageType.String:
            return param.AsString()
        if st == StorageType.Integer:
            return param.AsInteger()
        if st == StorageType.Double:
            return param.AsDouble()
        if st == StorageType.ElementId:
            eid = param.AsElementId()
            return int(eid.IntegerValue) if eid else None
    except Exception:
        pass
    try:
        return param.AsValueString()
    except Exception:
        return None


def _get_param(el, parameter_name):
    if not el or not parameter_name:
        return None
    p = el.LookupParameter(parameter_name)
    if p:
        return p
    try:
        for p in el.Parameters:
            if p.Definition and p.Definition.Name == parameter_name:
                return p
    except Exception:
        pass
    return None


def _palette(n, use_gradient=False):
    colors = []
    for i in range(max(n, 1)):
        if use_gradient:
            t = float(i) / float(max(n - 1, 1))
            r = int(40 + 180 * t)
            g = int(80 + 100 * (1 - abs(t - 0.5) * 2))
            b = int(220 - 160 * t)
        else:
            hue = (i * 0.61803398875) % 1.0
            # simple HSV-ish
            r = int(80 + 150 * abs(math.sin(hue * 6.28)))
            g = int(80 + 150 * abs(math.sin(hue * 6.28 + 2.1)))
            b = int(80 + 150 * abs(math.sin(hue * 6.28 + 4.2)))
        colors.append(Color(r % 256, g % 256, b % 256))
    return colors


def _json_safe(obj, limit=8000):
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > limit:
        return json.dumps(
            {
                "truncated": True,
                "preview": text[: limit - 80],
                "note": "Result truncated for chat context.",
            },
            ensure_ascii=False,
        )
    return text


# ---------------------------------------------------------------------------
# Tool implementations (must run on Revit API thread)
# ---------------------------------------------------------------------------

def tool_get_revit_status(uiapp, uidoc, doc, args):
    return {
        "ok": True,
        "has_document": doc is not None,
        "has_uidoc": uidoc is not None,
        "title": doc.Title if doc else None,
    }


def tool_get_revit_model_info(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    path = None
    try:
        path = doc.PathName
    except Exception:
        pass
    counts = {}
    try:
        walls = _collector_count(
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_Walls)
            .WhereElementIsNotElementType()
        )
        doors = _collector_count(
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_Doors)
            .WhereElementIsNotElementType()
        )
        windows = _collector_count(
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_Windows)
            .WhereElementIsNotElementType()
        )
        floors = _collector_count(
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_Floors)
            .WhereElementIsNotElementType()
        )
        levels = _collector_count(FilteredElementCollector(doc).OfClass(Level))
        counts = {
            "walls": walls,
            "doors": doors,
            "windows": windows,
            "floors": floors,
            "levels": levels,
        }
    except Exception as ex:
        counts = {"error": str(ex)}
    return {
        "title": doc.Title,
        "path": path or "",
        "is_family_document": doc.IsFamilyDocument,
        "is_workshared": doc.IsWorkshared,
        "is_modified": doc.IsModified,
        "counts": counts,
    }


def tool_get_current_view_info(uiapp, uidoc, doc, args):
    if not uidoc:
        return {"error": "No active UI document"}
    view = uidoc.ActiveView
    if not view:
        return {"error": "No active view"}
    info = {
        "id": int(view.Id.IntegerValue),
        "name": view.Name,
        "view_type": str(view.ViewType),
        "scale": view.Scale,
        "is_template": view.IsTemplate,
    }
    try:
        info["detail_level"] = str(view.DetailLevel)
    except Exception:
        pass
    try:
        info["crop_box_active"] = bool(view.CropBoxActive)
    except Exception:
        pass
    try:
        info["discipline"] = str(view.Discipline)
    except Exception:
        pass
    return info


def tool_get_current_view_elements(uiapp, uidoc, doc, args):
    if not uidoc or not doc:
        return {"error": "No active document/view"}
    view = uidoc.ActiveView
    limit = int(args.get("limit") or 200)
    if limit < 1:
        limit = 200
    if limit > 2000:
        limit = 2000
    include_levels = bool(args.get("include_levels"))
    include_location = bool(args.get("include_location"))

    collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
    elements = list(collector.ToElements())
    total = len(elements)
    category_counts = {}
    for el in elements:
        cn = _cat_name(el) or "None"
        category_counts[cn] = category_counts.get(cn, 0) + 1

    items = []
    for el in elements[:limit]:
        item = {
            "element_id": int(el.Id.IntegerValue),
            "name": _safe_name(el),
            "category": _cat_name(el),
        }
        if include_levels:
            try:
                p = el.get_Parameter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)
                if p and p.AsElementId() and p.AsElementId().IntegerValue > 0:
                    lvl = doc.GetElement(p.AsElementId())
                    item["level"] = lvl.Name if lvl else None
                    item["level_id"] = int(p.AsElementId().IntegerValue)
            except Exception:
                pass
        if include_location:
            try:
                loc = el.Location
                if hasattr(loc, "Point") and loc.Point:
                    pt = loc.Point
                    item["location"] = {"type": "point", "x": pt.X, "y": pt.Y, "z": pt.Z}
                elif hasattr(loc, "Curve") and loc.Curve:
                    c = loc.Curve
                    item["location"] = {
                        "type": "curve",
                        "start": [c.GetEndPoint(0).X, c.GetEndPoint(0).Y, c.GetEndPoint(0).Z],
                        "end": [c.GetEndPoint(1).X, c.GetEndPoint(1).Y, c.GetEndPoint(1).Z],
                    }
            except Exception:
                pass
        items.append(item)

    return {
        "view": view.Name,
        "total_elements": total,
        "returned_elements": len(items),
        "truncated": total > len(items),
        "category_counts": category_counts,
        "elements": items,
    }


def tool_get_selection(uiapp, uidoc, doc, args):
    if not uidoc:
        return {"error": "No active UI document"}

    live = _collect_selection(uidoc, doc)
    if live.get("count", 0) > 0:
        return {
            "count": live["count"],
            "returned": len(live.get("elements") or []),
            "elements": live.get("elements") or [],
            "source": "live",
        }

    cached = get_cached_selection()
    if cached.get("count", 0) > 0:
        return {
            "count": cached["count"],
            "returned": len(cached.get("elements") or []),
            "elements": cached.get("elements") or [],
            "source": "cached",
            "note": (
                "Revit often clears the selection when the Vibe chat box has focus. "
                "Using the last selection captured while you had elements picked in the view."
            ),
        }

    return {
        "count": 0,
        "returned": 0,
        "elements": [],
        "source": "none",
        "note": (
            "Nothing selected. Click an element in the model first, then ask again. "
            "Tip: select in the view, then send — you do not need to keep it highlighted "
            "after typing in the chat box."
        ),
    }


def tool_delete_elements(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}

    ids, skipped_linked = _resolve_host_element_ids(uidoc, doc, args)
    if ids.Count == 0:
        msg = "No deletable host elements found."
        if skipped_linked:
            msg += " Linked-model elements cannot be deleted from the host — select host elements."
        else:
            msg += " Select element(s) in the view first, then ask to delete."
        return {"error": msg, "deleted": 0, "skipped_linked": skipped_linked}

    if ids.Count > 100:
        return {
            "error": "Refusing to delete more than 100 elements at once.",
            "deleted": 0,
        }

    preview = []
    for eid in ids:
        el = doc.GetElement(eid)
        if el:
            preview.append(
                {
                    "element_id": int(eid.IntegerValue),
                    "name": _safe_name(el),
                    "category": _cat_name(el),
                }
            )

    t = Transaction(doc, "Vibe: Delete Elements")
    t.Start()
    try:
        deleted_ids = doc.Delete(ids)
        deleted_count = 0
        try:
            deleted_count = deleted_ids.Count
        except Exception:
            try:
                deleted_count = len(list(deleted_ids))
            except Exception:
                deleted_count = ids.Count
        t.Commit()
        # Clear cache entries for deleted ids
        try:
            global _selection_cache
            if _selection_cache.get("elements"):
                deleted_set = set()
                for did in deleted_ids:
                    try:
                        deleted_set.add(int(did.IntegerValue))
                    except Exception:
                        pass
                _selection_cache["elements"] = [
                    e
                    for e in _selection_cache["elements"]
                    if e.get("element_id") not in deleted_set
                ]
                _selection_cache["count"] = len(_selection_cache["elements"])
        except Exception:
            pass
        return {
            "deleted": deleted_count,
            "elements": preview[:50],
            "skipped_linked": skipped_linked,
            "mutated_model": True,
        }
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        return {"error": str(ex), "deleted": 0}


def tool_undo_last_change(uiapp, uidoc, doc, args):
    try:
        from Autodesk.Revit.UI import RevitCommandId, PostableCommand

        cmd_id = RevitCommandId.LookupPostableCommandId(PostableCommand.Undo)
        uiapp.PostCommand(cmd_id)
        return {"undone": True}
    except Exception as ex:
        return {"error": str(ex)}


def tool_list_levels(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
    items = []
    for lvl in levels:
        items.append(
            {
                "id": int(lvl.Id.IntegerValue),
                "name": lvl.Name,
                "elevation": lvl.Elevation,
            }
        )
    items.sort(key=lambda x: x["elevation"])
    return {"count": len(items), "levels": items}


def tool_list_revit_views(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    limit = int(args.get("limit") or 100)
    views = FilteredElementCollector(doc).OfClass(View).ToElements()
    items = []
    for v in views:
        try:
            if v.IsTemplate:
                continue
            vt = v.ViewType
            vt_name = str(vt)
            if vt_name in ("Internal", "ProjectBrowser", "SystemBrowser"):
                continue
            items.append(
                {
                    "id": int(v.Id.IntegerValue),
                    "name": v.Name,
                    "view_type": str(v.ViewType),
                }
            )
        except Exception:
            continue
        if len(items) >= limit:
            break
    return {"count": len(items), "views": items}


def tool_list_families(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    contains = (args.get("contains") or "").strip().lower()
    limit = int(args.get("limit") or 50)
    symbols = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    items = []
    for sym in symbols:
        try:
            fam = sym.Family.Name if sym.Family else ""
            typ = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            type_name = typ.AsString() if typ else sym.Name
            label = "{} : {}".format(fam, type_name)
            if contains and contains not in label.lower():
                continue
            items.append(
                {
                    "family": fam,
                    "type": type_name,
                    "id": int(sym.Id.IntegerValue),
                    "category": sym.Category.Name if sym.Category else None,
                }
            )
        except Exception:
            continue
        if len(items) >= limit:
            break
    return {"count": len(items), "families": items}


def tool_list_family_categories(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    symbols = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    cats = {}
    for sym in symbols:
        try:
            cn = sym.Category.Name if sym.Category else "None"
            cats[cn] = cats.get(cn, 0) + 1
        except Exception:
            continue
    return {"categories": [{"name": k, "type_count": v} for k, v in sorted(cats.items())]}


def tool_list_category_parameters(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    cat = _find_category(doc, args.get("category_name"))
    if not cat:
        return {"error": "Category not found: {}".format(args.get("category_name"))}
    elems = (
        FilteredElementCollector(doc)
        .OfCategoryId(cat.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if not elems:
        return {"category": cat.Name, "parameters": [], "note": "No instances found"}
    sample = list(elems)[:15]
    found = {}
    for el in sample:
        try:
            for p in el.Parameters:
                if not p.Definition:
                    continue
                name = p.Definition.Name
                if name in found:
                    continue
                found[name] = {
                    "name": name,
                    "storage": str(p.StorageType),
                    "sample": _param_value(p),
                }
        except Exception:
            continue
    params = sorted(found.values(), key=lambda x: x["name"])
    return {"category": cat.Name, "parameter_count": len(params), "parameters": params[:120]}


def tool_color_splash(uiapp, uidoc, doc, args):
    if not doc or not uidoc:
        return {"error": "No active document"}
    view = uidoc.ActiveView
    cat = _find_category(doc, args.get("category_name"))
    if not cat:
        return {"error": "Category not found"}
    param_name = args.get("parameter_name")
    use_gradient = bool(args.get("use_gradient"))
    elems = (
        FilteredElementCollector(doc, view.Id)
        .OfCategoryId(cat.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    groups = {}
    for el in elems:
        p = _get_param(el, param_name)
        key = str(_param_value(p)) if p else "<none>"
        groups.setdefault(key, []).append(el)

    keys = sorted(groups.keys())
    colors = _palette(len(keys), use_gradient)
    t = Transaction(doc, "Vibe: Color Splash")
    t.Start()
    try:
        assignments = []
        for i, key in enumerate(keys):
            c = colors[i]
            ogs = OverrideGraphicSettings()
            ogs.SetProjectionLineColor(c)
            try:
                ogs.SetSurfaceForegroundPatternColor(c)
            except Exception:
                pass
            try:
                ogs.SetCutLineColor(c)
            except Exception:
                pass
            for el in groups[key]:
                view.SetElementOverrides(el.Id, ogs)
            assignments.append({"value": key, "count": len(groups[key]), "color_rgb": [c.Red, c.Green, c.Blue]})
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        return {"error": str(ex)}
        return {
            "category": cat.Name,
            "parameter": param_name,
            "view": view.Name,
            "groups": len(keys),
            "elements": sum(len(v) for v in groups.values()),
            "assignments": assignments[:40],
            "mutated_model": True,
        }


def tool_clear_colors(uiapp, uidoc, doc, args):
    if not doc or not uidoc:
        return {"error": "No active document"}
    view = uidoc.ActiveView
    cat = _find_category(doc, args.get("category_name"))
    if not cat:
        return {"error": "Category not found"}
    elems = (
        FilteredElementCollector(doc, view.Id)
        .OfCategoryId(cat.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    t = Transaction(doc, "Vibe: Clear Colors")
    t.Start()
    try:
        blank = OverrideGraphicSettings()
        n = 0
        for el in elems:
            view.SetElementOverrides(el.Id, blank)
            n += 1
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        return {"error": str(ex)}
    return {"cleared": n, "category": cat.Name, "view": view.Name, "mutated_model": True}


def tool_place_family(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    family_name = (args.get("family_name") or "").strip()
    type_name = (args.get("type_name") or "").strip()
    x = float(args.get("x") or 0)
    y = float(args.get("y") or 0)
    z = float(args.get("z") or 0)
    rotation = float(args.get("rotation") or 0)
    level_name = (args.get("level_name") or "").strip()

    symbol = None
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements():
        try:
            fam = sym.Family.Name if sym.Family else ""
            if fam != family_name:
                continue
            typ = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            tn = typ.AsString() if typ else sym.Name
            if type_name and tn != type_name:
                continue
            symbol = sym
            if type_name:
                break
            if not type_name:
                break
        except Exception:
            continue
    if not symbol:
        return {"error": "Family/type not found: {} / {}".format(family_name, type_name or "*")}

    level = None
    if level_name:
        for lvl in FilteredElementCollector(doc).OfClass(Level).ToElements():
            if lvl.Name == level_name:
                level = lvl
                break

    t = Transaction(doc, "Vibe: Place Family")
    t.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
        pt = XYZ(x, y, z)
        if level is not None:
            inst = doc.Create.NewFamilyInstance(pt, symbol, level, StructuralType.NonStructural)
        else:
            inst = doc.Create.NewFamilyInstance(pt, symbol, StructuralType.NonStructural)
        if abs(rotation) > 1e-6:
            axis_line = Line.CreateBound(pt, pt + XYZ.BasisZ * 10.0)
            ElementTransformUtils.RotateElement(
                doc, inst.Id, axis_line, math.radians(rotation)
            )
        t.Commit()
        return {
            "placed": True,
            "element_id": int(inst.Id.IntegerValue),
            "family": family_name,
            "type": type_name or _safe_name(symbol),
            "location": [x, y, z],
            "mutated_model": True,
        }
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        return {"error": str(ex)}


def tool_save_document(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    path = args.get("file_path")
    try:
        if path:
            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            doc.SaveAs(path, opts)
            return {"saved_as": path}
        doc.Save()
        return {"saved": True, "path": doc.PathName}
    except Exception as ex:
        return {"error": str(ex)}


def tool_sync_with_central(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    if not doc.IsWorkshared:
        return {"error": "Document is not workshared. Use save_document instead."}
    comment = args.get("comment") or "Vibe Modeler sync"
    compact = bool(args.get("compact"))
    relinquish_all = True if args.get("relinquish_all", True) else False
    try:
        try:
            ro = RelinquishOptions(relinquish_all)
        except Exception:
            ro = RelinquishOptions()
            try:
                ro.UserWorksets = relinquish_all
            except Exception:
                pass
        swc = SynchronizeWithCentralOptions()
        try:
            swc.SetRelinquishOptions(ro)
        except Exception:
            pass
        try:
            swc.Compact = compact
        except Exception:
            pass
        try:
            swc.Comment = comment
        except Exception:
            pass
        twc = TransactWithCentralOptions()
        doc.SynchronizeWithCentral(twc, swc)
        return {"synced": True, "comment": comment}
    except Exception as ex:
        return {"error": str(ex)}


def tool_open_document(uiapp, uidoc, doc, args):
    path = args.get("file_path")
    if not path or not os.path.isfile(path):
        return {"error": "File not found: {}".format(path)}
    detach = bool(args.get("detach"))
    audit = bool(args.get("audit"))
    try:
        app = uiapp.Application
        model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
        opts = OpenOptions()
        if audit:
            opts.Audit = True
        if detach:
            opts.DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets
        new_doc = app.OpenDocumentFile(model_path, opts)
        return {"opened": True, "title": new_doc.Title, "path": path}
    except Exception as ex:
        return {"error": str(ex)}


def tool_close_document(uiapp, uidoc, doc, args):
    if not doc:
        return {"error": "No active document"}
    save = bool(args.get("save"))
    try:
        title = doc.Title
        doc.Close(save)
        return {"closed": True, "title": title, "saved": save}
    except Exception as ex:
        return {"error": str(ex)}


def tool_execute_revit_code(uiapp, uidoc, doc, args):
    code = args.get("code") or ""
    if not code.strip():
        return {"error": "Empty code"}
    # Block a few obvious foot-guns
    banned = ["os.system", "subprocess", "Shutdown", "__import__('ctypes')"]
    for b in banned:
        if b in code:
            return {"error": "Code blocked for safety: {}".format(b)}

    import Autodesk.Revit.DB as DB

    prints = []

    def _print(*a):
        prints.append(" ".join([str(x) for x in a]))

    scope = {
        "doc": doc,
        "uidoc": uidoc,
        "uiapp": uiapp,
        "DB": DB,
        "print": _print,
        "__builtins__": __builtins__,
    }
    try:
        compiled = compile(code, "<vibe>", "exec")
        runner = getattr(__builtins__, "exec", None)
        if callable(runner):
            runner(compiled, scope, scope)
        else:
            # IronPython 2 / CPython 2
            eval(compiled, scope)
        return {
            "ok": True,
            "description": args.get("description") or "Code execution",
            "output": "\n".join(prints) if prints else "",
        }
    except Exception as ex:
        return {
            "ok": False,
            "error": str(ex),
            "traceback": traceback.format_exc(),
            "output": "\n".join(prints) if prints else "",
        }


TOOL_IMPL = {
    "get_revit_status": tool_get_revit_status,
    "get_revit_model_info": tool_get_revit_model_info,
    "get_current_view_info": tool_get_current_view_info,
    "get_current_view_elements": tool_get_current_view_elements,
    "get_selection": tool_get_selection,
    "delete_elements": tool_delete_elements,
    "list_levels": tool_list_levels,
    "list_revit_views": tool_list_revit_views,
    "list_families": tool_list_families,
    "list_family_categories": tool_list_family_categories,
    "list_category_parameters": tool_list_category_parameters,
    "color_splash": tool_color_splash,
    "clear_colors": tool_clear_colors,
    "place_family": tool_place_family,
    "save_document": tool_save_document,
    "sync_with_central": tool_sync_with_central,
    "open_document": tool_open_document,
    "close_document": tool_close_document,
    "execute_revit_code": tool_execute_revit_code,
}


def dispatch_tool(name, arguments, uiapp, uidoc, doc):
    fn = TOOL_IMPL.get(name)
    if not fn:
        return {"error": "Unknown tool: {}".format(name)}
    if not isinstance(arguments, dict):
        arguments = {}
    return fn(uiapp, uidoc, doc, arguments)


# ---------------------------------------------------------------------------
# ExternalEvent bridge
# ---------------------------------------------------------------------------

_handler = None
_ext_event = None


class VibeToolHandler(UI.IExternalEventHandler):
    def __init__(self):
        self.pending_calls = None
        self.pending_undo = False
        self.results = None
        self.error = None
        self.done = False

    def Execute(self, uiapp):
        self.done = False
        self.error = None
        self.results = []
        try:
            start_selection_watcher(uiapp)
            uidoc = uiapp.ActiveUIDocument
            doc = uidoc.Document if uidoc else None

            if self.pending_undo:
                self.pending_undo = False
                try:
                    out = tool_undo_last_change(uiapp, uidoc, doc, {})
                except Exception as ex:
                    out = {"error": str(ex)}
                self.results.append(
                    {
                        "tool_call_id": "undo",
                        "role": "tool",
                        "name": "undo_last_change",
                        "content": _json_safe(out),
                    }
                )
                self.done = True
                return

            if uidoc:
                fp = _selection_fingerprint(uidoc)
                if fp:
                    _update_selection_cache(uidoc)
            for call in self.pending_calls or []:
                name = call.get("name")
                cid = call.get("id")
                raw_args = call.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                try:
                    out = dispatch_tool(name, raw_args, uiapp, uidoc, doc)
                    if (
                        name in MUTATING_TOOLS
                        and isinstance(out, dict)
                        and not out.get("error")
                    ):
                        out["mutated_model"] = True
                except Exception as ex:
                    out = {"error": str(ex), "traceback": traceback.format_exc()}
                self.results.append(
                    {
                        "tool_call_id": cid,
                        "role": "tool",
                        "name": name,
                        "content": _json_safe(out),
                    }
                )
        except Exception as ex:
            self.error = str(ex)
            self.results = self.results or []
        self.done = True

    def GetName(self):
        return "VibeModelerTools"


def ensure_external_event():
    """Create ExternalEvent once. Call from a Revit API context (pushbutton)."""
    global _handler, _ext_event
    if _ext_event is not None:
        try:
            from pyrevit import HOST_APP

            if HOST_APP and getattr(HOST_APP, "uiapp", None):
                start_selection_watcher(HOST_APP.uiapp)
        except Exception:
            pass
        return _handler, _ext_event
    _handler = VibeToolHandler()
    _ext_event = UI.ExternalEvent.Create(_handler)
    try:
        from pyrevit import HOST_APP

        if HOST_APP and getattr(HOST_APP, "uiapp", None):
            start_selection_watcher(HOST_APP.uiapp)
    except Exception:
        pass
    return _handler, _ext_event


def get_external_event():
    return _handler, _ext_event


def raise_tools(tool_calls):
    """
    Queue OpenAI tool_calls and Raise ExternalEvent.
    tool_calls: list of dicts with id, function.name, function.arguments
    """
    handler, event = get_external_event()
    if event is None or handler is None:
        handler, event = ensure_external_event()
    if event is None or handler is None:
        raise Exception(
            "Revit tool bridge not ready. Click Open Vibe on the ribbon, then retry."
        )
    normalized = []
    for tc in tool_calls or []:
        fn = tc.get("function") or {}
        normalized.append(
            {
                "id": tc.get("id"),
                "name": fn.get("name") or tc.get("name"),
                "arguments": fn.get("arguments") or tc.get("arguments") or {},
            }
        )
    handler.pending_calls = normalized
    handler.results = None
    handler.error = None
    handler.done = False
    event.Raise()
    return True


def raise_undo():
    """Queue Revit Undo via ExternalEvent (UI button)."""
    handler, event = get_external_event()
    if event is None or handler is None:
        handler, event = ensure_external_event()
    if event is None or handler is None:
        raise Exception(
            "Revit tool bridge not ready. Click Open Vibe on the ribbon, then retry."
        )
    handler.pending_calls = None
    handler.pending_undo = True
    handler.results = None
    handler.error = None
    handler.done = False
    event.Raise()
    return True
