# How-to: Negative scannen für die automatische Erkennung

Diese Anleitung fasst zusammen, was den Algorithmus (`auto_crop_negative.py`,
`film_scale.py`) zuverlässig macht — und was ihn ausbremst. Sie richtet sich nicht
danach, was fototechnisch "am schönsten" aussieht, sondern danach, was die Erkennung
messen kann. Hintergrund und Messungen: `bericht-erkennung-2026-09-22.md`,
`roadmap.md`.

## Die eine Regel, die am meisten bringt: Perforation mitscannen

Der wichtigste Trick des Algorithmus ist, dass der Lochabstand von 35-mm-Film
(4,7625 mm) eine physikalische Konstante ist. Daraus errechnet `film_scale.py` den
Abbildungsmaßstab der Rolle (px/mm) — unabhängig davon, wie unterschiedlich die
Motive selbst aussehen. Das funktioniert aber nur, **wenn die Perforation überhaupt
mit im Scan ist.**

- Lass beim Scannen an mindestens einer Seite (oben oder unten in der
  Transportrichtung des Films) einen sichtbaren Rand mit den Löchern stehen. Ein
  bis zwei Millimeter Luft genügen, mehr schadet nicht.
- Scanne **nicht** randscharf bis an den Bildinhalt heran, auch wenn es aufgeräumter
  aussieht. Sechs Rollen in den bisherigen Testdaten hatten die Perforation gar
  nicht im Scan — bei denen kann der Algorithmus die Rollengröße nur noch aus dem
  Vergleich mit anderen Rollen schätzen, mit deutlich schlechterer Trefferquote
  (rund 41 % statt rund 93 % Treffer, siehe Bericht).
- Der Algorithmus sucht die Perforation in den äußeren 24 % der kurzen Bildkante
  (`EDGE_FRAC` in `film_scale.py`). Sie muss also nicht mittig im Bild liegen —
  aber sie muss in diesem Randstreifen zu finden sein, nicht komplett weggeschnitten.

## Rahmen weder zu eng noch zu weit fassen

Der Algorithmus erwartet, dass der Negativrahmen ungefähr 80–95 % der Scandatei
füllt (Pitch liegt dann zwischen 7,0 % und 13,5 % der langen Bildkante,
`PITCH_REL` in `film_scale.py`). Zwei Fehler in beide Richtungen:

- **Zu eng gescannt** (nur das Bild selbst, ohne Rahmen und Perforation): Die
  Perforation fehlt komplett, siehe oben.
- **Zu weit gescannt** (viel schwarzer Rand, Filmhalter, Nachbarbilder mit im
  Scan): Der Takt lässt sich zwar noch messen, aber der Bezug zwischen Pixelgröße
  und Rollengröße wird instabiler, und der Algorithmus muss mehr raten, wo der
  eigentliche Bildinhalt beginnt.

Ein guter Anhaltspunkt: Rahmen und ein schmaler schwarzer Rand mit Perforation
sollen die Datei füllen, aber nicht mit viel Leerraum drumherum.

## Filmhalter sauber halten

Ein Filmhalter mit eigenem periodischem Muster am Rand (Riffelung, Zahnung,
Markierungen) kann vom Algorithmus mit der Perforation verwechselt werden, wenn
dieses Muster stärker hervortritt als die eigentlichen Löcher. In den Tests führte
ein solches Fremdmuster am äußersten Bildrand zu einem falschen, zu großen
Maßstab für die ganze Rolle. Der Algorithmus bündelt heute Treffer über mehrere
Streifenlagen, um einzelne Ausreißer zu entschärfen — trotzdem gilt:

- Filmhalter und Auflagefläche vor dem Scannen von Staub, Kratzern und
  Fremdmustern freihalten.
- Wenn der Halter selbst ein regelmäßiges Muster hat (z. B. Zahnung zum Einrasten),
  darauf achten, dass es nicht in den gescannten Bereich hineinragt.

## Eine Rolle konsistent scannen

Der Algorithmus bildet aus **allen Bildern einer Rolle zusammen** ein Maß für den
Perforationstakt (Autokorrelation wird über die ganze Rolle aufsummiert, weil der
Film in jedem Scan an derselben Stelle liegt und sich der Takt dadurch verstärkt,
Bildinhalt aber nicht). Das bringt zwei praktische Anforderungen:

- **Gleiche Auflösung/Scanner-Einstellung für die ganze Rolle.** Bilder mit
  abweichender Pixelgröße werden vom Algorithmus intern verworfen (sie passen
  nicht zu den übrigen ins gemeinsame Raster). Am einfachsten: Scanner-Preset für
  eine Rolle nicht mittendrin wechseln.
- **Gleicher Bildausschnitt/Rahmen für die ganze Rolle.** Je gleichmäßiger der Film
  im Scanner liegt, desto sauberer bündeln sich die Perforationstreffer über die
  Bilder der Rolle. Wechselnde Rähmchen oder stark unterschiedliche Ausrichtung
  vom Bild zu Bild schwächen das Signal.
- **Mindestens ein paar Bilder pro Rolle scannen**, nicht nur Einzelbilder. Ab drei
  Bildern reicht dem Algorithmus schon ein niedrigerer Score, um den Takt zu
  akzeptieren; bei zwölf Bildern kann er zusätzlich prüfen, ob zwei zufällige
  Hälften der Rolle sich gegenseitig bestätigen (`agree`) — das ist inzwischen das
  wichtigste Gütemaß und braucht mehrere Bilder, um überhaupt zu funktionieren. Bei
  Einzelscans (nur ein Bild "lose" digitalisiert) fehlt diese Bestätigung, und die
  Erkennung ist entsprechend vorsichtiger (siehe unten).

## Was für "Einzelphotos" ohne Rollenkontext gilt

Nicht jedes Bild stammt von einer vollständigen Rolle mit mehreren Aufnahmen —
manchmal gibt es nur ein einzelnes Negativ. Dann kann der Algorithmus die
Hälften-Bestätigung nicht durchführen und verlässt sich stärker auf einen hohen
Autokorrelations-Score im einzelnen Bild. Für diesen Fall gilt besonders:

- Perforation an **beiden** Rändern (oben und unten) mitscannen, falls möglich —
  mehr Streifenlagen mit sichtbarem Takt erhöhen die Chance auf einen sauberen
  Treffer schon in einem einzelnen Bild.
- Scharf und kontrastreich genug scannen, dass die Löcher als klare helle/dunkle
  Kanten erscheinen, nicht verwaschen.

## Belichtung/Entwicklung: hilft, ist aber nicht entscheidend

Ob der Scan bereits entwickelt/invertiert vorliegt oder als rohes, nicht
invertiertes Bild (nur Weißabgleich) — der Perforationstakt lässt sich in beiden
Fällen messen, weil er unabhängig vom Bildinhalt ist. Entwickelte Bilder liefern
im Schnitt eine etwas zuverlässigere Konfidenzeinschätzung; unentwickelte Rohbilder
retten aber gerade die Rollen, bei denen die entwickelte Version aus anderen
Gründen scheitert. Für die Perforationsmessung selbst ist die Belichtung also
zweitrangig — wichtiger bleibt, dass die Löcher überhaupt im Bild sind und scharf
genug abgebildet werden.

## Kurz zusammengefasst

1. Perforation mitscannen — an mindestens einer Kante, lieber an beiden.
2. Rahmen weder randscharf zuschneiden noch mit viel Leerraum umgeben.
3. Filmhalter sauber und ohne eigenes periodisches Muster im Scanbereich.
4. Eine Rolle mit gleicher Scanner-Einstellung und ähnlichem Bildausschnitt
   durchgängig scannen, nicht mittendrin wechseln.
5. Möglichst mehrere Bilder pro Rolle statt einzelner loser Scans — ab etwa
   zwölf Bildern kann der Algorithmus seine eigene Messung gegenprüfen.
6. Bei Einzelscans ohne Rollenkontext: beide Ränder erfassen, scharf und
   kontrastreich scannen.

Wer diese Punkte beachtet, landet in der Praxis in der Gruppe der Rollen, die
ihren Takt zuverlässig bestätigen — dort liegt die Trefferquote heute bei rund
93 % und die Präzision der automatisch übernommenen ("grünen") Ergebnisse bei
96 %, gegenüber deutlich schwächeren Werten für Rollen ohne verwertbare
Perforation.
