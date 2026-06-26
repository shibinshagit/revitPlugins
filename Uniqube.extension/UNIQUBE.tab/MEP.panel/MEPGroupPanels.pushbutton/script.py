# -*- coding: utf-8 -*-
"""MEP panel grouping with linked framing models (Task 5).

Host MEP model: groups MEP + colors panels, edits link .rvt for panel groups.
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
        parts.append("[ panel: {} ]".format(row["link_framing"]))
    elif row.get("host_framing"):
        parts.append("[ panel: {} ]".format(row["host_framing"]))
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
                "Groups MEP in this model + panel framing in the link .rvt.".format(
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


def _build_catalog(doc):
    panel_elements = pu.map_framing(doc)
    link_zones = pu.map_framing_from_links(doc)
    link_framing = pu.map_link_framing_by_container(doc)
    link_sources = pu.map_framing_link_sources(doc)
    mep_counts = pu.preview_mep_counts(doc, panel_elements, link_zones)
    link_counts = pu.count_link_framing(link_framing)

    all_pids = pu.get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())
    all_pids.update(panel_elements.keys())

    rows = []
    for pid in sorted(all_pids, key=lambda x: pu.panel_display_name(x).lower()):
        host_count = len(pu.merge_framing_for_panel(panel_elements, pid))
        link_count = sum(
            link_counts.get(k, 0)
            for k in link_counts
            if pu.panel_ids_match(k, pid)
        )
        in_link = pid in link_zones or link_count > 0
        if host_count and in_link:
            source = "host + link"
        elif in_link:
            source = "link"
        else:
            source = "host"
        rows.append({
            "pid": pid,
            "display": pu.panel_display_name(pid),
            "source": source,
            "mep_count": sum(
                mep_counts.get(k, 0)
                for k in mep_counts
                if pu.panel_ids_match(k, pid)
            ),
            "link_name": link_sources.get(pid, ""),
            "host_framing": host_count,
            "link_framing": link_count,
        })
    return rows, panel_elements, link_zones, link_framing


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


def _offer_select_one(selected, link_framing):
    """Let user pick ONE panel — selects its MEP group + panel framing together."""
    if not selected or len(selected) < 1:
        return 0
    display_map = {pu.panel_display_name(p): p for p in selected}
    options = sorted(display_map.keys())
    pick = forms.SelectFromList.show(
        options,
        title="UNIQUBE — Select one panel (MEP + panel together)",
        button_name="Select",
    )
    if not pick:
        return 0
    pid = display_map.get(pick)
    if not pid:
        return 0
    try:
        return pu.select_panel_pair(uidoc, doc, pid, link_framing)
    except Exception as ex:
        logger.debug("select_panel_pair failed: %s", ex)
        return 0


def _run_framing_doc(selected):
    stats = {"link_groups": 0, "errors": []}
    with revit.Transaction("UNIQUBE: Group Panel Framing"):
        pu._delete_groups_in_doc(doc, selected)
        stats = pu.group_framing_in_active_doc(doc, selected)
    msg = (
        "Done (framing model).\n\n"
        "Panel groups created: {}\n\n"
        "Next: open the MEP host model and run this button "
        "there to group MEP for the same panels.".format(
            stats.get("link_groups", 0)
        )
    )
    if stats.get("errors"):
        msg += "\n\nIssues:\n" + "\n".join(stats["errors"][:5])
    forms.alert(msg, title="UNIQUBE — MEP Group Panels")
    return stats


def _run_host_doc(selected, panel_elements, link_zones, link_framing):
    stats = {}
    link_stats = {"link_groups": 0, "link_files": 0, "reloaded": 0, "errors": []}

    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    try:
        with revit.Transaction("UNIQUBE: MEP Group Panels"):
            pu._delete_groups_in_doc(doc, selected)
            stats = pu.combine_panels_group_color(
                doc,
                view,
                selected,
                panel_elements,
                link_zones,
                link_framing=link_framing,
                tag_mep=True,
            )
    except Exception as ex:
        forms.alert("Host grouping failed:\n{}".format(ex), title="UNIQUBE")
        return

    try:
        link_stats = pu.group_link_panel_framing(
            app, doc, selected, link_framing
        )
    except Exception as ex:
        link_stats["errors"].append("Link grouping: {}".format(ex))

    msg = (
        "Done.\n\n"
        "Host MEP groups: {}\n"
        "Link panel groups: {}\n"
        "Link files edited: {}\n"
        "Links reloaded: {}\n"
        "Crossing MEP (red): {}".format(
            stats.get("groups", 0),
            link_stats.get("link_groups", 0),
            link_stats.get("link_files", 0),
            link_stats.get("reloaded", 0),
            stats.get("crossing_count", 0),
        )
    )
    errors = stats.get("group_errors", []) + link_stats.get("errors", [])
    if errors:
        msg += "\n\nIssues:\n" + "\n".join(errors[:6])
    if link_stats.get("link_groups", 0) == 0 and link_framing:
        msg += (
            "\n\nIf link groups = 0: open Willow Street_Framing.rvt "
            "directly (not as link), run this same button there to "
            "group panel framing, then return to the MEP model."
        )
    forms.alert(msg, title="UNIQUBE — MEP Group Panels")

    _offer_select_one(selected, link_framing)


def main():
    if not hasattr(pu, "select_panel_pair"):
        forms.alert(
            "panel_utils.py is out of date.\n\n"
            "git pull the full revitPlugins folder, then pyRevit → Reload.",
            title="UNIQUBE",
        )
        return

    rows, panel_elements, link_zones, link_framing = _build_catalog(doc)
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
