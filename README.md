# mirror-health

A GitHub Actions-based service that validates torrent indexer site mirrors and publishes scored, tiered results as static JSON files.

## The Problem

Torrent indexer sites (1337x, YTS, TPB, etc.) frequently change domains, go down, or get replaced by placeholder pages that return HTTP 200 but serve no real content. Simple HTTP health checks can't distinguish a real mirror from a fake one.

## The Solution

A background service running on GitHub Actions that:

1. Probes known indexer mirrors with **scraper-specific fingerprint validation** (not just HTTP status codes)
2. Scores mirrors using an **Elo-style rating system**
3. Classifies mirrors into **tiers** (Candidate, Alive, GOAT, Dead, Fallen Comrade)
4. Publishes results as a **static JSON file** anyone can consume

## Consuming the Data

Pull `data/mirror_scores.json` for pre-validated, ranked mirrors:

```json
{
  "generated_at": "2026-02-20T10:00:00Z",
  "scrapers": {
    "yts": [
      {
        "url": "https://yts.mx",
        "tier": "GOAT",
        "score": 0.85,
        "avg_response_ms": 320,
        "fallen_comrade": false,
        "last_checked": "2026-02-20T10:00:00Z",
        "cloudflare_detected": false
      }
    ]
  }
}
```

Only **Alive** and **GOAT** mirrors appear in `mirror_scores.json`, sorted by `score` descending (ties broken by faster `avg_response_ms`) within each scraper.

For the full state including Dead and Candidate mirrors, see `data/mirror_state.json`.

## Tier System

| Tier | Meaning |
|---|---|
| **Candidate** | Newly discovered, proving itself |
| **Alive** | Currently working, passing checks |
| **GOAT** | Proven reliable over extended period |
| **Dead** | Failing consistently |
| **Fallen Comrade** | Was once GOAT, now failing — a permanent badge of honor |

### Transitions

```
Discovery → Candidate
Candidate → Alive:          3 consecutive full health check passes
Alive → GOAT:               >90% success rate over 7 days AND avg response <2s
Alive → Dead:               5 consecutive basic check failures
GOAT → Fallen Comrade:      5 consecutive basic check failures
Dead → Candidate:           Passes a basic check (resurrection)
Fallen Comrade → Candidate: Passes a basic check (resurrection, badge preserved)
```

The `fallen_comrade` flag is **permanent** — once set, it never unsets, even if the mirror reaches GOAT again.

## Reliability Scoring

Each mirror gets a `score` in **0–1**: a **time-decayed [Wilson lower bound](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval) on its success rate**. Read it as a conservative "reliable success probability."

Each check is one Bernoulli trial (pass=1, fail=0). The score has three properties that a raw pass-rate lacks:

- **Recency** — outcomes decay exponentially with a configurable half-life (default 7 days), so the score reflects how a mirror behaves *now*, and a mirror that stops being checked drifts down.
- **Sample confidence** — the Wilson lower bound penalizes thin evidence: a mirror with 2/2 passes scores well below one with 200/204. New mirrors must earn trust.
- **Latency tiebreaker** — equal-reliability mirrors are ordered by lower `avg_response_ms`. Speed never inflates the reliability number itself (the score stays a clean probability).

Tuning lives in `config/scoring.json`:

```json
{ "half_life_hours": 168, "z": 1.96, "initial_score": 0.0 }
```

`z` controls conservatism (higher = harsher on small samples; 1.96 ≈ 95% confidence).

> **Note:** v1 used an Elo rating against a phantom 98%-uptime "opponent." With no real opponents that degenerated into an opaque transform of success rate, so it was replaced by this reliability score. The `elo` field has been removed from the output.

## Health Checks

### Basic Check
HTTP GET to `/`, 10s timeout. Validates: HTTP 200, non-empty body, no Cloudflare challenge page, no placeholder/parked domain content.

### Full Check
Scraper-specific fingerprint validation:

| Scraper | Probe | Pass Condition |
|---|---|---|
| YTS | `/api/v2/list_movies.json?limit=1` | Valid JSON with `data.movies` array |
| 1337x | `/` | HTML contains `table-list` |
| TPB | `/` | HTML contains `detLink` or `searchResult` |
| TorrentGalaxy | `/` | HTML contains `tgxtablerow` |
| EZTV | `/` | HTML contains `forum_header_border` |
| Anna's Archive | `/search?q=test` | HTML contains `js-vim-focus` |
| RuTracker | `/` | HTML contains `f-name` |

## Workflows

| Workflow | Schedule | What it does |
|---|---|---|
| `active_check.yml` | Every 2 hours | Check Candidate + Alive + GOAT mirrors |
| `inactive_check.yml` | Daily 06:00 UTC | Re-probe Dead + Fallen Comrade mirrors |
| `discovery.yml` | Weekly Sunday 03:00 UTC | TLD brute force to find new mirrors |

Estimated monthly GitHub Actions usage: ~582 minutes (within 1,000 min budget).

## Adding New Domains

Edit `config/known_domains.json` and submit a PR. The file maps scraper names to base domain names:

```json
{
  "yts": ["yts", "yify", "yifymovies"],
  "1337x": ["1337x", "x1337x", "1337xx"]
}
```

The discovery workflow will combine these with the TLD list in `config/tlds.json` to probe all combinations.

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest -v

# Run locally
python -m src.main active     # Check active mirrors
python -m src.main inactive   # Re-probe dead mirrors
python -m src.main discovery  # Discover new mirrors
```

## Tracker Health

This project monitors **indexer/search sites** only. For BitTorrent **tracker** health, see [newTrackon](https://newtrackon.com/).

## Known Limitations

- GitHub Actions runners are US-based. Mirrors that geo-block US IPs will appear as Dead.
- Cloudflare-protected mirrors are detected and counted as failures in v1.
- Scraper fingerprints may break if a site redesigns. If all mirrors for a scraper fail simultaneously, the fingerprint in `config/scrapers.json` likely needs updating.
