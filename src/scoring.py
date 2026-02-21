from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Mirror

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"

# Fallback K-factor if the tier string doesn't match any config key
_DEFAULT_K_FACTOR = 32


def load_scoring_config(path: Path = _CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def expected_score(mirror_elo: float, target_elo: float) -> float:
    """Probability that the mirror 'wins' (passes the check)."""
    return 1.0 / (1.0 + 10 ** ((target_elo - mirror_elo) / 400.0))


def update_elo(mirror: Mirror, passed: bool, config: dict[str, Any]) -> float:
    """Calculate new Elo rating after a check. Returns the new elo (does not mutate mirror)."""
    k_factors = config.get("k_factors", {})
    k = k_factors.get(mirror.tier, _DEFAULT_K_FACTOR)
    target = config.get("target_elo", 1200)
    actual = 1.0 if passed else 0.0
    exp = expected_score(mirror.elo, target)
    return mirror.elo + k * (actual - exp)


def normalize_score(elo: float, config: dict[str, Any]) -> float:
    """Normalize Elo to 0-1 range, clamped."""
    floor = config.get("elo_floor", 600)
    ceiling = config.get("elo_ceiling", 1600)
    if ceiling == floor:
        return 0.5
    return max(0.0, min(1.0, (elo - floor) / (ceiling - floor)))
