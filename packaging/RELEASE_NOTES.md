## Herunterladen und starten

Kein Python, kein Terminal nötig. Datei herunterladen, doppelklicken, den Ordner mit den abfotografierten
Negativen wählen – die Prüfung öffnet sich im Browser. Ein kleines Fenster zeigt den Fortschritt;
**Beenden** oder Schließen des Fensters beendet Kader.

**Windows** (10 und 11): `Kader-…-windows-x64.exe`
Die Datei ist nicht signiert. Windows zeigt deshalb beim ersten Start „Der Computer wurde durch Windows
geschützt“: auf **Weitere Informationen** und dann **Trotzdem ausführen** klicken.

**Linux** (Ubuntu 22.04, Mint 21, Debian 12 oder neuer): `Kader-…-x86_64.AppImage`
Nach dem Herunterladen einmal ausführbar machen: Rechtsklick → Eigenschaften → Berechtigungen →
„Als Programm ausführen“ (oder `chmod +x Kader-*.AppImage`), dann doppelklicken.

Statt den Dialog zu benutzen, kann man den Ordner auch direkt auf die Datei ziehen.

**darktable-Nutzer**: Ist darktable installiert, bietet der erste Start an, Kader als Plugin einzurichten
(darktable vorher schließen). Danach in darktable Bilder auswählen und „Review starten“. Ohne Plugin wählt man in
der Prüfung das Ziel „darktable-Sidecars“; darktable übernimmt den Zuschnitt beim Import des Ordners.
**RawTherapee-Nutzer**: Ziel „RawTherapee-Profile“ wählen, nach „Fertig“ öffnet „RawTherapee öffnen“ den Ordner.

RAW-Dateien werden ohne zusätzliche Programme entwickelt. Was sich geändert hat, steht in
[CHANGELOG.md](https://github.com/shrippen/kader/blob/main/CHANGELOG.md).
