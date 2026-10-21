"""Integration tests for repo.import_opml's transaction boundary.

test_opml_endpoint.py covers what an import persists and counts. What it
cannot reach is a failure inside the DB write: FeedRow validation rejects bad
input before the repo is called. These tests hand the repo a row that bypasses
validation, so the DB itself fails partway through the import.
"""

from __future__ import annotations

import psycopg
import pytest

import repo
from schemas import FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")


class TestImportOpmlRollback:
  def test_a_failing_feed_rolls_back_the_whole_import(self):
    existing_folder = repo.create_folder("Existing")
    existing_feed = repo.add_feed(FeedRow(name="Kept", url="https://kept.example.com/feed"))
    # name is NOT NULL in the DB; model_construct skips the check that would
    # otherwise stop this row before it reaches the INSERT.
    broken = FeedRow.model_construct(name=None, url="https://broken.example.com/feed")

    with pytest.raises(psycopg.errors.NotNullViolation):
      repo.import_opml(
        [("New", [FeedRow(name="Fine", url="https://fine.example.com/feed"), broken])],
        [],
      )

    assert [f.id for f in repo.list_folders()] == [existing_folder.id]
    assert [f.id for f in repo.list_feeds()] == [existing_feed.id]
