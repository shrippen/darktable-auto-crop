# Roadmap: Trefferrate & Konfidenz-Kalibrierung

Stand 2026-09: die darktable-Integration (Erkennung -> Style-Anwendung,
Undo) funktioniert technisch zuverlässig. Das eigentliche Problem ist
jetzt fachlich, nicht technisch: die Trefferrate ist noch nicht gut genug,
und die Konfidenz lügt teilweise - mehrere als **grün** (== "sicher, wende
automatisch an") markierte Ergebnisse waren laut Nutzer stark falsch. Ein
Konfidenzwert, dem man nicht vertrauen kann, ist schlimmer als gar keiner,
weil er falsches Vertrauen erzeugt. Diese Roadmap sortiert die Ursachen
und schlägt eine Reihenfolge vor, in der man das angeht.

## Stand der Umsetzung (2026-09-20)

| Phase | Erledigt | Offen |
| --- | --- | --- |
| 0 Fehlerfälle einsammeln | Werkzeuge; Film 35 vermessen und als Referenz übernommen (`feedback_gt.json`); Baseline neu (98/103, 7 Filme); Fehlermuster der 5 Fehltreffer | Film 33/34 labeln (Handarbeit), ursprünglich beobachtete „stark falsche Grüne“ identifizieren |
| 1 Ground Truth verbreitern | Tuning/Holdout pro Film, Leave-One-Film-Out in `eval.py` | mehr Rollen/Formate, vor allem mehr Fehltreffer |
| 2 Zweites Signal | Textur innen/außen und unabhängige Kantenlage getestet: kein Nutzen | Perforation als Positionsreferenz (aufwendig, erst mit mehr Fehltreffern belegbar) |
| 3 `film_trust` | `film_trust` und Belichtungsdeckel aus der Konfidenz genommen (Daten: irreführend); Positions-Clamp geprüft, nicht nötig | – |
| 4 Neu kalibrieren | LOFO-Formelvergleich; neue Konfidenz `0.5·size + 0.5·edge`; Schwellen 0.5/0.3 am Holdout bestätigt | Wiederholen, sobald Film 33/34 und weitere Rollen Referenzen haben |
| 5 Feedback-Schleife | Gegenmelden, Faktoren sichtbar, Auswertung, Feedback fließt in `eval.py`/`calibrate.py` | – |

Priorität jetzt: **mehr Referenzdaten** (Film 33/34 und neue Rollen über die Web-UI). Jede Aussage oben stützt sich auf nur 5 Fehltreffer; mit mehr Daten `tools/eval.py`, `tools/calibrate.py --loo` und `tools/signal_probe.py` erneut laufen lassen.

## Praxistest der Companion-UI (2026-09-20)

Erster Durchlauf in einer echten darktable-Bibliothek: **36 Raw-Fotos** wurden
importiert, analysiert, in der Web-UI eingestellt und korrigiert und dann an darktable
übergeben, **Crops und Farblabels**. Auch das **nochmalige Korrigieren** (Zurück zur
Prüfung, zweite Revision, erneutes Anwenden) hat funktioniert.

Bestätigt damit (vorher offene Integrationsrisiken, siehe `companion-ui-plan.md`
Abschnitt 13): Export aus darktable, Übergabe per `plan.json`, Anwenden über
`dt.styles` in der GUI, Differenz-Anwenden nach Revision 2, Stopp des Servers
(Knopf und automatisch beim Schließen von darktable).

Folgen für diese Roadmap:
- Die **technische** Integration ist kein Engpass mehr; die fachlichen Phasen unten
  (Trefferrate, Konfidenz) bleiben der Kern.
- Erstmals gibt es **Ground Truth aus echter Nutzung** auf einer bisher unbekannten Rolle
  (Phase 0/1/5): Die Sitzung schreibt jede Korrektur nach `feedback.jsonl`
  (Crop-Korrekturen, Gruppenwechsel, mit erkanntem Crop und Konfidenz). Beim Test
  fielen je Sitzung etwa fünf manuelle Crops und rund zwanzig Gruppenwechsel an.
  **Achtung:** Sitzungen werden nach 14 Tagen aus `~/.cache/auto-crop-negative/`
  gelöscht. Vorher die wertvollen `feedback.jsonl`/`state.json` in
  `review_data/` sichern oder nach `reviews.json` übernehmen.
- Phase 5 (Feedback-Schleife): das Werkzeug steht, es fehlt die **Auswertung**
  (Feedback → `tools/eval.py`/`tools/calibrate.py`).

## Ausgangslage: warum "grün" trotzdem falsch sein kann

Die aktuelle Konfidenzformel (`apply_film_consensus()` in
`auto_crop_negative.py`):

```
conf = 0.45 * size_agree + 0.35 * edge_score + 0.20 * film_trust
conf *= exposure_factor
```

- **`size_agree`**: wie gut die Einzeldetektion zur Film-Konsens-Größe
  (Median über die Rolle) passt.
- **`film_trust`**: wie eng die Größen *innerhalb der Rolle* streuen, plus
  ob genug Bilder (>= 8) vorliegen.
- **`edge_score`**: wie steil/eindeutig die Kante war, die die lokale
  Konsens-Verfeinerung gefunden hat.
- **`exposure_factor`**: Deckel für unterbelichtete/kontrastarme Bilder.

Das Problem: **alle vier Faktoren sind Selbstbezug auf die eigene
Erkennung, kein Abgleich mit der Realität.**

- `size_agree`/`film_trust` messen nur, ob eine Rolle *in sich konsistent*
  ist. Wenn die Erkennung auf einer ganzen Rolle systematisch denselben
  Fehler macht (z. B. weil Lichtverhältnisse/Filmhalter/Nachbarframe auf
  dieser Rolle immer ähnlich aussehen), ist das Ergebnis maximal
  "einträchtig falsch" - genau das Muster, das hohe Konfidenz erzeugt.
- `edge_score` misst nur "war da eine scharfe Kante", nicht "war es die
  *richtige* Kante". Eine Perforation, ein Klebestreifen, eine
  Lichthof-Grenze oder der Rand des Filmhalters erzeugen ebenfalls scharfe
  Kanten.
- Es gibt aktuell **keine zweite, unabhängige Methode**, die zum Vergleich
  herangezogen wird. Pass A liefert nur das Seitenverhältnis, keine eigene
  Bounding-Box; Pass B und die Konsens-Verfeinerung sind im Kern dieselbe
  Kantensuche, nur mit unterschiedlichem Startpunkt.
- Die Schwellen (grün >= 0.50, gelb >= 0.30) und die Formel-Gewichte
  (0.45/0.35/0.20) wurden ausschließlich auf `review_data/reviews.json`
  kalibriert - **98 Bilder aus 6 Filmen (27-32)**. Die 100 % grün-Präzision
  in `tools/eval.py` gilt nur auf genau dieser Menge. Reale Rollen wie die
  vom Nutzer getestete "Film 35" sind darin nicht enthalten - die Formel
  wurde nie an unbekannten Rollen gegengeprüft.

Kurz: die Konfidenz ist aktuell ein Maß für "wie sehr stimmt die Rolle mit
sich selbst überein", nicht für "wie sehr stimmt der Crop mit der Realität
überein". Das erklärt das beobachtete Muster ziemlich präzise.

## Phase 0 (sofort, Voraussetzung für alles Weitere): Fehlerfälle einsammeln

Ohne die konkreten falschen grünen Ergebnisse zu kennen, ist jede weitere
Maßnahme Rätselraten.

- [ ] Die vom Nutzer beobachteten falschen grünen Crops identifizieren
      (Filmordner + Dateiname) und mit `start_review_gui.sh` den
      *tatsächlich richtigen* Crop von Hand eintragen - genau wie die
      bestehenden 98 Referenzbilder.
      **Stand (teilweise):** Auf der bisher unbekannten Rolle „Film 35“ (36 Raw-Fotos) sind über die Web-UI 5 Crops von Hand korrigiert worden. Umgerechnet auf die 2000-px-Skala der Testfotos sind das Nudges von höchstens 49 px, also **innerhalb der 60-px-Toleranz: kein falsches Grün gemessen.** (Eine frühere Fassung dieser Zeile sprach von 5 falschen Grünen; das war ein Skalierungsfehler, die Toleranz war nicht auf die 8280-px-Exporte skaliert.) Die vom Nutzer ursprünglich beobachteten „stark falschen Grünen“ sind in den Daten weiterhin nicht identifiziert.
- [ ] Dabei **auch Film 33 und 34** vervollständigen (liegen schon als
      Testfotos vor, aber ohne Review-Daten - aktuell komplett ungenutzt
      für Kalibrierung/Auswertung).
      **Stand: offen (Handarbeit).** Film 33 (37 Bilder) und 34 (28) haben weiter keine Review-Daten, Film 32 nur 1 von 9. Werkzeug steht: `./start_review_gui.sh --films 33 34`; korrigieren oder mit „Akzeptieren“ bestätigen, „Fertig“ schreibt nach `reviews.json`.
- [x] Für jeden neuen falschen Fall kurz notieren, *welches* der vier
      Symptome zutraf (Nachbarframe erwischt? Filmhalterkante statt
      Bildrand? Falsche Seite/Position bei richtiger Größe? Rolle mit
      wenigen Bildern?) - das entscheidet, welcher Punkt unten zuerst
      etwas bringt.
      **Erledigt (soweit aus den Daten möglich):** `feedback_report.py` unterscheidet automatisch Größe, Position und beides. Die 5 Fehltreffer der Referenzdaten (Film 27: 0165/0166/0175, Film 29: 0223, Film 31: 0264) liegen alle in gelb/rot; Sichtprüfung von drei davon: dunkle/kontrastarme Aufnahmen (0175 mit dunkler Vignette, 0223 fast schwarz, 0264 dunkel). Nachbarframe oder Filmhalterkante wurde nicht festgestellt.
- [x] `tools/eval.py` und `tools/eval_baseline.json` mit den erweiterten
      Referenzdaten neu laufen lassen, um eine ehrliche (niedrigere)
      Ausgangs-Precision/Recall-Zahl zu bekommen, statt sich weiter auf
      die alten, zu optimistischen 93/98 zu verlassen.
      **Erledigt:** Baseline neu gemessen und festgeschrieben: 98/103 Treffer über 7 Filme (Film 35 dazu, 5/5). Die alte 93/98 galt nur für die 6 Filme.

## Phase 1: Ground Truth breiter aufstellen

Kalibrierung auf 6 Filmen von einer Kamera/einem Aufbau ist zu schmal, um
zu wissen, ob die Formel generalisiert.

- [ ] Ground Truth auf **mehr, unterschiedliche Rollen** ausweiten -
      idealerweise verschiedene Kameras, Belichtungssituationen,
      Filmformate (nicht nur Kleinbild), Farb- und S/W-Negative.
      **Stand: offen.** Bisher Kleinbild und ein Aufbau; Film 35 liegt nur als Feedback aus Sitzungen vor, nicht in der Kalibrierbasis.
- [x] Datensatz in **Tuning-Set** und **Holdout-Set** aufteilen (z. B. pro
      Film, nicht zufällig gemischt - sonst leckt Wissen über eine Rolle
      zwischen Tuning und Test). Schwellen/Gewichte nur auf dem
      Tuning-Set anpassen, Erfolg nur auf dem Holdout-Set berichten.
      **Erledigt:** `tools/splits.json` teilt pro Film in Tuning (28, 29, 30, 32, 35) und Holdout (27, 31); `eval.py` wählt die Schwelle nur auf dem Tuning-Set und berichtet den Holdout getrennt. Der Holdout enthält bewusst Film 27/31, weil alle 5 Fehltreffer in Film 27/29/31 liegen (sonst gäbe es im Holdout nichts zu prüfen).
- [x] `tools/eval.py` um eine **Leave-One-Film-Out-Auswertung** erweitern:
      für jeden Film einmal so tun, als wäre er unbekannt (nicht Teil der
      film_trust/Cross-Film-Pools), und Precision/Recall nur auf diesem
      Film messen. Das deckt genau die Art von Überanpassung auf, die
      hier vermutlich vorliegt.
      **Erledigt:** `python tools/eval.py` gibt die LOFO-Auswertung aus (Schwelle ohne den gemessenen Film gewählt).

## Phase 2: Eine echte zweite Signalquelle einbauen

Der Kern des Problems ist der fehlende unabhängige Gegencheck. Ohne den
bleibt jede Gewichts-Feinjustierung Kosmetik auf derselben fehleranfälligen
Grundlage.

Kandidaten (nicht exklusiv, nach vermutetem Aufwand/Nutzen-Verhältnis
sortiert):

- [x] **Inhalts-Plausibilität der Crop-Region.** Ein echtes Foto hat
      innerhalb des Rahmens typischerweise eine andere
      Helligkeits-/Varianzverteilung als Filmbasis, Sprocket-Löcher oder
      der Tisch drumherum (oft sehr gleichmäßig hell/dunkel oder mit
      regelmäßigem Muster). Ein einfacher Vergleich "Varianz/Histogramm
      innerhalb vs. direkt außerhalb des vorgeschlagenen Crops" ist
      billig zu berechnen und bestraft z. B. einen Crop, der zur Hälfte
      auf dem Filmträger sitzt, unabhängig davon, wie "einträchtig" die
      restliche Rolle ist.
      **Getestet, kein Nutzen:** Textur (lokale Standardabweichung) direkt innen vs. außen am Crop-Rand trennt Treffer und Fehltreffer schlechter als der Zufall (AUC 0.32): der Filmrand mit Perforation und Korn ist selbst stark texturiert. Nicht integriert. Reproduzierbar mit `tools/signal_probe.py`.
- [ ] **Perforation/Sprocket-Löcher als physische Referenz**, sofern auf
      den Aufnahmen sichtbar (je nach Digitalisier-Rig). Perforationen
      haben einen bekannten, extrem konstanten Rasterabstand - wenn
      erkennbar, ist das eine vom Bildinhalt komplett unabhängige
      Positionsreferenz und würde die "einträchtig falsch"-Schwäche
      strukturell auflösen. Aufwand deutlich höher, aber potenziell die
      robusteste Lösung.
      **Offen, bewusst nicht angegangen.** Die Löcher sind auf den Scans sichtbar (Film 27 links, Film 29/31 oben), aber ein robuster Detektor mit Rasterphase ist aufwendig, und die Referenzdaten enthalten nur 5 Fehltreffer, an denen sich ein Nutzen nicht belegen ließe. Sinnvoll erst mit mehr Fehltreffern (Phase 1).
- [x] **Zweite, andersartige Kantendetektion** (z. B. ein anderer
      Gradient-/Schwellwert-Ansatz oder ein auf Kontrast statt Gradient
      basierendes Verfahren) und deren *Übereinstimmung* mit der
      bestehenden Methode als Signal nutzen, statt nur die Sicherheit der
      einen Methode zu betrachten. Zwei unabhängige Verfahren, die sich
      einig sind, sind ein deutlich stärkeres Signal als eine Methode, die
      sich selbst sehr sicher ist.
      **Getestet, kein ausreichender Nutzen:** Eine unabhängig aus dem Texturprofil bestimmte Kantenlage stimmt mit dem Crop kaum besser bei Treffern überein (AUC 0.58); die Zahl übereinstimmender Detektoren trennt mäßig (AUC 0.72), deutlich schwächer als `edge_score` (0.98). Nicht integriert.

## Phase 3: film_trust von "einträchtig" zu "einträchtig UND plausibel" machen

- [x] `film_trust` sollte nicht nur geringe Streuung *innerhalb* der Rolle
      belohnen, sondern zusätzlich prüfen, ob der Rollen-Konsens selbst
      plausibel ist (z. B. via Phase-2-Signal, oder indem auffällt, wie
      viele Bilder der Rolle `size_ok=false` waren - viele Ausreißer
      deuten eher auf einen falschen Konsens als auf viele falsche
      Einzelbilder hin).
      **Erledigt, anders als vermutet:** `film_trust` trennt Treffer und Fehltreffer schlechter als der Zufall (AUC 0.32; falsche Crops sind oft „einig falsch“). Ein Anteil von Ausreißern pro Rolle als Plausibilitätsmaß hat kein Signal (AUC 0.52). Deshalb geht `film_trust` nicht mehr in die Konfidenz ein (bleibt als Diagnosefaktor sichtbar).
- [x] Cross-Film-Clamp (aktuell nur "nach oben" gedeckelt, siehe
      `MIN_POOL_SUPPORT`/`CLAMP_TOL` in `apply_film_consensus()`) auf
      Plausibilität für *Position*, nicht nur Größe, prüfen - aktuell wird
      nur eine zu große Konsens-Größe korrigiert, eine falsche Position
      (z. B. konstant zu weit links) auf der ganzen Rolle würde nicht
      auffallen.
      **Geprüft, keine Änderung nötig:** Die Rollen-Zentren der Crops liegen alle zwischen 0.47 und 0.53 (kein systematischer Positionsversatz einer Rolle), und die Abweichung vom Rollen-Zentrum sagt Fehltreffer nicht voraus. Ein Positions-Clamp wäre ohne Datenbasis Spekulation.

## Phase 4: Erst danach neu kalibrieren

`tools/calibrate.py` (logistische Regression) wurde bisher verworfen,
weil zu wenige Fehltreffer und zu schmale Datenbasis unzuverlässige,
teils falsch vorzeichnete Gewichte lieferten. Mit einem breiteren,
mehrere Filme/Kameras umfassenden Datensatz (Phase 1) und einem echten
zweiten Signal als zusätzlichem Feature (Phase 2) wird ein Neuversuch
sinnvoll:

- [x] Logistische Regression erneut versuchen, diesmal mit
      Leave-One-Film-Out-Kreuzvalidierung statt einer einzigen
      Train/Test-Zahl, um Überanpassung sofort sichtbar zu machen.
      **Erledigt:** `python tools/calibrate.py --loo --from-json <eval.json>` vergleicht Formeln per Leave-One-Film-Out (mit verschachteltem LOFO für die Schwelle). Ergebnis: eine logistische Regression über die vier Faktoren ist schlechter (AUC 0.93) als die einfache Formel `0.5·size_agree + 0.5·edge_score` (AUC 0.98); Abdeckung sicherer Treffer bei 98 % Precision 86.7 % vs. 99 %. Die alte Formel: AUC 0.885, 84.7 %. Die einfache Formel ist jetzt in `auto_crop_negative.py` (Version `size+edge-v2`).
- [x] Schwellen (grün/gelb) danach separat aus der ROC-Kurve auf dem
      Holdout-Set ableiten, mit einer bewusst konservativen
      Ziel-Precision für grün (z. B. >= 98 % auf dem Holdout, nicht nur
      auf den Trainingsdaten).
      **Erledigt:** Auf dem Tuning-Set erreicht ein Ziel von 100 % Precision die Schwelle 0.49; der Holdout (Film 27, 31) hat dort 40/40 (100 %). Die Produktionsschwellen bleiben daher **grün ≥ 0.5, gelb ≥ 0.3**. Auf allen 103 Referenzen: grün 91 Treffer / 0 Fehltreffer (vorher 76 / 0), gelb 6 / 3. Grenze: nur 5 Fehltreffer insgesamt, die Zahlen sind grob.

## Nachtrag: alle 209 Testfotos als Ground Truth (2026-09-20)

Alle Bilder in `Testphotos/` sind jetzt von Hand gecroppt (Web-UI, `review_data/reviews.json`; 52 bestätigt, 7 als Problem
markiert, 70 mit Tilt-Angabe). Damit ist die alte Aussage „nur 5 Fehltreffer in 103 Bildern“ überholt: **194 von 209
Treffern** (Baseline vorher 186), 15 Fehltreffer.

- [x] Orientierung aus der Bildform statt aus der Einzeldetektion (behob 6 der 8 schwersten Fehlschläge).
- [x] Einpassen mit ±10 statt ±45 px Größenspielraum (die Referenzen einer Rolle streuen um 3–12 px).
- [x] Belichtungsdeckel in der Konfidenz wieder eingesetzt: falsche Grüne 11 → 1, dafür weniger Grün (139 von 209).
      **Das kehrt die Phase-3/4-Entscheidung um:** die damalige Aussage galt für 98 Referenzen (7 Filme), mit 209 nicht mehr.
- [ ] **Offen: Film 34.** 9 Fehltreffer, alle mit Oberkante 40–70 px unterhalb der sichtbaren Rahmenkante. Entweder ist das
      Ihre Vorgabe (etwas enger schneiden) oder ein Erkennungsfehler; das lässt sich nur mit einer Ansage klären, ob der Crop
      *auf* der Rahmenkante oder *innerhalb* liegen soll.
- [ ] Offen: Film 29 (Mittelformat 6x6, fast quadratisch): zwei Fehltreffer mit ~220 px seitlichem Versatz; 0346/0378 sind
      Bilder mit ausgebranntem Himmel/Rand, wo der Crop nach Inhalt gesetzt wurde.
- Achtung Aussagekraft: Es wurde an allen 209 Bildern gleichzeitig gemessen und getunt; `tools/splits.json` ist nicht mehr
  sauber getrennt. Für einen ehrlichen Test braucht es neue, nicht angesehene Filme.

## Nachtrag: weitere Erkennungsmethoden geprüft (2026-09-20)

Auf den 209 Referenzen ausprobiert, **keine übernommen** (Stand 194/209; Rohdaten mit `tools/eval.py --json`):

- **Statische Maske pro Rolle:** Der Film liegt in allen Scans einer Rolle an derselben Stelle (Verschiebung per
  Phasenkorrelation ≈ 0 px), das Median-Bild zeigt nur die äußere Filmkante (Halter/Scanrand), keine Crop-Kanten.
  Die Crop-Position streut ±10–25 px, weil die Bilder auf dem Film unterschiedlich sitzen, nicht weil der Film wandert.
  Ausrichten am Median senkt die Streuung nicht (teils steigt sie). Kein Nutzen.
- **Homogenität außen / Kantenkonsistenz / Farbschritt als Konfidenz-Merkmal:** Einzel-AUC bis 0,87 (Farbschritt
  Minimum über die Kanten) gegenüber 0,85 der heutigen Konfidenz; im Leave-One-Film-Out ergibt eine logistische
  Kombination 144 → 152 Grüne bei 98 % Präzision, ohne Verbesserung der AUC (0,81). Im Rauschen, nicht übernommen.
- **Farbkanäle statt Graubild beim Einpassen** (max, min, R, G, B, Sättigung, Grau+Sättigung): 194–196 Treffer,
  Film 34 bleibt unverändert. Kein belastbarer Gewinn.

Zweite Runde (alle sechs Ideen, ebenfalls nicht übernommen; Bezug 194/209):

- **Kontrastnormalisierung vor dem Einpassen** (log, sqrt, Gamma, Dehnung, CLAHE, lokale Normierung): 192–195 Treffer.
  Nur CLAHE hebt die Leave-One-Film-Out-Abdeckung leicht (159 statt 151 Grüne bei gleicher Präzision, 96,4 %), Treffer
  unverändert. Im Rauschen.
- **Gelerntes Kanten-Offset-Modell** (lineares Filter auf dem 1D-Profil quer zur Kante, Softmax über Kandidaten,
  Leave-One-Film-Out): Kantenfehler gleich (77,6 % ≤ 10 px gegen 77,3 % heute), Treffer 186–193. Kein Gewinn.
- **Perforation als Anker:** Die Lochreihe beginnt bei den meisten Rollen am Bildrand (Median +1 px), bei Film 34 aber
  18 px darunter. Nur Unterkante bei Querformat: +2 Treffer, 0 verloren; auf alle vier Seiten verallgemeinert: 0 gewonnen,
  1 verloren (falsche Löcher im dunklen Bildinhalt). Zu fragil.
- **Lücke/Nachbarbild als zweite Seitenkante** (gleichmäßiges Band außerhalb): 177 Treffer, schlechter.
- **Korrekturen im Betrieb lernen** (mittlerer Kantenversatz der ersten k korrigierten Bilder einer Rolle auf den Rest):
  k=2 −1,1 Punkte, k=3 +0,2, k=5 +0,2, k=8 +1,4 Punkte Trefferquote. Erst ab etwa 8 Korrekturen ein kleiner Gewinn.
- **Pixelklassifikator statt Kantensuche** (Gradient Boosting auf 20 Merkmalen je Pixel, Box aus der Maske,
  Leave-One-Film-Out): 138 von 209 Treffer. Bei nur 9 Rollen zu wenig Vielfalt (Film 29/31 mit anderem Format: 0 Treffer).
- **Pixelklassifikator, zweiter Versuch** (auf Wunsch: 1000 px lange Kante statt 1/8, keine absoluten Lagemerkmale,
  als Zusatzsignal nur im „vertrauten“ Bereich; Gradient Boosting, 200 Bäume, Leave-One-Film-Out):
  142 von 209 Treffern allein (vorher 138). Film 27/30/33 (35/35, 28/28, 37/37) wie der Detektor, Film 29/31 ~0, Film 34 9/28.
  Die Selbsteinschätzung des Modells ist gut (Trefferquote in „außen sicher ≥ 0,95 und unentschieden ≤ 3 %“: 64/64),
  der Domänenabstand zum Training dagegen kaum (AUC 0,59). **Aber:** genau in diesem Bereich trifft der Detektor
  ebenfalls 64/64. Uneinigkeit gibt es 4-mal, dann liegt der Detektor 3-mal richtig, das Modell 2-mal. Als Konfidenz-
  Merkmal keine Verbesserung (AUC 0,80–0,81 gegen 0,85 heute), als Prüfer stuft es nur 1–4 der 70 Nicht-Grünen hoch.
  Beide Verfahren scheitern an denselben kontrastarmen Bildern; das Modell liefert daher kein unabhängiges zweites Signal.
  Skripte lagen im Scratchpad (nicht im Repo); sinnvoll erneut zu prüfen, sobald weitere Filme als Trainingsdaten da sind.
- Beobachtung zur Auswertung: Das Treffer-Kriterium prüft nur Breite und Höhe, nicht die Position. Versätze ohne
  Größenfehler (z. B. Film 29, 0222) zählen als Treffer.

Fazit: Die verbleibenden Fehltreffer (Film 34, Film 29) sind keine Frage des Kantensignals, sondern der Vorgabe
(Crop auf oder innerhalb der Rahmenkante) bzw. fehlender sichtbarer Kante.

## Nachtrag: 4106 weitere Testfotos, Auswertung gegen darktable-Crops (2026-09-21)

Neue Daten: `tools/convert_testphotos.py` hat alle übrigen Raws (Steinfeldts + Eigene, 145 Rollen) in ungecroppte 2000-px-JPEGs
umgewandelt (Originale und Sidecars unverändert). 1662 Sidecars enthalten einen früheren darktable-Crop, 498 davon mit
Drehung; sie liegen in `review_data/darktable_crops.json` und dienen mit `tools/eval_darktable_crops.py` als Referenz
für 62 weitere Rollen (dazu deine 209 Handcrops: zusammen 71 Rollen, 1871 Referenzen). Wichtig: Diese Crops sind älter und
teils bewusst enger als die sichtbare Rahmenkante (Forst, Stellwerk, Freibad 2, Altona: Referenz 40–130 px kleiner);
solche Rollen sind Konvention, kein Erkennungsfehler.

**Ausgangslage:** 90,4 % Treffer auf den 209 Handcrops, aber nur ~70 % auf den darktable-Rollen (Entwicklungshälfte 68,7 %,
Prüfhälfte 72,7 %), und 18 % der grünen Bilder waren falsch (auf den 209: 0,7 %).

**Ursachen (gemessen):**
- Pass A misst bei 12 von 71 Rollen ein falsches Seitenverhältnis (1,06, 1,10, 1,14, 2,3, 2,7, 8,6 statt 1,5). Trefferquote
  dort 21 %, bei den 59 übrigen 84 %. Betroffen sind vor allem Farbnegative.
- Die Rohbox allein trifft nur bei 34 % der Bilder (auch mit richtigem Seitenverhältnis). Erst Rollen-Median und der
  Cross-Film-Abgleich der Größe heben das auf ~84 %.
- **Der Cross-Film-Abgleich ist instabil.** Eine einzige zusätzliche Rolle (Film 25, 11 Bilder) im Pool lässt ~14 andere
  Rollen einbrechen (Film 1: 27 → 4, Film 10: 35 → 2), weil der Pool-Cluster kippt. Jede Rolle *einzeln* verarbeitet
  trifft nur 55,9 % (Stapel aus allen 71: 73,1 %); Stapel aus 5 Zufallsrollen streuen zwischen 1 % und 89 %. Reproduzierbar mit
  `tools/eval_darktable_crops.py --single`. Ein fester Verkleinerungsfaktor statt des Pools ist deutlich schlechter
  (Faktor 0,98: Prüfhälfte 66,7 %; ab 0,96 < 50 %), Pool je Rolle statt je Bild ebenfalls.
- Die Rahmengröße ist zwischen Rollen nicht konstant (Referenzen 1687×1126 bis 1910×1261). Eine globale Konstante
  (auch die exakt richtige) bringt 70–78 %.

**Übernommen:** Konfidenz `size+edge*exposure*roll-v4` (siehe CHANGELOG). Rollen-Verlässlichkeit aus Größenstreuung,
Abgleich-Umfang und Seitenverhältnis-Abweichung. AUC 0,74 → 0,85, Präzision der Grünen 79,7 % → 91,6 % (Prüfhälfte
81,7 % → 95,6 %), auf den 209 unverändert. Rollen mit Faktor ≥ 0,7 (36) treffen zu 89 %, darunter (35) zu 57 %; die
Totalausfälle sind damit fast alle erkannt.

**Geprüft und verworfen** (jeweils gegen Entwicklungs-, Prüf- und 209er-Satz):
- Kontrastnormalisierung, Farbkanäle, Pixelklassifikator, gelerntes Kantenmodell, Perforation (als Position; als Maßstab siehe Nachtrag 2026-09-22), Nachbarlücke, Korrekturen
  lernen (Runde 2, siehe oben): kein belastbarer Gewinn.
- Statische Rollenmaske / Kantenpaar-Schätzung der Rollengröße: findet die feste Halteröffnung (+27/+49 px zu groß), die
  Bildkante ist nur ein zweiter, etwas kleinerer Peak; „kleinster starker Peak" trifft 60 % der Rollen auf 30 px.
- 3:2 für alle Rollen erzwingen (67,8 % Prüfhälfte), Auswahl der besseren Hypothese je Rolle nach Konfidenz oder
  Rollenfaktor, auch mit auf verlässliche Rollen beschränktem Pool: die gewechselten Rollen werden besser, andere brechen
  ein (Pool-Kippen), netto schlechter; bei Film 14/Film 4 steigt die Konfidenz, die Treffer bleiben 0.
- Zweistufiges Einpassen (Größe aus eingepassten statt Rohboxen): ohne Pool besser (Entwicklung 283 → 342), mit Pool nicht.
- Globales Schrumpfen der Box um 8–32 px: verbessert die 209 (189 → 201), verschlechtert die darktable-Crops
  (Prüfhälfte 683 → 554); die Referenzsätze haben verschiedene Konventionen.
- Pool-Statistik (Median, 25./35./65. Perzentil statt dichtestem Cluster): Cluster ist am besten; Perzentil 25 bricht auf
  3 % zusammen. Pool-Schwelle 1,00–1,02 statt 1,03: gleich gut.

**Empfehlung / offen:**
- [ ] **Vorwissen aus bekannten Rollen speichern** (löst die Einzelrollen-Schwäche): die Größen bestätigter Rollen
      (reviews.json, akzeptierte Crops, ggf. darktable-Crops) als Pool für neue Läufe ablegen, ähnlich einer Bibliothek
      „Rohgröße → Rahmengröße". Offline: Rohschätzung + Korrektur aus den 10 ähnlichsten bekannten Rollen bringt 41 von 65
      Rollen auf ±45 px (ohne Korrektur 28).
- [ ] Pass A (Seitenverhältnis) für Farbnegative robuster machen; solange es fehlt, erscheinen die betroffenen Rollen
      dank `roll_factor` gelb/rot statt fälschlich grün.
- [ ] Pool stabilisieren (Cluster-Wahl kippt bei 3 % Abstand); Ansätze müssen Einzelrolle und Stapel gleichzeitig verbessern.

## Nachtrag: Schräglage (2026-09-20)

- [x] Schräglage messen und in der Web-UI anzeigen (`measure_skew` in `auto_crop_negative.py`).
- [x] Geradestellen über darktable (`ashift` + `crop`, Parameter gegen darktable 5.6.1 mit `darktable-cli` geprüft:
      Rotation = Messwert richtet aus, die Ausgabe ist die Bounding-Box, Version 5 mit 892 Byte Parametern).
- [ ] **Offen:** Messung an *echt* schräg fotografierten Bildern (die Referenzen haben höchstens 0.53°) und das
      Anwenden über `dt.styles` in der echten darktable-Oberfläche (im Test nur gegen den Lua-Stub und `darktable-cli`).
- [ ] **Offen:** Erkennung selbst arbeitet weiter mit achsparallelem Rechteck; bei ≥ 3° könnte eine Erkennung *im
      geradegestellten Bild* genauer sein (Neu-Erkennung nach dem Drehen).

## Phase 5: Feedback-Schleife aus echter Nutzung

Damit sich das Problem "grün war falsch" nicht wiederholt, ohne dass es
auffällt:

- [x] Einen leichten Weg schaffen, ein falsches automatisches Crop direkt
      aus darktable heraus gegenzumelden (z. B. ein Button "als falsch
      markieren", der Dateiname + berechnete Werte in eine Log-/Review-
      Datei schreibt) statt den Umweg über manuelles Wiederfinden im
      Review-GUI.
      **Erledigt (anders gelöst):** In der Web-UI genügt eine Korrektur oder die Rot-Markierung; jede Änderung geht mit erkanntem Crop und Konfidenz in `feedback.jsonl`, der Ordnermodus schreibt `reviews.json`. Kein Knopf in darktable nötig.
- [x] `_conf_parts` (bereits im Python-Code vorhanden) im Lua-Log oder in
      der GUI sichtbar machen, damit bei einem falschen grünen Ergebnis
      sofort erkennbar ist, welcher der vier Faktoren die Fehleinschätzung
      verursacht hat, ohne erst manuell nachrechnen zu müssen.
      **Erledigt:** Der Crop-Editor zeigt die vier Faktoren mit Balken und markiert den schwächsten; sie stehen auch im `feedback.jsonl`. Gilt für Analysen ab diesem Stand (ältere Sitzungen haben keine Faktoren gespeichert).

- [x] Auswertung des gesammelten Feedbacks: `tools/feedback_report.py` (Korrekturrate je
      Gruppe, falsches Grün mit Symptom, schwächster Faktor, Gruppenwechsel, Export als
      Ground Truth). Erster Lauf auf den 4 Testsitzungen (36 Bilder): 22 grün, davon
      5 leicht nachkorrigiert (alle innerhalb der Toleranz), 6 gelb und 8 rot ohne Korrektur;
      ein rot eingestuftes Bild hat der Nutzer nach grün verschoben.
- [x] Feedback in `tools/eval.py` und `tools/calibrate.py` einspeisen (Ground Truth aus
      `--export-gt` zusammen mit `reviews.json` auswerten; die Bilder liegen als Raw
      außerhalb von `Testphotos/`).

## Nachtrag: Perforation als Maßstab, unentwickelte Raws (2026-09-22)

**Übernommen: Perforations-Takt als Maßstab** (`film_scale.py`). Der frühere Versuch „Perforation als Positionsanker“ (oben,
verworfen) und dieser Ansatz sind verschieden: hier liefert der Lochabstand (4,7625 mm) die *Größe* des Rahmens für die ganze Rolle,
nicht die Position. Stapel aus allen 62 darktable-Rollen: 1040/1662 (62,6 %) → 1452/1662 (87,4 %); Grüne 80,4 % → 93,5 % richtig;
209 Handcrops 194 → 196. Die Größe der Rolle hängt damit nicht mehr am Cross-Film-Pool, der schon bei einer weiteren Rolle kippte.
Weiterhin ohne Erfolg: Leipzig (2/34), Freibad 3, Nachts Weihnachtsmarkt, Viktorianisches Wasser, Küste 1/2 (Takt-Score < 0,30).
Die Crop-Konvention (36,2 × 24,1 mm) wurde auf denselben Referenzen abgestimmt; ein Teil des Gewinns kann Anpassung sein.

**Geprüft und nicht übernommen:**
- *Nachführung der Rollengröße* per Kantenevidenz (`adapt` 8/16 px): 1449/1450 gegen 1452, Handcrops 196/195 gegen 196. Bleibt aus.
- *Unentwickelte, nicht invertierte Raws* als Erkennungsquelle (`convert_testphotos.py --unedited`, nur Weißabgleich bleibt):
  1397/1662 (84,1 %) gegen 1452 (87,4 %), AUC der Konfidenz 0,61 gegen 0,75. Zwei Rollen (Altona, Film 1) brachen komplett ein, weil
  die Takt-Messung von einem Fremdpeak in einer einzelnen Streifenlage getäuscht wurde. Mit der Konsens-Messung (jetzt im Code):
  Raw 1453 (87,4 %), entwickelt 1454; die Konfidenz auf Raw bleibt schlechter (AUC 0,65). Das Raw rettet Rollen, an denen die
  entwickelten Bilder scheitern (Leipzig 2 → 28/34, Küste 1/2 0 → 6/7, Freibad 3 0 → 4/5), und verliert in anderen (Cayeux 34 → 29).
- *Kombination beider Läufe* (je Rolle die höhere mittlere Konfidenz bzw. der höhere Takt-Score, je Bild die höhere Konfidenz):
  höchstens 88,3 % gegen 87,5 %; die Obergrenze bei perfekter Wahl je Rolle liegt bei 90,4 %. Der doppelte Export lohnt nicht.
- *Neu-Kalibrierung der Grün-Schwelle* auf Raw: für 90 % Precision wäre 0,63 nötig (Abdeckung 37–42 %), 95 % nur bei 0,91
  (Abdeckung 13 %); 98 % erreicht keine Schwelle. Auf entwickelten Bildern reicht 0,50 für 93,5 %/79 % Abdeckung.
  Auswahl der Schwelle je auf ganzen Rollen (2-fold, Leave-One-Roll-Out), nicht auf demselben Bild.

## Nachtrag: die Rolle als eigener Zeuge (2026-09-22, Details in `bericht-erkennung-2026-09-22.md`)

Ein Durchgang mit dem Ziel, die Trefferquote zu heben, hat vor allem gezeigt, dass sie die falsche Zielgröße ist:

- **Der Fehler ist ein konstanter Versatz je Rolle**, keine Streuung innerhalb der Rolle. Kennte man je Rolle nur den
  richtigen Größen-Versatz, wären es 1553/1662 (93,4 %) statt 87,5 % — mehr als rund 6 Prozentpunkte ist an dieser Stelle
  nicht zu holen, egal mit welchem Verfahren.
- **Die Takt-Messung ist nicht der Engpass.** Zwei Hälften einer Rolle getrennt gemessen weichen im Median um 0,13 %
  voneinander ab, der Fehler gegen die Referenz-Crops beträgt 1,03 %. Die Differenz korreliert mit 0,63 mit dem
  Bildausschnitt des Scanners: zwei Digitalisier-Sitzungen mit unterschiedlicher Crop-Gewohnheit (36,49 mm gegen 35,82 mm,
  Streuung innerhalb der Gruppe nur 0,51 % / 0,27 %). Der Rest ist Gewohnheit, nicht Physik. Stichprobengröße
  (6/12/24/alle Bilder) ändert daran nichts.
- **Übernommen** (siehe Changelog): die Übereinstimmung der Hälften als Gütemaß statt der Peakhöhe, und ein Deckel auf die
  Konfidenz der Rollen ohne bestätigten Maßstab. Trefferquote gleich, aber AUC 0,749 → 0,835 und 98 % Präzision erstmals
  erreichbar. Damit ist der Punkt „die Konfidenz lügt“ aus der Einleitung für die Rollen mit Maßstab weitgehend erledigt;
  für die Rollen ohne Maßstab ist er nicht gelöst, sondern nur noch ehrlich ausgewiesen.

**Geprüft und nicht übernommen:**
- *Crop-Konvention neu abstimmen*: der Sweep ist um 36,2 mm herum flach (36,0–36,1 → 1459, 36,2 → 1454, 36,3 → 1449).
  Zwei Konventionen je Scan-Gruppe brächten 1470 (88,4 %), eine je Rolle optimale 1507 (90,7 %) — beides Anpassung an die
  Referenz, kein übertragbarer Gewinn. 36,2 bleibt.
- *`REFINE_SIZE_SLACK`* (0/4/10/20 px): 1452/1457/1454/1446 Treffer, also Rauschen; es verschiebt nur Abdeckung gegen
  Genauigkeit. Das erklärt auch, warum die frühere Nachführung (`adapt`) nichts brachte: der Zug der Kanten ist bei ±10 px
  abgeschnitten (49 von 62 Rollen liegen in mindestens einer Achse am Anschlag) und korreliert nur mit 0,22 mit dem
  tatsächlichen Fehler.
- *Obertonkamm, gepoolte Autokorrelation, größere Stichprobe* für die Takt-Messung: ohne Gewinn, weil die Messung bereits
  genauer ist als ihr Fehler.
- **Nicht behebbar ohne neue Scans:** sechs Rollen (*Nachts Weihnachtsmarkt*, *Viktorianisches Wasser*, *Küste 1*, *Küste 2*,
  *Freibad 3*, *Prag 3*) liefern gar keinen Takt. Die Sichtprüfung der Scans zeigt: die Perforation ist dort nicht mit
  digitalisiert, der Rahmen füllt die Datei. Diese Rollen brauchen einen Neu-Scan mit Rand.

## Priorisierung

Phase 0 ist zwingend zuerst (ohne Daten kein Fortschritt). Danach hat
**Phase 2 (echtes zweites Signal)** den größten erwarteten Hebel, weil sie
die strukturelle Ursache angeht statt an Symptomen (Gewichte, Schwellen)
zu drehen - Phase 1, 3 und 4 sind ohne Phase 2 nur Feintuning auf einer
weiterhin fehleranfälligen Grundlage.
