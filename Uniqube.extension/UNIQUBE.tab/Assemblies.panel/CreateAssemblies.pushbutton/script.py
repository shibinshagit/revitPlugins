# -*- coding: utf-8 -*-
"""Create assemblies for panels, and align truss containers to assembly names.

Two actions in one run:
1. Wall panels: structural framing carrying BIMSF_Container directly (and not
   already inside an assembly) are grouped into a fresh assembly per panel,
   named after the panel.
2. Trusses: members already live inside an assembly (e.g. TB-4, CT003-3). For
   those, the assembly name is written into each member's BIMSF_Container,
   replacing the floor-panel container (e.g. *FT-FloorPanel2001 -> TB-4).
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


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


def _asm_name(value):
    """Wall assembly name from a container value (strip leading '*')."""
    if value is None:
        return ""
    return value.lstrip("*")


def collect_wall_panels():
    """{container: [members]} for framing with a direct container, not yet
    inside any assembly."""
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
        p = el.LookupParameter(pu.PARAM_NAME)
        if p and p.HasValue and p.AsString():
            wall_panels.setdefault(p.AsString(), []).append(el)
    return wall_panels


def main():
    wall_panels = collect_wall_panels()

    # Snapshot existing assemblies BEFORE we create new ones (these are trusses).
    existing_assemblies = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    if not wall_panels and not existing_assemblies:
        forms.alert(
            "No wall panels with '{}' and no existing assemblies found.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )
        return

    asm_created = 0
    truss_updated = 0
    truss_members_set = 0

    with revit.Transaction("UNIQUBE: Create Assemblies"):
        # --- 1. Trusses: write assembly name into members' BIMSF_Container ---
        for asm in existing_assemblies:
            try:
                name = asm.Name
            except Exception:
                name = None
            if not name:
                continue
            member_changed = False
            for mid in asm.GetMemberIds():
                member = doc.GetElement(mid)
                if member is None or not _is_structural_framing(member):
                    continue
                p = member.LookupParameter(pu.PARAM_NAME)
                if p and not p.IsReadOnly:
                    try:
                        if p.AsString() != name:
                            p.Set(name)
                            truss_members_set += 1
                            member_changed = True
                    except Exception:
                        pass
            if member_changed:
                truss_updated += 1

        # --- 2. Wall panels: create a fresh assembly per container ---
        for container in sorted(wall_panels.keys()):
            members = wall_panels[container]
            if len(members) < 2:
                continue  # an assembly needs more than one element

            target_name = _asm_name(container)

            # Remove any existing assembly with the same target name (re-run).
            for a in list(
                DB.FilteredElementCollector(doc)
                .OfClass(DB.AssemblyInstance)
                .ToElements()
            ):
                try:
                    if a.AssemblyTypeName == target_name:
                        doc.Delete(a.Id)
                except Exception:
                    pass

            ids = List[DB.ElementId]()
            for el in members:
                ids.Add(el.Id)

            try:
                naming_cat = DB.ElementId(
                    DB.BuiltInCategory.OST_StructuralFraming
                )
                new_asm = DB.AssemblyInstance.Create(doc, ids, naming_cat)
                doc.Regenerate()
                try:
                    new_asm.AssemblyTypeName = target_name
                except Exception:
                    logger.debug("Name clash for %s; keeping default", target_name)
                asm_created += 1
            except Exception as ex:
                logger.debug("Assembly create failed for %s: %s", container, ex)

    forms.alert(
        "Done.\n\n"
        "Wall panel assemblies created: {}\n"
        "Truss assemblies updated: {}\n"
        "Truss members re-tagged (BIMSF_Container = assembly name): {}".format(
            asm_created, truss_updated, truss_members_set
        ),
        title="UNIQUBE — Create Assemblies",
    )


main()
