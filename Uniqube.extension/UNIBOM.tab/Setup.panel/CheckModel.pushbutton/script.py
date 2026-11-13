# -*- coding: utf-8 -*-
"""UNIBOM Check Model: what would make the BOQ wrong, before running it."""
from __future__ import print_function

__title__ = "Check Model"
__author__ = "Uniqube"
# Keep the engine alive so Select links keep working after the script ends.
__persistentengine__ = True

from pyrevit import revit, script

from unibom import collect, engine, report
from unibom.ui import dialogs, selection

doc = revit.doc


def main():
    lib = engine.Library()
    with dialogs.busy("UNIBOM - checking the model..."):
        members, walls = collect.collect(doc, "model")
        issues, counted = engine.check_model(members, walls, lib)

    output = script.get_output()
    output.set_title("UNIBOM - Check Model")

    link = selection.SelectionLinks(output, revit.uidoc).link

    rep = report.check_report(doc.Title, [
        ("Rules", "v{}".format(lib.settings.get("version", 1))),
        ("Items in master", "{:,}".format(len(lib.items)))], counted, walls, issues, link)
    rep.show(output)


if __name__ == "__main__":
    main()
