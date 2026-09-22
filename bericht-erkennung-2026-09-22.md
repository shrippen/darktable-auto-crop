# Bericht: Wo die Erkennung heute steht und was sie besser macht

Stand 2026-09-22, Nachtlauf. Grundlage ist `main` bei `8de54dd` (Perforations-Maßstab mit Konsens-Messung).
Alle Zahlen sind selbst gemessen; die Skripte liegen im Sitzungs-Scratchpad und sind unten benannt.

**Kurzfassung.** Die Trefferquote ist nicht das Problem — sie lässt sich mit keinem der geprüften Mittel
nennenswert über 87,5 % heben, und die Obergrenze liegt ohnehin nur bei 93,4 %. Das Problem ist die
Konfidenz, genau wie es die Roadmap beschreibt. Eine Änderung behebt das messbar: Rollen ohne bestätigten
Perforationstakt dürfen nicht mehr grün werden. Damit steigt die Präzision der Grünen von 93,4 % auf
96,0 %, die Fläche unter der ROC von 0,749 auf 0,835 — und **98 % Präzision wird zum ersten Mal überhaupt
erreichbar** (vorher: bei keiner Schwelle). Der Patch liegt fertig vor, 83 Tests grün.

---

## 1. Ausgangslage

Zwei Referenzsätze, wie gehabt:

| Satz | Umfang | Trefferquote heute | AUC | grün (≥ 0,50) | Präzision | falsch-grün |
| --- | --- | --- | --- | --- | --- | --- |
| darktable-Crops (62 Rollen) | 1662 Bilder | 1454 (87,5 %) | 0,749 | 1221 | 93,4 % | 80 |
| Handcrops Film 27–35 | 209 Bilder | 197 (94,3 %) | 0,826 | 136 | 100 % | 0 |

Kriterium unverändert: |dW| und |dH| < 60 px bei 2000 px langer Kante. Bei den Handcrops zählen die
*starken* Referenzen (`reviews.json`), wie `tools/eval.py` sie misst; die 24 schwachen aus dem Feedback
überschreiben sie nicht.

### 1.1 Die Fehler sind ein Versatz pro Rolle, keine Streuung

Das ist der wichtigste Befund der Bestandsaufnahme (`bias.py`). Je Rolle den Median-Versatz und die
Streuung darum:

* Die Streuung innerhalb einer Rolle ist klein — bei 38 der 62 Rollen liegt die mittlere absolute
  Abweichung vom Rollen-Median unter 12 px, bei 46 Rollen unter 15 px, bei 58 Rollen unter 25 px.
* Der Median-Versatz der Rolle dagegen läuft von −791 px bis +308 px.

Kennte man je Rolle nur den richtigen Größen-Versatz, wären es **1553/1662 = 93,4 %** statt 87,5 %.
Alles, was an der Einzelbild-Erkennung gedreht wird, spielt unterhalb dieser Grenze; der Hebel ist die
Rollengröße. Das bestätigt den bisherigen Weg (Perforation als Maßstab) und begrenzt zugleich, was noch
zu holen ist: **höchstens 6 Prozentpunkte.**

### 1.2 Die Rollen zerfallen sauber in zwei Gruppen

| | Bilder | Treffer | grün | Präzision der Grünen |
| --- | --- | --- | --- | --- |
| Rollen **mit** anerkanntem Takt (51 von 62) | 1490 | 92,8 % | 1130 | **96,0 %** |
| Rollen **ohne** anerkannten Takt (11 von 62) | 172 | 41,3 % | 91 | **61,5 %** |

Von den 11 Rollen ohne Maßstab liefern 6 überhaupt keinen Takt (Abschnitt 2.4); bei den übrigen 5
(*Leipzig*, *Altona Sept 89*, *Film 2*, *Film 24*, *Film 95*) bleibt der Score unter der Schwelle 0,30.

Die 172 Bilder ohne Maßstab stellen 35 der 80 falschen Grünen. Allein die Rolle *07.07.2014 – Leipzig*
liefert 23 grüne Ergebnisse, von denen **kein einziges** richtig ist (Konfidenz-Median 0,66). Das ist
genau der Fall, den die Roadmap als „Konfidenz lügt" beschreibt.

---

## 2. Geprüfte Ideen

### 2.1 Größen-Spielraum beim Einpassen (`REFINE_SIZE_SLACK`) — kein Hebel

Beobachtung vorab: bei 49 der 62 Rollen liegt der Median-Zug des Einpassens in mindestens einer Achse
**genau auf dem Anschlag von 10 px**, bei 25 Rollen in beiden. Das Signal ist zensiert — und erklärt
nebenbei, warum die optionale
Nachführung `adapt` im letzten Lauf nichts brachte: die Korrelation zwischen dem Zug des Einpassens und
dem tatsächlichen Größenfehler der Rolle beträgt nur 0,22, weil der Zug fast überall am Anschlag klebt.

Vier volle Läufe über alle 3666 Bilder:

| Spielraum | Treffer | AUC | grün | Präzision | Abdeckung |
| --- | --- | --- | --- | --- | --- |
| 0 px | 1452 (87,4 %) | 0,747 | 920 | 93,8 % | 59,4 % |
| 4 px | 1457 (87,7 %) | 0,770 | 1102 | 94,2 % | 71,2 % |
| **10 px (heute)** | **1454 (87,5 %)** | 0,749 | 1221 | 93,4 % | 78,5 % |
| 20 px | 1446 (87,0 %) | 0,725 | 1277 | 92,9 % | 82,0 % |

Die Trefferquote bewegt sich über den ganzen Bereich um 11 Bilder — das ist Rauschen. Was sich ändert,
ist nur der Tausch zwischen Abdeckung und Präzision, und der entsteht daher, dass der Kantenscore (und
damit die Konfidenz) selbst vom Spielraum abhängt. **Nicht ändern.** Die These „das Einpassen wächst
systematisch um 10 px und verschiebt alle Crops" ist damit geprüft und widerlegt.

### 2.2 Bessere Takt-Messung — nicht nötig, die Messung ist längst genau genug

Geprüft wurden (`pitch_var.py`, auf 124 zwischengespeicherten Autokorrelations-Matrizen, damit Varianten
ohne erneutes Bildladen vergleichbar sind):

* alle Streifenlagen zu **einer** Autokorrelation aufsummieren statt je Lage einen Peak zu wählen,
* **Obertonkamm**: Bewertung eines Takt-Kandidaten über p, 2p, 3p, 4p gemeinsam,
* **Nachschärfen am Oberton**: den Peak bei k·p suchen und durch k teilen (k-fache relative Auflösung),
* **Bündel-Stärke als Score**: Summe statt Maximum, mit abgesenkter Aufnahmeschwelle.

Keine Variante schlägt den heutigen Stand. Der mittlere Fehler gegen die Referenz-Crops bleibt bei etwa
1 %; die gepoolten Varianten messen zwar mehr Rollen, aber mit mehr groben Fehlern (bis 22 statt 7 Rollen
über 2 % Fehler).

Der Grund wurde dann direkt gemessen (`repeat.py`): teilt man die Bilder einer Rolle in zwei Hälften und
misst beide getrennt, weichen die Ergebnisse im Median um **0,13 %** voneinander ab. Der Fehler gegen die
Referenz-Crops beträgt aber **1,03 %**. Die Messung ist also rund achtmal genauer, als ihr Fehler
vermuten lässt. Auch die Stichprobengröße ist ohne Einfluss:

| Bilder je Rolle | gemessene Rollen | Fehler (Median) | < 1 % | < 2 % |
| --- | --- | --- | --- | --- |
| 6 | 56 | 1,08 % | 25 | 49 |
| 12 (heute) | 56 | 1,02 % | 27 | 49 |
| 24 | 56 | 1,04 % | 27 | 49 |
| alle | 56 | 1,03 % | 27 | 49 |

**Folgerung: an der Takt-Messung ist nichts mehr zu gewinnen.** Der Rest von 1 % ist nicht Messrauschen,
sondern etwas anderes — siehe nächster Abschnitt.

### 2.3 Der Rest von 1 % ist die Crop-Gewohnheit, nicht die Physik

Der verbleibende Fehler hängt mit 0,625 am gemessenen `pitch_frac` zusammen, also am Bildausschnitt des
Scanners. Die Rollen zerfallen in zwei Digitalisier-Sitzungen:

| Gruppe | Rollen | Median-Fehler | implizierte Crop-Breite |
| --- | --- | --- | --- |
| `pitch_frac` < 0,120 (die „Film N"-Scans) | 38 | −0,80 % | **36,49 mm** |
| `pitch_frac` ≥ 0,120 (die datierten Scans) | 17 | +1,06 % | **35,82 mm** |

Die Restschwankung innerhalb der Gruppen beträgt nur 0,51 % bzw. 0,27 %. Die Physik liefert den Rahmen
korrekt; wie eng du ihn schneidest, hast du in den beiden Sitzungen unterschiedlich gehandhabt.

Was wäre damit zu holen (`mm_sweep.py`, Simulation über die Skalierung der Physik-Größe)?

| Konvention | Treffer |
| --- | --- |
| 36,2 mm (heute) | 1454 (87,5 %) |
| bester Einzelwert 36,0–36,1 mm | 1459 (87,8 %) |
| zwei Gruppen, 36,1 / 35,4 mm | 1470 (88,4 %) |
| Obergrenze: je Rolle optimal | 1507 (90,7 %) |

Die Kurve ist um das Optimum herum flach — 36,0 bis 36,3 mm liegen alle zwischen 1454 und 1459 Treffern.
**Der heutige Wert 36,2 mm ist gut gewählt und sollte bleiben.** Eine Konvention je Gruppe brächte 16
Referenzen, wäre aber an genau diesen Referenzsatz angepasst und auf neuen Rollen nicht belegt.

Für die gelernte Konvention in der Web-UI (`companion/session.py::_learn_convention`) folgt daraus
trotzdem etwas Brauchbares: Sie mittelt heute über alles. Da die Gewohnheit je Scan-Sitzung verschieden
ist und die Sitzung am `pitch_frac` erkennbar ist, mittelt sie zwei Dinge zusammen, die nicht zusammen
gehören. Das ist ein sauberer nächster Schritt, aber ein eigener.

### 2.4 Die Rollen ohne Takt sind nicht zu retten

Sechs Rollen liefern gar keinen Takt: *Nachts Weihnachtsmarkt*, *Viktorianisches Wasser*, *Küste 1*,
*Küste 2*, *Freibad 3*, *Prag 3*. Ich habe die Takt-Landschaft je Streifenlage ausgewertet
(`diag_pitch.py`) und die Scans angesehen (`nopitch.jpg`).

Das Ergebnis ist eindeutig: **in diesen Scans ist die Perforation nicht mit drin.** Der Rahmen füllt die
Datei bis an den Rand, es gibt nur einen schmalen hellen oder schwarzen Saum. Bei *Nachts
Weihnachtsmarkt* ist der richtige Takt in der Autokorrelation zwar schwach zu ahnen (Peaks bei 0,103–0,106
der Bildbreite in etwa 13 Streifenlagen), aber mit Scores von 0,04 bis 0,074 — und ohne zweiten Oberton.
Das ist kein Signal, das man tragfähig machen kann, ohne sich Fremdmuster einzufangen.

Auch die Idee, solchen Rollen den `pitch_frac` einer anderen Rolle derselben Sitzung zu leihen, trägt
nicht: die wahren Werte dieser sechs Rollen streuen von 0,103 bis 0,126 und passen zu keiner Gruppe.
Küste 1/2 und Freibad 3 würde man treffen, Weihnachtsmarkt und Viktorianisches Wasser um 18–20 %
verfehlen — bei dann hoher Konfidenz. Das ist ein schlechter Tausch.

Nebenbefund zu diesen Rollen: die Roh-Erkennung liefert dort teils physikalisch unmögliche
Seitenverhältnisse (*Freibad 3* 3,76; *Weihnachtsmarkt* `film_aspect` 8,56). Bei *Küste 2* stimmt die
lange Kante auf 3 px genau, nur die kurze kippt auf 736 statt 1247 px. Eine Plausibilitätsschranke (ein
35-mm-Rahmen ist 3:2 oder quadratisch, sonst nichts) würde diese Rolle einfangen. Das sind 7 Referenzen,
und die Bilder haben heute schon Konfidenz 0,01 — der praktische Nutzen ist klein. Notiert für später.

### 2.5 Die Rolle soll ihre eigene Messung bestätigen — **das ist die Änderung, die wirkt**

Aus 2.2 folgt der eigentliche Gedanke. Die Peakhöhe (`score`) ist heute das Gütemaß für den Takt, aber
sie sagt wenig darüber aus, ob die Messung stimmt: *Film 8* hat Score 0,972 und 2,0 % Fehler, *Leipzig*
hat Score 0,223 und 1,3 % Fehler. Die **Wiederholbarkeit** dagegen trennt sauber. Teilt man die Bilder
einer Rolle abwechselnd in zwei Hälften und misst beide getrennt:

| | Median | 90 %-Quantil | Maximum |
| --- | --- | --- | --- |
| Rollen mit Fehler < 2 % | 0,0020 | 0,0051 | 0,0104 |
| die zwei groben Fehlmessungen | *Einzelphotos* 0,0326 · *Film 24* 0,0702 | | |

Die Trennung ist mühelos. Die Fälle mit 2–3 % Fehler (Prag 1, Freibad 2, Hopfgarten, Film 25) fängt sie
nicht — die sind ja auch keine Fehlmessungen, sondern Crop-Gewohnheit (2.3).

Daraus die Regel, die ich umgesetzt und gemessen habe:

1. `measure_roll_pitch` misst zusätzlich beide Hälften und meldet `agree`, ihren relativen Abstand.
2. Widersprechen sich die Hälften (`agree > 0,012`), gilt der Takt **nicht** — auch bei hohem Score.
3. Bestätigen sie sich sehr genau (`agree < 0,002`), gilt er **auch ohne** hohen Score, und die
   Rollen-Verlässlichkeit in der Konfidenz geht auf 1,0.
4. Rollen **ohne** bestätigten Maßstab bekommen den Konfidenz-Faktor 0,5, weil ihre Größe aus dem Pool
   der übrigen Rollen kommt und dort gleichmäßig danebenliegen kann, ohne dass Streuung oder
   Kantenschärfe es zeigen (1.2).

Punkt 4 trägt den Großteil der Wirkung, Punkt 2 fängt *Einzelphotos* (19 % Fehler, Median-Versatz
+308 px).

---

## 3. Ergebnis der empfohlenen Änderung

Voller Lauf über beide Referenzsätze:

| | Treffer | AUC | grün | Präzision | falsch-grün | Abdeckung |
| --- | --- | --- | --- | --- | --- | --- |
| darktable heute | 1454 (87,5 %) | 0,749 | 1221 | 93,4 % | 80 | 78,5 % |
| darktable **neu** | 1453 (87,4 %) | **0,835** | 1163 | **96,0 %** | **47** | 76,8 % |
| Handcrops heute | 197 (94,3 %) | 0,826 | 136 | 100 % | 0 | — |
| Handcrops **neu** | 197 (94,3 %) | 0,826 | 136 | 100 % | 0 | — |

Auf den Handcrops ist die Änderung Bit für Bit wirkungslos: alle neun Rollen bestätigen ihren Takt
ohnehin, es gibt dort nichts zu deckeln. Das ist der erwünschte Nachweis, dass nichts kaputtgeht — der
Gewinn liegt woanders.

Die Trefferquote bleibt gleich (−1 Bild). Die falschen Grünen
gehen um 41 % zurück, bei 1,7 Punkten weniger Abdeckung. Woher der Gewinn kommt:

| Rolle | falsch-grün heute | neu |
| --- | --- | --- |
| 07.07.2014 – Leipzig | 23 | **0** |
| 2014 – Viktorianisches Wasser | 5 | 0 |
| Altona Sept 89 | 3 | 0 |
| Film 2 | 3 | 0 |
| Film 95 | 1 | 0 |
| 17.06.2013 – Freibad 2 | 0 | 1 |
| Einzelphotos | 0 | 1 |

### Die Schwellen-Kalibrierung ist der eigentliche Gewinn

`calib.py`, Schwellen nie auf denselben Bildern gewählt wie gemessen:

| Ziel-Präzision | heute | neu |
| --- | --- | --- |
| 90 % | t = 0,13 · Abdeckung 98,3 % | t = 0,08 · Abdeckung 98,7 % |
| 95 % | t = 0,74 · Abdeckung **42,4 %** | t = 0,38 · Abdeckung **86,0 %** |
| 95 %, Leave-One-Roll-Out | 92,7 % erreicht, Abdeckung 42,5 % | **94,4 %** erreicht, Abdeckung **85,1 %** |
| 98 % | **bei keiner Schwelle erreichbar** | t = 0,76 · Abdeckung 48,8 % (2-fach out-of-sample 97,5 %) |

Bei 95 % Ziel-Präzision verdoppelt sich die Abdeckung. Und 98 % Präzision — im letzten Lauf noch als
„für beide Quellen unerreichbar" notiert — wird erreichbar, bei knapp der Hälfte der Treffer. Das ist
die Zahl, die für den Arbeitsablauf zählt: mit 98 % kann man grün tatsächlich automatisch anwenden.

### Aufwand

Ein zusätzlicher Akkumulator je Rolle, kein zusätzliches Bildladen. Die Laufzeit über alle 3666 Bilder
ist unverändert (~6 min).

### Stand der Umsetzung

**Übernommen.** Die Änderung steht im Repo (`film_scale.py`, `auto_crop_negative.py`, dazu
`companion/session.py`); der Patch neben diesem Bericht ist damit nur noch Beleg. 91 Tests grün (acht
neue für das Tor und die Übereinstimmung). Ein Bestätigungslauf über beide Referenzsätze mit dem
Repo-Stand reproduziert die Tabellen oben Ziffer für Ziffer.

Die beiden vorgeschlagenen Kleinigkeiten sind mit eingeflossen:

* `agree_max` steht auf **0,02** statt 0,012. Die größte Abweichung einer richtigen Rolle liegt bei 0,0104,
  die kleinste einer Fehlmessung bei 0,0326 — 0,02 liegt sauber dazwischen, 0,012 ist knapp. Bei den
  heute verwendeten 12 Bildern je Rolle liegt keine Rolle zwischen 0,012 und 0,02; der Bestätigungslauf
  ist entsprechend bitgleich mit dem Messlauf. Reine Sicherheitsmarge.
* Die Stichprobe bleibt bei 12 Bildern. Mit 24 bestätigen sich zwar mehr Rollen (34 statt 25 auf besser
  als 0,2 %), aber am Tor ändert das kaum etwas: *Altona Sept 89* und *Film 95* kämen hinzu, dafür fiele
  *Film 12* heraus (heute 49/49), weil ihr Score bei 24 Bildern auf 0,27 rutscht und die Hälften sich mit
  0,0024 knapp oberhalb der strengen Schwelle bestätigen. Netto eine Rolle mehr bei doppelter Ladezeit —
  kein guter Tausch.

---

## 4. Was ich nicht empfehle

| Idee | Ergebnis | Warum nicht |
| --- | --- | --- |
| Größen-Spielraum ändern (0/4/20 px) | 1452 / 1457 / 1446 gegen 1454 | im Rauschen; verschiebt nur Abdeckung gegen Präzision |
| `crop_mm` neu justieren | Optimum 36,0–36,1 → +5 Referenzen | Kurve flach, heutiger Wert gut; Gewinn ist Anpassung an den Referenzsatz |
| Zwei Crop-Konventionen je Scan-Gruppe | 1470 (88,4 %) | an diesen Referenzsatz angepasst, auf neuen Rollen unbelegt |
| Takt-Messung mit Obertonkamm / gepoolter AC | kein Gewinn, mehr grobe Fehler | Messung ist mit 0,13 % Wiederholbarkeit bereits ausgereizt |
| Mehr Bilder je Rolle messen | identische Fehler bei 6/12/24/allen | Takt addiert sich schon bei 6 Bildern kohärent |
| Takt einer Nachbarrolle leihen | würde 3 Rollen treffen, 2 um 18–20 % verfehlen | Fehlgriffe kämen mit hoher Konfidenz |

---

## 5. Offene Punkte

* **Gelernte Konvention je Scan-Sitzung** statt global (2.3). Physikalisch begründet, die Sitzung ist am
  `pitch_frac` erkennbar. Braucht aber eine Entscheidung, wie die UI das dem Nutzer erklärt.
* **Plausibilitätsschranke für das Rollen-Seitenverhältnis** (2.4, Ende). Klein, aber sauber.
* **Die Obergrenze von 93,4 %** (1.1) ist mit einer Größe je Rolle erreichbar. Die restlichen 6,6 % sind
  Bilder, deren Referenz-Crop von der eigenen Rolle abweicht — dort hilft nur die Korrektur von Hand,
  und dafür gibt es seit gestern den Knopf „Größe auf Rolle".
* **Die 172 Bilder ohne Maßstab** bleiben bei 41 % Treffern. Mit dem Deckel sind sie immerhin nicht mehr
  gefährlich, sondern gelb/rot — das ist ehrlicher, aber es ist keine Lösung. Wenn dir die Scans dieser
  sechs Rollen noch mit Rand vorliegen, wäre ein Neu-Scan der billigste Weg.
* **Film 2** (−2,9 % Takt-Fehler) und **Film 25** (−3,2 %) sind die zwei Rollen, deren Crop-Gewohnheit am
  weitesten vom Rest abweicht. Ob das wirklich Gewohnheit ist oder ein Scan mit anderem Maßstab, ließe
  sich nur an den Originalen klären.

---

## 6. Verwendete Skripte

Alle im Sitzungs-Scratchpad
(`/tmp/claude-1000/-home-arian-…/233bb7cb-…/scratchpad/`):

| Datei | Zweck |
| --- | --- |
| `ana.py` | Rohlauf gegen die darktable-Crops auswerten, Treffer/AUC/grün je Rolle |
| `bias.py` | Median-Versatz und Streuung je Rolle, Obergrenze „Versatz bekannt" |
| `pitch_truth.py` | wahren `pitch_frac` je Rolle aus den Referenz-Crops ableiten |
| `cache_acc.py` | Autokorrelationen je Rolle zwischenspeichern (Lags bis 4× für Obertöne) |
| `pitch_var.py` | Varianten der Takt-Messung auf dem Zwischenspeicher vergleichen |
| `repeat.py` | Wiederholbarkeit (Hälften) und Einfluss der Stichprobengröße |
| `diag_pitch.py` | Takt-Landschaft je Streifenlage einer Rolle |
| `mm_sweep.py` | Crop-Konvention in mm durchfahren, ein Wert / zwei Gruppen / Obergrenze |
| `nsample.py` | Bestätigung der Hälften über Stichprobengrößen 12/20/24/32 |
| `runvar.py`, `runhc.py` | eine Pipeline-Variante über die 62 Rollen bzw. die Handcrops fahren |
| `calib.py` | Schwellen-Kalibrierung (2-fach über Rollen, Leave-One-Roll-Out) |
| `agree/` | die empfohlene Änderung (der Diff liegt als `bericht-erkennung-2026-09-22.patch` im Projektordner) |

Das Scratchpad ist an die Sitzung gebunden und verschwindet; die Rohläufe (`cons_edited_dt.json`,
`agree_dt.json`, `sl0/sl4/sl20_dt.json`, `base_hc.json`, `agree_hc.json`) liegen dort. Wenn du eine der
Messungen später nachvollziehen willst, sag Bescheid — die Läufe dauern je rund 6 Minuten.
