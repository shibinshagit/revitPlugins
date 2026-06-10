# -*- coding: utf-8 -*-
"""Shared helpers for BIMSF panel scripts — framing map, MEP zone, link support."""
from pyrevit import revit, DB
from System.Collections.Generic import List


PARAM_NAME = "BIMSF_Container"

MEP_CATS = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_ElectricalFixtures,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_DuctAccessory,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_LightingFixtures,
    DB.BuiltInCategory.OST_LightingDevices,
    DB.BuiltInCategory.OST_ElectricalEquipment,
    DB.BuiltInCategory.OST_MechanicalEquipment,
    DB.BuiltInCategory.OST_Sprinklers,
]

# Host + link spatial assignment for panel grouping workflows.
LINK_ASSIGN_CATS = MEP_CATS + [DB.BuiltInCategory.OST_StructuralFraming]

ZONE_PAD_FT = 0.2


def get_mep_filter():
    return DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](MEP_CATS))


def get_link_assign_filter():
    return DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](LINK_ASSIGN_CATS))


def map_framing(doc):
    """Return {panel_id: [element, ...]} from host structural framing."""
    all_framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    panel_elements = {}
    for beam in all_framing:
        p_param = beam.LookupParameter(PARAM_NAME)
        if p_param and p_param.HasValue:
            pid = p_param.AsString()
            if not pid:
                continue
            if pid not in panel_elements:
                panel_elements[pid] = []
            panel_elements[pid].append(beam)
    return panel_elements


def map_framing_from_links(doc):
    """Return {panel_id: [(host_min, host_max), ...]} from linked framing.

    Linked framing cannot be grouped in the host model, but its bounding
    boxes (transformed to host coordinates) define panel zones so host and
    linked MEP/electrical can be assigned to the same panel number.
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
        framing = (
            DB.FilteredElementCollector(link_doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for beam in framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if not pid:
                    continue
                bbox = beam.get_BoundingBox(None)
                if bbox is None:
                    continue
                t_min = transform.OfPoint(bbox.Min)
                t_max = transform.OfPoint(bbox.Max)
                if pid not in link_zones:
                    link_zones[pid] = []
                link_zones[pid].append((t_min, t_max))
    return link_zones


def get_all_panel_ids(panel_elements, link_zones=None):
    ids = set(panel_elements.keys())
    if link_zones:
        ids.update(link_zones.keys())
    return ids


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


def _panel_outline(min_pt, max_pt):
    pad = DB.XYZ(ZONE_PAD_FT, ZONE_PAD_FT, ZONE_PAD_FT)
    return DB.Outline(min_pt.Subtract(pad), max_pt.Add(pad))


def _bbox_intersects_outline(bbox, outline):
    if bbox is None:
        return False
    bb_outline = DB.Outline(bbox.Min, bbox.Max)
    return outline.Intersects(bb_outline, 0.001)


def _get_container(elem):
    p = elem.LookupParameter(PARAM_NAME)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def _set_container(elem, pid):
    p = elem.LookupParameter(PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            p.Set(pid)
            return True
        except Exception:
            return False
    return False


def _set_container_in_link(link_doc, elem, pid):
    """Write BIMSF_Container on a linked-model element when editable."""
    t = DB.Transaction(link_doc, "UNIQUBE: Set Panel Container")
    t.Start()
    try:
        ok = _set_container(elem, pid)
        if ok:
            t.Commit()
            return True
        t.RollBack()
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass
    return False


def _host_bbox_in_outline(elem, outline):
    bbox = elem.get_BoundingBox(None)
    return _bbox_intersects_outline(bbox, outline)


def _link_bbox_in_outline(elem, transform, outline):
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return False
    t_min = transform.OfPoint(bbox.Min)
    t_max = transform.OfPoint(bbox.Max)
    host_bb = DB.BoundingBoxXYZ()
    host_bb.Min = DB.XYZ(
        min(t_min.X, t_max.X),
        min(t_min.Y, t_max.Y),
        min(t_min.Z, t_max.Z),
    )
    host_bb.Max = DB.XYZ(
        max(t_min.X, t_max.X),
        max(t_min.Y, t_max.Y),
        max(t_min.Z, t_max.Z),
    )
    return _bbox_intersects_outline(host_bb, outline)


def assign_mep_to_panels(doc, panel_elements, link_zones=None, assign_links=True):
    """Assign host + linked disciplines to panels by spatial zone.

    Returns:
      host_assignments: {ElementId: set(panel_ids)} for host elements
      link_assignments: list of (link_inst, elem, set(panel_ids))
      stats: dict with counts of linked elements tagged
    """
    mep_filter = get_mep_filter()
    assign_filter = get_link_assign_filter()

    all_mep = (
        DB.FilteredElementCollector(doc)
        .WherePasses(mep_filter)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    host_assignments = {}
    for item in all_mep:
        host_assignments[item.Id] = set()

    all_pids = get_all_panel_ids(panel_elements, link_zones)
    panel_outlines = {}
    for pid in all_pids:
        host_elements = panel_elements.get(pid, [])
        lz = link_zones.get(pid, []) if link_zones else []
        min_pt, max_pt = compute_panel_bbox(host_elements, lz)
        panel_outlines[pid] = _panel_outline(min_pt, max_pt)

    for pid, outline in panel_outlines.items():
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
            .ToElements()
        )
        for item in nearby:
            if item.Id in host_assignments:
                host_assignments[item.Id].add(pid)

    link_assignments = []
    stats = {"link_tagged": 0, "link_readonly": 0}

    if assign_links and link_zones:
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
            candidates = (
                DB.FilteredElementCollector(link_doc)
                .WherePasses(assign_filter)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for elem in candidates:
                existing = _get_container(elem)
                matched = set()
                for pid, outline in panel_outlines.items():
                    if _link_bbox_in_outline(elem, transform, outline):
                        matched.add(pid)
                if not matched:
                    continue
                link_assignments.append((link_inst, elem, matched))
                if len(matched) == 1:
                    pid = list(matched)[0]
                    if existing != pid:
                        if _set_container_in_link(link_doc, elem, pid):
                            stats["link_tagged"] += 1
                        else:
                            stats["link_readonly"] += 1

    return host_assignments, link_assignments, stats


def set_link_element_override(view, link_inst, elem, override_settings):
    """Apply a graphic override to one element inside a linked model."""
    try:
        link_id = DB.LinkElementId(link_inst.Id, elem.Id)
        view.SetElementOverrides(link_id, override_settings)
        return True
    except Exception:
        return False


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
