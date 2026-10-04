#!/usr/bin/env python3
"""Entry point: ``python pipeline.py -i source/ -o output/``."""
import sys

from lineart.cli import main

if __name__ == "__main__":
    sys.exit(main())
