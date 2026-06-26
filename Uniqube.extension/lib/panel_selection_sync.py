# -*- coding: utf-8 -*-
"""Toggle host panel groups — ungroup for editing, regroup when done.

When UNGROUPED there is no background selection logic — normal Revit only.
"""
import __main__

from pyrevit import script

import panel_utils as pu

_ENV_UNGROUPED = "uniqube_panel_ungrouped"
_STORE_PIDS = "uniqube_panel_sync_pids"
_HANDLER_LIST_KEY = "_uniqube_idling_handler_list"


def is_ungrouped(uidoc=None):
    return bool(script.get_envvar(_ENV_UNGROUPED))


def is_enabled(uidoc=None):
    return is_ungrouped(uidoc)


def is_grouped(uidoc=None):
    return not is_ungrouped(uidoc)


def mark_grouped():
    script.set_envvar(_ENV_UNGROUPED, False)
    script.set_envvar("uniqube_panel_sync_active", False)


def mark_ungrouped():
    script.set_envvar(_ENV_UNGROUPED, True)
    script.set_envvar("uniqube_panel_sync_active", False)


def _handler_list():
    if not hasattr(__main__, _HANDLER_LIST_KEY):
        setattr(__main__, _HANDLER_LIST_KEY, [])
    return getattr(__main__, _HANDLER_LIST_KEY)


def purge_legacy_idling(uiapp):
    """Remove all tracked UNIQUBE Idling handlers (legacy auto-select)."""
    removed = 0

    for handler in list(_handler_list()):
        for _ in range(10):
            try:
                uiapp.Idling -= handler
                removed += 1
            except Exception:
                break
    setattr(__main__, _HANDLER_LIST_KEY, [])

    for attr in (
        "_idling_handler",
        "_uniqube_panel_sync_handler",
        "_uniqube_sync_handler",
        "_guard_handler",
    ):
        handler = getattr(__main__, attr, None)
        if handler is not None:
            for _ in range(10):
                try:
                    uiapp.Idling -= handler
                    removed += 1
                except Exception:
                    break
            try:
                delattr(__main__, attr)
            except Exception:
                setattr(__main__, attr, None)

    try:
        import panel_selection_sync as self_module
        for attr in ("_idling_handler", "_guard_handler"):
            handler = getattr(self_module, attr, None)
            if handler is not None:
                for _ in range(10):
                    try:
                        uiapp.Idling -= handler
                        removed += 1
                    except Exception:
                        break
                setattr(self_module, attr, None)
    except Exception:
        pass

    return removed


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
    """Ungroup host panels — no background selection behavior."""
    doc = uidoc.Document
    uiapp = uidoc.Application
    purge_legacy_idling(uiapp)
    panel_ids = _load_panel_ids(doc)
    stats = pu.ungroup_panels_in_host(doc, panel_ids)
    _save_panel_ids(stats.get("panel_ids", panel_ids))
    mark_ungrouped()
    return stats


def regroup_panels(uidoc, view=None):
    """Rebuild host panel groups (framing + MEP)."""
    doc = uidoc.Document
    uiapp = uidoc.Application
    purge_legacy_idling(uiapp)
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
    if is_ungrouped(uidoc):
        regroup_panels(uidoc, view)
        return False
    ungroup_panels(uidoc, view)
    return True


def enable(uidoc):
    mark_grouped()
    if uidoc is not None:
        purge_legacy_idling(uidoc.Application)
    return False


def disable(uidoc):
    mark_ungrouped()
    if uidoc is not None:
        purge_legacy_idling(uidoc.Application)
    return False
