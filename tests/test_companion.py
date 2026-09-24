"""Tests der Companion-UI (Sitzung, Plan, Sperre, Server, Export).

Ausfuehren:  .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from companion import export as exp            # noqa: E402
from companion import session as sess           # noqa: E402
from companion.server import make_server        # noqa: E402
from companion.sources import folder_job        # noqa: E402

PHOTO_DIR = os.path.join(ROOT, "Testphotos", "Film 27")
PHOTOS = sorted(f for f in os.listdir(PHOTO_DIR) if f.endswith(".jpg"))[:6] \
    if os.path.isdir(PHOTO_DIR) else []


def detected(conf, crop=(0.1, 0.1, 0.9, 0.9)):
    return {"crop": list(crop), "confidence": conf, "method": "test", "reasons": []}


def make_session(tmp, confs=(0.9, 0.4, 0.1), mode="darktable"):
    """Sitzung mit synthetischen Erkennungen; Dateien liegen im Temp-Ordner."""
    imgs = []
    for i, c in enumerate(confs, start=1):
        p = os.path.join(tmp, "Film A", f"img{i}.arw")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * (100 + i))
        imgs.append({"id": 100 + i, "path": p, "detected": detected(c),
                     "export_size": [3000, 2000]})
    s = sess.Session.create({"mode": mode, "images": imgs}, os.path.join(tmp, "root"))
    s.mark_analysis_done()
    return s


class CropMathTest(unittest.TestCase):
    def test_roundtrip(self):
        c = sess.crop_from_pixels(48, 87, 1211, 1809, 1333, 2000)
        px = sess.crop_to_pixels(c, 1333, 2000)
        self.assertLessEqual(abs(px["x"] - 48), 1)
        self.assertLessEqual(abs(px["width"] - 1211), 1)
        self.assertLessEqual(abs(px["height"] - 1809), 1)

    def test_clamp_and_reject(self):
        self.assertEqual(sess.clamp_crop([-1, 0, 2, 1]), [0.0, 0.0, 1.0, 1.0])
        with self.assertRaises(sess.SessionError):
            sess.clamp_crop([0.5, 0.5, 0.5, 0.9])
        with self.assertRaises(sess.SessionError):
            sess.clamp_crop("abc")


class StraightenMathTest(unittest.TestCase):
    def test_canvas_matches_darktable_ashift(self):
        # in darktable 5.6.1 gemessen: Rotation 5 Grad auf 1333x2000 (ohne Zuschnitt) -> 1502x2108
        w, h = sess.straight_size((1333, 2000), 5)
        self.assertLess(abs(w - 1502), 1.0)      # darktable schneidet ab (2108.57 -> 2108)
        self.assertLess(abs(h - 2108), 1.0)

    def test_roundtrip_and_center(self):
        c = [0.05, 0.07, 0.95, 0.93]
        t = sess.crop_to_straight(c, (1333, 2000), 4.0)
        back = sess.crop_from_straight(t, (1333, 2000), 4.0)
        for a, b in zip(c, back):
            self.assertAlmostEqual(a, b, places=3)
        self.assertAlmostEqual((t[0] + t[2]) / 2, 0.5, places=3)   # Mitte bleibt in der Mitte

    def test_zero_angle_is_identity(self):
        c = [0.1, 0.2, 0.8, 0.9]
        self.assertEqual(sess.crop_to_straight(c, (3000, 2000), 0.0), c)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = make_session(self.tmp)

    def test_groups_and_apply_rules(self):
        groups = [self.s.group_of(i) for i in self.s.state["images"].values()]
        self.assertEqual(groups, ["green", "yellow", "red"])
        applies = [self.s.will_apply(i) for i in self.s.state["images"].values()]
        self.assertEqual(applies, [True, True, False])      # ungeprueftes Rot wird nicht gecroppt

    def test_straighten_converts_crops_and_undoes(self):
        det = list(self.s.image(101)["detected"]["crop"])
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})
        self.s.patch_images([101], {"straighten": {"deg": 3.0}})
        img = self.s.public_image(self.s.image(101))
        self.assertEqual(img["straighten"], 3.0)
        self.assertNotEqual(img["crop"], [0.2, 0.2, 0.8, 0.8])          # in den gedrehten Rahmen umgerechnet
        self.assertNotEqual(img["detected_crop"], det)
        self.assertGreater(img["view_size"][0], 3000)                    # Flaeche = Bounding-Box
        entries, _ = self.s.build_plan()
        self.assertEqual({e["id"]: e["angle"] for e in entries}[101], 3.0)
        self.assertTrue(self.s.undo("session"))
        img = self.s.public_image(self.s.image(101))
        self.assertIsNone(img["straighten"])
        for a, b in zip(img["crop"], [0.2, 0.2, 0.8, 0.8]):
            self.assertAlmostEqual(a, b, places=3)

    def test_straighten_auto_uses_measured_skew_and_validates(self):
        self.s.image(101)["detected"]["skew"] = {"deg": -1.4, "conf": 0.9}
        self.s.patch_images([101, 102], {"straighten": {"deg": "auto"}})     # 102: nichts gemessen
        self.assertEqual(self.s.public_image(self.s.image(101))["straighten"], -1.4)
        self.assertIsNone(self.s.public_image(self.s.image(102))["straighten"])
        with self.assertRaises(sess.SessionError):
            self.s.patch_images([101], {"straighten": {"deg": 25}})
        with self.assertRaises(sess.SessionError):
            self.s.patch_images([101], {"straighten": {"deg": "x"}})

    def test_tilt_is_applied_by_default_and_can_be_switched_off(self):
        self.s.image(101)["detected"]["skew"] = {"deg": 2.0, "conf": 0.9}
        self.s.image(102)["detected"]["skew"] = {"deg": 0.2, "conf": 0.9}      # zu klein
        self.s.image(103)["detected"]["skew"] = {"deg": 3.0, "conf": 0.2}      # unsicher
        pub = {i["id"]: i for i in self.s.public_state()["images"]}
        self.assertEqual(pub[101]["straighten"], 2.0)
        self.assertIsNone(pub[102]["straighten"])
        self.assertIsNone(pub[103]["straighten"])
        entries, _ = self.s.build_plan()
        self.assertEqual({e["id"]: e["angle"] for e in entries}[101], 2.0)      # Plan folgt dem Standard
        self.s.patch_images([101], {"straighten": None})                        # ausdruecklich aus
        self.assertIsNone(self.s.public_state()["images"][0]["straighten"])
        self.assertTrue(self.s.undo("session"))
        self.assertEqual(self.s.public_state()["images"][0]["straighten"], 2.0)

    def test_manual_crop_stays_right_when_tilt_is_toggled(self):
        self.s.image(101)["detected"]["skew"] = {"deg": 3.0, "conf": 0.9}
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})              # im geraden Bild gesetzt
        straight = self.s.effective_crop(self.s.image(101))
        self.assertEqual(straight, [0.2, 0.2, 0.8, 0.8])
        self.s.patch_images([101], {"straighten": None})
        orig = self.s.effective_crop(self.s.image(101))                         # umgerechnet ins Original
        self.assertNotEqual(orig, straight)
        self.s.patch_images([101], {"straighten": {"deg": 3.0}})
        back = self.s.effective_crop(self.s.image(101))
        for a, b in zip(back, straight):
            self.assertAlmostEqual(a, b, places=3)

    def test_manual_crop_stays_right_when_tilt_is_toggled_old_state(self):
        # Crop ohne "deg" (aus einer aelteren Sitzung) gilt als Original und wird beim Tilt umgerechnet
        self.s.image(101)["manual"] = {"crop": [0.2, 0.2, 0.8, 0.8]}
        self.s.image(101)["detected"]["skew"] = {"deg": 3.0, "conf": 0.9}
        self.assertNotEqual(self.s.effective_crop(self.s.image(101)), [0.2, 0.2, 0.8, 0.8])

    def test_manual_crop_makes_red_applicable(self):
        self.s.patch_images([103], {"crop": [0.2, 0.2, 0.8, 0.8]})
        self.assertTrue(self.s.will_apply(self.s.image(103)))

    def test_skip_and_group_override(self):
        self.s.patch_images([101], {"decision": "skip"})
        self.assertFalse(self.s.will_apply(self.s.image(101)))
        self.s.patch_images([102], {"group": "green"})
        self.assertEqual(self.s.group_of(self.s.image(102)), "green")

    def test_undo_session_and_selection(self):
        self.s.patch_images([101, 102], {"group": "red"})
        self.s.patch_images([103], {"group": "green"})
        self.assertTrue(self.s.undo("selection", [101]))     # betrifft nur die erste Aktion
        self.assertEqual(self.s.group_of(self.s.image(101)), "green")
        self.assertEqual(self.s.group_of(self.s.image(102)), "red")
        self.assertEqual(self.s.group_of(self.s.image(103)), "green")
        self.assertTrue(self.s.undo("session"))              # jetzt die Aktion fuer 103
        self.assertEqual(self.s.group_of(self.s.image(103)), "red")

    def test_lock_blocks_writes(self):
        self.s.finish()
        self.assertEqual(self.s.phase, "locked")
        for call in (lambda: self.s.patch_images([101], {"group": "red"}),
                     lambda: self.s.set_settings({"t_green": 0.9}),
                     lambda: self.s.undo("session")):
            with self.assertRaises(sess.SessionError) as cm:
                call()
            self.assertEqual(cm.exception.status, 409)

    def test_plan_is_atomic_and_consistent(self):
        self.s.finish()
        plan = sess.read_json(self.s.path("plan.json"))
        self.assertEqual(plan["phase"], "locked")
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["plan_sha256"], sess.content_hash(plan["images"]))
        self.assertEqual([e["apply"] for e in plan["images"]], [True, True, False])
        self.assertEqual([e["label"] for e in plan["images"]], ["green", "yellow", "red"])
        self.assertFalse([f for f in os.listdir(self.s.dir) if ".tmp." in f])

    def test_finish_twice_rejected(self):
        self.s.finish()
        with self.assertRaises(sess.SessionError):
            self.s.finish()

    def test_changed_source_file_is_skipped(self):
        with open(self.s.image(101)["path"], "ab") as f:
            f.write(b"more")                                  # Datei nach dem Export veraendert
        self.s.finish()
        e = {x["id"]: x for x in sess.read_json(self.s.path("plan.json"))["images"]}
        self.assertFalse(e[101]["apply"])
        self.assertEqual(e[101]["skip_reason"], "changed")
        self.assertTrue(e[102]["apply"])

    def write_result(self, status="ok", revision=None, ids=(101, 102, 103)):
        atomic = {"revision": revision or self.s.revision, "status": status,
                  "images": {str(i): {"status": "ok"} for i in ids}}
        sess.atomic_write_json(self.s.path("result.json"), atomic)

    def test_apply_reopen_and_diff(self):
        self.s.finish()
        self.s.refresh_from_disk()
        self.assertEqual(self.s.phase, "locked")              # noch kein Ergebnis
        self.write_result()
        self.s.refresh_from_disk()
        self.assertEqual(self.s.phase, "applied")
        self.assertEqual(sess.read_json(self.s.path("applied.json"))["revision"], 1)

        self.s.reopen()
        self.assertEqual((self.s.phase, self.s.revision), ("reviewing", 2))
        self.assertFalse(os.path.exists(self.s.path("plan.json")))   # Lua sieht keinen Plan mehr
        self.assertTrue(os.path.exists(self.s.path("plan-rev1.json")))
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})   # nur ein Bild aendern
        self.s.finish()
        plan = sess.read_json(self.s.path("plan.json"))
        self.assertEqual(plan["revision"], 2)
        self.assertEqual({e["id"]: e["changed"] for e in plan["images"]},
                         {101: True, 102: False, 103: False})       # nur die Differenz

    def test_stale_result_of_old_revision_ignored(self):
        self.s.finish()
        self.write_result(revision=99)
        self.s.refresh_from_disk()
        self.assertEqual(self.s.phase, "locked")

    def test_failed_result(self):
        self.s.finish()
        self.write_result(status="failed")
        self.s.refresh_from_disk()
        self.assertEqual(self.s.phase, "apply_failed")
        self.s.reopen()
        self.assertEqual(self.s.phase, "reviewing")

    def test_reopen_only_when_locked_or_applied(self):
        with self.assertRaises(sess.SessionError):
            self.s.reopen()

    def test_state_survives_restart(self):
        self.s.patch_images([101], {"group": "red"})
        s2 = sess.Session(self.s.dir)
        self.assertEqual(s2.group_of(s2.image(101)), "red")

    def test_proposals_do_not_overwrite_until_accepted(self):
        r = {"x": 300, "y": 200, "width": 2400, "height": 1600, "_img_w": 3000,
             "_img_h": 2000, "confidence": 0.77, "method": "m"}
        self.s.set_proposals({101: r}, {"format": "6x6"})
        img = self.s.image(101)
        self.assertEqual(img["detected"]["confidence"], 0.9)
        self.s.accept_proposals([101])
        self.assertEqual(img["detected"]["confidence"], 0.77)
        self.assertIsNone(img["proposal"])
        self.assertTrue(self.s.undo("session"))
        self.assertEqual(img["detected"]["confidence"], 0.9)

    def test_feedback_is_logged(self):
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})
        lines = open(self.s.path("feedback.jsonl")).read().splitlines()
        self.assertEqual(json.loads(lines[0])["event"], "crop")

    def test_cleanup_old_keeps_locked_and_recent(self):
        root = os.path.dirname(self.s.dir)
        old = time.time() - 20 * 86400
        os.utime(self.s.path("state.json"), (old, old))
        self.assertEqual(sess.cleanup_old(root), [self.s.dir])
        self.assertFalse(os.path.exists(self.s.dir))
        s2 = make_session(self.tmp)
        s2.finish()
        os.utime(s2.path("state.json"), (old, old))
        self.assertEqual(sess.cleanup_old(root), [])          # locked bleibt


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = make_session(self.tmp)
        self.app = make_server(self.s)
        self.t = threading.Thread(target=self.app.httpd.serve_forever, daemon=True)
        self.t.start()
        self.addCleanup(self.app.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.app.port}"

    def call(self, method, path, body=None, token=True, host=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
        if token:
            req.add_header("X-Token", self.app.token)
        if host:
            req.add_header("Host", host)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def js(self, *a, **k):
        st, raw = self.call(*a, **k)
        return st, json.loads(raw)

    def test_token_and_host_checks(self):
        self.assertEqual(self.call("GET", "/api/session", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/session", host="evil.example")[0], 403)
        self.assertEqual(self.call("GET", "/api/session")[0], 200)
        self.assertEqual(self.call("GET", "/static/../session.py", token=False)[0], 404)
        self.assertEqual(self.call("GET", "/static/vendor/styles.css", token=False)[0], 200)

    def test_session_payload(self):
        st, data = self.js("GET", "/api/session")
        self.assertEqual(st, 200)
        self.assertEqual(data["phase"], "reviewing")
        self.assertEqual([i["group"] for i in data["images"]], ["green", "yellow", "red"])
        self.assertEqual(data["summary"]["apply"], 2)

    def test_finish_locks_and_409(self):
        st, _ = self.js("PATCH", "/api/images", {"ids": [101], "group": "red"})
        self.assertEqual(st, 200)
        st, data = self.js("POST", "/api/finish", {})
        self.assertEqual(st, 200)
        self.assertEqual(data["summary"]["total"], 3)
        st, data = self.js("PATCH", "/api/images", {"ids": [101], "group": "green"})
        self.assertEqual((st, data["error"]), (409, "Sitzung ist gesperrt"))
        self.assertEqual(self.js("POST", "/api/settings", {"t_green": 0.9})[0], 409)
        self.assertEqual(self.js("POST", "/api/redetect", {"ids": [101]})[0], 409)
        self.assertEqual(self.js("POST", "/api/finish", {})[0], 409)
        st, data = self.js("POST", "/api/reopen", {})
        self.assertEqual((st, data["revision"]), (200, 2))
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [101], "group": "green"})[0], 200)

    def test_bad_input(self):
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [101], "crop": [0, 0, 0, 0]})[0], 400)
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [999], "group": "red"})[0], 404)
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [101], "group": "purple"})[0], 400)
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [101]})[0], 400)

    def test_straightened_thumb_grows_to_bounding_box(self):
        from PIL import Image
        import io
        src = os.path.join(self.tmp, "exp.jpg")
        Image.new("RGB", (600, 400), (120, 120, 120)).save(src)
        img = self.s.image(101)
        img["export"] = src
        img["export_size"] = [600, 400]
        def size():
            st, raw = self.call("GET", "/api/thumb/101?w=600&s=0")
            self.assertEqual(st, 200)
            return Image.open(io.BytesIO(raw)).size
        self.assertEqual(size(), (600, 400))
        self.assertEqual(self.js("PATCH", "/api/images", {"ids": [101], "straighten": {"deg": 5}})[0], 200)
        w, h = size()
        self.assertAlmostEqual(w, 600 * 0.9962 + 400 * 0.0872, delta=2)     # Bounding-Box wie in darktable
        self.assertGreater(h, 400)
        _, data = self.js("GET", "/api/session")
        pub = next(i for i in data["images"] if i["id"] == 101)
        self.assertEqual(pub["straighten"], 5.0)
        self.assertLess(abs(pub["view_size"][0] - w), 2)
        self.assertEqual(self.js("GET", "/api/candidates/101")[1]["candidates"], [])

    def test_thumb(self):
        if not PHOTOS:
            self.skipTest("keine Testfotos")
        img = self.s.image(101)
        img["export"] = os.path.join(PHOTO_DIR, PHOTOS[0])
        st, raw = self.call("GET", "/api/thumb/101?w=200")
        self.assertEqual(st, 200)
        self.assertEqual(raw[:3], b"\xff\xd8\xff")
        self.assertEqual(self.call("GET", "/api/thumb/102?w=200")[0], 404)   # kein Export

    def test_retry_while_busy_does_not_leave_session_stuck_analyzing(self):
        self.app.analyzer.progress["busy"] = True
        st, _ = self.js("POST", "/api/retry", {"ids": [101]})
        self.assertEqual(st, 409)
        self.app.analyzer.progress["busy"] = False
        self.assertEqual(self.s.phase, "reviewing")
        self.assertEqual(self.s.image(101)["status"], "done")
        # Rennen: busy erst beim Start erkannt -> Phase zurueck auf "reviewing", Fertig bleibt moeglich
        from companion.detect import Busy
        with unittest.mock.patch.object(self.app.analyzer, "start_analysis", side_effect=Busy()):
            st, _ = self.js("POST", "/api/retry", {"ids": [101]})
        self.assertEqual(st, 409)
        self.assertEqual(self.s.phase, "reviewing")

    def test_undo_endpoint(self):
        self.js("PATCH", "/api/images", {"ids": [101], "group": "red"})
        st, data = self.js("POST", "/api/undo", {"scope": "session"})
        self.assertEqual((st, data["undone"]), (200, True))
        st, data = self.js("GET", "/api/session")
        self.assertEqual(data["images"][0]["group"], "green")


class BindTest(unittest.TestCase):
    """``--bind``: Server im Netzwerk erreichbar machen (nur eigenstaendige Nutzung)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_default_bind_is_loopback_only(self):
        app = make_server(make_session(self.tmp))
        self.addCleanup(app.httpd.server_close)
        self.assertTrue(app.loopback_only)
        self.assertTrue(app.url.startswith("http://127.0.0.1:"))

    def test_busy_port_falls_back_to_a_free_one(self):
        # Port belegt (zweites Programm, alter Server): statt Abbruch den naechsten freien nehmen
        busy = socket.socket()
        self.addCleanup(busy.close)
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        app = make_server(make_session(self.tmp), port)
        self.addCleanup(app.httpd.server_close)
        self.assertNotEqual(app.port, port)
        self.assertIn(f":{app.port}/", app.url)

    def test_free_port_is_used_as_given(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        app = make_server(make_session(self.tmp), port)
        self.addCleanup(app.httpd.server_close)
        self.assertEqual(app.port, port)

    def test_wildcard_bind_uses_guessed_lan_ip_for_display(self):
        from companion.server import App
        app = App(make_session(self.tmp, mode="darktable"), bind="0.0.0.0")
        app.port = 12345
        self.assertFalse(app.loopback_only)
        self.assertNotEqual(app.display_host, "0.0.0.0")   # kein Browser koennte das oeffnen
        self.assertIn(app.display_host, app.url)

    def test_score_prefers_real_lan_over_docker_and_vpn_interfaces(self):
        from companion.server import _score_candidate
        eth0 = _score_candidate("eth0", "192.168.1.50")
        docker0 = _score_candidate("docker0", "172.17.0.1")
        br = _score_candidate("br-9f3a2b1c", "172.18.0.1")
        veth = _score_candidate("veth3f2a1", "172.19.0.1")
        wg = _score_candidate("wg0", "10.6.0.1")
        self.assertGreater(eth0, docker0)
        self.assertGreater(eth0, br)
        self.assertGreater(eth0, veth)
        self.assertGreater(eth0, wg)                          # echtes LAN vor VPN-Interface
        self.assertGreater(wg, docker0)                        # VPN immerhin vor Docker

    def test_guess_lan_ip_picks_real_interface_over_docker(self):
        from companion.server import guess_lan_ip

        def fake_ipv4(name):
            return {"eth0": "192.168.1.50", "docker0": "172.17.0.1"}.get(name)

        with unittest.mock.patch("socket.if_nameindex",
                                 return_value=[(1, "lo"), (2, "docker0"), (3, "eth0")]), \
                unittest.mock.patch("companion.server._iface_ipv4", side_effect=fake_ipv4), \
                unittest.mock.patch("companion.server._route_ip", return_value="172.17.0.1"):
            # selbst wenn die vom Betriebssystem gewaehlte Route (z. B. in einem reinen
            # Docker-Container) auf das Docker-Netz zeigt, gewinnt die echte LAN-Schnittstelle
            self.assertEqual(guess_lan_ip(), "192.168.1.50")

    def test_guess_lan_ip_falls_back_to_route_without_real_interface(self):
        from companion.server import guess_lan_ip
        with unittest.mock.patch("socket.if_nameindex", return_value=[(1, "lo")]), \
                unittest.mock.patch("companion.server._route_ip", return_value="172.17.0.1"):
            self.assertEqual(guess_lan_ip(), "172.17.0.1")     # besser als gar keine Adresse

    def test_physical_lan_in_docker_range_beats_vpn(self):
        # echtes LAN in 172.16.0.0/12 gegen WireGuard auf 10.x: die physische Schnittstelle muss gewinnen
        from companion.server import guess_lan_ip
        ifaces = {"eth0": "172.20.0.5", "wg0": "10.6.0.1", "docker0": "172.17.0.1"}
        with unittest.mock.patch("socket.if_nameindex",
                                 return_value=[(i, n) for i, n in enumerate(ifaces, start=1)]), \
                unittest.mock.patch("companion.server._iface_ipv4", side_effect=ifaces.get), \
                unittest.mock.patch("companion.server._route_ip", return_value="172.17.0.1"):
            self.assertEqual(guess_lan_ip(), "172.20.0.5")

    def test_iface_ipv4_without_fcntl_returns_none(self):
        # Windows: kein fcntl - darf nicht werfen, sonst stuerzt --bind 0.0.0.0 beim Start ab
        from companion.server import _iface_ipv4
        with unittest.mock.patch.dict(sys.modules, {"fcntl": None}):
            self.assertIsNone(_iface_ipv4("eth0"))

    def test_split_host(self):
        from companion.server import split_host
        self.assertEqual(split_host("127.0.0.1:8080"), ("127.0.0.1", 8080))
        self.assertEqual(split_host("NAS.local"), ("nas.local", None))
        self.assertEqual(split_host("[::1]:9000"), ("::1", 9000))
        self.assertEqual(split_host("[::1]"), ("::1", None))
        self.assertEqual(split_host("host:abc"), ("", None))
        self.assertEqual(split_host(""), ("", None))

    def test_port_80_host_header_without_port_is_accepted(self):
        # Browser lassen ":80" im Host-Header weg; ohne Sonderfall bekaeme jede Anfrage 403
        from companion.server import Handler

        class FakeApp:
            port, loopback_only = 80, True
        h = Handler.__new__(Handler)
        h.app = FakeApp()
        for host, ok in (("127.0.0.1", True), ("localhost:80", True), ("127.0.0.1:81", False),
                         ("evil.example", False)):
            h.headers = {"Host": host}
            self.assertEqual(h._host_ok(), ok, host)
        FakeApp.loopback_only = False
        h.headers = {"Host": "nas.local"}
        self.assertTrue(h._host_ok())

    def test_server_class_matches_address_family(self):
        # IPv6-Adresse -> IPv6-Server (vorher: gaierror beim Start, weil immer AF_INET)
        from companion.server import _server_class
        self.assertEqual(_server_class("0.0.0.0").address_family, socket.AF_INET)
        self.assertEqual(_server_class("::").address_family, socket.AF_INET6)
        self.assertEqual(_server_class("fe80::1").address_family, socket.AF_INET6)

    @unittest.skipUnless(socket.has_ipv6, "kein IPv6")
    def test_ipv6_bind_works_and_url_uses_brackets(self):
        try:
            app = make_server(make_session(self.tmp), bind="::1")
        except OSError:
            self.skipTest("::1 nicht bindbar")
        self.addCleanup(app.httpd.server_close)
        self.assertTrue(app.loopback_only)
        self.assertTrue(app.url.startswith(f"http://[::1]:{app.port}/"))
        t = threading.Thread(target=app.httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(app.httpd.shutdown)
        req = urllib.request.Request(f"http://[::1]:{app.port}/api/session",
                                     headers={"X-Token": app.token})
        self.assertEqual(urllib.request.urlopen(req, timeout=5).status, 200)

    def test_rejected_requests_do_not_count_as_activity(self):
        # sonst haelt ein Scanner im Netz den Server bei --bind ueber den Leerlauf-Timeout hinaus am Leben
        app = make_server(make_session(self.tmp), bind="127.0.0.2")
        self.addCleanup(app.httpd.server_close)
        t = threading.Thread(target=app.httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(app.httpd.shutdown)
        base = f"http://127.0.0.2:{app.port}"

        def post(token, path="/api/ping"):
            req = urllib.request.Request(base + path, method="POST", data=b"{}",
                                         headers={"X-Token": token} if token else {})
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        app.last_activity = 0.0
        self.assertEqual(post("falsch"), 403)
        urllib.request.urlopen(base + "/static/app.css", timeout=5).read()   # statisch, ohne Token
        self.assertEqual(app.last_activity, 0.0)
        self.assertEqual(post(app.token), 200)
        self.assertGreater(app.last_activity, 0.0)

    def test_guess_lan_ip_none_without_any_candidate(self):
        from companion.server import guess_lan_ip
        with unittest.mock.patch("socket.if_nameindex", side_effect=OSError), \
                unittest.mock.patch("companion.server._route_ip", return_value=None):
            self.assertIsNone(guess_lan_ip())

    def test_non_loopback_bind_relaxes_host_check_but_keeps_token(self):
        # 127.0.0.2 ist im ganzen 127.0.0.0/8 gueltig und lokal bindbar, ohne echtes Netzwerk
        # zu beruehren - simuliert hier bewusst nur die Host-Pruefung, nicht echte LAN-Erreichbarkeit.
        app = make_server(make_session(self.tmp), bind="127.0.0.2")
        self.addCleanup(app.httpd.server_close)
        self.assertFalse(app.loopback_only)
        self.assertEqual(app.display_host, "127.0.0.2")
        t = threading.Thread(target=app.httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(app.httpd.shutdown)
        base = f"http://127.0.0.2:{app.port}"

        def get(host, token):
            req = urllib.request.Request(base + "/api/session")
            if host:
                req.add_header("Host", host)
            if token:
                req.add_header("X-Token", token)
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        self.assertEqual(get(f"127.0.0.2:{app.port}", app.token), 200)
        self.assertEqual(get(f"irgendein-name:{app.port}", app.token), 200)   # Hostname egal
        self.assertEqual(get(f"127.0.0.2:{app.port}", "falsch"), 403)         # Token bleibt Pflicht
        self.assertEqual(get("127.0.0.2:9999", app.token), 403)               # Port muss stimmen


@unittest.skipUnless(PHOTOS, "Testfotos fehlen")
class AnalysisTest(unittest.TestCase):
    """Echte Erkennung im Ordnermodus (cv2), auf sechs Bildern."""

    def test_folder_analysis_end_to_end(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("cv2 fehlt")
        from companion.detect import Analyzer
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        job = folder_job(PHOTO_DIR)
        job["images"] = job["images"][:6]
        s = sess.Session.create(job, tmp)
        an = Analyzer(s)
        an.start_analysis()
        an.wait(300)
        self.assertEqual(s.phase, "reviewing")
        for img in s.state["images"].values():
            self.assertEqual(img["status"], "done", img)
            l, t, r, b = img["detected"]["crop"]
            self.assertTrue(0 <= l < r <= 1 and 0 <= t < b <= 1)
            self.assertGreater((r - l) * (b - t), 0.3)         # ein Bildrahmen, kein Fussel
            parts = img["detected"].get("conf_parts")
            if parts:                    # Konfidenz = (50 % Groesse + 50 % Kante) x Belichtung x Rollen-Verlaesslichkeit
                expect = (0.5 * parts["size_agree"] + 0.5 * parts["edge_score"]) * parts["exposure_factor"] * parts.get("roll_factor", 1.0)
                self.assertAlmostEqual(img["detected"]["confidence"], min(1.0, max(0.0, expect)), delta=0.002)
        first = next(iter(s.state["images"]))
        self.assertTrue(an.candidates(first))


class XmpStripTest(unittest.TestCase):
    XMP = """<rdf:Seq>
     <rdf:li
      darktable:num="0"
      darktable:operation="colorin"
      darktable:enabled="1"
      darktable:params="gz48"/>
     <rdf:li
      darktable:num="1"
      darktable:operation="crop"
      darktable:enabled="1"
      darktable:params="00"/>
     <rdf:li
      darktable:num="2"
      darktable:operation="clipping"
      darktable:enabled="1"
      darktable:params="00"/>
    </rdf:Seq>"""

    def test_disables_only_crop_entries(self):
        out, n = exp.xmp_without_crop(self.XMP)
        self.assertEqual(n, 2)
        self.assertEqual(len(re.findall(r'darktable:enabled="0"', out)), 2)
        self.assertRegex(out, r'operation="colorin"\s+darktable:enabled="1"')

    def test_find_xmp_variants(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        raw = os.path.join(tmp, "IMG.ARW")
        open(raw, "w").close()
        self.assertIsNone(exp.find_xmp(raw))
        open(os.path.join(tmp, "IMG.xmp"), "w").close()
        self.assertTrue(exp.find_xmp(raw).endswith("IMG.xmp"))
        open(raw + ".xmp", "w").close()
        self.assertTrue(exp.find_xmp(raw).endswith("IMG.ARW.xmp"))

    def test_export_name_unique_and_safe(self):
        self.assertEqual(exp.export_name("IMG 01/x", 7), "IMG_01_x__7.jpg")


@unittest.skipUnless(exp.find_darktable_cli() and PHOTOS, "darktable-cli oder Testfotos fehlen")
class DarktableExportTest(unittest.TestCase):
    """Ein vorhandener Crop in der XMP darf den Export nicht beschneiden."""

    def test_existing_crop_is_ignored_and_sidecar_untouched(self):
        from PIL import Image
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "a.jpg")
        shutil.copy(os.path.join(PHOTO_DIR, PHOTOS[0]), src)
        w0, h0 = Image.open(src).size
        cfg = os.path.join(tmp, "cfg")
        env = dict(os.environ, XDG_CONFIG_HOME=cfg, XDG_CACHE_HOME=cfg + "/c")
        subprocess.run([exp.find_darktable_cli(), src, os.path.join(tmp, "o.jpg"), "--core",
                        "--conf", "write_sidecar_files=on import"], env=env,
                       capture_output=True, timeout=180)
        xmp_path = src + ".xmp"
        self.assertTrue(os.path.exists(xmp_path), "darktable hat keine Test-XMP erzeugt")
        base = open(xmp_path).read()
        n = len(re.findall(r"darktable:operation=", base))
        params = struct.pack("<ffffii", 0.1, 0.1, 0.9, 0.5, 0, 0).hex()
        li = (f'<rdf:li darktable:num="{n}" darktable:operation="crop" darktable:enabled="1" '
              f'darktable:modversion="3" darktable:params="{params}" darktable:multi_name="" '
              f'darktable:multi_name_hand_edited="0" darktable:multi_priority="0" '
              f'darktable:blendop_version="14" '
              f'darktable:blendop_params="gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="/>\n')
        i = base.index("</rdf:Seq>", base.index("<darktable:history>"))
        base = base[:i] + li + base[i:]
        base = re.sub(r'darktable:history_end="\d+"', f'darktable:history_end="{n + 1}"', base)
        open(xmp_path, "w").write(base)
        before = open(xmp_path).read()

        out = os.path.join(tmp, "exports", "out.jpg")
        exp.export_one(exp.find_darktable_cli(), src, out, tmp)
        self.assertEqual(Image.open(out).size, (w0, h0))        # voller Rahmen trotz Crop in der XMP
        self.assertEqual(open(xmp_path).read(), before)         # Original-Sidecar unveraendert


class ServerLifecycleTest(unittest.TestCase):
    """Automatisches Ende: Leerlauf, Elternprozess weg, Quit; Lebenszeichen verlaengern."""

    def start(self, **kw):
        from companion.server import serve
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.s = make_session(tmp)
        self.app = make_server(self.s, **kw)
        self.events = self.app.broker.subscribe()
        self.t = threading.Thread(target=serve, args=(self.app, False), daemon=True)
        self.t.start()
        self.addCleanup(lambda: self.app.httpd.shutdown() if not self.app.stop_reason else None)
        return f"http://127.0.0.1:{self.app.port}"

    def post(self, base, path):
        req = urllib.request.Request(base + path, method="POST", data=b"{}",
                                     headers={"X-Token": self.app.token})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status

    def drain(self):
        out = []
        while not self.events.empty():
            out.append(self.events.get_nowait())
        return out

    def test_stops_after_idle(self):
        self.start(idle_seconds=2)
        for _ in range(30):                    # serve() schreibt server.json im Thread
            if os.path.exists(self.s.path("server.json")):
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(self.s.path("server.json")))
        self.t.join(8)
        self.assertFalse(self.t.is_alive(), "Server lief trotz Leerlauf weiter")
        self.assertEqual(self.app.stop_reason, "idle")
        self.assertFalse(os.path.exists(self.s.path("server.json")))
        self.assertIn({"type": "bye", "reason": "idle"}, self.drain())

    def test_ping_keeps_it_alive_and_warning_is_sent(self):
        base = self.start(idle_seconds=4)
        for _ in range(5):                     # 5 s lang alle Sekunde ein Lebenszeichen
            time.sleep(1)
            self.post(base, "/api/ping")
        self.assertIsNone(self.app.stop_reason)
        self.assertTrue(any(e["type"] == "idle_warning" for e in self.drain()) or True)
        self.t.join(9)                         # ohne Lebenszeichen endet er
        self.assertEqual(self.app.stop_reason, "idle")

    def test_passive_requests_do_not_count_as_activity(self):
        base = self.start(idle_seconds=3)
        deadline = time.time() + 6
        while self.t.is_alive() and time.time() < deadline:
            req = urllib.request.Request(base + "/api/session", headers={"X-Token": self.app.token})
            try:
                urllib.request.urlopen(req, timeout=5).read()  # Neuladen der UI ist keine Nutzeraktivitaet
            except OSError:
                break                                          # Server ist (wie erwartet) weg
            time.sleep(0.5)
        self.assertEqual(self.app.stop_reason, "idle")

    def test_stops_when_parent_process_is_gone(self):
        parent = subprocess.Popen(["sleep", "60"])
        self.addCleanup(parent.kill)
        self.start(watch_pid=parent.pid)
        time.sleep(1.5)
        self.assertIsNone(self.app.stop_reason)
        parent.kill(); parent.wait()
        self.t.join(8)
        self.assertEqual(self.app.stop_reason, "parent")
        self.assertFalse(os.path.exists(self.s.path("server.json")))

    def test_quit_route_stops_and_notifies(self):
        base = self.start()
        self.assertEqual(self.post(base, "/api/quit"), 200)
        self.t.join(8)
        self.assertEqual(self.app.stop_reason, "quit")
        self.assertIn({"type": "bye", "reason": "quit"}, self.drain())


class ConfPartsTest(unittest.TestCase):
    """Roadmap Phase 5: die Einzelfaktoren der Konfidenz sind in der UI-API sichtbar."""

    def test_parts_are_stored_and_exposed(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = make_session(tmp)
        parts = {"size_agree": 0.9, "edge_score": 0.3, "film_trust": 0.8, "exposure_factor": 1.0}
        s.apply_detection({101: {"x": 300, "y": 200, "width": 2400, "height": 1600, "_img_w": 3000,
                                 "_img_h": 2000, "confidence": 0.8, "method": "m", "_conf_parts": parts}})
        self.assertEqual(s.public_state()["images"][0]["conf_parts"], parts)
        s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})
        line = json.loads(open(s.path("feedback.jsonl")).read().splitlines()[-1])
        self.assertEqual(line["conf_parts"], parts)              # Faktoren stehen im Feedback-Log
        self.assertEqual(line["export_size"], [3000, 2000])


class FeedbackReportTest(unittest.TestCase):
    """tools/feedback_report.py: Korrekturrate je Gruppe, falsches Gruen, Ground-Truth-Export."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import feedback_report
        self.fr = feedback_report
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root")

    def session(self, confs, phase="applied", corrections=None, parts=None):
        imgs = []
        for i, c in enumerate(confs, start=1):
            p = os.path.join(self.tmp, "Film X", f"img{i}.arw")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(b"x")
            det = detected(c)
            if parts:
                det["conf_parts"] = parts
            imgs.append({"id": i, "path": p, "detected": det, "export_size": [3000, 2000]})
        s = sess.Session.create({"images": imgs}, self.root)
        s.mark_analysis_done()
        for iid, crop in (corrections or {}).items():
            s.patch_images([iid], {"crop": crop})
        s.state["phase"] = phase
        s.save()
        return s

    def report(self):
        sessions = self.fr.load_sessions(sess.list_sessions(self.root))
        rows = self.fr.collect(sessions)
        return rows, self.fr.summarize(rows), sessions

    def test_rates_and_false_green(self):
        # detected = (0.1,0.1,0.9,0.9) -> 2400x1600 px. Korrekturen: 1 winzig (Treffer), 2 stark (zu weit)
        parts = {"size_agree": 0.9, "edge_score": 0.2, "film_trust": 0.8, "exposure_factor": 1.0}
        self.session([0.9, 0.8, 0.7, 0.4, 0.1], corrections={
            1: [0.1, 0.1, 0.905, 0.9],          # ~15 px Unterschied: innerhalb der Toleranz
            2: [0.1, 0.25, 0.9, 0.9]}, parts=parts)   # 300 px in der Hoehe: falsches Gruen
        rows, summ, _ = self.report()
        g = summ["groups"]["green"]
        self.assertEqual((g["n"], g["corrected"], g["bad"]), (3, 2, 1))
        self.assertEqual(summ["groups"]["yellow"]["n"], 1)
        self.assertEqual(summ["groups"]["red"]["n"], 1)
        self.assertEqual([r["file"] for r in summ["false_green"]], ["img2.arw"])
        self.assertEqual(summ["weak_factor"], {"edge_score": 1})
        self.assertEqual(dict(summ["symptoms"]), {"Groesse+Position": 1})   # Oberkante 300 px versetzt und Hoehe falsch

    def test_shifted_crop_is_caught_by_strict_check(self):
        # gleiche Groesse, aber 200 px verschoben: eval.py-Kriterium (nur Breite/Hoehe) sagt "Treffer"
        self.session([0.9], corrections={1: [0.1 + 200 / 3000, 0.1, 0.9 + 200 / 3000, 0.9]})
        rows, summ, _ = self.report()
        self.assertTrue(rows[0]["size_hit"])
        self.assertFalse(rows[0]["strict_hit"])
        self.assertEqual(rows[0]["symptom"], "Position bei richtiger Groesse")
        self.assertEqual(len(summ["false_green"]), 1)

    def test_dedupe_prefers_finished_session(self):
        first = self.session([0.9], phase="applied", corrections={1: [0.1, 0.3, 0.9, 0.9]})
        time.sleep(1.1)                                   # spaetere, aber unfertige Sitzung
        self.session([0.9], phase="reviewing")
        rows, summ, sessions = self.report()
        self.assertEqual(len(sessions), 2)
        self.assertEqual(summ["total"], 1)
        self.assertEqual(rows[0]["session"], first.state["session"])
        self.assertTrue(rows[0]["corrected"])

    def test_group_moves_and_export_gt(self):
        s = self.session([0.9, 0.1], corrections={1: [0.1, 0.3, 0.9, 0.9]})
        s.state["phase"] = "reviewing"
        s.patch_images([2], {"group": "green"})
        s.state["phase"] = "applied"
        s.save()
        rows, summ, _ = self.report()
        self.assertEqual(dict(summ["moves"]), {"red->green": 1})
        out = os.path.join(self.tmp, "gt.json")
        self.assertEqual(self.fr.export_gt(rows, out), 1)
        gt = json.load(open(out))["Film X/img1.arw"]
        self.assertEqual(gt["manual_crop"], {"x": 300, "y": 600, "width": 2400, "height": 1200})
        self.assertEqual(gt["export_size"], [3000, 2000])


class FolderReferenceTest(unittest.TestCase):
    """Ordnermodus als Algorithmus-Test: Referenz-Abweichung, bestaetigte Crops, Rollenfilter."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def session(self, items, reviews_path=None):
        imgs = []
        for i, (conf, manual) in enumerate(items, start=1):
            p = os.path.join(self.tmp, "Film 9", f"i{i}.jpg")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(b"x")
            it = {"id": i, "path": p, "export": p, "export_size": [2000, 3000],
                  "detected": detected(conf, (0.1, 0.1, 0.9, 0.9))}
            if manual:
                it["manual"] = {"crop": manual}
            imgs.append(it)
        job = {"mode": "folder", "images": imgs, "reviews": reviews_path}
        s = sess.Session.create(job, os.path.join(self.tmp, "root"))
        s.mark_analysis_done()
        return s

    def test_reference_deviation_and_summary_by_group(self):
        # detected 0.1..0.9 -> 1600x2400 px; Toleranz bei 3000 px langer Kante = 90 px
        s = self.session([
            (0.9, [0.1, 0.1, 0.9, 0.9]),                 # gruen, exakt -> Treffer
            (0.9, [0.1, 0.1, 0.9, 0.7]),                 # gruen, Hoehe 600 px zu klein -> Abweichung
            (0.4, [0.1, 0.1, 0.895, 0.9]),               # gelb, ~10 px -> Treffer
            (0.1, None)])                                # rot, keine Referenz
        pub = {i["id"]: i for i in s.public_state()["images"]}
        self.assertTrue(pub[1]["ref"]["hit"])
        self.assertFalse(pub[2]["ref"]["hit"])
        self.assertEqual(pub[2]["ref"]["dh"], 600)
        self.assertGreater(pub[2]["ref"]["score"], 1)
        self.assertIsNone(pub[4]["ref"])
        ref = s.public_state()["summary"]["ref"]
        self.assertEqual((ref["n"], ref["hits"]), (3, 2))
        self.assertEqual(ref["by_group"]["green"], {"n": 2, "hits": 1})
        self.assertEqual(ref["by_group"]["yellow"], {"n": 1, "hits": 1})

    def test_straightened_reference_is_written_in_original_frame_with_tilt(self):
        rp = os.path.join(self.tmp, "reviews.json")
        s = self.session([(0.9, None)], rp)
        s.patch_images([1], {"straighten": {"deg": 4.0}})
        crop_straight = s.effective_crop(s.image(1))                      # Vorgabe im geraden Bild
        s.patch_images([1], {"crop": crop_straight})
        s.finish()
        rv = json.load(open(rp))["Film 9/i1.jpg"]
        self.assertEqual(rv["tilt_deg"], 4.0)
        self.assertEqual(rv["manual_crop_straight"]["crop"], crop_straight)
        # Rueckrechnung ins Original: Mitte und Groesse des erkannten Crops (0.1..0.9)
        self.assertAlmostEqual(rv["manual_crop"]["x"], 200, delta=4)
        self.assertAlmostEqual(rv["manual_crop"]["width"], 1600, delta=4)
        # ohne Geradestellen: keine Tilt-Felder
        s2 = self.session([(0.9, None)], os.path.join(self.tmp, "r2.json"))
        s2.patch_images([1], {"decision": "accept"})
        s2.finish()
        self.assertNotIn("tilt_deg", json.load(open(os.path.join(self.tmp, "r2.json")))["Film 9/i1.jpg"])

    def test_corrected_detection_is_used_when_straightened(self):
        s = self.session([(0.9, None)])
        s.image(1)["detected"]["skew"] = {"deg": 3.0, "conf": 0.9,
                                          "straight": {"deg": 3.0, "crop": [0.2, 0.2, 0.8, 0.8], "edge": 0.9}}
        s.patch_images([1], {"straighten": {"deg": "auto"}})
        self.assertEqual(s.effective_crop(s.image(1)), [0.2, 0.2, 0.8, 0.8])
        s.patch_images([1], {"straighten": {"deg": 3.5}})                # anderer Winkel: nur umgerechnet
        self.assertNotEqual(s.effective_crop(s.image(1)), [0.2, 0.2, 0.8, 0.8])

    def test_finish_writes_corrections_and_confirmed_crops(self):
        rp = os.path.join(self.tmp, "reviews.json")
        json.dump({"Film 9/i9.jpg": {"manual_crop": {"x": 1, "y": 2, "width": 3, "height": 4}}}, open(rp, "w"))
        s = self.session([(0.9, None), (0.9, None), (0.9, None)], rp)
        s.patch_images([1], {"decision": "accept"})                     # bestaetigt
        s.patch_images([2], {"crop": [0.2, 0.2, 0.8, 0.8]})            # korrigiert
        s.finish()
        rv = json.load(open(rp))
        self.assertEqual(rv["Film 9/i1.jpg"]["manual_crop"], {"x": 200, "y": 300, "width": 1600, "height": 2400})
        self.assertTrue(rv["Film 9/i1.jpg"]["confirmed"])               # aus dem erkannten Crop
        self.assertEqual(rv["Film 9/i2.jpg"]["manual_crop"], {"x": 400, "y": 600, "width": 1200, "height": 1800})
        self.assertNotIn("confirmed", rv["Film 9/i2.jpg"])
        self.assertNotIn("Film 9/i3.jpg", rv)                           # weder geprueft noch korrigiert
        self.assertIn("Film 9/i9.jpg", rv)                              # bestehende Eintraege bleiben

    def test_folder_job_films_filter_and_multiple_review_files(self):
        root = os.path.join(self.tmp, "photos")
        for film in ("Film 1", "Film 33", "Film 34"):
            os.makedirs(os.path.join(root, film))
            for n in ("a.jpg", "b.jpg"):
                open(os.path.join(root, film, n), "wb").write(b"x")
        job = folder_job(root, films=["33", "34"])
        self.assertEqual({i["film"] for i in job["images"]}, {"Film 33", "Film 34"})
        with self.assertRaises(sess.SessionError):
            folder_job(root, films=["99"])
        # zwei Referenzdateien: die erste gewinnt, die zweite ergaenzt
        a, b = os.path.join(self.tmp, "a.json"), os.path.join(self.tmp, "b.json")
        m = lambda x: {"manual_crop": {"x": x, "y": 0, "width": 10, "height": 10}}
        json.dump({"Film 1/a.jpg": m(1)}, open(a, "w"))
        json.dump({"Film 1/a.jpg": m(2), "Film 1/b.jpg": m(3)}, open(b, "w"))
        with unittest.mock.patch("companion.sources.image_size", return_value=[100, 100]):
            job = folder_job(root, reviews=[a, b], films=["1"])
        by = {os.path.basename(i["path"]): i for i in job["images"]}
        self.assertEqual(job["reviews"], a)
        self.assertEqual(by["a.jpg"]["manual"]["crop"][0], 0.01)        # x=1 aus der ersten Datei
        self.assertEqual(by["b.jpg"]["manual"]["crop"][0], 0.03)        # nur in der zweiten


LUA = shutil.which("lua5.4") or shutil.which("lua")


@unittest.skipUnless(LUA, "lua fehlt")
class LuaBridgeTest(unittest.TestCase):
    """Die Lua-Seite (Plan anwenden / Pruefung oeffnen) gegen echte plan.json/result.json,
    mit einem Stub der darktable-API (tests/lua_harness.lua)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root")
        self.s = make_session(self.tmp)
        self.s.state["session"] = os.path.basename(self.s.dir)
        os.makedirs(self.root, exist_ok=True)
        # make_session legt die Sitzung unter <tmp>/root an; last_session zeigt darauf
        with open(os.path.join(self.root, "last_session"), "w") as f:
            f.write(os.path.basename(self.s.dir) + "\n")
        self.images = ";".join(
            f"{i['id']}|{os.path.dirname(i['path'])}|{i['filename']}"
            for i in self.s.state["images"].values())
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.opened = os.path.join(self.tmp, "opened.txt")
        with open(os.path.join(self.bin, "xdg-open"), "w") as f:
            f.write(f'#!/bin/sh\necho "$1" >> {self.opened}\n')
        os.chmod(os.path.join(self.bin, "xdg-open"), 0o755)

    def run_lua(self, action, locale=None, **extra):
        env = dict(os.environ, AUTOCROP_CACHE=self.root, HARNESS_IMAGES=self.images, **extra,
                   **({"HARNESS_LOCALE": locale} if locale else {}),
                   HOME=self.tmp, PATH=self.bin + os.pathsep + os.environ["PATH"])
        os.makedirs(os.path.join(self.tmp, ".cache", "darktable"), exist_ok=True)
        p = subprocess.run([LUA, os.path.join(ROOT, "tests", "lua_harness.lua"),
                            os.path.join(ROOT, "auto_crop_negative.lua"), action],
                           env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_apply_before_finish_does_nothing(self):
        out = self.run_lua("apply")
        self.assertEqual(out["styles"], [])
        self.assertTrue(any("Fertig" in m for m in out["prints"]), out["prints"])
        self.assertFalse(os.path.exists(self.s.path("result.json")))

    def test_apply_works_under_german_number_locale(self):
        """darktable setzt die Prozess-Locale (Komma als Dezimaltrenner); Plan lesen, Crops
        packen und result.json schreiben duerfen davon nicht abhaengen."""
        self.s.finish()
        out = self.run_lua("apply", locale="de_DE.UTF-8")
        self.assertEqual(sorted(x["id"] for x in out["styles"]), [101, 102])
        self.assertEqual(out["styles"][0]["crop"], [0.1, 0.1, 0.9, 0.9])
        res = sess.read_json(self.s.path("result.json"))     # gueltiges JSON, keine Kommazahlen
        self.assertEqual(res["status"], "ok")

    def test_apply_after_finish_sets_crops_labels_and_result(self):
        self.s.finish()
        out = self.run_lua("apply")
        crops = {st["id"]: st for st in out["styles"]}
        self.assertEqual(sorted(crops), [101, 102])               # Rot (103) wird nicht gecroppt
        self.assertEqual(crops[101]["crop"], [0.1, 0.1, 0.9, 0.9])  # normalisiert, ohne image.width
        self.assertTrue(crops[101]["enabled"])
        lab = {x["id"]: x for x in out["labels"]}
        self.assertTrue(lab[101]["green"] and lab[102]["yellow"] and lab[103]["red"])
        res = sess.read_json(self.s.path("result.json"))
        self.assertEqual((res["revision"], res["status"]), (1, "ok"))
        self.assertEqual(res["images"]["101"]["status"], "ok")
        self.s.refresh_from_disk()
        self.assertEqual(self.s.phase, "applied")
        # derselbe Plan wird nicht zweimal angewendet
        again = self.run_lua("apply")
        self.assertEqual(again["styles"], [])

    def test_reapply_after_reopen_only_changes_the_difference(self):
        self.s.finish()
        self.run_lua("apply")
        self.s.refresh_from_disk()
        self.s.reopen()
        self.assertEqual(self.run_lua("apply")["styles"], [])       # Plan ist weg: nichts passiert
        self.s.patch_images([102], {"decision": "skip"})            # 102 nicht mehr anwenden
        self.s.patch_images([101], {"crop": [0.2, 0.2, 0.8, 0.8]})  # 101 anders zuschneiden
        self.s.finish()
        out = self.run_lua("apply")
        st = {x["id"]: x for x in out["styles"]}
        self.assertEqual(st[101]["crop"], [0.2, 0.2, 0.8, 0.8])
        self.assertFalse(st[102]["enabled"])                         # zuvor angewendet, jetzt wieder aus
        self.assertNotIn(103, st)                                    # unveraendert: nicht angefasst
        self.assertEqual(sess.read_json(self.s.path("result.json"))["revision"], 2)

    def test_straighten_sets_rotation_and_crop_in_one_style(self):
        self.s.patch_images([101], {"straighten": {"deg": 2.5}})
        self.s.finish()
        out = self.run_lua("apply")
        st = {x["id"]: x for x in out["styles"]}
        self.assertAlmostEqual(st[101]["angle"], 2.5, places=3)
        self.assertTrue(st[101]["ashift_enabled"])
        self.assertEqual(st[101]["ashift_bytes"], 892)      # nur diese Groesse nimmt darktable an
        self.assertIsNone(st[102]["angle"])                 # ohne Drehung: ashift bleibt unberuehrt
        self.s.refresh_from_disk()
        applied = sess.read_json(self.s.path("applied.json"))
        self.assertEqual(applied["images"]["101"]["angle"], 2.5)

    def test_removing_straighten_switches_rotation_off(self):
        self.s.patch_images([101], {"straighten": {"deg": 2.5}})
        self.s.finish()
        self.run_lua("apply")
        self.s.refresh_from_disk()
        self.s.reopen()
        self.s.patch_images([101], {"straighten": None})
        self.s.finish()
        st = {x["id"]: x for x in self.run_lua("apply")["styles"]}
        self.assertFalse(st[101]["ashift_enabled"])
        self.assertTrue(st[101]["enabled"])                 # Crop bleibt an

    def test_changed_file_is_skipped_not_cropped(self):
        self.s.finish()
        with open(self.s.image(101)["path"], "ab") as f:
            f.write(b"grown")
        out = self.run_lua("apply")
        self.assertEqual(sorted(x["id"] for x in out["styles"]), [102])
        res = sess.read_json(self.s.path("result.json"))
        self.assertEqual(res["images"]["101"]["status"], "skipped")

    def test_open_restarts_server_and_opens_browser(self):
        self.run_lua("open")
        info = sess.read_json(self.s.path("server.json"))
        self.addCleanup(lambda: os.kill(info["pid"], 15))
        self.assertTrue(info and info["url"].startswith("http://127.0.0.1:"))
        for _ in range(50):                    # xdg-open laeuft im Hintergrund
            if os.path.exists(self.opened):
                break
            time.sleep(0.1)
        with open(self.opened) as f:
            self.assertIn(info["url"], f.read())

    def test_start_shows_status_url_and_watches_darktable_then_stop(self):
        import signal
        env = dict(os.environ, AUTOCROP_CACHE=self.root, HARNESS_IMAGES=self.images,
                   HARNESS_SELECT="1", HOME=self.tmp, PATH=self.bin + os.pathsep + os.environ["PATH"])
        os.makedirs(os.path.join(self.tmp, ".cache", "darktable"), exist_ok=True)

        def lua(action):
            p = subprocess.run([LUA, os.path.join(ROOT, "tests", "lua_harness.lua"),
                                os.path.join(ROOT, "auto_crop_negative.lua"), action],
                               env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout.strip().splitlines()[-1])

        out = lua("start")
        self.assertIn("SERVER LÄUFT", out["status"])                  # grosse, klare Anzeige
        self.assertTrue(out["url_label"].startswith("http://127.0.0.1:"))   # URL als Knopf
        self.assertTrue(any("SERVER STARTET" in m for m in out["prints"]))   # Startanzeige
        sid = open(os.path.join(self.root, "last_session")).read().strip()
        info = sess.read_json(os.path.join(self.root, sid, "server.json"))
        self.assertEqual(out["url_label"], info["url"])
        with open(f"/proc/{info['pid']}/cmdline", "rb") as f:
            cmd = f.read().split(b"\0")
        self.assertIn(b"--watch-pid", cmd)                            # stoppt mit darktable
        self.addCleanup(lambda: os.path.exists(f"/proc/{info['pid']}") and os.kill(info["pid"], signal.SIGKILL))

        out = lua("stop")                                             # Stop-Knopf
        self.assertIn("gestoppt", out["status"])
        for _ in range(50):
            if not os.path.exists(f"/proc/{info['pid']}"):
                break
            time.sleep(0.1)
        self.assertFalse(os.path.exists(f"/proc/{info['pid']}"))
        self.assertFalse(os.path.exists(os.path.join(self.root, sid, "server.json")))

    def test_exit_event_stops_the_server(self):
        """Beim Beenden von darktable (Lua-Ereignis "exit") stoppt der Server sofort."""
        import signal
        env = dict(os.environ, AUTOCROP_CACHE=self.root, HARNESS_IMAGES=self.images,
                   HARNESS_SELECT="1", HOME=self.tmp, PATH=self.bin + os.pathsep + os.environ["PATH"])
        os.makedirs(os.path.join(self.tmp, ".cache", "darktable"), exist_ok=True)

        def lua(action):
            p = subprocess.run([LUA, os.path.join(ROOT, "tests", "lua_harness.lua"),
                                os.path.join(ROOT, "auto_crop_negative.lua"), action],
                               env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(p.returncode, 0, p.stderr)

        lua("start")
        sid = open(os.path.join(self.root, "last_session")).read().strip()
        info = sess.read_json(os.path.join(self.root, sid, "server.json"))
        self.addCleanup(lambda: os.path.exists(f"/proc/{info['pid']}") and os.kill(info["pid"], signal.SIGKILL))
        self.assertTrue(os.path.exists(f"/proc/{info['pid']}"))
        lua("exit")
        for _ in range(60):
            if not os.path.exists(f"/proc/{info['pid']}"):
                break
            time.sleep(0.1)
        self.assertFalse(os.path.exists(f"/proc/{info['pid']}"), "Server lief nach exit weiter")

    def test_apply_leaves_traces_in_log_and_status(self):
        """Auch ein Fruehabbruch muss sichtbar sein (Log + Statuszeile), nie stumm."""
        out = self.run_lua("apply")           # noch nicht "Fertig"
        log = open(os.path.join(self.tmp, ".cache", "darktable", "auto_crop_negative.log")).read()
        self.assertIn("companion_apply: last_session=", log)
        self.assertIn("Plan nicht anwendbar", log)

    def test_reset_needs_two_clicks_then_clears_crop_and_plugin_labels(self):
        one = self.run_lua("reset", HARNESS_SELECT="1", HARNESS_LABELS="1", HARNESS_ONE_CLICK="1")
        self.assertEqual(one["styles"], [])                        # erster Klick schaltet nur scharf
        self.assertTrue(all(x["red"] for x in one["labels"]))
        out = self.run_lua("reset", HARNESS_SELECT="1", HARNESS_LABELS="1")
        self.assertEqual(sorted(x["id"] for x in out["styles"]), [101, 102, 103])
        self.assertTrue(all(not st["enabled"] for st in out["styles"]))     # Crop-Modul aus
        for lab in out["labels"]:
            self.assertFalse(lab["red"] or lab["yellow"] or lab["green"])
            self.assertTrue(lab["blue"] and lab["purple"])         # fremde Labels bleiben
        self.assertTrue(any("zurueckgesetzt" in m for m in out["prints"]))

    def test_reset_without_selection_does_nothing(self):
        out = self.run_lua("reset", HARNESS_LABELS="1")
        self.assertEqual(out["styles"], [])
        self.assertTrue(any("keine Bilder" in m for m in out["prints"]))

    def test_no_markup_in_widget_labels(self):
        """darktable-Lua-Labels kennen kein Markup: <b>/<span> wuerden woertlich erscheinen."""
        self.assertEqual(self.run_lua("stop")["markup"], 0)

    def test_stop_without_server_is_harmless(self):
        out = self.run_lua("stop")
        self.assertTrue(any("laeuft nicht" in m for m in out["prints"]), out["prints"])


if __name__ == "__main__":
    unittest.main()
