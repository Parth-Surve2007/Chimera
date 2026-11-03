"""Chimera launcher for standalone executable."""
import sys
from pathlib import Path

# Add chimera package to path for PyInstaller
if getattr(sys, 'frozen', False):
    chimera_path = Path(sys._MEIPASS)
    if chimera_path.exists():
        sys.path.insert(0, str(chimera_path))

# Now import and run the app
from chimera.app import main

if __name__ == "__main__":
    sys.exit(main())
