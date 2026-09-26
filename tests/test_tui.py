"""Tests der Terminal-Statusanzeige (--tui).

Ausfuehren:  .venv/bin/python -m unittest discover -s tests -v
"""
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

try:
    import pty
except ImportError:          # Windows: kein termios
    pty = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from companion import server, session as sess, tui               # noqa: E402
from companion.server import make_server                          # noqa: E402

try:
    import rich  # noqa: F401
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

TEST_JPG = os.path.join(ROOT, "test.jpg")
needs_rich = unittest.skipUnless(HAS_RICH, "rich fehlt")


def make_session(tmp, target="copies"):
    from PIL import Image
    p = os.path.join(tmp, "Film A", "a.jpg")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Image.new("RGB", (100, 100), "white").save(p)
    job = {"target": target, "images": [{"id": 1, "path": p, "export": p, "export_size": [100, 100],
                                         "detected": {"crop": [0.1, 0.1, 0.9, 0.9], "confidence": 0.9,
                                                     "method": "test", "reasons": []}}],
          "folder": tmp, "out": os.path.join(tmp, "autocrop")}
    s = sess.Session.create(job, os.path.join(tmp, "root"))
    s.mark_analysis_done()
    return s


class UnavailableReasonTest(unittest.TestCase):
    def test_missing_rich_is_reported(self):
        with unittest.mock.patch.dict(sys.modules, {"rich": None}):
            self.assertIn("rich", tui.unavailable_reason())

    @needs_rich
    def test_non_tty_is_reported(self):
        # unittest laeuft ohne TTY an stdin/stdout
        reason = tui.unavailable_reason()
        self.assertIsNotNone(reason)
        self.assertIn("Terminal", reason)


@needs_rich
class RenderableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = make_session(self.tmp)
        self.app = make_server(self.s)
        self.addCleanup(self.app.httpd.server_close)

    def text(self, message=None):
        from rich.console import Console
        con = Console(record=True, width=100, color_system=None)
        con.print(tui._renderable(self.app, message))
        return con.export_text()

    def test_shows_session_target_url_and_counts(self):
        out = self.text()
        self.assertIn(self.s.state["session"], out)
        self.assertIn("copies", out)
        self.assertIn(self.app.url, out)
        self.assertIn("1 gruen", out)
        self.assertIn("1 gesamt", out)
        self.assertIn("Server beenden", out)

    def test_shows_progress_while_busy(self):
        self.app.analyzer.progress.update(busy=True, stage="export", done=1, total=4)
        self.assertIn("RAW-Export: 1/4", self.text())

    def test_shows_result_after_apply(self):
        self.s.finish()
        self.assertEqual(self.s.phase, "applied")
        self.assertIn("OK: 1", self.text())

    def test_shows_transient_message(self):
        self.assertIn("Kein Browser", self.text("Kein Browser hier gefunden - URL oben in einem anderen Geraet oeffnen."))


class CliWiringTest(unittest.TestCase):
    def test_tui_flag_only_exists_on_open(self):
        from companion.__main__ import main
        with self.assertRaises(SystemExit):
            main(["serve", "--tui", "--folder", "/tmp"])

    def test_open_fails_fast_when_tui_unavailable(self):
        from companion.__main__ import main
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with unittest.mock.patch("companion.__main__.tui.unavailable_reason", return_value="kein Terminal"):
            code = main(["open", tmp, "--tui", "--root", os.path.join(tmp, "root")])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(tmp, "root")))   # keine Sitzung angelegt

    def test_check_reports_tui_availability(self):
        import io
        import contextlib
        from companion.__main__ import main
        buf = io.StringIO()
        with unittest.mock.patch("companion.__main__.tui.unavailable_reason", return_value=None), \
                contextlib.redirect_stdout(buf):
            main(["check"])
        self.assertIn("Terminal-Statusanzeige (--tui): ja", buf.getvalue())
        buf2 = io.StringIO()
        with unittest.mock.patch("companion.__main__.tui.unavailable_reason", return_value="testgrund"), \
                contextlib.redirect_stdout(buf2):
            main(["check"])
        self.assertIn("nein (testgrund)", buf2.getvalue())


@needs_rich
@unittest.skipUnless(pty is not None and hasattr(pty, "openpty"), "kein pty (kein POSIX)")
class PtySmokeTest(unittest.TestCase):
    """Echter Durchlauf in einem Pseudo-Terminal: startet --tui, drueckt Q, prueft das Ende."""

    def test_tui_starts_and_quits_on_q(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        film = os.path.join(tmp, "scans", "Film 1")
        os.makedirs(film)
        shutil.copy(TEST_JPG, os.path.join(film, "scan.jpg"))
        root = os.path.join(tmp, "root")

        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-m", "companion", "open", os.path.join(tmp, "scans"),
             "--target", "copies", "--root", root, "--tui", "--idle-minutes", "5"],
            cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, env=dict(os.environ, TERM="xterm"))
        os.close(slave)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())

        out = b""
        try:
            out += self._read_until(master, b"Kader", timeout=20)
            time.sleep(0.5)                                   # Live einmal rendern lassen
            os.write(master, b"q")
            out += self._read_until(master, b"", timeout=10, until_eof=True)
        finally:
            os.close(master)
        code = proc.wait(timeout=10)

        text = out.decode(errors="ignore")
        self.assertIn("Kader", text)
        self.assertIn("Beendet", text)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(_only_session_dir(root), "server.json")))

    @staticmethod
    def _read_until(fd, needle, timeout, until_eof=False):
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if not until_eof and needle in buf:
                break
        return buf


def _only_session_dir(root):
    return os.path.join(root, os.listdir(root)[0])


if __name__ == "__main__":
    unittest.main()
