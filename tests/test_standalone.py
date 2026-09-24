"""Tests der eigenstaendigen Nutzung: Ausgabe-Adapter, Konverter-Wahl, Bildsuche, CLI.

Ausfuehren:  .venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from companion import converters, pp3, targets          # noqa: E402
from companion import session as sess                   # noqa: E402
from companion.__main__ import main, _resume_standalone  # noqa: E402
from companion.orientation import crop_to_stored, raw_orientation   # noqa: E402
from companion.sources import standalone_job            # noqa: E402
from companion.targets.xmp import update_xmp            # noqa: E402

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError:          # pragma: no cover
    cv2 = None

TEST_JPG = os.path.join(ROOT, "test.jpg")
needs_cv2 = unittest.skipUnless(cv2, "cv2/numpy/Pillow fehlen")


def detected(conf, crop=(0.1, 0.1, 0.9, 0.9)):
    return {"crop": list(crop), "confidence": conf, "method": "test", "reasons": []}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def file(self, rel, data=b"x"):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def session(self, target, items, out=None, converter=None):
        """items: [(pfad, konfidenz, export_size)]"""
        imgs = [{"id": i, "path": p, "export": p, "export_size": size, "detected": detected(conf)}
                for i, (p, conf, size) in enumerate(items, start=1)]
        job = {"target": target, "images": imgs, "folder": self.tmp, "converter": converter,
               "out": out or os.path.join(self.tmp, "autocrop")}
        s = sess.Session.create(job, os.path.join(self.tmp, "root"))
        s.mark_analysis_done()
        return s


# ── Hilfsmodule ──────────────────────────────────────────────────────────────

class Pp3Test(unittest.TestCase):
    def test_updates_keys_and_keeps_the_rest(self):
        text = "[Version]\nAppVersion=5.9\n\n[Crop]\nEnabled=false\nX=0\nGuide=Frame\n\n[Exposure]\nCompensation=0.5\n"
        new = pp3.set_values(text, "Crop", {"Enabled": True, "X": 12, "W": 300})
        self.assertIn("Enabled=true\nX=12\nGuide=Frame\nW=300\n\n[Exposure]", new)
        self.assertIn("AppVersion=5.9", new)
        self.assertEqual(pp3.get_value(new, "Exposure", "Compensation"), "0.5")

    def test_adds_missing_section(self):
        new = pp3.set_values("", "General", {"ColorLabel": 1})
        self.assertEqual(new, "[General]\nColorLabel=1\n")
        new = pp3.set_values(new, "Crop", {"Enabled": False})
        self.assertEqual(pp3.get_value(new, "Crop", "Enabled"), "false")
        self.assertEqual(pp3.get_value(new, "General", "ColorLabel"), "1")


class OrientationTest(unittest.TestCase):
    def test_rotated_orientations_map_to_sensor_frame(self):
        crop = [0.1, 0.2, 0.5, 0.9]            # Anzeige
        self.assertEqual(crop_to_stored(crop, 1), crop)
        self.assertEqual(crop_to_stored(crop, None), crop)
        # 6: Anzeige = Sensor 90 Grad im Uhrzeigersinn gedreht -> x_s = y, y_s = 1 - x
        self.assertEqual(crop_to_stored(crop, 6), [0.2, 0.5, 0.9, 0.9])
        # 8: x_s = 1 - y, y_s = x
        self.assertEqual(crop_to_stored(crop, 8), [0.1, 0.1, 0.8, 0.5])
        self.assertEqual(crop_to_stored(crop, 3), [0.5, 0.1, 0.9, 0.8])

    @needs_cv2
    def test_reads_orientation_from_tiff_header(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "x.nef")               # NEF ist TIFF-basiert
        Image.new("RGB", (8, 4)).save(p, "TIFF", tiffinfo={0x0112: 6})
        self.assertEqual(raw_orientation(p), 6)
        q = os.path.join(d, "y.cr3")
        with open(q, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypcrx ")
        with unittest.mock.patch("companion.orientation._rawpy_orientation", return_value=None):
            self.assertIsNone(raw_orientation(q))


# ── Bildsuche, Zielvorschlag, Konverter ──────────────────────────────────────

class DiscoveryTest(Tmp):
    def test_standalone_job_finds_raws_drops_twins_and_skips_output(self):
        self.file("Film 1/a.NEF")
        self.file("Film 1/a.JPG")                  # RAW+JPEG der Kamera
        self.file("Film 1/b.tif")
        self.file("Film 2/c.arw")
        self.file("Film 2/.hidden.jpg")
        self.file("autocrop/Film 1/b.tif")
        self.file(f"autocrop/{targets.OUTPUT_MARKER}")
        job = standalone_job(self.tmp)
        names = sorted(os.path.relpath(i["path"], self.tmp) for i in job["images"])
        self.assertEqual(names, [os.path.join("Film 1", "a.NEF"), os.path.join("Film 1", "b.tif"),
                                 os.path.join("Film 2", "c.arw")])
        self.assertEqual({i["film"] for i in job["images"]}, {"Film 1", "Film 2"})
        with self.assertRaises(sess.SessionError):
            standalone_job(os.path.join(self.tmp, "nope"))

    def test_suggest_target_from_sidecars(self):
        jpg = self.file("s/a.jpg")
        raw = self.file("r/a.nef")
        self.assertEqual(targets.suggest([jpg]), "copies")
        self.assertEqual(targets.suggest([jpg, raw]), "json")
        self.file("r/a.xmp", b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                             b'<rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:Exposure2012="0"/>'
                             b'</rdf:RDF></x:xmpmeta>')
        self.assertEqual(targets.suggest([raw]), "xmp")
        self.file("r/a.nef.pp3", b"[Crop]\nEnabled=false\n")
        self.assertEqual(targets.suggest([raw]), "rawtherapee")

    def test_resolve_converter(self):
        raw = self.file("r/a.nef")
        all_on = {converters.DARKTABLE: True, converters.RAWTHERAPEE: True, converters.RAWPY: True}
        with unittest.mock.patch("companion.converters.available", return_value=all_on):
            self.assertIsNone(converters.resolve("auto", []))
            self.assertEqual(converters.resolve("rawpy", [raw]), "rawpy")
            self.assertEqual(converters.resolve("auto", [raw]), "darktable")       # Reihenfolge
            self.file("r/a.nef.pp3", b"[Crop]\n")
            self.assertEqual(converters.resolve("auto", [raw]), "rawtherapee")     # Sidecar gewinnt
        only_rawpy = dict.fromkeys(all_on, False)
        only_rawpy[converters.RAWPY] = True
        with unittest.mock.patch("companion.converters.available", return_value=only_rawpy):
            self.assertEqual(converters.resolve("auto", [raw]), "rawpy")          # pp3 da, RT fehlt

    def test_export_many_reports_missing_converter(self):
        got = []
        with unittest.mock.patch("companion.converters.find_rawtherapee_cli", return_value=None):
            converters.export_many([(1, "a.nef", "o.jpg")], lambda *a: got.append(a), "rawtherapee")
        self.assertEqual(got, [(1, None, "rawtherapee-cli nicht gefunden")])


def write_dng(path, gray, orientation=1):
    """Minimale CFA-DNG, die LibRaw liest (Graubild als Bayer-Mosaik)."""
    import tifffile
    h, w = gray.shape
    cfa = gray[:h - h % 2, :w - w % 2].astype("uint16") * 64
    tags = [(33421, "H", 2, (2, 2)), (33422, "B", 4, (0, 1, 1, 2)), (50706, "B", 4, (1, 4, 0, 0)),
            (50708, "s", 0, "Test Cam"), (50721, "2i", 9, (1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1)),
            (50728, "2I", 3, (1, 1, 1, 1, 1, 1)), (274, "H", 1, orientation)]
    tifffile.imwrite(path, cfa, photometric=32803, extratags=tags, subfiletype=0)


@needs_cv2
@unittest.skipUnless(converters.has_rawpy(), "rawpy fehlt")
class RawpyConverterTest(Tmp):
    def test_exports_dng_with_orientation(self):
        try:
            import tifffile  # noqa: F401
        except ImportError:
            self.skipTest("tifffile fehlt")
        gray = cv2.rotate(cv2.imread(TEST_JPG, cv2.IMREAD_GRAYSCALE), cv2.ROTATE_90_COUNTERCLOCKWISE)
        raw = os.path.join(self.tmp, "neg.dng")
        write_dng(raw, gray, orientation=6)                           # Sensor quer, angezeigt hoch
        self.assertEqual(raw_orientation(raw), 6)
        got = []
        out = os.path.join(self.tmp, "exports", "neg.jpg")
        converters.export_many([(1, raw, out)], lambda *a: got.append(a), converters.RAWPY)
        self.assertEqual(got, [(1, out, None)])
        self.assertEqual(Image.open(out).size, (1332, 2000))


# ── Ausgabe-Adapter ──────────────────────────────────────────────────────────

class SessionTargetTest(Tmp):
    def test_old_sessions_and_modes_map_to_targets(self):
        p = self.file("a.jpg")
        for mode, name in (("darktable", "darktable"), ("folder", "reviews")):
            s = sess.Session.create({"mode": mode, "images": [{"id": 1, "path": p}]},
                                    os.path.join(self.tmp, "root"))
            self.assertEqual(s.target_name, name)
            self.assertEqual(s.mode, mode)
        s = self.session("copies", [(p, 0.9, [100, 100])])
        self.assertEqual(s.mode, "standalone")
        st = s.public_state()["target"]
        self.assertEqual((st["name"], st["external"], st["out"]), ("copies", False, s.out_dir))
        with self.assertRaises(sess.SessionError):
            targets.get("nope")

    def test_set_target_only_while_editable_and_only_standalone(self):
        p = self.file("a.jpg")
        s = self.session("copies", [(p, 0.9, [100, 100])])
        s.set_target("xmp")
        self.assertEqual(s.target_name, "xmp")
        with self.assertRaises(sess.SessionError):
            s.set_target("nope")
        s.finish()
        with self.assertRaises(sess.SessionError):
            s.set_target("json")                                   # gesperrt
        d = sess.Session.create({"mode": "darktable", "images": [{"id": 1, "path": p}]},
                                os.path.join(self.tmp, "root2"))
        with self.assertRaises(sess.SessionError):
            d.set_target("json")                                   # nicht eigenstaendig

    def test_xmp_target_is_incompatible_with_non_raw_images(self):
        raw, jpg = self.file("F/a.nef"), self.file("F/b.jpg")
        s = self.session("xmp", [(raw, 0.9, [100, 100]), (jpg, 0.9, [100, 100])])
        pub = {i["id"]: i for i in s.public_state()["images"]}
        self.assertEqual(pub[1]["target_ok"], True)
        self.assertEqual((pub[2]["target_ok"], pub[2]["target_reason"]), (False, "not_raw"))
        self.assertFalse(pub[2]["apply"])                            # zaehlt nicht mehr zu "Anwenden"
        opts = {o["name"]: o for o in s.public_state()["target_options"]}
        self.assertEqual((opts["xmp"]["total"], opts["xmp"]["incompatible"]), (2, 1))
        self.assertEqual(opts["copies"]["incompatible"], 0)          # copies nimmt jedes Bild

    def test_add_images_keeps_decisions_and_reopens_locked_session(self):
        a = self.file("F/a.jpg")
        s = self.session("copies", [(a, 0.9, [100, 100])])
        s.patch_images([1], {"decision": "accept"})
        s.finish()
        self.assertEqual(s.phase, "applied")
        b = self.file("F/b.jpg")
        added = s.add_images([{"id": 1, "path": a, "film": "F"}, {"id": 1, "path": b, "film": "F"}])
        self.assertEqual(added, 1)
        self.assertEqual(len(s.state["images"]), 2)
        self.assertEqual(s.phase, "analyzing")                       # zurueck zur Analyse
        self.assertEqual(s.image(1)["decision"], "accept")           # alte Entscheidung unangetastet
        new_img = [i for i in s.state["images"].values() if i["path"] == b][0]
        self.assertEqual(new_img["status"], "pending")
        with self.assertRaises(sess.SessionError):
            self.session("reviews", []).add_images([])               # nur eigenstaendig

    def test_applied_target_warns_after_switch(self):
        p = self.file("a.jpg")
        s = self.session("copies", [(p, 0.9, [100, 100])])
        s.finish()
        self.assertIsNone(s.public_state()["applied_target"])
        s.reopen()
        s.set_target("json")
        self.assertEqual(s.public_state()["applied_target"], "copies")
        s.finish()
        self.assertIsNone(s.public_state()["applied_target"])        # jetzt wieder auf dem Stand

    def test_darktable_stays_locked_until_lua_reports(self):
        s = self.session("darktable", [(self.file("a.arw"), 0.9, [300, 200])])
        s.finish()
        self.assertEqual(s.phase, "locked")

    def test_failing_target_sets_apply_failed_and_can_reopen(self):
        s = self.session("json", [(self.file("a.jpg"), 0.9, [100, 100])])
        with unittest.mock.patch("companion.targets.manifest.ManifestTarget.apply", side_effect=OSError("voll")):
            s.finish()
        self.assertEqual(s.phase, "apply_failed")
        self.assertEqual(sess.read_json(s.path("result.json"))["message"], "voll")
        s.reopen()
        self.assertEqual((s.phase, s.revision), ("reviewing", 2))

    def test_json_manifest_and_csv(self):
        a, b = self.file("F/a.jpg"), self.file("F/b.jpg")
        s = self.session("json", [(a, 0.9, [2000, 1000]), (b, 0.1, [2000, 1000])])
        s.finish()
        self.assertEqual(s.phase, "applied")
        m = json.load(open(os.path.join(s.out_dir, "crops.json")))
        by = {r["filename"]: r for r in m["images"]}
        self.assertTrue(by["a.jpg"]["apply"])
        self.assertEqual(by["a.jpg"]["crop_px"], {"x": 200, "y": 100, "width": 1600, "height": 800})
        self.assertFalse(by["b.jpg"]["apply"])                       # rot, ungeprueft
        self.assertEqual(by["b.jpg"]["label"], "red")
        rows = list(csv.DictReader(open(os.path.join(s.out_dir, "crops.csv"))))
        self.assertEqual(rows[0]["width"], "1600")
        self.assertTrue(os.path.exists(os.path.join(s.out_dir, targets.OUTPUT_MARKER)))
        res = sess.read_json(s.path("result.json"))
        self.assertEqual(res["images"]["2"]["status"], "skipped")


@needs_cv2
class CopiesTest(Tmp):
    def image(self, rel, w=400, h=200, dtype="uint8", orientation=None):
        """Bild mit hellem Rechteck (x 40..360, y 20..180) auf dunklem Grund."""
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        top = 65535 if dtype == "uint16" else 255
        a = np.zeros((h, w, 3), dtype)
        a[20:180, 40:360] = top
        if orientation:
            im = Image.fromarray(a)
            ex = im.getexif()
            ex[0x0112] = orientation
            im.save(p, exif=ex.tobytes())
        else:
            cv2.imwrite(p, a)
        return p

    def test_copy_crop_revision_and_removal(self):
        p = self.image("Film 1/a.jpg")
        s = self.session("copies", [(p, 0.9, [400, 200])])
        s.patch_images([1], {"crop": [0.1, 0.1, 0.9, 0.9]})
        s.finish()
        dst = os.path.join(s.out_dir, "Film 1", "a.jpg")
        out = cv2.imread(dst)
        self.assertEqual(out.shape[:2], (160, 320))
        self.assertGreater(out.mean(), 240)                           # nur das helle Rechteck
        m = json.load(open(os.path.join(s.out_dir, "crops.json")))
        self.assertEqual(m["images"][0]["copy"], os.path.join("Film 1", "a.jpg"))

        s.reopen()                                                     # nichts geaendert -> bleibt
        s.finish()
        self.assertEqual(sess.read_json(s.path("result.json"))["images"]["1"]["message"], "unveraendert")

        s.reopen()                                                     # ueberspringen -> Kopie weg
        s.patch_images([1], {"decision": "skip"})
        s.finish()
        self.assertFalse(os.path.exists(dst))
        self.assertIsNone(json.load(open(os.path.join(s.out_dir, "crops.json")))["images"][0]["copy"])

    def test_straightened_copy_has_crop_size_of_rotated_frame(self):
        p = self.image("F/a.png")
        s = self.session("copies", [(p, 0.9, [400, 200])])
        s.patch_images([1], {"straighten": {"deg": 5.0}})
        crop = s.effective_crop(s.image(1))
        s.finish()
        W, H = sess.straight_size((400, 200), 5.0)
        out = cv2.imread(os.path.join(s.out_dir, "F", "a.png"))
        self.assertAlmostEqual(out.shape[1], (crop[2] - crop[0]) * W, delta=2)
        self.assertAlmostEqual(out.shape[0], (crop[3] - crop[1]) * H, delta=2)

    def test_16bit_tiff_keeps_depth(self):
        p = self.image("F/a.tif", dtype="uint16")
        s = self.session("copies", [(p, 0.9, [400, 200])])
        s.finish()
        out = cv2.imread(os.path.join(s.out_dir, "F", "a.tif"), cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
        self.assertEqual(out.dtype, np.uint16)
        self.assertIn("16 Bit", sess.read_json(s.path("result.json"))["images"]["1"]["message"])

    def test_exif_orientation_matches_analysis_frame(self):
        p = self.image("F/a.jpg", orientation=6)                      # angezeigt: 200 x 400
        s = self.session("copies", [(p, 0.9, [200, 400])])
        s.patch_images([1], {"crop": [0.1, 0.1, 0.9, 0.9]})
        s.finish()
        out = Image.open(os.path.join(s.out_dir, "F", "a.jpg"))
        self.assertEqual(out.size, (160, 320))
        self.assertEqual(out.getexif().get(0x0112), 1)                # Drehung ist angewendet

    def test_raw_source_uses_export_and_writes_jpeg(self):
        raw = self.file("F/a.nef")
        exp = self.image("exports/a__1.jpg")
        s = self.session("copies", [(raw, 0.9, [400, 200])])
        s.image(1)["export"] = exp
        s.finish()
        self.assertTrue(os.path.exists(os.path.join(s.out_dir, "F", "a.jpg")))


class XmpTest(Tmp):
    EXISTING = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/" crs:Exposure2012="+0.50" tiff:Make="NIKON">
   <crs:CropTop>0.5</crs:CropTop>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

    def test_update_keeps_other_values_prefixes_and_packet(self):
        new = update_xmp(self.EXISTING, {("crs", "CropTop"): "0.100000", ("xmp", "Label"): "Red"})
        self.assertTrue(new.startswith('<?xpacket begin='))
        self.assertTrue(new.rstrip().endswith('<?xpacket end="w"?>'))
        self.assertIn('crs:Exposure2012="+0.50"', new)
        self.assertIn('tiff:Make="NIKON"', new)
        self.assertIn('crs:CropTop="0.100000"', new)
        self.assertNotIn("<crs:CropTop>", new)                           # Element-Form ersetzt
        self.assertIn('xmp:Label="Red"', new)

    def test_target_writes_sidecars_for_raws_only(self):
        raw = self.file("F/a.nef")
        side = os.path.join(self.tmp, "F", "a.xmp")
        with open(side, "w", encoding="utf-8") as f:
            f.write(self.EXISTING)
        jpg = self.file("F/b.jpg")
        s = self.session("xmp", [(raw, 0.9, [3000, 2000]), (jpg, 0.9, [3000, 2000])])
        s.patch_images([1], {"straighten": {"deg": 2.0}})
        with unittest.mock.patch("companion.targets.xmp.raw_orientation", return_value=6):
            s.finish()
        text = open(side, encoding="utf-8").read()
        self.assertIn('crs:HasCrop="True"', text)
        self.assertIn('crs:Exposure2012="+0.50"', text)
        self.assertIn('xmp:Label="Green"', text)
        l, t, r, b = crop_to_stored(s.orig_crop(s.image(1)), 6)
        self.assertIn(f'crs:CropLeft="{l:.6f}"', text)
        self.assertIn(f'crs:CropBottom="{b:.6f}"', text)
        res = sess.read_json(s.path("result.json"))["images"]
        self.assertIn("Winkel", res["1"]["message"])
        self.assertEqual(res["2"]["status"], "skipped")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "F", "b.xmp")))

        s.reopen()
        s.patch_images([1], {"decision": "skip"})
        s.finish()
        self.assertIn('crs:HasCrop="False"', open(side, encoding="utf-8").read())

    def test_file_changed_since_analysis_is_skipped(self):
        raw = self.file("F/a.nef")
        s = self.session("xmp", [(raw, 0.9, [3000, 2000])])
        with open(raw, "ab") as f:
            f.write(b"neu")
        s.finish()
        res = sess.read_json(s.path("result.json"))["images"]["1"]
        self.assertEqual((res["status"], res["message"]), ("skipped", "Datei seit der Analyse geaendert"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "F", "a.xmp")))

    def test_new_sidecar_and_unknown_orientation(self):
        raw = self.file("F/a.cr3")
        s = self.session("xmp", [(raw, 0.9, [3000, 2000])])
        with unittest.mock.patch("companion.targets.xmp.raw_orientation", return_value=None):
            s.finish()
        text = open(os.path.join(self.tmp, "F", "a.xmp"), encoding="utf-8").read()
        self.assertIn('crs:CropLeft="0.100000"', text)
        self.assertIn("Orientierung unbekannt", sess.read_json(s.path("result.json"))["images"]["1"]["message"])


class RawTherapeeTest(Tmp):
    def test_writes_crop_and_label_and_keeps_profile(self):
        raw = self.file("F/a.nef")
        self.file("F/a.nef.pp3", b"[Exposure]\nCompensation=0.3\n\n[Crop]\nEnabled=false\nGuide=Frame\n")
        red = self.file("F/b.nef")
        s = self.session("rawtherapee", [(raw, 0.9, [3000, 2000]), (red, 0.1, [3000, 2000])], converter="rawpy")
        s.finish()
        text = open(raw + ".pp3", encoding="utf-8").read()
        self.assertEqual(pp3.get_value(text, "Crop", "Enabled"), "true")
        self.assertEqual((pp3.get_value(text, "Crop", "X"), pp3.get_value(text, "Crop", "W")), ("300", "2400"))
        self.assertEqual(pp3.get_value(text, "Crop", "Guide"), "Frame")
        self.assertEqual(pp3.get_value(text, "Exposure", "Compensation"), "0.3")
        self.assertEqual(pp3.get_value(text, "General", "ColorLabel"), "3")
        red_text = open(red + ".pp3", encoding="utf-8").read()       # rot: nur Label, kein Crop
        self.assertEqual(pp3.get_value(red_text, "General", "ColorLabel"), "1")
        self.assertIsNone(pp3.get_value(red_text, "Crop", "Enabled"))
        res = sess.read_json(s.path("result.json"))["images"]
        self.assertIn("rawpy", res["1"]["message"])                  # Rahmen aus fremdem Export

        s.reopen()
        s.patch_images([1], {"decision": "skip"})
        s.finish()
        self.assertEqual(pp3.get_value(open(raw + ".pp3").read(), "Crop", "Enabled"), "false")


# ── CLI und echter Durchlauf ─────────────────────────────────────────────────

class CliTest(Tmp):
    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_check_lists_converters_and_suggestion(self):
        self.file("Film/a.jpg")
        code, out = self.run_main(["check", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("rawpy", out)
        self.assertIn("Vorschlag Ziel:      copies", out)

    def test_open_shortcut_creates_then_resumes_session(self):
        self.file("Film/a.jpg")
        root = os.path.join(self.tmp, "root")
        with unittest.mock.patch("companion.__main__._run", return_value=0) as run:
            code, out = self.run_main([self.tmp, "--root", root, "--no-browser"])
            self.assertEqual(code, 0)
            first = run.call_args[0][0]
            self.assertEqual((first.target_name, first.mode), ("copies", "standalone"))
            self.assertEqual(first.out_dir, os.path.join(self.tmp, "autocrop"))
            self.run_main(["open", self.tmp, "--root", root])
            self.assertEqual(run.call_args[0][0].dir, first.dir)             # fortgesetzt
            self.run_main(["open", self.tmp, "--root", root, "--target", "json"])
            self.assertNotEqual(run.call_args[0][0].dir, first.dir)          # anderes Ziel -> neu
            self.run_main(["open", self.tmp, "--root", root, "--new"])
            self.assertNotEqual(run.call_args[0][0].dir, first.dir)
        self.assertEqual(_resume_standalone(root, "/anders", "copies", []), (None, 0))

    def test_open_resumes_and_adds_new_images_without_touching_old_ones(self):
        """Ordner waechst zwischen zwei Aufrufen: die alte Sitzung wird ergaenzt statt neu
        analysiert, bestehende Entscheidungen bleiben (Problem 4/6 aus der QoL-Analyse)."""
        self.file("Film/a.jpg")
        root = os.path.join(self.tmp, "root")
        with unittest.mock.patch("companion.__main__._run", return_value=0) as run:
            self.run_main([self.tmp, "--root", root, "--no-browser"])
            first = run.call_args[0][0]
            first.mark_analysis_done()
            first.patch_images([1], {"decision": "accept"}, log=False)   # simuliert Nutzerarbeit
            self.file("Film/b.jpg")                                      # Ordner waechst
            self.run_main([self.tmp, "--root", root, "--no-browser"])
            second = run.call_args[0][0]
        self.assertEqual(second.dir, first.dir)                          # dieselbe Sitzung
        self.assertEqual(len(second.state["images"]), 2)
        self.assertEqual(second.phase, "analyzing")                      # neues Bild muss analysiert werden
        self.assertEqual(second.image(1)["decision"], "accept")          # alte Entscheidung erhalten
        self.assertEqual(second.image(2)["status"], "pending")

    def test_open_starts_new_session_when_a_file_disappeared(self):
        self.file("Film/a.jpg")
        p_b = self.file("Film/b.jpg")
        root = os.path.join(self.tmp, "root")
        with unittest.mock.patch("companion.__main__._run", return_value=0) as run:
            self.run_main([self.tmp, "--root", root, "--no-browser"])
            first = run.call_args[0][0]
            os.remove(p_b)
            self.run_main([self.tmp, "--root", root, "--no-browser"])
            second = run.call_args[0][0]
        self.assertNotEqual(second.dir, first.dir)

    def test_open_without_raw_converter_fails_clearly(self):
        self.file("Film/a.nef")
        err = io.StringIO()
        with unittest.mock.patch("companion.converters.available", return_value=dict.fromkeys(converters.NAMES, False)), \
                contextlib.redirect_stderr(err):
            code, _ = self.run_main(["open", self.tmp, "--root", os.path.join(self.tmp, "root")])
        self.assertEqual(code, 1)
        self.assertIn("nicht verfuegbar", err.getvalue())

    def test_open_with_explicit_unavailable_converter_fails_before_starting(self):
        """--converter rawtherapee, aber rawtherapee-cli fehlt: soll sofort scheitern, nicht erst
        beim Export (Problem 5 aus der QoL-Analyse)."""
        self.file("Film/a.nef")
        err = io.StringIO()
        only_darktable = {converters.DARKTABLE: True, converters.RAWTHERAPEE: False, converters.RAWPY: False}
        with unittest.mock.patch("companion.converters.available", return_value=only_darktable), \
                unittest.mock.patch("companion.export.find_darktable_cli", return_value=None), \
                contextlib.redirect_stderr(err):
            code, _ = self.run_main(["open", self.tmp, "--converter", "rawtherapee",
                                     "--root", os.path.join(self.tmp, "root")])
        self.assertEqual(code, 1)
        self.assertIn("rawtherapee", err.getvalue())
        self.assertIn("nicht verfuegbar", err.getvalue())

    def test_second_open_on_same_session_does_not_start_a_second_server(self):
        """Zwei Prozesse auf demselben Sitzungsordner: der zweite bedient nicht mit, sondern
        meldet die laufende URL (Problem 1 aus der QoL-Analyse)."""
        self.file("Film/a.jpg")
        root = os.path.join(self.tmp, "root")
        with unittest.mock.patch("companion.__main__._run", return_value=0) as run:
            self.run_main([self.tmp, "--root", root, "--no-browser"])
            s = run.call_args[0][0]
        sess.atomic_write_json(os.path.join(s.dir, "server.json"),
                               {"pid": os.getpid(), "port": 1, "token": "t", "url": "http://x/live"})
        from companion.server import acquire_lock
        held = acquire_lock(s.dir)               # simuliert den laufenden ersten Server
        self.addCleanup(held.close)
        with unittest.mock.patch("companion.__main__.make_server") as ms:
            code, out = self.run_main([self.tmp, "--root", root, "--no-browser"])
        self.assertEqual(code, 0)
        ms.assert_not_called()                    # kein zweiter Server gestartet
        self.assertIn("http://x/live", out)


@needs_cv2
class EndToEndTest(Tmp):
    """Echte Erkennung auf test.jpg im eigenstaendigen Modus, dann zugeschnittene Kopie."""

    def test_detect_and_copy(self):
        from companion.detect import Analyzer
        src = os.path.join(self.tmp, "Scans", "Film 1", "scan.jpg")
        os.makedirs(os.path.dirname(src))
        shutil.copy(TEST_JPG, src)
        job = standalone_job(os.path.join(self.tmp, "Scans"))
        job.update(target="copies", out=os.path.join(self.tmp, "Scans", "autocrop"))
        s = sess.Session.create(job, os.path.join(self.tmp, "root"))
        an = Analyzer(s)
        an.start_analysis()
        an.wait(300)
        img = s.image(1)
        self.assertEqual(img["status"], "done", img.get("error"))
        s.finish()
        self.assertEqual(s.phase, "applied")
        out = Image.open(os.path.join(s.out_dir, "Film 1", "scan.jpg"))
        px = sess.crop_to_pixels(s.orig_crop(img), *img["export_size"])
        self.assertLess(abs(out.size[0] - px["width"]), 4)
        self.assertLess(abs(out.size[1] - px["height"]), 4)
        self.assertEqual(len(standalone_job(os.path.join(self.tmp, "Scans"))["images"]), 1)   # Ausgabe nicht erneut


if __name__ == "__main__":
    unittest.main()
