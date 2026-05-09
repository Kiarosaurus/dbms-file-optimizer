#!/usr/bin/env python3
"""
docs/generate_report.py — redirecciona a engine/report_generator.py

Si corres engine/report_generator.py directamente, ya no
sobreescribes FINAL_REPORT.md con la versión más nueva.
"""
from __future__ import annotations

import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.report_generator import generate_report

if __name__ == "__main__":
    generate_report()
