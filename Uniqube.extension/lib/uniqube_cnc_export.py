# -*- coding: utf-8 -*-
"""CFS CNC CSV export (rollformer COMPONENT format).

Supports:
  - Floor trusses in Revit assemblies (Comments = TopChord / BottomChord)
  - Wall panels grouped by BIMSF_Container (labels TTOP/TBOT/E/S/HB)

Matches Example/FT-1_362S162-43(50)_Edited.csv:

    DETAILS,,<job name>
    COMPONENT,<id>,<section>,<role>,<orient>,<qty>,
              <length>,<x0>,<y0>,<x1>,<y1>,<web_depth>,
              <OP>,<pos>,...

Coordinates and lengths are millimetres. Local X runs along the bottom
track/chord; local Y is height (world Z).
"""

from __future__ import print_function

import math
import re
from collections import defaultdict

# --- tooling offsets (mm), reverse-engineered from the example CSV ----------
SUPPORT_DIMPLE = 17.84
SUPPORT_SWAGE = 27.30

WEB_DIMPLE = 9.53
WEB_SWAGE_A = 27.30
WEB_SWAGE_B = 46.11

CHORD_LIP_NEAR = 19.13
CHORD_LIP_FAR = 36.58

FT_TO_MM = 304.8
_EPS = 1e-9
_JOINT_TOL_MM = 3.0

_ROLE_ORDER = {
    "BottomChord": 0,
    "TopChord": 1,
    "Nogging": 2,
    "Web": 3,
    "Support": 4,
}

_HORIZONTAL_ROLES = ("BottomChord", "TopChord", "Nogging")


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


def _type_name(doc, element):
    try:
        from Autodesk.Revit.DB import BuiltInParameter

        et = doc.GetElement(element.GetTypeId())
        if et is None:
            return ""
        p = et.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            return (p.AsString() or "").strip()
        return (getattr(et, "Name", None) or "").strip()
    except Exception:
        return ""


def _web_depth_mm(doc, element):
    try:
        et = doc.GetElement(element.GetTypeId())
        if et is None:
            return 0.0
        for name in ("d", "Height", "Depth", "Web Depth"):
            p = et.LookupParameter(name)
            if p and p.HasValue and p.StorageType.ToString() == "Double":
                return round(p.AsDouble() * FT_TO_MM, 2)
        tname = _type_name(doc, element)
        m = re.match(r"(\d{3})", tname or "")
        if m:
            hundredths = int(m.group(1))
            return round(hundredths / 100.0 * 25.4, 2)
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
        or _param_string(element, "BIMSF_ScheduleLabel")
        or _param_string(element, "Mark")
        or "M{}".format(element.Id.IntegerValue)
    )


def _role_from_label(label):
    """Infer CNC role from BIMSF_Label when Comments is empty."""
    text = (label or "").strip().upper()
    if not text:
        return ""
    if text.startswith("TTOP") or text.startswith("TC"):
        return "TopChord"
    if text.startswith("TBOT") or text.startswith("BC"):
        return "BottomChord"
    if text.startswith("HB") or text.startswith("NG") or text.startswith("BR"):
        return "Nogging"
    if text.startswith("WB"):
        # floor-truss webs vs wall labels — WB without digit pattern still Web
        return "Web"
    if text.startswith("E") or text.startswith("S") or text.startswith("ST"):
        return "Support"
    return ""


def _role_of(element):
    """Comments first (truss), else label prefixes (wall panel)."""
    comments = _param_string(element, "Comments") or ""
    if comments in (
        "TopChord",
        "BottomChord",
        "Web",
        "Support",
        "Nogging",
    ):
        return comments
    # map common aliases
    low = comments.lower()
    if low in ("top track", "toptrack", "top plate"):
        return "TopChord"
    if low in ("bottom track", "bottomtrack", "bottom plate", "sole plate"):
        return "BottomChord"
    if low in ("stud", "end stud", "king stud", "jack stud"):
        return "Support"
    if low in ("nogging", "noggin", "bridging", "blocking"):
        return "Nogging"
    inferred = _role_from_label(_label_of(element))
    return inferred or comments


def _is_structural_framing(element):
    try:
        from Autodesk.Revit.DB import BuiltInCategory

        cat = element.Category
        if cat is None:
            return False
        return cat.Id.IntegerValue == int(BuiltInCategory.OST_StructuralFraming)
    except Exception:
        return False


def _group_has_chords(members):
    roles = set(_role_of(el) for el in members)
    return "TopChord" in roles and "BottomChord" in roles


def _filter_framing(elements):
    members = []
    for el in elements:
        if el is None or _excluded_from_cnc(el) or not _is_structural_framing(el):
            continue
        if not _curve_ends(el):
            continue
        if not _role_of(el):
            continue
        members.append(el)
    return members


def collect_cnc_units(doc):
    """Collect exportable units: assemblies and/or BIMSF_Container panels.

    Returns list of dicts: {name, members, source}
    """
    from Autodesk.Revit.DB import (
        AssemblyInstance,
        BuiltInCategory,
        FilteredElementCollector,
    )

    units = []
    seen_ids = set()

    # 1) Floor-truss (or panel) assemblies
    for asm in FilteredElementCollector(doc).OfClass(AssemblyInstance):
        raw = [doc.GetElement(eid) for eid in asm.GetMemberIds()]
        members = _filter_framing(raw)
        if not _group_has_chords(members):
            continue
        name = asm.Name or asm.AssemblyTypeName or "Truss"
        units.append({"name": name, "members": members, "source": "assembly"})
        for el in members:
            seen_ids.add(el.Id.IntegerValue)

    # 2) Wall panels / trusses by BIMSF_Container (not already in an assembly unit)
    by_container = defaultdict(list)
    framing = FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_StructuralFraming
    ).WhereElementIsNotElementType()
    for el in framing:
        if el.Id.IntegerValue in seen_ids:
            continue
        if _excluded_from_cnc(el) or not _curve_ends(el):
            continue
        container = _param_string(el, "BIMSF_Container")
        if not container:
            continue
        if not _role_of(el):
            continue
        by_container[container].append(el)

    for name, raw in sorted(by_container.items()):
        members = _filter_framing(raw)
        if not _group_has_chords(members):
            continue
        units.append({"name": name, "members": members, "source": "container"})

    units.sort(key=lambda u: u["name"])
    return units


# Back-compat alias used by older button code
def collect_truss_assemblies(doc):
    """Legacy: return [(None, name)]-incompatible; use collect_cnc_units."""
    return collect_cnc_units(doc)


def _pick_bottom(members):
    for el in members:
        if _role_of(el) == "BottomChord":
            return el
    best = None
    best_len = -1.0
    for el in members:
        ends = _curve_ends(el)
        if not ends:
            continue
        p0, p1, length = ends
        # prefer near-horizontal
        if abs(p1.Z - p0.Z) < 0.1 and length > best_len:
            best_len = length
            best = el
    return best


def _build_local_frame(members):
    """Return (origin_xyz as tuple, ux, uy) horizontal span basis in ft.

    localX = dot(pt - origin, (ux,uy,0)) * mm
    localY = pt.Z * mm
    """
    bc = _pick_bottom(members)
    if bc is None:
        raise ValueError("No bottom chord/track found for local frame")
    p0, p1, _ = _curve_ends(bc)
    vx = p1.X - p0.X
    vy = p1.Y - p0.Y
    horiz = math.sqrt(vx * vx + vy * vy)
    if horiz < 1e-9:
        # vertical-only oddity — fall back to world X
        ux, uy = 1.0, 0.0
        origin = p0
    else:
        ux, uy = vx / horiz, vy / horiz
        # Put local X = 0 at the end with smaller projection along span
        # so the bottom runs 0 → +length after normalize.
        t0 = p0.X * ux + p0.Y * uy
        t1 = p1.X * ux + p1.Y * uy
        origin = p0 if t0 <= t1 else p1
    return (origin.X, origin.Y, origin.Z), ux, uy


def _to_local(origin, ux, uy, pt):
    ox, oy, _oz = origin
    lx = ((pt.X - ox) * ux + (pt.Y - oy) * uy) * FT_TO_MM
    ly = pt.Z * FT_TO_MM
    return lx, ly


def _orient(role, rotation_rad, x0, y0, x1, y1):
    """NORMAL / INVERTED from cross-section rotation + diagonal lean."""
    rot = rotation_rad if rotation_rad is not None else 0.0
    inverted = rot < -1e-6
    dx = x1 - x0
    dy = y1 - y0
    is_diagonal = abs(dx) > 1.0 and abs(dy) > 1.0
    if is_diagonal and role == "Web":
        going_up = dy > 0
        lean_dx = dx if going_up else -dx
        inverted = lean_dx < 0
    elif role in ("BottomChord", "TopChord", "Nogging", "Support"):
        # ±180° and negative rotations → INVERTED
        inverted = abs(rot) > math.radians(45) and rot < 0
        # end studs often use 180° (pi) which is "inverted" facing
        if abs(abs(rot) - math.pi) < 0.1:
            inverted = True
        elif abs(rot) < 0.1:
            inverted = False
    return "INVERTED" if inverted else "NORMAL"


def _normalize_member_ends(role, x0, y0, x1, y1):
    """Horizontals: low X at start. Verticals/webs: low Y at start."""
    if role in _HORIZONTAL_ROLES:
        if x0 > x1 + _EPS:
            return x1, y1, x0, y0
        return x0, y0, x1, y1
    if y0 > y1 + _EPS:
        return x1, y1, x0, y0
    return x0, y0, x1, y1


def _seg_intersect(a0, a1, b0, b1):
    """2D segment intersection. Returns (x, y) or None."""
    x1, y1 = a0
    x2, y2 = a1
    x3, y3 = b0
    x4, y4 = b1
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < _EPS:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / den
    if -0.05 <= t <= 1.05 and -0.05 <= u <= 1.05:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def _near_vertical_hit(horizontal, vertical):
    """Hit between near-horizontal and near-vertical members by shared X."""
    if abs(vertical["x0"] - vertical["x1"]) > _JOINT_TOL_MM:
        return None
    if abs(horizontal["y0"] - horizontal["y1"]) > _JOINT_TOL_MM:
        return None
    sx = 0.5 * (vertical["x0"] + vertical["x1"])
    hy = 0.5 * (horizontal["y0"] + horizontal["y1"])
    xmin = min(horizontal["x0"], horizontal["x1"]) - _JOINT_TOL_MM
    xmax = max(horizontal["x0"], horizontal["x1"]) + _JOINT_TOL_MM
    ymin = min(vertical["y0"], vertical["y1"]) - _JOINT_TOL_MM
    ymax = max(vertical["y0"], vertical["y1"]) + _JOINT_TOL_MM
    if xmin <= sx <= xmax and ymin <= hy <= ymax:
        return (sx, hy)
    return None


def _station_along(x0, y0, x1, y1, px, py):
    dx = x1 - x0
    dy = y1 - y0
    length = math.sqrt(dx * dx + dy * dy)
    if length < _EPS:
        return 0.0
    t = ((px - x0) * dx + (py - y0) * dy) / (length * length)
    t = max(0.0, min(1.0, t))
    return t * length


def _chord_span_for_web(web_rec):
    xs = (web_rec["x0"], web_rec["x1"])
    return min(xs), max(xs)


def build_member_records(doc, members):
    """Build local-frame member dicts for a unit's framing list."""
    if not members:
        return []
    origin, ux, uy = _build_local_frame(members)
    records = []
    for el in members:
        ends = _curve_ends(el)
        p0, p1, _length_ft = ends
        lx0, ly0 = _to_local(origin, ux, uy, p0)
        lx1, ly1 = _to_local(origin, ux, uy, p1)
        role = _role_of(el)
        lx0, ly0, lx1, ly1 = _normalize_member_ends(role, lx0, ly0, lx1, ly1)
        length_mm = math.sqrt((lx1 - lx0) ** 2 + (ly1 - ly0) ** 2)
        rot = _param_double(el, "Cross-Section Rotation")
        records.append(
            {
                "element": el,
                "id": el.Id.IntegerValue,
                "label": _label_of(el),
                "section": _type_name(doc, el),
                "role": role,
                "orient": _orient(role, rot, lx0, ly0, lx1, ly1),
                "qty": 1,
                "length": length_mm,
                "x0": lx0,
                "y0": ly0,
                "x1": lx1,
                "y1": ly1,
                "depth": _web_depth_mm(doc, el),
                "ops": [],
            }
        )
    return records


def _find_joints(records):
    """Map member id → joint list for tooling."""
    horizontals = [r for r in records if r["role"] in _HORIZONTAL_ROLES]
    supports = [r for r in records if r["role"] == "Support"]
    webs = [r for r in records if r["role"] == "Web"]
    joints = {r["id"]: [] for r in records}

    for horiz in horizontals:
        a0 = (horiz["x0"], horiz["y0"])
        a1 = (horiz["x1"], horiz["y1"])
        for other in supports + webs:
            b0 = (other["x0"], other["y0"])
            b1 = (other["x1"], other["y1"])
            hit = _seg_intersect(a0, a1, b0, b1)
            if hit is None and other["role"] == "Support":
                hit = _near_vertical_hit(horiz, other)
            if hit is None:
                continue
            hx, hy = hit
            h_station = _station_along(
                horiz["x0"], horiz["y0"], horiz["x1"], horiz["y1"], hx, hy
            )
            lean_sign = 0
            if other["role"] == "Web":
                xmin, xmax = _chord_span_for_web(other)
                mid = 0.5 * (xmin + xmax)
                lean_sign = 1 if mid > hx else -1
            joints[horiz["id"]].append(
                {
                    "kind": other["role"],
                    "station": h_station,
                    "lean_sign": lean_sign,
                }
            )
            # studs get mid-height dimples at nogging crossings
            if other["role"] == "Support" and horiz["role"] == "Nogging":
                s_station = _station_along(
                    other["x0"], other["y0"], other["x1"], other["y1"], hx, hy
                )
                joints[other["id"]].append(
                    {
                        "kind": "Nogging",
                        "station": s_station,
                        "lean_sign": 0,
                    }
                )
    return joints


def build_operations(member, joints_for_member):
    """Return sorted list of (op_name, station_mm) for one member."""
    role = member["role"]
    L = member["length"]
    ops = []

    if role == "Support":
        ops.extend(
            [
                ("DIMPLE", SUPPORT_DIMPLE),
                ("SWAGE", SUPPORT_SWAGE),
                ("SWAGE", L - SUPPORT_SWAGE),
                ("DIMPLE", L - SUPPORT_DIMPLE),
            ]
        )
        for j in joints_for_member:
            if j["kind"] == "Nogging":
                ops.append(("DIMPLE", j["station"]))
    elif role == "Web":
        ops.extend(
            [
                ("END_TRUSS", 0.0),
                ("DIMPLE", WEB_DIMPLE),
                ("SWAGE", WEB_SWAGE_A),
                ("SWAGE", WEB_SWAGE_B),
                ("SWAGE", L - WEB_SWAGE_B),
                ("SWAGE", L - WEB_SWAGE_A),
                ("DIMPLE", L - WEB_DIMPLE),
                ("END_TRUSS", L),
            ]
        )
    elif role in _HORIZONTAL_ROLES:
        for j in joints_for_member:
            s = j["station"]
            if j["kind"] == "Support":
                ops.append(("LIP NOTCH", s))
                ops.append(("DIMPLE", s))
            elif j["kind"] == "Web" and role in ("BottomChord", "TopChord"):
                ops.append(("DIMPLE", s))
                sign = j["lean_sign"] or 1
                ops.append(("LIP NOTCH", s + sign * CHORD_LIP_NEAR))
                ops.append(("LIP NOTCH", s + sign * CHORD_LIP_FAR))

    _op_pri = {"END_TRUSS": 0, "LIP NOTCH": 1, "DIMPLE": 2, "SWAGE": 3}

    def _key(item):
        name, pos = item
        return (round(pos, 4), _op_pri.get(name, 9), name)

    ops.sort(key=_key)
    cleaned = []
    for name, pos in ops:
        if pos < -0.5 or pos > L + 0.5:
            continue
        cleaned.append((name, max(0.0, min(L, pos))))
    return cleaned


def _round2(v):
    return round(v + 1e-9, 2)


def format_component_row(rec):
    parts = [
        "COMPONENT",
        rec["label"],
        rec["section"],
        rec["role"],
        rec["orient"],
        str(int(rec.get("qty", 1))),
        "{:.2f}".format(_round2(rec["length"])),
        "{:.2f}".format(_round2(rec["x0"])),
        "{:.2f}".format(_round2(rec["y0"])),
        "{:.2f}".format(_round2(rec["x1"])),
        "{:.2f}".format(_round2(rec["y1"])),
        "{:.2f}".format(_round2(rec["depth"])),
    ]
    for name, pos in rec.get("ops") or []:
        parts.append(name)
        parts.append("{:.2f}".format(_round2(pos)))
    return ",".join(parts)


def format_csv(job_name, components):
    lines = ["DETAILS,,{}".format(job_name or "MULK Test")]
    ordered = sorted(
        components,
        key=lambda r: (
            _ROLE_ORDER.get(r["role"], 99),
            r.get("label", ""),
        ),
    )
    for rec in ordered:
        lines.append(format_component_row(rec))
    return "\n".join(lines) + "\n"


def suggest_filename(unit_name, section):
    base = (unit_name or "Panel").strip()
    # FT-1-0 → FT-1 (truss assembly suffix); keep LB3 / LB4 as-is
    if re.match(r"^FT-", base, re.IGNORECASE):
        base = re.sub(r"-\d+$", "", base)
    sec = (section or "section").strip()
    for ch in '<>:"/\\|?*':
        base = base.replace(ch, "_")
        sec = sec.replace(ch, "_")
    return "{}_{}.csv".format(base, sec)


def export_unit(doc, unit, job_name=None):
    """Return (filename, csv_text, component_count) for one CNC unit dict."""
    members = unit.get("members") or []
    records = build_member_records(doc, members)
    if not records:
        raise ValueError("No exportable framing in {}".format(unit.get("name")))
    joints = _find_joints(records)
    for rec in records:
        rec["ops"] = build_operations(rec, joints.get(rec["id"], []))
    section = ""
    for rec in records:
        if rec["section"]:
            section = rec["section"]
            break
    name = unit.get("name") or "Panel"
    fname = suggest_filename(name, section)
    text = format_csv(job_name or "MULK Test", records)
    return fname, text, len(records)


def export_assembly(doc, assembly, job_name=None):
    """Back-compat: export a Revit AssemblyInstance."""
    raw = [doc.GetElement(eid) for eid in assembly.GetMemberIds()]
    unit = {
        "name": assembly.Name or assembly.AssemblyTypeName or "Truss",
        "members": _filter_framing(raw),
        "source": "assembly",
    }
    return export_unit(doc, unit, job_name=job_name)
