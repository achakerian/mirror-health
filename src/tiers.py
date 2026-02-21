from __future__ import annotations

from .models import CheckHistory7d, Mirror, Tier

CONSECUTIVE_FAILS_THRESHOLD = 5
CONSECUTIVE_PASSES_FOR_ALIVE = 3
GOAT_SUCCESS_RATE_7D = 0.90
GOAT_MAX_AVG_RESPONSE_MS = 2000.0


def _success_rate_7d(history: CheckHistory7d) -> float:
    total = history.basic_total + history.full_total
    passed = history.basic_passed + history.full_passed
    if total == 0:
        return 0.0
    return passed / total


def evaluate_tier_transition(mirror: Mirror) -> tuple[Tier, bool]:
    """
    Evaluate tier transition for a mirror.

    Returns (new_tier, new_fallen_comrade_flag).
    Does not mutate the mirror.
    """
    tier = Tier(mirror.tier)
    fallen = mirror.fallen_comrade

    # --- Demotion: 5 consecutive failures ---
    if mirror.consecutive_fails >= CONSECUTIVE_FAILS_THRESHOLD:
        if tier in (Tier.DEAD, Tier.FALLEN_COMRADE):
            # Already dead/FC — no further demotion
            return tier, fallen
        if tier == Tier.GOAT:
            return Tier.FALLEN_COMRADE, True
        if fallen:
            # Any tier with fallen_comrade flag goes to FC, not Dead
            return Tier.FALLEN_COMRADE, True
        return Tier.DEAD, False

    # --- Resurrection: Dead/FC passes a basic check ---
    if tier in (Tier.DEAD, Tier.FALLEN_COMRADE):
        if mirror.consecutive_passes >= 1:
            return Tier.CANDIDATE, fallen
        return tier, fallen

    # --- Promotion: Candidate → Alive (3 consecutive full passes) ---
    if tier == Tier.CANDIDATE:
        if mirror.consecutive_passes >= CONSECUTIVE_PASSES_FOR_ALIVE:
            return Tier.ALIVE, fallen

    # --- Promotion: Alive → GOAT (>90% 7d success AND avg response <2s) ---
    if tier == Tier.ALIVE:
        rate = _success_rate_7d(mirror.check_history_7d)
        if rate > GOAT_SUCCESS_RATE_7D and mirror.avg_response_ms < GOAT_MAX_AVG_RESPONSE_MS:
            return Tier.GOAT, fallen

    return tier, fallen
