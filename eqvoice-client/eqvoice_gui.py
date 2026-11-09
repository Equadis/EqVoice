"""PyInstaller entry point for EqVoice.exe (a windowed program: no console). `python eqvoice_gui.py` behaves the same."""
import sys

from eqvoice_client.launcher import main

if __name__ == "__main__":
    sys.exit(main())
