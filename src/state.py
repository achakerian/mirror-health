from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Mirror, MirrorState, ScoreEntry, ScoresOutput, Tier
from .scoring import normalize_score
from .utils import logger

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
STATE_PATH = DATA_DIR / "mirror_state.json"
SCORES_PATH = DATA_DIR / "mirror_scores.json"


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via temp file + rename.

    Writes to a temp file in the same directory, then renames. This ensures
    the target file is never in a partially-written state — either the old
    content or the new content is present, never a truncated mix.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w") as f:
            f.write(content)
        Path(tmp_path).replace(path)
    except BaseException:
        # Clean up the temp file on any error
        Path(tmp_path).unlink(missing_ok=True)
        raise


def load_state(path: Path = STATE_PATH) -> MirrorState:
    """Load mirror state from JSON. Returns empty state if file is missing or corrupted."""
    if not path.exists():
        logger.warning("State file not found at %s, starting fresh", path)
        return MirrorState()
    try:
        text = path.read_text().strip()
        if not text or text == "{}":
            return MirrorState()
        return MirrorState.model_validate_json(text)
    except Exception as e:
        logger.warning("Failed to load state from %s: %s. Starting fresh.", path, e)
        return MirrorState()


def save_state(state: MirrorState, path: Path = STATE_PATH) -> None:
    """Save full mirror state to JSON atomically."""
    state.generated_at = datetime.now(timezone.utc)
    _atomic_write(path, state.model_dump_json(indent=2))
    logger.info("Saved state with %d mirrors to %s", len(state.mirrors), path)


def load_known_domains(path: Path | None = None) -> dict[str, list[str]]:
    """Load seed domains from config."""
    if path is None:
        path = CONFIG_DIR / "known_domains.json"
    return json.loads(path.read_text())


def bootstrap_state(state: MirrorState, path: Path | None = None) -> int:
    """Seed mirrors from config/seed_mirrors.json if not already tracked.

    Returns the number of newly added mirrors.
    """
    if path is None:
        path = CONFIG_DIR / "seed_mirrors.json"
    if not path.exists():
        return 0

    seed_data: dict[str, list[str]] = json.loads(path.read_text())
    existing_urls = {m.url for m in state.mirrors}
    added = 0

    for scraper_name, urls in seed_data.items():
        for url in urls:
            url = url.rstrip("/")
            if url not in existing_urls:
                state.mirrors.append(Mirror(url=url, scraper=scraper_name))
                existing_urls.add(url)
                added += 1
                logger.info("Bootstrap: added seed mirror %s [%s]", url, scraper_name)

    return added


def generate_scores(state: MirrorState, scoring_config: dict) -> ScoresOutput:
    """Generate the public mirror_scores.json with only Alive and GOAT mirrors."""
    now = datetime.now(timezone.utc)
    scrapers: dict[str, list[ScoreEntry]] = {}

    for mirror in state.mirrors:
        tier = Tier(mirror.tier)
        if tier not in (Tier.ALIVE, Tier.GOAT):
            continue

        entry = ScoreEntry(
            url=mirror.url,
            tier=tier,
            score=normalize_score(mirror.elo, scoring_config),
            elo=mirror.elo,
            avg_response_ms=mirror.avg_response_ms,
            fallen_comrade=mirror.fallen_comrade,
            last_checked=mirror.last_checked,
            cloudflare_detected=mirror.cloudflare_detected,
        )
        scrapers.setdefault(mirror.scraper, []).append(entry)

    # Sort each scraper's mirrors by elo descending
    for scraper in scrapers:
        scrapers[scraper].sort(key=lambda e: e.elo, reverse=True)

    return ScoresOutput(generated_at=now, runner_geo=state.runner_geo, scrapers=scrapers)


def save_scores(state: MirrorState, scoring_config: dict, path: Path = SCORES_PATH) -> None:
    """Generate and save the public scores JSON atomically."""
    output = generate_scores(state, scoring_config)
    _atomic_write(path, output.model_dump_json(indent=2))
    total = sum(len(entries) for entries in output.scrapers.values())
    logger.info("Saved scores with %d active mirrors to %s", total, path)
