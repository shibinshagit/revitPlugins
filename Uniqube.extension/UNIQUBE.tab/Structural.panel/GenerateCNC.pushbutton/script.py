# -*- coding: utf-8 -*-
"""Generate CFS CNC CSV files for rollformer export (Vertex BD format).

Exports wall panels (BIMSF_Container groups) and floor-truss assemblies.
Writes one CSV per unit with COMPONENT rows and DIMPLE / SWAGE /
LIP NOTCH / WEB NOTCH operations derived from member crossings.
"""
from __future__ import print_function

import os

from pyrevit import revit, forms, script

import uniqube_cnc_export as cnc

doc = revit.doc
logger = script.get_logger()
# The output window is created only once there are results to report: opening
# it up front hides the selection dialogs behind it.


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


def _is_locked_error(ex):
    """True when the CSV could not be written because something has it open."""
    if getattr(ex, "errno", None) in (13, 32):
        return True
    text = str(ex).lower()
    return "another process" in text or "permission denied" in text


def _report(written, locked, errors, repeats, out_dir):
    """Results go to the output window.

    Revit's TaskDialog throws on long multi-line text (it crashed Revit when
    fed a file path plus an exception message), so nothing dynamic goes there.
    """
    out = script.get_output()
    out.print_md("## UNIQUBE CNC export")
    out.print_md("**Folder:** `{}`".format(out_dir))

    if written:
        out.print_md("**Wrote {} file(s):**".format(len(written)))
        for path, count, quantity in sorted(written):
            suffix = "  x{} identical units".format(quantity) if quantity > 1 else ""
            out.print_md(
                "- `{}` - {} components{}".format(
                    os.path.basename(path), count, suffix
                )
            )
    if repeats:
        out.print_md(
            "_{} identical unit(s) share a program file._".format(repeats)
        )

    if locked:
        out.print_md("### Not written - file is open in another program")
        out.print_md(
            "Close these in Excel (or whatever has them open), then run "
            "Generate CNC again:"
        )
        for fname in sorted(locked):
            out.print_md("- `{}`".format(fname))

    if errors:
        out.print_md("### Errors")
        for item in errors:
            out.print_md("- {}".format(item))

    if not written and not locked and not errors:
        out.print_md("Nothing was exported.")


def _default_out_dir():
    """<model folder>\\CNC, created on demand; Desktop if the model is unsaved."""
    base = ""
    try:
        if doc.PathName:
            model_dir = os.path.dirname(doc.PathName)
            # Don't nest a CNC folder inside a folder already called CNC.
            if os.path.basename(model_dir).upper() == "CNC":
                base = model_dir
            else:
                base = os.path.join(model_dir, "CNC")
    except Exception:
        pass
    if not base:
        base = os.path.join(
            os.path.expanduser("~"), "Desktop", "CNC"
        )
    if not os.path.isdir(base):
        try:
            os.makedirs(base)
        except Exception:
            return os.path.dirname(base) or os.path.expanduser("~")
    return base


def _confirm(unit_count, job_name, out_dir):
    """One dialog for job name and folder. Returns (job_name, out_dir) or None."""
    while True:
        choice = forms.alert(
            "Ready to export {} unit(s).\n\n"
            "Job name:  {}\n"
            "Folder:  {}".format(unit_count, job_name, out_dir),
            title="UNIQUBE - Generate CNC",
            options=["Export", "Change job name", "Change folder", "Cancel"],
        )
        if choice == "Export":
            return job_name, out_dir
        if choice == "Change job name":
            entered = forms.ask_for_string(
                default=job_name,
                prompt="Job name for the DETAILS row:",
                title="UNIQUBE - Generate CNC",
            )
            job_name = (entered or "").strip() or job_name
        elif choice == "Change folder":
            picked = forms.pick_folder(title="Select folder for CNC CSV files")
            if picked:
                out_dir = picked
        else:
            return None


def _preselected_names():
    """Panel / truss names for whatever is selected in Revit right now."""
    try:
        elements = [doc.GetElement(eid) for eid in revit.get_selection().element_ids]
    except Exception:
        return set()
    return cnc.unit_names_for_elements(doc, elements)


def main():
    with forms.ProgressBar(title="Scanning framing...", indeterminate=True):
        units = cnc.collect_cnc_units(doc)

    if not units:
        forms.alert(
            "No CNC units found.\n\n"
            "Each panel needs framing with BIMSF_Container plus a top and "
            "bottom track (BIMSF_Description = TTOP / TBOT), or a truss "
            "assembly with Comments = TopChord / BottomChord.",
            title="UNIQUBE - Generate CNC",
        )
        return

    # Selecting framing in Revit first exports just those panels.
    wanted = _preselected_names()
    if wanted:
        selected = [u for u in units if u["name"] in wanted]
    elif len(units) == 1:
        selected = units
    else:
        labels = [
            "{}  ({} members)".format(u["name"], len(u["members"]))
            for u in units
        ]
        picked = forms.SelectFromList.show(
            labels,
            title="Select panels / trusses to export",
            multiselect=True,
        )
        if not picked:
            forms.alert(
                "Export cancelled - no panels picked.",
                title="UNIQUBE - Generate CNC",
            )
            return
        picked_set = set(picked)
        selected = [
            u for u, label in zip(units, labels) if label in picked_set
        ]

    if not selected:
        forms.alert("Nothing selected to export.", title="UNIQUBE - Generate CNC")
        return

    confirmed = _confirm(len(selected), _project_name(), _default_out_dir())
    if confirmed is None:
        forms.alert("Export cancelled.", title="UNIQUBE - Generate CNC")
        return
    job_name, out_dir = confirmed

    written = []
    errors = []
    repeats = 0
    # Identical panels/trusses share one program; differing ones get a suffix.
    by_content = {}
    used_names = set()

    for unit in selected:
        try:
            fname, text, count = cnc.export_unit(doc, unit, job_name=job_name)
        except Exception as ex:
            logger.error("CNC export failed for %s: %s", unit["name"], ex)
            errors.append("{}: {}".format(unit["name"], ex))
            continue

        if text in by_content:
            by_content[text][1] += 1
            repeats += 1
            continue

        base, ext = os.path.splitext(fname)
        unique = fname
        index = 2
        while unique in used_names:
            unique = "{}_{}{}".format(base, index, ext)
            index += 1
        used_names.add(unique)
        by_content[text] = [unique, 1, count]

    locked = []
    for text, info in by_content.items():
        fname, quantity, count = info
        path = os.path.join(out_dir, fname)
        try:
            # Binary write: the CSV already carries CRLF line endings.
            with open(path, "wb") as handle:
                handle.write(text.encode("utf-8"))
        except Exception as ex:
            logger.error("could not write %s: %s", path, ex)
            if _is_locked_error(ex):
                locked.append(fname)
            else:
                errors.append("{}: {}".format(fname, ex))
            continue
        written.append((path, count, quantity))
        logger.info("CNC export %s (%s members, x%s)", path, count, quantity)

    _report(written, locked, errors, repeats, out_dir)


if __name__ == "__main__":
    main()
