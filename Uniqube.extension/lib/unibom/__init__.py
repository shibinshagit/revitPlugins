# -*- coding: utf-8 -*-
"""UNIBOM - BOQ / BOM takeoff inside Revit for UNIQUBE.

    formula  safe evaluator for rule formulas (no eval)
    engine   rules + records -> BOQ lines, issues, comparisons (pure Python)
    collect  Revit model -> records (Revit API)
    report   dark / orange HTML report (output window + file) and CSV
    ui/      dialogs (WPF XAML), report.css
    data/    settings, item master, member rules, wall recipes (JSON, shared
             with uni-server later)
"""
__version__ = "0.2.0"
