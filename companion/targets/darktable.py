"""darktable: das Lua-Plugin liest plan.json ("Plan anwenden") und schreibt result.json."""
from . import Target, DARKTABLE


class DarktableTarget(Target):
    name = DARKTABLE
    external = True
    straightens = True      # Modul "Drehen und Perspektive" (ashift)
