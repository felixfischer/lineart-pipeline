#!/usr/bin/env python3
"""Entry point: ``python gui.py [bild]`` starts the interactive web GUI."""
import sys

from lineart.gui.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
