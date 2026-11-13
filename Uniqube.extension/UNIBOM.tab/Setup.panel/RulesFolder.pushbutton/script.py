# -*- coding: utf-8 -*-
"""Open the UNIBOM data folder (settings, item master, rules)."""
from __future__ import print_function

__title__ = "Rules"
__author__ = "Uniqube"

import os

from unibom import engine

if __name__ == "__main__":
    os.startfile(engine.DATA_DIR)
