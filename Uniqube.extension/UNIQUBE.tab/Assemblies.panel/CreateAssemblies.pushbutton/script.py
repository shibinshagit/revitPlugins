# -*- coding: utf-8 -*-
"""Rebuild assemblies so panel number = BIMSF_Container = assembly name.

The panel number is the real identifier stored in BIMSF_Container (e.g.
ELB-2001 for walls, FT-FloorPanel2001 for floors). This tool guarantees a
clean 1:1 result: exactly one assembly per panel number, named exactly that,
with BIMSF_Container set to match on both the members and the assembly.

How it works (one run):
1. Determine each framing element's panel number. For elements inside an
   existing assembly the number is read from the assembly's BIMSF_Container
   (reliable); for loose framing it is read from the element's own container.
2. Dissolve all existing panel assemblies (members are kept) so the stale or
   mismatched names from earlier runs are cleared.
3. Group all framing by panel number and create ONE assembly per number,
   named exactly the panel number, writing the number into BIMSF_Container on
   the members and the assembly element.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
from Autodesk.Revit.DB import IFailuresPreprocessor, FailureProcessingResult
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


class _SwallowWarnings(IFailuresPreprocessor):
    """Auto-clear warnings (e.g. 'assembly views will be deleted') so the
    disassemble/rebuild transaction is not blocked."""

    def PreprocessFailures(self, accessor):
        try:
            for f in accessor.GetFailureMessages():
                if f.GetSeverity() == DB.FailureSeverity.Warning:
                    accessor.DeleteWarning(f)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def _is_structural_framing(elem):
    cat = elem.Category
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
    except Exception:
        try:
            return cat.Id.IntegerValue == int(
                DB.BuiltInCategory.OST_StructuralFraming
            )
        except Exception:
            return False


def _in_assembly(elem):
    try:
        aid = elem.AssemblyInstanceId
    except Exception:
        return False
    return aid is not None and aid != DB.ElementId.InvalidElementId


def _clean(value):
    if value is None:
        return ""
    return value.lstrip("*").strip()


def _get_container(elem):
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def _set_container(elem, value):
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            if p.AsString() != value:
                p.Set(value)
            return True
        except Exception:
            return False
    return False


def build_groups():
    """Return (groups, dissolve_list).

    groups: {panel_number: [ElementId of framing]}
    dissolve_list: [AssemblyInstance] to disassemble before rebuilding
    """
    groups = {}
    seen = set()
    dissolve_list = []

    assemblies = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    for asm in assemblies:
        member_framing = []
        for mid in asm.GetMemberIds():
            m = doc.GetElement(mid)
            if m is not None and _is_structural_framing(m):
                member_framing.append(m.Id)
        if not member_framing:
            continue

        # Panel number: prefer the assembly's container, else a member's.
        pno = _clean(_get_container(asm))
        if not pno:
            for fid in member_framing:
                pno = _clean(_get_container(doc.GetElement(fid)))
                if pno:
                    break
        if not pno:
            continue

        for fid in member_framing:
            groups.setdefault(pno, []).append(fid)
            seen.add(fid.IntegerValue)
        dissolve_list.append(asm)

    # Loose framing (not in any assembly) keyed by its own container.
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        if el.Id.IntegerValue in seen:
            continue
        if _in_assembly(el):
            continue
        pno = _clean(_get_container(el))
        if pno:
            groups.setdefault(pno, []).append(el.Id)
            seen.add(el.Id.IntegerValue)

    return groups, dissolve_list


def main():
    groups, dissolve_list = build_groups()

    if not groups:
        forms.alert(
            "No framing with a '{}' panel number found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    created = 0
    skipped_single = 0
    dissolved = 0
    dissolve_failed = 0
    rename_failed = 0
    errors = []
    new_assemblies = []  # (AssemblyInstance, panel_number)

    naming_cat = DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)

    # --- Transaction 1: dissolve old assemblies and create new ones ---
    # The assembly type is only realized after this transaction is committed,
    # so renaming has to wait for transaction 2.
    t1 = DB.Transaction(doc, "UNIQUBE: Rebuild Assemblies")
    t1.Start()
    opts = t1.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(_SwallowWarnings())
    opts.SetClearAfterRollback(True)
    t1.SetFailureHandlingOptions(opts)
    try:
        for asm in dissolve_list:
            try:
                asm.Disassemble()
                dissolved += 1
            except Exception as ex:
                dissolve_failed += 1
                if len(errors) < 3:
                    errors.append("Disassemble: {}".format(ex))
        doc.Regenerate()

        for pno in sorted(groups.keys()):
            ids = groups[pno]
            for eid in ids:
                el = doc.GetElement(eid)
                if el is not None:
                    _set_container(el, pno)

            free_ids = []
            for eid in ids:
                el = doc.GetElement(eid)
                if el is not None and not _in_assembly(el):
                    free_ids.append(eid)

            if len(free_ids) < 2:
                skipped_single += 1
                continue

            id_list = List[DB.ElementId]()
            for eid in free_ids:
                id_list.Add(eid)

            try:
                new_asm = DB.AssemblyInstance.Create(doc, id_list, naming_cat)
                new_assemblies.append((new_asm, pno))
                created += 1
            except Exception as ex:
                if len(errors) < 3:
                    errors.append("Create {}: {}".format(pno, ex))
        t1.Commit()
    except Exception as ex:
        t1.RollBack()
        forms.alert("Failed (create step): {}".format(ex), title="UNIQUBE")
        return

    # --- Transaction 2: rename assemblies and set their container ---
    t2 = DB.Transaction(doc, "UNIQUBE: Name Assemblies")
    t2.Start()
    opts2 = t2.GetFailureHandlingOptions()
    opts2.SetFailuresPreprocessor(_SwallowWarnings())
    opts2.SetClearAfterRollback(True)
    t2.SetFailureHandlingOptions(opts2)
    try:
        for new_asm, pno in new_assemblies:
            try:
                new_asm.AssemblyTypeName = pno
            except Exception as ex:
                rename_failed += 1
                if len(errors) < 3:
                    errors.append("Rename {}: {}".format(pno, ex))
            _set_container(new_asm, pno)
        t2.Commit()
    except Exception as ex:
        t2.RollBack()
        forms.alert("Failed (name step): {}".format(ex), title="UNIQUBE")
        return

    msg = (
        "Done.\n\n"
        "Existing assemblies dissolved: {}\n"
        "Assemblies rebuilt (1 per panel number): {}\n"
        "Panel number = BIMSF_Container = assembly name.".format(
            dissolved, created
        )
    )
    if dissolve_failed:
        msg += "\nDissolve failures: {}".format(dissolve_failed)
    if rename_failed:
        msg += "\nRename failures: {}".format(rename_failed)
    if skipped_single:
        msg += "\nPanels with <2 free elements (no assembly): {}".format(
            skipped_single
        )
    if errors:
        msg += "\n\nFirst errors:\n" + "\n".join(errors)
    forms.alert(msg, title="UNIQUBE — Create Assemblies")


main()
