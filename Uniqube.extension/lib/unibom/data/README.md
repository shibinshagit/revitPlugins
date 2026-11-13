# UNIBOM rules

These four files are the whole of UNIBOM's knowledge. The Revit buttons only run them. The same files will be read by uni-server later, so change them here and nowhere else.

| File | What it holds | Who edits it |
| --- | --- | --- |
| `settings.json` | Parameters from the Superstructure Logic spec (stud spacing, wastage, screws per kg), how panel names map to systems, role code names | Estimation / engineering |
| `items.json` | Item master: one row per buyable item, with kg per metre or sheet size. `source`: UNIQUBE, SUPPLIER or PROPOSED (still to confirm) | Procurement / estimation |
| `member_rules.json` | Counted mode: how each modelled element becomes a BOQ line. First matching rule wins | BIM / estimation |
| `wall_recipes.json` | Recipe mode: spec formulas that estimate wall framing from wall length, height and openings | Estimation |

Formulas use `+ - * / ^ ( )`, `ceil floor round min max sqrt abs cos sin tan` (degrees), record names (`length_m`, `width_mm`, `L`, `H`, `openings`) and `P.<parameter>` from settings. Nothing else runs; there is no `eval`.

After any change:

1. Run **UNIBOM > Check Model** in Revit.
2. Run the regression tests: `python3 tools/unibom_tests/test_unibom.py`. A changed total fails the test on purpose. Update the expected number only when the change is intended.
