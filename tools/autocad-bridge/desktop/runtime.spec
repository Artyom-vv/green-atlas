# Build from the qualified API environment; one-folder keeps native dylibs intact.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

repo = Path(SPECPATH).resolve().parents[2]
a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(repo / "apps/api")],
    hiddenimports=collect_submodules("app"),
    datas=collect_data_files("ezdxf"),
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GreenAtlasRuntime",
          console=True, target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name="GreenAtlasRuntime")
