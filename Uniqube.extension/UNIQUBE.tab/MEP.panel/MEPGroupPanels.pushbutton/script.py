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
                "{0} panel(s). {1} panel-crossing pipes/fittings (red). "
                "Auto-fills BIMSF_Container, copies panel framing from link, "
                "assembles panel + MEP in host, and turns on selection sync.".format(
                    len(rows), crossing_count
                )
            )
        else:
            self.summary_text.Text = (
                "{0} panel(s) in this framing model. "
                "Creates one Revit assembly per panel.".format(len(rows))
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


def _disable_sync():
    try:
        import panel_selection_sync as pss
        if pss.is_enabled():
            pss.disable(uidoc)
            return True
    except Exception:
        pass
    return False


def _run_framing_doc(selected):
    stats = {"link_groups": 0, "errors": []}
    with revit.Transaction("UNIQUBE: Assemble Panel Framing"):
        stats = pu.group_framing_in_active_doc(doc, selected)
    msg = (
        "Done (framing model).\n\n"
        "Panel assemblies created: {}\n\n"
        "Next: open the MEP host model and run Prepare MEP Panels "
        "there for the same panels.".format(
            stats.get("link_groups", 0)
        )
    )
    if stats.get("errors"):
        msg += "\n\nIssues:\n" + "\n".join(stats["errors"][:5])
    forms.alert(msg, title="UNIQUBE — Prepare MEP Panels")
    return stats


def _run_host_doc(selected, panel_elements, link_zones, link_framing):
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    _disable_sync()

    tag_stats = {}
    copy_stats = {
        "panels": 0,
        "members_copied": 0,
        "groups_exploded": 0,
        "host_groups": 0,
        "skipped": [],
        "errors": [],
        "copied_pids": [],
    }
    group_stats = {}

    tg = DB.TransactionGroup(doc, "UNIQUBE: Prepare MEP Panels")
    tg.Start()
    try:
        with revit.Transaction("UNIQUBE: Fill BIMSF_Container"):
            tag_stats = pu.fill_mep_bimsf_containers(
                doc, panel_elements, link_zones, selected=selected
            )

        with revit.Transaction("UNIQUBE: Reload Structural Links"):
            pu._ensure_framing_links_loaded(doc)

        with revit.Transaction("UNIQUBE: Copy Panel to Host"):
            link_framing = pu.map_link_framing_by_container(doc)
            if not link_framing:
                copy_stats["skipped"].append(
                    "(structural link not loaded — reload link and retry)"
                )
            elif hasattr(pu, "copy_panel_framing_to_host"):
                copy_stats = pu.copy_panel_framing_to_host(
                    doc, view, selected, link_framing, regroup=False
                )
            else:
                copy_stats["errors"].append(
                    "panel_utils.py out of date — git pull"
                )

        panel_elements = pu.map_framing(doc)
        link_zones = pu.map_framing_from_links(doc)
        link_framing = pu.map_link_framing_by_container(doc)

        with revit.Transaction("UNIQUBE: Assemble Panel + MEP"):
            group_stats = pu.combine_panels_group_color(
                doc,
                view,
                selected,
                panel_elements,
                link_zones,
                link_framing=link_framing,
                tag_mep=True,
            )
            copy_stats["host_groups"] = group_stats.get("groups", 0)

        tg.Assimilate()
    except Exception as ex:
        if tg.HasStarted() and not tg.HasEnded():
            tg.RollBack()
        forms.alert("Prepare MEP Panels failed:\n{}".format(ex), title="UNIQUBE")
        return

    if hasattr(pu, "verify_panel_copy"):
        copy_stats["verify"] = pu.verify_panel_copy(doc, selected)

    sync_on = False
    try:
        import panel_selection_sync as pss
        sync_on = pss.enable(uidoc)
    except Exception as ex:
        logger.debug("sync enable failed: %s", ex)

    msg = (
        "Done.\n\n"
        "1. BIMSF_Container filled: {0} new, {1} updated\n"
        "2. Via connected runs: {2} | Resolved: {3} | Crossing cleared: {4} | Outside cleared: {10}\n"
        "3. Panels copied to host: {5}\n"
        "4. Framing members copied: {6}\n"
        "5. Host assemblies (panel + MEP): {7}\n"
        "6. Panel crossings (red pipes/fittings): {8}\n"
        "7. Selection sync: {9}".format(
            tag_stats.get("tagged", 0),
            tag_stats.get("updated", 0),
            tag_stats.get("propagated", 0),
            tag_stats.get("resolved", 0),
            tag_stats.get("cleared_crossing", 0),
            copy_stats.get("panels", 0),
            copy_stats.get("members_copied", 0),
            copy_stats.get("host_groups", 0),
            group_stats.get("crossing_count", 0),
            "ON" if sync_on else "OFF",
            tag_stats.get("cleared_outside", 0),
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
            "You can remove the structural link (Manage Links → Remove) "
            "when Verify shows OK for each panel."
        )
    elif link_framing:
        msg += (
            "\n\nCopy failed — keep the structural link loaded and retry. "
            "Do not remove the link until Verify shows host framing."
        )

    forms.alert(msg, title="UNIQUBE — Prepare MEP Panels")


def main():
    if not hasattr(pu, "copy_panel_framing_to_host"):
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
