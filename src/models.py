from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Tier(enum.StrEnum):
    CANDIDATE = "Candidate"
    ALIVE = "Alive"
    GOAT = "GOAT"
    DEAD = "Dead"
    FALLEN_COMRADE = "FallenComrade"


class CheckHistory7d(BaseModel):
    basic_total: int = 0
    basic_passed: int = 0
    full_total: int = 0
    full_passed: int = 0
    window_start: Optional[datetime] = None


class Mirror(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    url: str
    scraper: str
    tier: Tier = Tier.CANDIDATE
    fallen_comrade: bool = False
    elo: float = 1000.0
    score: float = 0.4
    avg_response_ms: float = 0.0
    consecutive_fails: int = 0
    consecutive_passes: int = 0
    total_checks: int = 0
    total_passes: int = 0
    last_checked: Optional[datetime] = None
    last_passed: Optional[datetime] = None
    last_failed: Optional[datetime] = None
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cloudflare_detected: bool = False
    last_failure_reason: Optional[str] = None
    check_history_7d: CheckHistory7d = Field(default_factory=CheckHistory7d)
    response_times: list[float] = Field(default_factory=list)


class MirrorState(BaseModel):
    generated_at: Optional[datetime] = None
    mirrors: list[Mirror] = Field(default_factory=list)


class ScoreEntry(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    url: str
    tier: Tier
    score: float
    elo: float
    avg_response_ms: float
    fallen_comrade: bool
    last_checked: Optional[datetime] = None
    cloudflare_detected: bool = False


class ScoresOutput(BaseModel):
    generated_at: Optional[datetime] = None
    scrapers: dict[str, list[ScoreEntry]] = Field(default_factory=dict)
