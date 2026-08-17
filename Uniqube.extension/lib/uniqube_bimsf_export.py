# -*- coding: utf-8 -*-
"""Export BIMSF_Container (panel id) maps for Uniqube publish.

Builds a sidecar map from THIS document only:
  Revit ElementId / UniqueId / IFC GlobalId -> BIMSF_Container

Structure and MEP are published separately into the same Uniqube project.
The web viewer merges panel ids across loaded models so framing + MEP with
the same BIMSF_Container can be multi-selected together.

MEP prep workflow:
  link Structure -> Prepare MEP Panels (writes BIMSF on MEP) -> remove link ->
  publish MEP alone.
"""

from __future__ import print_function

from collections import defaultdict

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
)

try:
    from Autodesk.Revit.DB import AssemblyInstance
except Exception:
    AssemblyInstance = None

try:
    import uniqube_color_export as _uce
except Exception:
    _uce = None

PARAM_NAME = "BIMSF_Container"
PANEL_NAME_PARAM = "Panel Name"

_CATS = [
    BuiltInCategory.OST_StructuralFraming,
    BuiltInCategory.OST_StructuralColumns,
    BuiltInCategory.OST_StructuralFoundation,
    BuiltInCategory.OST_StructConnections,
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Floors,
    BuiltInCategory.OST_GenericModel,
    BuiltInCategory.OST_Assemblies,
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
    BuiltInCategory.OST_Sprinklers,
]


def _param_string(element, name):
    try:
        p = element.LookupParameter(name)
        if p and p.HasValue:
            if p.StorageType.ToString() == "String":
                return (p.AsString() or "").strip()
            vs = p.AsValueString()
            if vs:
                return vs.strip()
    except Exception:
        pass
    return None


def panel_display_name(raw):
    """Clean id for UI lists (strip group prefixes / leading *)."""
    if not raw:
        return ""
    text = str(raw).strip()
    for prefix in (
        "BIMSF Panel ",
        "BIMSF_Panel_",
        "BIMSF Panel_",
        "BIMSF_Panel ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    if text.startswith("*"):
        text = text[1:].strip()
    return text


def _to_ifc_guid(unique_id):
    if _uce is not None:
        try:
            return _uce.unique_id_to_ifc_guid(unique_id)
        except Exception:
            return None
    return None


def build_bimsf_map(doc):
    """
    Returns dict:
      version, documentTitle, count,
      byElementId, byUniqueId, byIfcGuid,
      byPanel: { panelId: { displayName, elementIds[], count } },
      panels: [ { id, displayName, count } ],
      elements: [ { elementId, uniqueId, ifcGuid, bimsf, panelName, category } ]
    """
    by_element_id = {}
    by_unique_id = {}
    by_ifc_guid = {}
    by_panel = defaultdict(lambda: {"displayName": "", "elementIds": [], "count": 0, "bimsf": ""})
    elements = []
    seen = set()
    # Assemblies with BIMSF - expand to members after first pass (FT / truss assemblies)
    assembly_seeds = []  # (assembly_element, bimsf, display)

    def _add_element(element, bimsf, display=None, panel_name=None):
        try:
            eid = int(element.Id.IntegerValue)
        except Exception:
            return False
        if eid in seen:
            return False
        seen.add(eid)

        display = display or panel_display_name(bimsf) or bimsf
        panel_name = panel_name or _param_string(element, PANEL_NAME_PARAM) or display

        unique_id = None
        try:
            unique_id = element.UniqueId
        except Exception:
            pass
        ifc_guid = _to_ifc_guid(unique_id) if unique_id else None

        cat_name = None
        try:
            cat_name = element.Category.Name if element.Category else None
        except Exception:
            pass

        entry = {
            "elementId": str(eid),
            "uniqueId": unique_id,
            "ifcGuid": ifc_guid,
            "bimsf": bimsf,
            "panelName": panel_name,
            "displayName": display,
            "category": cat_name,
        }
        elements.append(entry)
        by_element_id[str(eid)] = bimsf
        if unique_id:
            by_unique_id[unique_id] = bimsf
        if ifc_guid:
            by_ifc_guid[ifc_guid] = bimsf

        key = display or bimsf
        bucket = by_panel[key]
        bucket["displayName"] = display or bimsf
        bucket["bimsf"] = bimsf
        bucket["elementIds"].append(str(eid))
        bucket["count"] += 1
        return True

    for bic in _CATS:
        try:
            collector = (
                FilteredElementCollector(doc)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
            )
        except Exception:
            continue
        for element in collector:
            bimsf = _param_string(element, PARAM_NAME)
            if not bimsf:
                # Assemblies / trusses sometimes carry the id only on Mark (e.g. FT-1001)
                try:
                    mk = element.LookupParameter("Mark")
                    mark_val = None
                    if mk and mk.HasValue:
                        mark_val = (mk.AsString() or mk.AsValueString() or "").strip()
                    if mark_val and (
                        mark_val.upper().startswith("FT-")
                        or mark_val.upper().startswith("FT_")
                    ):
                        bimsf = mark_val
                except Exception:
                    pass
            if not bimsf:
                continue

            display = panel_display_name(bimsf) or bimsf
            _add_element(element, bimsf, display=display)

            # Remember assemblies so we can tag all members (geometry in IFC)
            try:
                is_asm = False
                if AssemblyInstance is not None and isinstance(element, AssemblyInstance):
                    is_asm = True
                else:
                    try:
                        is_asm = element.Category and (
                            element.Category.Id.IntegerValue
                            == int(BuiltInCategory.OST_Assemblies)
                        )
                    except Exception:
                        is_asm = False
                if is_asm:
                    assembly_seeds.append((element, bimsf, display))
            except Exception:
                pass

    # Floor-truss / panel assemblies: IFC exports member framing, not the assembly.
    # Propagate BIMSF_Container onto every member ElementId in the publish map.
    for asm, bimsf, display in assembly_seeds:
        try:
            member_ids = asm.GetMemberIds()
        except Exception:
            continue
        for mid in member_ids:
            try:
                member = doc.GetElement(mid)
            except Exception:
                member = None
            if member is None:
                continue
            _add_element(member, bimsf, display=display)

    panels = []
    for key, info in sorted(by_panel.items(), key=lambda x: x[0].lower()):
        panels.append(
            {
                "id": key,
                "displayName": info.get("displayName") or key,
                "bimsf": info.get("bimsf") or key,
                "count": info.get("count") or 0,
            }
        )

    return {
        "version": 1,
        "documentTitle": doc.Title,
        "count": len(elements),
        "panelCount": len(panels),
        "byElementId": by_element_id,
        "byUniqueId": by_unique_id,
        "byIfcGuid": by_ifc_guid,
        "byPanel": {
            k: {
                "displayName": v.get("displayName"),
                "bimsf": v.get("bimsf"),
                "count": v.get("count"),
                "elementIds": v.get("elementIds"),
            }
            for k, v in by_panel.items()
        },
        "panels": panels,
        "elements": elements,
    }
