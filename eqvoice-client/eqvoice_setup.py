"""PyInstaller entry point for EqVoice-Test-Setup.exe: the one-click installer (a windowed program). The program it installs is eqvoice_gui.py."""
import sys

from eqvoice_client.setup_app import run_setup

if __name__ == "__main__":
    sys.exit(run_setup(sys.argv[1:]))
