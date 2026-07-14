"""CycleWash multipage entry for technical evaluation and report exports."""

from __future__ import annotations

import sys
from pathlib import Path


OUTPUTS_DIR = Path(__file__).resolve().parents[1]
if str(OUTPUTS_DIR) not in sys.path:
    sys.path.insert(0, str(OUTPUTS_DIR))

from cyclewash_technical_evaluation_app import main


main()
