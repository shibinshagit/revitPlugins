# -*- coding: utf-8 -*-
"""One-step MEP panel prep: group, copy framing to host, enable sync (Task 5).

Host MEP model: groups MEP, copies linked panel framing into host, regroups
panel + MEP together, turns on click-to-select sync.

Framing link model (open .rvt directly): groups panel framing only.
"""
from pyrevit import revit, DB, forms, script
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
from System.Windows import Thickness
import panel_utils as pu

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
app = doc.Application
logger = script.get_logger()


def _row_label(row):
    parts = [row["display"]]
    if row.get("mep_count"):
        parts.append("[ MEP: {} ]".format(row["mep_count"]))
    if row.get("link_framing"):
        parts.append("[ link: {} ]".format(row["link_framing"]))
    if row.get("host_framing"):
        parts.append("[ host: {} ]".format(row["host_framing"]))
    if row.get("source") == "link" and row.get("link_name"):
        parts.append("(link: {})".format(row["link_name"]))
    return "  ".join(parts)


class MEPPanelSelector(forms.WPFWindow):
    def __init__(self, rows, crossing_count, mode_host=True):
        forms.WPFWindow.__init__(self, "SelectMEPPanels.xaml")
        self._checks = []
        self.selected = None

        if mode_host:
            self.summary_text.Text = (
                "{0} panel(s). {1} crossing MEP (red). "
                "Groups MEP, copies panel framing from link into this model, "
                "regroups panel + MEP, and turns on selection sync.".format(
                    len(rows), crossing_count
                )
            )
        else:
            self.summary_text.Text = (
                "{0} panel(s) in this framing model. "
                "Creates one Revit group per panel.".format(len(rows))
            )

        for row in rows:
            cb = CheckBox()
            cb.Content = _row_label(row)
            cb.Margin = Thickness(2, 3, 2, 3)
            has_content = (
                row.get("mep_count", 0) > 0
                or row.get("host_framing", 0) > 0
                or row.get("link_framing", 0) > 0
            )
            cb.IsChecked = has_content
            if not has_content:
                cb.Foreground = Brushes.Gray
            self.panel_stack.Children.Add(cb)
            self._checks.append((cb, row["pid"]))

    def select_all_click(self, sender, args):
        all_on = all(cb.IsChecked for cb, _ in self._checks)
        for cb, _ in self._checks:
            cb.IsChecked = not all_on

    def ok_click(self, sender, args):
        self.selected = [pid for cb, pid in self._checks if cb.IsChecked]
        self.Close()


def choose_panels(rows, crossing_count, mode_host=True):
    if not rows:
        return None
    try:
        win = MEPPanelSelector(rows, crossing_count, mode_host)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed: %s", ex)
        return pu.choose_panels([r["pid"] for r in rows])


def _is_framing_primary_doc(doc):
    """True when this IS the framing link file opened directly."""
    if doc.IsLinked:
        return False
    has_links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .GetElementCount()
    )
    if has_links > 0:
        return False
    return bool(pu.map_framing(doc))


def _run_framing_doc(selected):
    stats = {"link_groups": 0, "errors": []}
    with revit.Transaction("UNIQUBE: Group Panel Framing"):
        pu._delete_groups_in_doc(doc, selected)
        stats = pu.group_framing_in_active_doc(doc, selected)
    msg = (
        "Done (framing model).\n\n"
        "Panel groups created: {}\n\n"
        "Next: open the MEP host model and run Prepare MEP Panels "
        "there for the same panels.".format(
            stats.get("link_groups", 0)
        )
    )
    if stats.get("errors"):
        msg += "\n\nIssues:\n" + "\n".join(stats["errors"][:5])
    forms.alert(msg, title="UNIQUBE — Prepare MEP Panels")
    return stats


def _run_copy_to_host(selected, link_framing):
    """Copy linked framing into host and regroup — returns copy stats dict."""
    copy_stats = {
        "panels": 0,
        "members_copied": 0,
        "groups_exploded": 0,
        "host_groups": 0,
        "skipped": [],
        "errors": [],
        "copied_pids": [],
    }
    if not link_framing:
        copy_stats["skipped"].append("(no structural link loaded)")
        return copy_stats

    if not hasattr(pu, "copy_panel_framing_to_host"):
        copy_stats["errors"].append("panel_utils.py out of date — git pull")
        return copy_stats

    try:
        with revit.Transaction("UNIQUBE: Copy Panel to Host"):
            copy_stats = pu.copy_panel_framing_to_host(
                doc, view, selected, link_framing, regroup=False
            )
    except Exception as ex:
        copy_stats["errors"].append("Copy: {}".format(ex))
        return copy_stats

    copied_pids = copy_stats.get("copied_pids", [])
    if copy_stats.get("panels", 0) > 0 and copied_pids:
        try:
            with revit.Transaction("UNIQUBE: Regroup Panel + MEP"):
                regroup = pu.regroup_panels_in_host(
                    doc, view, copied_pids, tag_mep=True
                )
                copy_stats["host_groups"] = regroup.get("groups", 0)
                copy_stats.setdefault("errors", []).extend(
                    regroup.get("group_errors", [])
                )
        except Exception as ex:
            copy_stats.setdefault("errors", []).append("Regroup: {}".format(ex))

    if hasattr(pu, "verify_panel_copy"):
        copy_stats["verify"] = pu.verify_panel_copy(doc, selected)
    return copy_stats


def _run_host_doc(selected, panel_elements, link_zones, link_framing):
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    group_stats = {}
    try:
        with revit.Transaction("UNIQUBE: Group MEP + Panels"):
            pu._delete_groups_in_doc(doc, selected)
            group_stats = pu.combine_panels_group_color(
                doc,
                view,
                selected,
                panel_elements,
                link_zones,
                link_framing=link_framing,
                tag_mep=True,
            )
    except Exception as ex:
        forms.alert("Grouping failed:\n{}".format(ex), title="UNIQUBE")
        return

    link_framing = pu.map_link_framing_by_container(doc)
    copy_stats = _run_copy_to_host(selected, link_framing)

    try:
        import panel_selection_sync as pss
        pss.mark_grouped()
        pss.purge_legacy_idling(uidoc.Application)
    except Exception as ex:
        logger.debug("panel mode setup failed: %s", ex)

    msg = (
        "Done.\n\n"
        "1. MEP groups: {0}\n"
        "2. Crossing MEP (red): {1}\n"
        "3. Panels copied to host: {2}\n"
        "4. Framing members copied: {3}\n"
        "5. Final host groups (panel + MEP): {4}\n"
        "6. Panel mode: GROUPED".format(
            group_stats.get("groups", 0),
            group_stats.get("crossing_count", 0),
            copy_stats.get("panels", 0),
            copy_stats.get("members_copied", 0),
            copy_stats.get("host_groups", 0)
            or group_stats.get("groups", 0),
        )
    )

    verify = copy_stats.get("verify", [])
    if verify:
        msg += "\n\nVerify:"
        for row in verify[:10]:
            msg += "\n  {0} — {1} (host: {2}, link: {3})".format(
                row["panel"],
                row["status"],
                row["host_framing"],
                row["link_framing"],
            )

    skipped = copy_stats.get("skipped", [])
    if skipped:
        msg += "\n\nCopy skipped:\n" + "\n".join(skipped[:6])

    errors = (
        group_stats.get("group_errors", [])
        + copy_stats.get("errors", [])
    )
    if errors:
        msg += "\n\nIssues:\n" + "\n".join(errors[:8])

    if copy_stats.get("panels", 0) > 0:
        msg += (
            "\n\nPanel framing is in the host model. "
            "Remove the structural link (Manage Links → Remove) "
            "when all panels show OK in Verify.\n\n"
            "Use Sync Panel Selection to UNGROUP for editing, "
            "then regroup when done."
        )
    elif link_framing:
        msg += (
            "\n\nUse Sync Panel Selection to ungroup/regroup panels "
            "after copying framing to the host."
        )

    forms.alert(msg, title="UNIQUBE — Prepare MEP Panels")


def main():
    if not hasattr(pu, "select_panel_pair"):
        forms.alert(
            "panel_utils.py is out of date.\n\n"
            "git pull the full revitPlugins folder, then pyRevit → Reload.",
            title="UNIQUBE",
        )
        return

    rows, panel_elements, link_zones, link_framing = pu.build_panel_rows(doc)
    if not rows:
        forms.alert(
            "No panels with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    framing_only = _is_framing_primary_doc(doc)
    crossing = 0 if framing_only else pu.preview_crossing_mep(
        doc, panel_elements, link_zones
    )
    selected = choose_panels(rows, crossing, mode_host=not framing_only)
    if not selected:
        return

    if framing_only:
        _run_framing_doc(selected)
    else:
        _run_host_doc(selected, panel_elements, link_zones, link_framing)


main()
