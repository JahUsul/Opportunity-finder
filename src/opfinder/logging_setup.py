"""Logging setup per design doc §8.

Rotating file handler at data/logs/YYYY-MM-DD.log, INFO by default,
DEBUG when OPFINDER_DEBUG=1. Errors also stream to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{date.today().isoformat()}.log"
    level = logging.DEBUG if os.getenv("OPFINDER_DEBUG") == "1" else logging.INFO
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=8)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    return logging.getLogger("opfinder")
