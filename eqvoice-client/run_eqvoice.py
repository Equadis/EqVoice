"""PyInstaller entry point (the package's own __main__ uses relative imports)."""
import multiprocessing
import sys

from eqvoice_client.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
