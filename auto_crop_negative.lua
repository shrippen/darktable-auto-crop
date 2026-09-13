--[[
  Auto Crop Negative – Darktable contrib plugin
  Detects the film frame on negatives, applies crop via dt.styles
  (import a temporary .dtstyle, apply it to the image, delete it again).
]]

local dt = require "darktable"
local du = require "lib/dtutils"
du.check_min_api_version("8.0.0", "Auto Crop Negative")

local _ = dt.gettext.gettext

local script_data = {}
script_data.metadata = {
  name = _("Auto Crop Negative"),
  purpose = _("automatically crop film negatives to the image area"),
  author = "Arian", help = ""
}

local MOD_LT = "auto_crop_negative"

-- Preferences: Gruen/Gelb-Schwellen fuer die Konfidenz (Phase 3, siehe
-- tools/calibrate.py). Standardwerte sind auf den 98 Referenz-Crops
-- kalibriert: >= t_green -> croppen + gruen, >= t_yellow -> croppen +
-- gelb (zur Kontrolle), darunter -> NICHT croppen + rot.
dt.preferences.register(MOD_LT, "t_green", "float",
  _("Auto Crop: Gruen-Schwelle"),
  _("Ab dieser Konfidenz automatisch croppen und gruen markieren"),
  0.50, 0.0, 1.0, 0.01)
dt.preferences.register(MOD_LT, "t_yellow", "float",
  _("Auto Crop: Gelb-Schwelle"),
  _("Ab dieser Konfidenz croppen, aber zur Kontrolle gelb markieren "
    .. "- darunter wird NICHT gecroppt (rot)"),
  0.30, 0.0, 1.0, 0.01)

local function classify_confidence(conf, t_green, t_yellow)
  if conf >= t_green then return "green"
  elseif conf >= t_yellow then return "yellow"
  else return "red" end
end

local function get_script_dir()
  local info = debug.getinfo(1, "S")
  local src = info.source:match("@(.+)")
  if src then return src:match("(.*/)") or "." end
  return "."
end
local SCRIPT_DIR = get_script_dir()

local function file_exists(p)
  local h = io.open(p, "r")
  if h then h:close(); return true end
  return false
end

local function read_file(p)
  local h = io.open(p, "r")
  if not h then return nil end
  local s = h:read("*a"); h:close(); return s
end

local function write_file(p, c)
  local h = io.open(p, "w")
  if not h then return false end
  h:write(c); h:close(); return true
end

-- string.format("%f", ...) folgt der C-Locale des Prozesses: unter z.B.
-- Deutsch liefert es "0,300" statt "0.300" (Komma-Dezimaltrenner). An
-- Python weitergereicht bricht das --confidence-threshold sofort mit
-- "invalid float value" ab. Fuer Zahlen, die an einen anderen Prozess
-- gehen, IMMER diese Funktion statt string.format("%f", ...) verwenden.
local function fmt_float_c(v, prec)
  return string.format("%." .. prec .. "f", v):gsub(",", ".")
end

-- Datei-Log fuer Diagnose (unabhaengig von Terminal-Flags)
local LOG_FILE = os.getenv("HOME") .. "/.cache/darktable/auto_crop_negative.log"

local function log_rotate()
  local h = io.open(LOG_FILE, "r")
  if h then
    local size = h:seek("end")
    h:close()
    if size > 524288 then os.remove(LOG_FILE) end
  end
end

local function log(msg)
  local h = io.open(LOG_FILE, "a")
  if h then
    h:write(os.date("%Y-%m-%d %H:%M:%S ") .. msg .. "\n")
    h:close()
  end
end

-- Minimal JSON parser (recursive descent)
local function parse_json(str)
  local pos, len = 1, #str

  local function skip_ws()
    pos = str:find("[^ \t\n\r]", pos) or (len + 1)
  end

  local function parse_string()
    skip_ws()  -- nach ',' kann Whitespace stehen (indent=2-JSON)
    pos = pos + 1
    local parts = {}
    while pos <= len do
      local c = str:sub(pos, pos)
      if c == "\\" then
        pos = pos + 1
        local e = str:sub(pos, pos)
        if e == "n" then parts[#parts+1] = "\n"
        elseif e == "t" then parts[#parts+1] = "\t"
        else parts[#parts+1] = e end
      elseif c == '"' then
        pos = pos + 1
        return table.concat(parts)
      else parts[#parts+1] = c end
      pos = pos + 1
    end
    return table.concat(parts)
  end

  local function parse_number()
    local s = pos
    if str:sub(pos, pos) == "-" then pos = pos + 1 end
    while pos <= len and str:sub(pos, pos):match("[%d%.eE%+%-]") do pos = pos + 1 end
    return tonumber(str:sub(s, pos - 1))
  end

  local function parse_value()
    skip_ws()
    local c = str:sub(pos, pos)
    if c == '"' then return parse_string()
    elseif c == "{" then
      pos = pos + 1; local obj = {}; skip_ws()
      if str:sub(pos, pos) == "}" then pos = pos + 1; return obj end
      while true do
        local key = parse_string(); skip_ws(); pos = pos + 1
        obj[key] = parse_value(); skip_ws()
        local c2 = str:sub(pos, pos)
        if c2 == "," then pos = pos + 1
        elseif c2 == "}" then pos = pos + 1; return obj end
      end
    elseif c == "[" then
      pos = pos + 1; local arr = {}; skip_ws()
      if str:sub(pos, pos) == "]" then pos = pos + 1; return arr end
      while true do
        arr[#arr+1] = parse_value(); skip_ws()
        local c2 = str:sub(pos, pos)
        if c2 == "," then pos = pos + 1
        elseif c2 == "]" then pos = pos + 1; return arr end
      end
    elseif c == "t" then pos = pos + 4; return true
    elseif c == "f" then pos = pos + 5; return false
    elseif c == "n" then pos = pos + 4; return nil
    else return parse_number() end
  end

  local ok, r = pcall(parse_value)
  return ok and r or nil
end

-- ═══ Queue: filename → crop data ═══════

local QUEUE_PATH = SCRIPT_DIR .. "crop_queue.json"

local function load_queue()
  local raw = read_file(QUEUE_PATH)
  if not raw or #raw == 0 then return {} end
  local d = parse_json(raw)
  return (type(d) == "table") and d or {}
end

local function save_queue(queue)
  local parts = {}
  for fn, c in pairs(queue) do
    parts[#parts+1] = string.format(
      '"%s":{"x":%s,"y":%s,"w":%s,"h":%s,"band":"%s"}',
      fn:gsub('\\','\\\\'):gsub('"','\\"'),
      fmt_float_c(c.x, 4), fmt_float_c(c.y, 4), fmt_float_c(c.w, 4),
      fmt_float_c(c.h, 4), c.band or "yellow")
  end
  write_file(QUEUE_PATH, "{" .. table.concat(parts, ",") .. "}")
end

-- ═══ Python backend ═══════

local function find_python()
  -- Wrapper zuerst: leitet auf Venv-Python um (cv2 dort installiert)
  local candidates = {
    SCRIPT_DIR .. "auto_crop_negative_wrapper.py",
    SCRIPT_DIR .. "auto_crop_negative.py",
  }
  for _, p in ipairs(candidates) do
    if file_exists(p) then return p end
  end
  return nil
end

-- Zustand des Hintergrund-Batches. Lua blockiert NIE auf den Lauf (sonst
-- koennte der Abbruch-Knopf nicht feuern): Python laeuft detached, schreibt
-- die Queue selbst und meldet das Ende via BATCHDONE. check_batch()
-- finalisiert ereignisgesteuert (selection/mouseover/pixelpipe/...).
local batch_state = {
  active = false, cancelled = false,
  pid = nil, job = nil,
  json_file = nil, prog_file = nil,
  images = nil,
  n_images = nil,     -- echte Bildzahl (Python meldet Fortschritt in
                      -- Einheiten: RAW-Export + Pass A + Pass B je Bild)
  t_green = 0.5, t_yellow = 0.3,  -- bei Spawn aus den Preferences erfasst,
                                  -- damit ein spaeteres Aendern der
                                  -- Preferences den laufenden Batch nicht
                                  -- inkonsistent macht (Python nutzt zum
                                  -- Queue-Schreiben dieselben Werte)
  t_start = 0,        -- os.time() beim Spawn
  last_est = -1,      -- letzte angezeigte Schätzung (Sekunden), Dedupe
  toast_sec = 0,      -- letzte Sekunde, fuer die ein Toast kam
}

local lt_widget  -- Forward-Deklaration (Widget wird unten erzeugt;
                 -- check_batch/cleanup_batch aktualisieren die Statuszeile)

local function cleanup_batch(closing_job)
  local st = batch_state
  if st.json_file then os.remove(st.json_file); st.json_file = nil end
  if st.prog_file then os.remove(st.prog_file); st.prog_file = nil end
  pcall(function() lt_widget.children[10].label = "" end)
  if closing_job and st.job then
    pcall(function() st.job.valid = false end)
    st.job = nil
  end
  st.active = false
end

local function batch_cancel()
  local st = batch_state
  if not st.active then return end
  st.cancelled = true
  if st.pid then
    os.execute(string.format("kill -TERM %d 2>/dev/null", st.pid))
  end
  log("CANCEL: vom Benutzer angefordert")
  dt.print(_("Auto Crop: Abgebrochen"))
  cleanup_batch(true)
end

-- Poller: wird von haeufigen Events (selection-changed, mouse-over, ...)
-- aufgerufen. Aktualisiert den Fortschritt und finalisiert den Lauf.
-- Farblabels sind in der darktable-Lua-API BOOLEAN-FELDER direkt am
-- Bild-Objekt (image.red/yellow/green/blue/purple) - es gibt KEIN
-- dt.colorlabels-Modul/-Funktion (bestaetigt anhand der mitgelieferten
-- offiziellen Scripts unter /usr/share/darktable/lua-scripts/lib/dtutils/
-- string.lua). Die urspruengliche Fassung nutzte "dt.colorlabels.set(img,
-- dt.colorlabels.GREEN, true)", was IMMER mit "attempt to index a nil
-- value (field 'colorlabels')" abbrach.
local function set_color_label(img, band)
  img.red = (band == "red")
  img.yellow = (band == "yellow")
  img.green = (band == "green")
end

local function check_batch()
  local st = batch_state
  if not st.active then return end
  local prog = read_file(st.prog_file) or ""

  -- Python meldet den Fortschritt in "Einheiten" (RAW-Export + Pass A +
  -- Pass B je Bild - bei RAW-Dateien also bis zu 3 Einheiten PRO Bild),
  -- nicht in Bildern. "BATCHINFO total=" traegt die echte Bildzahl; ohne
  -- das wuerde die Statuszeile bei RAW-Rollen z.B. "Bild 42/108" fuer nur
  -- 36 ausgewaehlte Bilder anzeigen (108 = 3x36 Einheiten).
  local n_images = tonumber(prog:match("BATCHINFO total=(%d+)"))
  if n_images then st.n_images = n_images end

  local g_str, y_str, r_str =
    prog:match("BATCHDONE green=(%d+) yellow=(%d+) red=(%d+)")
  local done = g_str ~= nil
  local alive = false
  if st.pid and not done then
    alive = (os.execute(string.format("kill -0 %d 2>/dev/null", st.pid))
             == true)
  end

  if done then
    -- Lauf fertig: Python hat die Queue bereits geschrieben (band-basiert,
    -- rot ausgeschlossen) - hier nur noch Colorlabels setzen und melden.
    local raw = read_file(st.json_file) or ""
    local data = parse_json(raw)
    if type(data) == "table" and data.results then
      for _, r in ipairs(data.results) do
        if r and r.x ~= nil and r.width ~= nil then
          local band = classify_confidence(r.confidence or 0, st.t_green,
            st.t_yellow)
          for _, img in ipairs(st.images or {}) do
            if img.filename == r.filename then
              set_color_label(img, band)
              break
            end
          end
        end
      end
      log(string.format("queued: green=%s yellow=%s red=%s",
        g_str, y_str, r_str))
      dt.print(string.format(
        _("Auto Crop: %s gruen, %s gelb (Kontrolle), %s rot (nicht "
          .. "gecroppt). Zum Anwenden Bilder im Dunkelraum oeffnen."),
        g_str, y_str, r_str))
    else
      log("BATCHDONE, aber JSON unlesbar: "
          .. raw:sub(1, 150):gsub("[%c]", " "))
      dt.print(_("Auto Crop: Fehler - Details im Log"))
    end
    cleanup_batch(true)
    return
  end

  if not alive then
    -- Prozess tot ohne BATCHDONE: Abbruch oder Crash
    if st.cancelled then
      log("batch cancelled (bestaetigt)")
    else
      local tail = prog:match("([^\r\n]+)%s*$") or ""
      log("batch DIED ohne BATCHDONE: " .. tail)
      dt.print_error("Auto Crop Negative: " .. tail)
      dt.print(_("Auto Crop: Fehler - Details im Log"))
    end
    cleanup_batch(true)
    return
  end

  -- laeuft noch: Fortschritt aktualisieren (letzte PROGRESS-Zeile)
  local k, n = prog:match("PROGRESS (%d+)/(%d+)%s*$")
  if k and n and st.job then
    local frac = tonumber(k) / tonumber(n)
    if frac >= 0 and frac < 1 then  -- 1.0 wuerde den Job auto-schliessen
      st.job.percent = frac
      -- Fuer die Anzeige aus der Einheiten-Fraktion eine Bildzahl
      -- zurueckrechnen (n_images = echte Bildzahl aus BATCHINFO, sonst
      -- Einheiten als Notloesung).
      local disp_total = st.n_images or tonumber(n)
      local disp_done = math.min(disp_total,
        math.floor(frac * disp_total + 0.5))
      -- Zeitschaetzung: linear aus elapsed/frac, angezeigt im Toast
      -- (gedrosselt) und in der Statuszeile des Panels (jeder Poll)
      if frac > 0.02 and st.t_start > 0 then
        local elapsed = os.time() - st.t_start
        if elapsed >= 3 then
          local eta = math.floor((elapsed / frac - elapsed) + 0.5)
          local function fmt(s)
            s = math.max(0, s)
            if s >= 60 then
              return string.format("%d:%02d min", math.floor(s / 60), s % 60)
            end
            return string.format("%d s", s)
          end
          local eta_txt = fmt(eta)
          -- Panel-Statuszeile live aktualisieren
          pcall(function()
            lt_widget.children[10].label = string.format(
              _("Restzeit: %s (Bild %d/%d)"), eta_txt, disp_done, disp_total)
          end)
          -- Toast nur bei deutlicher Aenderung (10 s Schritte)
          if math.abs(eta - st.last_est) >= 10
             or os.time() - st.toast_sec >= 30 then
            st.last_est = eta
            st.toast_sec = os.time()
            dt.print(string.format(
              _("Auto Crop: ca. %s verbleibend (Bild %d/%d)"),
              eta_txt, disp_done, disp_total))
          end
        end
      end
    end
  end
end

-- check_batch(), aber ein Fehler landet im Log statt spurlos zu
-- verschwinden (darktable faengt Fehler in Event-Callbacks selbst ab,
-- ohne sie in unser eigenes Log zu schreiben).
local function safe_check_batch()
  local ok, err = pcall(check_batch)
  if not ok then
    log("check_batch Fehler: " .. tostring(err))
  end
end

-- ═══ Crop application via dt.styles (nicht mehr dt.gui.action) ═══════
-- Nach langem Rumprobieren mit dt.gui.action (Pfade, Instanzen, Elemente
-- - siehe git-Historie fuer die ganze Recherche): das Modul liess sich
-- darueber zuverlaessig ein/ausschalten, aber KEIN einziger Slider-Wert
-- (cx/cy/cw/ch, egal unter welchem Pfad/Instanz/Element probiert) hat
-- sich je aendern lassen (nachgewiesen per direkter SQL-Abfrage auf
-- library.db, nicht nur ueber die oft verzoegert geschriebene XMP).
--
-- Der Nutzer hat auf ein fremdes, unabhaengiges Skript hingewiesen
-- (github.com/hmoens/darktable-lua-scripts, lighttable_exposure_controls.lua),
-- das GENAU dasselbe Problem fuer das exposure-Modul dokumentiert:
-- "Darktable does not allow reading or modifying module parameters from
-- the lighttable via Lua" (dt.gui.action-Werte-Setzen ist demnach eine
-- bekannte Einschraenkung, kein Bug unsererseits). Der dortige Workaround:
-- einen darktable-Style (.dtstyle-XML, dieselbe Binaerkodierung wie ein
-- History-Eintrag) per dt.styles.import() importieren und per
-- dt.styles.apply() auf das Bild anwenden. Styles sind IMMER additiv -
-- sie haengen sich als neuer History-Eintrag an, ohne die bestehende
-- History zu beruehren, was auch das history_end-Risiko der alten
-- dt.gui.action-Route umgeht (Styles sind darktables eigener,
-- alltaeglich genutzter Mechanismus, um Bearbeitungen auf bereits
-- editierte Bilder anzuwenden).
--
-- crop-Parameterformat (src/iop/crop.c, modversion 3), per SQL an einem
-- echten crop-History-Eintrag verifiziert:
--   typedef struct { float cx, cy, cw, ch; int ratio_n, ratio_d; }
-- cx/cy/cw/ch sind Kantenfraktionen 0..1 (links/oben/rechts/unten,
-- gleiches Koordinatensystem wie unsere left/top/right/bottom-Werte -
-- keine Invertierung noetig).
--
-- ratio_n/ratio_d waehlen die Seitenverhaeltnis-Sperre der Aspekt-
-- Dropdown im Modul - relevant, weil ein gesperrtes Verhaeltnis unseren
-- gesetzten Crop wieder Richtung Original-Format zurechtruecken kann.
-- Aus der Preset-Liste in crop.c (dt_iop_crop_aspect_t, Feldreihenfolge
-- {name, d, n}):
--   { _("freehand"),       0, 0 }  -- "frei", kein gesperrtes Verhaeltnis
--   { _("original image"), 1, 0 }  -- an das Originalbild gesperrt
-- Bisher wurde hier (0, 1) gepackt - das entspricht laut dieser Liste
-- "original image", nicht "frei"! Auf Nutzerwunsch jetzt (0, 0) =
-- "freehand", damit das gesetzte Crop-Rechteck nicht wieder auf das
-- Original-Seitenverhaeltnis gezwungen wird.
local CROP_RATIO_N = 0
local CROP_RATIO_D = 0
local CROP_MODVERSION = 3
local CROP_BLENDOP_VERSION = 14
-- blendop_params eines echten, per obigem SQL-Check beobachteten
-- crop-Eintrags dieser darktable-Version - das ist ein Default-Blendmode
-- ("normal", keine Maske), fuer jedes Bild identisch gueltig.
local CROP_BLENDOP_PARAMS =
  "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="

local function pack_crop_params(cx, cy, cw, ch)
  local bin = string.pack("<ffffi4i4", cx, cy, cw, ch, CROP_RATIO_N,
    CROP_RATIO_D)
  return (bin:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function crop_style_xml(style_name, op_params_hex, enabled)
  return string.format([[<?xml version="1.0" encoding="UTF-8"?>
<darktable_style version="1.0">
  <info>
    <name>%s</name>
    <description></description>
  </info>
  <style>
    <plugin>
      <num>0</num>
      <module>%d</module>
      <operation>crop</operation>
      <op_params>%s</op_params>
      <enabled>%d</enabled>
      <blendop_params>%s</blendop_params>
      <blendop_version>%d</blendop_version>
      <multi_priority>0</multi_priority>
      <multi_name></multi_name>
      <multi_name_hand_edited>0</multi_name_hand_edited>
    </plugin>
  </style>
</darktable_style>
]], style_name, CROP_MODVERSION, op_params_hex, enabled and 1 or 0,
    CROP_BLENDOP_PARAMS, CROP_BLENDOP_VERSION)
end

-- Importiert+wendet einen einzelnen crop-Style auf ein Bild an und
-- raeumt danach wieder auf. Gemeinsame Basis fuer set_crop() (Crop
-- setzen) und undo_crop() (Crop-Modul wieder deaktivieren).
local function apply_crop_style(image, op_params_hex, enabled, log_desc)
  local style_name = "auto_crop_negative_tmp_" .. os.time() .. "_"
    .. tostring(math.random(100000, 999999))
  local tmp_path = os.tmpname() .. ".dtstyle"
  local xml = crop_style_xml(style_name, op_params_hex, enabled)
  local f = io.open(tmp_path, "w")
  f:write(xml)
  f:close()

  dt.styles.import(tmp_path)
  os.remove(tmp_path)

  local style_obj = nil
  for _, s in ipairs(dt.styles) do
    if s.name == style_name then
      style_obj = s
      break
    end
  end
  if not style_obj then
    error("importierter Style '" .. style_name .. "' nicht gefunden")
  end

  dt.styles.apply(style_obj, image)
  dt.styles.delete(style_obj)
  log(string.format("apply_crop_style %s: %s", image.filename, log_desc))
end

-- Bilder, denen in dieser Sitzung zuletzt per set_crop() ein Crop
-- verpasst wurde - Grundlage fuer den "Rueckgaengig"-Knopf. Absichtlich
-- nur ein einfacher Verlauf (kein Stack mit mehreren Ebenen): ein
-- erneuter Klick auf "Rueckgaengig" deaktiviert einfach nochmal alle
-- zuletzt gecroppten Bilder.
local last_cropped_images = {}

local function set_crop(image, crop)
  local iw, ih = image.width, image.height
  if not iw or not ih or iw == 0 or ih == 0 then
    log(string.format("set_crop %s: ungueltige Bildgroesse %sx%s",
      image and image.filename or "?", tostring(iw), tostring(ih)))
    return false
  end
  local left = math.max(0, math.min(1, crop.x / iw))
  local top = math.max(0, math.min(1, crop.y / ih))
  local right = math.max(left + 0.001, math.min(1, (crop.x + crop.w) / iw))
  local bottom = math.max(top + 0.001, math.min(1, (crop.y + crop.h) / ih))

  local ok, err = pcall(function()
    apply_crop_style(image, pack_crop_params(left, top, right, bottom), true,
      string.format("Style angewendet, frac l=%s t=%s r=%s b=%s",
        fmt_float_c(left, 4), fmt_float_c(top, 4), fmt_float_c(right, 4),
        fmt_float_c(bottom, 4)))
  end)
  if not ok then
    log("set_crop " .. image.filename .. " Fehler: " .. tostring(err))
    return false
  end
  last_cropped_images[#last_cropped_images + 1] = image
  return true
end

-- Rueckgaengig: deaktiviert das crop-Modul wieder fuer eine Teilmenge der
-- zuletzt automatisch gecroppten Bilder. Fuegt (wie set_crop) einen
-- neuen, additiven History-Eintrag hinzu (enabled=0) statt echt etwas
-- aus der History zu entfernen - die Lua-API bietet keinen Weg, einzelne
-- History-Eintraege zu loeschen. Das ist aber genau das native
-- darktable-Verhalten: ein Modul ausschalten ist eine ganz normale,
-- jederzeit umkehrbare History-Aenderung (in der History-Ansicht sichtbar,
-- ueber die dortigen Bordmittel jederzeit wieder rueckgaengig zu machen).
--
-- "images" waehlt die zu behandelnde Teilmenge aus last_cropped_images
-- (per Dateiname abgeglichen); nur tatsaechlich getroffene Bilder werden
-- aus last_cropped_images entfernt, alles andere bleibt fuer einen
-- spaeteren "Rueckgaengig"-Aufruf gemerkt.
local function undo_crop_images(images, label)
  local target = {}
  for _, img in ipairs(images) do target[img.filename] = true end

  local n = 0
  local remaining = {}
  for _, image in ipairs(last_cropped_images) do
    if target[image.filename] then
      local ok, err = pcall(function()
        apply_crop_style(image, pack_crop_params(0, 0, 1, 1), false,
          "Crop deaktiviert (Rueckgaengig, " .. label .. ")")
      end)
      if ok then
        n = n + 1
      else
        log("undo_crop " .. image.filename .. " Fehler: " .. tostring(err))
        remaining[#remaining + 1] = image
      end
    else
      remaining[#remaining + 1] = image
    end
  end
  last_cropped_images = remaining
  dt.print(string.format(
    _("Auto Crop: %d Crop(s) rueckgaengig gemacht (%s)"), n, label))
end

local function undo_crop_selected()
  local images = dt.gui.action_images
  if not images or #images == 0 then
    dt.print(_("Auto Crop: keine Bilder ausgewaehlt"))
    return
  end
  undo_crop_images(images, _("Auswahl"))
end

local function undo_crop_all()
  if #last_cropped_images == 0 then
    dt.print(_("Auto Crop: nichts zum Rueckgaengigmachen"))
    return
  end
  undo_crop_images(last_cropped_images, _("gesamte Sitzung"))
end

local function apply_crop(image)
  local queue = load_queue()
  local crop = queue[image.filename]
  if not crop then
    log("apply_crop " .. image.filename .. ": kein Queue-Eintrag")
    return false
  end
  local ok = set_crop(image, crop)
  if ok then
    set_color_label(image, crop.band == "green" and "green" or "yellow")
    queue[image.filename] = nil
    save_queue(queue)
  end
  return ok
end

-- ═══ Lighttable: Detect & Queue ═══════

local function detect_and_queue()
  local images = dt.gui.action_images
  if not images or #images == 0 then
    dt.print(_("No images selected"))
    return
  end
  local paths = {}
  for _, img in ipairs(images) do
    paths[#paths+1] = img.path .. "/" .. img.filename
  end
  dt.print(string.format(_("Auto Crop: detecting %d images..."), #paths))
  log_rotate()
  log(string.format("detect: %d images", #paths))

  -- Batch detached starten: Lua blockiert nicht -> Abbruch-Knopf lebt
  local py = find_python()
  if not py then
    dt.print_error("Auto Crop Negative: Python script not found")
    dt.print(_("Auto Crop: Fehler - Python nicht gefunden (siehe Log)"))
    return
  end
  local json_file = os.tmpname() .. ".json"
  local prog_file = os.tmpname() .. ".prog"
  local escaped = {}
  for _, fp in ipairs(paths) do
    escaped[#escaped+1] = string.format("'%s'", fp:gsub("'", "'\\''"))
  end
  -- stdout -> json_file (Ergebnis), stderr -> prog_file (Fortschritt);
  -- & startet Python im Hintergrund, echo $! liefert die PID.
  -- Schwellen EINMAL bei Spawn aus den Preferences lesen und Python direkt
  -- mitgeben: Python schreibt die gruen/gelb/rot-Queue selbst (siehe
  -- _run_batch_pipeline) und ist damit die alleinige Quelle dafuer - Lua
  -- pollt nur ereignisgesteuert und koennte sonst verzoegert oder gar
  -- nicht mehr finalisieren, wenn der Nutzer waehrend der Erkennung
  -- nichts anfasst.
  local t_green = dt.preferences.read(MOD_LT, "t_green", "float")
  local t_yellow = dt.preferences.read(MOD_LT, "t_yellow", "float")
  local cmd = string.format(
    "'%s' --batch %s --confidence-threshold %s --t-green %s "
      .. "2> '%s' > '%s' & echo $!",
    py, table.concat(escaped, " "), fmt_float_c(t_yellow, 3),
    fmt_float_c(t_green, 3), prog_file, json_file)
  log("spawn cmd: " .. cmd)
  local handle = io.popen(cmd, "r")
  local pid = handle and tonumber(handle:read("*l"))
  if handle then handle:close() end
  if not pid then
    os.remove(json_file); os.remove(prog_file)
    log("spawn fehlgeschlagen")
    dt.print_error("Auto Crop Negative: io.popen fehlgeschlagen")
    dt.print(_("Auto Crop: Fehler - Prozess konnte nicht gestartet werden"))
    return
  end

  batch_state.active = true
  batch_state.cancelled = false
  batch_state.pid = pid
  batch_state.json_file = json_file
  batch_state.prog_file = prog_file
  batch_state.images = images
  batch_state.t_green = t_green
  batch_state.t_yellow = t_yellow
  batch_state.t_start = os.time()
  batch_state.last_est = -1
  batch_state.toast_sec = 0
  batch_state.n_images = #paths
  batch_state.job = dt.gui.create_job(
    _("Auto Crop: detecting film frames"), true, batch_cancel)
  batch_state.job.percent = 0
  log(string.format("spawned pid=%d", pid))
  pcall(function()
    lt_widget.children[10].label = _("Auto Crop: 0 / %d Bilder analysiert...")
      :format(#paths)
  end)
  dt.print(string.format(
    _("Auto Crop: %d Bilder werden im Hintergrund analysiert - Abbruch ueber ✗"),
    #paths))

  -- Robuste Fertigstellung: NICHT nur auf zufaellige UI-Events warten
  -- (selection-changed, mouse-over, ...). Beobachtet: der Python-Prozess
  -- war laengst fertig (BATCHDONE stand in der Progress-Datei), aber
  -- check_batch() wurde nie wieder aufgerufen, weil der Nutzer waehrend
  -- des Wartens nichts angefasst hat - die Erkennung wirkte dadurch
  -- "haengengeblieben", obwohl das Backend laengst durch war.
  -- dt.control.sleep pumpt waehrenddessen die GTK-Eventloop durch (der
  -- Abbruch-Knopf bleibt reaktionsfaehig), blockiert also nicht wie ein
  -- OS-Sleep. batch_state.pid == pid schuetzt davor, dass zwei
  -- ueberlappende Aufrufe (Nutzer klickt zweimal) sich gegenseitig den
  -- Zustand kaputt pollen.
  while batch_state.active and batch_state.pid == pid do
    dt.control.sleep(300)
    -- pcall: ein Fehler hier (z.B. ein Bild-Objekt aus st.images wurde
    -- ungueltig) darf die Schleife nicht stillschweigend abbrechen - dann
    -- wuerde die Erkennung wieder "haengenbleiben", nur diesmal ohne jede
    -- Fehlermeldung. Im Log sichtbar machen und weiterprobieren.
    local ok, err = pcall(check_batch)
    if not ok then
      log("check_batch Fehler in Warteschleife: " .. tostring(err))
    end
  end
end

-- ═══ Darkroom: auto-apply on image load ═══════

dt.register_event("auto_crop_negative_darkroom_loaded", "darkroom-image-loaded",
  function(event, image)
    safe_check_batch()
    if event ~= "darkroom-image-loaded" or not image then return end
    local applied = apply_crop(image)
    log(string.format("darkroom-image-loaded %s: apply_crop -> %s",
      image.filename, tostring(applied)))
  end)

-- Fallback-Poller ueber haeufige Events, falls die Wartschleife in
-- detect_and_queue() aus irgendeinem Grund nicht mehr laeuft (Script neu
-- geladen, Fehler, ...).
dt.register_event("auto_crop_poll_selection", "selection-changed",
  safe_check_batch)
dt.register_event("auto_crop_poll_collection", "collection-changed",
  safe_check_batch)
dt.register_event("auto_crop_poll_mouseover", "mouse-over-image-changed",
  safe_check_batch)
dt.register_event("auto_crop_poll_pipe", "pixelpipe-processing-complete",
  safe_check_batch)

-- ═══ Darkroom: manual apply ═══════

local function apply_current()
  local images = dt.gui.action_images
  if not images or #images == 0 then
    dt.print(_("No image"))
    return
  end
  local n = 0
  for _, img in ipairs(images) do
    if apply_crop(img) then n = n + 1 end
  end
  if n > 0 then
    dt.print(string.format(_("Auto Crop: applied to %d image(s)"), n))
  else
    dt.print(_("Auto Crop: no queued crop for current image"))
  end
end

-- ═══ Alle Crops jetzt anwenden ═══════
-- Frueher ein Lighttable<->Dunkelraum-Rundlauf pro Bild, weil
-- dt.gui.action ohne sichtbaren, fokussierten Dunkelraum nicht
-- funktionierte. Mit dt.styles (siehe set_crop) faellt das komplett weg -
-- Styles lassen sich direkt aus dem Lighttable auf beliebig viele Bilder
-- anwenden, ganz ohne View-Wechsel.
local function apply_all_queued()
  local images = dt.gui.action_images
  if not images or #images == 0 then
    dt.print(_("Auto Crop: keine Bilder ausgewaehlt"))
    return
  end
  local queue = load_queue()
  local todo = {}
  for _, img in ipairs(images) do
    if queue[img.filename] then todo[#todo+1] = img end
  end
  if #todo == 0 then
    dt.print(_("Auto Crop: keine wartenden Crops in der Auswahl"))
    return
  end
  dt.print(string.format(_("Auto Crop: wende %d Crops an"), #todo))
  local n = 0
  for _, img in ipairs(todo) do
    local ok, err = pcall(function()
      if apply_crop(img) then n = n + 1 end
    end)
    if not ok then
      log("apply_all_queued: Fehler bei " .. img.filename .. ": "
          .. tostring(err))
    end
  end
  dt.print(string.format(_("Auto Crop: %d von %d Crops angewendet"),
    n, #todo))
end

-- ═══ Widgets ═══════

-- Ein Widget fuer beide Views (Lighttable + Darkroom)
lt_widget = dt.new_widget("box") {
  orientation = "vertical",
  dt.new_widget("label") {
    label = "<b>Auto Crop Negative</b>" },
  dt.new_widget("label") {
    label = _("Detect film frame on selected images and queue crops.") },
  dt.new_widget("separator") {},
  dt.new_widget("button") {
    label = _("Detect & Queue"),
    tooltip = _("Run three-pass detection, queue results for darkroom"),
    clicked_callback = function() detect_and_queue() end },
  dt.new_widget("separator") {},
  dt.new_widget("button") {
    label = _("Apply crop to current image"),
    tooltip = _("Apply queued crop from detection results"),
    clicked_callback = function() apply_current() end },
  dt.new_widget("separator") {},
  dt.new_widget("button") {
    label = _("Apply all queued now"),
    tooltip = _("Applies all queued crops to the current selection at "
      .. "once, directly from the lighttable."),
    clicked_callback = function() apply_all_queued() end },
  dt.new_widget("separator") {},
  dt.new_widget("button") {
    label = _("Undo crop (selected image(s))"),
    tooltip = _("Disables the crop module again on the currently "
      .. "selected image(s), if they were auto-cropped since the last "
      .. "undo. Adds a new history step (non-destructive) instead of "
      .. "removing the crop entry."),
    clicked_callback = function() undo_crop_selected() end },
  dt.new_widget("button") {
    label = _("Undo crop (all in this session)"),
    tooltip = _("Disables the crop module again on every image that was "
      .. "auto-cropped since the last undo, regardless of the current "
      .. "selection. Adds a new history step (non-destructive) instead "
      .. "of removing the crop entry."),
    clicked_callback = function() undo_crop_all() end },
  dt.new_widget("label") {
    label = _("After detection: open images in darkroom to apply crops.") },
  dt.new_widget("label") {
    label = "" },  -- Statuszeile: Fortschritt + Restzeit waehrend des Laufs
}

-- ═══ Registration ═══════

-- EINE Lib fuer BEIDE Views (Lighttable + Darkroom):
-- Workaround fuer darktable Issue #20371 – Lua-Libs ohne Container in der
-- aktiven View loesen "couldn't find a container" beim View-Wechsel aus
dt.register_lib(
  MOD_LT, _("Auto Crop Negative"), true, false,
  {[dt.gui.views.lighttable] = {"DT_UI_CONTAINER_PANEL_RIGHT_CENTER", 500},
   [dt.gui.views.darkroom] = {"DT_UI_CONTAINER_PANEL_RIGHT_CENTER", 500}},
  lt_widget, nil, nil)

dt.register_event("auto_crop_negative_shortcut", "shortcut",
  function(_, _) detect_and_queue() end,
  _("Auto Crop Negative: detect and queue selected images"))

dt.print_log("Auto Crop Negative: loaded")

-- ═══ script_manager interface ═══════

script_data.destroy = function()
  pcall(function() dt.gui.libs[MOD_LT].visible = false end)
end
script_data.destroy_method = "hide"
script_data.restart = function()
  pcall(function() dt.gui.libs[MOD_LT].visible = true end)
end
script_data.show = script_data.restart

return script_data

