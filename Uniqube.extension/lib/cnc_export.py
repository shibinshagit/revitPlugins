# -*- coding: utf-8 -*-
"""Export structural framing to ONYX / roll-forming CNC CSV format."""
from __future__ import print_function

import os
import re

from pyrevit import DB

FT_TO_MM = 304.8
TOLERANCE_FT = 0.016

ROLE_PARAM_NAMES = (
    "Member Type",
    "Member Role",
    "Structural Usage",
    "BIMSF Member Type",
    "Role",
)

LABEL_PARAM_NAMES = (
    "Mark",
    "Label",
    "Member Label",
    "Member Name",
)

FEATURE_PARAM_NAMES = (
    "BIMSF_Data",
    "Framing Layout Data",
    "CNC Data",
    "Manufacturing Data",
)

ROLE_ALIASES = {
    "bottomchord": "BottomChord",
    "bottom chord": "BottomChord",
    "bc": "BottomChord",
    "topchord": "TopChord",
    "top chord": "TopChord",
    "tc": "TopChord",
    "web": "Web",
    "wb": "Web",
    "support": "Support",
    "stud": "Stud",
    "track": "Track",
    "header": "Header",
    "sill": "Sill",
}

ROLE_ABBREV = {
    "BottomChord": "BC",
    "TopChord": "TC",
    "Web": "WB",
    "Support": "SP",
    "Stud": "ST",
    "Track": "TR",
    "Header": "HD",
    "Sill": "SL",
    "Member": "MB",
}


def _round_to_tol(value):
    step = TOLERANCE_FT
    return round(value / step) * step


def _mm(value_ft):
    return round(value_ft * FT_TO_MM, 2)


def _get_param_str(elem, param_name):
    param = elem.LookupParameter(param_name)
    if param and param.HasValue:
        return param.AsString() or ""
    return ""


def _get_family_type_name(doc, elem):
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


def extract_profile(type_name):
    """Return profile code like 362S162-43(50) from a family type string."""
    if not type_name:
        return "UNKNOWN"
    match = re.search(r"(\d+S\d+-\d+(?:\(\d+\))?)", type_name, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"(\d+S\d+-\d+)", type_name, re.IGNORECASE)
    if match:
        return match.group(1)
    return type_name.replace(" ", "-")[:40]


def profile_gauge_depth(profile):
    """Web depth in mm derived from SSMA profile prefix (362 -> ~91.95 mm)."""
    match = re.match(r"(\d+)S", profile or "")
    if match:
        return round(int(match.group(1)) * 0.254, 2)
    return 91.95


def _normalize_role(text):
    if not text:
        return ""
    key = text.strip().lower().replace("_", " ").replace("-", " ")
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    for token, role in ROLE_ALIASES.items():
        if token in key:
            return role
    cleaned = re.sub(r"[^A-Za-z]", "", text)
    if cleaned:
        return cleaned[:1].upper() + cleaned[1:]
    return "Member"


def detect_member_role(doc, elem):
    """Resolve CNC member role from parameters or the family/type name."""
    for name in ROLE_PARAM_NAMES:
        value = _get_param_str(elem, name)
        if value:
            return _normalize_role(value)

    for source in (_get_family_type_name(doc, elem), _get_param_str(elem, "Type")):
        lower = (source or "").lower()
        if "bottom" in lower and "chord" in lower:
            return "BottomChord"
        if "top" in lower and "chord" in lower:
            return "TopChord"
        if "web" in lower:
            return "Web"
        if "support" in lower:
            return "Support"
        if "stud" in lower:
            return "Stud"
        if "track" in lower:
            return "Track"

    loc = elem.Location
    if isinstance(loc, DB.LocationCurve):
        curve = loc.Curve
        start = curve.GetEndPoint(0)
        end = curve.GetEndPoint(1)
        dz = abs(start.Z - end.Z)
        dx = abs(start.X - end.X)
        dy = abs(start.Y - end.Y)
        horiz = max(dx, dy)
        if dz < 0.05 and horiz > 0.05:
            avg_z = (start.Z + end.Z) / 2.0
            return "BottomChord" if avg_z < 1.0 else "TopChord"
        if horiz > 0.05 and dz > 0.05:
            return "Web"
        if dz > horiz:
            return "Support"
    return "Member"


def detect_orientation(elem):
    """Return NORMAL or INVERTED based on the member instance orientation."""
    if not isinstance(elem, DB.FamilyInstance):
        return "NORMAL"
    try:
        facing = elem.FacingOrientation
        hand = elem.HandOrientation
        up = facing.CrossProduct(hand)
        if up.Z < -0.1:
            return "INVERTED"
    except Exception:
        pass
    return "NORMAL"


def _collect_solids(geo_element):
    solids = []
    if geo_element is None:
        return solids
    for geom in geo_element:
        if isinstance(geom, DB.Solid):
            if geom.Volume > 0:
                solids.append(geom)
        elif isinstance(geom, DB.GeometryInstance):
            solids.extend(_collect_solids(geom.GetInstanceGeometry()))
    return solids


def _loop_points(curve_loop):
    points = []
    for curve in curve_loop:
        for point in curve.Tessellate():
            points.append(point)
    return points


def _centroid(points):
    count = len(points)
    sx = sum(p.X for p in points)
    sy = sum(p.Y for p in points)
    sz = sum(p.Z for p in points)
    return DB.XYZ(sx / count, sy / count, sz / count)


def _bbox_diag(points):
    xs = [p.X for p in points]
    ys = [p.Y for p in points]
    zs = [p.Z for p in points]
    mn = DB.XYZ(min(xs), min(ys), min(zs))
    mx = DB.XYZ(max(xs), max(ys), max(zs))
    return mn.DistanceTo(mx)


def get_dimple_positions_ft(elem):
    """Hole/punch distances along the member centerline, in feet."""
    loc = elem.Location
    if not isinstance(loc, DB.LocationCurve):
        return []
    line = loc.Curve
    start = line.GetEndPoint(0)
    end = line.GetEndPoint(1)
    length = start.DistanceTo(end)
    if length < 1e-6:
        return []
    direction = end.Subtract(start).Normalize()

    try:
        opts = DB.Options()
        opts.ComputeReferences = False
        opts.DetailLevel = DB.ViewDetailLevel.Fine
        solids = _collect_solids(elem.get_Geometry(opts))
    except Exception:
        return []

    positions = []
    for solid in solids:
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            try:
                loops = face.GetEdgesAsCurveLoops()
            except Exception:
                continue
            if loops.Count < 2:
                continue
            loop_data = []
            for loop in loops:
                pts = _loop_points(loop)
                if pts:
                    loop_data.append((_bbox_diag(pts), _centroid(pts)))
            if len(loop_data) < 2:
                continue
            loop_data.sort(key=lambda item: item[0], reverse=True)
            for _diag, center in loop_data[1:]:
                dist = center.Subtract(start).DotProduct(direction)
                positions.append(_round_to_tol(dist))

    if not positions:
        return []

    rounded_len = _round_to_tol(length)
    forward = sorted(positions)
    reverse = sorted(rounded_len - dist for dist in positions)
    return forward if forward <= reverse else reverse


def _parse_feature_tokens(text):
    """Parse feature pairs from BIMSF_Data / Framing Layout strings."""
    if not text:
        return []
    features = []
    pattern = re.compile(
        r"(LIP\s*NOTCH|LIPNOTCH|DIMPLE|SWAGE|END[_\s-]*TRUSS)"
        r"\s*[:=]?\s*([\d.]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        name = match.group(1).upper().replace("_", " ").replace("  ", " ")
        if "LIP" in name and "NOTCH" in name:
            name = "LIP NOTCH"
        elif "END" in name and "TRUSS" in name:
            name = "END_TRUSS"
        else:
            name = name.strip()
        try:
            pos = float(match.group(2))
        except ValueError:
            continue
        features.append((name, pos))

    if features:
        return features

    tokens = re.split(r"[;|\n\r]+", text)
    for token in tokens:
        parts = re.split(r"[:,=]", token.strip())
        if len(parts) != 2:
            continue
        name = _normalize_feature_name(parts[0])
        if not name:
            continue
        try:
            pos = float(parts[1].strip())
        except ValueError:
            continue
        features.append((name, pos))
    return features


def _normalize_feature_name(name):
    upper = (name or "").upper().replace("_", " ").strip()
    if "LIP" in upper and "NOTCH" in upper:
        return "LIP NOTCH"
    if upper == "DIMPLE":
        return "DIMPLE"
    if upper == "SWAGE":
        return "SWAGE"
    if "END" in upper and "TRUSS" in upper:
        return "END_TRUSS"
    return ""


def get_feature_pairs(doc, elem, length_mm):
    """Return ordered CNC feature pairs for one member."""
    features = []
    for param_name in FEATURE_PARAM_NAMES:
        raw = _get_param_str(elem, param_name)
        if raw:
            features.extend(_parse_feature_tokens(raw))

    if not features:
        for dist_ft in get_dimple_positions_ft(elem):
            features.append(("DIMPLE", _mm(dist_ft)))

    role = detect_member_role(doc, elem)
    if role in ("Web", "Support") and length_mm > 0:
        has_end = any(name == "END_TRUSS" for name, _pos in features)
        if not has_end:
            features.insert(0, ("END_TRUSS", 0.0))
            features.append(("END_TRUSS", length_mm))

    deduped = []
    seen = set()
    for name, pos in sorted(features, key=lambda item: (item[1], item[0])):
        key = (name, round(pos, 2))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, round(pos, 2)))
    return deduped


def _member_label(doc, elem, role, counters):
    for name in LABEL_PARAM_NAMES:
        value = _get_param_str(elem, name)
        if value:
            return value.strip()
    counters[role] = counters.get(role, 0) + 1
    abbrev = ROLE_ABBREV.get(role, "MB")
    return "{}{}".format(abbrev, counters[role])


def _truss_origin(members):
    """Origin for truss-local CNC coordinates (min X/Y of all endpoints)."""
    points = []
    for elem in members:
        loc = elem.Location
        if isinstance(loc, DB.LocationCurve):
            points.append(loc.Curve.GetEndPoint(0))
            points.append(loc.Curve.GetEndPoint(1))
    if not points:
        return DB.XYZ.Zero
    return DB.XYZ(min(p.X for p in points), min(p.Y for p in points), 0.0)


def build_component_rows(doc, members):
    """Build CNC component dicts for a panel or truss assembly."""
    origin = _truss_origin(members)
    counters = {}
    rows = []

    def sort_key(elem):
        role = detect_member_role(doc, elem)
        order = {
            "BottomChord": 0,
            "TopChord": 1,
            "Web": 2,
            "Support": 3,
        }.get(role, 9)
        loc = elem.Location
        start_x = loc.Curve.GetEndPoint(0).X if isinstance(loc, DB.LocationCurve) else 0.0
        return (order, start_x)

    for elem in sorted(members, key=sort_key):
        loc = elem.Location
        if not isinstance(loc, DB.LocationCurve):
            continue

        curve = loc.Curve
        start = curve.GetEndPoint(0)
        end = curve.GetEndPoint(1)
        length_mm = _mm(curve.Length)
        if length_mm <= 0:
            continue

        type_name = _get_family_type_name(doc, elem)
        profile = extract_profile(type_name)
        role = detect_member_role(doc, elem)
        orientation = detect_orientation(elem)
        label = _member_label(doc, elem, role, counters)
        gauge = profile_gauge_depth(profile)
        features = get_feature_pairs(doc, elem, length_mm)

        rows.append(
            {
                "label": label,
                "profile": profile,
                "role": role,
                "orientation": orientation,
                "length_mm": length_mm,
                "start_x": _mm(start.X - origin.X),
                "start_y": _mm(start.Y - origin.Y),
                "end_x": _mm(end.X - origin.X),
                "end_y": _mm(end.Y - origin.Y),
                "gauge": gauge,
                "features": features,
            }
        )
    return rows


def format_component_row(row):
    fields = [
        "COMPONENT",
        row["label"],
        row["profile"],
        row["role"],
        row["orientation"],
        "1",
        "{:.2f}".format(row["length_mm"]),
        "{:.2f}".format(row["start_x"]),
        "{:.2f}".format(row["start_y"]),
        "{:.2f}".format(row["end_x"]),
        "{:.2f}".format(row["end_y"]),
        "{:.2f}".format(row["gauge"]),
    ]
    for name, pos in row["features"]:
        fields.append(name)
        fields.append("{:.2f}".format(pos))
    return ",".join(fields)


def build_csv_lines(doc, members, job_name):
    rows = build_component_rows(doc, members)
    if not rows:
        return [], rows

    profile = rows[0]["profile"]
    lines = ["DETAILS,," + (job_name or "UNIQUBE Export")]
    for row in rows:
        lines.append(format_component_row(row))
    return lines, rows


def default_csv_name(assembly_name, profile):
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", assembly_name or "Export")
    safe_profile = re.sub(r'[\\/:*?"<>|]', "_", profile or "Profile")
    return "{}_{}.csv".format(safe_name, safe_profile)


def write_csv(path, lines):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


def collect_export_groups(doc):
    """Return {label: [framing elements]} for wall panels and truss assemblies."""
    import panel_utils as pu

    groups = pu.map_framing(doc)

    assemblies = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    truss_members = {}
    truss_counts = {}
    for asm in assemblies:
        container_param = asm.LookupParameter(pu.PARAM_NAME)
        if not (container_param and container_param.HasValue and container_param.AsString()):
            continue

        members = []
        for member_id in asm.GetMemberIds():
            member = doc.GetElement(member_id)
            if member is None:
                continue
            cat = member.Category
            if cat is None:
                continue
            try:
                is_framing = cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
            except Exception:
                is_framing = False
            if not is_framing:
                continue
            member_container = member.LookupParameter(pu.PARAM_NAME)
            if member_container and member_container.HasValue and member_container.AsString():
                continue
            members.append(member)
        if not members:
            continue

        try:
            name = asm.Name
        except Exception:
            name = container_param.AsString()
        if not name:
            continue

        truss_counts[name] = truss_counts.get(name, 0) + 1
        bucket = truss_members.setdefault(name, [])
        seen = set(elem.Id.IntegerValue for elem in bucket)
        for member in members:
            if member.Id.IntegerValue not in seen:
                bucket.append(member)
                seen.add(member.Id.IntegerValue)

    for name, members in truss_members.items():
        label = "{} [ count {} ]".format(name, truss_counts[name])
        groups[label] = members

    return groups
