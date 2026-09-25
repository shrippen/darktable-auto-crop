"""RawTherapee-Profile (.pp3) zeilenweise aendern, ohne den Rest anzufassen.

Ein .pp3 ist INI-artig (``[Abschnitt]``, ``Schluessel=Wert``). ``configparser`` passt nicht:
es wuerde Gross-/Kleinschreibung der Schluessel und Kommentare verlieren. Hier werden nur
die genannten Schluessel ersetzt oder angehaengt; alles andere bleibt Byte fuer Byte.
"""
import re

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def sidecar_path(image_path):
    """RawTherapee legt das Profil neben das Bild: ``IMG_0001.NEF.pp3``."""
    return image_path + ".pp3"


def get_value(text, section, key):
    current = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1)
            continue
        if current != section or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return None


def set_values(text, section, values):
    """Setzt ``values`` ({schluessel: wert}) in ``[section]``; fehlender Abschnitt wird angehaengt."""
    lines = text.splitlines()
    pending = dict(values)
    out, current, section_end = [], None, None

    for line in lines:
        m = _SECTION.match(line)
        if m:
            if current == section:
                section_end = len(out)
            current = m.group(1)
            out.append(line)
            continue
        if current == section and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in pending:
                out.append(f"{k}={_fmt(pending.pop(k))}")
                continue
        out.append(line)
    if current == section:
        section_end = len(out)

    # Rest einfuegen: ans Ende des vorhandenen Abschnitts, sonst neuer Abschnitt am Dateiende
    rest = [f"{k}={_fmt(v)}" for k, v in pending.items()]
    if section_end is not None:
        while section_end > 0 and not out[section_end - 1].strip():
            section_end -= 1
        out[section_end:section_end] = rest
    elif rest:
        if out and out[-1].strip():
            out.append("")
        out += [f"[{section}]"] + rest
    return "\n".join(out) + "\n"


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
