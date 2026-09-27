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

os.environ["DATABASE_URL"] = "postgresql://stub/stub"
os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
