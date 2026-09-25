#!/usr/bin/env python3
"""Startet kader.py (gleiches Verzeichnis) mit der Venv-Python.

Das Darktable-Lua-Plugin ruft diesen Wrapper mit denselben Argumenten auf
wie das Skript selbst. Vorteil: cv2/numpy kommen aus der Venv, ohne dass
das Plugin den Interpreter kennen muss.

``kader_wrapper.py companion ...`` startet stattdessen die
Companion-UI (``python -m companion ...``).
"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.expanduser("~/.config/darktable/lua/.venv/bin/python")
python = venv_python if os.path.exists(venv_python) else sys.executable

if sys.argv[1:2] == ["companion"]:
    os.chdir(here)
    env = dict(os.environ, PYTHONPATH=here + os.pathsep + os.environ.get("PYTHONPATH", ""))
    os.execvpe(python, [python, "-m", "companion"] + sys.argv[2:], env)

script = os.path.join(here, "kader.py")
os.execvp(python, [python, script] + sys.argv[1:])
