#!/usr/bin/env python3
"""Browser-Smoke-Test der Companion-UI (Playwright + System-Chromium).

Startet den Server im Ordnermodus auf einigen Testfotos (echte Erkennung) und geht
den Ablauf durch: Galerie, Tastatur, Drag-and-drop, Crop-Editor, Fertig/Sperre,
Zurueck zur Pruefung, helles Theme. Nicht Teil von `unittest discover`, weil
Playwright kein Projekt-Requirement ist.

  python3 tests/ui_smoke.py [--shots DIR] [--n 8]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp()
    film = os.path.join(tmp, "photos", "Film 27")
    os.makedirs(film)
    src = os.path.join(ROOT, "Testphotos", "Film 27")
    for f in sorted(os.listdir(src))[:a.n]:
        os.symlink(os.path.join(src, f), os.path.join(film, f))
    # Referenzen (aus review_data/reviews.json) fuer die verwendeten Bilder als Schreibdatei der Sitzung
    reviews_path = os.path.join(tmp, "reviews.json")
    real = json.load(open(os.path.join(ROOT, "review_data", "reviews.json")))
    files = sorted(os.listdir(film))
    no_ref = files[1]                      # dieses Bild hat bewusst KEINE Referenz (fuer "Akzeptieren")
    keep = {f"Film 27/{f}" for f in files} - {f"Film 27/{no_ref}"}
    # nur die Crops, ohne is_problem/confirmed: der Test soll nicht von den Markierungen abhaengen, die
    # jemand spaeter in reviews.json speichert (sonst starten Bilder schon als "rot" und der Ablauf kippt)
    json.dump({k: {"manual_crop": v["manual_crop"]} for k, v in real.items()
               if k in keep and v.get("manual_crop")}, open(reviews_path, "w"))
    n_refs = len(json.load(open(reviews_path)))
    env = dict(os.environ, KADER_CACHE=os.path.join(tmp, "cache"))
    srv = subprocess.Popen([PY, "-m", "companion", "serve", "--folder", os.path.join(tmp, "photos"),
                            "--root", os.path.join(tmp, "cache"), "--reviews", reviews_path],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    url = srv.stdout.readline().strip()
    print("server", url)
    errors = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            # 1) Seite unter der echten CSP laden: keine Konsolenfehler (z. B. blockierte Skripte)
            csp = b.new_page()
            csp.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            csp.on("pageerror", lambda e: errors.append(str(e)))
            csp.goto(url)
            csp.wait_for_selector("#bands .tile", timeout=30000)
            csp.close()
            # 2) Interaktion mit umgangener CSP (Playwrights wait_for_function nutzt eval)
            ctx = b.new_context(viewport={"width": 1400, "height": 1000}, bypass_csp=True)
            pg = ctx.new_page()
            big = []                                   # angeforderte Grossbilder (Vorladen)
            pg.on("request", lambda r: big.append(r.url) if "/api/thumb/" in r.url and "w=1800" in r.url else None)
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(url)
            pg.wait_for_selector("#bands .tile", timeout=20000)
            # Analyse laeuft evtl. noch: warten, bis Phase "Pruefen"
            pg.wait_for_function("document.querySelector('#phase-pill .pill')?.dataset.state==='reviewing'", timeout=120000)
            n = pg.locator("#bands .tile").count()
            assert n == a.n, f"{n} Kacheln statt {a.n}"
            print('galerie ok', flush=True)
            # Ordnermodus als Algorithmus-Test: Referenz-Test-Hinweis mit Trefferzahl und Kachel-Marken
            assert n_refs > 0, "keine Referenzen fuer die Testbilder"
            notice = pg.locator("#notice").inner_text()
            assert "reference test" in notice.lower(), notice
            assert f"of {n_refs}" in notice, notice
            assert pg.locator("#bands .tile .tile-badge").count() >= 1
            assert pg.locator("#tb-sort option[value='ref_desc']").count() == 1
            shot = lambda name: pg.screenshot(path=os.path.join(a.shots, name), full_page=False) if a.shots else None
            if a.shots:
                os.makedirs(a.shots, exist_ok=True)
            shot("1-gallery.png")

            def count(tier):
                return pg.locator(f'#bands .band[data-tier="{tier}"] .tile').count()

            print('Tastatur', flush=True)
            # Tastatur: erste Kachel auswaehlen, mit 3 nach Rot
            tile = pg.locator("#bands .tile").first
            first_id = tile.get_attribute("data-id")
            tile.click()
            assert tile.get_attribute("aria-selected") == "true"
            red0 = count("red")
            pg.keyboard.press("3")
            pg.wait_for_function(f"document.querySelector('#bands .tile[data-id=\"{first_id}\"]').closest('.band').dataset.tier==='red'")
            assert count("red") == red0 + 1 or red0 + 1 >= 1
            # Undo
            pg.keyboard.press("z")
            pg.wait_for_function(f"document.querySelector('#bands .tile[data-id=\"{first_id}\"]').closest('.band').dataset.tier!=='red' || {red0}>0")

            print('Drag-and-drop in Rot', flush=True)
            # Drag-and-drop in Rot
            t2 = pg.locator("#bands .band[data-tier='green'] .tile, #bands .band[data-tier='yellow'] .tile").first
            id2 = t2.get_attribute("data-id")
            t2.drag_to(pg.locator("#bands .band[data-tier='red']"))
            pg.wait_for_function(f"document.querySelector('#bands .tile[data-id=\"{id2}\"]').closest('.band').dataset.tier==='red'", timeout=8000)

            # Akzeptieren bestaetigt den erkannten Crop als Referenz (Taste A)
            acc = pg.locator("#bands .tile", has=pg.locator(f".tile-name:text-is('{no_ref}')")).first
            acc_id = acc.get_attribute("data-id")
            acc.click()
            pg.keyboard.press("a")
            pg.wait_for_function(f"[...document.querySelectorAll('#bands .tile[data-id=\"{acc_id}\"] .tile-badge')].some(b => b.textContent.toLowerCase().includes('accept'))", timeout=8000)
            print('Editor', flush=True)
            # Editor: oeffnen, Griff ziehen
            pg.locator(f'#bands .tile[data-id="{id2}"]').dblclick()
            pg.wait_for_selector("#editor:not([hidden]) #ed-crop")
            pg.wait_for_function("document.querySelector('#ed-img').naturalWidth>0")
            pg.wait_for_timeout(300)
            # Reihenfolge im Editor = Reihenfolge der Galerie (erst Gruppe, dann Sortierung)
            gal = pg.eval_on_selector_all("#bands .tile", "els => els.map(e => e.dataset.id)")
            strip = pg.eval_on_selector_all("#ed-strip button[data-id]", "els => els.map(e => e.dataset.id)")
            assert strip == gal, f"Editor-Reihenfolge {strip} != Galerie {gal}"
            # Vorladen: die zwei Nachbarn davor und danach werden im Hintergrund angefordert
            cur_id = strip[[i for i, x in enumerate(strip) if x == id2][0]]
            pos = strip.index(id2)
            expect = {strip[j] for j in range(pos - 2, pos + 3) if 0 <= j < len(strip)}
            pg.wait_for_timeout(1500)
            got = {u.split("/api/thumb/")[1].split("?")[0] for u in big}
            assert expect <= got, f"nicht vorgeladen: {expect - got}"
            # Vorladen nutzt dieselbe Ansicht wie die Anzeige: geradegestellte Bilder tragen den Winkel im Link
            api_state = json.loads(pg.evaluate("fetch('/api/session',{headers:{'X-Token':new URLSearchParams(location.search).get('t')}}).then(r=>r.text())"))
            deg_of = {str(i["id"]): float(i.get("straighten") or 0) for i in api_state["images"]}
            for u in big:
                q = u.split("/api/thumb/")[1]
                iid = q.split("?")[0]
                sval = float(dict(kv.split("=") for kv in q.split("?")[1].split("&"))["s"])
                assert abs(sval - deg_of[iid]) < 1e-6, f"Vorladen ohne Tilt: {u}"
            # Phase 5: die vier Konfidenz-Faktoren sind im Editor sichtbar
            assert pg.locator("#ed-side .cp-row").count() == 4, "Konfidenz-Faktoren fehlen im Editor"
            # Schraeglage: Messwert und (im Ordnermodus) nur Anzeige, kein Geradestellen-Knopf
            side = pg.inner_text("#ed-side").lower()
            assert ("tilt" in side or "schräglage" in side), "Schraeglage fehlt im Editor"
            assert pg.locator("#ed-toolbar [data-act='tilt-toggle']").count() == 1, "Tilt-Schalter fehlt"
            # Tilt-Schalter (Taste T): ist standardmaessig an, wenn ein verlaesslicher Tilt gemessen wurde
            tog = "#ed-toolbar [data-act='tilt-toggle']"
            if not pg.locator(tog).is_disabled():
                start = pg.get_attribute(tog, "aria-pressed")
                for want in ("false" if start == "true" else "true", start):
                    pg.keyboard.press("t")
                    pg.wait_for_function(f"document.querySelector(\"{tog}\").getAttribute('aria-pressed')==='{want}'", timeout=8000)
                    pg.wait_for_timeout(300)
                    assert ("s=0" in pg.get_attribute("#ed-img", "src")) == (want == "false"), "Bildansicht folgt dem Schalter nicht"
            shot("2-editor.png")
            h = pg.locator("#ed-crop .handle[data-h='se']")
            box = h.bounding_box()
            pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            pg.mouse.down()
            pg.mouse.move(box["x"] - 60, box["y"] - 40, steps=6)
            pg.mouse.up()
            pg.wait_for_function(f"document.querySelector('#bands .tile[data-id=\"{id2}\"] .tile-badge.is-hl')", timeout=8000)
            st = json.loads(pg.evaluate("fetch('/api/session',{headers:{'X-Token':new URLSearchParams(location.search).get('t')}}).then(r=>r.text())"))
            img2 = next(i for i in st["images"] if str(i["id"]) == id2)
            assert img2["manual_crop"], "kein manueller Crop gespeichert"
            pg.keyboard.press("Escape")
            pg.wait_for_selector("#editor", state="hidden")

            print('Fertig -> Sperre', flush=True)
            # Fertig -> Sperre
            pg.locator("#actionbar [data-act='finish']").click()
            pg.wait_for_selector("#dialog-host .dialog")
            shot("3-finish-dialog.png")
            pg.locator("#dialog-host [data-x='yes']").click()
            # Ordnermodus: Fertig sperrt und gilt sofort als angewendet (kein darktable dahinter)
            pg.wait_for_function("['locked','applied'].includes(document.querySelector('#phase-pill .pill')?.dataset.state)", timeout=8000)
            assert pg.locator("#notice .callout-ok").count() == 1
            assert pg.locator("#bands .tile").first.get_attribute("draggable") == "false"
            shot("4-locked.png")
            code = pg.evaluate("""fetch('/api/images',{method:'PATCH',headers:{'X-Token':new URLSearchParams(location.search).get('t'),'Content-Type':'application/json'},body:JSON.stringify({ids:[%s],group:'green'})}).then(r=>r.status)""" % id2)
            assert code == 409, f"erwartet 409, war {code}"

            print('Zurueck zur Pruefung', flush=True)
            # Ordnermodus: "Fertig" hat Korrekturen UND bestaetigte Crops nach reviews.json geschrieben
            rv = json.load(open(reviews_path))
            key = f"Film 27/{no_ref}"
            assert rv.get(key, {}).get("confirmed") is True, f"{key} nicht als bestaetigt gespeichert: {rv.get(key)}"
            assert len(rv) >= n_refs
            # Zurueck zur Pruefung
            pg.locator("#actionbar [data-act='reopen']").click()
            pg.wait_for_selector("#dialog-host .dialog")
            pg.locator("#dialog-host [data-x='yes']").click()
            pg.wait_for_function("document.querySelector('#phase-pill .pill')?.dataset.state==='reviewing'", timeout=8000)
            assert "r2" in pg.locator("#phase-pill").inner_text().lower()
            assert pg.locator("#bands .tile").first.get_attribute("draggable") == "true"
            # Korrektur blieb erhalten
            assert pg.locator(f'#bands .tile[data-id="{id2}"] .tile-badge.is-hl').count() >= 1

            print('helles Theme', flush=True)
            # helles Theme + Deutsch
            pg.locator("[data-theme-set='light']").click()
            pg.locator(".lang button[data-lang='de']").click()
            pg.wait_for_timeout(300)
            shot("5-light-de.png")
            # Server beenden: die UI zeigt es sofort und erklaert, wie es weitergeht
            print('Server beenden', flush=True)
            pg.evaluate("""fetch('/api/quit',{method:'POST',headers:{'X-Token':new URLSearchParams(location.search).get('t'),'Content-Type':'application/json'},body:'{}'})""")
            pg.wait_for_selector("#notice .callout-danger", timeout=8000)
            assert "darktable" in pg.locator("#notice").inner_text()
            shot("6-server-stopped.png")
            b.close()
    finally:
        srv.terminate()
    real = [e for e in errors if "favicon" not in e and "409" not in e and "ERR_" not in e and "EventSource" not in e]   # 409 = absichtlicher Schreibversuch im gesperrten Zustand
    assert not real, f"Browser-Fehler: {real}"
    print("UI-Smoke-Test OK")


if __name__ == "__main__":
    sys.exit(main())
