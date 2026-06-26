# -*- coding: utf-8 -*-
"""Toggle host panel groups — ungroup for editing, regroup when done.

After Prepare MEP Panels (link removed), each panel is a Revit group.
Sync Panel Selection toggles:

  GROUPED (default) — panel + MEP move/select as one unit
  UNGROUPED         — individual studs, pipes, fittings can be selected

State is stored with pyRevit script env vars (shared across ribbon buttons).
"""
from pyrevit import script

import panel_utils as pu

_ENV_UNGROUPED = "uniqube_panel_ungrouped"
_STORE_PIDS = "uniqube_panel_sync_pids"


def is_ungrouped(uidoc=None):
    """True when panels are ungrouped (individual element selection)."""
    return bool(script.get_envvar(_ENV_UNGROUPED))


def is_enabled(uidoc=None):
    """Alias for is_ungrouped (legacy name)."""
    return is_ungrouped(uidoc)


def is_grouped(uidoc=None):
    return not is_ungrouped(uidoc)


def mark_grouped():
    script.set_envvar(_ENV_UNGROUPED, False)


def mark_ungrouped():
    script.set_envvar(_ENV_UNGROUPED, True)


def _load_panel_ids(doc):
    try:
        stored = script.load_data(_STORE_PIDS, this_project=True)
        if stored:
            return list(stored)
    except Exception:
        pass
    return pu.discover_host_panel_ids(doc)


def _save_panel_ids(panel_ids):
    try:
        script.store_data(_STORE_PIDS, list(panel_ids), this_project=True)
    except Exception:
        pass


def ungroup_panels(uidoc, view=None):
    """Ungroup all host panel groups for individual selection."""
    doc = uidoc.Document
    panel_ids = _load_panel_ids(doc)
    stats = pu.ungroup_panels_in_host(doc, panel_ids)
    _save_panel_ids(stats.get("panel_ids", panel_ids))
    mark_ungrouped()
    return stats


def regroup_panels(uidoc, view=None):
    """Rebuild host panel groups (framing + MEP) like Prepare MEP Panels."""
    doc = uidoc.Document
    if view is None:
        view = doc.ActiveView
    panel_ids = _load_panel_ids(doc)
    if not panel_ids:
        panel_ids = pu.discover_host_panel_ids(doc)
    stats = pu.regroup_panels_in_host(doc, view, panel_ids, tag_mep=False)
    _save_panel_ids(panel_ids)
    mark_grouped()
    return stats


def toggle(uidoc, view=None):
    """Toggle grouped ↔ ungrouped. Returns True if now ungrouped."""
    if is_ungrouped(uidoc):
        regroup_panels(uidoc, view)
        return False
    ungroup_panels(uidoc, view)
    return True


# Legacy no-ops — Prepare MEP Panels may call these.
def enable(uidoc):
    mark_grouped()
    return False


def disable(uidoc):
    mark_grouped()
    return True
