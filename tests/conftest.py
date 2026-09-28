"""Test-run environment, set before any test module is imported.

app.py and db_migrate.py read DATABASE_URL when imported, so without a .env the
suite could not even be collected. And with one, load_dotenv() would hand every
test the production database: importing app.py applies db_schema.sql to it
(init_db runs at import). A placeholder here comes first, and load_dotenv never
overrides a variable that is already set. Tests that need a real PostgreSQL
opt in with FLORA_TEST_DATABASE_URL (tests/test_preparation_database.py), which
points DATABASE_URL at a throwaway database for that test only.
"""
import os

import pytest

os.environ["DATABASE_URL"] = "postgresql://stub/stub"
os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")


@pytest.fixture(autouse=True, scope="session")
def _backfill_backups_outside_the_repository(tmp_path_factory):
    """backfill_outcome_agreement.py --apply saves a backup file first; a test run
    must not leave them in the repository's backups/ folder."""
    os.environ["OUTCOME_BACKFILL_BACKUP_DIR"] = str(tmp_path_factory.mktemp("backfill-backups"))
