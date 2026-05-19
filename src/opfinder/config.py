"""Config loading: .env + YAML in config/. See design doc §6."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str | None
    password: str | None


@dataclass(frozen=True)
class Config:
    project_root: Path
    db_path: Path
    log_dir: Path
    sources: dict
    scoring: dict
    anthropic_key: str | None
    github_token: str | None
    sheet_id: str | None
    gsheet_sa_path: str | None
    apify_token: str | None
    smtp: SmtpConfig
    notify_to: str | None
    opportunities_dir: Path

    @property
    def model(self) -> str:
        return self.scoring.get("model", "claude-haiku-4-5-20251001")

    @property
    def triage_threshold(self) -> int:
        return int(self.scoring.get("triage_threshold_text", 18))

    @property
    def lane_oss_cutoff(self) -> int:
        return int(self.scoring.get("lane_oss_cutoff", 3))


def load_config(project_root: Path | None = None) -> Config:
    root = (project_root or _PROJECT_ROOT).resolve()
    load_dotenv(root / ".env")
    sources = _load_yaml(root / "config" / "sources.yaml")
    scoring = _load_yaml(root / "config" / "scoring.yaml")

    smtp = SmtpConfig(
        host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.getenv("SMTP_PORT", "587")),
        user=os.getenv("SMTP_USER"),
        password=os.getenv("SMTP_PASS"),
    )

    opportunities_dir = os.getenv("OPPORTUNITIES_DIR")
    return Config(
        project_root=root,
        db_path=root / "data" / "seen_candidates.db",
        log_dir=root / "data" / "logs",
        sources=sources,
        scoring=scoring,
        anthropic_key=os.getenv("ANTHROPIC_API_KEY"),
        github_token=os.getenv("GITHUB_TOKEN"),
        sheet_id=os.getenv("SHEET_ID"),
        gsheet_sa_path=os.getenv("GOOGLE_SHEETS_SA_PATH"),
        apify_token=os.getenv("APIFY_TOKEN"),
        smtp=smtp,
        notify_to=os.getenv("NOTIFY_TO"),
        opportunities_dir=Path(opportunities_dir) if opportunities_dir else root / "opportunities",
    )


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}
