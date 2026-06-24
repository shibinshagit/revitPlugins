# -*- coding: utf-8 -*-
"""Make BIMSF_Container match the assembly name for every panel/truss.

Naming rules (values kept exactly as-is, asterisk preserved):
- Trusses: each existing assembly stays one unit. Its assembly name (e.g.
  CT003-3, TB-4) is written into BIMSF_Container on the assembly and its
  members. Trusses are never merged or dissolved.
- Wall panels: framing carrying BIMSF_Container directly (e.g. *ELB-1001) but
  not yet in an assembly is grouped into one assembly per panel, named exactly
  the container value (asterisk kept), with BIMSF_Container set on the
  assembly element too.

Run with the model in its original MWF state (reopen/discard if you previously
experimented), so trusses are their individual assemblies.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
from Autodesk.Revit.DB import IFailuresPreprocessor, FailureProcessingResult
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


class _SwallowWarnings(IFailuresPreprocessor):
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


def collect_wall_panels():
    """{container: [members]} for framing carrying a container directly and not
    inside any assembly. Container value is used verbatim (asterisk kept)."""
    wall_panels = {}
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        if _in_assembly(el):
            continue
        c = _get_container(el)
        if c:
            wall_panels.setdefault(c, []).append(el)
    return wall_panels


def main():
    existing_assemblies = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )
    wall_panels = collect_wall_panels()

    if not existing_assemblies and not wall_panels:
        forms.alert(
            "No assemblies and no wall panels with '{}' found.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )
        return

    truss_updated = 0
    created = 0
    skipped_single = 0
    rename_failed = 0
    errors = []
    new_assemblies = []  # (AssemblyInstance, name)

    naming_cat = DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)

    # --- Transaction 1: truss containers + create wall assemblies ---
    t1 = DB.Transaction(doc, "UNIQUBE: Create Assemblies")
    t1.Start()
    o1 = t1.GetFailureHandlingOptions()
    o1.SetFailuresPreprocessor(_SwallowWarnings())
    o1.SetClearAfterRollback(True)
    t1.SetFailureHandlingOptions(o1)
    try:
        # Trusses: BIMSF_Container = assembly name (on assembly + members).
        for asm in existing_assemblies:
            try:
                name = asm.Name
            except Exception:
                name = None
            if not name:
                continue
            _set_container(asm, name)
            for mid in asm.GetMemberIds():
                m = doc.GetElement(mid)
                if m is not None and _is_structural_framing(m):
                    _set_container(m, name)
            truss_updated += 1

        # Wall panels: one assembly per container (name kept verbatim).
        for container in sorted(wall_panels.keys()):
            members = wall_panels[container]
            if len(members) < 2:
                skipped_single += 1
                continue
            id_list = List[DB.ElementId]()
            for el in members:
                id_list.Add(el.Id)
            try:
                new_asm = DB.AssemblyInstance.Create(doc, id_list, naming_cat)
                # Regenerate after EACH create so Revit finalizes this
                # assembly's own type before the next one is created.
                # Without this, multiple assemblies created in one transaction
                # share an unfinalized type and all end up with the same name.
                doc.Regenerate()
                new_assemblies.append((new_asm, container))
                created += 1
            except Exception as ex:
                if len(errors) < 3:
                    errors.append("Create {}: {}".format(container, ex))
        t1.Commit()
    except Exception as ex:
        t1.RollBack()
        forms.alert("Failed (create step): {}".format(ex), title="UNIQUBE")
        return

    # --- Transaction 2: name wall assemblies + set their container ---
    if new_assemblies:
        t2 = DB.Transaction(doc, "UNIQUBE: Name Assemblies")
        t2.Start()
        o2 = t2.GetFailureHandlingOptions()
        o2.SetFailuresPreprocessor(_SwallowWarnings())
        o2.SetClearAfterRollback(True)
        t2.SetFailureHandlingOptions(o2)
        try:
            for new_asm, name in new_assemblies:
                # Name the assembly type from the panel number. For
                # geometrically identical panels Revit shares ONE type, so the
                # type name cannot differ between them - that's a Revit limit.
                try:
                    new_asm.AssemblyTypeName = name
                    doc.Regenerate()
                except Exception as ex:
                    rename_failed += 1
                    if len(errors) < 3:
                        errors.append("Rename {}: {}".format(name, ex))

                # Container + Mark on the assembly instance ARE per-instance,
                # so they always hold the correct panel number even when the
                # type name is shared.
                _set_container(new_asm, name)
                mk = new_asm.LookupParameter("Mark")
                if mk and not mk.IsReadOnly:
                    try:
                        mk.Set(name)
                    except Exception:
                        pass
            t2.Commit()
        except Exception as ex:
            t2.RollBack()
            forms.alert("Failed (name step): {}".format(ex), title="UNIQUBE")
            return

    msg = (
        "Done.\n\n"
        "Trusses updated (BIMSF_Container = assembly name): {}\n"
        "Wall panel assemblies created: {}".format(truss_updated, created)
    )
    if rename_failed:
        msg += "\nWall rename failures: {}".format(rename_failed)
    if skipped_single:
        msg += "\nWall panels with <2 elements (no assembly): {}".format(
            skipped_single
        )
    if errors:
        msg += "\n\nFirst errors:\n" + "\n".join(errors)
    forms.alert(msg, title="UNIQUBE — Create Assemblies")


main()
