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
