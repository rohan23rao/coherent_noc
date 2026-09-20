"""Pytest configuration shared by every tier of the testbench.

Puts ``tb/`` on ``sys.path`` so tests import the models as ``models.golden``
rather than by relative path, and exports the repo-root and RTL locations that
the cocotb runners need to build.
"""

import sys
from pathlib import Path

TB_DIR = Path(__file__).resolve().parent
REPO_ROOT = TB_DIR.parent
RTL_DIR = REPO_ROOT / "rtl"
SIM_DIR = REPO_ROOT / "sim"

if str(TB_DIR) not in sys.path:
    sys.path.insert(0, str(TB_DIR))
