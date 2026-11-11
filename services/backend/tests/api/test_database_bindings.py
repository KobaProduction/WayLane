"""The local test app must access the migrated database used by its fixtures."""

from sqlalchemy.engine import make_url

from config import database_settings
from tests.api import DATABASE_URL, IS_SQLITE, TEST_FROM


def test_local_sqlite_app_and_test_fixtures_share_one_database():
    if TEST_FROM == "local" and IS_SQLITE:
        assert make_url(database_settings.url) == make_url(DATABASE_URL)
