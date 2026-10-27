from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import flet
hiddenimports = collect_submodules("chimera")
flet_root = Path(flet.__file__).parent
datas = [("fonts", "fonts"), ("assets", "assets"), (str(flet_root / "controls" / "material" / "icons.json"), "flet/controls/material")]
a = Analysis(["chimera_entry.py"], pathex=["."], hiddenimports=hiddenimports, datas=datas, binaries=[], hookspath=[], runtime_hooks=[], excludes=["tests"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Chimera", console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Chimera")
