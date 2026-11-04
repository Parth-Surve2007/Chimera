# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# Bundle the Flet Flutter desktop client archive alongside the Python package.
# flet_desktop.ensure_client_cached() will extract this local archive on first
# launch instead of trying to download a UI runtime on the user's machine.
client_archive_value = os.environ.get("CHIMERA_FLET_CLIENT_ARCHIVE")
if not client_archive_value:
    raise SystemExit(
        "CHIMERA_FLET_CLIENT_ARCHIVE is not set. "
        "Build with scripts/build_windows.ps1 so the offline Flet client is staged."
    )
client_archive = Path(client_archive_value).expanduser().resolve()
if not client_archive.is_file():
    raise SystemExit(f"Flet desktop client archive is missing: {client_archive}")

flet_datas, flet_binaries, flet_hiddenimports = collect_all("flet_desktop")

# Flet resolves icon names lazily from package JSON at runtime. These are not
# included by collect_all("flet_desktop"), so mirror Flet's own PyInstaller
# hook and explicitly preserve both resource files in their package locations.
flet_icon_datas = collect_data_files(
    "flet.controls.material", includes=["icons.json"]
)
flet_icon_datas += collect_data_files(
    "flet.controls.cupertino", includes=["cupertino_icons.json"]
)

datas = [
    ("fonts", "fonts"),
    ("chimera", "chimera"),
    ("scripts/setup_windows.bat", "scripts"),
    (str(client_archive), "flet_desktop/app"),
]
datas.extend(flet_datas)
datas.extend(flet_icon_datas)

hiddenimports = [
    "flet",
    "flet_core",
    "flet_desktop",
    "flet_desktop.version",
    "pywinauto",
    "pywinauto.keyboard",
    "pywinauto.mouse",
    "pywinauto.application",
    "sqlite3",
    "json",
    "pathlib",
    "dataclasses",
    "typing",
    "threading",
    "uuid",
    "datetime",
    "hashlib",
    "struct",
    "inspect",
    "enum",
    "re",
    "logging",
    "chimera.workflow_engine",
    "chimera.workflow_planner",
    "chimera.result_verification",
    "chimera.failure_recovery",
    "chimera.workflow_integration",
    "chimera.workflow_learning",
    "chimera.workflow_ui",
    "chimera.applications",
    "chimera.applications.registry",
    "chimera.applications.dispatch",
    "chimera.applications.connectors",
    "chimera.applications.connectors.ghidra_connector",
    "chimera.applications.connectors.rea_connector",
    "chimera.applications.connectors.blender_connector",
    "chimera.reverse_engineering",
    "chimera.reverse_engineering.engine",
    "chimera.reverse_engineering.artifact",
    "chimera.reverse_engineering.backends",
    "chimera.reverse_engineering.backends.builtin_static",
    "chimera.reverse_engineering.backends.ghidra",
    "chimera.reverse_engineering.backends.radare2",
    "chimera.reverse_engineering.backends.rizin",
    "chimera.providers",
    "chimera.providers.registry",
    "chimera.providers.base",
    "chimera.providers.adapters",
    "chimera.providers.adapters.local_adapter",
    "chimera.learning",
]
hiddenimports.extend(flet_hiddenimports)

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=flet_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "tkinter",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Chimera",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Chimera",
)
