"""Tests des Perforations-Massstabs (film_scale.py), der gelernten Crop-Konvention und der Rollengroesse in der Web-UI.

Ausfuehren:  .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import film_scale as fs                         # noqa: E402
from companion import session as sess           # noqa: E402
from test_companion import make_session         # noqa: E402


def synthetic_strip(pitch=120.0, w=1600, h=1067, holes=True, seed=0, phase=0.0):
    """Graubild: helles Negativ, Loecher (dunkle Rechtecke) in einem schmalen Streifen am oberen und unteren Rand."""
    rng = np.random.default_rng(seed)
    g = np.full((h, w), 90.0) + rng.normal(0, 6, (h, w))
    g[int(h * 0.1):int(h * 0.9), int(w * 0.08):int(w * 0.92)] += rng.normal(0, 30, (1, 1))   # "Bildinhalt"
    g[int(h * 0.2):int(h * 0.8), int(w * 0.2):int(w * 0.6)] += 40
    if holes:
        x = phase
        while x < w:
            x0 = int(x)
            for y0 in (int(h * 0.02), int(h * 0.94)):
                g[y0:y0 + 18, x0:x0 + 40] = 235.0
            x += pitch
    return np.clip(g, 0, 255).astype(np.uint8)


def with_fake_edge_pattern(g, pitch, phase=0.0):
    """Zusaetzlich ein staerkeres, periodisches Muster mit anderem Takt in einem schmalen Streifen nahe dem unteren Rand
    (in den Scans etwa ein Halterungs- oder Randartefakt, nur in der aeussersten Streifenlage)."""
    h, w = g.shape
    g = g.copy()
    x = phase
    while x < w:
        x0 = int(x)
        g[int(h * 0.972):int(h * 0.984), x0:x0 + 60] = 0
        x += pitch
    return g


class PitchTest(unittest.TestCase):
    def test_single_strong_strip_does_not_override_consensus(self):
        # Altona Sept 89 / Film 1 (unentwickelt): ein Peak in einer Streifenlage (0,129 statt 0,118 der Bildbreite)
        # hatte den hoeheren Score, der echte Takt steht aber in mehreren Streifenlagen oben und unten
        grays = [with_fake_edge_pattern(synthetic_strip(pitch=118.4, phase=p, seed=i), 129.5, phase=p)
                 for i, p in enumerate((0, 31, 77, 5, 60, 90))]
        r = fs.measure_roll_pitch(grays)
        self.assertAlmostEqual(r["pitch"], 118.4, delta=118.4 * 0.01)

    def test_measures_pitch_of_synthetic_perforation(self):
        # jedes Bild hat eine andere Phase (Filmvorschub), der Takt bleibt
        grays = [synthetic_strip(pitch=118.4, phase=p, seed=i) for i, p in enumerate((0, 31, 77, 5, 60, 90))]
        r = fs.measure_roll_pitch(grays)
        self.assertIsNotNone(r["pitch"])
        self.assertAlmostEqual(r["pitch"], 118.4, delta=118.4 * 0.01)
        self.assertGreaterEqual(r["score"], 0.5)
        self.assertAlmostEqual(r["pitch_frac"], 118.4 / 1600, delta=0.001)

    def test_portrait_input_is_transposed(self):
        grays = [np.ascontiguousarray(synthetic_strip(pitch=125.0, phase=p, seed=p).T) for p in (0, 40, 80)]
        r = fs.measure_roll_pitch(grays)
        self.assertAlmostEqual(r["pitch"], 125.0, delta=1.5)

    def test_no_perforation_gives_low_score(self):
        r = fs.measure_roll_pitch([synthetic_strip(holes=False, seed=s) for s in range(6)])
        self.assertLess(r["score"], fs.MIN_SCORE)

    def test_empty_input(self):
        self.assertIsNone(fs.measure_roll_pitch([])["pitch"])
        self.assertIsNone(fs.measure_roll_pitch([None, np.zeros((50, 50), np.uint8)])["pitch"])


class ConventionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "convention.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_and_invalid(self):
        self.assertIsNone(fs.load_convention(self.path))
        os.makedirs(os.path.dirname(self.path))
        for bad in ('{"long_mm": 12, "short_mm": 24}', "kaputt", '{"long_mm": 36}'):
            with open(self.path, "w") as f:
                f.write(bad)
            self.assertIsNone(fs.load_convention(self.path), bad)

    def test_update_weights_by_count(self):
        first = fs.update_convention({"long_mm": 36.0, "short_mm": 24.0, "n": 30}, self.path)
        self.assertEqual(first["n"], 30)
        merged = fs.update_convention({"long_mm": 37.0, "short_mm": 25.0, "n": 10}, self.path)
        self.assertAlmostEqual(merged["long_mm"], 36.25, places=2)
        self.assertEqual(merged["n"], 40)
        self.assertEqual(fs.load_convention(self.path)["n"], 40)

    def test_env_can_disable(self):
        old = os.environ.get("AUTOCROP_CONVENTION")
        os.environ["AUTOCROP_CONVENTION"] = ""
        try:
            self.assertIsNone(fs.convention_path())
            self.assertIsNone(fs.load_convention())
            self.assertIsNone(fs.update_convention({"long_mm": 36.0, "short_mm": 24.0, "n": 5}))
        finally:
            if old is None:
                del os.environ["AUTOCROP_CONVENTION"]
            else:
                os.environ["AUTOCROP_CONVENTION"] = old


class RollSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = make_session(self.tmp.name, confs=(0.9, 0.4, 0.1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_transfers_size_keeps_center_and_undoes(self):
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.7, 0.8]})       # 1500 x 1200 px
        n = self.s.apply_roll_size(101)
        self.assertEqual(n, 2)
        for iid in (102, 103):
            self.assertEqual(self.s.effective_crop(self.s.image(iid)), [0.25, 0.2, 0.75, 0.8])
            self.assertEqual(self.s.image(iid)["manual"]["derived"], "roll")
        self.assertEqual(self.s.effective_crop(self.s.image(101)), [0.2, 0.2, 0.7, 0.8])
        self.assertTrue(self.s.undo())
        self.assertIsNone(self.s.image(102).get("manual"))
        self.assertEqual(self.s.effective_crop(self.s.image(102)), [0.1, 0.1, 0.9, 0.9])

    def test_skips_hand_corrected_images(self):
        self.s.patch_images([102], {"crop": [0.3, 0.3, 0.6, 0.6]})
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.7, 0.8]})
        self.assertEqual(self.s.apply_roll_size(101), 1)
        self.assertEqual(self.s.effective_crop(self.s.image(102)), [0.3, 0.3, 0.6, 0.6])

    def test_derived_crops_are_no_feedback(self):
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.7, 0.8]})
        path = self.s.path("feedback.jsonl")
        before = open(path).read().count("\n") if os.path.exists(path) else 0
        self.s.apply_roll_size(101)
        after = open(path).read().count("\n") if os.path.exists(path) else 0
        self.assertEqual(before, after)

    def test_orientation_is_kept_per_image(self):
        self.s.image(103)["detected"]["crop"] = [0.4, 0.1, 0.6, 0.9]     # Hochformat: 600 x 1600 px
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.7, 0.8]})       # Referenz 1500 x 1200
        self.s.apply_roll_size(101)
        c = self.s.effective_crop(self.s.image(103))
        w, h = (c[2] - c[0]) * 3000, (c[3] - c[1]) * 2000
        self.assertAlmostEqual(w, 1200, delta=2)
        self.assertAlmostEqual(h, 1500, delta=2)

    def test_requires_reference_crop(self):
        self.s.image(101)["detected"] = None
        with self.assertRaises(sess.SessionError):
            self.s.apply_roll_size(101)


class LearnedConventionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = make_session(self.tmp.name, confs=(0.9, 0.9, 0.9, 0.9))

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_scale_and_three_hand_crops(self):
        self.assertIsNone(self.s.learned_convention())
        # 3000 px lange Kante, Takt 3000*0.0784 = 235.2 px -> 49.39 px/mm; 36.4 mm = 0.5993 der Breite, 24.2 mm = 0.5976 der Hoehe
        self.s.state["film_scales"] = {"Film A": {"pitch_frac": 0.0784, "score": 0.8}}
        for iid in (101, 102):
            self.s.patch_images([iid], {"crop": [0.2, 0.2, 0.2 + 0.5993, 0.2 + 0.5976]})
        self.assertIsNone(self.s.learned_convention())                  # erst ab 3 Handcrops
        self.s.patch_images([103], {"crop": [0.2, 0.2, 0.2 + 0.5993, 0.2 + 0.5976]})
        c = self.s.learned_convention()
        self.assertEqual(c["n"], 3)
        self.assertAlmostEqual(c["long_mm"], 36.4, delta=0.2)

    def test_ignores_low_scores_and_derived(self):
        self.s.state["film_scales"] = {"Film A": {"pitch_frac": 0.0784, "score": 0.2}}
        for iid in (101, 102, 103):
            self.s.patch_images([iid], {"crop": [0.2, 0.2, 0.2 + 0.5993, 0.2 + 0.5976]})
        self.assertIsNone(self.s.learned_convention())          # Takt-Score 0.2 ist zu schwach
        self.s.state["film_scales"]["Film A"]["score"] = 0.8
        self.s.image(102)["manual"]["derived"] = "roll"
        self.s.image(103)["manual"]["derived"] = "roll"
        self.assertIsNone(self.s.learned_convention())          # nur noch eine echte Handarbeit


if __name__ == "__main__":
    unittest.main()
