"""The test database URL guard in conftest.

clean_db TRUNCATEs every table, so `_test_db_url()` must refuse any URL whose
effective database name does not end with `_test`.
"""

from __future__ import annotations

import pytest

from tests.conftest import _test_db_url


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
  monkeypatch.delenv("DATABASE_URL", raising=False)


def test_explicit_test_database_is_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
  url: str = "postgresql://u:p@db:5432/stingray_test?sslmode=disable"
  monkeypatch.setenv("TEST_DATABASE_URL", url)
  assert _test_db_url() == url


@pytest.mark.parametrize(
  "url",
  [
    "postgresql://u:p@db:5432/stingray",
    "postgresql://u:p@db:5432/stingray_test_backup",
    "postgresql://u:p@db:5432/stingray_test?dbname=stingray",
    "postgresql://u:p@db:5432",
  ],
  ids=["production", "test_in_middle", "query_overrides_path", "no_dbname"],
)
def test_explicit_non_test_database_is_refused(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
  monkeypatch.setenv("TEST_DATABASE_URL", url)
  with pytest.raises(RuntimeError, match="must end with '_test'"):
    _test_db_url()


def test_derives_test_database_from_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/myfeeds")
  assert _test_db_url() == "postgresql://u:p@db:5432/myfeeds_test"


def test_derived_url_still_honors_query_override(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/stingray?dbname=stingray")
  with pytest.raises(RuntimeError, match="must end with '_test'"):
    _test_db_url()
