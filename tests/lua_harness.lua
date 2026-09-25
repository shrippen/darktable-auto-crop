-- Testumgebung fuer kader.lua ohne darktable: ein kleiner Stub der
-- darktable-API. Aufruf: lua tests/lua_harness.lua <lua-script> <aktion> [args]
--   apply          "Plan anwenden" (liest KADER_CACHE/last_session)
--   open           "Pruefung oeffnen"
-- Bilder kommen aus der Umgebungsvariable HARNESS_IMAGES ("id|verzeichnis|datei;...").
-- Ausgabe: JSON-Zeilen mit den beobachteten Style-Aufrufen, Labels und Meldungen.

local script_path, action = arg[1], arg[2]
-- darktable ruft setlocale(LC_ALL, ""): unter deutscher Locale schreibt string.format Kommas
if os.getenv("HARNESS_LOCALE") then os.setlocale(os.getenv("HARNESS_LOCALE"), "numeric") end

local prints, styles_applied, registry, events = {}, {}, {}, {}
local images = {}
for item in (os.getenv("HARNESS_IMAGES") or ""):gmatch("[^;]+") do
  local id, dir, file = item:match("^(%d+)|(.-)|(.+)$")
  images[#images + 1] = { id = math.tointeger(tonumber(id)), path = dir, filename = file,
    width = 6000, height = 4000, red = os.getenv("HARNESS_LABELS") == "1", yellow = os.getenv("HARNESS_LABELS") == "1", green = os.getenv("HARNESS_LABELS") == "1", blue = os.getenv("HARNESS_LABELS") == "1", purple = os.getenv("HARNESS_LABELS") == "1" }
end

local function hex_to_floats(hex)
  -- op_params: 4 floats (little endian) + 2 int32
  local bytes = {}
  for i = 1, #hex, 2 do bytes[#bytes + 1] = string.char(tonumber(hex:sub(i, i + 1), 16)) end
  local raw = table.concat(bytes)
  local l, t, r, b = string.unpack("<ffff", raw)
  return { l, t, r, b }
end

local style_store = {}
local select_all = os.getenv("HARNESS_SELECT") == "1"
local dt = {
  gettext = { gettext = function(s) return s end },
  preferences = {
    register = function() end,
    read = function(_, name) return ({ t_green = 0.5, t_yellow = 0.3 })[name] end,
  },
  print = function(m) prints[#prints + 1] = m end,
  print_error = function(m) prints[#prints + 1] = "ERR:" .. m end,
  print_log = function() end,
  gui = { action_images = select_all and images or {}, views = { lighttable = 1, darkroom = 2 }, libs = {},
          create_job = function() return { percent = 0 } end },
  control = { sleep = function(ms) os.execute(string.format("sleep %.2f", (ms or 0) / 1000)) end },
  database = images,
  register_lib = function() end,
  register_event = function(name, ev, cb) events[ev] = cb end,
  styles = setmetatable({
    import = function(path)
      local f = io.open(path); local xml = f:read("*a"); f:close()
      local name = xml:match("<name>(.-)</name>")
      local entry = { name = name }
      for block in xml:gmatch("<plugin>(.-)</plugin>") do
        local op = block:match("<operation>(.-)</operation>")
        local params = block:match("<op_params>(.-)</op_params>")
        local enabled = block:match("<enabled>(%d)</enabled>") == "1"
        if op == "crop" then entry.params, entry.enabled = params, enabled
        elseif op == "ashift" then
          entry.ashift_bytes, entry.ashift_enabled = #params // 2, enabled
          entry.angle = string.unpack("<f", (params:sub(1, 8):gsub("%x%x", function(h) return string.char(tonumber(h, 16)) end)))
        end
      end
      style_store[#style_store + 1] = entry
    end,
    apply = function(style, image)
      styles_applied[#styles_applied + 1] = { file = image.filename, id = image.id,
        enabled = style.enabled, crop = hex_to_floats(style.params),
        angle = style.angle, ashift_enabled = style.ashift_enabled, ashift_bytes = style.ashift_bytes }
    end,
    delete = function(style)
      for i, s in ipairs(style_store) do if s == style then table.remove(style_store, i) break end end
    end,
  }, { __ipairs = function() return ipairs(style_store) end }),
  new_widget = function(kind)
    return function(t) t.kind = kind; registry[#registry + 1] = t; return t end
  end,
}
-- ipairs(dt.styles) muss die importierten Styles liefern
setmetatable(dt.styles, { __index = function(_, k) return style_store[k] end,
  __len = function() return #style_store end })

package.preload["darktable"] = function() return dt end
package.preload["lib/dtutils"] = function() return { check_min_api_version = function() end } end

local ok, err = pcall(dofile, script_path)
if not ok then io.stderr:write("SCRIPT ERROR: " .. tostring(err) .. "\n"); os.exit(2) end

if action == "exit" then
  if not events["exit"] then io.stderr:write("kein exit-Handler registriert\n"); os.exit(4) end
  events["exit"]()
  print('{"labels":[],"styles":[],"prints":[],"status":"","url_label":"","markup":0}')
  os.exit(0)
end
local wanted = ({ apply = "Plan anwenden", open = "Prüfung öffnen", start = "Review starten",
                  stop = "Server stoppen", reset = "Crop & Farben zuruecksetzen" })[action]
local button
for _, w in ipairs(registry) do
  if w.kind == "button" and w.label == wanted then button = w end
end
if not button then io.stderr:write("Knopf nicht gefunden: " .. tostring(wanted) .. "\n"); os.exit(3) end
button.clicked_callback()
if action == "reset" then
  if os.getenv("HARNESS_ONE_CLICK") ~= "1" then button.clicked_callback() end   -- zweiter Klick bestaetigt
end

local labels = {}
for _, img in ipairs(images) do
  labels[#labels + 1] = string.format('{"id":%d,"red":%s,"yellow":%s,"green":%s,"blue":%s,"purple":%s}', img.id,
    tostring(img.red), tostring(img.yellow), tostring(img.green), tostring(img.blue), tostring(img.purple))
end
local st = {}
for _, s in ipairs(styles_applied) do
  local function num(v) return (string.format("%.4f", v):gsub(",", ".")) end
  st[#st + 1] = string.format('{"id":%d,"enabled":%s,"crop":[%s,%s,%s,%s],"angle":%s,"ashift_enabled":%s,"ashift_bytes":%s}', s.id,
    tostring(s.enabled), num(s.crop[1]), num(s.crop[2]), num(s.crop[3]), num(s.crop[4]),
    s.angle and num(s.angle) or "null", tostring(s.ashift_enabled or false), tostring(s.ashift_bytes or "null"))
end
local pr = {}
for _, p in ipairs(prints) do pr[#pr + 1] = '"' .. p:gsub('"', '\\"') .. '"' end
local status, url_label = "", ""
local markup_labels = {}
for _, w in ipairs(registry) do
  if type(w.label) == "string" and w.label:find("<[%a/][^>]*>") then markup_labels[#markup_labels + 1] = w.label end
end
for _, w in ipairs(registry) do
  if (w.kind == "label" or w.kind == "section_label") and status == "" and w.label:lower():find("server") then status = w.label end
  if w.kind == "button" and w.label:find("^http") then url_label = w.label end
end
print(string.format('{"labels":[%s],"styles":[%s],"prints":[%s],"status":"%s","url_label":"%s","markup":%d}',
  table.concat(labels, ","), table.concat(st, ","), table.concat(pr, ","),
  status:gsub('"', '\\"'), url_label, #markup_labels))
