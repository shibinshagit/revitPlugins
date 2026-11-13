# -*- coding: utf-8 -*-
"""Open the UNIBOM reports folder."""
from __future__ import print_function

__title__ = "Reports"
__author__ = "Uniqube"

import os

from unibom import report
from unibom.ui import dialogs

if __name__ == "__main__":
    folder = dialogs.load_prefs().get("folder") or report.default_folder()
    if not os.path.isdir(folder):
        folder = report.default_folder()
    os.startfile(folder)
