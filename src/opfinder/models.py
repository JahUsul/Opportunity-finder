from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candidate:
    id: str
    source: str
    source_url: str
    author_id: str
    title: str
    body: str
    raw_excerpt: str
    scraped_at: datetime
    pain: int | None = None
    money: int | None = None
    buyer: int | None = None
    oss: int | None = None
    github_repo_url: str | None = None
    github_license: str | None = None
    lane: str | None = None
    machine_total: int | None = None
    dedup_hash: str = ""
    # Populated by scorer's Layer-2 injection pre-scan (milestone 4).
    injection_flag: bool = False
    injection_patterns: list[str] = field(default_factory=list)
