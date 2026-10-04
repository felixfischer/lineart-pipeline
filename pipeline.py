#!/usr/bin/env python3
"""CLI entry point: python pipeline.py -i source/ -o output/ -d medium"""

import sys

from lineart.cli import main

sys.exit(main())
