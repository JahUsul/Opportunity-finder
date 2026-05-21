"""Pipeline entry point — `python -m opfinder.main`.

Orchestrates scrape → dedupe → score → minimal-enrich → write → notify
across the four scrapers built through m1–m4. See design doc §5.8.

GitHub enrichment (m6) and Indeed/Wellfound (m6) are not wired here yet;
m5 sets oss=0 / lane=greenfield on every candidate as a placeholder.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config, load_config
from .dedup import DedupStore
from .logging_setup import setup_logging
from .models import Candidate
from .notifier import Notifier
from .scorer import Scorer
from .scrapers.app_store import AppStoreScraper
from .scrapers.hn import HNScraper
from .scrapers.play_store import PlayStoreScraper
from .scrapers.reddit import RedditScraper
from .sheet_writer import SheetWriter

log = logging.getLogger(__name__)


def run() -> None:
    cfg = load_config()
    setup_logging(cfg.log_dir)
    log.info("opfinder pipeline starting (db=%s)", cfg.db_path)

    failures: list[dict] = []
    sheet_url: str | None = None
    scored: list[Candidate] = []
    cost: float = 0.0

    try:
        all_candidates = _scrape_all(cfg, failures)
        log.info("scrape: %d candidates fetched across configured scrapers", len(all_candidates))

        new_candidates = _dedup(cfg, all_candidates)
        new_candidates = _cap(cfg, new_candidates)

        scored, cost = _score(cfg, new_candidates)
        _enrich_minimal(scored)

        sheet_url = _write_sheet(cfg, scored)
        log.info("sheet written: %s", sheet_url)

        _notify(cfg, sheet_url, scored, failures, cost)
        log.info("pipeline complete")
    except Exception as e:
        log.exception("pipeline aborted unexpectedly")
        try:
            _notify_pipeline_failure(cfg, sheet_url, str(e))
        except Exception:
            log.exception("notifier also failed; cron log is the last resort")
        raise


# ----- stages -----


def _scrape_all(cfg: Config, failures: list[dict]) -> list[Candidate]:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    candidates: list[Candidate] = []
    builders = [
        (RedditScraper, "reddit", lambda src: RedditScraper.from_env(_strip_meta(src))),
        (HNScraper, "hn", lambda src: HNScraper(**_strip_meta(src))),
        (AppStoreScraper, "app_store", lambda src: AppStoreScraper(**_strip_meta(src))),
        (PlayStoreScraper, "play_store", lambda src: PlayStoreScraper(**_strip_meta(src))),
    ]
    for cls, key, build in builders:
        source_cfg = cfg.sources.get(key, {}) or {}
        if not cls.is_configured(source_cfg):
            log.info("Skipping %s: not enabled or credentials not configured", cls.name)
            continue
        try:
            scraper = build(source_cfg)
            before = len(candidates)
            candidates.extend(scraper.fetch(since))
            log.info("%s: fetched %d candidates", cls.name, len(candidates) - before)
        except Exception as e:
            log.exception("%s scrape failed", cls.name)
            failures.append({"source": cls.name, "stage": "scrape", "error": str(e)})
    return candidates


def _strip_meta(source_cfg: dict) -> dict:
    return {k: v for k, v in source_cfg.items() if k != "enabled"}


def _dedup(cfg: Config, candidates: list[Candidate]) -> list[Candidate]:
    store = DedupStore(cfg.db_path)
    try:
        survivors = store.filter_new(candidates)
        log.info("dedup: %d in → %d survived", len(candidates), len(survivors))
        return survivors
    finally:
        store.close()


def _cap(cfg: Config, candidates: list[Candidate]) -> list[Candidate]:
    """Enforce scoring.max_candidates_per_week to bound per-run LLM cost.

    Deterministic within a single run (seeded by today's ISO date) so the same
    population produces the same sample if main is re-run within the same day.
    """
    cap = int(cfg.scoring.get("max_candidates_per_week") or 200)
    if len(candidates) <= cap:
        return candidates
    log.info(
        "Capping %d candidates to %d (max_candidates_per_week)",
        len(candidates), cap,
    )
    seed = datetime.now(timezone.utc).date().isoformat()
    rng = random.Random(seed)
    return rng.sample(candidates, cap)


def _score(cfg: Config, candidates: list[Candidate]) -> tuple[list[Candidate], float]:
    if not candidates:
        return [], 0.0
    scorer = Scorer(cfg.anthropic_key or "")
    scored = scorer.score_batch(candidates)
    cost = scorer.cost()
    log.info("scored %d candidates (cost=$%.4f)", len(scored), cost)
    return scored, cost


def _enrich_minimal(candidates: list[Candidate]) -> None:
    """m5 placeholder: real GitHub enrichment lands in m6."""
    for c in candidates:
        if c.oss is None:
            c.oss = 0
        if not c.lane:
            c.lane = "greenfield"
        c.machine_total = (
            (c.pain or 0) + (c.money or 0) + (c.buyer or 0) + (c.oss or 0)
        )


def _write_sheet(cfg: Config, candidates: list[Candidate]) -> str:
    if not cfg.sheet_id:
        raise RuntimeError("SHEET_ID not configured; cannot write output")
    promotion_threshold = _promotion_threshold(cfg)
    writer = SheetWriter(
        oauth_client_path=cfg.oauth_client_path or "",
        oauth_token_path=cfg.oauth_token_path or "",
        spreadsheet_id=cfg.sheet_id,
        promotion_threshold=promotion_threshold,
    )
    week_date = datetime.now(timezone.utc).date()
    return writer.write_week(candidates, week_date)


def _promotion_threshold(cfg: Config) -> int:
    value = cfg.scoring.get("promotion_threshold")
    if value is None:
        return 50
    try:
        return int(value)
    except (TypeError, ValueError):
        return 50


def _notify(
    cfg: Config,
    sheet_url: str,
    scored: list[Candidate],
    failures: list[dict],
    cost: float,
) -> None:
    notifier = _build_notifier(cfg)
    if notifier is None:
        log.warning("SMTP not configured; skipping email")
        return
    date_str = datetime.now(timezone.utc).date().isoformat()
    if failures:
        notifier.send_partial_email(sheet_url, failures, date_str=date_str)
        return
    stats = _summarize(scored, date_str=date_str, cost=cost)
    notifier.send_ready_email(sheet_url, stats)


def _notify_pipeline_failure(cfg: Config, sheet_url: str | None, error: str) -> None:
    notifier = _build_notifier(cfg)
    if notifier is None:
        return
    date_str = datetime.now(timezone.utc).date().isoformat()
    failures = [{"source": "pipeline", "stage": "run", "error": error}]
    notifier.send_partial_email(sheet_url or "(no sheet written)", failures, date_str=date_str)


def _build_notifier(cfg: Config) -> Notifier | None:
    smtp = cfg.smtp
    if not (smtp.host and smtp.user and smtp.password and cfg.notify_to):
        return None
    return Notifier(
        host=smtp.host,
        port=smtp.port,
        user=smtp.user,
        password=smtp.password,
        notify_to=cfg.notify_to,
    )


def _summarize(scored: list[Candidate], *, date_str: str, cost: float) -> dict[str, Any]:
    return {
        "date": date_str,
        "n_candidates": len(scored),
        "n_fast": sum(1 for c in scored if c.lane == "fast"),
        "n_greenfield": sum(1 for c in scored if c.lane == "greenfield"),
        "cost": f"{cost:.2f}",
    }


if __name__ == "__main__":
    run()
