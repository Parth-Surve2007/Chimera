"""Fetch and stage the version-matched Flet Windows client for offline packaging."""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def prepare(output: Path) -> Path:
    try:
        import flet_desktop
    except ImportError as exc:
        raise RuntimeError(
            "Flet desktop runtime is missing. Install the project dependencies "
            "with the flet[desktop] extra."
        ) from exc

    # Flet's versioned cache resolves/downloads the client matching the installed
    # flet-desktop package. Packaging then embeds that client archive so end-user
    # launches never need a network connection to obtain the UI runtime.
    client_dir = Path(flet_desktop.ensure_client_cached()).resolve()
    client_exe = client_dir / "flet" / "flet.exe"
    if not client_exe.is_file():
        raise RuntimeError(
            f"Flet desktop client was not provisioned correctly: {client_exe} is missing"
        )

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    flet_root = client_dir / "flet"
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for item in sorted(flet_root.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(client_dir).as_posix())

    with zipfile.ZipFile(temporary, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"Flet client archive failed integrity check at {corrupt}")
        if "flet/flet.exe" not in archive.namelist():
            raise RuntimeError("Flet client archive does not contain flet/flet.exe")

    temporary.replace(output)
    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"FLET_CLIENT_READY path={output} size_mb={size_mb:.1f} "
        f"version={flet_desktop.version.version}"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.output)
        return 0
    except Exception as exc:
        print(f"FLET_CLIENT_PREPARATION_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
