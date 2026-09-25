# How-to: scanning negatives for the automatic detection

This guide summarizes what makes the algorithm (`kader.py`,
`film_scale.py`) reliable — and what slows it down. It is not about what looks
"nicest" photographically, but about what the detection can actually measure.
Background and measurements: `bericht-erkennung-2026-09-22.md` (German),
`roadmap.md` (German).

## The one rule that matters most: scan the perforation too

The algorithm's most important trick is that the hole spacing of 35 mm film
(4.7625 mm) is a physical constant. `film_scale.py` uses it to compute the
scale of the roll (px/mm) — regardless of how different the actual motifs
look. But this only works **if the perforation is in the scan at all.**

- Leave a visible margin with the holes on at least one side (top or bottom,
  in the film's transport direction) when scanning. One or two millimeters of
  air is enough; more doesn't hurt.
- **Don't** scan tight to the image content, even though it looks cleaner. Six
  rolls in the test data so far had no perforation in the scan at all — for
  those, the algorithm can only estimate the roll size by comparison with
  other rolls, with a clearly worse hit rate (around 41% instead of around
  93%, see the report).
- The algorithm looks for the perforation in the outer 24% of the short image
  edge (`EDGE_FRAC` in `film_scale.py`). It doesn't have to be centered in the
  frame — but it must be findable in that margin strip, not cropped away
  entirely.

## Frame the negative neither too tight nor too loose

The algorithm expects the negative frame to fill roughly 80–95% of the scan
file (the pitch then falls between 7.0% and 13.5% of the long image edge,
`PITCH_REL` in `film_scale.py`). Two failure modes in opposite directions:

- **Too tight** (only the image itself, no frame and no perforation): the
  perforation is missing entirely, see above.
- **Too loose** (lots of black margin, film holder, neighboring frames in the
  scan): the pitch can still be measured, but the relationship between pixel
  size and roll size becomes less stable, and the algorithm has to guess more
  about where the actual image content starts.

A good rule of thumb: the frame plus a narrow black margin with the
perforation should fill the file, without a lot of empty space around it.

## Keep the film holder clean

A film holder with its own periodic pattern at the edge (ribbing, sprocket
teeth, markings) can be mistaken by the algorithm for the perforation if that
pattern stands out more strongly than the actual holes. In testing, a foreign
pattern like this at the very edge of the frame led to a wrong, too-large
scale for the whole roll. The algorithm now bundles hits across several strip
positions to soften individual outliers — even so:

- Keep the film holder and the surface it rests on free of dust, scratches
  and foreign patterns before scanning.
- If the holder itself has a regular pattern (e.g. sprocket teeth for
  locking it in place), make sure it doesn't extend into the scanned area.

## Scan one roll consistently

The algorithm builds its measurement of the perforation pitch from **all the
images of a roll together** (the autocorrelation is summed across the whole
roll, because the film sits in the same place in every scan and the pitch
adds up coherently while the image content doesn't). That creates two
practical requirements:

- **Same resolution/scanner setting for the whole roll.** Images with a
  different pixel size are internally discarded by the algorithm (they don't
  fit the shared grid with the rest). Simplest fix: don't change the scanner
  preset partway through a roll.
- **Same framing/crop for the whole roll.** The more evenly the film sits in
  the scanner, the more cleanly the perforation hits bundle up across the
  roll's images. Switching mounts or strongly varying alignment from image to
  image weakens the signal.
- **Scan more than a couple of images per roll**, not just single frames.
  From three images on, the algorithm already accepts a lower score for the
  pitch; from about twelve images on, it can additionally check whether two
  random halves of the roll confirm each other (`agree`) — this has become
  the most important quality measure, and it needs several images to work at
  all. With single scans (just one frame digitized in isolation) this
  confirmation is missing, and the detection is correspondingly more
  cautious (see below).

## What applies to standalone negatives without a roll

Not every image comes from a complete roll with several exposures —
sometimes there's just a single negative. In that case the algorithm can't
run the halves-confirmation and relies more heavily on a high
autocorrelation score in that one image. For this case, in particular:

- Scan the perforation on **both** edges (top and bottom) if possible — more
  strip positions with a visible pitch increase the chance of a clean hit
  even in a single image.
- Scan sharp and with enough contrast that the holes appear as clear
  light/dark edges, not blurred.

## Exposure/development: helps, but isn't decisive

Whether the scan is already developed/inverted or a raw, non-inverted image
(only white-balanced) — the perforation pitch can be measured either way,
because it's independent of the image content. Developed images give a
slightly more reliable confidence estimate on average; raw, undeveloped
images, though, rescue exactly the rolls where the developed version fails
for other reasons. For the pitch measurement itself, exposure is therefore
secondary — what matters more is that the holes are in the frame at all and
sharp enough.

## In short

1. Scan the perforation — on at least one edge, preferably both.
2. Frame neither edge-to-edge tight nor surrounded by a lot of empty space.
3. Keep the film holder clean and free of its own periodic pattern in the
   scanned area.
4. Scan a whole roll with the same scanner setting and similar framing,
   without switching partway through.
5. Scan several images per roll rather than isolated single frames — from
   about twelve images on, the algorithm can cross-check its own
   measurement.
6. For standalone scans without a roll: capture both edges, scan sharp and
   with good contrast.

Following these points puts a roll in practice into the group whose pitch
measurement confirms itself reliably — there, the current hit rate is around
93% and the precision of the automatically applied ("green") results is
96%, compared to clearly weaker numbers for rolls without a usable
perforation.
