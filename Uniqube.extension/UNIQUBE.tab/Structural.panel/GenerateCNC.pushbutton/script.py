# -*- coding: utf-8 -*-
"""Generate CFS CNC CSV files for rollformer export.

Exports floor-truss assemblies and wall panels (BIMSF_Container groups with
top/bottom tracks). Writes one CSV per unit with COMPONENT rows and
DIMPLE / SWAGE / LIP NOTCH / END_TRUSS operations.
"""
from __future__ import print_function

import os

from pyrevit import revit, forms, script

import uniqube_cnc_export as cnc

doc = revit.doc
logger = script.get_logger()
output = script.get_output()


def _project_name():
    try:
        pi = doc.ProjectInformation
        p = pi.LookupParameter("Project Name")
        if p and p.HasValue:
            name = (p.AsString() or "").strip()
            if name and name.lower() != "project name":
                return name
    except Exception:
        pass
    return "MULK Test"


def main():
    units = cnc.collect_cnc_units(doc)
    if not units:
        forms.alert(
            "No CNC units found.\n\n"
            "Need either:\n"
            "• A truss assembly with Comments = TopChord + BottomChord, or\n"
            "• A BIMSF_Container panel with TTOP/TBOT (or Top/Bottom chord) members.",
            title="UNIQUBE — Generate CNC",
        )
        return

    if len(units) == 1:
        selected = units
    else:
        labels = [
            "{} ({}, {} members)".format(
                u["name"], u["source"], len(u["members"])
            )
            for u in units
        ]
        picked = forms.SelectFromList.show(
            labels,
            title="Select panels / trusses to export",
            multiselect=True,
        )
        if not picked:
            return
        picked_set = set(picked)
        selected = [
            u
            for u, label in zip(units, labels)
            if label in picked_set
        ]

    job_name = forms.ask_for_string(
        default=_project_name(),
        prompt="Job name for DETAILS row:",
        title="UNIQUBE — Generate CNC",
    )
    if job_name is None:
        return
    job_name = (job_name or "").strip() or "MULK Test"

    out_dir = forms.pick_folder(title="Select folder for CNC CSV files")
    if not out_dir:
        return

    written = []
    errors = []
    for unit in selected:
        try:
            fname, text, count = cnc.export_unit(doc, unit, job_name=job_name)
            path = os.path.join(out_dir, fname)
            with open(path, "w") as handle:
                handle.write(text)
            written.append((path, count))
            logger.info(
                "CNC export %s → %s (%s members)", unit["name"], path, count
            )
        except Exception as ex:
            logger.error("CNC export failed for %s: %s", unit["name"], ex)
            errors.append("{}: {}".format(unit["name"], ex))

    if written:
        output.print_md("### CNC CSV export")
        for path, count in written:
            output.print_md("- `{}` ({} components)".format(path, count))

    msg = "Exported {} CSV file(s) to:\n{}".format(len(written), out_dir)
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors)
    forms.alert(msg, title="UNIQUBE — Generate CNC")


if __name__ == "__main__":
    main()
