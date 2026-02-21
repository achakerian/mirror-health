import math

from src.models import Mirror, Tier
from src.scoring import expected_score, normalize_score, update_elo

from .conftest import SCORING_CONFIG


class TestExpectedScore:
    def test_equal_ratings(self):
        """Equal ratings should give 50% expected win rate."""
        result = expected_score(1200, 1200)
        assert math.isclose(result, 0.5, abs_tol=0.001)

    def test_higher_mirror_rating(self):
        """Higher mirror rating should approach 1.0."""
        result = expected_score(1600, 1200)
        assert result > 0.9

    def test_lower_mirror_rating(self):
        """Lower mirror rating should approach 0.0."""
        result = expected_score(800, 1200)
        assert result < 0.1

    def test_starting_elo_vs_target(self):
        """Starting Elo 1000 vs target 1200: expected ~0.24."""
        result = expected_score(1000, 1200)
        assert math.isclose(result, 0.24, abs_tol=0.01)

    def test_goat_elo_vs_target(self):
        """GOAT with Elo 1400 vs target 1200: expected ~0.76."""
        result = expected_score(1400, 1200)
        assert math.isclose(result, 0.76, abs_tol=0.01)


class TestUpdateElo:
    def test_candidate_pass_gains_significantly(self):
        """Candidate (K=32) at Elo 1000 passing should gain ~24 points."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.CANDIDATE, elo=1000)
        new_elo = update_elo(m, passed=True, config=SCORING_CONFIG)
        delta = new_elo - 1000
        assert 23 < delta < 25  # ~24 points

    def test_candidate_fail_loses_moderately(self):
        """Candidate (K=32) at Elo 1000 failing should lose ~8 points."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.CANDIDATE, elo=1000)
        new_elo = update_elo(m, passed=False, config=SCORING_CONFIG)
        delta = 1000 - new_elo
        assert 7 < delta < 9

    def test_goat_pass_gains_minimally(self):
        """GOAT (K=16) at Elo 1400 passing should gain ~4 points."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.GOAT, elo=1400)
        new_elo = update_elo(m, passed=True, config=SCORING_CONFIG)
        delta = new_elo - 1400
        assert 3 < delta < 5

    def test_goat_fail_loses_significantly(self):
        """GOAT (K=16) at Elo 1400 failing should lose ~12 points."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.GOAT, elo=1400)
        new_elo = update_elo(m, passed=False, config=SCORING_CONFIG)
        delta = 1400 - new_elo
        assert 11 < delta < 13

    def test_alive_k_factor(self):
        """Alive tier uses K=24."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.ALIVE, elo=1200)
        new_elo = update_elo(m, passed=True, config=SCORING_CONFIG)
        # At equal ratings (1200 vs 1200), expected = 0.5, so delta = 24 * 0.5 = 12
        delta = new_elo - 1200
        assert math.isclose(delta, 12.0, abs_tol=0.1)

    def test_dead_k_factor(self):
        """Dead tier uses K=32 (high volatility for resurrection)."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.DEAD, elo=800)
        new_elo = update_elo(m, passed=True, config=SCORING_CONFIG)
        assert new_elo > 800  # Should gain

    def test_does_not_mutate_mirror(self):
        """update_elo should return new value, not mutate."""
        m = Mirror(url="https://test.com", scraper="test", tier=Tier.CANDIDATE, elo=1000)
        new_elo = update_elo(m, passed=True, config=SCORING_CONFIG)
        assert m.elo == 1000  # Unchanged
        assert new_elo != 1000


class TestNormalizeScore:
    def test_floor_returns_zero(self):
        assert normalize_score(600, SCORING_CONFIG) == 0.0

    def test_ceiling_returns_one(self):
        assert normalize_score(1600, SCORING_CONFIG) == 1.0

    def test_midpoint(self):
        result = normalize_score(1100, SCORING_CONFIG)
        assert math.isclose(result, 0.5, abs_tol=0.001)

    def test_below_floor_clamps(self):
        assert normalize_score(400, SCORING_CONFIG) == 0.0

    def test_above_ceiling_clamps(self):
        assert normalize_score(2000, SCORING_CONFIG) == 1.0

    def test_starting_elo(self):
        """Starting Elo of 1000 normalizes to 0.4."""
        result = normalize_score(1000, SCORING_CONFIG)
        assert math.isclose(result, 0.4, abs_tol=0.001)
