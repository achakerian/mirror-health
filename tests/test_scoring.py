import math
from datetime import datetime, timedelta, timezone

from src.models import Mirror
from src.scoring import (
    _decay_factor,
    current_score,
    record_outcome,
    wilson_lower_bound,
)

from .conftest import SCORING_CONFIG

NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


class TestWilsonLowerBound:
    def test_no_evidence_is_zero(self):
        assert wilson_lower_bound(0, 0) == 0.0

    def test_all_failures_is_zero(self):
        assert wilson_lower_bound(0, 5) == 0.0

    def test_bounded_0_1(self):
        for s, f in [(1, 0), (100, 0), (50, 50), (3, 1), (1000, 1)]:
            v = wilson_lower_bound(s, f)
            assert 0.0 <= v <= 1.0

    def test_small_sample_penalized_vs_large(self):
        """Same 100% pass rate: more samples => higher (more confident) score."""
        few = wilson_lower_bound(2, 0)
        many = wilson_lower_bound(200, 0)
        assert many > few
        assert few < 0.5  # 2/2 should be well below certainty
        assert many > 0.95

    def test_more_failures_lowers_score(self):
        assert wilson_lower_bound(90, 10) < wilson_lower_bound(99, 1)

    def test_z_controls_conservatism(self):
        """Higher z penalizes uncertainty more, lowering the bound."""
        assert wilson_lower_bound(10, 0, z=2.58) < wilson_lower_bound(10, 0, z=1.0)


class TestDecayFactor:
    def test_no_elapsed_is_identity(self):
        assert _decay_factor(0, 168) == 1.0

    def test_one_half_life_halves(self):
        assert math.isclose(_decay_factor(168, 168), 0.5, abs_tol=1e-9)

    def test_two_half_lives_quarter(self):
        assert math.isclose(_decay_factor(336, 168), 0.25, abs_tol=1e-9)

    def test_zero_half_life_is_identity(self):
        assert _decay_factor(100, 0) == 1.0


class TestRecordOutcome:
    def test_first_pass_sets_counts_and_score(self):
        m = Mirror(url="https://t.com", scraper="s")
        score = record_outcome(m, passed=True, now=NOW, config=SCORING_CONFIG)
        assert m.decayed_passes == 1.0
        assert m.decayed_fails == 0.0
        assert m.decay_updated_at == NOW
        assert score == m.score
        assert m.score > 0.0

    def test_first_fail_keeps_score_zero(self):
        m = Mirror(url="https://t.com", scraper="s")
        record_outcome(m, passed=False, now=NOW, config=SCORING_CONFIG)
        assert m.decayed_fails == 1.0
        assert m.decayed_passes == 0.0
        assert m.score == 0.0

    def test_repeated_passes_increase_score(self):
        m = Mirror(url="https://t.com", scraper="s")
        scores = []
        for i in range(10):
            # Space checks 2h apart, like the active workflow.
            record_outcome(m, passed=True, now=NOW + timedelta(hours=2 * i), config=SCORING_CONFIG)
            scores.append(m.score)
        assert scores == sorted(scores)  # monotonically non-decreasing
        assert scores[-1] > scores[0]

    def test_decay_reduces_weight_of_old_outcomes(self):
        """A pass one half-life ago contributes ~half a count."""
        m = Mirror(url="https://t.com", scraper="s")
        record_outcome(m, passed=True, now=NOW, config=SCORING_CONFIG)
        # 168h later (one half-life), record another pass.
        record_outcome(m, passed=True, now=NOW + timedelta(hours=168), config=SCORING_CONFIG)
        # Old pass decayed to 0.5, plus the new 1.0 => 1.5
        assert math.isclose(m.decayed_passes, 1.5, abs_tol=1e-6)

    def test_failure_after_passes_lowers_score(self):
        m = Mirror(url="https://t.com", scraper="s")
        for i in range(20):
            record_outcome(m, passed=True, now=NOW + timedelta(hours=2 * i), config=SCORING_CONFIG)
        high = m.score
        record_outcome(m, passed=False, now=NOW + timedelta(hours=42), config=SCORING_CONFIG)
        assert m.score < high


class TestCurrentScore:
    def test_matches_record_when_fresh(self):
        m = Mirror(url="https://t.com", scraper="s")
        for i in range(5):
            record_outcome(m, passed=True, now=NOW + timedelta(hours=2 * i), config=SCORING_CONFIG)
        # Evaluated at the same instant as the last check => identical.
        assert math.isclose(
            current_score(m, NOW + timedelta(hours=8), SCORING_CONFIG), m.score, abs_tol=1e-9
        )

    def test_staleness_decays_score(self):
        m = Mirror(url="https://t.com", scraper="s")
        for i in range(20):
            record_outcome(m, passed=True, now=NOW + timedelta(hours=2 * i), config=SCORING_CONFIG)
        fresh = current_score(m, NOW + timedelta(hours=40), SCORING_CONFIG)
        stale = current_score(m, NOW + timedelta(days=60), SCORING_CONFIG)
        assert stale < fresh

    def test_no_data_returns_initial(self):
        m = Mirror(url="https://t.com", scraper="s")
        assert current_score(m, NOW, SCORING_CONFIG) == 0.0
