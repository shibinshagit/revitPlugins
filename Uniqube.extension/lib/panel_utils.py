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
    """Group type name — clean panel id (e.g. ELB-1001)."""
    name = panel_display_name(container)
    return name or (container or "").strip()


def panel_ids_match(a, b):
    """True if two BIMSF_Container values are the same panel."""
    if not a or not b:
        return False
    return panel_display_name(a).lower() == panel_display_name(b).lower()


def merge_framing_for_panel(framing_map, pid):
    """Collect host framing for a panel (handles * prefix variants)."""
    elements = []
    seen = set()
    for key, items in framing_map.items():
        if not panel_ids_match(key, pid):
            continue
        for el in items:
            eid = el.Id.IntegerValue
            if eid not in seen:
                seen.add(eid)
                elements.append(el)
    return elements


def merge_link_framing_for_panel(link_framing, pid):
    """Collect linked framing for a panel (handles * prefix variants)."""
    pairs = []
    seen = set()
    for key, items in link_framing.items():
        if not panel_ids_match(key, pid):
            continue
        for link_inst, elem in items:
            uid = elem.UniqueId
            if uid not in seen:
                seen.add(uid)
                pairs.append((link_inst, elem))
    return pairs


def _assignment_matches_panel(assigned_pids, pid):
    if len(assigned_pids) != 1:
        return False
    return panel_ids_match(list(assigned_pids)[0], pid)


def _assignment_crosses_panel(assigned_pids, pid):
    if len(assigned_pids) <= 1:
        return False
    return any(panel_ids_match(p, pid) for p in assigned_pids)


def group_matches_panel(group_name, panel_id):
    """True if a group type name belongs to the given panel id."""
    if not group_name or not panel_id:
        return False
    return panel_ids_match(strip_group_prefix(group_name), panel_id)


MEP_CATS = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeAccessory,
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
    DB.BuiltInCategory.OST_PlumbingFixtures,
    DB.BuiltInCategory.OST_GenericModel,
]

BEND_OR_FITTING_PARAM = "Bend or Fitting"

FITTING_CATS = set([
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_PipeAccessory,
    DB.BuiltInCategory.OST_DuctAccessory,
])

# Host + link spatial assignment for panel grouping workflows.
LINK_ASSIGN_CATS = MEP_CATS + [DB.BuiltInCategory.OST_StructuralFraming]

ZONE_PAD_FT = 1.0


def canonical_panel_id(pid, known_pids):
    """Return the framing model's exact panel id string (e.g. *ELB-1001)."""
    if not pid:
        return pid
    for kp in known_pids:
        if panel_ids_match(kp, pid):
            return kp
    return pid


def _build_panel_outlines(panel_elements, link_zones):
    outlines = {}
    all_pids = get_all_panel_ids(panel_elements, link_zones)
    for pid in all_pids:
        host_elems = merge_framing_for_panel(panel_elements, pid)
        lz = link_zones.get(pid, []) if link_zones else []
        if not host_elems and not lz:
            for key in panel_elements:
                if panel_ids_match(key, pid):
                    host_elems = panel_elements[key]
                    break
            if link_zones:
                for key in link_zones:
                    if panel_ids_match(key, pid):
                        lz = link_zones[key]
                        break
        min_pt, max_pt = compute_panel_bbox(host_elems, lz)
        outlines[pid] = _panel_outline(min_pt, max_pt)
    return outlines


def _point_in_outline(pt, outline):
    try:
        return outline.Contains(pt, 0.001)
    except Exception:
        return False


def _endpoint_panels(elem, panel_outlines):
    """Return (start_panels, end_panels) for an MEPCurve."""
    loc = elem.Location
    if not isinstance(loc, DB.LocationCurve):
        return set(), set()
    curve = loc.Curve
    if curve is None:
        return set(), set()
    try:
        start = curve.GetEndPoint(0)
        end = curve.GetEndPoint(1)
    except Exception:
        return set(), set()
    start_p = {
        pid for pid, outline in panel_outlines.items()
        if _point_in_outline(start, outline)
    }
    end_p = {
        pid for pid, outline in panel_outlines.items()
        if _point_in_outline(end, outline)
    }
    return start_p, end_p


def _refine_curve_assignments(doc, host_assignments, panel_outlines):
    """Assign conduits/pipes from curve endpoints inside panel zones."""
    refined = 0
    for eid in list(host_assignments.keys()):
        el = doc.GetElement(eid)
        if el is None or not isinstance(el, DB.MEPCurve):
            continue
        start_p, end_p = _endpoint_panels(el, panel_outlines)
        chosen = None
        if len(start_p) == 1 and start_p == end_p:
            chosen = list(start_p)[0]
        elif len(start_p) == 1 and not end_p:
            chosen = list(start_p)[0]
        elif len(end_p) == 1 and not start_p:
            chosen = list(end_p)[0]
        else:
            common = start_p & end_p
            if len(common) == 1:
                chosen = list(common)[0]
        if chosen and host_assignments[eid] != set([chosen]):
            host_assignments[eid] = set([chosen])
            refined += 1
    return refined


def _is_mep_connection(el):
    """True for fittings / accessories that join MEP runs (panel crossing points)."""
    cat = el.Category
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory in FITTING_CATS
    except Exception:
        return False


def _neighbor_panel_ids(el, host_assignments, panel_outlines, valid_ids):
    """Panel ids reached from one element via connectors or curve endpoints."""
    panels = set()
    for nb in _mep_network_neighbors(el, valid_ids):
        nb_pids = host_assignments.get(nb.Id, set())
        if len(nb_pids) == 1:
            panels.add(list(nb_pids)[0])
        elif isinstance(nb, DB.MEPCurve):
            start_p, end_p = _endpoint_panels(nb, panel_outlines)
            panels.update(start_p)
            panels.update(end_p)
    if isinstance(el, DB.MEPCurve):
        start_p, end_p = _endpoint_panels(el, panel_outlines)
        panels.update(start_p)
        panels.update(end_p)
    return panels


def _is_panel_crossing_connection(el, host_assignments, panel_outlines, valid_ids):
    """True for fittings or pipe/conduit segments that join two different panels."""
    if _is_mep_connection(el):
        panels = _neighbor_panel_ids(el, host_assignments, panel_outlines, valid_ids)
        names = set()
        for p in panels:
            if p:
                names.add(panel_display_name(p).lower())
        return len(names) > 1

    if isinstance(el, DB.MEPCurve):
        start_p, end_p = _endpoint_panels(el, panel_outlines)
        start_names = {
            panel_display_name(p).lower() for p in start_p if p
        }
        end_names = {
            panel_display_name(p).lower() for p in end_p if p
        }
        if len(start_names) == 1 and len(end_names) == 1:
            return start_names != end_names
    return False


def _count_crossing_connections(doc, host_assignments, panel_outlines):
    """Count fittings and connecting pipes/conduits that join two different panels."""
    valid_ids = set(eid.IntegerValue for eid in host_assignments.keys())
    count = 0
    for eid in host_assignments:
        el = doc.GetElement(eid)
        if el is None:
            continue
        if _is_panel_crossing_connection(
            el, host_assignments, panel_outlines, valid_ids
        ):
            count += 1
    return count


def _resolve_panel_for_element(el, host_assignments, panel_outlines, spatial_pids):
    """Pick one panel using connectors, curve endpoints, or spatial overlap."""
    valid_ids = set(eid.IntegerValue for eid in host_assignments.keys())
    conn_panels = set()
    for nb in _mep_network_neighbors(el, valid_ids):
        nb_p = host_assignments.get(nb.Id, set())
        if len(nb_p) == 1:
            conn_panels.add(list(nb_p)[0])
    if len(conn_panels) == 1:
        return list(conn_panels)[0]

    if isinstance(el, DB.MEPCurve):
        start_p, end_p = _endpoint_panels(el, panel_outlines)
        if len(start_p) == 1 and start_p == end_p:
            return list(start_p)[0]
        if len(start_p) == 1 and not end_p:
            return list(start_p)[0]
        if len(end_p) == 1 and not start_p:
            return list(end_p)[0]
        common = start_p & end_p
        if len(common) == 1:
            return list(common)[0]
        endpoint_hit = start_p | end_p
        if spatial_pids:
            overlap = endpoint_hit & set(spatial_pids)
            if len(overlap) == 1:
                return list(overlap)[0]

    if len(spatial_pids) == 1:
        return list(spatial_pids)[0]
    return None


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


def _set_container(elem, pid, known_pids=None):
    pid = canonical_panel_id(pid, known_pids or [])
    candidates = []
    for val in [pid, panel_display_name(pid)]:
        if val and val not in candidates:
            candidates.append(val)
    disp = panel_display_name(pid)
    if disp:
        star = "*" + disp
        if star not in candidates:
            candidates.append(star)
    p = elem.LookupParameter(PARAM_NAME)
    if p is None or p.IsReadOnly:
        return False
    for val in candidates:
        try:
            p.Set(val)
            return True
        except Exception:
            continue
    return False


def _clear_container(elem):
    p = elem.LookupParameter(PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            p.Set("")
            return True
        except Exception:
            return False
    return False


def _is_conduit_bend(elem):
    """True for conduit fittings marked as bends (excluded from panel schedules)."""
    bend_param = elem.LookupParameter(BEND_OR_FITTING_PARAM)
    if bend_param and bend_param.HasValue:
        val = bend_param.AsString()
        if val and "bend" in val.lower():
            return True
    return False


def _panel_in_selection(pid, selected):
    if not selected:
        return True
    return any(panel_ids_match(pid, s) for s in selected)


def _read_container_value(elem):
    p = elem.LookupParameter(PARAM_NAME)
    if p and p.HasValue:
        val = p.AsString()
        if val and val.strip():
            return val.strip()
    return None


def _get_mep_connector_manager(elem):
    """Return ConnectorManager for MEPCurve, fittings, or equipment."""
    try:
        if isinstance(elem, DB.MEPCurve):
            return elem.ConnectorManager
    except Exception:
        pass
    try:
        mep_model = elem.MEPModel
        if mep_model is not None:
            return mep_model.ConnectorManager
    except Exception:
        pass
    try:
        return elem.ConnectorManager
    except Exception:
        pass
    return None


def _mep_network_neighbors(elem, valid_ids):
    """Return connected host MEP elements via physical connectors."""
    result = []
    seen = set()
    cm = _get_mep_connector_manager(elem)
    if cm is None:
        return result
    try:
        connectors = cm.Connectors
    except Exception:
        return result
    if connectors is None:
        return result
    for conn in connectors:
        try:
            refs = conn.AllRefs
        except Exception:
            continue
        if refs is None:
            continue
        for ref in refs:
            if ref is None:
                continue
            try:
                owner = ref.Owner
            except Exception:
                continue
            if owner is None or owner.Id == elem.Id:
                continue
            key = owner.Id.IntegerValue
            if key not in valid_ids or key in seen:
                continue
            seen.add(key)
            result.append(owner)
    return result


def _seed_assignments_from_parameters(doc, host_assignments, known_pids):
    """Use existing BIMSF_Container on elements as propagation seeds."""
    seeded = 0
    for eid, pids in host_assignments.items():
        if pids:
            continue
        el = doc.GetElement(eid)
        if el is None:
            continue
        existing = _read_container_value(el)
        if not existing:
            continue
        for kp in known_pids:
            if panel_ids_match(existing, kp):
                host_assignments[eid] = set([kp])
                seeded += 1
                break
    return seeded


def propagate_panel_assignments(doc, host_assignments, max_passes=100):
    """Extend panel assignment along connected MEP runs.

    Unassigned elements (0 panels) or ambiguous bbox hits (2+ panels) inherit
    the panel when all resolved connected neighbors share one panel id.
    """
    valid_ids = set(eid.IntegerValue for eid in host_assignments.keys())
    element_cache = {}
    propagated = 0

    def get_elem(eid):
        key = eid.IntegerValue
        if key not in element_cache:
            element_cache[key] = doc.GetElement(eid)
        return element_cache[key]

    def neighbor_panels(eid):
        el = get_elem(eid)
        if el is None:
            return set()
        panels = set()
        for nb in _mep_network_neighbors(el, valid_ids):
            nb_pids = host_assignments.get(nb.Id, set())
            if len(nb_pids) == 1:
                panels.add(list(nb_pids)[0])
        return panels

    for _ in range(max_passes):
        changed = False
        for eid, pids in list(host_assignments.items()):
            if len(pids) == 1:
                continue
            conn_panels = neighbor_panels(eid)
            if len(conn_panels) != 1:
                continue
            pid = list(conn_panels)[0]
            if pids != set([pid]):
                host_assignments[eid] = set([pid])
                propagated += 1
                changed = True
        if not changed:
            break
    return propagated


def _append_bimsf_param_assignments(doc, panel_outlines, host_assignments):
    """Assign any host element with BIMSF_Container inside a panel zone."""
    for pid, outline in panel_outlines.items():
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for item in nearby:
            cat = item.Category
            if cat is not None and cat.BuiltInCategory == (
                DB.BuiltInCategory.OST_StructuralFraming
            ):
                continue
            p = item.LookupParameter(PARAM_NAME)
            if p is None:
                continue
            if item.Id not in host_assignments:
                host_assignments[item.Id] = set()
            host_assignments[item.Id].add(pid)


def set_panel_labels(elem, panel_id, known_pids=None):
    """Write BIMSF_Container and Panel Name on a host element."""
    if known_pids is None:
        known_pids = []
    pid = canonical_panel_id(panel_id, known_pids)
    display = panel_display_name(pid)
    _set_container(elem, pid, known_pids)
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
    mep_assignments, _, _, spatial = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    panel_outlines = _build_panel_outlines(panel_elements, link_zones)
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())
    counts = {pid: 0 for pid in get_all_panel_ids(panel_elements, link_zones)}
    for eid, pids in mep_assignments.items():
        el = doc.GetElement(eid)
        if el is None:
            continue
        if _is_panel_crossing_connection(
            el, mep_assignments, panel_outlines, valid_ids
        ):
            continue
        if len(pids) == 1:
            pid = list(pids)[0]
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def preview_crossing_mep(doc, panel_elements, link_zones):
    """Return count of panel-crossing connections and connecting pipe segments."""
    mep_assignments, _, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    panel_outlines = _build_panel_outlines(panel_elements, link_zones)
    return _count_crossing_connections(doc, mep_assignments, panel_outlines)


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
    all_pids.update(link_framing.keys())

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
    panel_outlines = _build_panel_outlines(panel_elements, link_zones)

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

    _append_bimsf_param_assignments(doc, panel_outlines, host_assignments)

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
                        if pid and any(
                            panel_ids_match(pid, k) for k in all_pids
                        ):
                            matched.add(pid)
                else:
                    for pid, outline in panel_outlines.items():
                        if _link_bbox_in_outline(elem, transform, outline):
                            matched.add(pid)
                if not matched:
                    continue
                link_assignments.append((link_inst, elem, matched))
                stats["link_matched"] += 1

    known_pids = list(all_pids)
    spatial_assignments = {
        eid: set(pids) for eid, pids in host_assignments.items()
    }
    stats["curve_refined"] = _refine_curve_assignments(
        doc, host_assignments, panel_outlines
    )
    _seed_assignments_from_parameters(doc, host_assignments, known_pids)
    stats["propagated"] = propagate_panel_assignments(doc, host_assignments)
    stats["propagated"] += propagate_panel_assignments(doc, host_assignments)

    return host_assignments, link_assignments, stats, spatial_assignments


def fill_mep_bimsf_containers(
    doc,
    panel_elements,
    link_zones=None,
    selected=None,
    clear_crossings=True,
    clear_bends=True,
):
    """Write BIMSF_Container on host MEP inside each panel zone (spatial match).

    Pipes, conduits, fittings, fixtures, and any other host element that has
    a writable BIMSF_Container parameter inside the panel bounding box are
    tagged automatically. Unassigned items inherit the panel from connected
    runs (pipes/conduits/fittings). Crossing elements (two+ panels) are cleared.
    """
    stats = {
        "tagged": 0,
        "updated": 0,
        "cleared_crossing": 0,
        "cleared_bends": 0,
        "skipped_no_param": 0,
        "unassigned": 0,
        "propagated": 0,
        "resolved": 0,
    }
    mep_assignments, _, assign_stats, spatial_assignments = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    stats["propagated"] = assign_stats.get("propagated", 0)
    panel_outlines = _build_panel_outlines(panel_elements, link_zones)
    known_pids = list(get_all_panel_ids(panel_elements, link_zones))
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())

    for eid, pids in mep_assignments.items():
        el = doc.GetElement(eid)
        if el is None:
            continue

        if clear_bends and _is_conduit_bend(el):
            if _clear_container(el):
                stats["cleared_bends"] += 1
            continue

        if _is_panel_crossing_connection(
            el, mep_assignments, panel_outlines, valid_ids
        ):
            if clear_crossings and _clear_container(el):
                stats["cleared_crossing"] += 1
            continue

        pid = None
        if len(pids) == 1:
            pid = list(pids)[0]
        elif len(pids) == 0 or len(pids) > 1:
            pid = _resolve_panel_for_element(
                el, mep_assignments, panel_outlines, pids
            )
            if pid:
                stats["resolved"] += 1

        if not pid:
            stats["unassigned"] += 1
            continue

        if not _panel_in_selection(pid, selected):
            continue

        existing = el.LookupParameter(PARAM_NAME)
        if existing is None or existing.IsReadOnly:
            stats["skipped_no_param"] += 1
            continue

        had_value = existing.HasValue and existing.AsString()
        if had_value and panel_ids_match(had_value, pid):
            continue

        set_panel_labels(el, pid, known_pids)
        if had_value:
            stats["updated"] += 1
        else:
            stats["tagged"] += 1

    return stats


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


def _delete_groups_in_doc(doc, selected):
    """Dissolve existing panel groups without deleting member elements."""
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements():
        for pid in selected:
            if group_matches_panel(g.Name, pid):
                try:
                    g.UngroupMembers()
                except Exception:
                    try:
                        doc.Delete(g.Id)
                    except Exception:
                        pass


class _CopyUseDestinationTypes(DB.IDuplicateTypeNamesHandler):
    """Auto-resolve duplicate type names during copy (no modal dialog)."""

    def OnDuplicateTypeNamesFound(self, args):
        return DB.DuplicateTypeAction.UseDestinationTypes


def get_link_document_path(host_doc, link_inst):
    """Return the on-disk path for a Revit link instance."""
    link_doc = link_inst.GetLinkDocument()
    if link_doc is not None:
        try:
            path = link_doc.PathName
            if path:
                return path
        except Exception:
            pass
    try:
        ref = DB.ExternalFileUtils.GetExternalFileReference(
            host_doc, link_inst.GetTypeId()
        )
        mp = ref.GetPath()
        return DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
    except Exception:
        return None


def _document_path(doc):
    try:
        if doc.IsWorkshared:
            mp = doc.GetWorksharingCentralModelPath()
            return DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
    except Exception:
        pass
    try:
        return doc.PathName
    except Exception:
        return None


def _link_instances_for_path(host_doc, path):
    norm = path.lower()
    result = []
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        lp = get_link_document_path(host_doc, link_inst)
        if lp and lp.lower() == norm:
            result.append(link_inst)
    return result


def _unload_link_instances(host_doc, link_insts):
    """Unload link instances — must run inside a host-document transaction."""
    unloaded = []
    if not link_insts:
        return unloaded
    t = DB.Transaction(host_doc, "UNIQUBE: Unload Link")
    try:
        t.Start()
        for link_inst in link_insts:
            if link_inst.IsLoaded():
                link_inst.Unload()
                unloaded.append(link_inst)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return unloaded


def _load_link_instances(host_doc, link_insts):
    """Reload previously unloaded link instances."""
    count = 0
    if not link_insts:
        return count
    t = DB.Transaction(host_doc, "UNIQUBE: Reload Link")
    try:
        t.Start()
        for link_inst in link_insts:
            if not link_inst.IsLoaded():
                link_inst.Load()
                count += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return count


def _is_primary_document(doc):
    try:
        return doc is not None and not doc.IsLinked
    except Exception:
        return False


def _open_document_for_edit(app, path):
    """Open a link .rvt as a primary document for editing."""
    if not path:
        return None, False, False
    norm = path.lower()
    for d in app.Documents:
        try:
            if not _is_primary_document(d):
                continue
            dp = _document_path(d)
            if dp and dp.lower() == norm:
                return d, False, False
        except Exception:
            pass
    try:
        mp = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
        opts = DB.OpenOptions()
        opts.Audit = False
        opened = app.OpenDocumentFile(mp, opts)
        if not _is_primary_document(opened):
            return None, False, False
        return opened, True, True
    except Exception:
        return None, False, False


def group_framing_in_active_doc(doc, selected):
    """Group panel framing in the active (primary) document."""
    framing = map_framing(doc)
    stats = {"link_groups": 0, "errors": []}
    t = DB.Transaction(doc, "UNIQUBE: Group Panel Framing")
    try:
        t.Start()
        _delete_groups_in_doc(doc, selected)
        for pid in selected:
            group_ids = List[DB.ElementId]()
            for el in merge_framing_for_panel(framing, pid):
                group_ids.Add(el.Id)
            if group_ids.Count > 1:
                grp = doc.Create.NewGroup(group_ids)
                grp.GroupType.Name = panel_group_name(pid)
                stats["link_groups"] += 1
        t.Commit()
    except Exception as ex:
        stats["errors"].append(str(ex))
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return stats


def group_link_panel_framing(app, host_doc, selected, link_framing):
    """Create Revit groups for panel framing inside each link .rvt file.

    Revit cannot put linked elements in a host group, so each panel's
    studs/tracks are grouped in the link model with the same name as the
    host MEP group (e.g. ELB-1001).
    """
    stats = {
        "link_groups": 0,
        "link_files": 0,
        "reloaded": 0,
        "errors": [],
    }
    if not link_framing or not selected:
        return stats

    by_path = {}
    link_inst_map = {}
    for pid in selected:
        for link_inst, elem in merge_link_framing_for_panel(link_framing, pid):
            path = get_link_document_path(host_doc, link_inst)
            if not path:
                msg = "No file path for link — save the link model locally."
                if msg not in stats["errors"]:
                    stats["errors"].append(msg)
                continue
            if path not in by_path:
                by_path[path] = {}
                link_inst_map[path] = _link_instances_for_path(host_doc, path)
            by_path[path].setdefault(pid, []).append(elem.UniqueId)

    for path, panels in by_path.items():
        link_insts = link_inst_map.get(path, [])
        unloaded = _unload_link_instances(host_doc, link_insts)

        edit_doc, opened_here, close_after = _open_document_for_edit(app, path)
        if edit_doc is None or not _is_primary_document(edit_doc):
            stats["errors"].append(
                "Could not open link file for editing: {}".format(path)
            )
            _load_link_instances(host_doc, unloaded)
            continue

        stats["link_files"] += 1
        t = DB.Transaction(edit_doc, "UNIQUBE: Group Panel Framing")
        try:
            t.Start()
            _delete_groups_in_doc(edit_doc, selected)
            for pid, unique_ids in panels.items():
                group_ids = List[DB.ElementId]()
                seen_uids = set()
                for uid in unique_ids:
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    el = edit_doc.GetElement(uid)
                    if el is not None:
                        group_ids.Add(el.Id)
                if group_ids.Count > 1:
                    grp = edit_doc.Create.NewGroup(group_ids)
                    grp.GroupType.Name = panel_group_name(pid)
                    stats["link_groups"] += 1
            t.Commit()
            if opened_here:
                edit_doc.Save()
        except Exception as ex:
            stats["errors"].append("{}: {}".format(path, ex))
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        finally:
            if close_after and edit_doc is not None:
                try:
                    edit_doc.Close(False)
                except Exception:
                    pass
            stats["reloaded"] += _load_link_instances(host_doc, unloaded)

    return stats


def reload_links_for_paths(host_doc, paths):
    """Reload link instances whose source files were edited."""
    if not paths:
        return 0
    norm_paths = set(p.lower() for p in paths)
    count = 0
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        path = get_link_document_path(host_doc, link_inst)
        if path and path.lower() in norm_paths:
            try:
                link_inst.Reload()
                count += 1
            except Exception:
                pass
    return count


def select_panel_pair(uidoc, host_doc, pid, link_framing):
    """Select ONE panel's host group; include link only if framing is still linked."""
    refs = List[DB.Reference]()
    host_framing = map_framing(host_doc)
    host_only = bool(merge_framing_for_panel(host_framing, pid))
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )

    for g in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.Group)
        .ToElements()
    ):
        if not group_matches_panel(g.Name, pid):
            continue
        try:
            refs.Add(DB.Reference(g))
        except Exception:
            pass
        break

    if host_only:
        if refs.Count > 0:
            uidoc.Selection.SetReferences(refs)
            return refs.Count
        return 0

    link_group_found = False
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        for g in (
            DB.FilteredElementCollector(link_doc)
            .OfClass(DB.Group)
            .ToElements()
        ):
            if not group_matches_panel(g.Name, pid):
                continue
            try:
                refs.Add(DB.Reference(g).CreateLinkReference(link_inst))
                link_group_found = True
            except Exception:
                pass
            break

    if not link_group_found:
        # Do not select hundreds of individual linked studs — causes UI flicker.
        # Use panel groups in the link file, or run Prepare MEP Panels to copy
        # framing to host and create a host group.
        pass

    if refs.Count > 0:
        uidoc.Selection.SetReferences(refs)
        return refs.Count
    return 0


def select_panels_in_view(uidoc, host_doc, selected, link_framing):
    """Select each panel pair one at a time — use select_panel_pair instead."""
    if not selected:
        return 0
    return select_panel_pair(uidoc, host_doc, selected[0], link_framing)


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
    colored in the active view and should be grouped in the link file via
    group_link_panel_framing(). Panel-crossing fittings and connecting pipes red.
    """
    if link_framing is None:
        link_framing = map_link_framing_by_container(doc)

    mep_assignments, link_assignments, link_stats, spatial_assignments = (
        assign_mep_to_panels(doc, panel_elements, link_zones)
    )
    panel_outlines = _build_panel_outlines(panel_elements, link_zones)
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())
    red_settings, panel_settings = _view_color_kit(doc)

    stats = {
        "groups": 0,
        "mep_tagged": 0,
        "host_framing": 0,
        "link_framing_colored": 0,
        "crossing_count": 0,
        "skipped_empty": 0,
        "group_errors": [],
        "link_matched": link_stats.get("link_matched", 0),
    }

    processed_crossings = set()
    added_to_group = set()

    for pid in selected:
        settings = panel_settings()
        group_ids = List[DB.ElementId]()

        for el in merge_framing_for_panel(panel_elements, pid):
            view.SetElementOverrides(el.Id, settings)
            eid = el.Id.IntegerValue
            if eid not in added_to_group:
                added_to_group.add(eid)
                group_ids.Add(el.Id)
                stats["host_framing"] += 1

        for link_inst, elem in merge_link_framing_for_panel(link_framing, pid):
            if set_link_element_override(view, link_inst, elem, settings):
                stats["link_framing_colored"] += 1

        for eid, pids in mep_assignments.items():
            el = doc.GetElement(eid)
            if el is None:
                continue
            eid_int = eid.IntegerValue
            if _is_panel_crossing_connection(
                el, mep_assignments, panel_outlines, valid_ids
            ):
                if eid not in processed_crossings:
                    view.SetElementOverrides(eid, red_settings)
                    processed_crossings.add(eid)
                    stats["crossing_count"] += 1
                if tag_mep:
                    _clear_container(el)
                continue
            if _assignment_matches_panel(pids, pid):
                if eid_int not in added_to_group:
                    added_to_group.add(eid_int)
                    group_ids.Add(eid)
                view.SetElementOverrides(eid, settings)
                if tag_mep:
                    set_panel_labels(el, pid)
                    stats["mep_tagged"] += 1

        for link_inst, elem, pids in link_assignments:
            is_framing = (
                elem.Category is not None
                and elem.Category.BuiltInCategory
                == DB.BuiltInCategory.OST_StructuralFraming
            )
            if is_framing:
                continue
            if _assignment_matches_panel(pids, pid):
                set_link_element_override(view, link_inst, elem, settings)
            elif (
                _is_mep_connection(elem)
                and len(pids) > 1
                and _assignment_crosses_panel(pids, pid)
            ):
                set_link_element_override(view, link_inst, elem, red_settings)
                stats["crossing_count"] += 1

        has_link = bool(merge_link_framing_for_panel(link_framing, pid))
        if group_ids.Count > 1:
            try:
                new_grp = doc.Create.NewGroup(group_ids)
                new_grp.GroupType.Name = panel_group_name(pid)
                stats["groups"] += 1
            except Exception as ex:
                stats["group_errors"].append(
                    "{}: {}".format(panel_group_name(pid), ex)
                )
        elif group_ids.Count == 1 and not has_link:
            stats["skipped_empty"] += 1
        elif group_ids.Count == 0 and not has_link:
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


def build_panel_rows(doc):
    """Build panel list rows for MEP grouping / copy workflows."""
    panel_elements = map_framing(doc)
    link_zones = map_framing_from_links(doc)
    link_framing = map_link_framing_by_container(doc)
    link_sources = map_framing_link_sources(doc)
    mep_counts = preview_mep_counts(doc, panel_elements, link_zones)
    link_counts = count_link_framing(link_framing)

    all_pids = get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())
    all_pids.update(panel_elements.keys())

    rows = []
    for pid in sorted(all_pids, key=lambda x: panel_display_name(x).lower()):
        host_count = len(merge_framing_for_panel(panel_elements, pid))
        link_count = sum(
            link_counts.get(k, 0)
            for k in link_counts
            if panel_ids_match(k, pid)
        )
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
            "mep_count": sum(
                mep_counts.get(k, 0)
                for k in mep_counts
                if panel_ids_match(k, pid)
            ),
            "link_name": link_sources.get(pid, ""),
            "host_framing": host_count,
            "link_framing": link_count,
        })
    return rows, panel_elements, link_zones, link_framing


def _flatten_copied_elements(host_doc, element_ids):
    """Explode copied groups and return all leaf elements."""
    result = []
    pending = list(element_ids)
    exploded = 0
    while pending:
        eid = pending.pop()
        el = host_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.Group):
            try:
                for mid in el.UngroupMembers():
                    pending.append(mid)
                exploded += 1
            except Exception:
                pass
        else:
            result.append(el)
    return result, exploded


def _remove_host_framing_for_panel(host_doc, pid):
    """Delete copied host framing for one panel so link copy can run again."""
    removed = 0
    for el in merge_framing_for_panel(map_framing(host_doc), pid):
        try:
            gid = el.GroupId
            if gid is not None and gid != DB.ElementId.InvalidElementId:
                grp = host_doc.GetElement(gid)
                if grp is not None and isinstance(grp, DB.Group):
                    grp.UngroupMembers()
        except Exception:
            pass
    for el in merge_framing_for_panel(map_framing(host_doc), pid):
        try:
            host_doc.Delete(el.Id)
            removed += 1
        except Exception:
            pass
    return removed


def _ensure_framing_links_loaded(host_doc):
    """Reload link instances whose link document is not currently open."""
    loaded = 0
    for link_inst in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    ):
        if link_inst.GetLinkDocument() is not None:
            continue
        try:
            link_inst.Load()
            loaded += 1
        except Exception:
            pass
    return loaded


def copy_panel_framing_to_host(host_doc, view, selected, link_framing=None, regroup=True):
    """Copy linked panel framing into the host, explode groups, regroup with MEP.

    After this, panel studs/tracks live in the host model and can be grouped
    with MEP in a single Revit group so the structural link can be removed.
    """
    stats = {
        "panels": 0,
        "members_copied": 0,
        "groups_exploded": 0,
        "host_groups": 0,
        "skipped": [],
        "errors": [],
        "verify": [],
        "copied_pids": [],
    }
    if not selected:
        return stats

    if link_framing is None:
        link_framing = map_link_framing_by_container(host_doc)
    if not link_framing:
        return stats

    copy_opts = DB.CopyPasteOptions()
    try:
        copy_opts.SetDuplicateTypeNamesHandler(_CopyUseDestinationTypes())
    except Exception:
        pass
    host_framing = map_framing(host_doc)
    copied_pids = []

    for pid in selected:
        pairs = merge_link_framing_for_panel(link_framing, pid)
        label = panel_display_name(pid)
        if not pairs:
            stats["skipped"].append("{} (no link framing)".format(label))
            continue

        host_count = len(merge_framing_for_panel(host_framing, pid))
        if host_count > 0:
            _remove_host_framing_for_panel(host_doc, pid)
            host_framing = map_framing(host_doc)

        by_link = {}
        for link_inst, elem in pairs:
            key = link_inst.Id.IntegerValue
            if key not in by_link:
                by_link[key] = (link_inst, [])
            by_link[key][1].append(elem.Id)

        panel_copied = False
        for link_inst, elem_ids in by_link.values():
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                continue
            transform = link_inst.GetTotalTransform()
            src_ids = List[DB.ElementId]()
            for eid in elem_ids:
                src_ids.Add(eid)
            try:
                new_ids = DB.ElementTransformUtils.CopyElements(
                    link_doc,
                    src_ids,
                    host_doc,
                    transform,
                    copy_opts,
                )
                flat, exploded = _flatten_copied_elements(
                    host_doc, list(new_ids)
                )
                stats["groups_exploded"] += exploded
                for el in flat:
                    set_panel_labels(el, pid)
                    stats["members_copied"] += 1
                    panel_copied = True
            except Exception as ex:
                stats["errors"].append("{}: {}".format(label, ex))

        if panel_copied:
            stats["panels"] += 1
            copied_pids.append(pid)
            stats["copied_pids"] = copied_pids
            host_framing = map_framing(host_doc)

    if regroup and view is not None and copied_pids:
        try:
            regroup_stats = regroup_panels_in_host(
                host_doc, view, copied_pids, tag_mep=True
            )
            stats["host_groups"] = regroup_stats.get("groups", 0)
            stats["errors"].extend(regroup_stats.get("group_errors", []))
        except Exception as ex:
            stats["errors"].append("Regroup: {}".format(ex))

    stats["verify"] = verify_panel_copy(host_doc, copied_pids or selected)
    return stats


def regroup_panels_in_host(host_doc, view, selected, tag_mep=True):
    """Rebuild host panel groups (framing + MEP) after copy or regroup."""
    panel_elements = map_framing(host_doc)
    link_zones = map_framing_from_links(host_doc)
    remaining_link = map_link_framing_by_container(host_doc)
    _delete_groups_in_doc(host_doc, selected)
    return combine_panels_group_color(
        host_doc,
        view,
        selected,
        panel_elements,
        link_zones,
        link_framing=remaining_link,
        tag_mep=tag_mep,
    )


def verify_panel_copy(host_doc, panel_ids):
    """Return per-panel copy status for UI / debugging."""
    host_framing = map_framing(host_doc)
    link_framing = map_link_framing_by_container(host_doc)
    rows = []
    for pid in panel_ids:
        label = panel_display_name(pid)
        host_count = len(merge_framing_for_panel(host_framing, pid))
        link_count = len(merge_link_framing_for_panel(link_framing, pid))
        has_group = False
        for g in (
            DB.FilteredElementCollector(host_doc)
            .OfClass(DB.Group)
            .ToElements()
        ):
            if group_matches_panel(g.Name, pid):
                has_group = True
                break
        if host_count and has_group:
            status = "OK — in host, grouped"
        elif host_count:
            status = "Partial — host framing, no group"
        elif link_count:
            status = "Not copied — still in link only"
        else:
            status = "Missing — no framing found"
        rows.append({
            "panel": label,
            "host_framing": host_count,
            "link_framing": link_count,
            "host_group": has_group,
            "status": status,
        })
    return rows


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
