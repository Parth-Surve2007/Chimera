import os
import sys
from pathlib import Path

# Handle both package and PyInstaller execution
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    chimera_path = Path(sys._MEIPASS) / 'chimera'
    if chimera_path.exists():
        sys.path.insert(0, str(chimera_path))
    from registry import Registry
    from ui import launch
else:
    # Running as normal package
    from .registry import Registry
    from .ui import launch

def main():
    configured = os.environ.get("CHIMERA_DATA_DIR")
    if configured: data = Path(configured)
    elif getattr(__import__("sys"), "frozen", False): data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chimera"
    else: data = Path(".chimera")
    try: return launch(Registry(data))
    except Exception as exc: raise RuntimeError(f"Chimera could not start. Writable data directory: {data}. Error: {exc}") from exc

if __name__ == "__main__": main()

