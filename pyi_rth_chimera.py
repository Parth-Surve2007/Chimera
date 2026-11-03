"""PyInstaller runtime hook for Chimera package."""
import sys
from pathlib import Path

# Add chimera package to path at the very beginning
if getattr(sys, 'frozen', False):
    meipass = Path(sys._MEIPASS)
    # Add MEIPASS to path so imports work
    if str(meipass) not in sys.path:
        sys.path.insert(0, str(meipass))
