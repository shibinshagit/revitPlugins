# -*- coding: utf-8 -*-
"""Checks for uniqube_cnc_preview against the two production CSV files.

    python tools/cnc_preview_tests/test_preview.py

Plain Python, no Revit: the module under test is what the Generate CNC
window uses to read a program back before it is written.
"""
from __future__ import print_function

import io
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
          preview.draw_layer(tbot) == 2 and preview.draw_layer(stud) == 0)
    check("click on a stud selects it", preview.hit_test(program["components"], 536.0, 1800.0) == stud["index"])
    check("click where a stud runs into the track selects the track",
          preview.hit_test(program["components"], 536.0, 10.0) == tbot["index"])
    check("click on empty space selects nothing",
          preview.hit_test(program["components"], 280.0, 1800.0) is None)


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


if __name__ == "__main__":
    production_files()
    geometry()
    faults()
    formatting()
    writing()
    print("")
    print("FAILURES: {}".format(len(FAILURES)))
    sys.exit(1 if FAILURES else 0)
