# -*- coding: utf-8 -*-
"""Fail when UNIBOM's settings drift from the estimation studio's superstructure rules.

UNIBOM (Revit) and the studio (web) both price LGS. Their parameters were copied from the same
spec on 26 Oct 2026 and must stay equal until uni-server serves one copy to both.

    python3 tools/check_rules_drift.py [path/to/superstructureRules.json]

Default studio path assumes the MULK-ECOSYSTEM layout (revitPlugins next to uniqube-software).
Exit code 1 lists every parameter that differs. Run it with the UNIBOM tests after any rule change.
"""
from __future__ import print_function

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UNIBOM = os.path.join(HERE, "..", "Uniqube.extension", "lib", "unibom", "data", "settings.json")
STUDIO = os.path.join(HERE, "..", "..", "uniqube-software", "uniqube", "src", "studio", "data", "superstructureRules.json")

# UNIBOM-only knobs with no studio counterpart.
UNIBOM_ONLY = {"kg_per_m_tolerance", "board_wastage"}


def load(path):
    with io.open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def matches(unibom_value, studio_value):
    # The studio may list the allowed choices (stud spacing 450 or 600); UNIBOM picks one.
    if isinstance(studio_value, list) and not isinstance(unibom_value, list):
        return unibom_value in studio_value
    if isinstance(unibom_value, (int, float)) and isinstance(studio_value, (int, float)):
        return abs(float(unibom_value) - float(studio_value)) < 1e-9
    return unibom_value == studio_value


def main(argv):
    studio_path = argv[1] if len(argv) > 1 else STUDIO
    if not os.path.exists(studio_path):
        print("Studio rules not found at %s. Pass the path to superstructureRules.json." % studio_path)
        return 2
    unibom = load(UNIBOM)["parameters"]
    studio = {row["id"]: row.get("value") for row in load(studio_path)["parameters"]}
    problems = []
    for key, entry in sorted(unibom.items()):
        if key in UNIBOM_ONLY:
            continue
        if key not in studio:
            problems.append("%s: in UNIBOM (%r) but not in the studio rules" % (key, entry.get("value")))
        elif not matches(entry.get("value"), studio[key]):
            problems.append("%s: UNIBOM %r, studio %r" % (key, entry.get("value"), studio[key]))
    if problems:
        print("Rules drift between UNIBOM and the estimation studio:")
        for line in problems:
            print("  - " + line)
        print("Fix the side that is wrong; both must agree before an estimate goes out.")
        return 1
    print("UNIBOM and studio rules agree on %d shared parameters." % (len(unibom) - len(UNIBOM_ONLY & set(unibom))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
