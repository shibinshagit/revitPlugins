# -*- coding: utf-8 -*-
"""MEP panel grouping with linked framing models (Task 5).

Lists panels from linked/host framing (BIMSF_Container), groups host
MEP + any host framing, creates matching panel groups inside each link
.rvt file, applies Panel Combine Color highlighting, and selects panel
+ MEP together in the view.
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
    if row["mep_count"]:
        parts.append("[ MEP: {} ]".format(row["mep_count"]))
    if row.get("link_framing"):
        parts.append("[ panel: {} ]".format(row["link_framing"]))
    elif row.get("host_framing"):
        parts.append("[ panel: {} ]".format(row["host_framing"]))
    if row["source"] == "link" and row["link_name"]:
        parts.append("(link: {})".format(row["link_name"]))
    elif row["source"] == "host + link":
        parts.append("(host + link)")
    return "  ".join(parts)


class MEPPanelSelector(forms.WPFWindow):
    """Checkbox list of panels with MEP + framing counts."""

    def __init__(self, rows, crossing_count):
        forms.WPFWindow.__init__(self, "SelectMEPPanels.xaml")
        self._checks = []
        self.selected = None

        link_panels = sum(1 for r in rows if "link" in r["source"])
        self.summary_text.Text = (
            "{0} panel(s) found — {1} from linked model(s). "
            "{2} host MEP element(s) cross panel boundaries "
            "(shown in red). Panel framing is grouped inside each "
            "link file; MEP is grouped in the host.".format(
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
            if row["source"] == "link":
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


def _delete_existing_groups(selected):
    pu._delete_groups_in_doc(doc, selected)


def _load_catalog(doc):
    """Load panel catalog; tolerate older panel_utils on partial sync."""
    result = pu.build_panel_catalog(doc)
    rows = result[0]
    panel_elements = result[1]
    link_zones = result[2]
    if len(result) > 3:
        link_framing = result[3]
    else:
        link_framing = pu.map_link_framing_by_container(doc)
    counts = pu.count_link_framing(link_framing)
    for row in rows:
        if "link_framing" not in row:
            row["link_framing"] = counts.get(row["pid"], 0)
    return rows, panel_elements, link_zones, link_framing


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    if not hasattr(pu, "group_link_panel_framing"):
        forms.alert(
            "panel_utils.py is out of date on this machine.\n\n"
            "Run git pull on the full revitPlugins folder, then "
            "pyRevit → Reload.",
            title="UNIQUBE — MEP Group Panels",
        )
        return

    rows, panel_elements, link_zones, link_framing = _load_catalog(doc)
    if not rows:
        forms.alert(
            "No panels found.\n\n"
            "Structural framing with '{}' must exist in the host "
            "model or a linked model (e.g. Willow Street_Framing.rvt).".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE — MEP Group Panels",
        )
        return

    crossing = pu.preview_crossing_mep(doc, panel_elements, link_zones)
    selected = choose_panels(rows, crossing)
    if not selected:
        return

    with revit.Transaction("UNIQUBE: MEP Group Panels"):
        _delete_existing_groups(selected)
        stats = pu.combine_panels_group_color(
            doc,
            view,
            selected,
            panel_elements,
            link_zones,
            link_framing=link_framing,
            tag_mep=True,
        )

    link_stats = pu.group_link_panel_framing(
        app, doc, selected, link_framing
    )

    selected_count = 0
    try:
        selected_count = pu.select_panels_in_view(
            uidoc, doc, selected, link_framing
        )
    except Exception as ex:
        logger.debug("Selection failed: %s", ex)

    msg = (
        "Done.\n\n"
        "Panels selected: {}\n"
        "Host groups (MEP + host framing): {}\n"
        "Link groups (panel framing in .rvt): {}\n"
        "Link files updated: {}\n"
        "Links reloaded in host: {}\n"
        "Host MEP tagged: {}\n"
        "Linked panel framing colored: {}\n"
        "Crossing elements (red): {}\n"
        "Elements selected in view: {}".format(
            len(selected),
            stats["groups"],
            link_stats["link_groups"],
            link_stats["link_files"],
            link_stats["reloaded"],
            stats["mep_tagged"],
            stats["link_framing_colored"],
            stats["crossing_count"],
            selected_count,
        )
    )
    if link_stats["errors"]:
        msg += "\n\nNotes:\n" + "\n".join(link_stats["errors"][:5])

    forms.alert(msg, title="UNIQUBE — MEP Group Panels")


main()
