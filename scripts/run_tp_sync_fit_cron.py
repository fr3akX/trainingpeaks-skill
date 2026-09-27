#!/usr/bin/env python3
"""Portable FIT-sync cron wrapper.

Resolve tp.py relative to this file. Relative output paths are resolved from the
caller's working directory; pass --output-dir with an absolute path in cron.
Additional command-line arguments override the default sync options.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    tp_path = Path(__file__).resolve().with_name("tp.py")
    command = [
        sys.executable, str(tp_path), "sync-fit",
        "--days", "3", "--output-dir", "fit_exports", *sys.argv[1:],
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
