# -*- coding: utf-8 -*-
"""Shared helpers for BIMSF panel scripts — framing map, MEP zone, link support."""
import random

from pyrevit import revit, DB
from System.Collections.Generic import List


PARAM_NAME = "BIMSF_Container"
PANEL_NAME_PARAM = "Panel Name"

# Legacy group type prefixes (ours and MWF) — panel name only in UI.
_GROUP_PREFIXES = (
    "BIMSF Panel ",
    "BIMSF_Panel_",
    "BIMSF Panel_",
    "BIMSF_Panel ",
)


def strip_group_prefix(name):
    """Remove 'BIMSF Panel …' / 'BIMSF_Panel_…' from a group type name."""
    if not name:
        return ""
    text = name.strip()
    for prefix in _GROUP_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def panel_display_name(raw):
    """Clean panel name for lists/schedules — no group prefix, no leading '*'."""
    name = strip_group_prefix(raw)
    if name.startswith("*"):
        name = name[1:]
    return name.strip()


def panel_group_name(container):
    """Group type name = BIMSF_Container value only (e.g. *ELB-2001)."""
    return (container or "").strip()


def group_matches_panel(group_name, panel_id):
    """True if a group type name belongs to the given panel id."""
    if not group_name or not panel_id:
        return False
    g = strip_group_prefix(group_name)
    p = (panel_id or "").strip()
    return g == p or panel_display_name(g) == panel_display_name(p)


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


def map_link_framing_by_container(doc):
    """Return {panel_id: [(link_inst, elem), ...]} for linked framing only.

    Each member is matched by its own BIMSF_Container — not the whole link
    model — so only that panel's studs/tracks are highlighted.
    """
    result = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
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
                if pid:
                    result.setdefault(pid, []).append((link_inst, beam))
    return result


def count_link_framing(link_framing_map):
    """Return {panel_id: member_count} from map_link_framing_by_container."""
    return {pid: len(items) for pid, items in link_framing_map.items()}


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


def _set_container(elem, pid):
    p = elem.LookupParameter(PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            p.Set(pid)
            return True
        except Exception:
            return False
    return False


def set_panel_labels(elem, panel_id):
    """Write BIMSF_Container and Panel Name on a host element."""
    display = panel_display_name(panel_id)
    _set_container(elem, panel_id)
    p = elem.LookupParameter(PANEL_NAME_PARAM)
    if p and not p.IsReadOnly:
        try:
            p.Set(display)
        except Exception:
            pass


def map_framing_link_sources(doc):
    """Return {panel_id: link_doc_title} from linked structural framing."""
    sources = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        link_title = link_doc.Title or "linked model"
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
                if pid:
                    sources.setdefault(pid, link_title)
    return sources


def preview_mep_counts(doc, panel_elements, link_zones):
    """Return {panel_id: host_mep_count} for elements in exactly one panel."""
    mep_assignments, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    counts = {pid: 0 for pid in get_all_panel_ids(panel_elements, link_zones)}
    for _eid, pids in mep_assignments.items():
        if len(pids) == 1:
            pid = list(pids)[0]
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def preview_crossing_mep(doc, panel_elements, link_zones):
    """Return count of host MEP elements assigned to more than one panel."""
    mep_assignments, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    return sum(1 for pids in mep_assignments.values() if len(pids) > 1)


def build_panel_catalog(doc):
    """Build panel rows for the MEP grouping UI.

    Each row: pid, display, source, mep_count, link_name, host_framing,
    link_framing.
    """
    panel_elements = map_framing(doc)
    link_zones = map_framing_from_links(doc)
    link_sources = map_framing_link_sources(doc)
    link_framing = map_link_framing_by_container(doc)
    link_framing_counts = count_link_framing(link_framing)
    mep_counts = preview_mep_counts(doc, panel_elements, link_zones)
    all_pids = get_all_panel_ids(panel_elements, link_zones)

    rows = []
    for pid in sorted(all_pids, key=lambda x: panel_display_name(x).lower()):
        host_count = len(panel_elements.get(pid, []))
        link_count = link_framing_counts.get(pid, 0)
        in_link = pid in link_zones or link_count > 0
        if host_count and in_link:
            source = "host + link"
        elif in_link:
            source = "link"
        else:
            source = "host"
        rows.append({
            "pid": pid,
            "display": panel_display_name(pid),
            "source": source,
            "mep_count": mep_counts.get(pid, 0),
            "link_name": link_sources.get(pid, ""),
            "host_framing": host_count,
            "link_framing": link_count,
        })
    return rows, panel_elements, link_zones, link_framing


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

    Host elements can receive BIMSF_Container writes. Linked-model elements
    are read-only from the host — they are returned for view coloring only.

    Returns:
      host_assignments: {ElementId: set(panel_ids)} for host elements
      link_assignments: list of (link_inst, elem, set(panel_ids))
      stats: dict with link_matched count (for coloring)
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
    stats = {"link_matched": 0}

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
                matched = set()
                cat = elem.Category
                is_framing = (
                    cat is not None
                    and cat.BuiltInCategory
                    == DB.BuiltInCategory.OST_StructuralFraming
                )
                if is_framing:
                    # Panel studs/tracks: match BIMSF_Container only (not
                    # spatial zone — avoids coloring the whole link).
                    p_param = elem.LookupParameter(PARAM_NAME)
                    if p_param and p_param.HasValue:
                        pid = p_param.AsString()
                        if pid and pid in all_pids:
                            matched.add(pid)
                else:
                    for pid, outline in panel_outlines.items():
                        if _link_bbox_in_outline(elem, transform, outline):
                            matched.add(pid)
                if not matched:
                    continue
                link_assignments.append((link_inst, elem, matched))
                stats["link_matched"] += 1

    return host_assignments, link_assignments, stats


def _view_color_kit(doc):
    """Fill pattern + red override + factory for random panel colors."""
    fill_pattern = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FillPatternElement)
        .FirstElement()
    )
    red_settings = DB.OverrideGraphicSettings()
    if fill_pattern:
        red_settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
        red_settings.SetSurfaceForegroundPatternColor(DB.Color(255, 0, 0))

    def panel_settings():
        r = random.randint(0, 180)
        g = random.randint(50, 255)
        b = random.randint(50, 255)
        settings = DB.OverrideGraphicSettings()
        if fill_pattern:
            settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
            settings.SetSurfaceForegroundPatternColor(DB.Color(r, g, b))
        return settings

    return red_settings, panel_settings


def combine_panels_group_color(
    doc,
    view,
    selected,
    panel_elements,
    link_zones,
    link_framing=None,
    tag_mep=True,
):
    """Group host panel + MEP and color like Panel Combine (Color).

    Host framing and MEP go into Revit groups. Linked panel framing is
    colored in the active view by BIMSF_Container (cannot be grouped in
    the host). Crossing MEP is marked red.
    """
    if link_framing is None:
        link_framing = map_link_framing_by_container(doc)

    mep_assignments, link_assignments, link_stats = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    red_settings, panel_settings = _view_color_kit(doc)

    stats = {
        "groups": 0,
        "mep_tagged": 0,
        "host_framing": 0,
        "link_framing_colored": 0,
        "crossing_count": 0,
        "skipped_empty": 0,
        "link_matched": link_stats.get("link_matched", 0),
    }

    processed_crossings = set()

    for pid in selected:
        settings = panel_settings()
        group_ids = List[DB.ElementId]()

        for el in panel_elements.get(pid, []):
            view.SetElementOverrides(el.Id, settings)
            group_ids.Add(el.Id)
            stats["host_framing"] += 1

        for link_inst, elem in link_framing.get(pid, []):
            if set_link_element_override(view, link_inst, elem, settings):
                stats["link_framing_colored"] += 1

        for eid, pids in mep_assignments.items():
            el = doc.GetElement(eid)
            if el is None:
                continue
            if len(pids) == 1 and list(pids)[0] == pid:
                group_ids.Add(eid)
                view.SetElementOverrides(eid, settings)
                if tag_mep:
                    set_panel_labels(el, pid)
                    stats["mep_tagged"] += 1
            elif len(pids) > 1 and pid in pids:
                view.SetElementOverrides(eid, red_settings)
                if eid not in processed_crossings:
                    processed_crossings.add(eid)
                    stats["crossing_count"] += 1
                if tag_mep:
                    p = el.LookupParameter(PARAM_NAME)
                    if p and not p.IsReadOnly:
                        try:
                            p.Set("")
                        except Exception:
                            pass

        for link_inst, elem, pids in link_assignments:
            is_framing = (
                elem.Category is not None
                and elem.Category.BuiltInCategory
                == DB.BuiltInCategory.OST_StructuralFraming
            )
            if is_framing:
                continue
            if len(pids) == 1 and list(pids)[0] == pid:
                set_link_element_override(view, link_inst, elem, settings)
            elif len(pids) > 1 and pid in pids:
                set_link_element_override(view, link_inst, elem, red_settings)
                stats["crossing_count"] += 1

        if group_ids.Count > 1:
            try:
                new_grp = doc.Create.NewGroup(group_ids)
                new_grp.GroupType.Name = panel_group_name(pid)
                stats["groups"] += 1
            except Exception:
                pass
        elif group_ids.Count == 0 and not link_framing.get(pid):
            stats["skipped_empty"] += 1

    return stats


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
