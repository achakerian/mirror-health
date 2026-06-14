import pytest


SCORING_CONFIG = {
    "half_life_hours": 168,
    "z": 1.96,
    "initial_score": 0.0,
}


@pytest.fixture
def scoring_config():
    return SCORING_CONFIG.copy()
