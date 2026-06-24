# -*- coding: utf-8 -*-
"""Assign global sequential Advance Position IDs to structural framing.

Members with the same fingerprint receive the same ID. The fingerprint is
family type + nominal length + punch positions (each hole's distance along
the member centerline), so studs with different punches get different IDs
while truly identical members share one ID. The fingerprint is join-
independent, so wall studs and floor truss members both group correctly.
IDs are global across the entire project. Includes a Reset option.

Wall panels are listed by BIMSF_Container. Floor trusses are listed one
level down, per truss assembly (by assembly name) with an instance count.
"""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes

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


def _get_family_type_name(elem):
    """Get the full family type name like 'BIMSF-SSMA-S 600S162-43'."""
    elem_type = doc.GetElement(elem.GetTypeId())
    if elem_type is None:
        return ""
    fam_name = elem_type.get_Parameter(
        DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM
    )
    type_name = elem_type.get_Parameter(
        DB.BuiltInParameter.SYMBOL_NAME_PARAM
    )
    fam = fam_name.AsString() if fam_name and fam_name.HasValue else ""
    typ = type_name.AsString() if type_name and type_name.HasValue else ""
    return "{} {}".format(fam, typ).strip()


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


def get_element_length(elem):
    """Get curve length rounded to tolerance."""
    loc = elem.Location
    if isinstance(loc, DB.LocationCurve):
        return _round_to_tol(loc.Curve.Length)
    return 0.0


def _collect_solids(geo_element):
    """Yield all non-empty solids from a geometry element (handles nesting)."""
    solids = []
    if geo_element is None:
        return solids
    for g in geo_element:
        if isinstance(g, DB.Solid):
            if g.Volume > 0:
                solids.append(g)
        elif isinstance(g, DB.GeometryInstance):
            inst_geo = g.GetInstanceGeometry()
            solids.extend(_collect_solids(inst_geo))
    return solids


def _loop_points(curve_loop):
    """Tessellate every curve in a loop into a flat list of XYZ points."""
    pts = []
    for curve in curve_loop:
        for p in curve.Tessellate():
            pts.append(p)
    return pts


def _centroid(pts):
    """Average position of a list of XYZ points."""
    n = len(pts)
    sx = sum(p.X for p in pts)
    sy = sum(p.Y for p in pts)
    sz = sum(p.Z for p in pts)
    return DB.XYZ(sx / n, sy / n, sz / n)


def _bbox_diag(pts):
    """Diagonal length of the bounding box around a list of points."""
    xs = [p.X for p in pts]
    ys = [p.Y for p in pts]
    zs = [p.Z for p in pts]
    mn = DB.XYZ(min(xs), min(ys), min(zs))
    mx = DB.XYZ(max(xs), max(ys), max(zs))
    return mn.DistanceTo(mx)


def get_punch_positions(elem):
    """Return positions of punches/holes along the member's centerline.

    Standard punches are through-holes in the web, which appear as inner
    loops on the flat web faces. For each hole we take its centroid, project
    it onto the member's centerline, and record the distance along the
    length. The result is made orientation-independent (a stud modeled
    start->end matches an identical stud modeled end->start) and rounded so
    identical studs match exactly.

    Two studs with the same number of punches but in DIFFERENT positions
    produce different tuples, so they get different IDs.

    Returns a sorted tuple of rounded distances, or () on failure.
    """
    loc = elem.Location
    if not isinstance(loc, DB.LocationCurve):
        return ()
    line = loc.Curve
    try:
        start = line.GetEndPoint(0)
        end = line.GetEndPoint(1)
    except Exception:
        return ()
    length = start.DistanceTo(end)
    if length < 1e-6:
        return ()
    direction = end.Subtract(start).Normalize()

    try:
        opts = DB.Options()
        opts.ComputeReferences = False
        opts.DetailLevel = DB.ViewDetailLevel.Fine
        geo = elem.get_Geometry(opts)
        solids = _collect_solids(geo)
    except Exception as ex:
        logger.debug("Punch geometry read failed: %s", ex)
        return ()

    positions = []
    for s in solids:
        for face in s.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            try:
                loops = face.GetEdgesAsCurveLoops()
            except Exception:
                continue
            if loops.Count < 2:
                continue
            loop_data = []
            for lp in loops:
                pts = _loop_points(lp)
                if pts:
                    loop_data.append((_bbox_diag(pts), _centroid(pts)))
            if len(loop_data) < 2:
                continue
            # Largest loop is the outer boundary; the rest are holes.
            loop_data.sort(key=lambda x: x[0], reverse=True)
            for _diag, cen in loop_data[1:]:
                dist = cen.Subtract(start).DotProduct(direction)
                positions.append(_round_to_tol(dist))

    if not positions:
        return ()

    rounded_len = _round_to_tol(length)
    forward = tuple(sorted(positions))
    reverse = tuple(sorted(rounded_len - d for d in positions))
    return min(forward, reverse)


def compute_fingerprint(elem):
    """Compute the identity fingerprint for a structural member.

    Fingerprint = (type, length, punch_positions)
    Two members are the same "instance" only if all parts match.
    - Type determines the profile/gauge
    - Length is the nominal centerline length (join-independent)
    - Punch positions are each hole's distance along the member's own
      centerline, made orientation-independent. This catches a punch being
      removed or relocated, including the case where two studs have the same
      number of punches but in DIFFERENT spots.

    We deliberately do NOT use raw solid face/edge counts or volume. Members
    that are joined/coped to neighbors (e.g. floor truss web members and
    chords) report different geometry depending on what they connect to, so
    those measures would wrongly split identical members. Punch positions are
    measured along the member's centerline and are unaffected by joins, so
    walls and floor trusses both group correctly.
    """
    type_name = _get_family_type_name(elem)
    length = get_element_length(elem)
    punches = get_punch_positions(elem)
    return (type_name, length, punches)


# --------------- PANEL COLLECTION ---------------

def _is_structural_framing(elem):
    """True if the element is in the Structural Framing category."""
    cat = elem.Category
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


def collect_panel_elements(doc):
    """Return {panel_id: [framing element, ...]} from the host model.

    Includes two sources:
    1. Wall panels: framing members that carry BIMSF_Container directly.
       Keyed by the container value (e.g. *ELB-1001).
    2. Floor trusses: members of assemblies that carry BIMSF_Container but
       where the container lives on the assembly, not each member. These are
       grouped one level down - per individual truss assembly, keyed by the
       assembly name (e.g. CT003-3) - rather than by the whole floor panel.
    """
    panel_elements = pu.map_framing(doc)

    assemblies = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    # Aggregate truss members by assembly name and count instances per name.
    truss_members = {}
    truss_counts = {}
    for asm in assemblies:
        cparam = asm.LookupParameter(pu.PARAM_NAME)
        if not (cparam and cparam.HasValue and cparam.AsString()):
            continue

        # Only pull in members that don't already have their own container
        # (those are wall studs already handled above). What's left are the
        # truss members whose container only lives on the assembly.
        members = []
        for mid in asm.GetMemberIds():
            member = doc.GetElement(mid)
            if member is None or not _is_structural_framing(member):
                continue
            mc = member.LookupParameter(pu.PARAM_NAME)
            if mc and mc.HasValue and mc.AsString():
                continue
            members.append(member)
        if not members:
            continue

        try:
            name = asm.Name
        except Exception:
            name = None
        if not name:
            name = cparam.AsString()

        truss_counts[name] = truss_counts.get(name, 0) + 1
        bucket = truss_members.setdefault(name, [])
        bucket_ids = set(e.Id for e in bucket)
        for member in members:
            if member.Id not in bucket_ids:
                bucket.append(member)
                bucket_ids.add(member.Id)

    # Add truss entries with an instance count suffix, e.g. "CT003-3 [ count 2 ]".
    for name, members in truss_members.items():
        label = "{} [ count {} ]".format(name, truss_counts[name])
        panel_elements[label] = members

    return panel_elements


def _is_truss_key(key):
    """Truss entries carry a '[ count N ]' suffix; wall panels do not."""
    return "[ count " in key


def _links_with_framing():
    """Return names of loaded links that contain framing with BIMSF_Container.

    Used to give a helpful message when the panels live in a link (which
    cannot be edited from the host), instead of a generic 'not found'.
    """
    names = []
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        ldoc = link_inst.GetLinkDocument()
        if ldoc is None:
            continue
        framing = (
            DB.FilteredElementCollector(ldoc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for f in framing:
            p = f.LookupParameter(pu.PARAM_NAME)
            if p and p.HasValue and p.AsString():
                try:
                    names.append(ldoc.Title)
                except Exception:
                    names.append("linked model")
                break
    return names


def _alert_no_framing():
    """Helpful message: explain link limitation if framing is in a link."""
    link_names = _links_with_framing()
    if link_names:
        forms.alert(
            "The structural framing with '{0}' is inside a linked model:\n"
            "  {1}\n\n"
            "Advance Position ID writes the ID onto each framing member, and "
            "Revit does not allow editing elements inside a linked model from "
            "the host.\n\n"
            "Open that linked model directly (double-click it, or open the "
            ".rvt), run Advance Position ID there, then save the link. The IDs "
            "will show through in this host model.".format(
                pu.PARAM_NAME, "\n  ".join(sorted(set(link_names)))
            ),
            title="UNIQUBE — Advance Position ID",
        )
    else:
        forms.alert(
            "No structural framing with '{}' found in this model.\n\n"
            "If the panels come from Vertex BD / IFC, run 'Setup BIMSF' or "
            "'IFC Panel Mapper' first to populate BIMSF_Container.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )


class PanelSelector(forms.WPFWindow):
    """Checkbox list of panels. Truss entries are shown in red."""

    def __init__(self, keys):
        forms.WPFWindow.__init__(self, "SelectPanels.xaml")
        self._checks = []
        for key in keys:
            cb = CheckBox()
            cb.Content = key
            cb.Margin = self._row_margin()
            if _is_truss_key(key):
                cb.Foreground = Brushes.Red
            self.panel_stack.Children.Add(cb)
            self._checks.append((cb, key))
        self.selected = None

    @staticmethod
    def _row_margin():
        from System.Windows import Thickness
        return Thickness(2, 3, 2, 3)

    def select_all_click(self, sender, args):
        all_on = all(cb.IsChecked for cb, _ in self._checks)
        for cb, _ in self._checks:
            cb.IsChecked = not all_on

    def ok_click(self, sender, args):
        self.selected = [key for cb, key in self._checks if cb.IsChecked]
        self.Close()


def choose_panels_colored(panel_ids):
    """Show the custom selector (truss rows in red). Falls back to the
    standard pyRevit dialog if the custom window fails to load."""
    keys = sorted(panel_ids)
    if not keys:
        return None
    try:
        win = PanelSelector(keys)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed, using default: %s", ex)
        return pu.choose_panels(panel_ids)


# --------------- RESET MODE ---------------

def run_reset():
    """Clear Advanced Position IDs from selected panels."""
    panel_elements = collect_panel_elements(doc)

    if not panel_elements:
        _alert_no_framing()
        return

    # Find panels that have at least one assigned element
    assigned_panels = {}
    for pid, elements in panel_elements.items():
        for elem in elements:
            if _get_param_str(elem, POSITION_PARAM):
                assigned_panels[pid] = elements
                break

    if not assigned_panels:
        forms.alert(
            "No panels have Position IDs to reset.",
            title="UNIQUBE",
        )
        return

    selected = choose_panels_colored(assigned_panels.keys())
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
        "Panels reset: {}\n"
        "Members cleared: {}".format(len(selected), cleared),
        title="UNIQUBE — Reset Position IDs",
    )


# --------------- ASSIGN MODE ---------------

def run_assign():
    """Assign Position IDs to selected panels."""
    panel_elements = collect_panel_elements(doc)

    if not panel_elements:
        _alert_no_framing()
        return

    # Filter out panels already fully assigned
    pending_panels = {}
    for pid, elements in panel_elements.items():
        if not is_panel_done(elements):
            pending_panels[pid] = elements

    if not pending_panels:
        forms.alert(
            "All panels already have Advanced Position IDs assigned.\n\n"
            "Use 'Reset Position IDs' to clear and re-run.",
            title="UNIQUBE",
        )
        return

    selected = choose_panels_colored(pending_panels.keys())
    if not selected:
        return

    # Get current max ID in the project for global continuity
    current_max = get_max_existing_id(doc)
    next_id = current_max + 1

    # Build fingerprint-to-ID map from already-assigned elements globally
    fingerprint_map = {}
    for pid, elements in panel_elements.items():
        for elem in elements:
            existing_id = _get_param_str(elem, POSITION_PARAM)
            if existing_id:
                try:
                    fp = compute_fingerprint(elem)
                    existing_num = int(existing_id)
                    if fp not in fingerprint_map:
                        fingerprint_map[fp] = existing_num
                except (ValueError, TypeError):
                    pass

    # Process selected panels
    assigned_count = 0
    instance_count = 0

    with revit.Transaction("UNIQUBE: Advance Position ID"):
        for pid in selected:
            elements = pending_panels.get(pid, [])
            if not elements:
                continue

            for elem in elements:
                if _get_param_str(elem, POSITION_PARAM):
                    continue

                fp = compute_fingerprint(elem)

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


# --------------- MAIN ---------------

def main():
    action = forms.CommandSwitchWindow.show(
        ["Assign Position IDs", "Reset Position IDs"],
        message="Choose action:",
    )
    if not action:
        return

    if action == "Assign Position IDs":
        run_assign()
    elif action == "Reset Position IDs":
        run_reset()


main()
