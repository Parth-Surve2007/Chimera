"""Chimera package initialization with PyInstaller support."""
import sys
from pathlib import Path

# Handle PyInstaller bundled execution
if getattr(sys, 'frozen', False):
    # Add the chimera package to sys.path when bundled
    chimera_path = Path(sys._MEIPASS) / 'chimera'
    if chimera_path.exists() and str(chimera_path) not in sys.path:
        sys.path.insert(0, str(chimera_path))
