#!/usr/bin/env bash
# Baut dist/Kader-<VERSION>-x86_64.AppImage aus dem PyInstaller-Verzeichnis dist/kader/.
# Aufruf aus dem Projektverzeichnis, nach "pyinstaller packaging/kader.spec":
#   packaging/build-appimage.sh [VERSION]
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="${1:-$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)}"
[ -x dist/kader/kader ] || { echo "dist/kader/kader fehlt - erst: pyinstaller packaging/kader.spec" >&2; exit 1; }

APPDIR=build/Kader.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a dist/kader "$APPDIR/usr/lib/kader"
cp packaging/kader.png "$APPDIR/kader.png"
cp packaging/kader.desktop "$APPDIR/kader.desktop"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
exec "$APPDIR/usr/lib/kader/kader" "$@"
EOF
chmod +x "$APPDIR/AppRun"

TOOL="$(command -v appimagetool || true)"
if [ -z "$TOOL" ]; then
    TOOL=build/appimagetool-x86_64.AppImage
    [ -x "$TOOL" ] || {
        curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
        chmod +x "$TOOL"
    }
fi
# ohne FUSE (CI-Container): appimagetool entpackt sich selbst
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" "dist/Kader-${VERSION}-x86_64.AppImage"
echo "dist/Kader-${VERSION}-x86_64.AppImage"
