# -*- coding: utf-8 -*-
"""Run UNIBOM under pyRevit's IronPython 2.7 against the open model.

Executed from inside Revit (doc, LIB, FIXTURE, OUT set by the host). Catches
IronPython-only problems that CPython tests cannot: string formatting,
unicode, Revit API access in collect.py, report and CSV generation.
"""
import io
import json
import sys
import traceback

RESULT = []


def log(*parts):
    RESULT.append(u" ".join(u"{}".format(p) for p in parts))


try:
    if LIB not in sys.path:
        sys.path.insert(0, LIB)
    import clr
    clr.AddReference("RevitAPI")
    from unibom import collect, engine, formula, report

    lib = engine.Library()
    log("formula", formula.evaluate("ceil(5000 / P.x)", {"P": {"x": 2400}}))

    members, walls = collect.collect(doc, "model")
    log("collected", len(members), "members,", len(walls), "walls,",
        sum(1 for w in walls if w.get("container")), "framed")

    with io.open(FIXTURE, "r", encoding="utf-8-sig") as fh:
        fixture = json.load(fh)
    a = engine.run_counted(members, lib).lgs_summary()["net_kg"]
    b = engine.run_counted(fixture["members"], lib).lgs_summary()["net_kg"]
    log("net kg live collector", round(a, 2), "| fixture", round(b, 2))

    issues, counted = engine.check_model(members, walls, lib)
    recipe = engine.run_recipes(walls, lib)
    issues = issues + recipe.issues
    walls_sys = ["External wall panels", "Internal wall panels"]

    def link(ids, label):
        return u'<a href="#">{}</a>'.format(label)

    for lk in (link, None):
        rep = report.boq_report(doc.Title, [("Scope", "Whole model"), ("Method", "Both")], counted, recipe,
                                issues, link=lk, files=[OUT + "\\example.csv"], wall_systems=walls_sys)
        html = rep.body_html()
        log("report html", len(html), "chars", "with links" if lk else "file")
    rep.save(OUT + "\\ipy_report.html")
    chk = report.check_report(doc.Title, [("Rules", "v1")], counted, walls, issues, link)
    log("check report html", len(chk.body_html()))
    when = report.stamp()
    log("csv", report.write_csv(counted, doc.Title, OUT, when))
    log("csv", report.write_csv(recipe, doc.Title, OUT, when))
    log("OK")
except Exception:
    log("FAILED")
    log(traceback.format_exc())

RESULT = u"\n".join(RESULT)
