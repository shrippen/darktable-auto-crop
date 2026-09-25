"""Minimales Terminal-Interface fuer die eigenstaendige Nutzung (``--tui``).

Kein Ersatz fuer die Web-UI (keine Bedienung, kein Editor): zeigt nur, dass der Server laeuft,
den Fortschritt und die URL, mit zwei Tasten (Browser oeffnen, Server beenden). Gedacht fuer
NAS/SSH-Sitzungen, wo sonst nur die einmal ausgegebene URL zu sehen waere, ohne Ruecklauf.

Laeuft im selben Prozess wie der Server (kein HTTP-Umweg): liest ``app.session``/``app.analyzer``
direkt. Optionale Abhaengigkeit ``rich`` (Extra ``[tui]``); ohne sie oder ohne POSIX-Terminal
(``termios``/``tty``, kein Windows) meldet :func:`unavailable_reason` das und die CLI faellt
auf die bisherige Textausgabe zurueck.
"""
import os
import queue
import select
import sys
import threading
import time
import webbrowser

from .session import read_json

POLL_S = 0.25
OPEN_MSG_S = 3.0

PHASE_STYLE = {"analyzing": "yellow", "reviewing": "yellow", "locked": "cyan",
              "applied": "green", "apply_failed": "red"}
PHASE_LABEL = {"analyzing": "Analysiere", "reviewing": "Pruefen", "locked": "Gesperrt",
              "applied": "Angewendet", "apply_failed": "Fehler"}


def unavailable_reason():
    """None wenn ``run()`` funktioniert, sonst ein Text fuer die Fehlermeldung der CLI."""
    try:
        import rich  # noqa: F401
    except ImportError:
        return "Paket 'rich' fehlt (pip install \"auto-crop-negative[tui]\" oder: pip install rich)"
    try:
        import termios, tty  # noqa: F401
    except ImportError:
        return "die TUI braucht ein POSIX-Terminal (unter Windows nicht verfuegbar)"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return "kein interaktives Terminal (stdin/stdout sind keine TTY)"
    return None


def _renderable(app, message=None):
    from rich.align import Align
    from rich.console import Group
    from rich.panel import Panel
    from rich.progress_bar import ProgressBar
    from rich.table import Table
    from rich.text import Text

    st = app.session.public_state()
    prog = app.analyzer.progress
    n = st["summary"]
    phase = st["phase"]

    head = Table.grid(padding=(0, 2), expand=True)
    head.add_column(); head.add_column(justify="right")
    head.add_row(Text(st["session"], style="dim"),
                Text(PHASE_LABEL.get(phase, phase), style=f"bold {PHASE_STYLE.get(phase, 'white')}"))
    tgt = st["target"]
    tgt_line = f"Ziel: {tgt['name']}" + (f"  ->  {tgt['out']}" if tgt.get("out") else "")
    head.add_row(Text(tgt_line, style="dim", overflow="ellipsis", no_wrap=True), "")

    counts = Table.grid(padding=(0, 2))
    counts.add_row(Text(f"{n['green']} gruen", style="green"), Text(f"{n['yellow']} gelb", style="yellow"),
                  Text(f"{n['red']} rot", style="red"), Text(f"{n['apply']} anwenden", style="bold"),
                  Text(f"{n['total']} gesamt", style="dim"))

    body = [head, counts]
    if prog.get("busy"):
        total = prog.get("total") or 1
        stage = {"export": "RAW-Export", "detect": "Erkennung", "skew": "Schraeglage"}.get(prog.get("stage"), prog.get("stage") or "")
        body.append(Text(f"{stage}: {prog.get('done', 0)}/{prog.get('total', 0)}"))
        body.append(ProgressBar(total=total, completed=prog.get("done", 0)))
    elif phase in ("applied", "apply_failed"):
        res = read_json(app.session.path("result.json"), {}) or {}
        imgs = list((res.get("images") or {}).values())
        cnt = lambda k: sum(1 for i in imgs if i.get("status") == k)   # noqa: E731
        if res.get("message"):
            body.append(Text(res["message"], style="red"))
        if imgs:
            body.append(Text(f"OK: {cnt('ok')}  Uebersprungen: {cnt('skipped')}  Fehler: {cnt('error')}"))
    else:
        body.append(Text("Bereit.", style="dim"))

    body.append(Panel(Align.center(Text(app.url, style="bold underline cyan")),
                      title="In einem Browser oeffnen", border_style="cyan", padding=(0, 1)))
    if not app.loopback_only:
        # Steht dauerhaft hier, nicht nur einmal beim Start: der Alt-Screen der TUI verdeckt jede
        # Ausgabe von davor, solange sie laeuft - genau dann muss der Hinweis sichtbar bleiben.
        body.append(Panel(Text(f"Im Netzwerk erreichbar ({app.bind}). Nur der Token oben "
                               "schuetzt den Zugriff - nicht in unsicheren Netzen freigeben.",
                               style="bold red"), border_style="red", padding=(0, 1)))
    if message:
        body.append(Text(message, style="italic yellow"))
    body.append(Text("O  Browser oeffnen      Q  Server beenden", style="dim"))

    return Panel(Group(*body), title="[bold] Auto Crop Negative [/]", border_style="yellow", padding=(1, 2))


def _key_reader(fd, out_q, stop_evt):
    """Liest einzelne Tasten ohne Enter (cbreak) in einem eigenen Thread; ``select`` mit Timeout,
    damit der Thread bei ``stop_evt`` zeitnah endet statt in ``os.read`` haengen zu bleiben."""
    while not stop_evt.is_set():
        try:
            ready, _, _ = select.select([fd], [], [], 0.3)
        except (OSError, ValueError):
            return
        if ready:
            ch = os.read(fd, 1)
            if ch:
                out_q.put(ch.decode(errors="ignore").lower())


def run(app):
    """Blockiert, bis der Server endet (Taste Q, Leerlauf, Elternprozess weg, Signal)."""
    import termios
    import tty
    from rich.console import Console
    from rich.live import Live

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    key_q = queue.Queue()
    stop_evt = threading.Event()
    reader = threading.Thread(target=_key_reader, args=(fd, key_q, stop_evt), daemon=True)
    message, message_until = None, 0.0

    tty.setcbreak(fd)
    reader.start()
    try:
        console = Console()
        # kein transient=True: die letzte Meldung (z. B. "Beendet.") soll noch kurz zu sehen sein,
        # bevor der Alt-Screen-Wechsel beim Verlassen von Live sie ohnehin verschwinden laesst
        with Live(_renderable(app), console=console, screen=True, auto_refresh=False) as live:
            while app.stop_reason is None:
                try:
                    key = key_q.get(timeout=POLL_S)
                except queue.Empty:
                    key = None
                if key == "q":
                    app.stop("quit")
                elif key == "o":
                    message = ("Browser geoeffnet." if webbrowser.open(app.url)
                              else "Kein Browser hier gefunden - URL oben in einem anderen Geraet oeffnen.")
                    message_until = time.time() + OPEN_MSG_S
                if message and time.time() > message_until:
                    message = None
                live.update(_renderable(app, message), refresh=True)
            live.update(_renderable(app, {
                "quit": "Beendet.", "idle": "Wegen Leerlauf beendet.",
                "parent": "Elternprozess beendet, Server stoppt.", "signal": "Beendet.",
            }.get(app.stop_reason, "Beendet.")), refresh=True)
            time.sleep(0.6)
    finally:
        stop_evt.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
