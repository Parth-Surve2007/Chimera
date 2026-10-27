import os
from pathlib import Path
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

