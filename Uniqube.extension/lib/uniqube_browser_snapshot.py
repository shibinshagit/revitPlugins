# -*- coding: utf-8 -*-
"""Capture Revit Project Browser metadata for Uniqube publish."""
from __future__ import print_function

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    View,
    ViewSheet,
    ViewSchedule,
    ViewType,
    RevitLinkInstance,
    Level,
    Phase,
    Family,
    Viewport,
    ModelPathUtils,
    SectionType,
)


def _safe_str(val):
    if val is None:
        return ""
    try:
        from uniqube_text import as_unicode
        return as_unicode(val)
    except Exception:
        try:
            return str(val)
        except Exception:
            return ""


def _view_type_name(view):
    try:
        return str(view.ViewType)
    except Exception:
        return "Unknown"


def _discipline(view):
    try:
        return str(view.Discipline)
    except Exception:
        return ""


def _extract_schedule_table(schedule, max_rows=500, max_cols=40):
    """Extract schedule body as columns + rows (best-effort)."""
    columns = []
    rows = []
    try:
        table = schedule.GetTableData()
        body = table.GetSectionData(SectionType.Body)
        n_rows = min(body.NumberOfRows, max_rows)
        n_cols = min(body.NumberOfColumns, max_cols)
        # Header row often at top of body or separate Header section
        try:
            header = table.GetSectionData(SectionType.Header)
            if header and header.NumberOfRows > 0:
                hrow = header.NumberOfRows - 1
                for c in range(min(header.NumberOfColumns, n_cols)):
                    columns.append(_safe_str(schedule.GetCellText(SectionType.Header, hrow, c)))
        except Exception:
            pass
        if not columns:
            for c in range(n_cols):
                columns.append("Col{}".format(c + 1))
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                try:
                    row.append(_safe_str(schedule.GetCellText(SectionType.Body, r, c)))
                except Exception:
                    row.append("")
            # skip empty rows
            if any(cell.strip() for cell in row):
                rows.append(row)
    except Exception as ex:
        return {
            "columns": columns or [],
            "rows": rows,
            "error": _safe_str(ex),
        }
    return {"columns": columns, "rows": rows}


def build_browser_snapshot(doc):
    """
    Return a dict mirroring Revit Project Browser richness.
    Suitable for json.dumps (IronPython).
    """
    pi = doc.ProjectInformation
    project_info = {
        "title": _safe_str(doc.Title),
        "path": _safe_str(doc.PathName),
        "name": _safe_str(pi.Name),
        "number": _safe_str(pi.Number),
        "client": _safe_str(pi.ClientName),
        "address": _safe_str(pi.Address),
        "status": _safe_str(pi.Status),
        "author": _safe_str(pi.Author),
        "buildingName": _safe_str(pi.BuildingName),
    }

    levels = []
    for lvl in FilteredElementCollector(doc).OfClass(Level):
        elev = 0.0
        try:
            elev = float(lvl.Elevation)
        except Exception:
            pass
        levels.append({
            "id": lvl.Id.IntegerValue,
            "name": _safe_str(lvl.Name),
            "elevation": elev,
        })
    levels.sort(key=lambda x: x["elevation"])

    phases = []
    for ph in FilteredElementCollector(doc).OfClass(Phase):
        phases.append({"id": ph.Id.IntegerValue, "name": _safe_str(ph.Name)})

    links = []
    for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        lt = doc.GetElement(li.GetTypeId())
        path = ""
        status = ""
        try:
            status = _safe_str(lt.GetLinkedFileStatus())
        except Exception:
            pass
        try:
            ep = lt.GetExternalFileReference()
            path = ModelPathUtils.ConvertModelPathToUserVisiblePath(ep.GetPath())
        except Exception:
            path = ""
        links.append({
            "id": li.Id.IntegerValue,
            "name": _safe_str(li.Name),
            "status": status,
            "path": path,
            "typeId": lt.Id.IntegerValue if lt else None,
        })

    views = []
    view_templates = []
    schedules = []
    legends = []

    for v in FilteredElementCollector(doc).OfClass(View):
        if isinstance(v, ViewSheet):
            continue
        name = _safe_str(v.Name)
        vt = _view_type_name(v)
        entry = {
            "id": v.Id.IntegerValue,
            "name": name,
            "viewType": vt,
            "discipline": _discipline(v),
            "isTemplate": bool(v.IsTemplate),
        }
        if v.IsTemplate:
            view_templates.append(entry)
            continue
        if isinstance(v, ViewSchedule) or vt == "Schedule":
            # Skip noisy auto revision schedules from drawing export list,
            # but still include lightweight schedule data when cheap.
            table = None
            if not name.startswith("<Revision Schedule"):
                table = _extract_schedule_table(v)
            else:
                table = {"columns": [], "rows": []}
            schedules.append({
                "id": entry["id"],
                "name": name,
                "viewType": "Schedule",
                "discipline": entry["discipline"],
                "table": table,
            })
            continue
        if vt == "Legend":
            legends.append(entry)
            continue
        # Skip browser/system utility views
        if vt in ("ProjectBrowser", "SystemBrowser", "Internal", "DraftingView"):
            if vt == "DraftingView":
                views.append(entry)
            continue
        views.append(entry)

    sheets = []
    for s in FilteredElementCollector(doc).OfClass(ViewSheet):
        viewports = []
        for vp in FilteredElementCollector(doc, s.Id).OfClass(Viewport):
            vv = doc.GetElement(vp.ViewId)
            if vv:
                viewports.append({
                    "viewportId": vp.Id.IntegerValue,
                    "viewId": vv.Id.IntegerValue,
                    "viewName": _safe_str(vv.Name),
                    "viewType": _view_type_name(vv),
                })
        sheets.append({
            "id": s.Id.IntegerValue,
            "number": _safe_str(s.SheetNumber),
            "name": _safe_str(s.Name),
            "viewports": viewports,
        })
    sheets.sort(key=lambda x: x["number"])

    families_summary = {}
    try:
        for f in FilteredElementCollector(doc).OfClass(Family):
            cat = "(none)"
            try:
                if f.FamilyCategory:
                    cat = _safe_str(f.FamilyCategory.Name)
            except Exception:
                pass
            families_summary[cat] = families_summary.get(cat, 0) + 1
    except Exception:
        pass

    return {
        "schemaVersion": 1,
        "projectInfo": project_info,
        "levels": levels,
        "phases": phases,
        "links": links,
        "views": views,
        "sheets": sheets,
        "schedules": schedules,
        "legends": legends,
        "viewTemplates": [{"id": t["id"], "name": t["name"], "viewType": t["viewType"]} for t in view_templates],
        "familiesSummary": families_summary,
    }
