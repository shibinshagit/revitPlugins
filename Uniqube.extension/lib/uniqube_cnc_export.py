# -*- coding: utf-8 -*-
"""CFS CNC CSV export (rollformer COMPONENT format, Vertex BD compatible).

Reproduces the Vertex BD panel file format, e.g.
Example/CNC/WP-70_362S162-43(33)_Edited.csv:

    DETAILS,,<job name>
    COMPONENT,<label>,<section>,<role>,<NORMAL|INVERTED>,<qty>,
              <length>,<x0>,<y0>,<x1>,<y1>,<web_depth>,
              <OP>,<station>,...

Everything is millimetres in a panel-local 2D frame: X along the bottom
track (0 at one end), Y = height.

Role codes come from BIMSF_Description (TBOT, TTOP, EV, SV, HB, CR, ...);
floor trusses fall back to Comments (BottomChord, TopChord, Web, Support).

Tooling is derived from member-to-member crossings, not fixed guesses:
  * DIMPLE on both members at every crossing (fastener position)
  * SWAGE at SWAGE_END_OFFSET from each end of every non-track member
  * Track  x vertical crossing  -> track gets LIP NOTCH
  * EV     x horizontal infill  -> EV gets LIP NOTCH
  * Stud   x horizontal infill  -> stud gets SWAGE, infill gets
                                   WEB NOTCH + LIP NOTCH

Service openings cut in Revit (Structural Framing Opening) become:
  * SERVICE HOLE on studs / noggins / any non-track member
  * WEB HOLE on tracks (Vertex BD naming on bottom track)
"""

from __future__ import print_function

import math
import re
from collections import defaultdict

FT_TO_MM = 304.8
_EPS = 1e-9

# Fixed machine offsets calibrated against the working Vertex BD wall panel
# (CNC/WP-70_362S162-43(33)_Edited.csv).
SWAGE_END_OFFSET = 26.79

# Floor-truss offsets, calibrated against the Vertex BD truss file
# (Example/FT-1_362S162-43(50)_Edited.csv).
TRUSS_SUPPORT_DIMPLE = 17.84
TRUSS_SUPPORT_SWAGE = 27.30
TRUSS_WEB_DIMPLE = 9.53
TRUSS_WEB_SWAGE_A = 27.30
TRUSS_WEB_SWAGE_B = 46.11
TRUSS_CHORD_LIP_NEAR = 19.13
TRUSS_CHORD_LIP_FAR = 36.58

OP_END_TRUSS = "END_TRUSS"

# Crossing detection tolerances (mm)
_TOUCH_TOL_MM = 2.0
_PARALLEL_TOL = 0.02

OP_WEB_NOTCH = "WEB NOTCH"
OP_LIP_NOTCH = "LIP NOTCH"
OP_SWAGE = "SWAGE"
OP_DIMPLE = "DIMPLE"
OP_SERVICE_HOLE = "SERVICE HOLE"
OP_WEB_HOLE = "WEB HOLE"

# DIMPLE always last at a shared station; WEB NOTCH first.
_OP_PRIORITY = {
    OP_END_TRUSS: 0,
    OP_WEB_NOTCH: 1,
    OP_LIP_NOTCH: 2,
    OP_SWAGE: 3,
    OP_SERVICE_HOLE: 3,
    OP_WEB_HOLE: 3,
    OP_DIMPLE: 4,
}

# Role classes -------------------------------------------------------------
TRACK_ROLES = ("TBOT", "TTOP", "BottomChord", "TopChord")
END_VERTICAL_ROLES = ("EV",)
TRUSS_ROLES = ("BottomChord", "TopChord", "Web", "Support")
TRUSS_CHORD_ROLES = ("BottomChord", "TopChord")
# Roles that must never reach the machine.
SKIP_ROLES = ("Locked Panel",)
SKIP_TYPE_NAMES = ("LockedPanel",)

# Output row order (mirrors Vertex BD): verticals, tracks, then infill.
_ROLE_ORDER = [
    "EV",
    "SV",
    "SW",
    "CR",
    "SD",
    "TBOT",
    "TTOP",
    "HB",
    "SBW",
    "HDW",
    "HDD",
    "USER",
    # floor-truss roles
    "BottomChord",
    "TopChord",
    "Web",
    "Support",
]


# --- parameter helpers ----------------------------------------------------
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


def _param_double(element, name):
    try:
        p = element.LookupParameter(name)
        if p and p.HasValue and p.StorageType.ToString() == "Double":
            return p.AsDouble()
    except Exception:
        pass
    return None


def _excluded_from_cnc(element):
    p = element.LookupParameter("Exclude From CNC")
    if not p or not p.HasValue:
        return False
    try:
        if p.StorageType.ToString() == "Integer":
            return bool(p.AsInteger())
        text = (p.AsString() or p.AsValueString() or "").strip().lower()
        return text in ("1", "yes", "true", "y")
    except Exception:
        return False


_TYPE_NAME_CACHE = {}


def _type_name(doc, element):
    try:
        from Autodesk.Revit.DB import BuiltInParameter

        type_id = element.GetTypeId()
        key = (doc.PathName, type_id.IntegerValue)
        if key in _TYPE_NAME_CACHE:
            return _TYPE_NAME_CACHE[key]

        et = doc.GetElement(type_id)
        if et is None:
            name = ""
        else:
            p = et.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if p and p.HasValue:
                name = (p.AsString() or "").strip()
            else:
                name = (getattr(et, "Name", None) or "").strip()
        _TYPE_NAME_CACHE[key] = name
        return name
    except Exception:
        return ""


def _web_depth_mm(doc, element):
    try:
        et = doc.GetElement(element.GetTypeId())
        if et is not None:
            for name in ("d", "Height", "Depth", "Web Depth"):
                p = et.LookupParameter(name)
                if p and p.HasValue and p.StorageType.ToString() == "Double":
                    return p.AsDouble() * FT_TO_MM
        m = re.match(r"(\d{3})", _type_name(doc, element) or "")
        if m:
            return int(m.group(1)) / 100.0 * 25.4
    except Exception:
        pass
    return 0.0


def _curve_ends(element):
    loc = element.Location
    if loc is None or not hasattr(loc, "Curve") or loc.Curve is None:
        return None
    curve = loc.Curve
    return curve.GetEndPoint(0), curve.GetEndPoint(1), curve.Length


def _label_of(element):
    return (
        _param_string(element, "BIMSF_Label")
        or _param_string(element, "Label")
        or _param_string(element, "BIMSF_ScheduleLabel")
        or _param_string(element, "Mark")
        or "M{}".format(element.Id.IntegerValue)
    )


def _role_from_label(label):
    text = (label or "").strip().upper()
    if not text:
        return ""
    for prefix, role in (
        ("TTOP", "TTOP"),
        ("TBOT", "TBOT"),
        ("TC", "TopChord"),
        ("BC", "BottomChord"),
        ("HB", "HB"),
        ("WB", "Web"),
        ("E", "EV"),
        ("S", "SV"),
        ("C", "CR"),
    ):
        if text.startswith(prefix):
            return role
    return ""


def _role_of(element):
    """Role code: BIMSF_Description, else Tag, else Comments, else label."""
    for name in ("BIMSF_Description", "Tag"):
        value = _param_string(element, name)
        if value:
            return value
    comments = _param_string(element, "Comments")
    if comments:
        return comments
    return _role_from_label(_label_of(element))


def _is_track(role):
    return role in TRACK_ROLES


def _is_end_vertical(role):
    return role in END_VERTICAL_ROLES


def _is_structural_framing(element):
    try:
        from Autodesk.Revit.DB import BuiltInCategory

        cat = element.Category
        if cat is None:
            return False
        return cat.Id.IntegerValue == int(BuiltInCategory.OST_StructuralFraming)
    except Exception:
        return False


def _exportable(doc, element):
    if element is None or not _is_structural_framing(element):
        return False
    if _excluded_from_cnc(element) or not _curve_ends(element):
        return False
    role = _role_of(element)
    if not role or role in SKIP_ROLES:
        return False
    if _type_name(doc, element) in SKIP_TYPE_NAMES:
        return False
    return True


# --- unit collection ------------------------------------------------------
def _has_reference_member(doc, members):
    return any(_is_track(_role_of(el)) for el in members)


def unit_names_for_elements(doc, elements):
    """Panel / truss names the given elements belong to."""
    names = set()
    for el in elements or []:
        if el is None:
            continue
        try:
            asm_id = el.AssemblyInstanceId
            if asm_id is not None and asm_id.IntegerValue != -1:
                asm = doc.GetElement(asm_id)
                if asm is not None:
                    names.add(asm.Name or asm.AssemblyTypeName)
                    continue
        except Exception:
            pass
        container = _param_string(el, "BIMSF_Container") or _param_string(
            el, "MasterContainer"
        )
        if container:
            names.add(container)
    return names


def collect_cnc_units(doc):
    """Collect exportable panels/trusses as [{name, members, source}]."""
    _TYPE_NAME_CACHE.clear()
    from Autodesk.Revit.DB import (
        AssemblyInstance,
        BuiltInCategory,
        FilteredElementCollector,
    )

    units = []
    seen = set()

    for asm in FilteredElementCollector(doc).OfClass(AssemblyInstance):
        members = [
            el
            for el in (doc.GetElement(eid) for eid in asm.GetMemberIds())
            if _exportable(doc, el)
        ]
        if not _has_reference_member(doc, members):
            continue
        units.append(
            {
                "name": asm.Name or asm.AssemblyTypeName or "Assembly",
                "members": members,
                "source": "assembly",
            }
        )
        for el in members:
            seen.add(el.Id.IntegerValue)

    by_container = defaultdict(list)
    framing = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
    )
    for el in framing:
        if el.Id.IntegerValue in seen or not _exportable(doc, el):
            continue
        container = _param_string(el, "BIMSF_Container") or _param_string(
            el, "MasterContainer"
        )
        if container:
            by_container[container].append(el)

    for name, members in by_container.items():
        if not _has_reference_member(doc, members):
            continue
        units.append({"name": name, "members": members, "source": "panel"})

    units.sort(key=lambda u: _natural_key(u["name"]))
    return units


def _natural_key(text):
    parts = re.split(r"(\d+)", str(text or ""))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


# --- local frame ----------------------------------------------------------
def _pick_reference(members):
    """Bottom track / bottom chord defines the local X axis."""
    bottoms = [
        el for el in members if _role_of(el) in ("TBOT", "BottomChord")
    ]
    if not bottoms:
        bottoms = [el for el in members if _is_track(_role_of(el))]
    if not bottoms:
        return None
    return max(bottoms, key=lambda el: _curve_ends(el)[2])


def _flange_width_mm(doc, element):
    """Section flange width (bf): the bottom member's vertical extent."""
    try:
        et = doc.GetElement(element.GetTypeId())
        if et is not None:
            for name in ("bf", "b", "Flange Width", "Width"):
                p = et.LookupParameter(name)
                if p and p.HasValue and p.StorageType.ToString() == "Double":
                    return p.AsDouble() * FT_TO_MM
    except Exception:
        pass
    return 0.0


def _base_elevation_mm(doc, element):
    """World Z of the underside of the bottom track / chord.

    The bottom member lies web-down, so its centreline sits half a flange
    above the underside of the unit. Measuring heights from there keeps the
    output identical for a panel or truss whatever floor it sits on.
    """
    p0, p1, _ = _curve_ends(element)
    centre_z = min(p0.Z, p1.Z) * FT_TO_MM
    return centre_z - 0.5 * _flange_width_mm(doc, element)


def _build_local_frame(doc, members):
    """Return (origin, ux, uy, z0) for the panel-local frame.

    X runs along the bottom track from one end. Y is height above the level
    the unit is hosted on rather than project elevation, so a panel on an
    upper floor exports the same coordinates as the identical one at Z=0.
    """
    ref = _pick_reference(members)
    if ref is None:
        raise ValueError("no bottom track / chord to define the panel frame")
    p0, p1, _ = _curve_ends(ref)

    z0 = _base_elevation_mm(doc, ref)

    vx, vy = p1.X - p0.X, p1.Y - p0.Y
    horiz = math.sqrt(vx * vx + vy * vy)
    if horiz < 1e-9:
        return (p0.X, p0.Y), 1.0, 0.0, z0
    return (p0.X, p0.Y), vx / horiz, vy / horiz, z0


def _to_local(origin, ux, uy, z0, pt):
    ox, oy = origin
    return (
        ((pt.X - ox) * ux + (pt.Y - oy) * uy) * FT_TO_MM,
        pt.Z * FT_TO_MM - z0,
    )


def _vector_to_local(ux, uy, vec):
    return vec.X * ux + vec.Y * uy, vec.Z


def _flange_direction(element):
    """In-plane direction the C-section lips open toward (world vector)."""
    try:
        basis_y = element.GetTransform().BasisY
    except Exception:
        return None
    # BIMSF-SSMA sections point BasisY at the back of the web, so the open
    # side faces the opposite way.
    return -basis_y.X, -basis_y.Y, -basis_y.Z


def _orientation_sign(direction_2d, flange_2d):
    """2D cross product: >0 is NORMAL, <0 is INVERTED (before calibration)."""
    ax, ay = direction_2d
    fx, fy = flange_2d
    return ax * fy - ay * fx


def _normalize_ends(role, x0, y0, x1, y1):
    """Tracks and chords run left to right; everything else bottom to top.

    Matches how Vertex BD lists endpoints, including sloped truss webs.
    """
    if _is_track(role) or abs(y1 - y0) < 1.0:
        if x0 > x1 + _EPS:
            return x1, y1, x0, y0
    elif y0 > y1 + _EPS:
        return x1, y1, x0, y0
    return x0, y0, x1, y1


def build_member_records(doc, members):
    """Local-frame member dicts with roles, geometry and orientation."""
    if not members:
        return []
    origin, ux, uy, z0 = _build_local_frame(doc, members)

    records = []
    for el in members:
        p0, p1, _length_ft = _curve_ends(el)
        lx0, ly0 = _to_local(origin, ux, uy, z0, p0)
        lx1, ly1 = _to_local(origin, ux, uy, z0, p1)
        role = _role_of(el)
        is_vertical = abs(ly1 - ly0) > abs(lx1 - lx0)
        lx0, ly0, lx1, ly1 = _normalize_ends(role, lx0, ly0, lx1, ly1)
        length = math.hypot(lx1 - lx0, ly1 - ly0)
        if length < _EPS:
            continue

        direction = ((lx1 - lx0) / length, (ly1 - ly0) / length)
        flange = _flange_direction(el)
        sign = 0.0
        if flange is not None:
            fx, fy = flange[0] * ux + flange[1] * uy, flange[2]
            norm = math.hypot(fx, fy)
            if norm > 1e-6:
                sign = _orientation_sign(direction, (fx / norm, fy / norm))

        records.append(
            {
                "element": el,
                "id": el.Id.IntegerValue,
                "label": _label_of(el),
                "section": _type_name(doc, el),
                "role": role,
                "qty": 1,
                "length": length,
                "x0": lx0,
                "y0": ly0,
                "x1": lx1,
                "y1": ly1,
                "depth": _web_depth_mm(doc, el),
                "vertical": is_vertical,
                "sign": sign,
                "ops": [],
            }
        )

    _apply_orientation(records)
    return records


def _apply_orientation(records):
    """Bottom track is always NORMAL; everything else follows from it.

    Keeps NORMAL/INVERTED independent of which way the reference curve
    happens to run in Revit.
    """
    flip = False
    for rec in records:
        if rec["role"] in ("TBOT", "BottomChord") and rec["sign"] < 0:
            flip = True
            break
    for rec in records:
        sign = -rec["sign"] if flip else rec["sign"]
        rec["orient"] = "NORMAL" if sign > 0 else "INVERTED"


# --- crossings ------------------------------------------------------------
def _segment_cross(rec_a, rec_b):
    """Intersection point of two member centrelines, or None."""
    x1, y1, x2, y2 = rec_a["x0"], rec_a["y0"], rec_a["x1"], rec_a["y1"]
    x3, y3, x4, y4 = rec_b["x0"], rec_b["y0"], rec_b["x1"], rec_b["y1"]
    dx1, dy1 = x2 - x1, y2 - y1
    dx2, dy2 = x4 - x3, y4 - y3
    len_a = math.hypot(dx1, dy1)
    len_b = math.hypot(dx2, dy2)
    if len_a < _EPS or len_b < _EPS:
        return None
    den = dx1 * dy2 - dy1 * dx2
    # |den| / (len_a * len_b) is sin(angle): reject near-parallel members.
    if abs(den) < _PARALLEL_TOL * len_a * len_b:
        return None
    qx, qy = x3 - x1, y3 - y1
    t = (qx * dy2 - qy * dx2) / den
    u = (qx * dy1 - qy * dx1) / den
    pad_a = _TOUCH_TOL_MM / len_a
    pad_b = _TOUCH_TOL_MM / len_b
    if -pad_a <= t <= 1 + pad_a and -pad_b <= u <= 1 + pad_b:
        return x1 + t * dx1, y1 + t * dy1
    return None


def _station(rec, px, py):
    dx, dy = rec["x1"] - rec["x0"], rec["y1"] - rec["y0"]
    length = math.hypot(dx, dy)
    if length < _EPS:
        return 0.0
    t = ((px - rec["x0"]) * dx + (py - rec["y0"]) * dy) / (length * length)
    return max(0.0, min(1.0, t)) * length


def _end_tolerance(rec):
    """A crossing this close to an end means the member butts in there."""
    depth = rec.get("depth") or 0.0
    return 0.5 * depth + 2.0 if depth else 50.0


def _terminates_at(rec, station):
    tol = _end_tolerance(rec)
    return station <= tol or station >= rec["length"] - tol


def _receiver_rank(rec):
    """Lower wins when two members butt into each other at a corner."""
    if _is_track(rec["role"]):
        return 0
    if _is_end_vertical(rec["role"]):
        return 1
    if rec["vertical"]:
        return 2
    return 3


def find_crossings(records):
    """Per-member crossing list with the joint type already resolved.

    Each entry is {station, joint} where joint is one of:
      receive - the other member butts into this one (lips notched here)
      butt    - this member butts into the other one (no extra tooling)
      cross   - both members run through the joint
    """
    crossings = dict((rec["id"], []) for rec in records)
    for i, rec_a in enumerate(records):
        for rec_b in records[i + 1 :]:
            hit = _segment_cross(rec_a, rec_b)
            if hit is None:
                continue
            px, py = hit
            station_a = _station(rec_a, px, py)
            station_b = _station(rec_b, px, py)
            ends_a = _terminates_at(rec_a, station_a)
            ends_b = _terminates_at(rec_b, station_b)

            if ends_a and ends_b:
                # Corner joint: the track (then the end stud) receives.
                a_receives = _receiver_rank(rec_a) <= _receiver_rank(rec_b)
                joint_a = "receive" if a_receives else "butt"
                joint_b = "butt" if a_receives else "receive"
            elif ends_a:
                joint_a, joint_b = "butt", "receive"
            elif ends_b:
                joint_a, joint_b = "receive", "butt"
            else:
                joint_a = joint_b = "cross"

            crossings[rec_a["id"]].append(
                {"station": station_a, "joint": joint_a}
            )
            crossings[rec_b["id"]].append(
                {"station": station_b, "joint": joint_b}
            )
    return crossings


def is_truss_unit(records):
    """Floor/roof trusses carry chord and web roles instead of track codes."""
    return any(rec["role"] in TRUSS_ROLES for rec in records)


def _truss_joints(records):
    """Chord crossings with webs and supports, with the web lean direction."""
    chords = [r for r in records if r["role"] in TRUSS_CHORD_ROLES]
    others = [r for r in records if r["role"] in ("Web", "Support")]
    joints = dict((rec["id"], []) for rec in records)
    for chord in chords:
        for other in others:
            hit = _segment_cross(chord, other)
            if hit is None:
                continue
            px, py = hit
            lean = 0
            if other["role"] == "Web":
                mid_x = 0.5 * (other["x0"] + other["x1"])
                lean = 1 if mid_x > px else -1
            joints[chord["id"]].append(
                {
                    "station": _station(chord, px, py),
                    "kind": other["role"],
                    "lean": lean,
                }
            )
    return joints


def build_truss_operations(member, joints_for_member):
    """Tooling for truss members (calibrated on the FT-1 Vertex BD file)."""
    role = member["role"]
    length = member["length"]
    ops = []

    if role == "Support":
        ops.extend(
            [
                (OP_DIMPLE, TRUSS_SUPPORT_DIMPLE),
                (OP_SWAGE, TRUSS_SUPPORT_SWAGE),
                (OP_SWAGE, length - TRUSS_SUPPORT_SWAGE),
                (OP_DIMPLE, length - TRUSS_SUPPORT_DIMPLE),
            ]
        )
    elif role == "Web":
        ops.extend(
            [
                (OP_END_TRUSS, 0.0),
                (OP_DIMPLE, TRUSS_WEB_DIMPLE),
                (OP_SWAGE, TRUSS_WEB_SWAGE_A),
                (OP_SWAGE, TRUSS_WEB_SWAGE_B),
                (OP_SWAGE, length - TRUSS_WEB_SWAGE_B),
                (OP_SWAGE, length - TRUSS_WEB_SWAGE_A),
                (OP_DIMPLE, length - TRUSS_WEB_DIMPLE),
                (OP_END_TRUSS, length),
            ]
        )
    elif role in TRUSS_CHORD_ROLES:
        for joint in joints_for_member:
            station = joint["station"]
            if joint["kind"] == "Support":
                ops.append((OP_LIP_NOTCH, station))
                ops.append((OP_DIMPLE, station))
            else:
                lean = joint["lean"] or 1
                ops.append((OP_DIMPLE, station))
                ops.append((OP_LIP_NOTCH, station + lean * TRUSS_CHORD_LIP_NEAR))
                ops.append((OP_LIP_NOTCH, station + lean * TRUSS_CHORD_LIP_FAR))

    return _finish_operations(ops, length)


def build_operations(member, crossings_for_member, openings_for_member=None):
    """Machine operations for one member, sorted along its length."""
    length = member["length"]
    ops = []

    # Members that butt into others are swaged near both ends so the
    # connection lies flat. Tracks run edge to edge and are not swaged.
    if not _is_track(member["role"]) and length > 2 * SWAGE_END_OFFSET:
        ops.append((OP_SWAGE, SWAGE_END_OFFSET))
        ops.append((OP_SWAGE, length - SWAGE_END_OFFSET))

    for crossing in crossings_for_member:
        station = crossing["station"]
        joint = crossing["joint"]

        if joint == "receive":
            # The other member lands here: notch the lips to seat it.
            ops.append((OP_LIP_NOTCH, station))
        elif joint == "cross":
            # Both members run through: the horizontal is notched over the
            # vertical, and the vertical is swaged flat underneath it.
            if member["vertical"]:
                ops.append((OP_SWAGE, station))
            else:
                ops.append((OP_WEB_NOTCH, station))
                ops.append((OP_LIP_NOTCH, station))

        ops.append((OP_DIMPLE, station))

    # Revit structural openings punched through the member web.
    # Vertex BD writes SERVICE HOLE on studs/noggins and WEB HOLE on tracks.
    hole_op = OP_WEB_HOLE if _is_track(member["role"]) else OP_SERVICE_HOLE
    for station in openings_for_member or []:
        ops.append((hole_op, station))

    return _finish_operations(ops, length)


def find_openings(doc, records):
    """Station of every structural opening along each member centreline.

    Returns {member_id: [station_mm, ...]} for openings hosted on the
    given framing. Stations are measured from the exported (x0, y0) end.
    """
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, XYZ

    by_id = dict((rec["id"], rec) for rec in records)
    if not by_id:
        return {}

    # World points of the exported local frame, so an opening centre can
    # be projected onto the same 2D axes the member ends use.
    members = [r["element"] for r in records]
    origin, ux, uy, z0 = _build_local_frame(doc, members)

    out = defaultdict(list)
    openings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFramingOpening)
        .WhereElementIsNotElementType()
    )
    for opening in openings:
        try:
            host = opening.Host
        except Exception:
            host = None
        if host is None or host.Id.IntegerValue not in by_id:
            continue
        bb = opening.get_BoundingBox(None)
        if bb is None:
            continue
        mid = XYZ(
            (bb.Min.X + bb.Max.X) * 0.5,
            (bb.Min.Y + bb.Max.Y) * 0.5,
            (bb.Min.Z + bb.Max.Z) * 0.5,
        )
        lx, ly = _to_local(origin, ux, uy, z0, mid)
        rec = by_id[host.Id.IntegerValue]
        station = _station(rec, lx, ly)
        if station < -0.5 or station > rec["length"] + 0.5:
            continue
        out[rec["id"]].append(max(0.0, min(rec["length"], station)))

    for stations in out.values():
        stations.sort()
    return out


def _finish_operations(ops, length):
    """Sort along the member, drop out-of-range and duplicate operations."""
    ops.sort(key=lambda op: (round(op[1], 3), _OP_PRIORITY.get(op[0], 9)))
    cleaned = []
    for name, station in ops:
        if station < -0.5 or station > length + 0.5:
            continue
        clamped = max(0.0, min(length, station))
        if cleaned and cleaned[-1][0] == name and abs(cleaned[-1][1] - clamped) < 0.01:
            continue
        cleaned.append((name, clamped))
    return cleaned


# --- formatting -----------------------------------------------------------
def format_number(value):
    """Fixed 2 decimals, as written by Vertex BD (e.g. 914.40, 0.00)."""
    text = "{:.2f}".format(round(value + 1e-9, 2))
    return "0.00" if text == "-0.00" else text


def panel_prefix(unit_name, is_truss=False):
    """Component label prefix: WP-70 -> W70, LB-1 -> LB1.

    Truss components are named on their own (BC1-2, WB3-5) and take no
    prefix, the way Vertex BD writes them.
    """
    if is_truss:
        return ""
    compact = re.sub(r"[\s_-]+", "", str(unit_name or ""))
    if not compact:
        return ""
    # Vertex drops the P of a WP panel: WP-70 becomes W70.
    match = re.match(r"^([A-Za-z])P(\d+)$", compact)
    if match:
        return "{}{}".format(match.group(1), match.group(2))
    return compact


def component_label(prefix, label):
    if not prefix:
        return label
    # Some models already carry the panel name in BIMSF_Label.
    if str(label).startswith(prefix + "-"):
        return label
    return "{}-{}".format(prefix, label)


def component_fields(rec, prefix=""):
    fields = [
        "COMPONENT",
        component_label(prefix, rec["label"]),
        rec["section"],
        rec["role"],
        rec["orient"],
        str(int(rec.get("qty", 1))),
        format_number(rec["length"]),
        format_number(rec["x0"]),
        format_number(rec["y0"]),
        format_number(rec["x1"]),
        format_number(rec["y1"]),
        format_number(rec["depth"]),
    ]
    for name, station in rec.get("ops") or []:
        fields.append(name)
        fields.append(format_number(station))
    return fields


def _role_rank(role):
    try:
        return _ROLE_ORDER.index(role)
    except ValueError:
        return len(_ROLE_ORDER)


def format_csv(job_name, records, prefix=""):
    ordered = sorted(
        records,
        key=lambda r: (_role_rank(r["role"]), _natural_key(r["label"])),
    )
    rows = [["DETAILS", "", job_name or ""]]
    rows.extend(component_fields(rec, prefix) for rec in ordered)
    return "\r\n".join(",".join(row) for row in rows) + "\r\n"


def suggest_filename(unit_name, section):
    base = str(unit_name or "Panel").strip()
    if re.match(r"^FT-", base, re.IGNORECASE):
        base = re.sub(r"-\d+$", "", base)
    section = str(section or "section").strip()
    for ch in '<>:"/\\|?*':
        base = base.replace(ch, "_")
        section = section.replace(ch, "_")
    # The machine expects a trailing underscore before the extension.
    return "{}_{}_.csv".format(base, section)


# --- entry point ----------------------------------------------------------
def export_unit(doc, unit, job_name=None):
    """Return (filename, csv_text, component_count) for one panel/truss."""
    records = build_member_records(doc, unit.get("members") or [])
    if not records:
        raise ValueError("no exportable framing in {}".format(unit.get("name")))

    truss = is_truss_unit(records)
    openings = find_openings(doc, records)
    if truss:
        joints = _truss_joints(records)
        for rec in records:
            ops = build_truss_operations(rec, joints.get(rec["id"], []))
            for station in openings.get(rec["id"], []):
                ops.append((OP_SERVICE_HOLE, station))
            rec["ops"] = _finish_operations(ops, rec["length"])
    else:
        crossings = find_crossings(records)
        for rec in records:
            rec["ops"] = build_operations(
                rec,
                crossings.get(rec["id"], []),
                openings.get(rec["id"], []),
            )

    section = next((r["section"] for r in records if r["section"]), "")
    name = unit.get("name") or "Panel"
    text = format_csv(job_name, records, prefix=panel_prefix(name, truss))
    return suggest_filename(name, section), text, len(records)


def export_assembly(doc, assembly, job_name=None):
    """Back-compat wrapper for a Revit AssemblyInstance."""
    members = [
        el
        for el in (doc.GetElement(eid) for eid in assembly.GetMemberIds())
        if _exportable(doc, el)
    ]
    unit = {
        "name": assembly.Name or assembly.AssemblyTypeName or "Assembly",
        "members": members,
        "source": "assembly",
    }
    return export_unit(doc, unit, job_name=job_name)
