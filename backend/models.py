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
