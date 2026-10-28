"""Entry point: python deadlock_mm.py [command]  (no command = interactive menu)."""

import sys

from dlmm.cli import main

if __name__ == '__main__':
    sys.exit(main())
