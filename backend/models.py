from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
  title: str
  url: str
  source: str
  published: datetime | None
  content_snippet: str
  title_translated: str = ""
  summary: str = ""
  content_html: str = ""
  content_translated: str = ""
  # The feed row this article was fetched for. None for ad-hoc fetches (e.g.
  # extraction previews) that aren't tied to a stored feed.
  feed_id: int | None = None
