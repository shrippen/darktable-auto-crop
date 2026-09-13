#!/usr/bin/env python3
"""Startet auto_crop_negative.py (gleiches Verzeichnis) mit der Venv-Python.

Das Darktable-Lua-Plugin ruft diesen Wrapper mit denselben Argumenten auf
wie das Skript selbst. Vorteil: cv2/numpy kommen aus der Venv, ohne dass
das Plugin den Interpreter kennen muss.
"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
script = os.path.join(here, "auto_crop_negative.py")
venv_python = os.path.expanduser("~/.config/darktable/lua/.venv/bin/python")

if os.path.exists(venv_python):
    os.execvp(venv_python, [venv_python, script] + sys.argv[1:])
os.execvp(sys.executable, [sys.executable, script] + sys.argv[1:])
