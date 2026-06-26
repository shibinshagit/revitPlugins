# -*- coding: utf-8 -*-
"""MEP panel grouping with linked framing models (Task 5)."""
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
    elif row.get("source") == "host + link":
        parts.append("(host + link)")
    return "  ".join(parts)


class MEPPanelSelector(forms.WPFWindow):
    def __init__(self, rows, crossing_count):
        forms.WPFWindow.__init__(self, "SelectMEPPanels.xaml")
        self._checks = []
        self.selected = None

        link_panels = sum(1 for r in rows if "link" in r.get("source", ""))
        self.summary_text.Text = (
            "{0} panel(s). {1} from link(s). {2} crossing MEP (red). "
            "Creates host MEP group + panel group inside each link .rvt "
            "(same name, e.g. ELB-1001).".format(
                len(rows), link_panels, crossing_count
            )
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
            if row.get("source") == "link":
                cb.Foreground = Brushes.DarkBlue
            elif not has_content:
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


def choose_panels(rows, crossing_count):
    if not rows:
        return None
    try:
        win = MEPPanelSelector(rows, crossing_count)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed: %s", ex)
        return pu.choose_panels([r["pid"] for r in rows])


def _build_catalog(doc):
    """Build panel list without relying on build_panel_catalog return shape."""
    panel_elements = pu.map_framing(doc)
    link_zones = pu.map_framing_from_links(doc)
    link_framing = pu.map_link_framing_by_container(doc)
    link_sources = pu.map_framing_link_sources(doc)
    mep_counts = pu.preview_mep_counts(doc, panel_elements, link_zones)
    link_counts = pu.count_link_framing(link_framing)

    all_pids = pu.get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())

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


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    required = (
        "combine_panels_group_color",
        "group_link_panel_framing",
        "panel_ids_match",
    )
    missing = [n for n in required if not hasattr(pu, n)]
    if missing:
        forms.alert(
            "panel_utils.py is out of date (missing: {}).\n\n"
            "git pull the full revitPlugins folder, then pyRevit → Reload.".format(
                ", ".join(missing)
            ),
            title="UNIQUBE — MEP Group Panels",
        )
        return

    rows, panel_elements, link_zones, link_framing = _build_catalog(doc)
    if not rows:
        forms.alert(
            "No panels with '{}' in host or links.".format(pu.PARAM_NAME),
            title="UNIQUBE — MEP Group Panels",
        )
        return

    crossing = pu.preview_crossing_mep(doc, panel_elements, link_zones)
    selected = choose_panels(rows, crossing)
    if not selected:
        return

    stats = {}
    link_stats = {"link_groups": 0, "link_files": 0, "reloaded": 0, "errors": []}

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
        forms.alert(
            "Host grouping failed:\n{}".format(ex),
            title="UNIQUBE — MEP Group Panels",
        )
        logger.debug("Host grouping failed: %s", ex)
        return

    try:
        link_stats = pu.group_link_panel_framing(
            app, doc, selected, link_framing
        )
    except Exception as ex:
        link_stats["errors"].append("Link grouping: {}".format(ex))
        logger.debug("Link grouping failed: %s", ex)

    selected_count = 0
    try:
        selected_count = pu.select_panels_in_view(
            uidoc, doc, selected, link_framing
        )
    except Exception as ex:
        logger.debug("Selection failed: %s", ex)

    msg = (
        "Done.\n\n"
        "Panels processed: {}\n"
        "Host groups (MEP): {}\n"
        "Host MEP tagged: {}\n"
        "Link groups (panel in .rvt): {}\n"
        "Link files edited: {}\n"
        "Links reloaded: {}\n"
        "Panel framing colored: {}\n"
        "Crossing MEP (red): {}\n"
        "Groups selected: {}".format(
            len(selected),
            stats.get("groups", 0),
            stats.get("mep_tagged", 0),
            link_stats.get("link_groups", 0),
            link_stats.get("link_files", 0),
            link_stats.get("reloaded", 0),
            stats.get("link_framing_colored", 0),
            stats.get("crossing_count", 0),
            selected_count,
        )
    )
    errors = stats.get("group_errors", []) + link_stats.get("errors", [])
    if errors:
        msg += "\n\nIssues:\n" + "\n".join(errors[:6])
    if link_stats.get("link_groups", 0) == 0 and link_framing:
        msg += (
            "\n\nTip: Link groups need a saved local path. "
            "Save Willow Street_Framing.rvt locally, close it if open "
            "separately, then run again."
        )

    forms.alert(msg, title="UNIQUBE — MEP Group Panels")


main()
