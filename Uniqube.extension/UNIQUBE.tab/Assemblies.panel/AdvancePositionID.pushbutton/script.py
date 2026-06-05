# -*- coding: utf-8 -*-
"""Assign global sequential Advance Position IDs to structural framing.

Members with the same fingerprint receive the same ID.
Fingerprint uses BIMSF_Data (primary) or geometric intersections (fallback).
IDs are global across the entire project.
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


def get_intersection_count(elem, panel_elements):
    """Count how many other members connect to this element (within tolerance).

    Returns the count of intersecting/nearby members as a simple integer.
    This is used only as a fallback when BIMSF_Data is not available.
    """
    curve_a = get_element_curve(elem)
    if curve_a is None:
        return 0

    count = 0
    for other in panel_elements:
        if other.Id == elem.Id:
            continue
        curve_b = get_element_curve(other)
        if curve_b is None:
            continue

        try:
            mid_b = curve_b.Evaluate(0.5, True)
            proj = curve_a.Project(mid_b)
            if proj and proj.Distance < TOLERANCE_FT * 6:
                count += 1
        except Exception:
            pass

    return count


def compute_fingerprint(elem, panel_elements):
    """Compute the identity fingerprint for a structural member.

    Strategy:
    - If BIMSF_Data is available, it already encodes the full stud
      configuration (punches, dimples, connections). Use it directly
      with type name and length. No geometric analysis needed.
    - If BIMSF_Data is empty, fall back to type + length + connection count.
    """
    # 1. Family type name (profile like "BIMSF-SSMA-S 600S162-43")
    elem_type = doc.GetElement(elem.GetTypeId())
    type_name = ""
    if elem_type:
        p = elem_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            type_name = p.AsString() or ""

    # 2. Length rounded to tolerance
    length = get_element_length(elem)

    # 3. BIMSF_Data — primary discriminator
    bimsf_data = _get_param_str(elem, "BIMSF_Data")

    if bimsf_data:
        # BIMSF_Data encodes the full configuration; no need for geometry
        return (type_name, length, bimsf_data)
    else:
        # Fallback: use connection count as a rough geometric discriminator
        conn_count = get_intersection_count(elem, panel_elements)
        return (type_name, length, "", conn_count)


# ---------------------------------------------------------------------------
# RESET functionality
# ---------------------------------------------------------------------------

def reset_position_ids():
    """Clear Advanced Position IDs from selected panels."""
    panel_elements = pu.map_framing(doc)
    if not panel_elements:
        forms.alert("No panels found.", title="UNIQUBE")
        return

    # Show only panels that HAVE assigned IDs
    assigned_panels = {}
    for pid, elements in panel_elements.items():
        has_any = False
        for elem in elements:
            if _get_param_str(elem, POSITION_PARAM):
                has_any = True
                break
        if has_any:
            assigned_panels[pid] = elements

    if not assigned_panels:
        forms.alert(
            "No panels have Position IDs assigned yet.",
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(assigned_panels.keys())
    if not selected:
        return

    cleared = 0
    with revit.Transaction("UNIQUBE: Reset Position IDs"):
        for pid in selected:
            elements = assigned_panels.get(pid, [])
            for elem in elements:
                param = elem.LookupParameter(POSITION_PARAM)
                if param and not param.IsReadOnly and param.HasValue:
                    param.Set("")
                    cleared += 1

    forms.alert(
        "Reset complete.\n\n"
        "Panels cleared: {}\n"
        "Members cleared: {}".format(len(selected), cleared),
        title="UNIQUBE — Reset Position IDs",
    )


# ---------------------------------------------------------------------------
# ASSIGN functionality
# ---------------------------------------------------------------------------

def assign_position_ids():
    """Main assignment logic."""
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
            "All panels already have Advanced Position IDs assigned.\n\n"
            "Use 'Reset IDs' to clear and re-run.",
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(pending_panels.keys())
    if not selected:
        return

    # Get current max ID in the project for global continuity
    current_max = get_max_existing_id(doc)
    next_id = current_max + 1

    # Build fingerprint-to-ID map from already-assigned elements in project
    fingerprint_map = {}
    for pid, elements in panel_elements.items():
        for elem in elements:
            existing_id = _get_param_str(elem, POSITION_PARAM)
            if existing_id:
                try:
                    fp = compute_fingerprint(elem, elements)
                    existing_num = int(existing_id)
                    if fp not in fingerprint_map:
                        fingerprint_map[fp] = existing_num
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

            # Compute fingerprint for each unassigned element
            elem_fingerprints = []
            for elem in elements:
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
            current_max + 1 if instance_count else 0,
            next_id - 1 if instance_count else 0,
        ),
        title="UNIQUBE — Advance Position ID",
    )


# ---------------------------------------------------------------------------
# Entry point — choose mode
# ---------------------------------------------------------------------------

def main():
    action = forms.CommandSwitchWindow.show(
        ["Assign IDs", "Reset IDs"],
        message="Advance Position ID — choose action:",
    )
    if action == "Assign IDs":
        assign_position_ids()
    elif action == "Reset IDs":
        reset_position_ids()


main()
