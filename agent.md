- When writing something intended for human consumption, (comment, commit message, reply to prompt) use as few words as possible. Pick every word meticulously to reduce the volume to a strict minimum. Be down to the point. Less is more.

- Avoid superlatives and praise. Stop telling me I am absolutely right. Give me the cold hard truth.

- Avoid magic numbers and strings by extracting recurring or meaningful values into descriptive constants (const) or enums. Keep self-explanatory, one-off values inline to avoid clutter. If a value comes from a spec (e.g. HTTP 200 OK), use a constant regardless.

- Reduce code indentation. Avoid Arrow Anti-Pattern. Leverage early return and continue.

- Keep function names short. Less than 30 characters.

- Use enums instead of booleans for function parameters.

- Let the reader of the code breathe. Add empty lines between logical blocks of code.

- Add a small, to the point, comment to explain *what* the block does and *why*. Use examples when possible. Propose ASCII drawings to explain complete systems.

- Treat member visibility changes as a breaking design shift. Keep all fields and functions private unless external access is strictly required by the design. Prompt the user for explicit approval before changing any access modifier from private to internal or public.

- Program to levels of abstraction. Lower-level mechanics (e.g., raw hardware I/O, sector parsing, direct socket streams) must be encapsulated in a dedicated driver/abstraction layer. Expose clean, high-level APIs to the rest of the application so calling code works with domain concepts, not raw implementation details.

- Don't touch blocks of code unrelated to the feature you implement. e.g. Don't add comments to a block of code if you did not create it or modify it. As much as possible try to minimize the number of changed lines when implementing a feature.

- Strictly adhere to the layered boundary hierarchy: each layer may only communicate with its immediate neighbor directly below it. Never "punch holes" through layers (e.g., controllers or UI components must never directly call database queries, raw hardware drivers, or low-level network clients; always route through the intermediate service/abstraction layer).

- Always use {}, even on a one-line "if" statement.

When you write a commit message, follow these 7 rules:
Rule 1: Separate the subject line from the body with a single blank line.
Rule 2: Limit the subject line to 50 characters (72 is the absolute hard limit).
Rule 3: Capitalize the first letter of the subject line.
Rule 4: Do not end the subject line with a period.
Rule 5: Use the imperative mood in the subject line (e.g., "Fix bug," "Add feature," 
        not "Fixed" or "Adds"). Test formula: It must complete the sentence: "If applied,
        this commit will [your subject line here]".
Rule 6: Wrap the body text manually at 72 characters to prevent Git formatting issues.
Rule 7: Use the body to explain what and why vs. how. Assume the code explains the how;
        the message must explain the context and reasoning. 

- If the prompt indicates that a bug is being fixed, don't write the fix right away. First write the test. Observe it failing. Then write the fix. And observe the test passing.

## Auto-Crop-Negative – Projekt-Wissen

## Zweck
Automatisches Zuschneiden von fotoabfotografierten Filmnegativen. Das Skript erkennt den
Bildbereich innerhalb des Filmstreifens (ohne Sprossenlöcher, Filmrand, Halter) und liefert
die Crop-Koordinaten als JSON.

## Workflow
1. **Vollautomatisch** – das finale Skript läuft ohne menschliches Eingreifen.
2. Unsichere Kandidaten werden automatisch als `needs_review` markiert.
3. Manuelle Korrekturen im Review-GUI dienen **nur zum Trainieren/Validieren** des Algorithmus.
   Sie sind **nicht pixelgenau**, sondern geben Anhaltspunkte für die Richtung der Korrektur.
   Im Zweifel: groben Bias korrigieren, nicht einzelne Bilder optimieren.
4. Validierung per Delta-Kriterien: ΔHeight <100px, ΔWidth <50px = OK

## Pipeline
- `auto_crop_negative.py` – Stage 1 (Filmstreifen-Erkennung) + Stage 2 (Bildinhalt)
- `film_analysis.py` – Filmweites Seitenverhältnis + Bildgröße aus Pass-1-Daten
- `batch_test.py` – `--two-pass`: Pass 1 → Film-Analyse → Pass 2 (mit Film-Metadaten)
- `start_review_gui.sh` – interaktive Analyseauswahl + GUI-Start
- `review_gui.py` – Bild-Review, Crop-Editor, Export

## Bekannte Fehlerklassen (Stand: 98 manuelle Korrekturen, 92/98 OK = 94%)
- **Geloest: Film-Aspect-Erkennung** via Pass A (measure_image_aspect):
  Polaritaets-adaptiv (Bild kann heller ODER dunkler als Filmrand sein –
  Negativ-Polaritaet!), Dark-Span (Halter <100) + Span mit Threshold bei
  Rand + 25% des Kontrasts.
- **Geloest: Kontrast-Konsens statt Median:** Die bestkontrasteste Messung
  (klarste Bildgrenzen) fuehrt; alle Messungen innerhalb +/-10% davon werden
  gemittelt. Grund: schwaechere Messungen erwischt oft den Filmstreifen
  statt das Bild (Film 31: Kontrast 67 -> 1.03 korrekt vs. Kontrast 13-28
  -> 1.1-1.6 gestreut).
- **Geloest: 1:1-Filme (Film 29: 11/13, Film 31: 11/12).**
- **Rest-MISS (6):** Alles Grenzfaelle (Delta 57-85px, Schwelle 50/100).
  Kein systematischer Fehler mehr.

## Architektur: Drei-Phasen-Pipeline
1. **Pass A** (`--measure-aspect`): Misst Bild-Aspect pro Bild OHNE
   Ziel-Vorurteil. Polaritaets-adaptive Span-Suche, ~1s/Bild.
2. **Film-Aspect-Bestimmung** (`determine_film_aspects`): Portrait-
   Normalisierung, bestkontrasteste Messung + Konsens (+/-10%),
   Fallback 1.5 bei <3 validen.
3. **Pass B** (volle Erkennung): Stage 1 + Stage 2 mit `--aspect-ratio`
   aus Schritt 2. Geometry-Fallback + Expand-Height nutzen das echte Format.

## Wichtige Erkenntnisse
- Aspect-Strafe in Stage 1 wuerde das Format-Erkennen verhindern
  (Chicken-and-Egg). Daher: Pass A ohne jeden Aspect-Bias.
- Negative haben BEIDE Polaritaeten: Bild heller ODER dunkler als Rand
  (je nach Belichtung). Immer beide pruefen.
- Bright-Span-Threshold bei (border+content)/2 zerbricht bei dunkleren
  Bildmitteilen; 25%-Regel behebt das.
- Full-Image-Dark-Span in Stage 2 war ein Fehlschlag (Out-of-Bounds-Crops
  bei Portrait-Bildern) – entfernt.
- Nutzer-Definition "sauberer Crop": moeglichst wenig Filmrand, Aspect
  ist Richtwert kein Dogma. Geometry-Fallback nur bei starker Abweichung
  (>15%) und nur wenn er den Crop tatsaechlich verbessert.

## Architektur: Drei-Phasen-Pipeline
1. **Pass A** (`--measure-aspect`): Misst Bild-Aspect pro Bild OHNE
   Ziel-Vorurteil. Nur Dark-Span + Bright-Span, kein Scoring/Merge. ~1s/Bild.
2. **Film-Aspect-Bestimmung** (`determine_film_aspects`): Portrait-
   Normalisierung (a<1 -> 1/a), Median pro Film, Fallback 1.5 bei <3 validen.
3. **Pass B** (volle Erkennung): Stage 1 + Stage 2 mit `--aspect-ratio`
   aus Schritt 2. Geometry-Fallback + Expand-Height nutzen das echte Format.

## Wichtige Erkenntnisse
- Aspect-Strafe in Stage 1 wuerde das Format-Erkennen verhindern
  (Chicken-and-Egg). Daher: Pass A ohne jeden Aspect-Bias.
- Bright-Span-Threshold bei (border+content)/2 zerbricht bei dunkleren
  Bildmitteilen; 25%-Regel behebt das.
- Full-Image-Dark-Span in Stage 2 war ein Fehlschlag (produzierte
  Out-of-Bounds-Crops bei Portrait-Bildern) – entfernt.
- Konsistenz-Korrektur orientation-blind: funktioniert, weil Pass B mit
  korrektem Film-Aspect laeuft und die Medians dann stimmen.

## Gelernte Bugs
- Konsistenz-Call stand in main() statt _run_two_pass (Muster
  "elapsed + results.sort" existiert doppelt – Edit traf falsches Vorkommen).
  Bei doppelten Code-Mustern immer eindeutigen Kontext verwenden!
- Film-Konsistenz nur anwenden wenn Film-Aspect verlässlich
  (matching >= 3 Bilder nahe am Film-Aspect).
