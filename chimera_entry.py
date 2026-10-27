"""PyInstaller entry point; keeps Chimera imports package-qualified."""
from chimera.app import main

if __name__ == "__main__":
    raise SystemExit(main())
