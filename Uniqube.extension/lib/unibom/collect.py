# -*- coding: utf-8 -*-
"""Revit side of UNIBOM: read the model into plain dict records.

Only this module and unibom.report touch the Revit API. Everything else is
plain Python, so the same rules can run on the server later.
"""
from __future__ import division

import re

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FamilyInstance,
    FilteredElementCollector,
    Wall,
)

FT_TO_MM = 304.8
FT2_TO_M2 = 0.09290304

# Fixed English names, so rules do not depend on the Revit UI language.
MEMBER_CATEGORIES = [
    (BuiltInCategory.OST_StructuralFraming, "Structural Framing"),
    (BuiltInCategory.OST_StructConnections, "Structural Connections"),
    (BuiltInCategory.OST_Windows, "Windows"),
    (BuiltInCategory.OST_Doors, "Doors"),
    (BuiltInCategory.OST_Roofs, "Roofs"),
    (BuiltInCategory.OST_Casework, "Casework"),
]

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_SIZE_IN_NAME = re.compile(r"(\d{3,5})\s*[xX]\s*(\d{3,5})")


def eid(element_id):
    """ElementId -> int on every Revit version (Value from 2024, IntegerValue before)."""
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


def _category_names():
    out = {}
    for bic, name in MEMBER_CATEGORIES:
        out[int(bic)] = name
    return out


_CATEGORY_NAMES = _category_names()


# --- parameter readers --------------------------------------------------------
def p_string(element, name):
    try:
        p = element.LookupParameter(name)
        if p is None or not p.HasValue:
            return ""
        if p.StorageType.ToString() == "String":
            return (p.AsString() or "").strip()
        return (p.AsValueString() or "").strip()
    except Exception:
        return ""


def p_length_mm(element, name):
    """A length parameter in mm (internal feet converted)."""
    try:
        p = element.LookupParameter(name)
        if p is not None and p.HasValue and p.StorageType.ToString() == "Double":
            return p.AsDouble() * FT_TO_MM
    except Exception:
        pass
    return None


def p_display_number(element, name):
    """First number in the parameter's display text, e.g. '2.81 kgf/m' -> 2.81."""
    try:
        p = element.LookupParameter(name)
        if p is None or not p.HasValue:
            return None
        storage = p.StorageType.ToString()
        if storage == "Integer":
            return float(p.AsInteger())
        if storage == "ElementId":
            return float(eid(p.AsElementId()))
        text = p.AsString() if storage == "String" else p.AsValueString()
        m = _NUMBER.search(text or "")
        return float(m.group(0)) if m else None
    except Exception:
        return None


def bip_double(element, bip):
    try:
        p = element.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except Exception:
        pass
    return None


_TYPE_CACHE = {}


def type_info(doc, element):
    """(family name, type name, type element) with a per-run cache."""
    type_id = element.GetTypeId()
    if type_id is None or type_id == ElementId.InvalidElementId:
        return "", "", None
    key = eid(type_id)
    if key not in _TYPE_CACHE:
        et = doc.GetElement(type_id)
        if et is None:
            _TYPE_CACHE[key] = ("", "", None)
        else:
            family = getattr(et, "FamilyName", "") or ""
            p = et.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            name = (p.AsString() if p is not None and p.HasValue else "") or ""
            if not name:
                try:
                    name = et.Name
                except Exception:
                    name = ""
            _TYPE_CACHE[key] = (family.strip(), name.strip(), et)
    return _TYPE_CACHE[key]


def _first(*values):
    for v in values:
        if v not in (None, "", 0, 0.0):
            return v
    return None


def _role_of(element):
    for name in ("BIMSF_Description", "Tag", "Comments"):
        value = p_string(element, name)
        if value:
            return value
    return ""


def _level_name(doc, element):
    for name in ("Reference Level", "Level", "Base Constraint"):
        value = p_string(element, name)
        if value:
            return value
    try:
        level = doc.GetElement(element.LevelId)
        if level is not None:
            return level.Name
    except Exception:
        pass
    return ""


def _width_height_mm(element, element_type, type_name):
    w = h = None
    for source in (element_type, element):
        if source is None:
            continue
        w = w or _ft(bip_double(source, BuiltInParameter.FAMILY_WIDTH_PARAM))
        h = h or _ft(bip_double(source, BuiltInParameter.FAMILY_HEIGHT_PARAM))
        w = w or p_length_mm(source, "Width")
        h = h or p_length_mm(source, "Height")
    if not (w and h):
        m = _SIZE_IN_NAME.search(type_name or "")
        if m:
            w, h = w or float(m.group(1)), h or float(m.group(2))
    return w, h


def _ft(value):
    return value * FT_TO_MM if value else None


# --- members ----------------------------------------------------------------
def member_record(doc, element):
    cat = element.Category
    if cat is None:
        return None
    category = _CATEGORY_NAMES.get(eid(cat.Id))
    if category is None:
        return None
    family, type_name, et = type_info(doc, element)
    if not family:
        try:
            family = element.Symbol.FamilyName
        except Exception:
            family = ""

    rec = {
        "id": eid(element.Id),
        "category": category,
        "family": family,
        "type": type_name,
        "role": _role_of(element),
        "label": _first(p_string(element, "BIMSF_Label"), p_string(element, "Label"),
                        p_string(element, "BIMSF_ScheduleLabel")) or "",
        "container": _first(p_string(element, "BIMSF_Container"),
                            p_string(element, "UQ_Panel")) or "",
        "level": _level_name(doc, element),
    }

    if category == "Structural Framing":
        rec["length_mm"] = _first(
            p_length_mm(element, "Cut Length"),
            p_length_mm(element, "Length"),
            _curve_length_mm(element)) or 0.0
        if et is not None:
            rec["kg_per_m_revit"] = p_display_number(et, "Weight")
        if family == "SheathingPanel" or p_string(element, "BIMSF_Description") == "SheathingPanel":
            rec["sheet_w_mm"] = _first(p_length_mm(element, "Sheathing Width"),
                                       p_length_mm(element, "BIMSF_SheetWidth"))
            rec["sheet_h_mm"] = _first(p_length_mm(element, "Sheathing Height"),
                                       p_length_mm(element, "BIMSF_SheetHeight"))
            thickness = _first(p_length_mm(element, "Thickness"),
                               p_length_mm(element, "BIMSF_SheetThickness"))
            rec["thickness_mm"] = round(thickness, 1) if thickness else None
            rec["layer"] = p_string(element, "Layer Location")
    elif category in ("Windows", "Doors"):
        rec["width_mm"], rec["height_mm"] = _width_height_mm(element, et, type_name)
    elif category == "Roofs":
        area = bip_double(element, BuiltInParameter.HOST_AREA_COMPUTED)
        rec["area_m2"] = area * FT2_TO_M2 if area else 0.0
    elif category == "Casework":
        rec["length_mm"] = _first(p_length_mm(element, "Length"), _curve_length_mm(element)) or 0.0
    return rec


def _curve_length_mm(element):
    try:
        loc = element.Location
        if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
            return loc.Curve.Length * FT_TO_MM
    except Exception:
        pass
    return None


def _member_elements(doc, view_id=None):
    for bic, _name in MEMBER_CATEGORIES:
        collector = (FilteredElementCollector(doc, view_id) if view_id
                     else FilteredElementCollector(doc))
        for element in collector.OfCategory(bic).WhereElementIsNotElementType():
            yield element


# --- walls ------------------------------------------------------------------
def _panel_params_by_wall(doc):
    """BIMSF PanelParams casework -> {wall id: (container, stud spacing mm)}."""
    out = {}
    collector = FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Casework).WhereElementIsNotElementType()
    for element in collector:
        family, _type, _et = type_info(doc, element)
        if family != "PanelParams":
            continue
        host = None
        try:
            if element.Host is not None:
                host = eid(element.Host.Id)
        except Exception:
            host = None
        if host is None:
            host = p_display_number(element, "Host Id")
        if host is None:
            continue
        prefix = p_string(element, "ss_Prefix")
        number = p_display_number(element, "ss_Number")
        container = u"{}{}".format(prefix, int(number)) if prefix and number is not None else ""
        spacing = p_display_number(element, "ss_StudSpacing")
        out[int(host)] = (container, spacing)
    return out


def wall_record(doc, wall, panel_params):
    family, type_name, _et = type_info(doc, wall)
    length = bip_double(wall, BuiltInParameter.CURVE_ELEM_LENGTH)
    height = bip_double(wall, BuiltInParameter.WALL_USER_HEIGHT_PARAM)
    if not height:
        bbox = wall.get_BoundingBox(None)
        height = (bbox.Max.Z - bbox.Min.Z) if bbox else 0.0
    openings = 0
    try:
        for insert_id in wall.FindInserts(True, False, False, False):
            insert = doc.GetElement(insert_id)
            if insert is not None and insert.Category is not None and eid(insert.Category.Id) in (
                    int(BuiltInCategory.OST_Windows), int(BuiltInCategory.OST_Doors)):
                openings += 1
    except Exception:
        pass
    container, spacing = panel_params.get(eid(wall.Id), ("", None))
    return {
        "id": eid(wall.Id),
        "type": type_name or getattr(wall, "Name", ""),
        "level": _level_name(doc, wall),
        "length_mm": (length or 0.0) * FT_TO_MM,
        "height_mm": (height or 0.0) * FT_TO_MM,
        "openings": openings,
        "stud_spacing_mm": spacing,
        "container": container,
    }


# --- entry points -----------------------------------------------------------
def collect(doc, scope="model", view=None, selected_ids=None):
    """Return (members, walls) for the scope: 'model', 'view' or 'selection'.

    A selected wall brings all framing in its BIMSF panel with it, so picking
    walls is enough to take off their panels.
    """
    _TYPE_CACHE.clear()
    panel_params = _panel_params_by_wall(doc)

    if scope == "view" and view is not None:
        members = [member_record(doc, e) for e in _member_elements(doc, view.Id)]
        walls_src = FilteredElementCollector(doc, view.Id).OfClass(Wall)
    elif scope == "selection":
        selected = set(selected_ids or [])
        walls_src = [doc.GetElement(ElementId(i)) for i in selected]
        walls_src = [w for w in walls_src if isinstance(w, Wall)]
        containers = set()
        for w in walls_src:
            containers.add(panel_params.get(eid(w.Id), ("", None))[0])
        containers.discard("")
        members = []
        for e in _member_elements(doc):
            if eid(e.Id) in selected:
                members.append(member_record(doc, e))
            elif containers and p_string(e, "BIMSF_Container") in containers:
                members.append(member_record(doc, e))
    else:
        members = [member_record(doc, e) for e in _member_elements(doc)]
        walls_src = FilteredElementCollector(doc).OfClass(Wall)

    members = [m for m in members if m is not None]
    walls = [wall_record(doc, w, panel_params) for w in walls_src]
    return members, walls
