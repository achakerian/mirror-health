import pytest


SCORING_CONFIG = {
    "k_factors": {
        "Candidate": 32,
        "Alive": 24,
        "GOAT": 16,
        "Dead": 32,
        "FallenComrade": 32,
    },
    "target_elo": 1200,
    "initial_elo": 1000,
    "elo_floor": 600,
    "elo_ceiling": 1600,
}


@pytest.fixture
def scoring_config():
    return SCORING_CONFIG.copy()
