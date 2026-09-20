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

## Priorisierung

Phase 0 ist zwingend zuerst (ohne Daten kein Fortschritt). Danach hat
**Phase 2 (echtes zweites Signal)** den größten erwarteten Hebel, weil sie
die strukturelle Ursache angeht statt an Symptomen (Gewichte, Schwellen)
zu drehen - Phase 1, 3 und 4 sind ohne Phase 2 nur Feintuning auf einer
weiterhin fehleranfälligen Grundlage.
