"""CycleWash multipage entry for drivetrain design and sprocket preview."""

import sys
from pathlib import Path


OUTPUTS_DIR = Path(__file__).resolve().parent
if str(OUTPUTS_DIR) not in sys.path:
    sys.path.insert(0, str(OUTPUTS_DIR))

from cyclewash_streamlit_app import main


main()
