"""Application entry point shared by source and frozen launches."""
import os
import sys
from pathlib import Path

# Keep package-relative imports in both source and PyInstaller builds.
# The frozen bundle includes the chimera package itself; importing its
# modules as top-level registry / ui breaks their relative imports.
from .registry import Registry
from .ui import launch


def main():
    configured = os.environ.get("CHIMERA_DATA_DIR")
    if configured:
        data = Path(configured).expanduser()
    elif getattr(sys, "frozen", False):
        data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chimera"
    else:
        data = Path(".chimera")

    # CI runs this mode against the built executable. Reaching this point
    # proves the frozen package and all startup imports were resolved.
    if os.environ.get("CHIMERA_PACKAGED_SMOKE") == "1":
        import flet
        from .local_model import LocalModelProvider, config_from_env
        if flet is None:
            raise RuntimeError("Flet is unavailable in the packaged application")
        LocalModelProvider(config_from_env()).stop()
        print("CHIMERA_PACKAGED_SMOKE_OK")
        return 0

    try:
        data.mkdir(parents=True, exist_ok=True)
        return launch(Registry(data))
    except Exception as exc:
        raise RuntimeError(
            f"Chimera could not start. Writable data directory: {data}. Error: {exc}"
        ) from exc


if __name__ == "__main__":
    main()
