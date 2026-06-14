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
    GEO_RESTRICTED = "GeoRestricted"


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
    score: float = 0.0
    # Time-decayed Bernoulli counts feeding the Wilson reliability score.
    decayed_passes: float = 0.0
    decayed_fails: float = 0.0
    decay_updated_at: Optional[datetime] = None
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
    # Geo-restriction: regions (non-US) check-host saw this mirror reachable from
    # while it was failing the US check. Populated only when geo-rechecked.
    geo_checked_at: Optional[datetime] = None
    geo_reachable_regions: list[str] = Field(default_factory=list)
    last_failure_reason: Optional[str] = None
    check_history_7d: CheckHistory7d = Field(default_factory=CheckHistory7d)
    response_times: list[float] = Field(default_factory=list)


class RunnerGeo(BaseModel):
    ip: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    org: Optional[str] = None
    timezone: Optional[str] = None


class MirrorState(BaseModel):
    generated_at: Optional[datetime] = None
    runner_geo: Optional[RunnerGeo] = None
    mirrors: list[Mirror] = Field(default_factory=list)


class ScoreEntry(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    url: str
    tier: Tier
    score: float
    avg_response_ms: float
    fallen_comrade: bool
    last_checked: Optional[datetime] = None
    cloudflare_detected: bool = False


class ScoresOutput(BaseModel):
    generated_at: Optional[datetime] = None
    runner_geo: Optional[RunnerGeo] = None
    scrapers: dict[str, list[ScoreEntry]] = Field(default_factory=dict)


class RegionScoreEntry(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    url: str
    tier: Tier
    score: float
    avg_response_ms: float
    fallen_comrade: bool
    # True: content-validated from the US runner. False: reachability-only via check-host.
    validated: bool
    last_checked: Optional[datetime] = None
    cloudflare_detected: bool = False
    geo_reachable_regions: list[str] = Field(default_factory=list)


class RegionScoresOutput(BaseModel):
    region: str
    generated_at: Optional[datetime] = None
    runner_geo: Optional[RunnerGeo] = None
    fidelity_note: str = ""
    scrapers: dict[str, list[RegionScoreEntry]] = Field(default_factory=dict)
