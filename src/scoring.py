from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Mirror

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"

# Reliability scoring: a time-decayed Wilson lower bound on the success rate.
#
# Each check contributes one Bernoulli trial (pass=1, fail=0). Older trials are
# exponentially down-weighted by a half-life, so the score reflects *recent*
# reliability. The Wilson lower bound then penalizes mirrors with little
# (decayed) evidence, so a mirror with 2/2 passes ranks below one with 200/204.
# The result is a conservative "reliable success probability" in [0, 1].
DEFAULT_HALF_LIFE_HOURS = 168.0  # 7 days
DEFAULT_Z = 1.96  # 95% Wilson confidence; higher z = more sample-size penalty
DEFAULT_INITIAL_SCORE = 0.0  # score with no evidence yet


def load_scoring_config(path: Path = _CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _decay_factor(elapsed_hours: float, half_life_hours: float) -> float:
    """Exponential decay multiplier for `elapsed_hours` given a half-life."""
    if half_life_hours <= 0 or elapsed_hours <= 0:
        return 1.0
    return 0.5 ** (elapsed_hours / half_life_hours)


def wilson_lower_bound(successes: float, failures: float, z: float = DEFAULT_Z) -> float:
    """Lower bound of the Wilson score interval for a Bernoulli proportion.

    Accepts fractional (time-decayed) counts. Returns 0.0 when there is no
    evidence (n <= 0). Larger z is more conservative (penalizes small samples).
    """
    n = successes + failures
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, min(1.0, (center - margin) / denom))


def _score_from_counts(successes: float, failures: float, config: dict[str, Any]) -> float:
    if successes + failures <= 0:
        return float(config.get("initial_score", DEFAULT_INITIAL_SCORE))
    return wilson_lower_bound(successes, failures, config.get("z", DEFAULT_Z))


def record_outcome(mirror: Mirror, passed: bool, now: datetime, config: dict[str, Any]) -> float:
    """Record a check outcome and recompute the mirror's reliability score.

    Decays the existing counts to `now`, adds this trial, then stores the new
    Wilson score on the mirror. Mutates `decayed_passes`, `decayed_fails`,
    `decay_updated_at`, and `score`. Returns the new score.
    """
    half_life = config.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS)

    if mirror.decay_updated_at is not None:
        elapsed_hours = (now - mirror.decay_updated_at).total_seconds() / 3600.0
        factor = _decay_factor(elapsed_hours, half_life)
        mirror.decayed_passes *= factor
        mirror.decayed_fails *= factor

    if passed:
        mirror.decayed_passes += 1.0
    else:
        mirror.decayed_fails += 1.0
    mirror.decay_updated_at = now

    mirror.score = _score_from_counts(mirror.decayed_passes, mirror.decayed_fails, config)
    return mirror.score


def current_score(mirror: Mirror, now: datetime, config: dict[str, Any]) -> float:
    """Reliability score with decay applied up to `now`, without recording a trial.

    A mirror that has not been checked in a while loses confidence (its decayed
    n shrinks), so its score drifts down. Does not mutate the mirror. Use this at
    output time so published scores reflect freshness, not just the last check.
    """
    half_life = config.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS)
    s, f = mirror.decayed_passes, mirror.decayed_fails
    if mirror.decay_updated_at is not None:
        factor = _decay_factor(
            (now - mirror.decay_updated_at).total_seconds() / 3600.0, half_life
        )
        s *= factor
        f *= factor
    return _score_from_counts(s, f, config)
