import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "bsebot_test.db"


@pytest.fixture
def migrations_dir():
    return MIGRATIONS_DIR
