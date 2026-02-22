from datetime import datetime, timezone

from src.models import CheckHistory7d, Mirror, MirrorState, RunnerGeo, ScoreEntry, ScoresOutput, Tier


class TestTier:
    def test_enum_values(self):
        assert Tier.CANDIDATE == "Candidate"
        assert Tier.ALIVE == "Alive"
        assert Tier.GOAT == "GOAT"
        assert Tier.DEAD == "Dead"
        assert Tier.FALLEN_COMRADE == "FallenComrade"

    def test_str_comparison(self):
        assert Tier.CANDIDATE == "Candidate"
        assert Tier("Candidate") is Tier.CANDIDATE


class TestMirror:
    def test_defaults(self):
        m = Mirror(url="https://example.com", scraper="test")
        assert m.url == "https://example.com"
        assert m.scraper == "test"
        assert m.tier == "Candidate"
        assert m.fallen_comrade is False
        assert m.elo == 1000.0
        assert m.score == 0.4
        assert m.avg_response_ms == 0.0
        assert m.consecutive_fails == 0
        assert m.consecutive_passes == 0
        assert m.total_checks == 0
        assert m.total_passes == 0
        assert m.last_checked is None
        assert m.last_passed is None
        assert m.last_failed is None
        assert m.first_seen is not None
        assert m.cloudflare_detected is False
        assert m.last_failure_reason is None
        assert m.check_history_7d.basic_total == 0
        assert m.response_times == []

    def test_tier_stored_as_string(self):
        m = Mirror(url="https://example.com", scraper="test", tier=Tier.GOAT)
        assert m.tier == "GOAT"
        assert isinstance(m.tier, str)

    def test_json_round_trip(self):
        m = Mirror(
            url="https://1337x.to",
            scraper="1337x",
            tier=Tier.ALIVE,
            fallen_comrade=True,
            elo=1220.0,
            consecutive_passes=8,
            total_checks=142,
            total_passes=128,
        )
        json_str = m.model_dump_json()
        m2 = Mirror.model_validate_json(json_str)
        assert m2.url == m.url
        assert m2.scraper == m.scraper
        assert m2.tier == "Alive"
        assert m2.fallen_comrade is True
        assert m2.elo == 1220.0
        assert m2.consecutive_passes == 8

    def test_fallen_comrade_persists_through_serialization(self):
        m = Mirror(
            url="https://example.com",
            scraper="test",
            tier=Tier.CANDIDATE,
            fallen_comrade=True,
        )
        json_str = m.model_dump_json()
        m2 = Mirror.model_validate_json(json_str)
        assert m2.fallen_comrade is True
        assert m2.tier == "Candidate"


class TestCheckHistory7d:
    def test_defaults(self):
        h = CheckHistory7d()
        assert h.basic_total == 0
        assert h.basic_passed == 0
        assert h.full_total == 0
        assert h.full_passed == 0

    def test_success_rate(self):
        h = CheckHistory7d(basic_total=10, basic_passed=9, full_total=2, full_passed=2)
        total = h.basic_total + h.full_total
        passed = h.basic_passed + h.full_passed
        assert passed / total == 11 / 12


class TestRunnerGeo:
    def test_all_fields(self):
        geo = RunnerGeo(
            ip="1.2.3.4",
            city="Ashburn",
            region="Virginia",
            country="US",
            org="AS8075 Microsoft Corporation",
            timezone="America/New_York",
        )
        assert geo.ip == "1.2.3.4"
        assert geo.country == "US"

    def test_defaults_to_none(self):
        geo = RunnerGeo()
        assert geo.ip is None
        assert geo.city is None

    def test_json_round_trip(self):
        geo = RunnerGeo(ip="1.2.3.4", city="Ashburn", country="US")
        json_str = geo.model_dump_json()
        geo2 = RunnerGeo.model_validate_json(json_str)
        assert geo2.ip == "1.2.3.4"
        assert geo2.city == "Ashburn"


class TestMirrorState:
    def test_empty(self):
        state = MirrorState()
        assert state.mirrors == []
        assert state.generated_at is None
        assert state.runner_geo is None

    def test_json_round_trip(self):
        now = datetime.now(timezone.utc)
        m = Mirror(url="https://example.com", scraper="test")
        state = MirrorState(generated_at=now, mirrors=[m])
        json_str = state.model_dump_json()
        state2 = MirrorState.model_validate_json(json_str)
        assert len(state2.mirrors) == 1
        assert state2.mirrors[0].url == "https://example.com"
        assert state2.generated_at is not None

    def test_backward_compat_without_runner_geo(self):
        """JSON without runner_geo loads fine (defaults to None)."""
        raw = '{"generated_at": null, "mirrors": []}'
        state = MirrorState.model_validate_json(raw)
        assert state.runner_geo is None

    def test_with_runner_geo(self):
        geo = RunnerGeo(ip="1.2.3.4", city="Ashburn", country="US")
        state = MirrorState(runner_geo=geo, mirrors=[])
        json_str = state.model_dump_json()
        state2 = MirrorState.model_validate_json(json_str)
        assert state2.runner_geo is not None
        assert state2.runner_geo.ip == "1.2.3.4"


class TestScoresOutput:
    def test_structure(self):
        entry = ScoreEntry(
            url="https://yts.mx",
            tier=Tier.GOAT,
            score=0.85,
            elo=1450,
            avg_response_ms=320,
            fallen_comrade=False,
            cloudflare_detected=False,
        )
        output = ScoresOutput(
            generated_at=datetime.now(timezone.utc),
            scrapers={"yts": [entry]},
        )
        json_str = output.model_dump_json()
        output2 = ScoresOutput.model_validate_json(json_str)
        assert "yts" in output2.scrapers
        assert output2.scrapers["yts"][0].url == "https://yts.mx"
        assert output2.scrapers["yts"][0].tier == "GOAT"

    def test_runner_geo_included(self):
        geo = RunnerGeo(ip="1.2.3.4", city="Ashburn", country="US")
        output = ScoresOutput(
            generated_at=datetime.now(timezone.utc),
            runner_geo=geo,
            scrapers={},
        )
        json_str = output.model_dump_json()
        output2 = ScoresOutput.model_validate_json(json_str)
        assert output2.runner_geo is not None
        assert output2.runner_geo.ip == "1.2.3.4"

    def test_backward_compat_without_runner_geo(self):
        raw = '{"generated_at": null, "scrapers": {}}'
        output = ScoresOutput.model_validate_json(raw)
        assert output.runner_geo is None
