#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Installations-Skript fuer "Auto Crop Negative"
#   ./install.sh               darktable-Plugin (Lua + Companion-UI)
#   ./install.sh --standalone  nur Befehl "auto-crop-negative" (ohne darktable)
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Eigenstaendig: eigene venv, Befehl nach ~/.local/bin ─────────────────────
if [ "${1:-}" = "--standalone" ]; then
    DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/auto-crop-negative"
    VENV_DIR="${DATA_DIR}/venv"
    BIN_DIR="${HOME}/.local/bin"
    echo "Auto Crop Negative – Installation ohne darktable"
    command -v python3 &>/dev/null || { echo "  ✗ python3 nicht gefunden"; exit 1; }
    mkdir -p "$DATA_DIR" "$BIN_DIR"
    [ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade "$SCRIPT_DIR"
    # rawpy (LibRaw) entwickelt RAWs ohne darktable/RawTherapee; optional
    if ! "$VENV_DIR/bin/pip" install rawpy; then
        echo "  ! rawpy nicht installiert: RAWs brauchen dann darktable-cli oder rawtherapee-cli"
    fi
    # rich: Terminal-Statusanzeige (--tui) fuer SSH/NAS-Sitzungen ohne lokalen Browser; optional
    if ! "$VENV_DIR/bin/pip" install rich; then
        echo "  ! rich nicht installiert: --tui (Terminal-Statusanzeige) steht dann nicht zur Verfuegung"
    fi
    ln -sf "$VENV_DIR/bin/auto-crop-negative" "$BIN_DIR/auto-crop-negative"
    echo "  ✓ ${BIN_DIR}/auto-crop-negative"
    case ":${PATH}:" in
        *":${BIN_DIR}:"*) ;;
        *) echo "  ! ${BIN_DIR} ist nicht im PATH" ;;
    esac
    echo ""
    echo "Start:  auto-crop-negative ~/Scans/Film-12"
    echo "Prüfen: auto-crop-negative check ~/Scans/Film-12"
    echo "Per SSH/NAS ohne lokalen Browser: auto-crop-negative ~/Scans/Film-12 --tui"
    exit 0
fi
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

# Pillow: Vorschauen der Companion-UI (Web-Oberflaeche)
VENV_PY="${LUA_DIR}/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$PYTHON_BIN"
if ! "$VENV_PY" -c "import PIL" 2>/dev/null; then
    echo "  → Installiere Pillow (Vorschauen der Web-UI)..."
    if [ "$VENV_PY" = "${LUA_DIR}/.venv/bin/python" ]; then
        "${LUA_DIR}/.venv/bin/pip" install pillow
    else
        $PYTHON_BIN -m pip install --user pillow
    fi
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
    if [ -f "$SCRIPT_DIR/film_scale.py" ]; then
        cp "$SCRIPT_DIR/film_scale.py" "$PLUGIN_DIR/"
        echo "  ✓ ${PLUGIN_DIR}/film_scale.py"
    fi
else
    echo "  ✗ auto_crop_negative.py nicht gefunden"
    exit 1
fi

echo ""

# ── 3. Lua-Plugin installieren ──────────────────────────────────────────────
echo "[3/4] Installiere Lua-Plugin..."
cp "$SCRIPT_DIR/auto_crop_negative.lua" "$PLUGIN_DIR/"
echo "  ✓ ${PLUGIN_DIR}/auto_crop_negative.lua"

# Companion-UI (lokaler Server + Web-Oberflaeche); Tests und Caches bleiben draussen
rm -rf "${PLUGIN_DIR}/companion"
mkdir -p "${PLUGIN_DIR}/companion"
cp -r "$SCRIPT_DIR/companion/." "${PLUGIN_DIR}/companion/"
find "${PLUGIN_DIR}/companion" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "  ✓ ${PLUGIN_DIR}/companion/"
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
echo "  3. Lighttable: Bilder auswählen → 'Review starten' (Web-UI öffnet sich)"
echo "  4. In der Web-UI prüfen/korrigieren → 'Fertig'"
echo "  5. Zurück in darktable: 'Plan anwenden'"
echo "     (Nicht zufrieden? 'Prüfung öffnen' → in der Web-UI 'Zurück zur Prüfung')"
echo "  Alternativ ohne Web-UI: 'Detect & Queue', dann Bilder in der Dunkelkammer öffnen"
echo ""
echo "Tastenkürzel:"
echo "  Voreinstellungen → Tastatur → Auto Crop Negative"
echo ""
