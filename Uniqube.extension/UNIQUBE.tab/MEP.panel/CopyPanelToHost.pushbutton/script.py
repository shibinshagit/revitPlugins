# -*- coding: utf-8 -*-
"""Copy linked panel framing into the MEP host, explode, regroup with MEP.

Run after MEP Group Panels so MEP is already grouped/tagged. Copies structural
framing from the link by BIMSF_Container, ungroups copies, then rebuilds one
host Revit group per panel (framing + MEP). The structural link can then be
removed.
"""
from pyrevit import revit, DB, forms, script
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
from System.Windows import Thickness
import panel_utils as pu

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


def _row_label(row):
    parts = [row["display"]]
    if row.get("link_framing"):
        parts.append("[ link: {} ]".format(row["link_framing"]))
    if row.get("host_framing"):
        parts.append("[ host: {} ]".format(row["host_framing"]))
    if row.get("mep_count"):
        parts.append("[ MEP: {} ]".format(row["mep_count"]))
    if row.get("link_name"):
        parts.append("(link: {})".format(row["link_name"]))
    return "  ".join(parts)


class CopyPanelSelector(forms.WPFWindow):
    def __init__(self, rows):
        forms.WPFWindow.__init__(self, "SelectCopyPanels.xaml")
        self._checks = []
        self.selected = None

        copyable = sum(
            1 for r in rows
            if r.get("link_framing", 0) > 0 and r.get("host_framing", 0) == 0
        )
        self.summary_text.Text = (
            "{0} panel(s). {1} ready to copy from link (no host framing yet). "
            "Copies studs/tracks, explodes groups, regroups panel + MEP in host."
            .format(len(rows), copyable)
        )

        for row in rows:
            cb = CheckBox()
            cb.Content = _row_label(row)
            cb.Margin = Thickness(2, 3, 2, 3)
            ready = (
                row.get("link_framing", 0) > 0
                and row.get("host_framing", 0) == 0
            )
            cb.IsChecked = ready
            if not ready:
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


def choose_panels(rows):
    if not rows:
        return None
    try:
        win = CopyPanelSelector(rows)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed: %s", ex)
        return pu.choose_panels([r["pid"] for r in rows])


def main():
    if not hasattr(pu, "copy_panel_framing_to_host"):
        forms.alert(
            "panel_utils.py is out of date.\n\n"
            "git pull the full revitPlugins folder, then pyRevit → Reload.",
            title="UNIQUBE",
        )
        return

    if doc.IsLinked:
        forms.alert("Open the MEP host model, not a linked file.", title="UNIQUBE")
        return

    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    rows, _, _, link_framing = pu.build_panel_rows(doc)
    if not rows:
        forms.alert(
            "No panels with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    if not link_framing:
        forms.alert(
            "No linked panel framing found.\n\n"
            "Load the structural framing link, then run MEP Group Panels first.",
            title="UNIQUBE",
        )
        return

    selected = choose_panels(rows)
    if not selected:
        return

    stats = {}
    try:
        with revit.Transaction("UNIQUBE: Copy Panel to Host"):
            stats = pu.copy_panel_framing_to_host(
                doc, view, selected, link_framing, regroup=False
            )
    except Exception as ex:
        forms.alert("Copy failed:\n{}".format(ex), title="UNIQUBE")
        return

    copied_pids = stats.get("copied_pids", [])
    if stats.get("panels", 0) > 0 and copied_pids:
        try:
            with revit.Transaction("UNIQUBE: Regroup Panel + MEP"):
                regroup = pu.regroup_panels_in_host(
                    doc, view, copied_pids, tag_mep=True
                )
                stats["host_groups"] = regroup.get("groups", 0)
                stats.setdefault("errors", []).extend(
                    regroup.get("group_errors", [])
                )
        except Exception as ex:
            stats.setdefault("errors", []).append("Regroup: {}".format(ex))

    stats["verify"] = pu.verify_panel_copy(doc, selected)

    msg = (
        "Done.\n\n"
        "Panels copied: {}\n"
        "Framing members copied: {}\n"
        "Groups exploded: {}\n"
        "Host groups (panel + MEP): {}".format(
            stats.get("panels", 0),
            stats.get("members_copied", 0),
            stats.get("groups_exploded", 0),
            stats.get("host_groups", 0),
        )
    )
    verify = stats.get("verify", [])
    if verify:
        msg += "\n\nVerify:"
        for row in verify[:8]:
            msg += "\n  {} — {} (host: {}, link: {})".format(
                row["panel"],
                row["status"],
                row["host_framing"],
                row["link_framing"],
            )
    skipped = stats.get("skipped", [])
    if skipped:
        msg += "\n\nSkipped:\n" + "\n".join(skipped[:8])
    errors = stats.get("errors", [])
    if errors:
        msg += "\n\nIssues:\n" + "\n".join(errors[:6])

    if stats.get("panels", 0) > 0:
        msg += (
            "\n\nPanel framing is now in the host model and grouped with MEP. "
            "You can remove the structural link (Manage Links → Remove)."
        )
    forms.alert(msg, title="UNIQUBE — Copy Panel to Host")

    try:
        import panel_selection_sync as pss
        pss.enable(uidoc)
    except Exception:
        pass


main()
