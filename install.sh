#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Installations-Skript fuer "Auto Crop Negative" – Darktable contrib plugin
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LUA_DIR="${HOME}/.config/darktable/lua"
PLUGIN_DIR="${LUA_DIR}/contrib/auto_crop_negative"

echo "╔════════════════════════════════════════╗"
echo "║  Auto Crop Negative – Installation     ║"
echo "╚════════════════════════════════════════╝"
echo ""

# ── 1. Python-Abhängigkeiten ─────────────────────────────────────────────────
echo "[1/4] Prüfe Python-Abhängigkeiten..."

PYTHON_BIN=""
if command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
    PYTHON_BIN="python"
else
    echo "  ✗ Python nicht gefunden. Bitte installiere Python 3."
    exit 1
fi
echo "  ✓ Python: $($PYTHON_BIN --version)"

mkdir -p "$PLUGIN_DIR"

if $PYTHON_BIN -c "import cv2; print('  ✓ OpenCV:', cv2.__version__)" 2>/dev/null; then
    :
else
    echo "  ! OpenCV nicht installiert."
    echo "  → Installiere opencv-python-headless..."

    VENV_DIR="${LUA_DIR}/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        echo "  → Erstelle virtuelle Umgebung in ${VENV_DIR}..."
        $PYTHON_BIN -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install opencv-python-headless numpy
fi

# Wrapper immer installieren: leitet auf die Venv-Python um und faellt
# ohne Venv auf das System-Python zurueck
WRAPPER="${PLUGIN_DIR}/auto_crop_negative_wrapper.py"
cp "$SCRIPT_DIR/auto_crop_negative_wrapper.py" "$WRAPPER"
chmod +x "$WRAPPER"
echo "  ✓ Venv-Wrapper installiert: ${WRAPPER}"

if $PYTHON_BIN -c "import numpy; print('  ✓ NumPy:', numpy.__version__)" 2>/dev/null; then
    :
else
    echo "  → Installiere NumPy..."
    $PYTHON_BIN -m pip install --user numpy
fi
echo ""

# ── 2. Plugin-Verzeichnis erstellen ──────────────────────────────────────────
echo "[2/4] Installiere Plugin nach contrib/auto_crop_negative/..."
mkdir -p "$PLUGIN_DIR"

if [ -f "$SCRIPT_DIR/auto_crop_negative.py" ]; then
    cp "$SCRIPT_DIR/auto_crop_negative.py" "$PLUGIN_DIR/"
    chmod +x "$PLUGIN_DIR/auto_crop_negative.py"
    echo "  ✓ ${PLUGIN_DIR}/auto_crop_negative.py"
else
    echo "  ✗ auto_crop_negative.py nicht gefunden"
    exit 1
fi

echo ""

# ── 3. Lua-Plugin installieren ──────────────────────────────────────────────
echo "[3/4] Installiere Lua-Plugin..."
cp "$SCRIPT_DIR/auto_crop_negative.lua" "$PLUGIN_DIR/"
echo "  ✓ ${PLUGIN_DIR}/auto_crop_negative.lua"
echo ""

# ── 4. Alte Dateien im Root bereinigen ───────────────────────────────────────
echo "[4/4] Bereinige alte Installation..."
OLD_ROOT="${LUA_DIR}/auto_crop_negative.lua"
if [ -f "$OLD_ROOT" ]; then
    mv "$OLD_ROOT" "${OLD_ROOT}.bak"
    echo "  ⚠ Alte ${OLD_ROOT} umbenannt zu .bak"
fi

INIT_FILE="${LUA_DIR}/init.lua"
if [ -f "$INIT_FILE" ] && grep -q 'require.*auto_crop_negative' "$INIT_FILE"; then
    sed -i 's/^require.*auto_crop_negative/-- removed: auto_crop_negative now via Script Manager/' "$INIT_FILE"
    echo "  ✓ init.lua: auto_crop_negative require entfernt"
fi

echo ""
echo "╔════════════════════════════════════════╗"
echo "║  Installation abgeschlossen!           ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "Nächste Schritte:"
echo "  1. Darktable neu starten"
echo "  2. Script Manager aktivieren:"
echo "     Lua → Script Manager → contrib → Auto Crop Negative → ON"
echo "  3. Lighttable: Bilder auswählen → 'Detect & Queue' klicken"
echo "  4. Dunkelkammer: Bilder öffnen → Crop wird automatisch gesetzt"
echo ""
echo "Tastenkürzel:"
echo "  Voreinstellungen → Tastatur → Auto Crop Negative"
echo ""
