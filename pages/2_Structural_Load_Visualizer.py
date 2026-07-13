"""CycleWash multipage entry for STL structural load visualization."""

from __future__ import annotations

import sys
from pathlib import Path


OUTPUTS_DIR = Path(__file__).resolve().parents[1]
if str(OUTPUTS_DIR) not in sys.path:
    sys.path.insert(0, str(OUTPUTS_DIR))

from cyclewash_structural_app import main


main()
