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


def assembly_matches_panel(assembly_name, panel_id):
    """True if an assembly type name belongs to the given panel id."""
    return group_matches_panel(assembly_name, panel_id)


def host_assembly_for_panel(doc, pid):
    """Return the host AssemblyInstance for one panel, if any."""
    for asm in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    ):
        try:
            if assembly_matches_panel(asm.AssemblyTypeName, pid):
                return asm
        except Exception:
            pass
        try:
            if assembly_matches_panel(asm.Name, pid):
                return asm
        except Exception:
            pass
        container = _read_container_value(asm)
        if container and panel_ids_match(container, pid):
            return asm
    return None


def group_label(group):
    """Return the display/type name for a Revit group."""
    try:
        group_type = group.GroupType
        if group_type is not None and group_type.Name:
            return group_type.Name
    except Exception:
        pass
    try:
        return group.Name or ""
    except Exception:
        return ""


def _short_revit_error(ex):
    """Return a single-line Revit/IronPython error for UI dialogs."""
    msg = str(ex).strip()
    if not msg:
        return "unknown error"
    for sep in ("\r\n", "\n"):
        if sep in msg:
            msg = msg.split(sep)[0].strip()
            break
    if "Parameter name:" in msg:
        msg = msg.split("Parameter name:")[0].strip().rstrip(";")
    if len(msg) > 180:
        msg = msg[:177] + "..."
    return msg


def _is_structural_framing(elem):
    cat = elem.Category if elem is not None else None
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
    except Exception:
        try:
            return cat.Id.IntegerValue == int(
                DB.BuiltInCategory.OST_StructuralFraming
            )
        except Exception:
            return False


def _element_in_assembly(elem):
    invalid = DB.ElementId.InvalidElementId
    try:
        aid = elem.AssemblyInstanceId
        return aid is not None and aid != invalid
    except Exception:
        return False


def _element_in_group(elem):
    invalid = DB.ElementId.InvalidElementId
    try:
        gid = elem.GroupId
        return gid is not None and gid != invalid
    except Exception:
        return False


def _ungroup_all_containing_members(doc, target_ints):
    """Dissolve every model group that includes any target element."""
    for _pass in range(30):
        changed = False
        for group in (
            DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
        ):
            try:
                member_ids = list(group.GetMemberIds())
            except Exception:
                continue
            if not any(mid.IntegerValue in target_ints for mid in member_ids):
                continue
            changed = True
            try:
                group.UngroupMembers()
            except Exception:
                try:
                    doc.Delete(group.Id)
                except Exception:
                    pass
        if not changed:
            break
        try:
            doc.Regenerate()
        except Exception:
            pass


def _assembly_container_name(doc, pid, id_list):
    """Use the link/MWF container string verbatim (asterisk kept)."""
    for eid in id_list:
        el = doc.GetElement(eid)
        val = _read_container_value(el)
        if val:
            return val
    return panel_group_name(pid)


def _filter_assembly_ready_members(doc, id_list):
    """Keep only ungrouped structural framing not already in an assembly."""
    ready = List[DB.ElementId]()
    for eid in id_list:
        el = doc.GetElement(eid)
        if el is None:
            continue
        if not _is_structural_framing(el):
            continue
        if _element_in_assembly(el) or _element_in_group(el):
            continue
        ready.Add(eid)
    return ready


def doc_has_mep_content(doc):
    """True when the document contains any host MEP elements."""
    for cat in MEP_CATS:
        try:
            count = (
                DB.FilteredElementCollector(doc)
                .OfCategory(cat)
                .WhereElementIsNotElementType()
                .GetElementCount()
            )
            if count > 0:
                return True
        except Exception:
            pass
    return False


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

LINEAR_MEP_CATS = frozenset([
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_CableTray,
])


def _is_linear_mep_curve(el):
    """True for pipe/conduit/cable-tray runs (linear MEP curves)."""
    if el is None:
        return False
    try:
        if isinstance(el, DB.MEPCurve):
            return True
    except Exception:
        pass
    cat = el.Category
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory in LINEAR_MEP_CATS
    except Exception:
        return False

# Host + link spatial assignment for panel grouping workflows.
LINK_ASSIGN_CATS = MEP_CATS + [DB.BuiltInCategory.OST_StructuralFraming]

ZONE_PAD_FT = 0.25
INTERIOR_TOL_FT = 0.05


def canonical_panel_id(pid, known_pids):
    """Return the framing model's exact panel id string (e.g. *ELB-1001)."""
    if not pid:
        return pid
    for kp in known_pids:
        if panel_ids_match(kp, pid):
            return kp
    return pid


def _build_panel_outlines(panel_elements, link_zones):
    """Return (query_outlines, interior_outlines).

    query_outlines — padded bbox for spatial collectors.
    interior_outlines — tight framing bounds for inside/crossing tests.
    """
    query_outlines = {}
    interior_outlines = {}
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
        query_outlines[pid] = _panel_outline(min_pt, max_pt)
        interior_outlines[pid] = _interior_outline(min_pt, max_pt)
    return query_outlines, interior_outlines


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


def _location_panels(elem, panel_outlines):
    """Panels whose zone contains the element insert / connector origin."""
    panels = set()
    try:
        loc = elem.Location
        if isinstance(loc, DB.LocationPoint):
            pt = loc.Point
            for pid, outline in panel_outlines.items():
                if _point_in_outline(pt, outline):
                    panels.add(pid)
            return panels
    except Exception:
        pass
    cm = _get_mep_connector_manager(elem)
    if cm is not None:
        try:
            for conn in cm.Connectors:
                try:
                    pt = conn.Origin
                except Exception:
                    continue
                for pid, outline in panel_outlines.items():
                    if _point_in_outline(pt, outline):
                        panels.add(pid)
        except Exception:
            pass
    if not panels:
        try:
            bbox = elem.get_BoundingBox(None)
        except Exception:
            bbox = None
        if bbox is not None:
            center = DB.XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0,
            )
            for pid, outline in panel_outlines.items():
                if _point_in_outline(center, outline):
                    panels.add(pid)
    return panels


def _curve_touches_panel_zone(el, panel_outlines):
    """True when at least one curve endpoint lies inside a panel zone."""
    if not _is_linear_mep_curve(el):
        return bool(_location_panels(el, panel_outlines))
    start_p, end_p = _endpoint_panels(el, panel_outlines)
    return bool(start_p or end_p)


def _panels_matching(pid, panel_set):
    return any(panel_ids_match(pid, p) for p in panel_set)


def _curve_fully_inside_panel(el, panel_outlines, pid):
    """True when both curve endpoints lie inside the given panel volume."""
    if not _is_linear_mep_curve(el):
        return _panels_matching(pid, _location_panels(el, panel_outlines))
    start_p, end_p = _endpoint_panels(el, panel_outlines)
    return _panels_matching(pid, start_p) and _panels_matching(pid, end_p)


def _mep_belongs_in_panel(
    el, pid, panel_outlines, host_assignments=None, valid_ids=None
):
    """Host MEP fully inside panel framing (not outside connecting runs)."""
    existing = _read_container_value(el)
    if existing and panel_ids_match(existing, pid):
        return True

    if _is_linear_mep_curve(el):
        return _curve_fully_inside_panel(el, panel_outlines, pid)

    if _is_mep_connection(el):
        if _panels_matching(pid, _location_panels(el, panel_outlines)):
            return True
        if host_assignments is not None and valid_ids is not None:
            return _fitting_serves_panel_run(
                el, pid, host_assignments, panel_outlines, valid_ids
            )
        return False

    return _panels_matching(pid, _location_panels(el, panel_outlines))


def _fitting_serves_panel_run(el, pid, host_assignments, panel_outlines, valid_ids):
    """True when a fitting joins non-crossing runs assigned to this panel."""
    for nb in _mep_network_neighbors(el, valid_ids):
        if not _is_linear_mep_curve(nb):
            continue
        if _curve_crosses_panel_boundary(nb, panel_outlines):
            continue
        if _curve_fully_inside_panel(nb, panel_outlines, pid):
            return True
        nb_pids = host_assignments.get(nb.Id, set())
        if len(nb_pids) == 1 and panel_ids_match(list(nb_pids)[0], pid):
            return True
    return False


def _fitting_part_type_name(el):
    for pname in ("Part Type", BEND_OR_FITTING_PARAM):
        p = el.LookupParameter(pname)
        if p and p.HasValue:
            val = p.AsString()
            if val and val.strip():
                return val.strip().lower()
    return ""


def _fitting_connectors_collinear(el):
    """True when a two-port fitting continues a run (coupling-like)."""
    cm = _get_mep_connector_manager(el)
    if cm is None:
        return False
    try:
        connectors = list(cm.Connectors)
    except Exception:
        return False
    if len(connectors) != 2:
        return False
    try:
        d0 = connectors[0].CoordinateSystem.BasisZ
        d1 = connectors[1].CoordinateSystem.BasisZ
        return abs(d0.DotProduct(d1)) > 0.95
    except Exception:
        return False


def _is_inline_fitting(el):
    """True for couplings/unions — fittings that continue a run without bending."""
    if not _is_mep_connection(el):
        return False
    if _is_conduit_bend(el):
        return False

    part = _fitting_part_type_name(el)
    if part:
        if any(k in part for k in ("coupling", "union", "cap", "plug", "flange")):
            return True
        if any(
            k in part
            for k in ("elbow", "tee", "wye", "cross", "lateral", "transition", "offset")
        ):
            return False

    try:
        if isinstance(el, DB.FamilyInstance):
            fname = (el.Symbol.FamilyName or "").lower()
            if "coupling" in fname or " union" in fname or fname.startswith("union"):
                return True
            if any(k in fname for k in ("elbow", " tee", "tee ", "wye", "bend")):
                return False
    except Exception:
        pass

    return _fitting_connectors_collinear(el)


def _fitting_on_crossing_run(el, panel_outlines, valid_ids):
    """True for exit elbows/tees on runs that leave the panel — not inline couplings."""
    if not _is_mep_connection(el):
        return False
    if _is_inline_fitting(el):
        return False
    for nb in _mep_network_neighbors(el, valid_ids):
        if _is_linear_mep_curve(nb) and _curve_crosses_panel_boundary(
            nb, panel_outlines
        ):
            return True
    return False


def _mep_belongs_in_assembly(
    el, pid, panel_outlines, host_assignments, valid_ids
):
    """MEP members for Revit assembly — fully inside panel, no exit fittings."""
    if _is_linear_mep_curve(el):
        return _curve_fully_inside_panel(el, panel_outlines, pid)
    if _is_mep_connection(el):
        if _fitting_on_crossing_run(el, panel_outlines, valid_ids):
            return False
        return _fitting_serves_panel_run(
            el, pid, host_assignments, panel_outlines, valid_ids
        )
    if _is_panel_crossing_connection(
        el, host_assignments, panel_outlines, valid_ids
    ):
        return False
    return _mep_belongs_in_panel(
        el, pid, panel_outlines, host_assignments, valid_ids
    )


def _mep_touches_panel_zone(
    el, panel_outlines, host_assignments=None, valid_ids=None
):
    """Element is part of panel MEP (inside zone or on an inside panel run)."""
    if _curve_touches_panel_zone(el, panel_outlines):
        return True
    if _is_mep_connection(el) and host_assignments is not None and valid_ids:
        for nb in _mep_network_neighbors(el, valid_ids):
            if _is_linear_mep_curve(nb) and _curve_touches_panel_zone(
                nb, panel_outlines
            ):
                return True
    return False


def _connectors_cross_panel_boundary(el, panel_outlines):
    """True when MEP connectors span inside and outside the panel volume."""
    cm = _get_mep_connector_manager(el)
    if cm is None:
        return False
    try:
        connectors = list(cm.Connectors)
    except Exception:
        return False
    if not connectors:
        return False
    inside_flags = []
    for conn in connectors:
        try:
            pt = conn.Origin
        except Exception:
            continue
        inside_flags.append(
            any(_point_in_outline(pt, outline) for outline in panel_outlines.values())
        )
    if not inside_flags:
        return False
    return any(inside_flags) and not all(inside_flags)


def _curve_samples_outside_panel(el, panel_outlines, pid):
    """True when any sampled point on the curve lies outside the panel box."""
    outline = panel_outlines.get(pid)
    if outline is None:
        return False
    loc = el.Location
    if not isinstance(loc, DB.LocationCurve):
        return False
    curve = loc.Curve
    if curve is None:
        return False
    for i in range(1, 10):
        try:
            pt = curve.Evaluate(i / 10.0, True)
        except Exception:
            continue
        if not _point_in_outline(pt, outline):
            return True
    return False


def _curve_crosses_panel_boundary(el, panel_outlines):
    """True when a pipe/conduit enters or exits a panel (inside ↔ outside)."""
    if not _is_linear_mep_curve(el):
        return False

    if _connectors_cross_panel_boundary(el, panel_outlines):
        return True

    start_p, end_p = _endpoint_panels(el, panel_outlines)
    start_in = bool(start_p)
    end_in = bool(end_p)

    # One end inside a panel zone, the other outside all zones.
    if start_in != end_in:
        return True

    # Endpoints in two different panels (no shared panel at endpoints).
    if start_p and end_p and not (start_p & end_p):
        start_names = {panel_display_name(p).lower() for p in start_p}
        end_names = {panel_display_name(p).lower() for p in end_p}
        if start_names != end_names:
            return True

    # Both endpoints inside — check mid-span still inside (top/side exit).
    if start_in and end_in:
        common = start_p & end_p
        panels = common if common else (start_p if start_p == end_p else set())
        for pid in panels:
            if _curve_samples_outside_panel(el, panel_outlines, pid):
                return True
    return False


def _refine_curve_assignments(doc, host_assignments, panel_outlines):
    """Assign conduits/pipes from endpoints; drop bbox-only hits outside panels."""
    refined = 0
    for eid in list(host_assignments.keys()):
        el = doc.GetElement(eid)
        if el is None or not _is_linear_mep_curve(el):
            continue
        start_p, end_p = _endpoint_panels(el, panel_outlines)

        if not start_p and not end_p:
            if host_assignments[eid]:
                host_assignments[eid] = set()
                refined += 1
            continue

        if _curve_crosses_panel_boundary(el, panel_outlines):
            combined = start_p | end_p
            if host_assignments[eid] != combined:
                host_assignments[eid] = combined
                refined += 1
            continue

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
        elif _is_linear_mep_curve(nb):
            start_p, end_p = _endpoint_panels(nb, panel_outlines)
            panels.update(start_p)
            panels.update(end_p)
    if _is_linear_mep_curve(el):
        start_p, end_p = _endpoint_panels(el, panel_outlines)
        panels.update(start_p)
        panels.update(end_p)
    return panels


def _is_panel_crossing_connection(el, host_assignments, panel_outlines, valid_ids):
    """True for pipes/conduits exiting a panel; fittings only when on exit-only runs."""
    if _is_linear_mep_curve(el):
        return _curve_crosses_panel_boundary(el, panel_outlines)

    if not _is_mep_connection(el):
        return False

    has_crossing = False
    has_inside = False
    panel_names = set()

    for nb in _mep_network_neighbors(el, valid_ids):
        if not _is_linear_mep_curve(nb):
            continue
        if _curve_crosses_panel_boundary(nb, panel_outlines):
            has_crossing = True
            continue
        sp, ep = _endpoint_panels(nb, panel_outlines)
        if sp or ep:
            has_inside = True
            for p in sp | ep:
                panel_names.add(panel_display_name(p).lower())

    # Couplings between inside-panel runs (pipe or conduit fittings).
    if has_inside and not has_crossing:
        return False

    # Elbow/tee at panel exit: inside run + crossing run → panel fitting, not red.
    if has_inside and has_crossing:
        return False

    if has_crossing:
        return True

    if len(panel_names) > 1:
        return True
    return False


def _assign_fittings_from_runs(doc, host_assignments, panel_outlines):
    """Assign pipe/conduit fittings from connected inside-panel runs."""
    valid_ids = set(eid.IntegerValue for eid in host_assignments.keys())
    assigned = 0
    for eid in list(host_assignments.keys()):
        if host_assignments[eid]:
            continue
        el = doc.GetElement(eid)
        if el is None or not _is_mep_connection(el):
            continue
        for nb in _mep_network_neighbors(el, valid_ids):
            if not _is_linear_mep_curve(nb):
                continue
            if _curve_crosses_panel_boundary(nb, panel_outlines):
                continue
            nb_pids = host_assignments.get(nb.Id, set())
            if len(nb_pids) == 1:
                host_assignments[eid] = set(nb_pids)
                assigned += 1
                break
    return assigned


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

    if _is_linear_mep_curve(el):
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
                link_zones[pid].append(
                    (t_min, t_max, _framing_is_horizontal(beam))
                )
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


def _framing_is_horizontal(elem):
    """True for tracks / sills (used for panel top and bottom Z limits)."""
    try:
        loc = elem.Location
        if isinstance(loc, DB.LocationCurve):
            curve = loc.Curve
            if curve is None:
                return False
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            dx = p1.X - p0.X
            dy = p1.Y - p0.Y
            dz = abs(p1.Z - p0.Z)
            run = (dx * dx + dy * dy) ** 0.5
            return run > 0.01 and dz / run < 0.35
    except Exception:
        pass
    return False


def compute_panel_bbox(elements, link_bboxes=None):
    """Panel bounds: XY from all framing; Z from horizontal tracks only.

    Vertical studs often extend past top/bottom tracks. Using stud tips for Z
    kept pipes above the top track inside the panel zone (missing red crossing).
    """
    xy_min = DB.XYZ(10000, 10000, 0)
    xy_max = DB.XYZ(-10000, -10000, 0)
    track_z_min = []
    track_z_max = []
    fallback_z_min = []
    fallback_z_max = []

    for el in elements:
        bbox = el.get_BoundingBox(None)
        if bbox is None:
            continue
        xy_min = DB.XYZ(
            min(xy_min.X, bbox.Min.X),
            min(xy_min.Y, bbox.Min.Y),
            xy_min.Z,
        )
        xy_max = DB.XYZ(
            max(xy_max.X, bbox.Max.X),
            max(xy_max.Y, bbox.Max.Y),
            xy_max.Z,
        )
        fallback_z_min.append(bbox.Min.Z)
        fallback_z_max.append(bbox.Max.Z)
        if _framing_is_horizontal(el):
            track_z_min.append(bbox.Min.Z)
            track_z_max.append(bbox.Max.Z)

    if link_bboxes:
        for item in link_bboxes:
            if len(item) >= 3:
                bb_min, bb_max, is_horiz = item[0], item[1], item[2]
            else:
                bb_min, bb_max = item[0], item[1]
                is_horiz = False
            xy_min = DB.XYZ(
                min(xy_min.X, bb_min.X),
                min(xy_min.Y, bb_min.Y),
                xy_min.Z,
            )
            xy_max = DB.XYZ(
                max(xy_max.X, bb_max.X),
                max(xy_max.Y, bb_max.Y),
                xy_max.Z,
            )
            fallback_z_min.append(bb_min.Z)
            fallback_z_max.append(bb_max.Z)
            if is_horiz:
                track_z_min.append(bb_min.Z)
                track_z_max.append(bb_max.Z)

    if track_z_min:
        z0 = min(track_z_min)
        z1 = max(track_z_max)
    else:
        z0 = min(fallback_z_min) if fallback_z_min else 0
        z1 = max(fallback_z_max) if fallback_z_max else 0

    min_pt = DB.XYZ(xy_min.X, xy_min.Y, z0)
    max_pt = DB.XYZ(xy_max.X, xy_max.Y, z1)
    return min_pt, max_pt


def _horizontal_run_ft(elem):
    """Plan-view run length of a horizontal framing member, in feet."""
    try:
        loc = elem.Location
        if not isinstance(loc, DB.LocationCurve):
            return 0.0
        curve = loc.Curve
        if curve is None:
            return 0.0
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y
        return (dx * dx + dy * dy) ** 0.5
    except Exception:
        return 0.0


def compute_panel_dimensions(elements):
    """Return (length, height, thickness) in feet for factory panel schedules.

    Uses MWF / shop-drawing convention:
    - Length = longest horizontal track/sill run (not full XY bbox).
      Sloped top tracks often extend past end studs in bbox only (+39 mm).
    - Height = Z span of horizontal tracks (stud tips excluded).
    - Thickness = smallest plan extent from vertical member bboxes.
    """
    if not elements:
        return None

    horiz_runs = []
    track_z_min = []
    track_z_max = []
    fallback_z_min = []
    fallback_z_max = []
    vert_x_min = vert_y_min = None
    vert_x_max = vert_y_max = None
    all_x_min = all_y_min = all_z_min = None
    all_x_max = all_y_max = all_z_max = None

    for el in elements:
        bbox = el.get_BoundingBox(None)
        if bbox is None:
            continue
        if all_x_min is None:
            all_x_min, all_y_min, all_z_min = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
            all_x_max, all_y_max, all_z_max = bbox.Max.X, bbox.Max.Y, bbox.Max.Z
        else:
            all_x_min = min(all_x_min, bbox.Min.X)
            all_y_min = min(all_y_min, bbox.Min.Y)
            all_z_min = min(all_z_min, bbox.Min.Z)
            all_x_max = max(all_x_max, bbox.Max.X)
            all_y_max = max(all_y_max, bbox.Max.Y)
            all_z_max = max(all_z_max, bbox.Max.Z)

        fallback_z_min.append(bbox.Min.Z)
        fallback_z_max.append(bbox.Max.Z)

        if _framing_is_horizontal(el):
            run = _horizontal_run_ft(el)
            if run > 0.01:
                horiz_runs.append(run)
            track_z_min.append(bbox.Min.Z)
            track_z_max.append(bbox.Max.Z)
        else:
            if vert_x_min is None:
                vert_x_min, vert_y_min = bbox.Min.X, bbox.Min.Y
                vert_x_max, vert_y_max = bbox.Max.X, bbox.Max.Y
            else:
                vert_x_min = min(vert_x_min, bbox.Min.X)
                vert_y_min = min(vert_y_min, bbox.Min.Y)
                vert_x_max = max(vert_x_max, bbox.Max.X)
                vert_y_max = max(vert_y_max, bbox.Max.Y)

    if track_z_min:
        height = max(track_z_max) - min(track_z_min)
    elif fallback_z_min:
        height = max(fallback_z_max) - min(fallback_z_min)
    else:
        height = 0.0

    if horiz_runs:
        length = max(horiz_runs)
    elif vert_x_min is not None:
        length = max(vert_x_max - vert_x_min, vert_y_max - vert_y_min)
    elif all_x_min is not None:
        length = max(all_x_max - all_x_min, all_y_max - all_y_min)
    else:
        return None

    if vert_x_min is not None:
        thickness = min(vert_x_max - vert_x_min, vert_y_max - vert_y_min)
    elif all_x_min is not None:
        horiz = sorted([all_x_max - all_x_min, all_y_max - all_y_min])
        thickness = horiz[0]
    else:
        thickness = 0.0

    return (length, height, thickness)


def _panel_outline(min_pt, max_pt):
    pad = DB.XYZ(ZONE_PAD_FT, ZONE_PAD_FT, ZONE_PAD_FT)
    return DB.Outline(min_pt.Subtract(pad), max_pt.Add(pad))


def _interior_outline(min_pt, max_pt):
    """Tight panel volume aligned to framing — no large outside bleed."""
    tol = DB.XYZ(INTERIOR_TOL_FT, INTERIOR_TOL_FT, INTERIOR_TOL_FT)
    return DB.Outline(min_pt.Subtract(tol), max_pt.Add(tol))


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
    if _is_linear_mep_curve(elem):
        try:
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


def propagate_panel_assignments(doc, host_assignments, panel_outlines=None, max_passes=100):
    """Extend panel assignment along connected MEP runs.

    Unassigned elements (0 panels) or ambiguous bbox hits (2+ panels) inherit
    the panel when all resolved connected neighbors share one panel id.
    Only propagates to elements that touch a panel zone (endpoint/location).
    """
    if panel_outlines is None:
        panel_outlines = {}
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
            el = get_elem(eid)
            if el is None:
                continue
            if panel_outlines and not _mep_touches_panel_zone(
                el, panel_outlines, host_assignments, valid_ids
            ):
                continue
            if _is_linear_mep_curve(el) and _curve_crosses_panel_boundary(
                el, panel_outlines
            ):
                continue
            if _is_mep_connection(el) and _is_panel_crossing_connection(
                el, host_assignments, panel_outlines, valid_ids
            ):
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
    """Assign host elements with BIMSF_Container that lie inside panel framing."""
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
            if _is_linear_mep_curve(item):
                if not _curve_touches_panel_zone(item, panel_outlines):
                    continue
            elif not _location_panels(item, panel_outlines):
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
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())
    counts = {pid: 0 for pid in get_all_panel_ids(panel_elements, link_zones)}
    for eid, pids in mep_assignments.items():
        el = doc.GetElement(eid)
        if el is None:
            continue
        if _is_panel_crossing_connection(
            el, mep_assignments, interior_outlines, valid_ids
        ):
            continue
        if len(pids) == 1:
            pid = list(pids)[0]
            if _mep_belongs_in_panel(
                el, pid, interior_outlines, mep_assignments, valid_ids
            ):
                counts[pid] = counts.get(pid, 0) + 1
    return counts


def preview_crossing_mep(doc, panel_elements, link_zones):
    """Return count of panel-crossing connections and connecting pipe segments."""
    mep_assignments, _, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
    return _count_crossing_connections(doc, mep_assignments, interior_outlines)


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
    query_outlines, interior_outlines = _build_panel_outlines(
        panel_elements, link_zones
    )

    for pid, outline in query_outlines.items():
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
            .ToElements()
        )
        for item in nearby:
            if item.Id in host_assignments:
                host_assignments[item.Id].add(pid)

    _append_bimsf_param_assignments(doc, interior_outlines, host_assignments)

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
                    for pid, outline in interior_outlines.items():
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
        doc, host_assignments, interior_outlines
    )
    _seed_assignments_from_parameters(doc, host_assignments, known_pids)
    stats["propagated"] = propagate_panel_assignments(
        doc, host_assignments, interior_outlines
    )
    stats["propagated"] += propagate_panel_assignments(
        doc, host_assignments, interior_outlines
    )
    stats["fitting_assigned"] = _assign_fittings_from_runs(
        doc, host_assignments, interior_outlines
    )
    stats["fitting_assigned"] += _assign_fittings_from_runs(
        doc, host_assignments, interior_outlines
    )

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
        "cleared_outside": 0,
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
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
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
            el, mep_assignments, interior_outlines, valid_ids
        ):
            if clear_crossings and _clear_container(el):
                stats["cleared_crossing"] += 1
            continue

        if not _mep_touches_panel_zone(
            el, interior_outlines, mep_assignments, valid_ids
        ):
            if _read_container_value(el) and _clear_container(el):
                stats["cleared_outside"] += 1
            stats["unassigned"] += 1
            continue

        pid = None
        if len(pids) == 1:
            pid = list(pids)[0]
        elif len(pids) == 0 or len(pids) > 1:
            pid = _resolve_panel_for_element(
                el, mep_assignments, interior_outlines, pids
            )
            if pid:
                stats["resolved"] += 1

        if not pid:
            stats["unassigned"] += 1
            continue

        if not _mep_belongs_in_panel(
            el, pid, interior_outlines, mep_assignments, valid_ids
        ):
            if _read_container_value(el) and _clear_container(el):
                stats["cleared_outside"] += 1
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
    for _pass in range(12):
        found = False
        for group in (
            DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
        ):
            label = group_label(group)
            for pid in selected:
                if not group_matches_panel(label, pid):
                    continue
                found = True
                try:
                    group.UngroupMembers()
                except Exception:
                    try:
                        doc.Delete(group.Id)
                    except Exception:
                        pass
                break
        if not found:
            break


def _release_elements_for_assembly(doc, element_ids):
    """Ungroup elements so AssemblyInstance.Create can succeed."""
    target = _member_id_ints(element_ids)
    _ungroup_all_containing_members(doc, target)


def _member_id_ints(member_ids):
    ints = set()
    for eid in member_ids:
        if eid is None:
            continue
        if isinstance(eid, DB.ElementId):
            ints.add(eid.IntegerValue)
        else:
            ints.add(int(eid))
    return ints


def _release_members_from_assemblies(doc, member_ids, keep_asm_id=None):
    """Remove elements from any existing assembly that would block a new one."""
    invalid = DB.ElementId.InvalidElementId
    to_delete = set()
    target = _member_id_ints(member_ids)
    keep_int = None
    if keep_asm_id is not None:
        try:
            keep_int = keep_asm_id.IntegerValue
        except Exception:
            keep_int = int(keep_asm_id)

    for asm in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    ):
        try:
            for mid in asm.GetMemberIds():
                if mid.IntegerValue in target:
                    to_delete.add(asm.Id.IntegerValue)
                    break
        except Exception:
            pass

    for eid in member_ids:
        elem = doc.GetElement(eid)
        if elem is None:
            continue
        aid = elem.AssemblyInstanceId
        if aid is not None and aid != invalid:
            to_delete.add(aid.IntegerValue)

    if keep_int is not None:
        to_delete.discard(keep_int)

    for aid in to_delete:
        try:
            doc.Delete(DB.ElementId(aid))
        except Exception:
            pass
    if to_delete:
        try:
            doc.Regenerate()
        except Exception:
            pass


def _assembly_naming_category(doc, id_list):
    """Fixed structural framing category — matches Create Assemblies tool."""
    return DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)


def _prepare_members_for_assembly(doc, pid, member_ids):
    id_list = List[DB.ElementId]()
    for eid in member_ids:
        if eid is not None:
            id_list.Add(eid)
    if id_list.Count <= 1:
        return id_list, "need at least 2 elements"

    target = _member_id_ints(id_list)
    _release_members_from_assemblies(doc, id_list)
    _delete_groups_in_doc(doc, [pid])
    _ungroup_all_containing_members(doc, target)
    try:
        doc.Regenerate()
    except Exception:
        pass
    return id_list, None


def _add_mep_members_to_assembly(doc, asm, mep_ids):
    """Add panel MEP to an existing assembly one element at a time."""
    added = 0
    skipped = 0
    if asm is None or mep_ids is None or mep_ids.Count == 0:
        return added, skipped

    asm_id = asm.Id
    for eid in mep_ids:
        el = doc.GetElement(eid)
        if el is None:
            skipped += 1
            continue
        single = List[DB.ElementId]()
        single.Add(eid)
        target = {eid.IntegerValue}
        _release_members_from_assemblies(doc, single, keep_asm_id=asm_id)
        _ungroup_all_containing_members(doc, target)
        el = doc.GetElement(eid)
        if el is None:
            skipped += 1
            continue
        if _element_in_group(el):
            skipped += 1
            continue
        asm = doc.GetElement(asm_id)
        if asm is None:
            skipped += 1
            continue
        batch = List[DB.ElementId]()
        batch.Add(eid)
        try:
            asm.AddMemberIds(batch)
            added += 1
        except Exception:
            skipped += 1

    if added:
        try:
            doc.Regenerate()
        except Exception:
            pass
    return added, skipped


def _create_panel_assembly(doc, pid, framing_ids, mep_ids=None):
    """Create a Revit assembly from framing, then add MEP members.

    Returns (ok, message).
    """
    id_list, prep_err = _prepare_members_for_assembly(doc, pid, framing_ids)
    if prep_err:
        return False, prep_err

    target = _member_id_ints(id_list)
    _ungroup_all_containing_members(doc, target)
    try:
        doc.Regenerate()
    except Exception:
        pass

    ready = _filter_assembly_ready_members(doc, id_list)
    if ready.Count <= 1:
        blocked = id_list.Count - ready.Count
        if blocked:
            return False, (
                "only {} framing members ready for assembly "
                "({} still grouped or in another assembly)".format(
                    ready.Count, blocked
                )
            )
        return False, "need at least 2 ungrouped framing members"

    naming_cat = _assembly_naming_category(doc, ready)
    container_name = _assembly_container_name(doc, pid, ready)

    try:
        new_asm = DB.AssemblyInstance.Create(doc, ready, naming_cat)
        doc.Regenerate()
    except Exception as ex:
        return False, _short_revit_error(ex)

    asm_id = new_asm.Id
    _set_container(new_asm, container_name)
    set_panel_labels(new_asm, container_name)
    mk = new_asm.LookupParameter("Mark")
    if mk and not mk.IsReadOnly:
        try:
            mk.Set(panel_display_name(container_name))
        except Exception:
            pass

    mep_added = 0
    mep_skipped = 0
    if mep_ids is not None and mep_ids.Count > 0:
        mep_added, mep_skipped = _add_mep_members_to_assembly(
            doc, new_asm, mep_ids
        )

    new_asm = doc.GetElement(asm_id)
    if new_asm is None:
        new_asm = host_assembly_for_panel(doc, pid)
    if new_asm is None:
        return True, (
            "framing copied; assembly not finalized — "
            "MEP in assembly: {}".format(mep_added)
        )

    try:
        doc.Regenerate()
        new_asm = doc.GetElement(new_asm.Id)
        if new_asm is not None:
            new_asm.AssemblyTypeName = container_name
        doc.Regenerate()
    except Exception as ex:
        msg = "assembly created; rename failed: {}".format(
            _short_revit_error(ex)
        )
        if mep_skipped:
            msg += " (MEP in assembly: {})".format(mep_added)
        return True, msg

    if mep_ids is not None and mep_ids.Count > 0:
        if mep_added == 0:
            return True, (
                "framing assembly only — {} MEP could not join assembly".format(
                    mep_skipped
                )
            )
        if mep_skipped:
            return True, (
                "MEP in assembly: {} | outside assembly: {}".format(
                    mep_added, mep_skipped
                )
            )
    return True, None


def _delete_assemblies_in_doc(doc, selected):
    """Remove existing panel assemblies without deleting member elements."""
    to_delete = set()
    for pid in selected:
        asm = host_assembly_for_panel(doc, pid)
        if asm is not None:
            to_delete.add(asm.Id.IntegerValue)
    for asm in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    ):
        if asm.Id.IntegerValue in to_delete:
            continue
        for pid in selected:
            if assembly_matches_panel(asm.AssemblyTypeName, pid):
                to_delete.add(asm.Id.IntegerValue)
                break
    for aid in to_delete:
        try:
            doc.Delete(DB.ElementId(aid))
        except Exception:
            pass


def _clear_panel_containers_in_doc(doc, selected):
    """Remove existing panel groups and assemblies for a clean re-run."""
    _delete_groups_in_doc(doc, selected)
    _delete_assemblies_in_doc(doc, selected)
    target = set()
    framing = map_framing(doc)
    for pid in selected:
        for el in merge_framing_for_panel(framing, pid):
            target.add(el.Id.IntegerValue)
    if target:
        _ungroup_all_containing_members(doc, target)
        try:
            doc.Regenerate()
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
    """Create named panel assemblies for framing in the active document."""
    framing = map_framing(doc)
    stats = {"link_groups": 0, "errors": []}
    t = DB.Transaction(doc, "UNIQUBE: Assemble Panel Framing")
    try:
        t.Start()
        _clear_panel_containers_in_doc(doc, selected)
        for pid in selected:
            assembly_ids = List[DB.ElementId]()
            for el in merge_framing_for_panel(framing, pid):
                assembly_ids.Add(el.Id)
            ok, msg = _create_panel_assembly(doc, pid, assembly_ids)
            if ok:
                stats["link_groups"] += 1
                if msg:
                    stats["errors"].append(
                        "{}: {}".format(panel_group_name(pid), msg)
                    )
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
        t = DB.Transaction(edit_doc, "UNIQUBE: Assemble Panel Framing")
        try:
            t.Start()
            _clear_panel_containers_in_doc(edit_doc, selected)
            for pid, unique_ids in panels.items():
                assembly_ids = List[DB.ElementId]()
                seen_uids = set()
                for uid in unique_ids:
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    el = edit_doc.GetElement(uid)
                    if el is not None:
                        assembly_ids.Add(el.Id)
                ok, msg = _create_panel_assembly(edit_doc, pid, assembly_ids)
                if ok:
                    stats["link_groups"] += 1
                    if msg:
                        stats["errors"].append(
                            "{}: {}".format(panel_group_name(pid), msg)
                        )
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


def _host_panel_member_ids(host_doc, pid):
    """All host framing + MEP element ids belonging to one panel."""
    ids = set()
    for el in merge_framing_for_panel(map_framing(host_doc), pid):
        ids.add(el.Id.IntegerValue)

    asm = host_assembly_for_panel(host_doc, pid)
    if asm is not None:
        try:
            for mid in asm.GetMemberIds():
                ids.add(mid.IntegerValue)
        except Exception:
            pass
        return ids

    panel_elements = map_framing(host_doc)
    link_zones = map_framing_from_links(host_doc)
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
    mep_assignments, _, _, _ = assign_mep_to_panels(
        host_doc, panel_elements, link_zones
    )
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())

    for cat in MEP_CATS:
        try:
            elements = (
                DB.FilteredElementCollector(host_doc)
                .OfCategory(cat)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            continue
        for el in elements:
            p = el.LookupParameter(PARAM_NAME)
            if not (p and p.HasValue and p.AsString()):
                continue
            if not panel_ids_match(p.AsString(), pid):
                continue
            if not _mep_belongs_in_assembly(
                el, pid, interior_outlines, mep_assignments, valid_ids
            ):
                continue
            ids.add(el.Id.IntegerValue)
    return ids


def _append_host_panel_mep_refs(host_doc, refs, pid, seen):
    """Add host MEP refs for one panel, excluding exit fittings."""
    asm = host_assembly_for_panel(host_doc, pid)
    if asm is not None:
        try:
            for mid in asm.GetMemberIds():
                el = host_doc.GetElement(mid)
                if el is None or el.Category is None:
                    continue
                if el.Category.BuiltInCategory not in MEP_CATS:
                    continue
                i = mid.IntegerValue
                if i in seen:
                    continue
                seen.add(i)
                try:
                    refs.Add(DB.Reference(el))
                except Exception:
                    pass
        except Exception:
            pass
        return

    panel_elements = map_framing(host_doc)
    link_zones = map_framing_from_links(host_doc)
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
    mep_assignments, _, _, _ = assign_mep_to_panels(
        host_doc, panel_elements, link_zones
    )
    valid_ids = set(eid.IntegerValue for eid in mep_assignments.keys())

    for cat in MEP_CATS:
        try:
            elements = (
                DB.FilteredElementCollector(host_doc)
                .OfCategory(cat)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            continue
        for el in elements:
            p = el.LookupParameter(PARAM_NAME)
            if not (p and p.HasValue and p.AsString()):
                continue
            if not panel_ids_match(p.AsString(), pid):
                continue
            if not _mep_belongs_in_assembly(
                el, pid, interior_outlines, mep_assignments, valid_ids
            ):
                continue
            i = el.Id.IntegerValue
            if i in seen:
                continue
            seen.add(i)
            try:
                refs.Add(DB.Reference(el))
            except Exception:
                pass


def _append_host_panel_refs(host_doc, refs, pid):
    """Select every host framing + MEP element for one panel."""
    seen = set()
    for el in merge_framing_for_panel(map_framing(host_doc), pid):
        i = el.Id.IntegerValue
        if i in seen:
            continue
        seen.add(i)
        try:
            refs.Add(DB.Reference(el))
        except Exception:
            pass
    _append_host_panel_mep_refs(host_doc, refs, pid, seen)


def _add_assembly_members_to_refs(host_doc, refs, asm):
    """Add every member of an assembly to a reference list."""
    if asm is None:
        return 0
    count = 0
    try:
        for mid in asm.GetMemberIds():
            refs.Add(DB.Reference(mid))
            count += 1
    except Exception:
        try:
            refs.Add(DB.Reference(asm))
            count = 1
        except Exception:
            pass
    return count


def select_panel_pair(uidoc, host_doc, pid, link_framing):
    """Select the host panel assembly when present; else individual members."""
    asm = host_assembly_for_panel(host_doc, pid)
    if asm is not None:
        ids = List[DB.ElementId]()
        ids.Add(asm.Id)
        uidoc.Selection.SetElementIds(ids)
        try:
            return len(list(asm.GetMemberIds())) + 1
        except Exception:
            return 1

    refs = List[DB.Reference]()
    host_framing = map_framing(host_doc)
    host_only = bool(merge_framing_for_panel(host_framing, pid))
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )

    _append_host_panel_refs(host_doc, refs, pid)

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
        for asm in (
            DB.FilteredElementCollector(link_doc)
            .OfClass(DB.AssemblyInstance)
            .ToElements()
        ):
            if not assembly_matches_panel(asm.AssemblyTypeName, pid):
                continue
            try:
                for mid in asm.GetMemberIds():
                    refs.Add(
                        DB.Reference(mid).CreateLinkReference(link_inst)
                    )
                link_group_found = True
            except Exception:
                pass
            break
        if link_group_found:
            break
        for g in (
            DB.FilteredElementCollector(link_doc)
            .OfClass(DB.Group)
            .ToElements()
        ):
            if not group_matches_panel(group_label(g), pid):
                continue
            try:
                refs.Add(DB.Reference(g).CreateLinkReference(link_inst))
                link_group_found = True
            except Exception:
                pass
            break
        if link_group_found:
            break

    if not link_group_found:
        # Do not select hundreds of individual linked studs — causes UI flicker.
        # Use panel assemblies in the link file, or run Prepare MEP Panels to copy
        # framing to host and create a host assembly.
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
    """Create named host panel assemblies with MEP and color like Panel Combine (Color).

    Host framing and MEP go into Revit assemblies. Linked panel framing is
    colored in the active view and should be assembled in the link file via
    group_link_panel_framing(). Panel-crossing pipes/conduits/fittings (enter/exit panel) red.
    """
    if link_framing is None:
        link_framing = map_link_framing_by_container(doc)

    mep_assignments, link_assignments, link_stats, spatial_assignments = (
        assign_mep_to_panels(doc, panel_elements, link_zones)
    )
    _, interior_outlines = _build_panel_outlines(panel_elements, link_zones)
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
    _clear_panel_containers_in_doc(doc, selected)

    for pid in selected:
        settings = panel_settings()
        framing_ids = List[DB.ElementId]()
        mep_ids = List[DB.ElementId]()
        framing_seen = set()
        mep_seen = set()

        for el in merge_framing_for_panel(panel_elements, pid):
            view.SetElementOverrides(el.Id, settings)
            eid = el.Id.IntegerValue
            if eid not in framing_seen:
                framing_seen.add(eid)
                framing_ids.Add(el.Id)
                stats["host_framing"] += 1

        if framing_ids.Count == 0 and count_host_framing_for_panel(doc, pid) > 0:
            for el in (
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                .WhereElementIsNotElementType()
                .ToElements()
            ):
                val = _read_container_value(el)
                if not (val and panel_ids_match(val, pid)):
                    continue
                eid = el.Id.IntegerValue
                if eid in framing_seen:
                    continue
                framing_seen.add(eid)
                framing_ids.Add(el.Id)
                view.SetElementOverrides(el.Id, settings)
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
                el, mep_assignments, interior_outlines, valid_ids
            ):
                if eid not in processed_crossings:
                    view.SetElementOverrides(eid, red_settings)
                    processed_crossings.add(eid)
                    stats["crossing_count"] += 1
                if tag_mep:
                    _clear_container(el)
                continue
            if _fitting_on_crossing_run(
                el, interior_outlines, valid_ids
            ):
                if eid not in processed_crossings:
                    view.SetElementOverrides(eid, red_settings)
                    processed_crossings.add(eid)
                    stats["crossing_count"] += 1
                if tag_mep:
                    _clear_container(el)
                continue
            if _assignment_matches_panel(pids, pid):
                if not _mep_belongs_in_panel(
                    el, pid, interior_outlines, mep_assignments, valid_ids
                ):
                    continue
                if not _mep_belongs_in_assembly(
                    el, pid, interior_outlines, mep_assignments, valid_ids
                ):
                    if tag_mep:
                        _clear_container(el)
                    continue
                if eid_int not in mep_seen:
                    mep_seen.add(eid_int)
                    mep_ids.Add(eid)
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
            elif _is_mep_connection(elem) and (
                len(pids) > 1 or _assignment_crosses_panel(pids, pid)
            ):
                set_link_element_override(view, link_inst, elem, red_settings)
                stats["crossing_count"] += 1

        has_link = bool(merge_link_framing_for_panel(link_framing, pid))

        if framing_ids.Count >= 2:
            try:
                ok, msg = _create_panel_assembly(
                    doc, pid, framing_ids, mep_ids
                )
                if ok:
                    stats["groups"] += 1
                    if msg:
                        stats["group_errors"].append(
                            "{}: {}".format(panel_group_name(pid), msg)
                        )
                else:
                    stats["group_errors"].append(
                        "{}: {}".format(panel_group_name(pid), msg or "unknown")
                    )
            except Exception as ex:
                stats["group_errors"].append(
                    "{}: {}".format(panel_group_name(pid), _short_revit_error(ex))
                )
        elif framing_ids.Count == 0 and has_link:
            pass
        elif framing_ids.Count == 1 and not has_link:
            stats["skipped_empty"] += 1
        elif framing_ids.Count == 0 and not has_link:
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


def _is_framing_element(elem):
    cat = elem.Category if elem is not None else None
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
    except Exception:
        try:
            return cat.Id.IntegerValue == int(
                DB.BuiltInCategory.OST_StructuralFraming
            )
        except Exception:
            return False


def _collect_link_panel_framing_live(host_doc, pid):
    """Scan loaded structural links for framing matching one panel id."""
    pairs = []
    for link_inst in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    ):
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
            val = _read_container_value(beam)
            if val and panel_ids_match(val, pid):
                pairs.append((link_inst, beam))
    return pairs


def _refresh_link_panel_framing(host_doc, pid):
    """Live link framing for one panel — always query the link document."""
    pairs = _collect_link_panel_framing_live(host_doc, pid)
    link_framing = map_link_framing_by_container(host_doc)
    return pairs, link_framing


def _expand_link_framing_leaves(link_doc, elements, pid):
    """Expand groups/assemblies; return framing leaves for this panel only."""
    invalid = DB.ElementId.InvalidElementId
    queue = list(elements)
    seen = set()
    expanded_groups = set()
    expanded_asms = set()
    leaves = []
    leaf_ids = set()

    while queue:
        el = queue.pop()
        if el is None:
            continue
        eid = el.Id.IntegerValue
        if eid in seen:
            continue
        seen.add(eid)

        aid = el.AssemblyInstanceId
        if aid is not None and aid != invalid:
            ai = aid.IntegerValue
            if ai not in expanded_asms:
                expanded_asms.add(ai)
                asm = link_doc.GetElement(aid)
                if asm is not None:
                    try:
                        for mid in asm.GetMemberIds():
                            queue.append(link_doc.GetElement(mid))
                    except Exception:
                        pass

        gid = el.GroupId
        if gid is not None and gid != invalid:
            gi = gid.IntegerValue
            if gi not in expanded_groups:
                expanded_groups.add(gi)
                group = link_doc.GetElement(gid)
                if group is not None and isinstance(group, DB.Group):
                    try:
                        for mid in group.GetMemberIds():
                            queue.append(link_doc.GetElement(mid))
                    except Exception:
                        pass

        if not _is_framing_element(el):
            continue
        val = _read_container_value(el)
        if val and panel_ids_match(val, pid) and eid not in leaf_ids:
            leaf_ids.add(eid)
            leaves.append(el)

    return leaves


def _copy_ids_for_link_framing(link_doc, elements, pid):
    """Copy ids for one panel — never a multi-panel link group shell."""
    leaves = _expand_link_framing_leaves(link_doc, elements, pid)
    ids = List[DB.ElementId]()
    seen = set()
    for el in leaves:
        i = el.Id.IntegerValue
        if i in seen:
            continue
        seen.add(i)
        ids.Add(el.Id)
    return ids


def _expand_copy_ids_from_groups(link_doc, src_ids):
    """If batch copy fails, replace group ids with their member ids."""
    invalid = DB.ElementId.InvalidElementId
    expanded = List[DB.ElementId]()
    for eid in src_ids:
        el = link_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.Group):
            try:
                for mid in el.GetMemberIds():
                    expanded.Add(mid)
            except Exception:
                expanded.Add(eid)
        else:
            expanded.Add(eid)
    return expanded


def _copy_link_framing_to_host(link_doc, src_ids, host_doc, transform, copy_opts):
    """Copy link framing to host; fall back to members / one-by-one."""
    if src_ids is None or src_ids.Count == 0:
        return []

    attempts = [src_ids, _expand_copy_ids_from_groups(link_doc, src_ids)]
    seen = set()
    ordered = []
    for attempt in attempts:
        for eid in attempt:
            i = eid.IntegerValue
            if i not in seen:
                seen.add(i)
                ordered.append(eid)

    for attempt in attempts:
        batch = List[DB.ElementId]()
        for eid in attempt:
            if link_doc.GetElement(eid) is not None:
                batch.Add(eid)
        if batch.Count == 0:
            continue
        try:
            return list(
                DB.ElementTransformUtils.CopyElements(
                    link_doc,
                    batch,
                    host_doc,
                    transform,
                    copy_opts,
                )
            )
        except Exception:
            pass

    results = []
    for eid in ordered:
        if link_doc.GetElement(eid) is None:
            continue
        one = List[DB.ElementId]()
        one.Add(eid)
        try:
            new_ids = DB.ElementTransformUtils.CopyElements(
                link_doc,
                one,
                host_doc,
                transform,
                copy_opts,
            )
            results.extend(list(new_ids))
        except Exception:
            pass
    return results


def _remove_host_framing_for_panel(host_doc, pid):
    """Delete copied host framing for one panel so link copy can run again."""
    removed = 0
    asm = host_assembly_for_panel(host_doc, pid)
    if asm is not None:
        try:
            host_doc.Delete(asm.Id)
        except Exception:
            pass
    for asm in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    ):
        if assembly_matches_panel(asm.AssemblyTypeName, pid):
            try:
                host_doc.Delete(asm.Id)
            except Exception:
                pass
    host_map = map_framing(host_doc)
    for el in merge_framing_for_panel(host_map, pid):
        try:
            gid = el.GroupId
            if gid is not None and gid != DB.ElementId.InvalidElementId:
                grp = host_doc.GetElement(gid)
                if grp is not None and isinstance(grp, DB.Group):
                    grp.UngroupMembers()
        except Exception:
            pass
    host_map = map_framing(host_doc)
    for el in merge_framing_for_panel(host_map, pid):
        try:
            host_doc.Delete(el.Id)
            removed += 1
        except Exception:
            pass
    try:
        host_doc.Regenerate()
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
        pairs, link_framing = _refresh_link_panel_framing(host_doc, pid)
        known_pids = list(link_framing.keys())
        label = panel_display_name(pid)
        if not pairs:
            stats["skipped"].append("{} (no link framing)".format(label))
            continue

        host_count = len(merge_framing_for_panel(host_framing, pid))
        if host_count > 0:
            _remove_host_framing_for_panel(host_doc, pid)
            host_framing = map_framing(host_doc)

        by_link = {}
        source_container = None
        for link_inst, elem in pairs:
            if source_container is None:
                source_container = _read_container_value(elem) or pid
            key = link_inst.Id.IntegerValue
            if key not in by_link:
                by_link[key] = (link_inst, [])
            by_link[key][1].append(elem)

        panel_copied = False
        copy_failures = []
        for link_inst, elems in by_link.values():
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                stats["errors"].append(
                    "{}: structural link not loaded — reload link and retry".format(
                        label
                    )
                )
                continue
            transform = link_inst.GetTotalTransform()
            src_ids = _copy_ids_for_link_framing(link_doc, elems, pid)
            if src_ids.Count == 0:
                copy_failures.append(
                    "no copyable framing ids (check link groups/assemblies)"
                )
                continue
            new_ids = _copy_link_framing_to_host(
                link_doc, src_ids, host_doc, transform, copy_opts
            )
            if not new_ids:
                copy_failures.append(
                    "{} member(s) could not be copied from link".format(
                        len(elems)
                    )
                )
                continue
            flat, exploded = _flatten_copied_elements(host_doc, new_ids)
            stats["groups_exploded"] += exploded
            label_pid = source_container or pid
            for el in flat:
                if not _is_framing_element(el):
                    continue
                set_panel_labels(el, label_pid, known_pids=known_pids)
                stats["members_copied"] += 1
                panel_copied = True

        if copy_failures and not panel_copied:
            stats["errors"].append(
                "{}: {} — try ungrouping panel framing in the link model "
                "or reload the structural link".format(label, copy_failures[0])
            )
        elif panel_copied:
            post_count = len(
                merge_framing_for_panel(map_framing(host_doc), pid)
            )
            link_count = len(pairs)
            if post_count < link_count:
                stats["errors"].append(
                    "{}: partial copy (host: {}, link: {})".format(
                        label, post_count, link_count
                    )
                )

        if panel_copied:
            post_count = count_host_framing_for_panel(host_doc, pid)
            if post_count == 0:
                stats["errors"].append(
                    "{}: copy finished but host framing not found — "
                    "check BIMSF_Container on link members".format(label)
                )
            else:
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
    """Rebuild host panel assemblies (framing + MEP) after copy or regroup."""
    panel_elements = map_framing(host_doc)
    link_zones = map_framing_from_links(host_doc)
    remaining_link = map_link_framing_by_container(host_doc)
    _clear_panel_containers_in_doc(host_doc, selected)
    return combine_panels_group_color(
        host_doc,
        view,
        selected,
        panel_elements,
        link_zones,
        link_framing=remaining_link,
        tag_mep=tag_mep,
    )


def count_host_framing_for_panel(host_doc, pid):
    """Count host structural framing for one panel (by container or assembly)."""
    count = len(merge_framing_for_panel(map_framing(host_doc), pid))
    if count:
        return count
    asm = host_assembly_for_panel(host_doc, pid)
    if asm is None:
        return 0
    n = 0
    try:
        for mid in asm.GetMemberIds():
            el = host_doc.GetElement(mid)
            if el is not None and _is_framing_element(el):
                n += 1
    except Exception:
        pass
    return n


def verify_panel_copy(host_doc, panel_ids):
    """Return per-panel copy status for UI / debugging."""
    host_framing = map_framing(host_doc)
    link_framing = map_link_framing_by_container(host_doc)
    rows = []
    for pid in panel_ids:
        label = panel_display_name(pid)
        host_count = count_host_framing_for_panel(host_doc, pid)
        link_count = len(merge_link_framing_for_panel(link_framing, pid))
        has_asm = host_assembly_for_panel(host_doc, pid) is not None
        has_model_group = False
        if not has_asm:
            for g in (
                DB.FilteredElementCollector(host_doc)
                .OfClass(DB.Group)
                .ToElements()
            ):
                if group_matches_panel(group_label(g), pid):
                    has_model_group = True
                    break
        if host_count and has_asm:
            status = "OK — in host, assembly"
        elif host_count and has_model_group:
            status = "Partial — model group only (dissolve and retry)"
        elif host_count:
            status = "Partial — host framing, no assembly"
        elif link_count:
            status = "Not copied — still in link only"
        else:
            status = "Missing — no framing found"
        rows.append({
            "panel": label,
            "host_framing": host_count,
            "link_framing": link_count,
            "host_group": has_asm,
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
