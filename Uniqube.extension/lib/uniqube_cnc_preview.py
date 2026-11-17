# -*- coding: utf-8 -*-
"""Read a CNC program back from its CSV text, for the Generate CNC preview.

The preview window draws and tabulates the exact text that is written to
disk, not the exporter's internal records, so what is checked on screen is
what the rollformer receives.

No Revit or WPF imports: this runs under IronPython 2.7 and CPython.
"""
from __future__ import print_function

import math
import re

DEFAULT_FLANGE_MM = 41.15
_FIXED_FIELDS = 12
_LENGTH_TOL_MM = 1.5
_STATION_TOL_MM = 0.01

COVER_ROLES = ("TBOT", "TTOP", "BOTTOMCHORD", "TOPCHORD")
ORIENTATIONS = ("NORMAL", "INVERTED")

# Display order for tooling totals; anything else follows alphabetically.
OP_ORDER = (
    "DIMPLE",
    "SWAGE",
    "LIP NOTCH",
    "WEB NOTCH",
    "SERVICE HOLE",
    "WEB HOLE",
    "END_TRUSS",
)

ERROR = "error"
WARNING = "warning"

_SECTION_RE = re.compile(r"^\s*\d{3,4}\s*[A-Za-z]+\s*(\d{3})\s*-")
_STRAP_RE = re.compile(r"^\s*FS\s*(\d+(?:\.\d+)?)\s*[xX]")


def fmt(value):
    """Two decimals, the way the CSV writes numbers."""
    text = "{:.2f}".format(round(value + 1e-9, 2))
    return "0.00" if text == "-0.00" else text


def fmt_overall(value):
    """One decimal, for sizes worked out from the rows.

    End points are written to 0.01 mm, so an outside size built from two of
    them plus the flange width is only good to about 0.01 mm either way.
    """
    text = "{:.1f}".format(round(value + 1e-9, 1))
    return "0.0" if text == "-0.0" else text


def flange_width_mm(section):
    """Width of the member as seen in elevation, from its section name.

    SSMA C sections show their flange: 600S162-54(50) -> 1.62 in.
    BIMSF flat straps show their full width: FS100x1.5 -> 100 mm.

    Returns (width_mm, assumed). The CSV carries no such width, so the
    drawing takes it from the section name and says so when it cannot.
    """
    match = _SECTION_RE.match(section or "")
    if match:
        return int(match.group(1)) / 100.0 * 25.4, False
    strap = _STRAP_RE.match(section or "")
    if strap:
        return float(strap.group(1)), False
    return DEFAULT_FLANGE_MM, True


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _problem(level, text, component=None, row=None):
    return {"level": level, "text": text, "component": component, "row": row}


def parse_program(text):
    """Parse CNC CSV text into {job, components, problems}.

    Every value is kept twice: as the literal CSV string (shown in the
    tables) and as a float (used for the drawing and the checks).
    """
    job = ""
    components = []
    problems = []

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for row, line in enumerate(lines, 1):
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split(",")]
        kind = fields[0].upper()

        if kind == "DETAILS":
            filled = [f for f in fields[1:] if f]
            job = filled[-1] if filled else ""
            continue
        if kind != "COMPONENT":
            problems.append(
                _problem(
                    ERROR,
                    u"Row {}: unknown row type '{}'".format(row, fields[0]),
                    row=row,
                )
            )
            continue

        label = fields[1] if len(fields) > 1 and fields[1] else u"(row {})".format(row)
        if len(fields) < _FIXED_FIELDS:
            problems.append(
                _problem(
                    ERROR,
                    u"{}: only {} of {} fixed fields".format(
                        label, len(fields), _FIXED_FIELDS
                    ),
                    row=row,
                )
            )
            continue

        numbers = [_number(f) for f in fields[6:12]]
        if any(n is None for n in numbers):
            problems.append(
                _problem(
                    ERROR,
                    u"{}: length / coordinates / web depth are not all numbers".format(
                        label
                    ),
                    row=row,
                )
            )
            continue
        length, x0, y0, x1, y1, depth = numbers

        index = len(components)
        dx, dy = x1 - x0, y1 - y0
        span = math.hypot(dx, dy)
        ux, uy = (dx / span, dy / span) if span > 1e-6 else (1.0, 0.0)
        orient = fields[4].upper()
        flange, flange_assumed = flange_width_mm(fields[2])

        ops = []
        for col in range(_FIXED_FIELDS, len(fields), 2):
            name = fields[col].upper()
            station_text = fields[col + 1] if col + 1 < len(fields) else ""
            if not name and not station_text:
                continue
            station = _number(station_text)
            if not name:
                problems.append(
                    _problem(
                        ERROR,
                        u"{}: station {} has no operation name".format(
                            label, station_text
                        ),
                        index,
                        row,
                    )
                )
                continue
            if station is None:
                problems.append(
                    _problem(
                        ERROR,
                        u"{}: {} has no valid station".format(label, name),
                        index,
                        row,
                    )
                )
                continue
            ops.append({"name": name, "station": station, "text": station_text})

        comp = {
            "index": index,
            "row": row,
            "raw": line,
            "label": label,
            "section": fields[2],
            "role": fields[3],
            "orient": orient,
            "qty": fields[5],
            "length": length,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "depth": depth,
            "text": {
                "length": fields[6],
                "x0": fields[7],
                "y0": fields[8],
                "x1": fields[9],
                "y1": fields[10],
                "depth": fields[11],
            },
            "ops": ops,
            "span": span,
            "ux": ux,
            "uy": uy,
            # Left-hand normal of the member direction.
            "nx": -uy,
            "ny": ux,
            "open_sign": -1 if orient == "INVERTED" else 1,
            "flange": flange,
            "flange_assumed": flange_assumed,
        }
        components.append(comp)
        _check_component(comp, problems)

    _check_labels(components, problems)
    if not components:
        problems.append(_problem(ERROR, u"No COMPONENT rows in the program"))
    return {"job": job, "components": components, "problems": problems}


def _check_component(comp, problems):
    label, index, row = comp["label"], comp["index"], comp["row"]

    def add(level, text):
        problems.append(_problem(level, text, index, row))

    if comp["orient"] not in ORIENTATIONS:
        add(ERROR, u"{}: orientation '{}' is not NORMAL or INVERTED".format(
            label, comp["orient"]))
    qty = _number(comp["qty"])
    if qty is None or qty <= 0 or qty != int(qty):
        add(ERROR, u"{}: quantity '{}' is not a positive whole number".format(
            label, comp["qty"]))
    if comp["length"] <= 0 or comp["span"] <= 1e-6:
        add(ERROR, u"{}: length and end points must be greater than zero".format(
            label))
    elif abs(comp["span"] - comp["length"]) > _LENGTH_TOL_MM:
        add(WARNING, u"{}: length {} but end points are {} apart".format(
            label, fmt(comp["length"]), fmt(comp["span"])))
    if comp["flange_assumed"]:
        add(WARNING, u"{}: section '{}' not recognised, drawn {} mm wide".format(
            label, comp["section"], fmt(DEFAULT_FLANGE_MM)))

    previous = None
    ordered = True
    for op in comp["ops"]:
        station = op["station"]
        if station < -_STATION_TOL_MM or station > comp["length"] + _STATION_TOL_MM:
            add(ERROR, u"{}: {} at {} is outside 0 - {}".format(
                label, op["name"], op["text"], fmt(comp["length"])))
        if previous is not None and station < previous - _STATION_TOL_MM:
            ordered = False
        previous = station
    if not ordered:
        add(WARNING, u"{}: operations are not in station order".format(label))


def _check_labels(components, problems):
    seen = {}
    for comp in components:
        first = seen.get(comp["label"])
        if first is None:
            seen[comp["label"]] = comp
            continue
        problems.append(
            _problem(
                WARNING,
                u"{}: label used on rows {} and {}".format(
                    comp["label"], first["row"], comp["row"]
                ),
                comp["index"],
                comp["row"],
            )
        )


# --- geometry ---------------------------------------------------------------
def point_at(comp, station):
    """Model point on the member centreline at a station from its start."""
    return (
        comp["x0"] + comp["ux"] * station,
        comp["y0"] + comp["uy"] * station,
    )


def offset_point(comp, station, offset):
    """Point at a station, moved sideways. Positive offset is the lip side."""
    px, py = point_at(comp, station)
    side = offset * comp["open_sign"]
    return px + comp["nx"] * side, py + comp["ny"] * side


def outline(comp):
    """The member as seen in elevation: end points x flange width."""
    half = comp["flange"] / 2.0
    ox, oy = comp["nx"] * half, comp["ny"] * half
    return [
        (comp["x0"] + ox, comp["y0"] + oy),
        (comp["x1"] + ox, comp["y1"] + oy),
        (comp["x1"] - ox, comp["y1"] - oy),
        (comp["x0"] - ox, comp["y0"] - oy),
    ]


def web_edge(comp):
    """The long edge on the web (closed) side of the C section."""
    half = comp["flange"] / 2.0
    return offset_point(comp, 0.0, -half), offset_point(comp, comp["span"], -half)


LIP_RETURN_MM = 12.7
# A chord's lip notches for a sloping web sit this far past the joint
# dimple, toward the web (Vertex BD truss files).
TRUSS_LIP_OFFSETS = (19.13, 36.58)
# Notches are drawn this much wider each side than the member they seat.
NOTCH_CLEARANCE_MM = 2.0


def _lean(station, dimples):
    """+1 / -1 when a lip notch belongs to a sloping web joint, else 0."""
    for dimple in dimples:
        for offset in TRUSS_LIP_OFFSETS:
            if abs(station - dimple - offset) < 0.1:
                return 1
            if abs(dimple - station - offset) < 0.1:
                return -1
    return 0


def _band(edge, t0, t1, depth, op_index, name, span):
    t0, t1 = max(0.0, t0), min(span, t1)
    if t1 - t0 < 1e-6 or depth <= 0.0:
        return None
    return {"edge": edge, "t0": t0, "t1": t1, "depth": depth,
            "op_index": op_index, "name": name, "tilt": 0.0}


NOTCHES = ("LIP NOTCH", "WEB NOTCH")


def _inward(comp, edge):
    """Unit vector from a long edge toward the member centreline."""
    side = comp["open_sign"] * (1.0 if edge == "lip" else -1.0)
    return -comp["nx"] * side, -comp["ny"] * side


def _tilt(comp, band, others):
    """Along-member shift per mm inward, so a notch's sides follow the
    member seated in it. 0 when that member is square to this one."""
    half = comp["flange"] / 2.0
    sign = 1.0 if band["edge"] == "lip" else -1.0
    mid = (band["t0"] + band["t1"]) / 2.0
    px, py = offset_point(comp, mid, sign * (half - band["depth"] / 2.0))
    mx, my = _inward(comp, band["edge"])
    best, best_across = None, None
    for other in others:
        if other["index"] == comp["index"] or other["span"] <= 0.0:
            continue
        # Members alongside this one (sliders, doubled chords) seat nothing.
        if abs(other["ux"] * mx + other["uy"] * my) < 0.2:
            continue
        dx, dy = px - other["x0"], py - other["y0"]
        along = dx * other["ux"] + dy * other["uy"]
        if along < -comp["flange"] or along > other["span"] + comp["flange"]:
            continue
        across = abs(dx * other["nx"] + dy * other["ny"])
        if across > (other["flange"] + comp["flange"]) / 2.0:
            continue
        if best_across is None or across < best_across:
            best, best_across = other, across
    if best is None:
        return 0.0
    cu = best["ux"] * comp["ux"] + best["uy"] * comp["uy"]
    cm = best["ux"] * mx + best["uy"] * my
    return max(-3.0, min(3.0, cu / cm))


def cuts(comp, min_inset=0.0, others=None):
    """Bands of each long edge that the tooling cuts away or presses in.

    Each band is {edge: "lip"|"web", t0, t1, depth, tilt, op_index, name}
    in mm along and across the member; t0..t1 is measured half way into
    the cut. Notches are one flange wide plus NOTCH_CLEARANCE_MM each
    side, centred on the station, except a
    sloping web's pair on a truss chord: each of those runs from its
    station back toward the joint dimple, which is where the web crosses
    the lips. With ``others`` (the rest of the program) a notch's sides
    lean with the member seated in it. A swage near an end runs out to
    that end as a single joggle step. ``min_inset`` keeps shallow swages
    visible when zoomed out.
    """
    span = comp["span"]
    flange = comp["flange"]
    limit = flange * 0.45
    notch_depth = min(LIP_RETURN_MM, flange * 0.35)
    swage_depth = min(limit, max(flange * 0.07, min_inset))
    swage_band = flange * 0.6
    dimples = [op["station"] for op in comp["ops"] if op["name"] == "DIMPLE"]
    out = []
    for index, op in enumerate(comp["ops"]):
        name = op["name"]
        s = max(0.0, min(span, op["station"]))
        if name in ("LIP NOTCH", "WEB NOTCH"):
            edge = "lip" if name == "LIP NOTCH" else "web"
            lean = _lean(op["station"], dimples) if name == "LIP NOTCH" else 0
            if lean > 0:
                t0, t1 = s - flange, s
            elif lean < 0:
                t0, t1 = s, s + flange
            else:
                t0, t1 = s - flange / 2.0, s + flange / 2.0
            out.append(_band(edge, t0, t1, notch_depth, index, name, span))
        elif name == "SWAGE":
            if s <= flange:
                t0, t1 = 0.0, s + swage_band / 2.0
            elif span - s <= flange:
                t0, t1 = s - swage_band / 2.0, span
            else:
                t0, t1 = s - swage_band / 2.0, s + swage_band / 2.0
            for edge in ("lip", "web"):
                out.append(_band(edge, t0, t1, swage_depth, index, name, span))
    out = [band for band in out if band is not None]
    for band in out:
        if band["name"] not in NOTCHES:
            continue
        if others:
            band["tilt"] = _tilt(comp, band, others)
        # Clearance square to the seated member's side, whatever its lean.
        gap = NOTCH_CLEARANCE_MM * math.sqrt(1.0 + band["tilt"] ** 2)
        band["t0"] = max(0.0, band["t0"] - gap)
        band["t1"] = min(span, band["t1"] + gap)
    return [b for b in out if b["name"] not in NOTCHES] + _merge_notches(out)


def _merge_notches(bands):
    """Overlapping notches on one edge become one cut, each end leaning
    with the member seated there, so no sliver is left between them."""
    merged = []
    for edge in ("lip", "web"):
        notches = sorted(
            (b for b in bands if b["name"] in NOTCHES and b["edge"] == edge),
            key=lambda b: b["t0"],
        )
        for band in notches:
            last = merged[-1] if merged and merged[-1]["edge"] == edge else None
            if last is not None and band["t0"] <= last["t1"] + 0.5:
                if band["t1"] > last["t1"]:
                    last["t1"], last["tilt1"] = band["t1"], band["tilt"]
                last["depth"] = max(last["depth"], band["depth"])
                last["ops"].append(band["op_index"])
                continue
            band = dict(band, tilt0=band["tilt"], tilt1=band["tilt"],
                        ops=[band["op_index"]])
            merged.append(band)
    return merged


def _edge_points(comp, bands, sign):
    span = comp["span"]
    half = comp["flange"] / 2.0
    stops = sorted(set([0.0, span] + [b["t0"] for b in bands] + [b["t1"] for b in bands]))
    points = []
    for a, b in zip(stops, stops[1:]):
        if b - a < 1e-9:
            continue
        mid = (a + b) / 2.0
        depth = max([band["depth"] for band in bands
                     if band["t0"] <= mid <= band["t1"]] or [0.0])
        for t in (a, b):
            p = offset_point(comp, t, sign * (half - depth))
            if not points or abs(points[-1][0] - p[0]) > 1e-9 \
                    or abs(points[-1][1] - p[1]) > 1e-9:
                points.append(p)
    return points


def profile(comp, bands):
    """(outline polygon, web edge polyline) with the swages stepped in.

    Notches are left out: they are cut from this outline with
    ``band_polygon`` so their sides can lean.
    """
    pressed = [b for b in bands if b["name"] not in NOTCHES]
    lip = _edge_points(comp, [b for b in pressed if b["edge"] == "lip"], 1.0)
    web = _edge_points(comp, [b for b in pressed if b["edge"] == "web"], -1.0)
    return lip + list(reversed(web)), web


def band_polygon(comp, band):
    """The four corners of the material a band removes (outer edge first)."""
    half = comp["flange"] / 2.0
    sign = 1.0 if band["edge"] == "lip" else -1.0
    outer, inner = sign * half, sign * (half - band["depth"])
    tilt = band.get("tilt", 0.0)
    shift0 = band.get("tilt0", tilt) * band["depth"] / 2.0
    shift1 = band.get("tilt1", tilt) * band["depth"] / 2.0
    return [
        offset_point(comp, band["t0"] - shift0, outer),
        offset_point(comp, band["t1"] - shift1, outer),
        offset_point(comp, band["t1"] + shift1, inner),
        offset_point(comp, band["t0"] + shift0, inner),
    ]


def bounds(components):
    """(min_x, min_y, max_x, max_y) of every member outline, or None."""
    xs, ys = [], []
    for comp in components:
        for px, py in outline(comp):
            xs.append(px)
            ys.append(py)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def overall_bounds(components):
    """Bounds of the panel or truss itself, for its overall dimensions.

    Taken from the tracks / chords when there are any: a diagonal strap
    brace drawn at its full width pokes past the corners and would
    otherwise be measured as part of the panel.
    """
    frame = [c for c in components if c["role"].upper() in COVER_ROLES]
    return bounds(frame or components)


def draw_layer(comp):
    """Paint order: 0 studs and webs, 1 horizontal infill, 2 tracks / chords.

    Tracks and chords wrap the members that butt into them, so they are
    painted last and hide the stud ends, as in a fabrication elevation.
    """
    if comp["role"].upper() in COVER_ROLES:
        return 2
    return 1 if abs(comp["uy"]) < 0.2 else 0


def distance_to(comp, px, py):
    """Distance from a model point to the member centreline segment."""
    dx, dy = comp["x1"] - comp["x0"], comp["y1"] - comp["y0"]
    squared = dx * dx + dy * dy
    if squared < 1e-12:
        return math.hypot(px - comp["x0"], py - comp["y0"])
    t = ((px - comp["x0"]) * dx + (py - comp["y0"]) * dy) / squared
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (comp["x0"] + dx * t), py - (comp["y0"] + dy * t))


def hit_test(components, px, py, slack=0.0):
    """Index of the member under a model point, or None.

    The topmost painted member wins, so what is clicked is what is seen.
    """
    best = None
    best_key = None
    for comp in components:
        distance = distance_to(comp, px, py)
        if distance > comp["flange"] / 2.0 + slack:
            continue
        key = (-draw_layer(comp), distance)
        if best_key is None or key < best_key:
            best, best_key = comp["index"], key
    return best


# --- totals -----------------------------------------------------------------
def op_totals(components):
    """[(operation, count)] in display order."""
    counts = {}
    for comp in components:
        for op in comp["ops"]:
            counts[op["name"]] = counts.get(op["name"], 0) + 1
    known = [(name, counts[name]) for name in OP_ORDER if name in counts]
    extra = sorted((n, c) for n, c in counts.items() if n not in OP_ORDER)
    return known + extra


def summarize(program):
    components = program["components"]
    box = overall_bounds(components)
    totals = op_totals(components)
    return {
        "components": len(components),
        "cut_length": sum(c["length"] for c in components),
        "op_total": sum(count for _name, count in totals),
        "ops": totals,
        "extents": (box[2] - box[0], box[3] - box[1]) if box else (0.0, 0.0),
        "errors": sum(1 for p in program["problems"] if p["level"] == ERROR),
        "warnings": sum(1 for p in program["problems"] if p["level"] == WARNING),
    }


def component_flags(program):
    """{component index: 'error' | 'warning'} for table highlighting."""
    flags = {}
    for problem in program["problems"]:
        index = problem["component"]
        if index is None:
            continue
        if problem["level"] == ERROR or index not in flags:
            flags[index] = problem["level"]
    return flags
