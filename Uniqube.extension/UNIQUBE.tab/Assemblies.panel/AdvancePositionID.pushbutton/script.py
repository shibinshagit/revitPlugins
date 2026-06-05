# -*- coding: utf-8 -*-
"""Assign global sequential Advance Position IDs to structural framing.

Members with the same fingerprint (type, length, BIMSF_Data, intersection
positions) receive the same ID. IDs are global across the entire project.
"""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()

POSITION_PARAM = "Advanced Position ID"
TOLERANCE_FT = 0.016  # ~5mm in feet


def _round_to_tol(value):
    """Round a float to the nearest tolerance step."""
    step = TOLERANCE_FT
    return round(value / step) * step


def _get_param_str(elem, param_name):
    """Return string value of a parameter or empty string."""
    p = elem.LookupParameter(param_name)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def get_max_existing_id(doc):
    """Scan all structural framing for the highest existing Position ID."""
    max_id = 0
    all_framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for elem in all_framing:
        val = _get_param_str(elem, POSITION_PARAM)
        if val:
            try:
                num = int(val)
                if num > max_id:
                    max_id = num
            except (ValueError, TypeError):
                pass
    return max_id


def is_panel_done(elements):
    """True if ALL elements in the panel already have a Position ID."""
    if not elements:
        return True
    for elem in elements:
        val = _get_param_str(elem, POSITION_PARAM)
        if not val:
            return False
    return True


def get_element_curve(elem):
    """Get the LocationCurve's curve from a framing element."""
    loc = elem.Location
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve
    return None


def get_element_length(elem):
    """Get curve length rounded to tolerance."""
    curve = get_element_curve(elem)
    if curve:
        return _round_to_tol(curve.Length)
    return 0.0


def get_intersection_positions(elem, panel_elements):
    """Find relative distances along elem's centerline where other members cross.

    Returns a sorted tuple of rounded distances from the curve start.
    """
    curve_a = get_element_curve(elem)
    if curve_a is None:
        return ()

    length_a = curve_a.Length
    if length_a < TOLERANCE_FT:
        return ()

    positions = []
    for other in panel_elements:
        if other.Id == elem.Id:
            continue
        curve_b = get_element_curve(other)
        if curve_b is None:
            continue

        # Use SetComparisonResult to find closest approach
        try:
            result = curve_a.Intersect(curve_b)
            if result == DB.SetComparisonResult.Overlap:
                # Curves intersect — get intersection point via projection
                mid_b = curve_b.Evaluate(0.5, True)
                proj = curve_a.Project(mid_b)
                if proj:
                    dist = _round_to_tol(proj.Parameter)
                    positions.append(dist)
            elif result == DB.SetComparisonResult.Disjoint:
                # Check if close enough (within tolerance)
                mid_b = curve_b.Evaluate(0.5, True)
                proj = curve_a.Project(mid_b)
                if proj and proj.Distance < TOLERANCE_FT * 3:
                    dist = _round_to_tol(proj.Parameter)
                    positions.append(dist)
        except Exception:
            # Fallback: project midpoint of other curve onto this curve
            try:
                mid_b = curve_b.Evaluate(0.5, True)
                proj = curve_a.Project(mid_b)
                if proj and proj.Distance < TOLERANCE_FT * 10:
                    dist = _round_to_tol(proj.Parameter)
                    positions.append(dist)
            except Exception:
                pass

    positions.sort()
    return tuple(positions)


def compute_fingerprint(elem, panel_elements):
    """Compute the identity fingerprint for a structural member.

    Returns (type_name, length, bimsf_data, intersection_positions).
    """
    # 1. Family type name (profile)
    elem_type = doc.GetElement(elem.GetTypeId())
    type_name = elem_type.get_Parameter(
        DB.BuiltInParameter.ALL_MODEL_TYPE_NAME
    ).AsString() if elem_type else ""

    # 2. Length
    length = get_element_length(elem)

    # 3. BIMSF_Data (encodes punch/dimple config from Vertex BD)
    bimsf_data = _get_param_str(elem, "BIMSF_Data")

    # 4. Intersection positions (geometric dimple detection)
    intersections = get_intersection_positions(elem, panel_elements)

    return (type_name, length, bimsf_data, intersections)


def main():
    # Collect panels from host model
    panel_elements = pu.map_framing(doc)

    if not panel_elements:
        forms.alert(
            "No structural framing with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    # Filter out panels already fully assigned
    pending_panels = {}
    for pid, elements in panel_elements.items():
        if not is_panel_done(elements):
            pending_panels[pid] = elements

    if not pending_panels:
        forms.alert(
            "All panels already have Advanced Position IDs assigned.",
            title="UNIQUBE",
        )
        return

    # Show panel selection UI (only pending panels)
    selected = pu.choose_panels(pending_panels.keys())
    if not selected:
        return

    # Get current max ID in the project for global continuity
    current_max = get_max_existing_id(doc)
    next_id = current_max + 1

    # Build a global fingerprint-to-ID map from already-assigned elements
    fingerprint_map = {}
    for pid, elements in panel_elements.items():
        if pid in pending_panels and pid not in selected:
            continue
        for elem in elements:
            existing_id = _get_param_str(elem, POSITION_PARAM)
            if existing_id:
                try:
                    fp = compute_fingerprint(elem, elements)
                    if fp not in fingerprint_map:
                        fingerprint_map[fp] = int(existing_id)
                except Exception:
                    pass

    # Process selected panels
    assigned_count = 0
    instance_count = 0

    with revit.Transaction("UNIQUBE: Advance Position ID"):
        for pid in selected:
            elements = pending_panels.get(pid, [])
            if not elements:
                continue

            # Compute fingerprint for each element in this panel
            elem_fingerprints = []
            for elem in elements:
                # Skip elements already assigned
                if _get_param_str(elem, POSITION_PARAM):
                    continue
                fp = compute_fingerprint(elem, elements)
                elem_fingerprints.append((elem, fp))

            # Assign IDs
            for elem, fp in elem_fingerprints:
                if fp not in fingerprint_map:
                    fingerprint_map[fp] = next_id
                    next_id += 1
                    instance_count += 1

                pos_id = fingerprint_map[fp]
                param = elem.LookupParameter(POSITION_PARAM)
                if param and not param.IsReadOnly:
                    param.Set(str(pos_id))
                    assigned_count += 1

    forms.alert(
        "Done.\n\n"
        "Panels processed: {}\n"
        "Members assigned: {}\n"
        "Unique instances found: {}\n"
        "ID range: {} - {}".format(
            len(selected),
            assigned_count,
            instance_count,
            current_max + 1,
            next_id - 1,
        ),
        title="UNIQUBE — Advance Position ID",
    )


main()
