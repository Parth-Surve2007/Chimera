import os
from pathlib import Path
from .registry import Registry
from .ui import launch

def main():
    data = Path(os.environ.get("CHIMERA_DATA_DIR", ".chimera")); return launch(Registry(data))

if __name__ == "__main__": main()

