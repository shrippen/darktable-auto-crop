# Doppelklick-Pakete bauen

Für Nutzer ohne Python/Terminal: eine `.exe` für Windows, ein AppImage für Linux. Beide starten
`companion/launcher.py`: Ordner per Dialog oder Drag&Drop, dann wie `kader open ORDNER --window`
(Statusfenster statt Terminal, Fehler als Meldungsfenster).

## Automatisch (Release)

Ein Tag `v*` wird von git.arianw.de nach GitHub gespiegelt; dort baut `.github/workflows/release.yml`
beide Pakete und hängt sie an das GitHub-Release (Text aus `RELEASE_NOTES.md`). Von Hand
(Actions → release → Run workflow) entstehen nur Artefakte zum Testen, kein Release.

## Von Hand

Windows (PowerShell, Python 3.12 von python.org, mit tkinter):

```powershell
py -3.12 -m venv build\venv
build\venv\Scripts\pip install . rawpy pyinstaller
build\venv\Scripts\pyinstaller --noconfirm packaging\kader.spec
# -> dist\kader.exe
```

Linux:

```bash
python3 -m venv build/venv
build/venv/bin/pip install . rawpy pyinstaller
build/venv/bin/pyinstaller --noconfirm packaging/kader.spec
packaging/build-appimage.sh          # -> dist/Kader-<version>-x86_64.AppImage
```

Das AppImage läuft nur auf Systemen mit mindestens der glibc des Build-Rechners. Ein lokal auf einem
aktuellen Arch gebautes AppImage startet daher nicht auf Ubuntu 22.04; für Veröffentlichungen den
CI-Build (Ubuntu 22.04) nehmen.

## Icon

`kader.ico`/`kader.png` sind aus `docs/icon.svg` erzeugt:

```bash
for n in 16 32 48 64 128 256; do rsvg-convert -w $n -h $n docs/icon.svg -o /tmp/icon-$n.png; done
magick /tmp/icon-{16,32,48,64,128,256}.png packaging/kader.ico
cp /tmp/icon-256.png packaging/kader.png
```
