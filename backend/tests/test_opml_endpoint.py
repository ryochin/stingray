"""Behavioral tests for the OPML import/export endpoints.

test_opml.py covers the pure parse/build functions; this file covers the HTTP
layer around them — what actually gets persisted, what is counted as skipped,
and when an import auto-triggers a refresh. That boundary had no coverage,
which matters because the import path is about to be restructured to move its
DB writes off the event loop.

The real lifespan is replaced rather than skipped, so no config load, schema
probe or background summarizer runs while every request still shares one event
loop (see the `client` fixture). The DB comes from the `clean_db` fixture, and
_run_refresh is stubbed so the auto-refresh wiring runs without fetching.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo
from schemas import AppConfig, FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")

OPML_TWO_FOLDERS = """<?xml version="1.0"?>
<opml version="2.0"><body>
  <outline text="Tech">
    <outline type="rss" text="F1" xmlUrl="https://a.example.com/feed"/>
    <outline type="rss" text="F2" xmlUrl="https://b.example.com/feed"/>
  </outline>
  <outline text="News">
    <outline type="rss" text="F3" xmlUrl="https://c.example.com/feed"/>
  </outline>
  <outline type="rss" text="Loose" xmlUrl="https://d.example.com/feed"/>
</body></opml>"""


@pytest.fixture(autouse=True)
def clean_refresh_state():
  """Each test starts with no refresh in flight and freshly created locks.

  A contended asyncio.Lock binds to the loop it waits on (an uncontended
  acquire returns early and binds nothing), and every test runs in a loop of
  its own — so the locks are replaced rather than merely released.
  """
  app_module._refresh_task = None
  app_module._background_tasks.clear()
  app_module._refresh_lock = asyncio.Lock()
  app_module._opml_import_lock = asyncio.Lock()
  yield
  app_module._refresh_task = None
  app_module._background_tasks.clear()


@pytest.fixture
def refreshes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
  """Record refresh triggers instead of fetching anything."""
  triggers: list[str] = []

  async def fake_run_refresh(
    config: Any, *, trigger: str = "web", job_id: int | None = None, force: bool = False
  ) -> None:
    triggers.append(trigger)

  monkeypatch.setattr(app_module, "_run_refresh", fake_run_refresh)
  return triggers


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
  """A TestClient whose requests all share one event loop.

  Used as a context manager on purpose: without it starlette starts a fresh
  portal — and so a fresh loop — for every request, and a lock can only
  serialize requests that share a loop. Entering the context normally runs the
  lifespan, so that is swapped for a no-op first; the real one loads config,
  applies the schema and starts the background summarizer.
  """

  @asynccontextmanager
  async def no_lifespan(_app: Any) -> AsyncIterator[None]:
    yield

  monkeypatch.setattr(app_module.app.router, "lifespan_context", no_lifespan)
  app_module.app.state.config = AppConfig()
  with TestClient(app_module.app) as test_client:
    yield test_client


def _import(client: TestClient, xml: str | bytes) -> dict[str, int]:
  payload = io.BytesIO(xml.encode("utf-8") if isinstance(xml, str) else xml)
  resp = client.post(
    "/api/opml/import",
    files={"file": ("subscriptions.opml", payload, "text/xml")},
  )
  assert resp.status_code == 200, resp.text
  return resp.json()


class TestExport:
  def test_exports_folders_and_feeds(self, client: TestClient):
    folder = repo.create_folder("Tech")
    repo.add_feed(FeedRow(name="F1", url="https://a.example.com/feed", folder_id=folder.id))
    repo.add_feed(FeedRow(name="Loose", url="https://d.example.com/feed"))

    resp = client.get("/api/opml/export")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    body = resp.text
    assert 'text="Tech"' in body
    assert "https://a.example.com/feed" in body
    assert "https://d.example.com/feed" in body

  def test_filename_carries_todays_date(self, client: TestClient):
    resp = client.get("/api/opml/export")
    expected = f'attachment; filename="stingray_subscriptions_{date.today():%Y%m%d}.opml"'
    assert resp.headers["content-disposition"] == expected


class TestImport:
  def test_creates_folders_and_feeds(self, client: TestClient, refreshes: list[str]):
    result = _import(client, OPML_TWO_FOLDERS)

    assert result == {"folders_created": 2, "feeds_created": 4, "feeds_skipped": 0}
    assert {f.name for f in repo.list_folders()} == {"Tech", "News"}
    assert len(repo.list_feeds()) == 4

  def test_preserves_file_order(self, client: TestClient, refreshes: list[str]):
    """Imported feeds append (at_top=False), so the OPML order survives."""
    _import(client, OPML_TWO_FOLDERS)
    positions = {f.name: f.position for f in repo.list_feeds()}
    assert positions["F1"] < positions["F2"] < positions["F3"] < positions["Loose"]

  def test_skips_feeds_whose_url_already_exists(
    self, client: TestClient, refreshes: list[str]
  ):
    repo.add_feed(FeedRow(name="Existing", url="https://a.example.com/feed"))

    result = _import(client, OPML_TWO_FOLDERS)

    assert result["feeds_created"] == 3
    assert result["feeds_skipped"] == 1
    assert len(repo.list_feeds()) == 4

  def test_counts_a_feed_added_mid_import_as_skipped(
    self, client: TestClient, refreshes: list[str], monkeypatch: pytest.MonkeyPatch
  ):
    """A feed that slips in after the import read the existing URLs hits the
    unique index; it is skipped rather than failing the whole import."""
    repo.add_feed(FeedRow(name="Existing", url="https://a.example.com/feed"))
    real_list_feeds = repo.list_feeds
    monkeypatch.setattr(repo, "list_feeds", lambda **_kw: [])

    result = _import(client, OPML_TWO_FOLDERS)

    monkeypatch.setattr(repo, "list_feeds", real_list_feeds)
    assert result["feeds_created"] == 3
    assert result["feeds_skipped"] == 1
    assert len(repo.list_feeds()) == 4

  def test_reuses_an_existing_folder_with_the_same_name(
    self, client: TestClient, refreshes: list[str]
  ):
    repo.create_folder("Tech")

    result = _import(client, OPML_TWO_FOLDERS)

    assert result["folders_created"] == 1
    assert len(repo.list_folders()) == 2

  def test_folderless_feeds_land_uncategorized(self, client: TestClient, refreshes: list[str]):
    _import(client, OPML_TWO_FOLDERS)
    loose = next(f for f in repo.list_feeds() if f.name == "Loose")
    assert loose.folder_id is None

  def test_reimporting_an_export_is_a_no_op(self, client: TestClient, refreshes: list[str]):
    _import(client, OPML_TWO_FOLDERS)

    result = _import(client, client.get("/api/opml/export").text)

    assert result == {"folders_created": 0, "feeds_created": 0, "feeds_skipped": 4}
    assert len(repo.list_feeds()) == 4

  def test_rejects_invalid_xml(self, client: TestClient):
    payload = io.BytesIO(b"<opml><body>")
    resp = client.post(
      "/api/opml/import", files={"file": ("bad.opml", payload, "text/xml")}
    )
    assert resp.status_code == 400
    assert "Invalid OPML XML" in resp.json()["detail"]

  def test_rejects_undecodable_bytes(self, client: TestClient):
    payload = io.BytesIO(b"\xff\xfe\x00invalid")
    resp = client.post(
      "/api/opml/import", files={"file": ("bad.opml", payload, "text/xml")}
    )
    assert resp.status_code == 400
    assert "Invalid file encoding" in resp.json()["detail"]


class TestImportTriggersRefresh:
  def test_new_feeds_trigger_a_refresh(self, client: TestClient, refreshes: list[str]):
    _import(client, OPML_TWO_FOLDERS)

    assert refreshes == ["opml"]
    assert repo.get_latest_refresh_job() is not None

  def test_an_import_that_creates_nothing_does_not_trigger_a_refresh(
    self, client: TestClient, refreshes: list[str]
  ):
    repo.add_feed(FeedRow(name="Loose", url="https://d.example.com/feed"))
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="Loose" xmlUrl="https://d.example.com/feed"/>
    </body></opml>"""

    result = _import(client, xml)

    assert result["feeds_created"] == 0
    assert refreshes == []
    assert repo.get_latest_refresh_job() is None
