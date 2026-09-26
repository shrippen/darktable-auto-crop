# PyInstaller-Spezifikation (siehe packaging/README.md).
#   Windows: eine Datei dist/kader.exe, ohne Konsolenfenster
#   Linux:   Verzeichnis dist/kader/ fuer das AppImage (packaging/build-appimage.sh); eine einzelne
#            Datei muesste im AppImage bei jedem Start noch einmal komplett entpackt werden
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WINDOWS = sys.platform == "win32"

a = Analysis(
    [os.path.join(SPECPATH, "kader_app.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "companion", "static"), os.path.join("companion", "static")),
           (os.path.join(ROOT, "kader.lua"), ".")],          # darktable-Plugin, siehe companion/dtplugin.py
    # Spaet importiert (erst in Funktionen); die Analyse findet sie nicht zuverlaessig.
    hiddenimports=["kader", "film_scale", "companion.__main__", "companion.window", "companion.tui",
                   "companion.dtplugin", "companion.targets.darktable_xmp", "rawpy"],
    excludes=["rich", "matplotlib", "pytest"],
)
pyz = PYZ(a.pure)

if WINDOWS:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas,
        name="kader",
        console=False,
        icon=os.path.join(SPECPATH, "kader.ico"),
        upx=False,
    )
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, name="kader", console=True, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="kader", upx=False)
