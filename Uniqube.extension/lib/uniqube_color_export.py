# -*- coding: utf-8 -*-
"""Export Revit display / system colors for Uniqube publish.

MEP system LineColor (e.g. Domestic Cold Water = #0000FF) does not survive
IFC materials. This builds a sidecar color map keyed by Revit ElementId,
UniqueId, and IFC GlobalId so the viewer can restore the same colours.
"""

from __future__ import print_function

import json

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
    MEPSystem,
)

# IFC 22-char GlobalId alphabet (buildingSMART / Autodesk)
_IFC_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"

# Categories that commonly take system / material display colours
_MEP_CATS = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_PipeAccessory,
    BuiltInCategory.OST_FlexPipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_FlexDuctCurves,
    BuiltInCategory.OST_Conduit,
    BuiltInCategory.OST_ConduitFitting,
    BuiltInCategory.OST_CableTray,
    BuiltInCategory.OST_CableTrayFitting,
    BuiltInCategory.OST_PlumbingFixtures,
    BuiltInCategory.OST_MechanicalEquipment,
    BuiltInCategory.OST_ElectricalEquipment,
    BuiltInCategory.OST_ElectricalFixtures,
    BuiltInCategory.OST_LightingFixtures,
    BuiltInCategory.OST_LightingDevices,
    BuiltInCategory.OST_DataDevices,
    BuiltInCategory.OST_FireAlarmDevices,
    BuiltInCategory.OST_SecurityDevices,
    BuiltInCategory.OST_NurseCallDevices,
    BuiltInCategory.OST_CommunicationDevices,
    BuiltInCategory.OST_TelephoneDevices,
    BuiltInCategory.OST_Sprinklers,
]

_STRUCT_CATS = [
    BuiltInCategory.OST_StructuralFraming,
    BuiltInCategory.OST_StructuralColumns,
    BuiltInCategory.OST_StructuralFoundation,
    BuiltInCategory.OST_StructConnections,
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Floors,
    BuiltInCategory.OST_GenericModel,
    BuiltInCategory.OST_Assemblies,
]


def _color_to_hex(color):
    if color is None:
        return None
    try:
        if hasattr(color, "IsValid") and not color.IsValid:
            return None
        # IronPython 2.7: prefer %-formatting (str.format {:02X} is unreliable)
        return "#%02X%02X%02X" % (int(color.Red), int(color.Green), int(color.Blue))
    except Exception:
        return None


def _compress_guid_bytes(data):
    """16-byte GUID (bytearray/list) -> 22-char IFC GlobalId."""
    arr = bytearray(data)
    if len(arr) != 16:
        raise ValueError("GUID must be 16 bytes")
    num = 0
    for b in arr:
        num = (num << 8) | (b & 0xFF)
    chars = []
    for _ in range(22):
        chars.append(_IFC_CHARS[num % 64])
        num //= 64
    chars.reverse()
    return "".join(chars)


def unique_id_to_ifc_guid(unique_id):
    """
    Convert Revit UniqueId (45 chars) to IFC GlobalId (22 chars).

    UniqueId = '{8}-{4}-{4}-{4}-{12}-{8hexElementId}'
    Autodesk XORs the element id into the last 4 bytes of the GUID before
    compressing to the IFC alphabet.
    """
    if not unique_id or len(unique_id) < 45:
        return None
    try:
        guid_part = unique_id[0:36]
        elem_hex = unique_id[37:]
        elem_id = int(elem_hex, 16)

        hex_only = guid_part.replace("-", "")
        if len(hex_only) != 32:
            return None

        # .NET Guid byte layout (Data1/2/3 little-endian, Data4 as written)
        b = bytearray(16)
        b[0] = int(hex_only[6:8], 16)
        b[1] = int(hex_only[4:6], 16)
        b[2] = int(hex_only[2:4], 16)
        b[3] = int(hex_only[0:2], 16)
        b[4] = int(hex_only[10:12], 16)
        b[5] = int(hex_only[8:10], 16)
        b[6] = int(hex_only[14:16], 16)
        b[7] = int(hex_only[12:14], 16)
        for i in range(8):
            b[8 + i] = int(hex_only[16 + i * 2 : 18 + i * 2], 16)

        # XOR element id into last 4 bytes (Autodesk IFC exporter behaviour)
        b[12] = b[12] ^ ((elem_id >> 24) & 0xFF)
        b[13] = b[13] ^ ((elem_id >> 16) & 0xFF)
        b[14] = b[14] ^ ((elem_id >> 8) & 0xFF)
        b[15] = b[15] ^ (elem_id & 0xFF)

        return _compress_guid_bytes(b)
    except Exception as ex:
        try:
            print("unique_id_to_ifc_guid failed: %s" % ex)
        except Exception:
            pass
        return None


def _system_line_color(element):
    """Piping / duct / conduit system type LineColor."""
    try:
        mep = getattr(element, "MEPSystem", None)
        if mep is None:
            return None, None, None
        # Some fittings expose MEPSystem as a property that may be None
        if not isinstance(mep, MEPSystem) and mep is None:
            return None, None, None
        doc = element.Document
        st = doc.GetElement(mep.GetTypeId()) if mep else None
        if st is None:
            return None, None, None
        hex_color = None
        if hasattr(st, "LineColor"):
            hex_color = _color_to_hex(st.LineColor)
        try:
            sys_name = mep.Name
        except Exception:
            sys_name = None
        try:
            sys_type = st.Name if hasattr(st, "Name") else None
            # Prefer Element.Name for type
            from Autodesk.Revit.DB import Element as RevitElement

            sys_type = RevitElement.Name.GetValue(st)
        except Exception:
            sys_type = None
        return hex_color, sys_name, sys_type
    except Exception:
        return None, None, None


def _material_color(element):
    """First instance material graphics colour, if any."""
    try:
        doc = element.Document
        mat_ids = element.GetMaterialIds(False)
        if not mat_ids or mat_ids.Count == 0:
            return None
        for mid in mat_ids:
            mat = doc.GetElement(mid)
            if mat is None:
                continue
            hex_color = _color_to_hex(getattr(mat, "Color", None))
            if hex_color and hex_color != "#000000":
                return hex_color
        # Accept black only if that is all we have
        mat = doc.GetElement(mat_ids[0])
        return _color_to_hex(getattr(mat, "Color", None)) if mat else None
    except Exception:
        return None


def _category_line_color(element):
    try:
        cat = element.Category
        if cat is None:
            return None
        return _color_to_hex(cat.LineColor)
    except Exception:
        return None


def _resolve_color(element):
    """Prefer MEP system LineColor, then material, then category."""
    hex_color, sys_name, sys_type = _system_line_color(element)
    if hex_color:
        return {
            "color": hex_color,
            "source": "system",
            "systemName": sys_name,
            "systemType": sys_type,
        }
    mat = _material_color(element)
    if mat:
        return {"color": mat, "source": "material"}
    cat = _category_line_color(element)
    if cat and cat != "#000000":
        return {"color": cat, "source": "category"}
    return None


def build_color_map(doc):
    """
    Build a color map for the given document.

    Returns dict:
      version, documentTitle,
      byElementId, byUniqueId, byIfcGuid,
      elements: [ { elementId, uniqueId, ifcGuid, color, source, ... } ]
    """
    by_element_id = {}
    by_unique_id = {}
    by_ifc_guid = {}
    elements = []

    cats = list(_MEP_CATS) + list(_STRUCT_CATS)
    seen = set()

    for bic in cats:
        try:
            collector = (
                FilteredElementCollector(doc)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
            )
        except Exception:
            continue
        for element in collector:
            try:
                eid = int(element.Id.IntegerValue)
            except Exception:
                continue
            if eid in seen:
                continue
            seen.add(eid)

            info = _resolve_color(element)
            if not info:
                continue

            unique_id = None
            try:
                unique_id = element.UniqueId
            except Exception:
                pass
            ifc_guid = unique_id_to_ifc_guid(unique_id) if unique_id else None

            entry = {
                "elementId": str(eid),
                "uniqueId": unique_id,
                "ifcGuid": ifc_guid,
                "color": info["color"],
                "source": info.get("source"),
                "systemName": info.get("systemName"),
                "systemType": info.get("systemType"),
            }
            elements.append(entry)
            by_element_id[str(eid)] = info["color"]
            if unique_id:
                by_unique_id[unique_id] = info["color"]
            if ifc_guid:
                by_ifc_guid[ifc_guid] = info["color"]

    return {
        "version": 1,
        "documentTitle": doc.Title,
        "count": len(elements),
        "byElementId": by_element_id,
        "byUniqueId": by_unique_id,
        "byIfcGuid": by_ifc_guid,
        "elements": elements,
    }


def write_color_map(doc, path):
    """Write color map JSON to path. Returns the map dict."""
    color_map = build_color_map(doc)
    with open(path, "w") as f:
        json.dump(color_map, f, indent=2)
    return color_map
