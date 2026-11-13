# -*- coding: utf-8 -*-
"""Write CNC programs to disk and describe what happened.

Pure Python: no Revit, no window. The text written is the text the Generate
CNC window showed and checked, byte for byte.
"""
from __future__ import print_function

import os


def _is_locked_error(ex):
    """True when the CSV could not be written because something has it open."""
    if getattr(ex, "errno", None) in (13, 32):
        return True
    text = str(ex).lower()
    return "another process" in text or "permission denied" in text


def _log(log, level, *args):
    if log is None:
        return
    try:
        getattr(log, level)(*args)
    except Exception:
        pass


def write_programs(items, out_dir, log=None):
    """Write one CSV per distinct program.

    items  [{"unit": {"name"}, "filename", "text", "count", "error"}, ...]

    Identical panels / trusses share one file; programs that differ but would
    get the same file name receive a numeric suffix.

    Returns {"out_dir", "written": [(path, components, quantity)],
             "locked": [file name], "errors": [text], "repeats": int}.
    """
    written = []
    locked = []
    errors = []
    repeats = 0
    by_content = {}
    order = []
    used_names = set()

    if not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir)
        except Exception as ex:
            errors.append("{}: {}".format(out_dir, ex))

    for item in items:
        name = item["unit"]["name"]
        if item["error"]:
            _log(log, "error", "CNC export failed for %s: %s", name, item["error"])
            errors.append("{}: {}".format(name, item["error"]))
            continue
        fname, text, count = item["filename"], item["text"], item["count"]

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
        order.append(text)

    for text in order:
        fname, quantity, count = by_content[text]
        path = os.path.join(out_dir, fname)
        try:
            # Binary write: the CSV already carries CRLF line endings.
            with open(path, "wb") as handle:
                handle.write(text.encode("utf-8"))
        except Exception as ex:
            _log(log, "error", "could not write %s: %s", path, ex)
            if _is_locked_error(ex):
                locked.append(fname)
            else:
                errors.append("{}: {}".format(fname, ex))
            continue
        written.append((path, count, quantity))
        _log(log, "info", "CNC export %s (%s members, x%s)", path, count, quantity)

    return {
        "out_dir": out_dir,
        "written": written,
        "locked": locked,
        "errors": errors,
        "repeats": repeats,
    }


def describe(report):
    """A report as {"ok", "title", "summary", "details"} for display."""
    written = sorted(report["written"])
    locked = sorted(report["locked"])
    errors = list(report["errors"])
    problems = len(locked) + len(errors)

    if written and not problems:
        title = "Export finished"
    elif written:
        title = "Export finished, with problems"
    elif problems:
        title = "Nothing was written"
    else:
        title = "Nothing was exported"

    summary = "{} file{} written to {}".format(
        len(written), "" if len(written) == 1 else "s", report["out_dir"]
    )
    if report["repeats"]:
        summary += "\n{} identical unit{} share{} a program file.".format(
            report["repeats"],
            "" if report["repeats"] == 1 else "s",
            "s" if report["repeats"] == 1 else "",
        )
    if problems:
        summary += "\n{} problem{} - see below.".format(
            problems, "" if problems == 1 else "s"
        )

    lines = []
    if locked:
        lines.append("NOT WRITTEN - THE FILE IS OPEN IN ANOTHER PROGRAM")
        lines.append("Close these in Excel (or whatever has them open), then export again.")
        lines.extend("  " + fname for fname in locked)
        lines.append("")
    if errors:
        lines.append("ERRORS")
        lines.extend("  " + text for text in errors)
        lines.append("")
    if written:
        lines.append("WRITTEN")
        width = max(len(os.path.basename(path)) for path, _c, _q in written)
        for path, count, quantity in written:
            line = "  {}   {} component{}".format(
                os.path.basename(path).ljust(width), count, "" if count == 1 else "s"
            )
            if quantity > 1:
                line += "   x{} identical units".format(quantity)
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()

    return {
        "ok": bool(written) and not problems,
        "title": title,
        "summary": summary,
        "details": "\n".join(lines),
    }
