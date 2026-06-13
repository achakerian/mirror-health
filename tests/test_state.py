import json
from datetime import datetime, timezone
from pathlib import Path

from src.models import Mirror, MirrorState, RunnerGeo, Tier
from src.state import generate_scores, load_state, save_scores, save_state

from .conftest import SCORING_CONFIG


class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        state = load_state(tmp_path / "nonexistent.json")
        assert state.mirrors == []
        assert state.generated_at is None

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("{}")
        state = load_state(p)
        assert state.mirrors == []

    def test_corrupted_file_returns_empty(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("not valid json {{{")
        state = load_state(p)
        assert state.mirrors == []

    def test_valid_file_loads(self, tmp_path):
        m = Mirror(url="https://test.com", scraper="test", score=0.9)
        state = MirrorState(mirrors=[m])
        p = tmp_path / "state.json"
        p.write_text(state.model_dump_json(indent=2))
        loaded = load_state(p)
        assert len(loaded.mirrors) == 1
        assert loaded.mirrors[0].url == "https://test.com"
        assert loaded.mirrors[0].score == 0.9

    def test_legacy_elo_field_ignored(self, tmp_path):
        """Old state files carrying the removed `elo` field still load."""
        p = tmp_path / "state.json"
        p.write_text(
            '{"mirrors": [{"url": "https://old.com", "scraper": "s", "elo": 1234, '
            '"score": 0.5}]}'
        )
        loaded = load_state(p)
        assert len(loaded.mirrors) == 1
        assert loaded.mirrors[0].score == 0.5
        assert not hasattr(loaded.mirrors[0], "elo")


class TestSaveState:
    def test_round_trip(self, tmp_path):
        m1 = Mirror(url="https://a.com", scraper="s1", score=0.8, tier=Tier.ALIVE)
        m2 = Mirror(url="https://b.com", scraper="s2", score=0.1, tier=Tier.DEAD)
        state = MirrorState(mirrors=[m1, m2])
        p = tmp_path / "data" / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert len(loaded.mirrors) == 2
        assert loaded.mirrors[0].url == "https://a.com"
        assert loaded.mirrors[1].url == "https://b.com"
        assert loaded.generated_at is not None

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "state.json"
        state = MirrorState()
        save_state(state, p)
        assert p.exists()


class TestAtomicWrite:
    def test_no_temp_file_left_on_success(self, tmp_path):
        p = tmp_path / "state.json"
        state = MirrorState(mirrors=[Mirror(url="https://a.com", scraper="s1")])
        save_state(state, p)
        # Only the target file should exist, no .tmp leftovers
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "state.json"

    def test_existing_file_replaced_atomically(self, tmp_path):
        p = tmp_path / "state.json"
        # Write initial state
        m1 = Mirror(url="https://old.com", scraper="s1")
        save_state(MirrorState(mirrors=[m1]), p)
        # Overwrite with new state
        m2 = Mirror(url="https://new.com", scraper="s1")
        save_state(MirrorState(mirrors=[m2]), p)
        loaded = load_state(p)
        assert len(loaded.mirrors) == 1
        assert loaded.mirrors[0].url == "https://new.com"


class TestGenerateScores:
    def test_only_alive_and_goat_included(self):
        mirrors = [
            Mirror(url="https://alive.com", scraper="s1", tier=Tier.ALIVE, score=0.7),
            Mirror(url="https://goat.com", scraper="s1", tier=Tier.GOAT, score=0.95),
            Mirror(url="https://dead.com", scraper="s1", tier=Tier.DEAD, score=0.1),
            Mirror(url="https://cand.com", scraper="s1", tier=Tier.CANDIDATE, score=0.4),
            Mirror(url="https://fc.com", scraper="s1", tier=Tier.FALLEN_COMRADE, score=0.2),
        ]
        state = MirrorState(mirrors=mirrors)
        output = generate_scores(state, SCORING_CONFIG)
        urls = [e.url for e in output.scrapers["s1"]]
        assert "https://alive.com" in urls
        assert "https://goat.com" in urls
        assert "https://dead.com" not in urls
        assert "https://cand.com" not in urls
        assert "https://fc.com" not in urls

    def test_sorted_by_score_descending(self):
        # decay_updated_at=None => current_score uses the raw decayed counts.
        mirrors = [
            Mirror(url="https://low.com", scraper="s1", tier=Tier.ALIVE,
                   decayed_passes=20, decayed_fails=3),   # ~0.68
            Mirror(url="https://high.com", scraper="s1", tier=Tier.GOAT,
                   decayed_passes=200, decayed_fails=0),  # ~0.99
            Mirror(url="https://mid.com", scraper="s1", tier=Tier.ALIVE,
                   decayed_passes=50, decayed_fails=1),   # ~0.90
        ]
        state = MirrorState(mirrors=mirrors)
        output = generate_scores(state, SCORING_CONFIG)
        urls = [e.url for e in output.scrapers["s1"]]
        assert urls == ["https://high.com", "https://mid.com", "https://low.com"]
        scores = [e.score for e in output.scrapers["s1"]]
        assert scores == sorted(scores, reverse=True)

    def test_ties_broken_by_faster_response(self):
        """Equal reliability => the faster mirror ranks first."""
        mirrors = [
            Mirror(url="https://slow.com", scraper="s1", tier=Tier.ALIVE,
                   decayed_passes=50, decayed_fails=1, avg_response_ms=900),
            Mirror(url="https://fast.com", scraper="s1", tier=Tier.ALIVE,
                   decayed_passes=50, decayed_fails=1, avg_response_ms=120),
        ]
        state = MirrorState(mirrors=mirrors)
        output = generate_scores(state, SCORING_CONFIG)
        urls = [e.url for e in output.scrapers["s1"]]
        assert urls == ["https://fast.com", "https://slow.com"]

    def test_grouped_by_scraper(self):
        mirrors = [
            Mirror(url="https://a.com", scraper="yts", tier=Tier.ALIVE),
            Mirror(url="https://b.com", scraper="1337x", tier=Tier.GOAT),
        ]
        state = MirrorState(mirrors=mirrors)
        output = generate_scores(state, SCORING_CONFIG)
        assert "yts" in output.scrapers
        assert "1337x" in output.scrapers
        assert len(output.scrapers["yts"]) == 1
        assert len(output.scrapers["1337x"]) == 1

    def test_empty_state_produces_empty_output(self):
        state = MirrorState()
        output = generate_scores(state, SCORING_CONFIG)
        assert output.scrapers == {}

    def test_no_active_mirrors_produces_empty(self):
        mirrors = [
            Mirror(url="https://dead.com", scraper="s1", tier=Tier.DEAD),
        ]
        state = MirrorState(mirrors=mirrors)
        output = generate_scores(state, SCORING_CONFIG)
        assert output.scrapers == {}

    def test_propagates_runner_geo(self):
        geo = RunnerGeo(ip="1.2.3.4", city="Ashburn", country="US")
        mirrors = [
            Mirror(url="https://a.com", scraper="s1", tier=Tier.ALIVE),
        ]
        state = MirrorState(mirrors=mirrors, runner_geo=geo)
        output = generate_scores(state, SCORING_CONFIG)
        assert output.runner_geo is not None
        assert output.runner_geo.ip == "1.2.3.4"
        assert output.runner_geo.city == "Ashburn"

    def test_none_runner_geo_propagated(self):
        state = MirrorState(mirrors=[], runner_geo=None)
        output = generate_scores(state, SCORING_CONFIG)
        assert output.runner_geo is None


class TestSaveScores:
    def test_writes_valid_json(self, tmp_path):
        mirrors = [
            Mirror(url="https://a.com", scraper="yts", tier=Tier.GOAT),
        ]
        state = MirrorState(mirrors=mirrors)
        p = tmp_path / "scores.json"
        save_scores(state, SCORING_CONFIG, p)
        data = json.loads(p.read_text())
        assert "scrapers" in data
        assert "yts" in data["scrapers"]
        assert data["scrapers"]["yts"][0]["url"] == "https://a.com"
