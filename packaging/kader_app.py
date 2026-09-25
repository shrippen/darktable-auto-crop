"""Einstiegsskript fuer PyInstaller (siehe kader.spec)."""
import multiprocessing
import sys

if __name__ == "__main__":
    # Muss vor allem anderen laufen: die Erkennung nutzt einen Prozesspool, und jeder Worker startet
    # in der gebuendelten Datei dieselbe exe - ohne freeze_support() wuerde er den Launcher erneut
    # ausfuehren (Ordner-Dialog je Worker).
    multiprocessing.freeze_support()
    from companion.launcher import main
    sys.exit(main())
