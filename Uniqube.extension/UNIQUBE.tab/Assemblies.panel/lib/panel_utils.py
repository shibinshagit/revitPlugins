# -*- coding: utf-8 -*-
"""Shared helpers for BIMSF panel scripts — framing map, MEP zone, link support."""
from pyrevit import revit, DB
from System.Collections.Generic import List


PARAM_NAME = "BIMSF_Container"

# Fallback parameter names for Vertex BD / IFC imports that lack BIMSF_Container
FALLBACK_PARAM_NAMES = [
    "BIMSF_Container",
    "MasterContainer",
    "BIMSF_Id",
    "IfcTag",
    "IfcName",
    "Mark",
    "Assembly Name",
]

STRUCT_CATS = [
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralColumns,
]

MEP_CATS = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_ElectricalFixtures,
]


def get_mep_filter():
    return DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](MEP_CATS))


def _read_panel_id(element):
    """Try BIMSF_Container first, then fall back to IFC/Vertex BD properties."""
    for pname in FALLBACK_PARAM_NAMES:
        p = element.LookupParameter(pname)
        if p and p.HasValue:
            val = p.AsString()
            if val and val.strip():
                return val.strip()
    return None


def map_framing(doc):
    """Return {panel_id: [element, ...]} from host structural framing + columns."""
    panel_elements = {}
    for bic in STRUCT_CATS:
        col = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for el in col:
            pid = _read_panel_id(el)
            if not pid:
                continue
            if pid not in panel_elements:
                panel_elements[pid] = []
            panel_elements[pid].append(el)
    return panel_elements


def map_framing_from_links(doc):
    """Return {panel_id: [(min, max), ...]} from linked model framing.

    Reads BIMSF_Container first; falls back to IFC/Vertex BD properties.
    Scans both Structural Framing and Structural Columns in every loaded link.
    """
    link_zones = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        transform = link_inst.GetTotalTransform()
        for bic in STRUCT_CATS:
            elements = (
                DB.FilteredElementCollector(link_doc)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for el in elements:
                pid = _read_panel_id(el)
                if not pid:
                    continue
                bbox = el.get_BoundingBox(None)
                if bbox is None:
                    continue
                t_min = transform.OfPoint(bbox.Min)
                t_max = transform.OfPoint(bbox.Max)
                if pid not in link_zones:
                    link_zones[pid] = []
                link_zones[pid].append((t_min, t_max))
    return link_zones


def compute_panel_bbox(elements, link_bboxes=None):
    """Compute combined bounding box for a panel's framing + link bboxes."""
    min_pt = DB.XYZ(10000, 10000, 10000)
    max_pt = DB.XYZ(-10000, -10000, -10000)

    for el in elements:
        bbox = el.get_BoundingBox(None)
        if bbox:
            min_pt = DB.XYZ(
                min(min_pt.X, bbox.Min.X),
                min(min_pt.Y, bbox.Min.Y),
                min(min_pt.Z, bbox.Min.Z),
            )
            max_pt = DB.XYZ(
                max(max_pt.X, bbox.Max.X),
                max(max_pt.Y, bbox.Max.Y),
                max(max_pt.Z, bbox.Max.Z),
            )

    if link_bboxes:
        for bb_min, bb_max in link_bboxes:
            min_pt = DB.XYZ(
                min(min_pt.X, bb_min.X),
                min(min_pt.Y, bb_min.Y),
                min(min_pt.Z, bb_min.Z),
            )
            max_pt = DB.XYZ(
                max(max_pt.X, bb_max.X),
                max(max_pt.Y, bb_max.Y),
                max(max_pt.Z, bb_max.Z),
            )

    return min_pt, max_pt


def assign_mep_to_panels(doc, panel_elements, link_zones=None):
    """Return {ElementId: set(panel_ids)} for MEP elements in the host model."""
    mep_filter = get_mep_filter()
    all_mep = (
        DB.FilteredElementCollector(doc)
        .WherePasses(mep_filter)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mep_assignments = {}
    for item in all_mep:
        mep_assignments[item.Id] = set()

    all_pids = set(panel_elements.keys())
    if link_zones:
        all_pids.update(link_zones.keys())

    for pid in all_pids:
        host_elements = panel_elements.get(pid, [])
        lz = link_zones.get(pid, []) if link_zones else []
        min_pt, max_pt = compute_panel_bbox(host_elements, lz)

        zone = DB.Outline(
            min_pt.Add(DB.XYZ(-0.2, -0.2, -0.2)),
            max_pt.Add(DB.XYZ(0.2, 0.2, 0.2)),
        )
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WherePasses(DB.BoundingBoxIntersectsFilter(zone))
            .ToElements()
        )
        for item in nearby:
            if item.Id in mep_assignments:
                mep_assignments[item.Id].add(pid)

    return mep_assignments


def choose_panels(panel_ids):
    """Show a dialog letting user pick single panel, multiple, or all."""
    sorted_ids = sorted(panel_ids)
    options = ["All panels ({})".format(len(sorted_ids))] + sorted_ids
    from pyrevit import forms
    selected = forms.SelectFromList.show(
        options,
        title="UNIQUBE — Select Panel(s)",
        multiselect=True,
        button_name="Select",
    )
    if not selected:
        return None
    if any("All panels" in s for s in selected):
        return sorted_ids
    return selected
