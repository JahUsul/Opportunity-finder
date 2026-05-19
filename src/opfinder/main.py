"""Pipeline entry point — `python -m opfinder.main`.

Milestone 1: scaffold only. Scrapers, scoring, enrichment, and output
are added in later milestones (see design doc §12).
"""

from __future__ import annotations

from .config import load_config
from .logging_setup import setup_logging


def run() -> None:
    cfg = load_config()
    log = setup_logging(cfg.log_dir)
    log.info("opfinder pipeline starting (db=%s)", cfg.db_path)
    log.info("no scrapers yet")


if __name__ == "__main__":
    run()
