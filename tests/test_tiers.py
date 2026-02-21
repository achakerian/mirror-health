from src.models import CheckHistory7d, Mirror, Tier
from src.tiers import evaluate_tier_transition


def _make_mirror(**kwargs) -> Mirror:
    defaults = {"url": "https://test.com", "scraper": "test"}
    defaults.update(kwargs)
    return Mirror(**defaults)


class TestCandidateToAlive:
    def test_promotes_at_3_consecutive_passes(self):
        m = _make_mirror(tier=Tier.CANDIDATE, consecutive_passes=3)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE
        assert fallen is False

    def test_does_not_promote_at_2_passes(self):
        m = _make_mirror(tier=Tier.CANDIDATE, consecutive_passes=2)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.CANDIDATE

    def test_promotes_with_fallen_comrade_flag_preserved(self):
        m = _make_mirror(tier=Tier.CANDIDATE, consecutive_passes=3, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE
        assert fallen is True


class TestAliveToGOAT:
    def test_promotes_with_high_success_and_low_response(self):
        m = _make_mirror(
            tier=Tier.ALIVE,
            avg_response_ms=500,
            check_history_7d=CheckHistory7d(
                basic_total=80, basic_passed=78, full_total=10, full_passed=10
            ),
        )
        # Success rate: 88/90 = 97.8% > 90%
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.GOAT

    def test_does_not_promote_at_exactly_90_percent(self):
        """Spec says >90%, so exactly 90% should NOT promote."""
        m = _make_mirror(
            tier=Tier.ALIVE,
            avg_response_ms=500,
            check_history_7d=CheckHistory7d(
                basic_total=90, basic_passed=81, full_total=10, full_passed=9
            ),
        )
        # Success rate: 90/100 = 90.0% — NOT > 90%
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE

    def test_does_not_promote_with_high_response_time(self):
        m = _make_mirror(
            tier=Tier.ALIVE,
            avg_response_ms=2500,
            check_history_7d=CheckHistory7d(
                basic_total=80, basic_passed=78, full_total=10, full_passed=10
            ),
        )
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE

    def test_does_not_promote_with_no_history(self):
        m = _make_mirror(tier=Tier.ALIVE, avg_response_ms=500)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE


class TestDemotionOnFailures:
    def test_alive_to_dead(self):
        m = _make_mirror(tier=Tier.ALIVE, consecutive_fails=5)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.DEAD
        assert fallen is False

    def test_alive_with_fallen_flag_to_fallen_comrade(self):
        m = _make_mirror(tier=Tier.ALIVE, consecutive_fails=5, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
        assert fallen is True

    def test_goat_to_fallen_comrade(self):
        m = _make_mirror(tier=Tier.GOAT, consecutive_fails=5)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
        assert fallen is True  # Flag gets set

    def test_candidate_to_dead(self):
        m = _make_mirror(tier=Tier.CANDIDATE, consecutive_fails=5)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.DEAD
        assert fallen is False

    def test_candidate_with_fallen_flag_to_fallen_comrade(self):
        m = _make_mirror(tier=Tier.CANDIDATE, consecutive_fails=5, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
        assert fallen is True

    def test_does_not_demote_at_4_fails(self):
        m = _make_mirror(tier=Tier.ALIVE, consecutive_fails=4)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE


class TestResurrection:
    def test_dead_to_candidate_on_pass(self):
        m = _make_mirror(tier=Tier.DEAD, consecutive_passes=1)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.CANDIDATE
        assert fallen is False

    def test_fallen_comrade_to_candidate_on_pass(self):
        m = _make_mirror(tier=Tier.FALLEN_COMRADE, consecutive_passes=1, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.CANDIDATE
        assert fallen is True  # Preserved!

    def test_dead_stays_dead_with_zero_passes(self):
        m = _make_mirror(tier=Tier.DEAD, consecutive_passes=0)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.DEAD

    def test_fallen_comrade_stays_with_zero_passes(self):
        m = _make_mirror(tier=Tier.FALLEN_COMRADE, consecutive_passes=0, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE


class TestFallenComradeBadgePersistence:
    def test_full_lifecycle_goat_to_fc_to_candidate_to_alive(self):
        """Badge persists through the full resurrection cycle."""
        # GOAT fails → FC
        m = _make_mirror(tier=Tier.GOAT, consecutive_fails=5)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
        assert fallen is True

        # FC passes → Candidate (badge stays)
        m.tier = Tier.FALLEN_COMRADE
        m.fallen_comrade = True
        m.consecutive_fails = 0
        m.consecutive_passes = 1
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.CANDIDATE
        assert fallen is True

        # Candidate promotes → Alive (badge stays)
        m.tier = Tier.CANDIDATE
        m.fallen_comrade = True
        m.consecutive_passes = 3
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE
        assert fallen is True

    def test_resurrected_alive_with_fallen_flag_demotes_to_fc_not_dead(self):
        """A resurrected mirror with fallen_comrade=true goes to FC, not Dead."""
        m = _make_mirror(tier=Tier.ALIVE, consecutive_fails=5, fallen_comrade=True)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
        assert fallen is True


class TestNoTransition:
    def test_goat_stays_goat(self):
        m = _make_mirror(tier=Tier.GOAT, consecutive_fails=0, consecutive_passes=10)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.GOAT

    def test_alive_stays_alive_below_thresholds(self):
        m = _make_mirror(
            tier=Tier.ALIVE,
            consecutive_fails=2,
            consecutive_passes=0,
            avg_response_ms=3000,
        )
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.ALIVE


class TestDeadAndFCStayOnMoreFailures:
    def test_dead_with_more_fails_stays_dead(self):
        m = _make_mirror(tier=Tier.DEAD, consecutive_fails=10, consecutive_passes=0)
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.DEAD

    def test_fc_with_more_fails_stays_fc(self):
        m = _make_mirror(
            tier=Tier.FALLEN_COMRADE, consecutive_fails=10, consecutive_passes=0, fallen_comrade=True
        )
        new_tier, fallen = evaluate_tier_transition(m)
        assert new_tier == Tier.FALLEN_COMRADE
