# -*- coding: utf-8 -*-
"""Checks for uniqube_cnc_preview against the two production CSV files.

    python tools/cnc_preview_tests/test_preview.py

Plain Python, no Revit: the module under test is what the Generate CNC
window uses to read a program back before it is written.
"""
from __future__ import print_function

import io
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "Uniqube.extension", "lib"))

import uniqube_cnc_preview as preview  # noqa: E402

SAMPLES = os.path.join(
    os.path.dirname(REPO), "Models added on revit", "For CNC Script on Revit"
)
ELB = os.path.join(SAMPLES, "ELB-1_600S162-54(50)_.csv")
RT = os.path.join(SAMPLES, "RT-1_362S162-54(50)_.csv")
BIG5 = os.path.join(HERE, "fixtures", "BIG5_362S162-54(50)_.csv")

FAILURES = []


def check(label, condition, detail=""):
    print("  {}  {}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)
        if detail != "":
            print("        -> {}".format(detail))


def read(path):
    with io.open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def rebuild(program):
    """Write the parsed program back out; must reproduce the file."""
    rows = ["DETAILS,,{}".format(program["job"])]
    for comp in program["components"]:
        text = comp["text"]
        fields = [
            "COMPONENT", comp["label"], comp["section"], comp["role"],
            comp["orient"], comp["qty"], text["length"], text["x0"],
            text["y0"], text["x1"], text["y1"], text["depth"],
        ]
        for op in comp["ops"]:
            fields.extend([op["name"], op["text"]])
        rows.append(",".join(fields))
    return "\r\n".join(rows) + "\r\n"


def production_files():
    print("production files")
    for path, components, operations, totals in (
        (ELB, 7, 59, {"DIMPLE": 24, "SWAGE": 12, "LIP NOTCH": 12,
                      "WEB NOTCH": 2, "SERVICE HOLE": 9}),
        (RT, 47, 450, {"LIP NOTCH": 123, "DIMPLE": 165, "END_TRUSS": 40,
                       "SWAGE": 122}),
    ):
        name = os.path.basename(path)
        text = read(path)
        program = preview.parse_program(text)
        summary = preview.summarize(program)
        check(name + ": components", summary["components"] == components,
              summary["components"])
        check(name + ": operations", summary["op_total"] == operations,
              summary["op_total"])
        check(name + ": totals per operation", dict(summary["ops"]) == totals,
              summary["ops"])
        check(name + ": no errors or warnings",
              (summary["errors"], summary["warnings"]) == (0, 0),
              program["problems"])
        check(name + ": nothing lost or altered reading it back",
              rebuild(program) == text)
        check(name + ": job name", program["job"] == "ZUHA-ADMIN BUILDING",
              program["job"])


def geometry():
    print("geometry")
    program = preview.parse_program(read(ELB))
    by_label = dict((c["label"], c) for c in program["components"])
    check("flange width from 600S162", abs(preview.flange_width_mm("600S162-54(50)")[0] - 41.148) < 1e-9)
    check("flat strap shows its full width", preview.flange_width_mm("FS100x1.5") == (100.0, False),
          preview.flange_width_mm("FS100x1.5"))
    check("unknown section is flagged as assumed", preview.flange_width_mm("C150")[1])

    box = preview.bounds(program["components"])
    check("panel outline starts at 0,0", abs(box[0]) < 0.01 and abs(box[1]) < 0.01, box)
    check("panel is 1060.85 wide", abs(box[2] - 1060.85) < 0.01, box)
    check("panel is 3600 high", abs(box[3] - 3600.0) < 0.01, box)

    # A 100 mm strap brace corner to corner must not widen the panel size.
    braced = preview.parse_program(
        read(ELB) + "COMPONENT,ELB1-CB1,FS100x1.5,CB,NORMAL,1,3750.29,"
        "0.00,1.44,1060.85,3597.00,100.00\r\n"
    )
    check("strap brace is drawn past the corners",
          preview.bounds(braced["components"])[0] < -10.0)
    check("but the overall size is still the panel",
          preview.summarize(braced)["extents"] == preview.summarize(program)["extents"],
          preview.summarize(braced)["extents"])

    # Bottom track NORMAL: web on the floor, lips up. Top track is the mirror.
    tbot, ttop = by_label["ELB1-TBOT1"], by_label["ELB1-TTOP1"]
    check("bottom track web edge is its underside",
          abs(preview.web_edge(tbot)[0][1] - 0.0) < 0.01, preview.web_edge(tbot))
    check("top track web edge is its top",
          abs(preview.web_edge(ttop)[0][1] - 3600.0) < 0.01, preview.web_edge(ttop))
    # End studs turn their webs outward.
    check("left end stud web faces out",
          preview.web_edge(by_label["ELB1-E1"])[0][0] < 20.57)
    check("right end stud web faces out",
          preview.web_edge(by_label["ELB1-E2"])[0][0] > 1040.27)

    stud = by_label["ELB1-S1"]
    x, y = preview.point_at(stud, 1198.56)
    check("station runs from the first end point", (round(x, 2), round(y, 2)) == (536.0, 1200.0), (x, y))
    check("that station lands on the noggin centreline",
          abs(y - by_label["ELB1-HB1"]["y0"]) < 0.01)

    check("tracks are painted over stud ends",
          preview.draw_layer(tbot) > preview.draw_layer(stud))

    # BIG5 calibration panel: diagonal braces modelled with a TTOP label.
    braced = preview.parse_program(
        read(ELB) + "COMPONENT,ELB1-TTOP3,600S162-54(50),TTOP,INVERTED,1,912.00,"
        "8.59,622.85,707.66,1208.56,152.40,DIMPLE,15.63\r\n"
    )
    brace = [c for c in braced["components"] if c["label"] == "ELB1-TTOP3"][0]
    check("a diagonal labelled TTOP is not drawn as a track",
          not preview.is_cover(brace) and preview.is_cover(tbot))
    check("studs are painted over the ends of braces",
          preview.draw_layer(brace) < preview.draw_layer(stud))
    check("a diagonal TTOP does not change the panel size",
          preview.summarize(braced)["extents"] == preview.summarize(program)["extents"],
          preview.summarize(braced)["extents"])
    truss = preview.parse_program(read(RT))
    check("sloping truss chords still cover the webs",
          all(preview.is_cover(c) for c in truss["components"]
              if c["role"] in ("TopChord", "BottomChord")))
    check("click on a stud selects it", preview.hit_test(program["components"], 536.0, 1800.0) == stud["index"])
    check("click where a stud runs into the track selects the track",
          preview.hit_test(program["components"], 536.0, 10.0) == tbot["index"])
    check("click on empty space selects nothing",
          preview.hit_test(program["components"], 280.0, 1800.0) is None)


def profiles():
    print("tooling drawn into the outline")
    program = preview.parse_program(read(ELB))
    by_label = dict((c["label"], c) for c in program["components"])
    tbot, stud = by_label["ELB1-TBOT1"], by_label["ELB1-S1"]
    flange = tbot["flange"]

    plain = dict(tbot, ops=[])
    polygon, web = preview.profile(plain, preview.cuts(plain))
    check("no tooling: a plain rectangle", len(polygon) == 4 and len(web) == 2,
          polygon)

    bands = preview.cuts(tbot)
    lips = [b for b in bands if b["name"] == "LIP NOTCH"]
    check("every lip notch cuts the lip edge",
          len(lips) == sum(1 for op in tbot["ops"] if op["name"] == "LIP NOTCH")
          and all(b["edge"] == "lip" for b in lips))
    notch = preview.band_polygon(tbot, lips[1])
    ys = sorted(p[1] for p in notch)
    check("bottom track notch is cut down from the lips",
          all(abs(y - (flange - lips[1]["depth"])) < 0.01 for y in ys[:2])
          and all(abs(y - flange) < 0.01 for y in ys[2:]), notch)
    _polygon, web = preview.profile(tbot, bands)
    check("bottom track underside stays straight",
          all(abs(p[1]) < 0.01 for p in web), web)
    seated = [b for b in preview.cuts(tbot, others=program["components"])
              if b["name"] == "LIP NOTCH" and b["t0"] < 536.0 < b["t1"]][0]
    check("a square stud's notch is the stud plus clearance",
          abs(seated["t0"] - (536.0 - flange / 2.0 - preview.NOTCH_CLEARANCE_MM)) < 0.05
          and abs(seated["t1"] - (536.0 + flange / 2.0 + preview.NOTCH_CLEARANCE_MM)) < 0.05,
          (seated["t0"], seated["t1"]))

    swages = [b for b in preview.cuts(stud) if b["name"] == "SWAGE"]
    first = min(swages, key=lambda b: b["t0"])
    check("swage near the end runs out to the end as one joggle",
          first["t0"] == 0.0 and first["t1"] < flange, first)
    check("swage presses both edges",
          set(b["edge"] for b in swages) == set(["lip", "web"]))
    box = preview.bounds([stud])
    polygon, _web = preview.profile(stud, preview.cuts(stud))
    check("profile stays inside the member outline",
          all(box[0] - 0.01 <= p[0] <= box[2] + 0.01
              and box[1] - 0.01 <= p[1] <= box[3] + 0.01 for p in polygon))
    truss = preview.parse_program(read(RT))
    parts = dict((c["label"], c) for c in truss["components"])
    chord, web = parts["BC1-2"], parts["WB3-6"]
    # WB3-6 slopes into the chord beside the vertical WB6-7 (575-616):
    # their notches touch, so they are one cut.
    joint = [b for b in preview.cuts(chord, others=truss["components"])
             if b["name"] == "LIP NOTCH" and b["t0"] < 600.0 < b["t1"]]
    check("touching notches are one cut", len(joint) == 1 and len(joint[0]["ops"]) == 3,
          joint)
    joint = joint[0]
    # The sloping web crosses the lip band between its lip edge and the
    # bottom of the notch; the square cut must clear both crossings.
    edges = []
    for a, b in ((preview.outline(web)[0], preview.outline(web)[1]),
                 (preview.outline(web)[3], preview.outline(web)[2])):
        for y in (chord["flange"], chord["flange"] - joint["depth"]):
            edges.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
    first = min(x for x in edges if x < 600.0)
    check("the square cut clears the sloping web where it enters the lips",
          first - 6.0 < joint["t0"] < first - preview.NOTCH_CLEARANCE_MM + 0.01,
          (joint["t0"], first))
    check("and ends clear of the vertical web's far side",
          abs(joint["t1"] - (616.12 + preview.NOTCH_CLEARANCE_MM)) < 0.05, joint["t1"])
    stud_notch = [b for b in preview.cuts(tbot) if b["name"] == "LIP NOTCH"
                  and b["t0"] < 536.0 < b["t1"]][0]
    check("a notch leaves clearance either side of the stud",
          abs(stud_notch["t0"] - (536.0 - flange / 2.0 - preview.NOTCH_CLEARANCE_MM)) < 0.01
          and abs(stud_notch["t1"] - (536.0 + flange / 2.0 + preview.NOTCH_CLEARANCE_MM)) < 0.01,
          (stud_notch["t0"], stud_notch["t1"]))
    outer0, outer1, inner1, inner0 = preview.band_polygon(chord, joint)
    check("both ends of the cut are square, not leaning",
          abs(inner0[0] - outer0[0]) < 0.01 and abs(inner1[0] - outer1[0]) < 0.01,
          (outer0, inner0, outer1, inner1))
    for label in ("TC3-4", "TC4-5"):
        top = parts[label]
        notches = [b for b in preview.cuts(top, others=truss["components"])
                   if b["name"] == "LIP NOTCH"]
        bare = [b for b in preview.cuts(top) if b["name"] == "LIP NOTCH"]
        check(label + ": sloping chord notches fit their webs, sliders ignored",
              len(notches) == len(bare)
              and all(b["t1"] - b["t0"] < 4.0 * top["flange"] for b in notches),
              [(round(b["t0"], 1), round(b["t1"], 1)) for b in notches])
    check("shallow swages stay visible when zoomed out",
          all(b["depth"] >= 5.0 for b in preview.cuts(stud, min_inset=5.0)
              if b["name"] == "SWAGE"))


def faults():
    print("faults are reported")
    good = ("COMPONENT,P1-S1,600S162-54(50),SV,NORMAL,1,1000.00,"
            "0.00,0.00,0.00,1000.00,152.40")

    def problems(row, level):
        program = preview.parse_program("DETAILS,,JOB\r\n" + row + "\r\n")
        return [p["text"] for p in program["problems"] if p["level"] == level]

    check("clean row raises nothing",
          problems(good + ",DIMPLE,500.00", preview.ERROR) == []
          and problems(good + ",DIMPLE,500.00", preview.WARNING) == [])
    check("station past the end", len(problems(good + ",DIMPLE,1000.02", preview.ERROR)) == 1)
    check("station at the end is allowed", problems(good + ",DIMPLE,1000.00", preview.ERROR) == [])
    check("negative station", len(problems(good + ",DIMPLE,-1.00", preview.ERROR)) == 1)
    check("operation without a station", len(problems(good + ",DIMPLE", preview.ERROR)) == 1)
    check("station without an operation", len(problems(good + ",,500.00", preview.ERROR)) == 1)
    check("bad orientation", len(problems(good.replace("NORMAL", "FLIPPED"), preview.ERROR)) == 1)
    check("zero quantity", len(problems(good.replace(",NORMAL,1,", ",NORMAL,0,"), preview.ERROR)) == 1)
    # A row that cannot be read is dropped, which also leaves no components.
    bad_number = problems(good.replace("0.00,1000.00,152", "x,1000.00,152"), preview.ERROR)
    check("non-numeric coordinate", any("not all numbers" in t for t in bad_number), bad_number)
    short = problems("COMPONENT,P1-S1,600S162-54(50),SV", preview.ERROR)
    check("short row", any("fixed fields" in t for t in short), short)
    check("unknown row type", len(problems("PANEL,1,2,3", preview.ERROR)) >= 1)
    check("length disagrees with end points",
          len(problems(good.replace(",1,1000.00,", ",1,1010.00,"), preview.WARNING)) == 1)
    check("unreadable section", len(problems(good.replace("600S162-54(50)", "C150"), preview.WARNING)) == 1)
    check("out-of-order stations",
          len(problems(good + ",DIMPLE,500.00,DIMPLE,400.00", preview.WARNING)) == 1)
    check("duplicate label",
          len(problems(good + "\r\n" + good, preview.WARNING)) == 1)
    check("empty program", len(problems("", preview.ERROR)) == 1)

    program = preview.parse_program("DETAILS,,JOB\r\n" + good + ",DIMPLE,1500.00\r\n")
    check("the faulty component is flagged for the table",
          preview.component_flags(program) == {0: preview.ERROR})


def formatting():
    print("formatting")
    check("two decimals", preview.fmt(1060.849999999) == "1060.85")
    check("no negative zero", preview.fmt(-0.0001) == "0.00")
    check("overall size to one decimal", preview.fmt_overall(3600.008) == "3600.0")
    check("overall size rounds half up", preview.fmt_overall(1060.85) == "1060.9")


def writing():
    """uniqube_cnc_files: what Export puts on disk and what it then reports."""
    import shutil
    import tempfile

    import uniqube_cnc_files as files

    print("writing files")
    elb, rt = read(ELB), read(RT)
    other = elb.replace("DETAILS,,", "DETAILS,,OTHER ", 1)

    def item(name, filename, text, count, error=None):
        return {"unit": {"name": name}, "filename": filename, "text": text,
                "count": count, "error": error}

    items = [
        item("ELB-1", "ELB-1_600S162-54(50)_.csv", elb, 7),
        item("ELB-2", "ELB-2_600S162-54(50)_.csv", elb, 7),  # same program
        item("RT-1", "RT-1_362S162-54(50)_.csv", rt, 47),
        item("ELB-1", "ELB-1_600S162-54(50)_.csv", other, 7),  # same name, differs
        item("WP-9", "", "", 0, error="no exportable framing in WP-9"),
    ]
    folder = tempfile.mkdtemp(prefix="cnc_files_")
    try:
        out_dir = os.path.join(folder, "CNC")  # does not exist yet
        report = files.write_programs(items, out_dir)
        names = sorted(os.listdir(out_dir))
        check("identical units share a file, a clash gets a suffix", names == [
            "ELB-1_600S162-54(50)_.csv", "ELB-1_600S162-54(50)__2.csv",
            "RT-1_362S162-54(50)_.csv"], names)
        with io.open(os.path.join(out_dir, names[0]), "rb") as handle:
            check("written bytes are the program text", handle.read() == elb.encode("utf-8"))
        with io.open(os.path.join(out_dir, names[2]), "rb") as handle:
            check("truss bytes too", handle.read() == rt.encode("utf-8"))
        quantities = dict((os.path.basename(p), q) for p, _c, q in report["written"])
        check("the shared file counts both units",
              quantities.get("ELB-1_600S162-54(50)_.csv") == 2 and report["repeats"] == 1,
              (quantities, report["repeats"]))
        check("a unit that could not be built is reported, not written",
              report["errors"] == ["WP-9: no exportable framing in WP-9"], report["errors"])

        info = files.describe(report)
        check("report says there were problems",
              info["title"] == "Export finished, with problems" and not info["ok"], info["title"])
        check("report counts the files", info["summary"].startswith(
            "3 files written to " + out_dir), info["summary"])
        check("report lists files and the error",
              "x2 identical units" in info["details"] and "WP-9" in info["details"]
              and "47 components" in info["details"], info["details"])

        clean = files.describe(files.write_programs(items[:3], out_dir))
        check("a clean export says so", clean["ok"] and clean["title"] == "Export finished",
              clean["title"])
        check("and notes the repeat", "1 identical unit shares a program file." in clean["summary"],
              clean["summary"])

        blocked = os.path.join(folder, "blocked")
        with io.open(blocked, "w") as handle:
            handle.write(u"a file where the folder should be")
        failed = files.write_programs(items[:1], blocked)
        check("an unusable folder is an error, not a crash",
              not failed["written"] and len(failed["errors"]) >= 1, failed)
        check("and the report says nothing was written",
              files.describe(failed)["title"] == "Nothing was written")

        empty = files.describe(files.write_programs([], out_dir))
        check("nothing ticked, nothing exported", empty["title"] == "Nothing was exported")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def records_from(program, prefix):
    """Export records as ``uniqube_cnc_export`` holds them, from a program."""
    records = []
    for comp in program["components"]:
        records.append({
            "label": comp["label"][len(prefix) + 1:], "section": comp["section"],
            "role": comp["role"], "orient": comp["orient"], "qty": comp["qty"],
            "length": comp["length"], "x0": comp["x0"], "y0": comp["y0"],
            "x1": comp["x1"], "y1": comp["y1"], "depth": comp["depth"],
            "ops": [(op["name"], op["station"]) for op in comp["ops"]],
        })
    return records


def seated_notches():
    """A lip notch slides to sit centred on a brace passing through it."""
    import uniqube_cnc_export as export

    print("lip notches seat sloping members")
    # BIG5 (calibration panel) as exported before notches were seated.
    records = records_from(preview.parse_program(read(BIG5)), "BIG5")
    before = dict((r["label"], list(r["ops"])) for r in records)
    text = export.format_csv("CALIBRATION", records, prefix="BIG5")
    check("BIG5: notches move", export._seat_lip_notches(records, text, "BIG5"))
    after = dict((r["label"], r["ops"]) for r in records)
    moved = sorted(label for label in after if after[label] != before[label])
    check("only members a brace lands on change", moved == ["E1", "E2", "TBOT1", "TTOP1"],
          moved)

    def notches(ops):
        return [s for name, s in ops if name == "LIP NOTCH"]

    def dimples(ops):
        return [s for name, s in ops if name == "DIMPLE"]

    check("the operation count stays the same",
          all(len(after[k]) == len(before[k]) for k in after))
    check("dimples stay where they were", all(dimples(after[k]) == dimples(before[k])
                                              for k in after))
    # Two braces meet at E1's mid-height in a V: their notches spread apart.
    old, new = notches(before["E1"]), notches(after["E1"])
    check("E1: each brace notch moves toward where the brace sits in the lips",
          len(new) == 2 and 5.0 < old[0] - new[0] < 20.0 and 5.0 < new[1] - old[1] < 20.0,
          (old, new))

    panel = preview.parse_program(export.format_csv("CALIBRATION", records, prefix="BIG5"))
    members = dict((c["label"], c) for c in panel["components"])
    end = members["BIG5-E1"]
    lip_x = preview.offset_point(end, 0.0, end["flange"] / 2.0)[0]
    deep_x = preview.offset_point(end, 0.0, end["flange"] / 2.0 - preview.LIP_RETURN_MM)[0]
    for label, station in zip(("BIG5-TTOP5", "BIG5-TTOP3"), new):
        corners = preview.outline(members[label])
        ys = []
        for a, b in ((corners[0], corners[1]), (corners[3], corners[2])):
            for x in (lip_x, deep_x):
                ys.append(a[1] + (x - a[0]) * (b[1] - a[1]) / (b[0] - a[0]) - end["y0"])
        check(label + ": its notch on E1 is centred on it",
              abs(station - (min(ys) + max(ys)) / 2.0) < 0.05, (station, ys))
    cut = [b for b in preview.cuts(end, others=panel["components"]) if b["name"] == "LIP NOTCH"]
    check("the notch keeps its width",
          all(abs(b["t1"] - b["t0"] - end["flange"] - 2 * preview.NOTCH_CLEARANCE_MM) < 0.05
              for b in cut), [(b["t0"], b["t1"]) for b in cut])

    # A wall of square studs is left exactly as it was.
    elb = preview.parse_program(read(ELB))
    plain = records_from(elb, "ELB1")
    untouched = [list(r["ops"]) for r in plain]
    check("square studs: no notch moves",
          not export._seat_lip_notches(plain, read(ELB), "ELB1")
          and [r["ops"] for r in plain] == untouched)

    print("inclined members are chamfered at both ends")
    export._chamfer_inclined_ends(records)
    ends = dict((r["label"], [s for name, s in r["ops"] if name == "END_TRUSS"])
                for r in records)
    braces = ("TTOP3", "TTOP5", "TTOP7", "TTOP9")
    check("every brace gets END_TRUSS at both ends, first and last",
          all(ends[b] == [0.0, r["length"]] and r["ops"][0][0] == "END_TRUSS"
              and r["ops"][-1][0] == "END_TRUSS"
              for r in records for b in [r["label"]] if b in braces),
          dict((b, ends[b]) for b in braces))
    check("square members get none",
          all(not ends[label] for label in ends if label not in braces),
          [label for label in ends if ends[label] and label not in braces])
    export._chamfer_inclined_ends(records)
    check("running it twice adds nothing",
          all(len(ends[r["label"]]) == len([1 for n, _s in r["ops"] if n == "END_TRUSS"])
              for r in records))
    rt = records_from(preview.parse_program(read(RT)), "")
    before_rt = [list(r["ops"]) for r in rt]
    export._chamfer_inclined_ends(rt)
    check("RT-1 truss webs already match Vertex: unchanged",
          [r["ops"] for r in rt] == before_rt)

    text = export.format_csv("CALIBRATION", records, prefix="BIG5")
    panel = preview.parse_program(text)
    brace = [c for c in panel["components"] if c["label"] == "BIG5-TTOP3"][0]
    polygon, _web = preview.profile(brace, [])
    leg = preview.chamfer_mm(brace)
    check("a chamfered member is drawn with its four end corners cut",
          len(polygon) == 8, polygon)
    tip = preview.point_at(brace, 0.0)
    nearest = min(math.hypot(p[0] - tip[0], p[1] - tip[1]) for p in polygon)
    half = brace["flange"] / 2.0
    check("the chamfer is 45 degrees with the set leg",
          abs(nearest - (half - leg)) < 0.01, (nearest, half - leg))
    stud = [c for c in panel["components"] if c["label"] == "BIG5-S2"][0]
    check("a square stud keeps square ends", len(preview.profile(stud, [])[0]) == 4)

    with io.open(os.path.join(HERE, "out", "BIG5_seated.csv"), "w", encoding="utf-8",
                 newline="") as handle:
        handle.write(text)


if __name__ == "__main__":
    production_files()
    geometry()
    profiles()
    seated_notches()
    faults()
    formatting()
    writing()
    print("")
    print("FAILURES: {}".format(len(FAILURES)))
    sys.exit(1 if FAILURES else 0)
