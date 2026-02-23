#!/usr/bin/env python3
"""
Launcher for the agent-trace file viewer.
Run from the viewer install dir (e.g. ~/.agent-trace/viewer).
Usage: python run_viewer.py [--project /path/to/project]
"""
from __future__ import annotations

import os
import sys

# Ensure we run from the directory containing backend/
VIEWER_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(VIEWER_ROOT)
if VIEWER_ROOT not in sys.path:
    sys.path.insert(0, VIEWER_ROOT)

from backend.main import main

if __name__ == "__main__":
    main()
