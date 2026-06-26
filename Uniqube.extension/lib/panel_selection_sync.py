# -*- coding: utf-8 -*-
"""Toggle host panel groups — ungroup for editing, regroup when done.

When UNGROUPED, a selection guard blocks legacy auto-select handlers from
replacing a single MEP pick with bulk panel framing selection.
"""
import __main__

from System import EventHandler
from System.Collections.Generic import List
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB, script

import panel_utils as pu

_ENV_UNGROUPED = "uniqube_panel_ungrouped"
_STORE_PIDS = "uniqube_panel_sync_pids"
_HANDLER_LIST_KEY = "_uniqube_idling_handler_list"

_guard_handler = None
_guard_uiapp_id = None
_guard_state = {"snapshot": None}
_restoring = False


def is_ungrouped(uidoc=None):
    return bool(script.get_envvar(_ENV_UNGROUPED))


def is_enabled(uidoc=None):
    return is_ungrouped(uidoc)


def is_grouped(uidoc=None):
    return not is_ungrouped(uidoc)


def mark_grouped():
    script.set_envvar(_ENV_UNGROUPED, False)


def mark_ungrouped():
    script.set_envvar(_ENV_UNGROUPED, True)


def _handler_list():
    if not hasattr(__main__, _HANDLER_LIST_KEY):
        setattr(__main__, _HANDLER_LIST_KEY, [])
    return getattr(__main__, _HANDLER_LIST_KEY)


def _track_handler(handler):
    handlers = _handler_list()
    if handler not in handlers:
        handlers.append(handler)


def _uiapp_key(uiapp):
    return uiapp.GetHashCode()


def _classify_selection(uidoc):
    """Return (framing_count, mep_count) for current selection."""
    doc = uidoc.Document
    framing = 0
    mep = 0
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if el is None or el.Category is None:
            continue
        bic = el.Category.BuiltInCategory
        if bic == DB.BuiltInCategory.OST_StructuralFraming:
            framing += 1
        elif bic in pu.MEP_CATS:
            mep += 1

    invalid = DB.ElementId.InvalidElementId
    for ref in uidoc.Selection.GetReferences():
        try:
            link_elem_id = ref.LinkedElementId
        except Exception:
            link_elem_id = invalid
        if link_elem_id is None or link_elem_id == invalid:
            continue
        link_inst = doc.GetElement(ref.ElementId)
        if link_inst is None:
            continue
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        elem = link_doc.GetElement(link_elem_id)
        if elem is None or elem.Category is None:
            continue
        bic = elem.Category.BuiltInCategory
        if bic == DB.BuiltInCategory.OST_StructuralFraming:
            framing += 1
        elif bic in pu.MEP_CATS:
            mep += 1
    return framing, mep


def _capture_selection(uidoc):
    ids = List[DB.ElementId]()
    for eid in uidoc.Selection.GetElementIds():
        ids.Add(eid)
    refs = []
    try:
        for ref in uidoc.Selection.GetReferences():
            refs.append(ref)
    except Exception:
        pass
    return {"ids": ids, "refs": refs}


def _restore_selection(uidoc, snapshot):
    global _restoring
    if snapshot is None:
        return
    _restoring = True
    try:
        if snapshot.get("refs"):
            ref_list = List[DB.Reference]()
            for ref in snapshot["refs"]:
                ref_list.Add(ref)
            uidoc.Selection.SetReferences(ref_list)
        elif snapshot.get("ids") and snapshot["ids"].Count > 0:
            uidoc.Selection.SetElementIds(snapshot["ids"])
    except Exception:
        pass
    finally:
        _restoring = False


def _classify_snapshot(uidoc, snapshot):
    doc = uidoc.Document
    framing = 0
    mep = 0
    ids = snapshot.get("ids")
    if ids is not None:
        for i in range(ids.Count):
            el = doc.GetElement(ids[i])
            if el is None or el.Category is None:
                continue
            bic = el.Category.BuiltInCategory
            if bic == DB.BuiltInCategory.OST_StructuralFraming:
                framing += 1
            elif bic in pu.MEP_CATS:
                mep += 1
    invalid = DB.ElementId.InvalidElementId
    for ref in snapshot.get("refs") or []:
        try:
            link_elem_id = ref.LinkedElementId
        except Exception:
            link_elem_id = invalid
        if link_elem_id is None or link_elem_id == invalid:
            el = doc.GetElement(ref.ElementId)
            if el is None or el.Category is None:
                continue
            bic = el.Category.BuiltInCategory
            if bic == DB.BuiltInCategory.OST_StructuralFraming:
                framing += 1
            elif bic in pu.MEP_CATS:
                mep += 1
            continue
        link_inst = doc.GetElement(ref.ElementId)
        if link_inst is None:
            continue
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        elem = link_doc.GetElement(link_elem_id)
        if elem is None or elem.Category is None:
            continue
        bic = elem.Category.BuiltInCategory
        if bic == DB.BuiltInCategory.OST_StructuralFraming:
            framing += 1
        elif bic in pu.MEP_CATS:
            mep += 1
    return framing, mep


def _looks_like_auto_hijack(uidoc):
    """True when selection jumped to bulk framing with no MEP."""
    framing, mep = _classify_selection(uidoc)
    if mep > 0 or framing < 2:
        return False
    snap = _guard_state.get("snapshot")
    if snap is None:
        return False
    prev_framing, prev_mep = _classify_snapshot(uidoc, snap)
    if prev_mep > 0:
        return True
    ids = snap.get("ids")
    if ids is not None and ids.Count <= 1 and framing >= 2:
        return True
    return False


def _on_guard_idling(sender, args):
    global _restoring
    if _restoring or not is_ungrouped():
        return
    uidoc = sender.ActiveUIDocument
    if uidoc is None:
        return

    if _looks_like_auto_hijack(uidoc):
        _restore_selection(uidoc, _guard_state.get("snapshot"))
        return

    framing, mep = _classify_selection(uidoc)
    if not (framing >= 2 and mep == 0):
        _guard_state["snapshot"] = _capture_selection(uidoc)


def _attach_guard(uiapp):
    global _guard_handler, _guard_uiapp_id
    key = _uiapp_key(uiapp)
    if _guard_handler is not None and _guard_uiapp_id == key:
        return
    _detach_guard(uiapp)
    _guard_handler = EventHandler[IdlingEventArgs](_on_guard_idling)
    uiapp.Idling += _guard_handler
    _guard_uiapp_id = key
    _track_handler(_guard_handler)
    _guard_state["snapshot"] = None


def _detach_guard(uiapp):
    global _guard_handler, _guard_uiapp_id
    if _guard_handler is not None:
        for _ in range(10):
            try:
                uiapp.Idling -= _guard_handler
            except Exception:
                break
    _guard_handler = None
    _guard_uiapp_id = None
    _guard_state["snapshot"] = None


def ensure_guard(uidoc):
    """Attach selection guard when panels are ungrouped in the model."""
    if uidoc is None:
        return
    doc = uidoc.Document
    if not pu.discover_host_panel_ids(doc):
        return
    if pu.count_panel_groups(doc) == 0:
        mark_ungrouped()
        _attach_guard(uidoc.Application)
    elif is_ungrouped(uidoc):
        _attach_guard(uidoc.Application)


def purge_legacy_idling(uiapp):
    """Remove legacy auto-select Idling handlers; keep guard if ungrouped."""
    removed = 0
    guard = _guard_handler

    for handler in list(_handler_list()):
        if handler is guard:
            continue
        for _ in range(10):
            try:
                uiapp.Idling -= handler
                removed += 1
            except Exception:
                break
    setattr(__main__, _HANDLER_LIST_KEY, [])
    if guard is not None:
        _track_handler(guard)

    for attr in (
        "_idling_handler",
        "_uniqube_panel_sync_handler",
        "_uniqube_sync_handler",
    ):
        handler = getattr(__main__, attr, None)
        if handler is not None and handler is not guard:
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
    """Ungroup all host panel groups for individual selection."""
    doc = uidoc.Document
    uiapp = uidoc.Application
    purge_legacy_idling(uiapp)
    panel_ids = _load_panel_ids(doc)
    stats = pu.ungroup_panels_in_host(doc, panel_ids)
    _save_panel_ids(stats.get("panel_ids", panel_ids))
    mark_ungrouped()
    _attach_guard(uiapp)
    return stats


def regroup_panels(uidoc, view=None):
    """Rebuild host panel groups (framing + MEP) like Prepare MEP Panels."""
    doc = uidoc.Document
    uiapp = uidoc.Application
    _detach_guard(uiapp)
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
        _detach_guard(uidoc.Application)
        purge_legacy_idling(uidoc.Application)
    return False


def disable(uidoc):
    mark_grouped()
    if uidoc is not None:
        _detach_guard(uidoc.Application)
        purge_legacy_idling(uidoc.Application)
    return True
